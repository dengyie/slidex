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
