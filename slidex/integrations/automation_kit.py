from __future__ import annotations

from typing import Any, Dict, List, Tuple

from slidex.vision import VisualChallengeResult, redact_sensitive


def _import_automation_kit_types() -> Tuple[Any, Any, Any]:
    try:
        from automation_core.drivers import ActionResult, ArtifactHandle
        from automation_core.events import EventEnvelope
    except ImportError as exc:
        raise ImportError(
            "automation-kit is not installed. Install slidex[automation-kit] to use native adapters."
        ) from exc
    return ActionResult, ArtifactHandle, EventEnvelope


def to_action_result(result: VisualChallengeResult, *, prefer_native: bool = False):
    payload = {
        "success": result.success,
        "message": f"{result.challenge_type.value} solved by {result.provider}",
        "data": result.to_dict(),
    }
    if not prefer_native:
        return payload

    try:
        ActionResult, _, _ = _import_automation_kit_types()
    except ImportError as exc:
        raise ImportError(
            "Install slidex[automation-kit] to use native automation-kit adapters."
        ) from exc
    return ActionResult(**payload)


def to_artifacts(result: VisualChallengeResult, *, prefer_native: bool = False):
    artifacts: List[Dict[str, Any]] = [
        {
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
            "metadata": redact_sensitive(artifact.metadata),
        }
        for artifact in result.artifacts
    ]
    if not prefer_native:
        return artifacts

    try:
        _, ArtifactHandle, _ = _import_automation_kit_types()
    except ImportError as exc:
        raise ImportError(
            "Install slidex[automation-kit] to use native automation-kit adapters."
        ) from exc
    return [
        ArtifactHandle(
            artifact_type=artifact["artifact_type"],
            path=artifact["path"],
            metadata=artifact["metadata"],
        )
        for artifact in artifacts
    ]


def to_events(
    result: VisualChallengeResult,
    *,
    task_id: str | None = None,
    prefer_native: bool = False,
):
    events: List[Dict[str, Any]] = []
    for artifact in result.artifacts:
        events.append(
            {
                "event_type": "artifact",
                "task_id": task_id,
                "payload": {
                    "artifact_type": artifact.artifact_type,
                    "path": str(artifact.path),
                    "metadata": redact_sensitive(artifact.metadata),
                },
            }
        )
    events.append(
        {
            "event_type": "task.end",
            "task_id": task_id,
            "payload": result.to_dict(),
        }
    )

    if not prefer_native:
        return events

    try:
        _, _, EventEnvelope = _import_automation_kit_types()
    except ImportError as exc:
        raise ImportError(
            "Install slidex[automation-kit] to use native automation-kit adapters."
        ) from exc
    return [EventEnvelope(**event) for event in events]
