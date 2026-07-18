import json
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
        metadata={"text": "damai"},
    )


def test_to_action_result_without_automation_kit_installed():
    from slidex.integrations.automation_kit import to_action_result

    action_result = to_action_result(_result())

    assert action_result["success"] is True
    assert action_result["message"] == "ocr_text solved by fake"
    assert action_result["data"]["metadata"]["text"] == "damai"


def test_to_artifacts_redacts_sensitive_metadata():
    from slidex.integrations.automation_kit import to_artifacts

    artifacts = to_artifacts(_result())

    assert artifacts[0]["artifact_type"] == "ocr_result"
    assert artifacts[0]["metadata"]["token"] == "[redacted]"
    assert artifacts[0]["metadata"]["source"] == "unit"


def test_to_artifacts_without_native_adapter_is_json_serializable():
    from slidex.integrations.automation_kit import to_artifacts

    artifacts = to_artifacts(_result())

    assert artifacts[0]["path"] == "artifacts/run-1/ocr/result.json"
    json.dumps(artifacts)


def test_to_events_emits_artifact_and_task_end():
    from slidex.integrations.automation_kit import to_events

    events = to_events(_result(), task_id="task-1")

    assert [event["event_type"] for event in events] == ["artifact", "task.end"]
    assert events[1]["payload"]["success"] is True


def test_native_automation_kit_adapter_when_dependency_is_available():
    drivers = pytest.importorskip("automation_core.drivers")
    events_module = pytest.importorskip("automation_core.events")
    from slidex.integrations.automation_kit import to_action_result, to_artifacts, to_events

    action_result = to_action_result(_result(), prefer_native=True)
    artifacts = to_artifacts(_result(), prefer_native=True)
    events = to_events(_result(), task_id="task-1", prefer_native=True)

    assert isinstance(action_result, drivers.ActionResult)
    assert isinstance(artifacts[0], drivers.ArtifactHandle)
    assert isinstance(events[0], events_module.EventEnvelope)


def test_import_error_helper_message(monkeypatch):
    from slidex.integrations import automation_kit as module

    original = module._import_automation_kit_types

    def fake_import():
        raise ImportError("boom")

    monkeypatch.setattr(module, "_import_automation_kit_types", fake_import)
    with pytest.raises(ImportError, match="Install slidex\\[automation-kit\\]"):
        module.to_action_result(_result(), prefer_native=True)

    monkeypatch.setattr(module, "_import_automation_kit_types", original)


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
    request = capabilities.CapabilityRequest(
        capability="visual.challenge",
        operation="solve",
        parameters={
            "challenge_type": "image_text",
            "context": "android_screenshot_bytes",
            "image_bytes": b"fake-png",
            "provider": "auto",
        },
        metadata={"run_id": "run-1", "task_id": "task-1"},
    )

    result = __import__("asyncio").run(provider.aexecute(request))

    assert provider.manifest.name == "visual.challenge"
    assert solver.requests[0].challenge_type == ChallengeType.IMAGE_TEXT
    assert solver.requests[0].image_bytes == b"fake-png"
    assert result.success is True
    assert result.provider == "slidex"
    assert result.data["metadata"]["text"] == "dianping"
    assert result.metadata["visual_provider"] == "fake-ocr"


def test_slidex_visual_capability_preserves_artifacts_and_events():
    pytest.importorskip("automation_core")
    from automation_core import capabilities
    from slidex.integrations.automation_kit import SlidexVisualCapability

    class FakeVisualSolver:
        async def solve(self, request):
            return _result()

    request = capabilities.CapabilityRequest(
        capability="visual.challenge",
        operation="solve",
        parameters={
            "challenge_type": "ocr_text",
            "context": "image_bytes",
            "image_bytes": b"fake",
        },
        metadata={"task_id": "task-1"},
    )

    result = __import__("asyncio").run(
        SlidexVisualCapability(visual_solver=FakeVisualSolver()).aexecute(request)
    )

    assert result.artifacts[0].artifact_type == "ocr_result"
    assert result.artifacts[0].metadata["token"] == "[redacted]"
    assert result.events[-1].event_type == "task.end"
    assert result.events[-1].task_id == "task-1"
