import asyncio
from pathlib import Path

import pytest

from slidex.vision import ChallengeType, VisionArtifact, VisualChallengeResult


def _result():
    return VisualChallengeResult(
        success=True,
        challenge_type=ChallengeType.OCR_TEXT,
        provider="fake",
        confidence=0.99,
        artifacts=[
            VisionArtifact(
                artifact_type="ocr_result",
                path=Path("artifacts/run-1/ocr/result.json"),
                metadata={"token": "secret", "source": "unit"},
            )
        ],
        metadata={"text": "damai", "x5sec": "secret"},
    )


def _request(capabilities, **parameters):
    return capabilities.CapabilityRequest(
        capability="visual.challenge",
        operation="solve",
        parameters=parameters,
    )


def _context():
    from automation_core.execution import ExecutionContext

    return ExecutionContext(
        run_id="run-1",
        task_id="task-1",
        workflow_name="slidex-contract",
        correlation_id="trace-1",
    )


def test_slidex_visual_capability_maps_generic_request_to_visual_solver():
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexVisualCapability

    class FakeVisualSolver:
        def __init__(self):
            self.requests = []

        async def solve(self, request):
            self.requests.append(request)
            return VisualChallengeResult(
                success=True,
                challenge_type=request.challenge_type,
                provider="fake-ocr",
                confidence=0.95,
                metadata={"text": "dianping"},
            )

    solver = FakeVisualSolver()
    provider = SlidexVisualCapability(visual_solver=solver)
    result = asyncio.run(
        provider.execute(
            _request(
                capabilities,
                challenge_type="image_text",
                context="android_screenshot_bytes",
                image_bytes=b"fake-png",
                provider="auto",
            )
            ,
            _context(),
        )
    )

    assert provider.manifest.name == "visual.challenge"
    assert solver.requests[0].challenge_type == ChallengeType.IMAGE_TEXT
    assert solver.requests[0].image_bytes == b"fake-png"
    assert solver.requests[0].metadata["task_id"] == "task-1"
    assert result.success is True
    assert result.provider == "slidex"
    assert result.data["metadata"]["text"] == "dianping"


def test_slidex_visual_capability_preserves_artifacts_and_redacts_sensitive_fields():
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexVisualCapability

    class FakeVisualSolver:
        async def solve(self, request):
            return _result()

    result = asyncio.run(
        SlidexVisualCapability(visual_solver=FakeVisualSolver()).execute(
            _request(
                capabilities,
                challenge_type="ocr_text",
                context="image_bytes",
                image_bytes=b"fake",
            )
            ,
            _context(),
        )
    )

    assert result.artifacts[0].artifact_type == "ocr_result"
    assert result.artifacts[0].metadata["token"] == "[redacted]"
    assert result.data["metadata"]["x5sec"] == "[redacted]"


def test_slidex_visual_capability_profile_marks_ocr_as_unsupported_cancellation():
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexVisualCapability

    provider = SlidexVisualCapability()
    profile = provider.execution_profile(
        _request(
            capabilities,
            challenge_type="ocr_text",
            context="image_bytes",
            image_bytes=b"fake",
        )
    )

    assert profile.cancellation == "unsupported"
    assert profile.blocking is True


@pytest.mark.parametrize(
    ("parameters", "message"),
    [
        ({"context": "image_bytes", "image_bytes": b"fake"}, "challenge_type"),
        (
            {"challenge_type": "ocr_text", "context": "unknown", "image_bytes": b"fake"},
            "context",
        ),
        (
            {"challenge_type": "ocr_text", "context": "image_bytes", "image_bytes": b""},
            "image_bytes",
        ),
        (
            {"challenge_type": "slider_captcha", "context": "playwright_page"},
            "page",
        ),
        (
            {
                "challenge_type": "ocr_text",
                "context": "image_bytes",
                "image_bytes": b"fake",
                "timeout_ms": 0,
            },
            "timeout_ms",
        ),
    ],
)
def test_slidex_visual_capability_rejects_invalid_requests(parameters, message):
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexVisualCapability

    with pytest.raises(capabilities.CapabilityProtocolError, match=message):
        asyncio.run(
            SlidexVisualCapability().execute(
                _request(capabilities, **parameters),
                _context(),
            )
        )
