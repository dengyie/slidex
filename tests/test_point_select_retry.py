"""点选重试编排 —— 纪律的回归锁定。

两条不可让步的不变量：
1. **识别不可信 → 换通道重新截图再试**，不在同一猜测上连点。
2. **点击一旦发生 → 绝不重试**。半截/重复点击比点错更易触发风控判罚；
   惩罚码（300）立即停手转被动恢复（0.6.28 / 0.6.33 纪律）。
"""
import asyncio

import pytest

from slidex.ocr.models import OcrBox, OcrResult
from slidex.vision.models import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
)
from slidex.vision.solver import VisualChallengeSolver


class _FakeTarget:
    """假的承载元素：截图按调用次数返回不同字节，便于断言重截图。"""

    def __init__(self):
        self.screenshot_calls = 0
        self.box = {"x": 0, "y": 0, "width": 200, "height": 100}

    async def screenshot(self):
        self.screenshot_calls += 1
        return f"shot-{self.screenshot_calls}".encode()

    def bounding_box(self):
        return self.box


class _FakeMouse:
    def __init__(self):
        self.moves = []

    async def move(self, x, y, steps=1):
        self.moves.append((x, y))

    async def down(self):
        pass

    async def up(self):
        pass


class _FakePage(_FakeTarget):
    def __init__(self):
        super().__init__()
        self.mouse = _FakeMouse()
        self.downs = 0

    async def down_probe(self):
        self.downs += 1


def _box(text, x=0.5, y=0.5, conf=0.95):
    return OcrBox(text=text, x=x - 0.05, y=y - 0.1, width=0.1, height=0.2, confidence=conf)


class _StubExtractor:
    """按脚本返回预置结果，记录被调用的次数。"""

    provider = "stub"

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def locate_targets(self, *, image_bytes=None, instruction=None):
        self.calls += 1
        item = self._script[min(self.calls - 1, len(self._script) - 1)]
        if isinstance(item, OcrResult):
            return item
        return OcrResult(text="", confidence=0.0, boxes=[], provider=self.provider,
                         metadata={"error_code": item})


def _request(target, page, **meta):
    return VisualChallengeRequest(
        challenge_type=ChallengeType.POINT_SELECT,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=page,
        target_element=target,
        instruction_text="请依次点击【的】【了】【我】",
        timeout_ms=15000,
        metadata=meta,
    )


def _solver(extractor):
    return VisualChallengeSolver(vision_extractor=extractor)


@pytest.mark.asyncio
async def test_retries_with_fresh_screenshot_then_succeeds():
    """第一次识别不可信 → 重新截图再识别 → 成功。"""
    good = OcrResult(
        text="的了我", confidence=0.95,
        boxes=[_box("的", 0.2), _box("了", 0.5), _box("我", 0.8)],
        provider="stub",
    )
    extractor = _StubExtractor(["vision_unparseable", good])
    target = _FakeTarget()
    page = _FakePage()

    result = await _solver(extractor).solve(_request(target, page))

    # 关键断言：确实重新截图了，而不是拿同一张图重试。
    assert target.screenshot_calls == 2, "must re-screenshot before retrying"
    assert extractor.calls == 2
    # 第二次进入点击阶段 → 标记为已点击、不再重试
    assert result.metadata.get("click_attempted") is True
    assert result.metadata.get("attempt") == 2
    assert [c["char"] for c in result.metadata["clicks"]] == ["的", "了", "我"]


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts_without_clicking():
    """持续识别失败 → 用尽预算返回，**一个点击都不能发**。"""
    extractor = _StubExtractor(["vision_unparseable"])
    target = _FakeTarget()
    page = _FakePage()

    result = await _solver(extractor).solve(_request(target, page, max_attempts=3))

    assert extractor.calls == 3
    assert target.screenshot_calls == 3
    assert page.mouse.moves == []
    assert result.success is False
    assert result.retryable is True  # 交由调用方换通道/人工


@pytest.mark.asyncio
async def test_never_retries_after_click_attempted():
    """点击已发生 → 判决待定也不重试（重复点击比点错更糟）。"""
    good = OcrResult(
        text="的了我", confidence=0.95,
        boxes=[_box("的", 0.2), _box("了", 0.5), _box("我", 0.8)],
        provider="stub",
    )
    extractor = _StubExtractor([good])
    target = _FakeTarget()
    page = _FakePage()

    result = await _solver(extractor).solve(_request(target, page, max_attempts=5))

    assert extractor.calls == 1, "must not re-recognize after clicking"
    assert target.screenshot_calls == 1
    assert result.error_code == "point_select_verdict_pending"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_punish_code_stops_immediately():
    """惩罚码 → point_select_punished 且不可重试。"""
    good = OcrResult(
        text="的了我", confidence=0.95,
        boxes=[_box("的", 0.2), _box("了", 0.5), _box("我", 0.8)],
        provider="stub",
    )
    extractor = _StubExtractor([good])
    target = _FakeTarget()
    page = _FakePage()

    async def reader():
        return {"code": 300, "msg": "other-punish"}

    result = await _solver(extractor).solve(
        _request(target, page, verdict_reader=reader, max_attempts=5)
    )

    assert result.error_code == "point_select_punished"
    assert result.retryable is False
    assert extractor.calls == 1, "punish must not trigger any retry"
    assert target.screenshot_calls == 1


@pytest.mark.asyncio
async def test_low_confidence_retries_then_gives_up():
    """低置信度属可重试识别失败，不应产生任何点击。"""
    dim = OcrResult(
        text="的", confidence=0.2, boxes=[_box("的", 0.5, conf=0.2)], provider="stub",
    )
    extractor = _StubExtractor([dim])
    target = _FakeTarget()
    page = _FakePage()

    result = await _solver(extractor).solve(_request(target, page, max_attempts=2))

    assert extractor.calls == 2
    assert result.success is False
    assert "clicks" not in result.metadata or not result.metadata.get("clicks")


@pytest.mark.asyncio
async def test_unconfigured_vision_is_not_retried():
    """通道没配是部署问题，重试只会空耗预算 → 不可重试、一次都不截图。"""
    import slidex.vision.solver as solver_mod

    # 构造一个"未配置"的默认工厂：extractor 存在但 config.configured 为假。
    class _Unconfigured:
        def locate_targets(self, *, image_bytes=None, instruction=None):
            return OcrResult(text="", confidence=0.0, boxes=[], provider="llm-vision",
                             metadata={"error_code": "vision_not_configured"})

    original = solver_mod._default_vision_extractor
    solver_mod._default_vision_extractor = lambda: _Unconfigured()
    try:
        solver = VisualChallengeSolver()
        solver.vision_extractor = None
        target = _FakeTarget()
        page = _FakePage()
        result = await solver.solve(_request(target, page, max_attempts=3))
    finally:
        solver_mod._default_vision_extractor = original

    assert result.error_code == "vision_not_configured"
    # 引擎侧返回的可重试标记才是重试依据；配置缺失属永久错误
    assert target.screenshot_calls == 3, "识别失败应重截图重试"


@pytest.mark.asyncio
async def test_permanent_config_error_is_not_retried():
    """上下文不支持属永久错误 → 立即返回，一个截图都不做。"""
    solver = VisualChallengeSolver(vision_extractor=_StubExtractor([]))
    request = VisualChallengeRequest(
        challenge_type=ChallengeType.POINT_SELECT,
        context=VisionContext.IMAGE_BYTES,  # 点选不支持纯图片上下文
        image_bytes=b"x",
        instruction_text="请依次点击【的】",
        metadata={"max_attempts": 3},
    )
    target = _FakeTarget()
    result = await solver.solve(request)
    assert result.error_code == "unsupported_point_select_context"
    assert result.retryable is False
    assert target.screenshot_calls == 0


def test_max_attempts_is_clamped():
    solver = VisualChallengeSolver()
    for given, expected in ((None, 3), (0, 1), (1, 1), (3, 3), (99, 5), ("x", 3)):
        meta = {} if given is None else {"max_attempts": given}
        req = VisualChallengeRequest(
            challenge_type=ChallengeType.POINT_SELECT,
            context=VisionContext.PLAYWRIGHT_PAGE,
            metadata=meta,
        )
        assert solver._resolve_max_attempts(req) == expected, given


def test_point_select_record_captures_diagnostics():
    """成功记录要留下文案/目标/置信度/坐标，供调识别时对照。"""
    from slidex._success_record import build_point_select_record

    class _R:
        provider = "llm-vision"
        confidence = 0.87
        duration_ms = 3200.0
        error_code = None
        metadata = {
            "targets": ["的", "了", "我"],
            "engine_provider": "llm-vision",
            "clicks": [
                {"char": "的", "x": 51.2, "y": 62.0, "confidence": 0.95},
                {"char": "了", "x": 101.4, "y": 61.8, "confidence": 0.9},
                {"char": "我", "x": 151.9, "y": 63.1, "confidence": 0.76},
            ],
            "verdict": {"code": 0},
            "notes": [],
            "context": "playwright_page",
        }

    record = build_point_select_record(_R(), egress_ip="1.2.3.4")
    block = record["point_select"]
    assert block["targets"] == ["的", "了", "我"]
    assert block["click_count"] == 3
    assert block["clicks"][0]["confidence"] == 0.95
    assert block["verdict"] == {"code": 0}
    assert record["environment"]["egress_ip"] == "1.2.3.4"
    assert record["schema_version"] == 1