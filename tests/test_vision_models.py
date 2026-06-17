from pathlib import Path

from slidex.vision import (
    ChallengeType,
    ProviderManifest,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)


def test_visual_challenge_result_serializes_core_fields():
    result = VisualChallengeResult(
        success=True,
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        provider="geetest",
        confidence=0.91,
        duration_ms=1234.5,
        cookies={"session": "abc"},
        artifacts=[
            VisionArtifact(
                artifact_type="telemetry",
                path=Path("artifacts/run-1/telemetry/events.jsonl"),
                metadata={"source": "unit", "token": "secret"},
            )
        ],
        metadata={"slide_code": 0, "cookie_value": "sensitive"},
    )

    payload = result.to_dict()

    assert payload["success"] is True
    assert payload["challenge_type"] == "slider_captcha"
    assert payload["provider"] == "geetest"
    assert payload["confidence"] == 0.91
    assert payload["cookies"] == {"session": "[redacted]"}
    assert payload["artifacts"][0]["artifact_type"] == "telemetry"
    assert payload["artifacts"][0]["metadata"]["token"] == "[redacted]"
    assert payload["metadata"]["slide_code"] == 0
    assert payload["metadata"]["cookie_value"] == "[redacted]"


def test_request_accepts_multiple_context_inputs():
    request = VisualChallengeRequest(
        challenge_type=ChallengeType.OCR_TEXT,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"fake-image",
        roi={"x": 1, "y": 2, "width": 3, "height": 4},
        metadata={"language": "zh-CN"},
    )

    assert request.challenge_type == ChallengeType.OCR_TEXT
    assert request.context == VisionContext.IMAGE_BYTES
    assert request.image_bytes == b"fake-image"
    assert request.roi["width"] == 3


def test_provider_manifest_declares_capabilities():
    manifest = ProviderManifest(
        name="geetest",
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )

    assert manifest.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.CDP)
    assert not manifest.supports(ChallengeType.OCR_TEXT, VisionContext.IMAGE_BYTES)
