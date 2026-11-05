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


def test_ocr_execution_does_not_block_event_loop_scheduling():
    pytest.importorskip("automation_core")
    from automation_core.execution import ExecutionContext
    from slidex.integrations.automation_kit import SlidexVisualCapability
    from slidex.vision.solver import VisualChallengeSolver

    progress = []

    class SlowOcr:
        def extract(self, **kwargs):
            import time

            time.sleep(0.05)
            from slidex.ocr import OcrResult

            return OcrResult(text="ok", provider="slow", confidence=1.0, language="en", boxes=[], metadata={})

    async def run():
        async def marker():
            await asyncio.sleep(0.01)
            progress.append("tick")

        provider = SlidexVisualCapability(
            visual_solver=VisualChallengeSolver(ocr_extractor=SlowOcr())
        )
        marker_task = asyncio.create_task(marker())
        result = await provider.execute(
            _request(
                __import__("automation_core").capabilities,
                challenge_type="ocr_text",
                context="image_bytes",
                image_bytes=b"fake",
            ),
            ExecutionContext(run_id="run-1", task_id="step-1", workflow_name="ocr"),
        )
        await marker_task
        return result

    result = asyncio.run(run())
    assert result.success is True
    assert progress == ["tick"]


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


# --- SlidexManualCapability (second visual.challenge provider) ---------------


def _manual(**parameters):
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexManualCapability

    return capabilities, SlidexManualCapability(**parameters)


def test_manual_capability_manifest_ranks_below_auto_solver():
    pytest.importorskip("automation_core")
    from slidex.integrations.automation_kit import (
        SlidexManualCapability,
        SlidexVisualCapability,
    )

    manual = SlidexManualCapability()
    auto = SlidexVisualCapability()

    assert manual.manifest.name == "visual.challenge"
    assert manual.manifest.priority == -10
    assert auto.manifest.priority == 0
    assert manual.manifest.metadata == {"implementation": "slidex-manual"}


def test_manual_capability_without_completer_returns_pending_failure():
    capabilities, provider = _manual()
    result = asyncio.run(
        provider.execute(
            _request(
                capabilities,
                challenge_type="slider_captcha",
                session_id="session-1",
            ),
            _context(),
        )
    )

    assert result.success is False
    assert result.provider == "slidex-manual"
    assert result.error_code == "manual_fallback_pending"
    assert result.retryable is True
    assert result.data["session_id"] == "session-1"
    assert result.data["token"] == "[redacted]"


def test_manual_capability_completer_metadata_becomes_success():
    async def completer(session):
        assert session.session_id == "session-2"
        return {"text": "1234"}

    capabilities, provider = _manual(completer=completer)
    result = asyncio.run(
        provider.execute(
            _request(
                capabilities,
                challenge_type="slider_captcha",
                session_id="session-2",
            ),
            _context(),
        )
    )

    assert result.success is True
    assert result.provider == "slidex-manual"
    assert result.data["metadata"]["text"] == "1234"
    assert result.data["metadata"]["session_id"] == "session-2"
    assert result.metadata["visual_provider"] == "manual"


def test_manual_capability_rejects_invalid_requests():
    capabilities, provider = _manual()

    with pytest.raises(capabilities.CapabilityProtocolError, match="challenge_type"):
        asyncio.run(
            provider.execute(_request(capabilities), _context())
        )
    with pytest.raises(capabilities.CapabilityProtocolError, match="timeout_s"):
        asyncio.run(
            provider.execute(
                _request(capabilities, challenge_type="slider_captcha", timeout_s=0),
                _context(),
            )
        )


def test_manual_capability_sync_completer_supported():
    capabilities, provider = _manual(completer=lambda session: {"text": "ok"})

    result = asyncio.run(
        provider.execute(
            _request(capabilities, challenge_type="ocr_text", session_id="s3"),
            _context(),
        )
    )

    assert result.success is True
    assert result.data["metadata"]["text"] == "ok"


def test_two_provider_failover_auto_failure_routes_to_manual():
    """The governance gate proof: auto solver fails -> manual fallback wins."""
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from automation_core.capabilities import (
        CapabilityExecutor,
        CapabilityRegistry,
        CapabilityResolver,
    )
    from slidex.integrations.automation_kit import (
        SlidexManualCapability,
        SlidexVisualCapability,
    )

    class FailingSolver:
        async def solve(self, request):
            return VisualChallengeResult(
                success=False,
                challenge_type=request.challenge_type,
                provider="fake-ocr",
                error_code="slider_not_solved",
                retryable=False,
            )

    registry = CapabilityRegistry()
    registry.register(SlidexVisualCapability(visual_solver=FailingSolver()), name="slidex")
    registry.register(
        SlidexManualCapability(completer=lambda session: {"text": "human"}),
        name="slidex-manual",
    )
    executor = CapabilityExecutor(CapabilityResolver(registry))

    result = asyncio.run(
        executor.execute(
            _request(
                capabilities,
                challenge_type="slider_captcha",
                context="image_bytes",
                image_bytes=b"fake-png",
            ),
            _context(),
        )
    )

    assert result.success is True
    assert result.provider == "slidex-manual"
    assert result.data["metadata"]["text"] == "human"
