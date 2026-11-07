"""VisualChallengeSolver 的 hCaptcha 分派 — 上下文、重试纪律与结果映射。

hCaptcha 是「一次挑战、提交即成判决」的题型，因此重试语义与滑块/点选都
不同：只有「还没碰页面」的失败才允许调用方安全重来，其余一律不可重试。
这里注入假执行器锁死这些契约，不触碰浏览器与网络。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)


def _request(context=VisionContext.PLAYWRIGHT_PAGE, **kwargs):
    return VisualChallengeRequest(
        challenge_type=ChallengeType.HCAPTCHA,
        context=context,
        page=kwargs.pop("page", object()),
        **kwargs,
    )


def _executor(outcome):
    executor = MagicMock()
    executor.solve = AsyncMock(return_value=outcome)
    return executor


@pytest.mark.asyncio
async def test_success_maps_verdict_and_metadata():
    executor = _executor(
        {
            "success": True,
            "mode": "binary",
            "question": "Please click all traffic lights",
            "crumbs": 1,
            "rounds": 2,
            "clicked": [{"tile": 1}, {"tile": 9}],
            "verdict": {"pass": True, "has_pass_uuid": True},
            "pass_token": True,
            "error_code": None,
        }
    )
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())

    assert result.success is True
    assert result.challenge_type == ChallengeType.HCAPTCHA
    assert result.provider == "hcaptcha"
    assert result.error_code is None
    assert result.retryable is False
    assert result.cookies is None  # hCaptcha 产物是 token，不是 cookie
    assert result.metadata["mode"] == "binary"
    assert result.metadata["rounds"] == 2
    assert result.metadata["pass_token"] is True
    assert result.metadata["clicked"] == [{"tile": 1}, {"tile": 9}]
    executor.solve.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_after_submit_is_not_retryable():
    """判错/无判决：重试等于重启挑战并追加判罚样本，必须不可重试。"""
    executor = _executor(
        {"success": False, "error_code": "hcaptcha_verdict_pending", "mode": "point"}
    )
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_code == "hcaptcha_verdict_pending"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_challenge_timeout_is_safely_retryable():
    """挑战还没出现就超时：页面未被触碰，调用方可以安全重来。"""
    executor = _executor(
        {"success": False, "error_code": "hcaptcha_challenge_timeout"}
    )
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())

    assert result.error_code == "hcaptcha_challenge_timeout"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_recognition_failure_is_not_retryable():
    executor = _executor({"success": False, "error_code": "vision_unparseable"})
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())

    assert result.error_code == "vision_unparseable"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_image_context_is_rejected_without_touching_executor():
    executor = _executor({"success": True})
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.HCAPTCHA,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=b"fake",
        )
    )

    assert result.success is False
    assert result.error_code == "unsupported_hcaptcha_context"
    assert result.retryable is False
    executor.solve.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_context_without_page_is_rejected():
    executor = _executor({"success": True})
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.HCAPTCHA,
            context=VisionContext.PLAYWRIGHT_PAGE,
            page=None,
        )
    )

    assert result.error_code == "unsupported_hcaptcha_context"
    executor.solve.assert_not_awaited()


@pytest.mark.asyncio
async def test_cdp_context_with_page_is_allowed():
    executor = _executor({"success": True, "mode": "binary"})
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(
        _request(context=VisionContext.CDP, cdp_endpoint="ws://localhost:9222/devtools/browser/1")
    )

    assert result.success is True
    executor.solve.assert_awaited_once()


@pytest.mark.asyncio
async def test_executor_exception_becomes_error_result():
    executor = MagicMock()
    executor.solve = AsyncMock(side_effect=RuntimeError("page closed"))
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_code == "hcaptcha_executor_error"
    assert result.retryable is False


@pytest.mark.asyncio
async def test_overrides_are_forwarded():
    executor = _executor({"success": True})
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    await solver.solve(
        _request(
            question_language="en",
            challenge_iframe="IFRAME",
            hcaptcha={"max_rounds": 2, "prompt": "CUSTOM"},
        )
    )

    kwargs = executor.solve.await_args.kwargs
    assert kwargs["question_language"] == "en"
    assert kwargs["max_rounds"] == 2
    assert kwargs["prompt"] == "CUSTOM"
    assert kwargs["challenge_iframe"] == "IFRAME"


@pytest.mark.asyncio
async def test_result_dict_redacts_tokens_and_has_no_cookies():
    executor = _executor(
        {
            "success": True,
            "mode": "binary",
            "verdict": {"pass": True, "token": "SECRET-TOKEN"},
            "pass_token": True,
        }
    )
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request())
    payload = result.to_dict()

    assert payload["cookies"] == {}
    # redact_sensitive 会把任何含 token 的键抹掉，避免判决里的凭据泄漏到日志。
    assert payload["metadata"]["verdict"]["token"] == "[redacted]"


@pytest.mark.asyncio
async def test_timeout_is_reported_as_timeout():
    async def _slow(*_args, **_kwargs):
        import asyncio

        await asyncio.sleep(5)
        return {"success": True}

    executor = MagicMock()
    executor.solve = _slow
    solver = VisualChallengeSolver(hcaptcha_executor=executor)

    result = await solver.solve(_request(timeout_ms=50))

    assert result.success is False
    assert result.error_code == "timeout"
