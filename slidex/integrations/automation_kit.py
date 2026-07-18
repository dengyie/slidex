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


def _import_capability_types() -> Tuple[Any, Any]:
    try:
        from automation_core.capabilities import CapabilityManifest, CapabilityResult
    except ImportError as exc:
        raise ImportError(
            "automation-kit is not installed. Install slidex[automation-kit] "
            "to use SlidexVisualCapability."
        ) from exc
    return CapabilityManifest, CapabilityResult


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
    if not prefer_native:
        return [
            {
                "artifact_type": artifact.artifact_type,
                "path": str(artifact.path),
                "metadata": redact_sensitive(artifact.metadata),
            }
            for artifact in result.artifacts
        ]

    try:
        _, ArtifactHandle, _ = _import_automation_kit_types()
    except ImportError as exc:
        raise ImportError(
            "Install slidex[automation-kit] to use native automation-kit adapters."
        ) from exc
    return [
        ArtifactHandle(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            metadata=redact_sensitive(artifact.metadata),
        )
        for artifact in result.artifacts
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


class SlidexVisualCapability:
    """Expose Slidex through the generic automation-kit capability contract."""

    def __init__(self, *, visual_solver=None):
        CapabilityManifest, _ = _import_capability_types()
        from slidex.vision import VisualChallengeSolver

        self.manifest = CapabilityManifest(
            name="visual.challenge",
            version="1.0.0",
            operations=("solve",),
            platforms=("web", "android", "image"),
            metadata={"implementation": "slidex"},
        )
        self.visual_solver = visual_solver or VisualChallengeSolver()

    async def aexecute(self, request):
        _, CapabilityResult = _import_capability_types()
        from slidex.vision import (
            ChallengeType,
            VisionContext,
            VisualChallengeRequest,
        )

        parameters = dict(request.parameters)
        visual_metadata = dict(parameters.get("metadata") or {})
        for key in ("run_id", "task_id", "correlation_id"):
            if request.metadata.get(key) is not None:
                visual_metadata[key] = request.metadata[key]

        visual_request = VisualChallengeRequest(
            challenge_type=ChallengeType(parameters["challenge_type"]),
            context=VisionContext(parameters["context"]),
            page=parameters.get("page"),
            cdp_endpoint=parameters.get("cdp_endpoint"),
            page_url=parameters.get("page_url", ""),
            image_bytes=parameters.get("image_bytes"),
            image_path=parameters.get("image_path"),
            roi=parameters.get("roi"),
            provider=parameters.get("provider", "auto"),
            timeout_ms=parameters.get("timeout_ms", 30_000),
            metadata=visual_metadata,
        )
        visual_result = await self.visual_solver.solve(visual_request)

        return CapabilityResult(
            success=visual_result.success,
            provider="slidex",
            data=visual_result.to_dict(),
            error_code=visual_result.error_code,
            retryable=visual_result.retryable,
            artifacts=to_artifacts(visual_result, prefer_native=True),
            events=to_events(
                visual_result,
                task_id=request.metadata.get("task_id"),
                prefer_native=True,
            ),
            metadata={
                "capability_version": self.manifest.version,
                "visual_provider": visual_result.provider,
            },
        )
