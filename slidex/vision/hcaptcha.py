"""hCaptcha 求解：协议探测 + 三型挑战（9 宫格 / 区域点选 / 拖放）。

与滑块族（``slidex.providers.*``）是**两套产品**：hCaptcha 的挑战运行在
``iframe[src^="https://newassets.hcaptcha.com/captcha/v1/"]`` 内，判题结果走
``/checkcaptcha/`` 的 ``{"pass": bool}``，成功产物是 ``h-captcha-response``
token 而非 cookie。因此它不走 ``CaptchaProvider`` 那条滑块形状的流水线，而是
作为 ``ChallengeType.HCAPTCHA`` 由 ``VisualChallengeSolver`` 分派到这里。

架构沿用本库既有的**两段式解耦**（见 ``slidex/vision/point_select.py`` 头注释）：

1. **纯规划器 / 解析器**（无页面依赖、纯函数）：
   ``plan_grid_clicks`` / ``plan_drag_gesture`` / ``parse_grid_answer`` /
   ``parse_points_answer`` / ``parse_drag_answer``。单测只覆盖这一层也能抓住
   绝大多数回归（坐标、夹取、1-based/0-based 二义、容错解析）。
2. **执行器** ``HcaptchaExecutor``：只负责「找 iframe → 截图 → 问引擎 → 按
   plan 点击/拖拽 → 提交 → 读判决」，不关心模型怎么得出答案。

设计取舍（参考 QIN2DIM/hcaptcha-challenger 的协议形状，clean-room 重写）：

- **不逆向 hsw.js / msgpack**：密文载荷绑定 hCaptcha 每次更新，且参考实现是
  GPL-3.0。本模块优先读 ``/getcaptcha/`` 的明文 JSON（拿得到就拿到
  ``request_type`` + ``requester_question``），拿不到就**直接截挑战画面 +
  从 DOM 读提示**——两条路都只依赖公开的页面结构。
- **判题走多模态 LLM**：与 challenger v0.13 的 SCoT 路线一致，复用
  ``LlmVisionExtractor.ask`` 的同一条传输管道（``SLIDEX_VISION_*``）。
- **提交后不重试**：hCaptcha 点错只会抬高后续难度，同一轮重复提交等于在风控
  前追加劣质样本。失败交给调用方按轮次决策（与 0.6.33 点选纪律一致）。

能力边界：本模块只覆盖视觉/答题层。hCaptcha 的通过率同时受浏览器指纹与 IP
信誉影响，那属于 stealth 层，不在本模块职责内。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from slidex.ocr.models import OcrResult
from slidex.vision.point_select import ClickPlan, ClickPoint, _humanized_click, _jitter

# hCaptcha 的 iframe 前缀与两种 frame。选择器同时用于主文档查询与 frame 兜底。
_FRAME_PREFIX = "https://newassets.hcaptcha.com/captcha/v1/"
_CHECKBOX_FRAME_SEL = (
    'iframe[src*="newassets.hcaptcha.com/captcha/v1/"][src*="frame=checkbox"]'
)
_CHALLENGE_FRAME_SEL = (
    'iframe[src*="newassets.hcaptcha.com/captcha/v1/"][src*="frame=challenge"]'
)
_CHECKBOX_SEL = "#checkbox"
_CHALLENGE_VIEW_SEL = ".challenge-view"
_TASKS_SEL = ".task"
_SUBMIT_SEL = "//div[@class='button-submit button']"
_SUBMIT_FALLBACK_SEL = ".button-submit"
_PROMPT_SELECTORS = (
    ".challenge-header",
    ".prompt-text",
    ".challenge-view .header",
    "div[class*='prompt']",
)
_TOKEN_SELECTORS = (
    "textarea[name='h-captcha-response']",
    "input[name='h-captcha-response']",
    "textarea[name='g-recaptcha-response']",
    "input[name='g-recaptcha-response']",
)

_GRID_SIZE = 9
_GRID_COLS = 3

# ── 判题提示词（输出键名固定英文；题面按原语种注入到末尾）──────────────
_GRID_PROMPT = (
    "这是一道 hCaptcha 九宫格验证码。图片被均分为 3x3 共 9 格，"
    "已用 1..9 的数字标注（左上为 1，按行向右递增）。\n"
    "请根据题面判断哪些格子**确实包含**题目要求的目标（例如“请点击所有包含红绿灯的图片”）。\n"
    "只输出严格 JSON，不要解释：{\"selected\":[1,4,9]}\n"
    "selected 使用 1 到 9 的编号；若所有格子都不符合，输出 {\"selected\":[]}。"
)

_POINTS_PROMPT = (
    "这是一道 hCaptcha 区域点选验证码。请根据题面找出应当点击的位置。\n"
    "坐标一律用 0 到 1 的归一化值表示，左上角为 (0,0)、右下角为 (1,1)，"
    "x/y 为目标**中心点**。\n"
    "只输出严格 JSON，不要解释：{\"points\":[{\"x\":0.42,\"y\":0.61}]}\n"
    "题面要求点几个就返回几个，按任意顺序；确实找不到时输出 {\"points\":[]}。"
)

_DRAG_PROMPT = (
    "这是一道 hCaptcha 拖放验证码。请找出应当拖动的**起点**（待移动的图块中心）"
    "与**落点**（目标轮廓中心）。\n"
    "坐标一律用 0 到 1 的归一化值表示，左上角为 (0,0)、右下角为 (1,1)。\n"
    "只输出严格 JSON，不要解释：{\"start\":{\"x\":0.22,\"y\":0.48},"
    "\"end\":{\"x\":0.71,\"y\":0.52}}\n"
    "若这不是拖放题型（或找不到起点/落点），输出 {\"unsupported\":true}。"
)


# ────────────────────── 数据结构 ──────────────────────


@dataclass
class TileSelection:
    """九宫格判题结果。flags 长度 9，True 表示该格应被点击。"""

    flags: List[bool] = field(default_factory=list)
    provider: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and len(self.flags) == _GRID_SIZE


@dataclass
class DragGesture:
    """一次拖放：归一化起止点 + 沿路径的归一化点列（含首尾）。"""

    start: Tuple[float, float]
    end: Tuple[float, float]
    points: List[Tuple[float, float]] = field(default_factory=list)
    provider: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and bool(self.points)


# ────────────────────── 纯规划器 ──────────────────────


def plan_grid_clicks(
    flags: Sequence[bool],
    *,
    confidence: float = 1.0,
    fallback_index: int = 0,
) -> ClickPlan:
    """9 个真值 → 格子中心点击计划。

    全 False 时**兜底点第 ``fallback_index`` 格**：hCaptcha 对「一格未选就提交」
    几乎必然判空作答，点一格再交至少进入正常判题流程（与参考实现的取舍一致）。
    """
    values = [bool(flag) for flag in flags]
    if len(values) != _GRID_SIZE:
        return ClickPlan(
            points=[],
            error_code="grid_size_mismatch",
            confidence=0.0,
            notes=[f"expected {_GRID_SIZE} flags, got {len(values)}"],
        )

    indices = [i for i, flag in enumerate(values) if flag]
    notes: List[str] = []
    if not indices:
        indices = [max(0, min(_GRID_SIZE - 1, int(fallback_index)))]
        notes.append("no positive tiles; fallback to one tile to avoid empty submission")

    points: List[ClickPoint] = []
    for index in indices:
        col = index % _GRID_COLS
        row = index // _GRID_COLS
        points.append(
            ClickPoint(
                char=f"tile{index + 1}",
                x=(col + 0.5) / _GRID_COLS,
                y=(row + 0.5) / _GRID_COLS,
                confidence=confidence,
                box_index=index,
            )
        )
    return ClickPlan(points=points, confidence=confidence, notes=notes)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _smootherstep(t: float) -> float:
    """两端一阶导为 0 的缓动：拖拽起止自然减速，中段更快。"""
    t = _clamp01(t)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def plan_drag_gesture(
    start: Tuple[float, float],
    end: Tuple[float, float],
    *,
    steps: int = 25,
    jitter: float = 0.004,
    confidence: float = 1.0,
) -> DragGesture:
    """归一化起止点 → 二次贝塞尔拖拽点列（起止缓动）。

    纯函数：同样的输入与 jitter=0 时输出逐值一致，便于单测锚定。
    """
    sx, sy = _clamp01(start[0]), _clamp01(start[1])
    ex, ey = _clamp01(end[0]), _clamp01(end[1])
    if math.hypot(ex - sx, ey - sy) < 1e-6:
        return DragGesture(
            start=(sx, sy),
            end=(ex, ey),
            points=[],
            confidence=0.0,
            error_code="degenerate_drag_path",
        )

    steps = max(4, int(steps))
    mid_x = (sx + ex) / 2.0
    mid_y = (sy + ey) / 2.0
    dx, dy = ex - sx, ey - sy
    distance = math.hypot(dx, dy)
    # 垂直方向的控制点偏移（确定性，不用随机）：让路径略有弧度而非直线。
    ctrl_x = mid_x - dy / distance * distance * 0.15
    ctrl_y = mid_y + dx / distance * distance * 0.15

    points: List[Tuple[float, float]] = []
    for i in range(steps + 1):
        t = _smootherstep(i / steps)
        one_minus = 1.0 - t
        x = one_minus * one_minus * sx + 2 * one_minus * t * ctrl_x + t * t * ex
        y = one_minus * one_minus * sy + 2 * one_minus * t * ctrl_y + t * t * ey
        if 0 < i < steps and jitter > 0:
            x += random.uniform(-jitter, jitter)
            y += random.uniform(-jitter, jitter)
        points.append((_clamp01(x), _clamp01(y)))
    # 端点强制精确，避免抖动/浮点误差把落点推离目标。
    points[0] = (sx, sy)
    points[-1] = (ex, ey)
    return DragGesture(
        start=(sx, sy),
        end=(ex, ey),
        points=points,
        confidence=confidence,
    )


# ────────────────────── 容错解析 ──────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.search(text or "")
    return (match.group(1) if match else text or "").strip()


def _loads_lenient(text: str) -> Any:
    cleaned = _strip_fence(text)
    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass
    # 退化：抓取第一个 {...} 或 [...] 结构再试一次（模型偶发前后缀说明）。
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except (ValueError, TypeError):
                continue
    return None


def _as_int_list(value: Any) -> Optional[List[int]]:
    if not isinstance(value, list):
        return None
    out: List[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            return None
    return out


def parse_grid_answer(text: str) -> Optional[List[bool]]:
    """解析九宫格答案 → 长度 9 的 bool 列表；解析不出返回 None。

    支持：``{"selected":[1,4,9]}``、``{"tiles":[true,...]}``、裸 ``[0,1,...]``、
    ``1,3,5`` / ``1 3 5`` 文本。索引默认 **1-based**（与提示词一致）；一旦出现
    0 则整体按 0-based 解释。
    """
    payload = _loads_lenient(text)

    if isinstance(payload, dict):
        for key in ("tiles", "flags", "selected", "indices", "grid"):
            if key in payload:
                payload = payload[key]
                break
        else:
            payload = None

    if isinstance(payload, list) and payload and all(isinstance(v, bool) for v in payload):
        if len(payload) != _GRID_SIZE:
            return None
        return list(payload)

    indices = _as_int_list(payload)
    if indices is None:
        # 纯文本索引列表
        numbers = re.findall(r"-?\d+", _strip_fence(text))
        if not numbers or len(numbers) > _GRID_SIZE:
            return None
        indices = [int(n) for n in numbers]

    zero_based = any(value == 0 for value in indices)
    flags = [False] * _GRID_SIZE
    hit = False
    for value in indices:
        index = value if zero_based else value - 1
        if 0 <= index < _GRID_SIZE:
            flags[index] = True
            hit = True
    if hit:
        return flags
    # 显式空选择（``{"selected":[]}``）是合法答案：全部为 False 必须能传到
    # plan_grid_clicks 的兜底逻辑，否则「模型明确说没有目标」会被误报成
    # 解析失败。全越界索引仍视为无效输出。
    return flags if indices == [] else None


def _as_point(item: Any) -> Optional[ClickPoint]:
    if isinstance(item, dict):
        x, y = item.get("x"), item.get("y")
        conf = item.get("confidence", 1.0)
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        x, y = item[0], item[1]
        conf = 1.0
    else:
        return None
    try:
        px, py = float(x), float(y)
        pconf = float(conf)
    except (TypeError, ValueError):
        return None
    return ClickPoint(char="point", x=_clamp01(px), y=_clamp01(py), confidence=pconf)


def parse_points_answer(text: str) -> Optional[List[ClickPoint]]:
    """解析区域点选答案 → ClickPoint 列表；解析不出返回 None。"""
    payload = _loads_lenient(text)
    if isinstance(payload, dict):
        payload = payload.get("points", payload.get("targets"))
    if not isinstance(payload, list):
        return None

    points: List[ClickPoint] = []
    for item in payload:
        point = _as_point(item)
        if point is not None:
            points.append(point)
    return points or None


def parse_drag_answer(text: str) -> Optional[DragGesture]:
    """解析拖放答案 → DragGesture；不是拖放题型或解析不出返回 None。"""
    payload = _loads_lenient(text)
    if isinstance(payload, dict):
        if payload.get("unsupported"):
            return None
        start = payload.get("start")
        end = payload.get("end") or payload.get("target")
    elif isinstance(payload, list) and len(payload) >= 2:
        start, end = payload[0], payload[1]
    else:
        return None

    start_point = _as_point(start)
    end_point = _as_point(end)
    if start_point is None or end_point is None:
        return None
    gesture = plan_drag_gesture((start_point.x, start_point.y), (end_point.x, end_point.y))
    if not gesture.ok:
        return None
    gesture.confidence = min(start_point.confidence, end_point.confidence)
    return gesture


# ────────────────────── frame / DOM 探测 ──────────────────────


def _frame_matches(frame: Any, needle: str) -> bool:
    url = str(getattr(frame, "url", "") or "")
    return url.startswith(_FRAME_PREFIX) and needle in url


async def _frame_from_selector(page: Any, selector: str, needle: str) -> Any:
    try:
        element = await page.query_selector(selector)
    except Exception as exc:  # pragma: no cover - 页面异常保护
        logger.debug(f"hcaptcha: query_selector failed: {exc}")
        return None
    if element is None:
        return None
    content_frame = getattr(element, "content_frame", None)
    if content_frame is None:
        return None
    try:
        return await content_frame()
    except Exception:  # pragma: no cover
        return None


async def _find_frame(page: Any, needle: str, selector: str) -> Any:
    # 1) 直接遍历 page.frames（最稳，跨 OOPIF 适用）。
    try:
        for frame in list(getattr(page, "frames", []) or []):
            if _frame_matches(frame, needle):
                return frame
    except Exception:  # pragma: no cover
        pass
    # 2) 主文档 iframe 元素 → content_frame。
    return await _frame_from_selector(page, selector, needle)


async def find_checkbox_frame(page: Any) -> Any:
    """返回 checkbox 的 iframe（``frame=checkbox``），找不到返回 None。"""
    return await _find_frame(page, "frame=checkbox", _CHECKBOX_FRAME_SEL)


async def find_challenge_frame(page: Any) -> Any:
    """返回挑战的 iframe（``frame=challenge``），找不到返回 None。"""
    return await _find_frame(page, "frame=challenge", _CHALLENGE_FRAME_SEL)


async def read_question(frame: Any) -> str:
    """从挑战 iframe 的 DOM 读题面文案；读不到返回空串。"""
    if frame is None:
        return ""
    for selector in _PROMPT_SELECTORS:
        try:
            locator = frame.locator(selector)
            if await locator.count() == 0:
                continue
            text = await locator.first.inner_text()
        except Exception:
            continue
        cleaned = " ".join(str(text or "").split())
        if cleaned:
            return cleaned
    return ""


async def count_tasks(frame: Any) -> int:
    """当前 crumb 的九宫格数量（0 表示不是 binary 型）。"""
    if frame is None:
        return 0
    try:
        return int(await frame.locator(_TASKS_SEL).count())
    except Exception:
        return 0


async def _crumb_count(frame: Any) -> int:
    try:
        count = int(await frame.locator(".Crumb").count())
    except Exception:
        count = 0
    return max(1, count)


async def _is_challenge_visible(frame: Any) -> bool:
    if frame is None:
        return False
    try:
        return bool(await frame.locator(_CHALLENGE_VIEW_SEL).first.is_visible())
    except Exception:
        return False


async def _read_token(page: Any) -> str:
    for selector in _TOKEN_SELECTORS:
        try:
            element = await page.query_selector(selector)
        except Exception:
            continue
        if element is None:
            continue
        for getter in ("input_value",):
            try:
                value = await getattr(element, getter)()
                if isinstance(value, str) and value.strip():
                    return value.strip()
            except Exception:
                pass
        try:
            value = await element.get_attribute("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:
            pass
    return ""


# ────────────────────── 识别引擎（LLM 适配器）──────────────────────


def _default_extractor() -> Any:
    # 延迟导入：solver 反向依赖本模块，模块级导入会成环。
    from slidex.vision.solver import _default_vision_extractor

    return _default_vision_extractor()


class LlmHcaptchaEngine:
    """把三种题型翻译成 ``LlmVisionExtractor.ask`` 的提示词协议。

    刻意只依赖 ``ask`` 一个方法：换引擎（本地 ONNX / 其它多模态）只需实现同样
    三个方法，执行器无需改动。
    """

    def __init__(self, extractor: Any = None):
        self._extractor = extractor

    @property
    def extractor(self) -> Any:
        if self._extractor is None:
            self._extractor = _default_extractor()
        return self._extractor

    def _ask(self, image_bytes: bytes, prompt: str) -> OcrResult:
        extractor = self.extractor
        ask = getattr(extractor, "ask", None)
        if not callable(ask):
            return OcrResult(
                text="",
                confidence=0.0,
                provider="llm-vision",
                metadata={"error_code": "engine_lacks_ask"},
            )
        return ask(image_bytes=image_bytes, prompt=prompt)

    @staticmethod
    def _with_question(prompt: str, question: str) -> str:
        if question:
            return f"{prompt}\n\n题面原文：{question}"
        return prompt

    def select_tiles(
        self,
        image_bytes: bytes,
        question: str,
        *,
        prompt: Optional[str] = None,
    ) -> TileSelection:
        result = self._ask(image_bytes, self._with_question(prompt or _GRID_PROMPT, question))
        error = (result.metadata or {}).get("error_code")
        if error:
            return TileSelection(provider=result.provider, metadata=result.metadata, error_code=str(error))
        flags = parse_grid_answer(result.text)
        if flags is None:
            return TileSelection(
                provider=result.provider,
                metadata={**result.metadata, "raw_excerpt": (result.text or "")[:200]},
                error_code="vision_unparseable",
            )
        return TileSelection(
            flags=flags,
            provider=result.provider,
            confidence=result.confidence,
            metadata=result.metadata,
        )

    def locate_points(
        self,
        image_bytes: bytes,
        question: str,
        *,
        prompt: Optional[str] = None,
    ) -> ClickPlan:
        result = self._ask(image_bytes, self._with_question(prompt or _POINTS_PROMPT, question))
        error = (result.metadata or {}).get("error_code")
        if error:
            return ClickPlan(points=[], error_code=str(error), confidence=0.0)
        points = parse_points_answer(result.text)
        if not points:
            return ClickPlan(
                points=[],
                error_code="vision_unparseable",
                confidence=0.0,
                notes=[(result.text or "")[:120]],
            )
        confidence = min(point.confidence for point in points)
        return ClickPlan(points=points, confidence=confidence, notes=[])

    def locate_drag(
        self,
        image_bytes: bytes,
        question: str,
        *,
        prompt: Optional[str] = None,
    ) -> DragGesture:
        result = self._ask(image_bytes, self._with_question(prompt or _DRAG_PROMPT, question))
        error = (result.metadata or {}).get("error_code")
        if error:
            return DragGesture(start=(0.0, 0.0), end=(0.0, 0.0), error_code=str(error))
        gesture = parse_drag_answer(result.text)
        if gesture is None:
            return DragGesture(
                start=(0.0, 0.0), end=(0.0, 0.0), error_code="not_a_drag_challenge"
            )
        gesture.provider = result.provider
        return gesture


# ────────────────────── 执行器 ──────────────────────


class HcaptchaExecutor:
    """在活页面上完成一次 hCaptcha 挑战。

    不判断成功与否的「业务含义」——只把判决原样带回，由
    ``VisualChallengeSolver`` 映射成 ``VisualChallengeResult``。
    """

    def __init__(
        self,
        vision_engine: Any = None,
        *,
        click_radius_px: float = 6.0,
        inter_click_delay: Tuple[float, float] = (0.35, 0.9),
        tile_padding_px: float = 3.0,
        max_rounds: int = 3,
        wait_challenge_s: float = 8.0,
        settle_s: float = 0.35,
    ):
        self.vision_engine = vision_engine
        self.click_radius_px = click_radius_px
        self.inter_click_delay = inter_click_delay
        self.tile_padding_px = tile_padding_px
        self.max_rounds = max(1, int(max_rounds))
        self.wait_challenge_s = max(1.0, float(wait_challenge_s))
        self.settle_s = max(0.0, float(settle_s))
        self._verdict: Optional[Dict[str, Any]] = None
        self._payload: Optional[Dict[str, Any]] = None
        self._answers: List[Dict[str, Any]] = []

    # ── 引擎 ──
    def _engine(self) -> Any:
        if self.vision_engine is None:
            self.vision_engine = LlmHcaptchaEngine()
        return self.vision_engine

    # ── 网络：明文 JSON 载荷 + /checkcaptcha/ 判决 ──
    def _attach_listeners(self, page: Any) -> Any:
        async def _handle(response: Any) -> None:
            url = str(getattr(response, "url", "") or "")
            try:
                if "/getcaptcha/" in url:
                    headers = getattr(response, "headers", {}) or {}
                    if "json" in str(headers.get("content-type", "")).lower():
                        data = await response.json()
                        if isinstance(data, dict) and not data.get("pass"):
                            payload = dict(data)
                            payload.pop("c", None)  # 签名载荷不落地，避免泄露/体积
                            self._payload = payload
                elif "/checkcaptcha/" in url:
                    data = await response.json()
                    if isinstance(data, dict):
                        self._verdict = {
                            "pass": bool(data.get("pass")),
                            "error": str(data.get("error") or ""),
                            "has_pass_uuid": bool(data.get("generated_pass_UUID")),
                        }
            except Exception as exc:  # pragma: no cover - 页面异常保护
                logger.debug(f"hcaptcha: response handler skipped {url}: {exc}")

        def handler(response: Any) -> None:
            try:
                task = asyncio.get_running_loop().create_task(_handle(response))
            except RuntimeError:
                return
            task.add_done_callback(lambda _t: None)

        try:
            page.on("response", handler)
        except Exception:  # pragma: no cover
            return None
        return handler

    @staticmethod
    def _detach_listeners(page: Any, handler: Any) -> None:
        if handler is None:
            return
        try:
            page.remove_listener("response", handler)
        except Exception:  # pragma: no cover
            pass

    def _question_from_payload(self, language: str) -> str:
        payload = self._payload or {}
        question = payload.get("requester_question")
        if not isinstance(question, dict) or not question:
            return ""
        if language and language != "auto":
            text = question.get(language)
            if text:
                return " ".join(str(text).split())
        for key in ("zh", "zh-cn", "zh-CN", "en"):
            text = question.get(key)
            if text:
                return " ".join(str(text).split())
        return ""

    # ── 点击 / 拖拽 ──
    async def _click_box(
        self,
        page: Any,
        box: Optional[Dict[str, float]],
        *,
        radius: Optional[float] = None,
    ) -> bool:
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return False
        cx = box["x"] + box["width"] / 2.0
        cy = box["y"] + box["height"] / 2.0
        # 九宫格用更小的抖动上限（tile_padding_px）：格子本身比按钮小得多，
        # 沿用 checkbox 的 6px 容易跨到邻格。
        limit = self.click_radius_px if radius is None else radius
        dx, dy = _jitter(min(limit, box["width"] / 2.0, box["height"] / 2.0))
        await _humanized_click(page, cx + dx, cy + dy, limit * 0.4)
        return True

    async def _click_tiles(self, page: Any, frame: Any, plan: ClickPlan) -> List[Dict[str, Any]]:
        tasks = frame.locator(_TASKS_SEL)
        clicked: List[Dict[str, Any]] = []
        for order, point in enumerate(plan.points):
            index = point.box_index if point.box_index >= 0 else order
            try:
                box = await tasks.nth(index).bounding_box()
            except Exception:
                box = None
            if not await self._click_box(page, box, radius=self.tile_padding_px):
                continue
            clicked.append({"tile": index + 1, "x": round(box["x"], 1), "y": round(box["y"], 1)})
            if order < len(plan.points) - 1:
                await asyncio.sleep(random.uniform(*self.inter_click_delay))
        return clicked

    async def _click_points(
        self, page: Any, frame: Any, plan: ClickPlan
    ) -> List[Dict[str, Any]]:
        try:
            box = await frame.locator(_CHALLENGE_VIEW_SEL).first.bounding_box()
        except Exception:
            box = None
        if not box:
            return []
        clicked: List[Dict[str, Any]] = []
        for order, point in enumerate(plan.points):
            px = box["x"] + point.x * box["width"]
            py = box["y"] + point.y * box["height"]
            dx, dy = _jitter(self.click_radius_px)
            await _humanized_click(page, px + dx, py + dy, self.click_radius_px * 0.4)
            clicked.append({"x": round(px, 1), "y": round(py, 1)})
            if order < len(plan.points) - 1:
                await asyncio.sleep(random.uniform(*self.inter_click_delay))
        return clicked

    async def _drag(self, page: Any, frame: Any, gesture: DragGesture) -> bool:
        try:
            box = await frame.locator(_CHALLENGE_VIEW_SEL).first.bounding_box()
        except Exception:
            box = None
        if not box or not gesture.points:
            return False
        coords = [
            (box["x"] + x * box["width"], box["y"] + y * box["height"])
            for x, y in gesture.points
        ]
        start_x, start_y = coords[0]
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await page.mouse.down()
        for x, y in coords[1:]:
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.012, 0.028))
        await asyncio.sleep(random.uniform(0.05, 0.1))
        await page.mouse.up()
        return True

    async def _submit(self, page: Any, frame: Any) -> bool:
        for selector in (_SUBMIT_SEL, _SUBMIT_FALLBACK_SEL):
            try:
                locator = frame.locator(selector)
                if await locator.count() == 0:
                    continue
                box = await locator.first.bounding_box()
            except Exception:
                continue
            if await self._click_box(page, box):
                return True
        return False

    # ── 单 crumb 解题 ──
    async def _challenge_signature(self, frame: Any) -> Optional[str]:
        """挑战画面的指纹。用于区分「已换下一 crumb」与「同一题还在原地」。"""
        try:
            image = await frame.locator(_CHALLENGE_VIEW_SEL).first.screenshot(type="png")
        except Exception:
            return None
        if not image:
            return None
        return hashlib.sha1(image).hexdigest()

    async def _solve_crumb(
        self,
        page: Any,
        frame: Any,
        question: str,
        prompt: Optional[str],
    ) -> Dict[str, Any]:
        try:
            image_bytes = await frame.locator(_CHALLENGE_VIEW_SEL).first.screenshot(type="png")
        except Exception as exc:
            logger.warning(f"hcaptcha: challenge screenshot failed: {exc}")
            return {"error_code": "screenshot_failed"}

        tasks = await count_tasks(frame)
        engine = self._engine()

        if tasks == _GRID_SIZE:
            selection = engine.select_tiles(image_bytes, question, prompt=prompt)
            if not selection.ok:
                return {
                    "error_code": selection.error_code or "recognition_failed",
                    "engine_provider": selection.provider,
                    "engine_meta": selection.metadata,
                }
            plan = plan_grid_clicks(selection.flags, confidence=selection.confidence)
            if not plan.ok:
                return {"error_code": plan.error_code or "plan_failed"}
            clicked = await self._click_tiles(page, frame, plan)
            mode = "binary"
            engine_provider = selection.provider
        else:
            # 先试拖放，再退化到点选：两型在 DOM 上无法可靠区分（都是
            # .challenge-view 内的一张合成图），只能让引擎按题面判。
            gesture = engine.locate_drag(image_bytes, question, prompt=prompt)
            if gesture.ok and await self._drag(page, frame, gesture):
                clicked = [{"drag": [list(gesture.start), list(gesture.end)]}]
                mode = "drag"
                engine_provider = gesture.provider
            else:
                points = engine.locate_points(image_bytes, question, prompt=prompt)
                if not points.ok:
                    return {
                        "error_code": points.error_code or "recognition_failed",
                        "engine_provider": "",
                    }
                clicked = await self._click_points(page, frame, points)
                mode = "point"
                engine_provider = ""

        await asyncio.sleep(self.settle_s)
        # 提交前给画面留指纹：提交后若无判决，用它与当时画面比对，判断是
        # 「换到下一页」还是「同一题原地不动」。后者绝不能重解重提——同一份
        # 答案再交一次只会追加一份被判样本。
        signature = await self._challenge_signature(frame)
        submitted = await self._submit(page, frame)
        if not submitted:
            return {"error_code": "submit_not_found", "mode": mode, "engine_provider": engine_provider}
        return {
            "error_code": None,
            "mode": mode,
            "clicked": clicked,
            "engine_provider": engine_provider,
            "submitted": True,
            "signature": signature,
        }

    async def _await_verdict(self, page: Any, frame: Any, timeout_s: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + max(0.1, timeout_s)
        while time.monotonic() < deadline:
            if self._verdict is not None:
                return {"verdict": self._verdict}
            token = await _read_token(page)
            if token:
                return {"verdict": {"pass": True, "token": bool(token)}}
            # 出现新 crumb（题面刷新）说明上一页已提交成功，继续解下一页。
            if frame is not None and await _is_challenge_visible(frame):
                tasks = await count_tasks(frame)
                if tasks == _GRID_SIZE or await _is_challenge_visible(frame):
                    await asyncio.sleep(self.settle_s)
                    return None
            await asyncio.sleep(0.2)
        return None

    async def solve(
        self,
        page: Any,
        *,
        question_language: str = "auto",
        max_rounds: Optional[int] = None,
        prompt: Optional[str] = None,
        challenge_iframe: Any = None,
    ) -> Dict[str, Any]:
        rounds = max(1, int(max_rounds or self.max_rounds))
        self._verdict = None
        self._payload = None
        handler = self._attach_listeners(page)
        started = time.monotonic()
        try:
            # 1) 点 checkbox（可见才点；不可见/invisible 模式直接等挑战或 token）
            checkbox_frame = await find_checkbox_frame(page)
            if checkbox_frame is not None:
                try:
                    checkbox = checkbox_frame.locator(_CHECKBOX_SEL)
                    if await checkbox.count() and await checkbox.first.is_visible():
                        await self._click_box(page, await checkbox.first.bounding_box())
                except Exception as exc:
                    logger.debug(f"hcaptcha: checkbox click skipped: {exc}")

            # 2) 等挑战出现（或 token 出现）
            frame = challenge_iframe
            deadline = time.monotonic() + self.wait_challenge_s
            while time.monotonic() < deadline:
                if frame is None:
                    frame = await find_challenge_frame(page)
                if frame is not None and (
                    await count_tasks(frame) == _GRID_SIZE or await _is_challenge_visible(frame)
                ):
                    break
                token = await _read_token(page)
                if token:
                    return {
                        "success": True,
                        "mode": "invisible",
                        "question": "",
                        "crumbs": 0,
                        "rounds": 0,
                        "verdict": {"pass": True, "token": True},
                        "pass_token": True,
                        "error_code": None,
                    }
                await asyncio.sleep(0.25)

            if frame is None and not await _is_challenge_visible(frame):
                return {
                    "success": False,
                    "mode": "",
                    "error_code": "hcaptcha_challenge_timeout",
                    "question": "",
                    "crumbs": 0,
                    "rounds": 0,
                    "verdict": {},
                    "pass_token": False,
                }

            crumbs = await _crumb_count(frame)
            question = await read_question(frame) or self._question_from_payload(question_language)
            step = 0
            max_steps = rounds * max(1, crumbs) * 2
            last_mode = ""
            clicked_total: List[Dict[str, Any]] = []

            while step < max_steps:
                step += 1
                if self._verdict is not None:
                    # 判决可能在上一次轮询窗口的缝隙里到达：必须按 pass 值
                    # 判成败，不能把「已收到判决」直接当成通过。
                    if bool(self._verdict.get("pass")):
                        return self._final(True, last_mode, question, crumbs, step, clicked_total)
                    return {
                        "success": False,
                        "mode": last_mode,
                        "question": question,
                        "crumbs": crumbs,
                        "rounds": step,
                        "clicked": clicked_total,
                        "verdict": self._verdict,
                        "pass_token": False,
                        "error_code": "hcaptcha_failed",
                    }
                if not question:
                    question = await read_question(frame) or self._question_from_payload(question_language)

                outcome = await self._solve_crumb(page, frame, question, prompt)
                last_mode = outcome.get("mode", last_mode)
                clicked_total.extend(outcome.get("clicked") or [])
                if outcome.get("error_code"):
                    return {
                        "success": False,
                        "mode": last_mode,
                        "question": question,
                        "crumbs": crumbs,
                        "rounds": step,
                        "clicked": clicked_total,
                        "verdict": self._verdict or {},
                        "pass_token": False,
                        "error_code": outcome["error_code"],
                        "engine_provider": outcome.get("engine_provider", ""),
                    }

                verdict = await self._await_verdict(page, frame, timeout_s=3.0)
                if verdict is not None:
                    passed = bool((verdict.get("verdict") or {}).get("pass"))
                    if passed:
                        return self._final(True, last_mode, question, crumbs, step, clicked_total)
                    return {
                        "success": False,
                        "mode": last_mode,
                        "question": question,
                        "crumbs": crumbs,
                        "rounds": step,
                        "clicked": clicked_total,
                        "verdict": self._verdict or {},
                        "pass_token": False,
                        "error_code": "hcaptcha_failed" if self._verdict else "hcaptcha_verdict_pending",
                    }

                # 无判决：只有题面确实换了（下一 crumb）才继续；画面原地不动
                # 说明这次提交没被接受，重解重提只会追加一份被判样本，停手。
                current = await self._challenge_signature(frame)
                if current is None or current == outcome.get("signature"):
                    return {
                        "success": False,
                        "mode": last_mode,
                        "question": question,
                        "crumbs": crumbs,
                        "rounds": step,
                        "clicked": clicked_total,
                        "verdict": self._verdict or {},
                        "pass_token": False,
                        "error_code": "hcaptcha_verdict_pending",
                    }

            return {
                "success": False,
                "mode": last_mode,
                "question": question,
                "crumbs": crumbs,
                "rounds": step,
                "clicked": clicked_total,
                "verdict": self._verdict or {},
                "pass_token": False,
                "error_code": "hcaptcha_verdict_pending",
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }
        finally:
            self._detach_listeners(page, handler)

    def _final(
        self,
        success: bool,
        mode: str,
        question: str,
        crumbs: int,
        rounds: int,
        clicked: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "success": success,
            "mode": mode,
            "question": question,
            "crumbs": crumbs,
            "rounds": rounds,
            "clicked": clicked,
            "verdict": self._verdict or {},
            "pass_token": bool((self._verdict or {}).get("has_pass_uuid")) or success,
            "error_code": None,
        }


__all__ = [
    "TileSelection",
    "DragGesture",
    "plan_grid_clicks",
    "plan_drag_gesture",
    "parse_grid_answer",
    "parse_points_answer",
    "parse_drag_answer",
    "find_checkbox_frame",
    "find_challenge_frame",
    "read_question",
    "count_tasks",
    "LlmHcaptchaEngine",
    "HcaptchaExecutor",
]
