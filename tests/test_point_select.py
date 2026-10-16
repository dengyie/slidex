"""点选规划器 / 执行器 —— 纯函数与坐标换算的回归锁定。

规划器（plan_click_points）是纯函数：不碰浏览器、不发网络。引擎可换
（多模态/本地 OCR/假实现）而它不变，这是点选能做通用能力的关键，因此
这里的测试全部以 OcrResult 为输入，不依赖任何真实识别服务。
"""
import asyncio

import pytest

from slidex.ocr.instruction import Instruction, parse_instruction
from slidex.ocr.models import OcrBox, OcrResult
from slidex.vision.point_select import (
    ClickPlan,
    PointSelectExecutor,
    is_punish_point_code,
    plan_click_points,
)


def _box(text, x, y, w=0.1, h=0.2, conf=0.95):
    return OcrBox(text=text, x=x, y=y, width=w, height=h, confidence=conf)


def _instruction(text="请依次点击【的】【了】【我】"):
    parsed = parse_instruction(text)
    assert parsed is not None
    return parsed


def test_plans_targets_in_order():
    result = OcrResult(
        text="的了我你我好",
        confidence=0.95,
        boxes=[_box("的", 0.1, 0.5), _box("了", 0.4, 0.5), _box("我", 0.7, 0.5)],
        provider="test",
    )
    plan = plan_click_points(result, _instruction())
    assert plan.ok, plan.error_code
    assert [p.char for p in plan.points] == ["的", "了", "我"]
    # 顺序必须跟指令一致，不能按框在图里的位置重排
    assert plan.points[0].x == pytest.approx(0.15)
    assert plan.points[2].x == pytest.approx(0.75)


def test_picks_highest_confidence_when_char_repeats():
    """同字多次出现时取置信度最高的——点错一个即整体失败。"""
    result = OcrResult(
        text="的",
        confidence=0.95,
        boxes=[_box("的", 0.1, 0.5, conf=0.60), _box("的", 0.8, 0.5, conf=0.99)],
        provider="test",
    )
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))
    assert plan.ok
    assert plan.points[0].x == pytest.approx(0.85)
    assert plan.points[0].confidence == pytest.approx(0.99)


def test_low_confidence_blocks_instead_of_clicking():
    """置信度不足必须停手换通道，不能硬点（0.6.28 重试纪律）。"""
    result = OcrResult(
        text="的",
        confidence=0.2,
        boxes=[_box("的", 0.1, 0.5, conf=0.2)],
        provider="test",
    )
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))
    assert not plan.ok
    assert plan.error_code == "low_confidence"


def test_missing_target_reports_error_not_wrong_point():
    result = OcrResult(
        text="的了我",
        confidence=0.95,
        boxes=[_box("的", 0.1, 0.5), _box("了", 0.4, 0.5)],
        provider="test",
    )
    plan = plan_click_points(result, Instruction(targets=("的", "我"), raw="的 我"))
    assert not plan.ok
    assert plan.error_code == "target_char_not_found"
    assert any("not_found" in n for n in plan.notes)


def test_empty_instruction_yields_error():
    """空目标不可被当成「无需点击」——那会把解析失败静默成通过。"""
    result = OcrResult(text="的", confidence=0.9, boxes=[_box("的", 0.1, 0.5)], provider="test")
    plan = plan_click_points(result, Instruction(targets=(), raw=""))
    assert not plan.ok
    assert plan.error_code == "instruction_unparsed"


def test_pixel_boxes_are_normalized():
    """像素字框按图片尺寸归一化——截图与页面可能有缩放差异。"""
    result = OcrResult(
        text="的",
        confidence=0.95,
        boxes=[_box("的", 200, 300, w=40, h=60, conf=0.95)],  # 明显 >1.0 → 像素
        provider="test",
    )
    plan = plan_click_points(
        result, Instruction(targets=("的",), raw="的"), image_size=(400, 600)
    )
    assert plan.ok, plan.error_code
    # 中心 = (200+20, 300+30) / (400,600) = (0.55, 0.55)
    assert plan.points[0].x == pytest.approx(0.55)
    assert plan.points[0].y == pytest.approx(0.55)


def test_pixel_boxes_without_size_refuse():
    """像素字框缺尺寸时拒绝——盲点比漏认更糟。"""
    result = OcrResult(
        text="的", confidence=0.95, boxes=[_box("的", 200, 300, w=40, h=60)], provider="test"
    )
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))
    assert not plan.ok
    assert plan.error_code == "pixel_boxes_without_image_size"


def test_fullwidth_target_matches_halfwidth_box():
    """指令里全角字与图中半角字应归一后匹配。"""
    result = OcrResult(
        text="AB", confidence=0.95, boxes=[_box("A", 0.2, 0.5), _box("B", 0.6, 0.5)],
        provider="test",
    )
    plan = plan_click_points(result, Instruction(targets=("Ａ", "Ｂ"), raw="AB"))
    assert plan.ok, plan.error_code
    assert [p.char for p in plan.points] == ["A", "B"]


def test_no_boxes_detected():
    result = OcrResult(text="", confidence=0.0, boxes=[], provider="test")
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))
    assert not plan.ok
    assert plan.error_code == "no_boxes_detected"


def test_to_page_coords_maps_into_element():
    """图内归一化 → 页面绝对坐标：元素偏移 + 比例缩放。"""
    plan = ClickPlan(points=[])
    executor = PointSelectExecutor()
    box = {"x": 100, "y": 200, "width": 400, "height": 200}
    from slidex.vision.point_select import ClickPoint

    point = ClickPoint(char="的", x=0.25, y=0.5, confidence=0.9)
    px, py = executor.to_page_coords(point, box)
    assert px == pytest.approx(100 + 0.25 * 400)
    assert py == pytest.approx(200 + 0.5 * 200)


def test_punish_code_reuses_slider_semantics():
    assert is_punish_point_code(300) is True
    assert is_punish_point_code("300") is True
    assert is_punish_point_code(0) is False
    assert is_punish_point_code(None) is False


class _FakeMouse:
    def __init__(self):
        self.calls = []

    async def move(self, x, y, steps=1):
        self.calls.append(("move", x, y, steps))

    async def down(self):
        self.calls.append(("down",))

    async def up(self):
        self.calls.append(("up",))


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 200, "height": 100}

    async def screenshot(self):
        return b"fake-png"


@pytest.mark.asyncio
async def test_executor_clicks_every_target_in_order():
    page = _FakePage()
    result = OcrResult(
        text="的了我",
        confidence=0.95,
        boxes=[_box("的", 0.25, 0.5), _box("了", 0.5, 0.5), _box("我", 0.75, 0.5)],
        provider="test",
    )
    plan = plan_click_points(result, _instruction())
    assert plan.ok

    outcome = await PointSelectExecutor().execute(
        page=page, target_element=page, plan=plan
    )
    assert outcome["error_code"] is None
    assert [c["char"] for c in outcome["clicked"]] == ["的", "了", "我"]
    # 每次点击必须是 down→up 配对（人工化点击的可信输入语义）
    downs = sum(1 for c in page.mouse.calls if c[0] == "down")
    ups = sum(1 for c in page.mouse.calls if c[0] == "up")
    assert downs == 3 and ups == 3


@pytest.mark.asyncio
async def test_executor_refuses_bad_plan_without_clicking():
    """计划不可信时一个点击都不能发——半截/错误点击比不点更糟。"""
    page = _FakePage()
    outcome = await PointSelectExecutor().execute(
        page=page,
        target_element=page,
        plan=ClickPlan(error_code="low_confidence"),
    )
    assert outcome["error_code"] == "low_confidence"
    assert outcome["clicked"] == []
    assert page.mouse.calls == []


@pytest.mark.asyncio
async def test_executor_reports_missing_element_box():
    class _NoBox:
        def __init__(self):
            self.mouse = _FakeMouse()

        async def bounding_box(self):
            return None

    result = OcrResult(text="的", confidence=0.95, boxes=[_box("的", 0.5, 0.5)], provider="test")
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))
    outcome = await PointSelectExecutor().execute(
        page=_NoBox(), target_element=_NoBox(), plan=plan
    )
    assert outcome["error_code"] == "element_box_unavailable"
    assert outcome["clicked"] == []


@pytest.mark.asyncio
async def test_verdict_reader_is_consulted():
    page = _FakePage()
    result = OcrResult(text="的", confidence=0.95, boxes=[_box("的", 0.5, 0.5)], provider="test")
    plan = plan_click_points(result, Instruction(targets=("的",), raw="的"))

    async def reader():
        return {"code": 0, "msg": "verifySuccess"}

    outcome = await PointSelectExecutor().execute(
        page=page, target_element=page, plan=plan, verdict_reader=reader
    )
    assert outcome["verdict"]["code"] == 0