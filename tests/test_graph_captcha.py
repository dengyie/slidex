"""字符图形验证码：归一化、规划、编排。

重点覆盖两类错误：一类是把对的答案改错（易混字符折叠），一类是把错的答案
放行（长度/字符集不校验）。
"""

from __future__ import annotations

import asyncio

import pytest

from slidex.ocr.models import OcrResult
from slidex.vision.graph_captcha import (
    ALPHABET_ANY,
    ALPHABET_DIGITS_ONLY,
    ALPHABET_UPPER_ONLY,
    DEFAULT_EXPECTED_LENGTH,
    GraphCaptchaExecutor,
    is_punish_graph_code,
    normalize_chars,
    plan_graph_chars,
)
from slidex.vision.models import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision.solver import VisualChallengeSolver


# ── 归一化 ────────────────────────────────────────────────────────
def test_preserves_mixed_charset_letters():
    """回归：字母数字混合字符集下不得把字母"纠"成数字。

    实测样本的正确答案是 AEZQ。旧实现无条件把 Q→0、Z→2，把它改成了 AE20，
    由对变错。这类折叠在混合字符集上永远是破坏。
    """
    text, normalized_from = normalize_chars("AEZQ", alphabet=ALPHABET_ANY)
    assert text == "AEZQ"
    assert normalized_from is None


def test_mixed_charset_does_not_collapse_digits_either():
    """数字形态同样是合法答案，混合字符集下一律不动。"""
    assert normalize_chars("0123", alphabet=ALPHABET_ANY)[0] == "0123"


def test_digits_only_alphabet_collapses_letters():
    """渠道声明只有数字时，O/Q/Z 折叠成数字才是纠错。"""
    assert normalize_chars("OQZ", alphabet=ALPHABET_DIGITS_ONLY)[0] == "002"


def test_upper_only_alphabet_collapses_digits():
    """渠道声明只有大写字母时，数字折叠成字母才是纠错。

    0 是例外（可对应 O/Q/D），见下一个用例。
    """
    assert normalize_chars("0512", alphabet=ALPHABET_UPPER_ONLY)[0] == "0SIZ"


def test_upper_only_leaves_ambiguous_digit_alone():
    """0 可对应 O/Q/D 三者，反向无解时必须原样保留，不得由 dict 顺序决定。

    否则 digits_only 与 upper_only 两个策略会得出互不自洽的结论。
    """
    assert normalize_chars("0", alphabet=ALPHABET_UPPER_ONLY)[0] == "0"


def test_strips_separators_and_whitespace():
    assert normalize_chars(" A E Z Q ")[0] == "AEZQ"


def test_uppercases_lowercase_input():
    assert normalize_chars("aezq")[0] == "AEZQ"


def test_reports_normalization_provenance():
    _, normalized_from = normalize_chars("OQZ", alphabet=ALPHABET_DIGITS_ONLY)
    assert normalized_from == "OQZ"


def test_empty_input_yields_empty():
    assert normalize_chars("")[0] == ""
    assert normalize_chars("  -  ")[0] == ""


# ── 规划 ──────────────────────────────────────────────────────────
def test_plan_accepts_real_sample():
    plan = plan_graph_chars("AEZQ")
    assert plan.ok
    assert plan.chars.text == "AEZQ"
    assert plan.error_code is None


def test_plan_accepts_exact_expected_length():
    assert plan_graph_chars("AEZQ", expected_length=DEFAULT_EXPECTED_LENGTH).ok


def test_plan_rejects_wrong_expected_length():
    plan = plan_graph_chars("AEZ", expected_length=4)
    assert not plan.ok
    assert plan.error_code == "unexpected_char_count"


def test_plan_rejects_too_few_chars():
    assert plan_graph_chars("ABC").error_code == "unexpected_char_count"


def test_plan_rejects_too_many_chars():
    assert plan_graph_chars("ABCDEFG").error_code == "unexpected_char_count"


def test_plan_rejects_empty_recognition():
    assert plan_graph_chars("").error_code == "no_chars_recognized"
    assert plan_graph_chars("   ").error_code == "no_chars_recognized"


def test_plan_keeps_chars_on_rejection_for_diagnostics():
    """拒判时仍带回识别结果，供失败对照——不能连"识别成了什么"都丢掉。"""
    plan = plan_graph_chars("ABC", expected_length=4)
    assert plan.chars is not None
    assert plan.chars.text == "ABC"


def test_punish_code_reuses_slider_semantics():
    """惩罚码判定复用滑块 300 语义（0.6.33 纪律）。"""
    assert is_punish_graph_code(300) is True
    assert is_punish_graph_code(0) is False
    assert is_punish_graph_code(None) is False


# ── 执行器 ────────────────────────────────────────────────────────
class _FakeLocator:
    def __init__(self):
        self.clicked = 0
        self.typed: list[str] = []
        self.filled: list[str] = []

    async def click(self):
        self.clicked += 1

    async def fill(self, value):
        self.filled.append(value)

    async def type(self, text, delay=0):
        self.typed.append(text)


def test_executor_types_each_character_separately():
    """逐字键入而非整块填充——行为特征上更接近真人。"""
    input_loc, submit_loc = _FakeLocator(), _FakeLocator()
    out = asyncio.run(
        GraphCaptchaExecutor().execute(
            input_locator=input_loc, submit_locator=submit_loc, text="AEZQ"
        )
    )
    assert out["filled"] is True
    assert out["submitted"] is True
    assert input_loc.typed == ["A", "E", "Z", "Q"]
    assert submit_loc.clicked == 1


def test_executor_refuses_empty_text():
    out = asyncio.run(
        GraphCaptchaExecutor().execute(input_locator=_FakeLocator(), text="")
    )
    assert out["filled"] is False
    assert out["error_code"] == "no_chars_to_submit"


def test_executor_without_submit_locator_only_fills():
    submit_loc = _FakeLocator()
    out = asyncio.run(
        GraphCaptchaExecutor().execute(input_locator=_FakeLocator(), text="AEZQ")
    )
    assert out["submitted"] is False
    assert submit_loc.clicked == 0


def test_executor_reports_fill_failure():
    class _Boom:
        async def click(self):
            raise RuntimeError("detached")

    out = asyncio.run(
        GraphCaptchaExecutor().execute(input_locator=_Boom(), text="AEZQ")
    )
    assert out["error_code"] == "fill_failed"


def test_executor_surfaces_verdict():
    async def reader():
        return {"code": 300}

    out = asyncio.run(
        GraphCaptchaExecutor().execute(
            input_locator=_FakeLocator(), text="AEZQ", verdict_reader=reader
        )
    )
    assert out["verdict"] == {"code": 300}


# ── 编排层 ────────────────────────────────────────────────────────
class _StubEngine:
    """按脚本返回识别结果的假引擎。"""

    provider = "stub"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def read_chars(self, *, image_bytes=None, image_path=None):
        self.calls += 1
        # 脚本用尽时立刻失败，而不是静默返回一个空列表——后者会把
        # "编排层多调了一次" 伪装成识别失败，掩盖真正的重试次数问题。
        if not self.script:
            raise AssertionError("recognition script exhausted")
        return self.script.pop(0)


def _request(**kwargs):
    base = dict(
        challenge_type=ChallengeType.GRAPH_CHARS,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"png",
    )
    base.update(kwargs)
    return VisualChallengeRequest(**base)


def _ok(text="AEZQ", **meta):
    return OcrResult(text=text, confidence=0.9, provider="stub", metadata=meta)


class _FakeTarget:
    """假的目标元素：每次 screenshot() 都给新图，模拟活页面重截。"""

    def __init__(self):
        self.shots = 0

    async def screenshot(self):
        self.shots += 1
        return f"png-{self.shots}".encode()


def _live_page_request():
    """活页面请求：给 target_element，由执行器每轮重新截图。"""
    return VisualChallengeRequest(
        challenge_type=ChallengeType.GRAPH_CHARS,
        context=VisionContext.PLAYWRIGHT_PAGE,
        target_element=_FakeTarget(),
    )


@pytest.mark.asyncio
async def test_recognition_only_returns_chars():
    """没给输入框 → 只识别不碰页面，调用方自己填。"""
    engine = _StubEngine([_ok()])
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(_request())
    assert result.success is True
    assert result.metadata["chars"] == "AEZQ"
    assert result.metadata["recognition_only"] is True


@pytest.mark.asyncio
async def test_engine_without_read_chars_is_rejected():
    """引擎没实现 read_chars 时明确报错，不退化到点选路径。

    两者输出语义不同（有序带坐标 vs 纯字符），混用会填错。
    """
    class _NoChars:
        provider = "point-only"

        def locate_targets(self, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("must not be called")

    solver = VisualChallengeSolver(vision_extractor=_NoChars())
    result = await solver.solve(_request())
    assert result.success is False
    assert result.error_code == "engine_lacks_read_chars"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_bad_length_on_static_image_does_not_pointlessly_retry():
    """静态图片 + 长度不符 → 不重试。

    同一张图对同一模型的答案不会变，重试只是把同一个错误答案再算一遍。
    换图的责任在调用方（它才持有页面）。
    """
    engine = _StubEngine([_ok("ABC"), _ok("AEZQ")])
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(_request())
    assert result.success is False
    assert result.error_code == "unexpected_char_count"
    assert result.retryable is False
    assert result.metadata["from_live_page"] is False
    assert engine.calls == 1


@pytest.mark.asyncio
async def test_bad_length_on_live_page_retries_with_fresh_screenshot():
    """活页面 + 长度不符 → 重新截图重识别（不是重复提交同一猜测）。"""
    engine = _StubEngine([_ok("ABC"), _ok("AEZQ")])
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(_live_page_request())
    assert result.success is True
    assert result.metadata["chars"] == "AEZQ"
    assert engine.calls == 2


@pytest.mark.asyncio
async def test_engine_error_is_not_retried():
    """通道类错误重发无意义，不消耗重试预算。"""
    engine = _StubEngine(
        [OcrResult(text="", confidence=0.0, provider="stub",
                   metadata={"error_code": "vision_http_403"})]
    )
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(_request())
    assert result.error_code == "vision_http_403"
    assert engine.calls == 1


@pytest.mark.asyncio
async def test_truncation_is_retryable_while_other_engine_errors_are_not():
    """截断是"预算被 reasoning 吃光、正文没吐完"，重发同一张图有意义。

    对照 401/403 这类通道错误：重发换不来新信息，必须一次停手。
    """
    truncated = OcrResult(
        text="", confidence=0.0, provider="stub",
        metadata={"error_code": "vision_empty_response", "truncated": True},
    )
    engine = _StubEngine([truncated, _ok("AEZQ")])
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(_request())
    assert result.success is True
    assert result.metadata["chars"] == "AEZQ"
    assert engine.calls == 2


@pytest.mark.asyncio
async def test_missing_image_and_context_is_rejected():
    engine = _StubEngine([_ok()])
    solver = VisualChallengeSolver(vision_extractor=engine)
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.GRAPH_CHARS,
            context=VisionContext.IMAGE_BYTES,
        )
    )
    assert result.error_code == "unsupported_graph_chars_context"