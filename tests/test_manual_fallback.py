import pytest

from slidex.vision import ChallengeType
from slidex.vision.manual import ManualFallbackSession


def test_manual_fallback_session_redacts_token():
    session = ManualFallbackSession(
        session_id="s1",
        challenge_type=ChallengeType.OCR_TEXT,
        token="secret-token",
        timeout_s=60,
    )

    payload = session.to_dict()

    assert payload["session_id"] == "s1"
    assert payload["challenge_type"] == "ocr_text"
    assert payload["token"] == "[redacted]"
    assert payload["timeout_s"] == 60


@pytest.mark.asyncio
async def test_manual_fallback_can_complete_ocr_correction():
    session = ManualFallbackSession(
        session_id="s1",
        challenge_type=ChallengeType.OCR_TEXT,
        token="secret-token",
        timeout_s=60,
    )

    result = session.complete_text("人工修正")

    assert result.success is True
    assert result.challenge_type == ChallengeType.OCR_TEXT
    assert result.provider == "manual"
    assert result.metadata["text"] == "人工修正"


def test_manual_fallback_can_complete_slider_result():
    session = ManualFallbackSession(
        session_id="s1",
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        token="secret-token",
        timeout_s=60,
    )

    result = session.complete_metadata({"completed": True})

    assert result.success is True
    assert result.challenge_type == ChallengeType.SLIDER_CAPTCHA
    assert result.provider == "manual"
    assert result.metadata["completed"] is True
