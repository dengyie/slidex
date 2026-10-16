"""点选执行器：把「有序目标字符」变成「页面上依序的点击」。

两段式解耦（0.6.34）——这是让点选成为**通用能力**而非渠道特化的关键：

1. **规划器** ``plan_click_points``：纯函数。输入 ``OcrResult``（任意引擎产出）
   + ``Instruction``（有序目标），输出归一化坐标序列。不碰浏览器、不发网络。
   换识别引擎只改这一段的输入来源；换验证形态只改这一段的挑选规则。
2. **执行器** ``PointSelectExecutor``：只负责坐标换算 + 依序点击 + 读判决。
   不关心字是怎么识别出来的。

坐标语义：规划和模型输出一律 **0..1 相对整张图**；换算成页面像素只在执行器
做一次，且基于目标元素的 ``bounding_box()``。截图与元素渲染尺寸之间可能有
devicePixelRatio 缩放、iframe 偏移、CSS 尺寸与实际渲染不一致等偏差，在规划
阶段做换算只会把偏差固化进每个点。
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from slidex._slide_result import is_punish_slide_code
from slidex.ocr.instruction import Instruction
from slidex.ocr.models import OcrBox, OcrResult

# 单个目标字的允许半径（归一化，相对于图片短边）。超过则判为识别不可信：
# 点到远处等于点错一个字，会让整个挑战失败，不如停下来换通道重截图。
MAX_TARGET_RADIUS = 0.25

# 点选结果码：与滑块 0.6.33 的惩罚码语义对齐（300 = other-punish）。
# 点选判罚同样是"判决命中即停"，换识别通道重试只会追加风控确认样本。
PUNISH_POINT_CODES = frozenset({300})


@dataclass(frozen=True)
class ClickPoint:
    """一个待点击目标。坐标为整图归一化中心点。"""

    char: str
    x: float
    y: float
    confidence: float
    #: 命中的来源字框下标，供遥测回溯与失败对照。
    box_index: int = -1


@dataclass
class ClickPlan:
    """一次点选的完整决策，含可解释的失败原因。"""

    points: List[ClickPoint] = field(default_factory=list)
    error_code: Optional[str] = None
    confidence: float = 0.0
    #: 逐目标的判定说明，进遥测——调识别时需要知道是"没找到"还是"没认清"。
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error_code and bool(self.points)


def _box_center(box: OcrBox) -> Tuple[float, float]:
    return (box.x + box.width / 2.0, box.y + box.height / 2.0)


def _boxes_are_absolute_pixels(boxes: Sequence[OcrBox]) -> bool:
    """判断字框是像素还是归一化坐标。

    多模态引擎一律回归一化；本地 OCR 在 ROI 被裁过时可能给出像素坐标。
    判据：任一字框坐标超出 [0,1] 即认定是像素。纯归一化数据不会越界，
    所以这个探测是安全的（不会误判正常数据），代价仅是像素数据里
    恰好全部落在 0..1 的极端情况——那种图本身小到点击也无从谈起。
    """
    for box in boxes:
        if box.x > 1.0 or box.y > 1.0 or box.width > 1.0 or box.height > 1.0:
            return True
    return False


def _boxes_to_normalized(boxes: Sequence[OcrBox], width: int, height: int) -> List[OcrBox]:
    if not width or not height:
        return list(boxes)
    return [
        OcrBox(
            text=b.text,
            x=b.x / width,
            y=b.y / height,
            width=b.width / width,
            height=b.height / height,
            confidence=b.confidence,
        )
        for b in boxes
    ]


def _normalize_chars(text: str) -> str:
    """全角/半角统一，避免「Ａ」与「A」匹配失败。"""
    out: List[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII → 半角
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:  # 全角空格
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def plan_click_points(
    result: OcrResult,
    instruction: Instruction,
    *,
    image_size: Optional[Tuple[int, int]] = None,
    min_confidence: float = 0.35,
) -> ClickPlan:
    """把识别结果 + 指令变成有序点击点。纯函数，无副作用。

    ``image_size`` 仅在字框为像素坐标时需要，用于归一化。
    """
    if not instruction or not instruction.targets:
        return ClickPlan(error_code="instruction_unparsed")

    boxes = list(result.boxes or [])
    if not boxes:
        # 多模态引擎直接给中心点、无尺寸字框时，退化为零尺寸字框再走同一条路，
        # 保证下游只有一套挑选逻辑。
        if not result.text:
            return ClickPlan(error_code="no_boxes_detected")
        boxes = [
            OcrBox(text=result.text, x=0.0, y=0.0, width=0.0, height=0.0, confidence=result.confidence)
        ]

    if _boxes_are_absolute_pixels(boxes):
        if not image_size:
            return ClickPlan(error_code="pixel_boxes_without_image_size")
        boxes = _boxes_to_normalized(boxes, image_size[0], image_size[1])

    normalized = [_normalize_chars(b.text) for b in boxes]
    points: List[ClickPoint] = []
    notes: List[str] = []
    confidences: List[float] = []

    for target in instruction.targets:
        # 指令中的目标字也做全角归一，保持与字框同口径。
        needle = _normalize_chars(target)
        candidates = [(idx, box) for idx, box in enumerate(boxes) if normalized[idx] == needle]
        if not candidates:
            candidates = [
                (idx, box) for idx, box in enumerate(boxes) if needle in normalized[idx]
            ]
            if not candidates:
                notes.append(f"target={target} not_found")
                return ClickPlan(
                    error_code="target_char_not_found",
                    notes=notes,
                )
            notes.append(f"target={target} matched_by_substring")

        # 多个候选（同字重复出现）时取置信度最高的；同分取靠左的，
        # 与人眼从左到右的默认阅读序一致。
        idx, box = max(candidates, key=lambda pair: (pair[1].confidence, -pair[0]))
        if box.confidence < min_confidence:
            notes.append(f"target={target} low_confidence={box.confidence:.2f}")
            return ClickPlan(error_code="low_confidence", notes=notes)

        cx, cy = _box_center(box)
        # 坐标缺失（零尺寸且落在原点）说明模型没给位置，不能盲点。
        if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0):
            notes.append(f"target={target} out_of_bounds")
            return ClickPlan(error_code="target_out_of_bounds", notes=notes)

        points.append(
            ClickPoint(
                # 回显归一化后的字面：匹配走的是归一口径，回显若是原始全角
                # 会在遥测里看起来像"点了另一个字"。
                char=normalized[idx],
                x=cx,
                y=cy,
                confidence=box.confidence,
                box_index=idx,
            )
        )
        confidences.append(box.confidence)

    if len(points) != len(instruction.targets):
        return ClickPlan(error_code="target_count_mismatch", notes=notes)

    return ClickPlan(
        points=points,
        confidence=min(confidences) if confidences else 0.0,
        notes=notes,
    )


def _jitter(radius: float) -> Tuple[float, float]:
    return (random.uniform(-radius, radius), random.uniform(-radius, radius))


async def _humanized_click(page: Any, x: float, y: float, radius: float) -> None:
    """一次可信点击：微移 → 按下 → 微顿 → 抬起。

    逐点 await 是**故意的**：点选的时间尺度是「人看图再点」，单次点击间隔
    天然在数百毫秒量级，隧道 RTT 相对可忽略。这里不需要 0.6.24 那套拖动
    流水线——那是为了解决 25-75ms 间隔被 RTT 撕碎的问题，点选不存在这个
    问题。照搬流水线反而会把点击节奏压成机器匀速。
    """
    await page.mouse.move(x, y, steps=random.randint(2, 4))
    await asyncio.sleep(random.uniform(0.04, 0.12))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.14))
    await page.mouse.up()


class PointSelectExecutor:
    """在 Playwright 页面上执行有序点选。

    复用 ``page.mouse``（与滑块同源，``isTrusted`` 为真）。坐标换算基于目标
    元素 ``bounding_box()``：截图通常是元素截图，图内归一化坐标 × 元素
    尺寸 + 元素视口偏移 = 页面坐标。
    """

    def __init__(
        self,
        *,
        click_radius_px: float = 6.0,
        inter_click_delay: Tuple[float, float] = (0.35, 0.9),
        settle_delay_s: float = 0.6,
    ):
        self.click_radius_px = click_radius_px
        self.inter_click_delay = inter_click_delay
        self.settle_delay_s = settle_delay_s

    async def _element_box(self, target: Any) -> Optional[Dict[str, float]]:
        getter = getattr(target, "bounding_box", None)
        if getter is None:
            return None
        try:
            box = getter()
            if asyncio.iscoroutine(box):
                box = await box
        except Exception as exc:
            logger.warning(f"point-select: bounding_box failed: {exc}")
            return None
        if not isinstance(box, dict):
            return None
        if box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return None
        return box

    def to_page_coords(self, point: ClickPoint, box: Dict[str, float]) -> Tuple[float, float]:
        """图内归一化 → 页面绝对坐标。"""
        px = box["x"] + point.x * box["width"]
        py = box["y"] + point.y * box["height"]
        return (px, py)

    async def execute(
        self,
        *,
        page: Any,
        target_element: Any,
        plan: ClickPlan,
        verdict_reader: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """按序点击并（可选）读取判决码。

        返回 ``{"clicked": [...], "verdict": {...}}``。**点击序列一旦开始
        就必须点完**——点选挑战的判罚是"整体序列对不对"，中途放弃留下
        半截点击状态会让前端判为异常，比点错更容易触发惩罚。
        """
        if not plan.ok:
            return {"clicked": [], "error_code": plan.error_code, "notes": plan.notes}

        box = await self._element_box(target_element)
        if box is None:
            return {"clicked": [], "error_code": "element_box_unavailable", "notes": plan.notes}

        clicked: List[Dict[str, Any]] = []
        for index, point in enumerate(plan.points):
            if index > 0:
                await asyncio.sleep(random.uniform(*self.inter_click_delay))
            px, py = self.to_page_coords(point, box)
            dx, dy = _jitter(self.click_radius_px)
            await _humanized_click(page, px + dx, py + dy, self.click_radius_px * 0.4)
            clicked.append({"char": point.char, "x": round(px, 1), "y": round(py, 1),
                            "confidence": point.confidence})
            logger.debug(
                f"point-select: clicked {index + 1}/{len(plan.points)} "
                f"char={point.char} at ({px:.0f},{py:.0f}) conf={point.confidence:.2f}"
            )

        # 最后一点点完给前端留出校验与刷新时间。
        await asyncio.sleep(self.settle_delay_s)

        verdict: Dict[str, Any] = {}
        if verdict_reader is not None:
            try:
                verdict = await verdict_reader()
            except Exception as exc:
                logger.warning(f"point-select: verdict read failed: {exc}")
                verdict = {"error_code": "verdict_read_failed"}

        return {"clicked": clicked, "verdict": verdict, "error_code": None}


def is_punish_point_code(code: Any) -> bool:
    """点选判决码是否命中风控惩罚。复用滑块的 300 语义。"""
    return is_punish_slide_code(code) or (
        isinstance(code, (int, float)) and int(code) in PUNISH_POINT_CODES
    )