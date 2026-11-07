"""hCaptcha 识别引擎（LlmHcaptchaEngine）— 提示词协议与错误码契约。

引擎是「题型 → 多模态协议」的薄适配器：只依赖 ``ask`` 一个方法，因此这里
注入假 extractor 做零网络验证——不测任何特定模型，只锁定三件事：
1. 题面确实注入到提示词里（否则模型不知道要判什么）；
2. 模型输出 → 结构化答案的解析与错误码；
3. 传输层错误码原样透传（部署问题不该被伪装成「认不出」）。
"""
import pytest

from slidex.ocr.models import OcrResult
from slidex.vision.hcaptcha import LlmHcaptchaEngine


class _FakeExtractor:
    """记录最后一次调用的 prompt，并按 preset 返回固定回答。"""

    provider = "fake-vision"

    def __init__(self, text="", *, error_code=None, confidence=0.9):
        self._text = text
        self._error_code = error_code
        self._confidence = confidence
        self.calls = []

    def ask(self, *, image_bytes=None, image_path=None, prompt):
        self.calls.append({"image_bytes": image_bytes, "prompt": prompt})
        metadata = {}
        if self._error_code:
            metadata["error_code"] = self._error_code
        return OcrResult(
            text=self._text,
            confidence=self._confidence,
            provider=self.provider,
            metadata=metadata,
        )


def test_select_tiles_parses_and_injects_question():
    extractor = _FakeExtractor('{"selected":[1,5,9]}')
    engine = LlmHcaptchaEngine(extractor)

    selection = engine.select_tiles(b"PNG", "请点击所有包含红绿灯的图片")

    assert selection.ok
    assert selection.flags == [
        True, False, False, False, True, False, False, False, True
    ]
    assert selection.provider == "fake-vision"
    assert selection.confidence == pytest.approx(0.9)
    sent = extractor.calls[-1]
    assert sent["image_bytes"] == b"PNG"
    # 题面必须进提示词，且输出键名固定英文（不随语种变化）。
    assert "红绿灯" in sent["prompt"]
    assert '"selected"' in sent["prompt"]


def test_select_tiles_custom_prompt_replaces_default_but_keeps_question():
    extractor = _FakeExtractor('{"selected":[2]}')
    engine = LlmHcaptchaEngine(extractor)
    engine.select_tiles(b"PNG", "click all buses", prompt="MY_CUSTOM_PROMPT")
    sent = extractor.calls[-1]["prompt"]
    assert sent.startswith("MY_CUSTOM_PROMPT")
    assert "click all buses" in sent


def test_select_tiles_unparseable_reports_error():
    engine = LlmHcaptchaEngine(_FakeExtractor("我不知道"))
    selection = engine.select_tiles(b"PNG", "题面")
    assert not selection.ok
    assert selection.error_code == "vision_unparseable"
    assert "raw_excerpt" in selection.metadata


def test_select_tiles_passes_through_transport_error():
    engine = LlmHcaptchaEngine(_FakeExtractor("", error_code="vision_http_401"))
    selection = engine.select_tiles(b"PNG", "题面")
    assert not selection.ok
    assert selection.error_code == "vision_http_401"


def test_locate_points_returns_click_plan():
    engine = LlmHcaptchaEngine(_FakeExtractor('{"points":[{"x":0.4,"y":0.6}]}'))
    plan = engine.locate_points(b"PNG", "请点击图中的消防栓")
    assert plan.ok
    assert len(plan.points) == 1
    assert plan.points[0].x == pytest.approx(0.4)
    assert plan.points[0].y == pytest.approx(0.6)


def test_locate_points_unparseable_is_error():
    engine = LlmHcaptchaEngine(_FakeExtractor('{"points":[]}'))
    plan = engine.locate_points(b"PNG", "题面")
    assert not plan.ok
    assert plan.error_code == "vision_unparseable"


def test_locate_drag_returns_gesture():
    engine = LlmHcaptchaEngine(
        _FakeExtractor('{"start":{"x":0.2,"y":0.3},"end":{"x":0.7,"y":0.8}}')
    )
    gesture = engine.locate_drag(b"PNG", "拖动拼图到缺口")
    assert gesture.ok
    assert gesture.start == (pytest.approx(0.2), pytest.approx(0.3))
    assert gesture.end == (pytest.approx(0.7), pytest.approx(0.8))
    assert gesture.provider == "fake-vision"


def test_locate_drag_not_a_drag_challenge():
    engine = LlmHcaptchaEngine(_FakeExtractor('{"unsupported":true}'))
    gesture = engine.locate_drag(b"PNG", "题面")
    assert not gesture.ok
    assert gesture.error_code == "not_a_drag_challenge"


def test_locate_drag_transport_error_passthrough():
    engine = LlmHcaptchaEngine(_FakeExtractor("", error_code="vision_not_configured"))
    gesture = engine.locate_drag(b"PNG", "题面")
    assert not gesture.ok
    assert gesture.error_code == "vision_not_configured"


def test_extractor_without_ask_reports_engine_lacks_ask():
    """只实现了旧的 read_chars 的引擎不能静默退化——必须明确报错。"""

    class _CharOnlyExtractor:
        provider = "char-only"

        def read_chars(self, **_kwargs):  # pragma: no cover - 不应被调用
            return OcrResult(text="1234")

    engine = LlmHcaptchaEngine(_CharOnlyExtractor())
    selection = engine.select_tiles(b"PNG", "题面")
    assert not selection.ok
    assert selection.error_code == "engine_lacks_ask"
