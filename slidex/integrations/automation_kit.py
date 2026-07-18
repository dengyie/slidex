from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Tuple

from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
    redact_sensitive,
)


def _import_automation_kit_types() -> Tuple[Any, Any, Any, Any, Any]:
    try:
        from automation_core.capabilities import (
            CapabilityManifest,
            CapabilityProtocolError,
            CapabilityResult,
        )
        from automation_core.drivers import ArtifactHandle
        from automation_core.events import CapabilityEndEvent
    except ImportError as exc:
        raise ImportError(
            "automation-kit>=0.2.0 is required. Install slidex[automation-kit] "
            "to use SlidexVisualCapability."
        ) from exc
    return (
        CapabilityManifest,
        CapabilityProtocolError,
        CapabilityResult,
        ArtifactHandle,
        CapabilityEndEvent,
    )


def _enum_parameter(enum_type, parameters: Dict[str, Any], name: str, error_type):
    if name not in parameters:
        raise error_type(f"missing required parameter: {name}")
    try:
        return enum_type(parameters[name])
    except (TypeError, ValueError) as exc:
        raise error_type(f"invalid {name}: {parameters[name]!r}") from exc


def _non_blank_string(value: Any, name: str, error_type) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{name} must be a non-blank string")
    return value.strip()


class SlidexVisualCapability:
    """Expose Slidex through the automation-kit capability contract."""

    def __init__(self, *, visual_solver=None):
        CapabilityManifest, _, _, _, _ = _import_automation_kit_types()
        self.manifest = CapabilityManifest(
            name="visual.challenge",
            version="1.0.0",
            operations=("solve",),
            platforms=("web", "android", "image"),
            metadata={"implementation": "slidex"},
        )
        self.visual_solver = visual_solver or VisualChallengeSolver()

    async def aexecute(self, request):
        (
            _,
            CapabilityProtocolError,
            CapabilityResult,
            ArtifactHandle,
            CapabilityEndEvent,
        ) = _import_automation_kit_types()

        if request.capability != self.manifest.name:
            raise CapabilityProtocolError(
                f"unsupported capability: {request.capability}"
            )
        if request.operation != "solve":
            raise CapabilityProtocolError(
                f"unsupported operation: {request.operation}"
            )

        parameters = dict(request.parameters)
        challenge_type = _enum_parameter(
            ChallengeType,
            parameters,
            "challenge_type",
            CapabilityProtocolError,
        )
        context = _enum_parameter(
            VisionContext,
            parameters,
            "context",
            CapabilityProtocolError,
        )
        timeout_ms = parameters.get("timeout_ms", 30_000)
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise CapabilityProtocolError("timeout_ms must be a positive integer")

        provider = _non_blank_string(
            parameters.get("provider", "auto"),
            "provider",
            CapabilityProtocolError,
        )
        visual_metadata = parameters.get("metadata", {})
        if not isinstance(visual_metadata, dict):
            raise CapabilityProtocolError("metadata must be a dictionary")
        visual_metadata = dict(visual_metadata)
        for key in ("run_id", "task_id", "correlation_id"):
            if request.metadata.get(key) is not None:
                visual_metadata[key] = request.metadata[key]

        image_bytes = parameters.get("image_bytes")
        image_path = parameters.get("image_path")
        page = parameters.get("page")
        cdp_endpoint = parameters.get("cdp_endpoint")

        if context in {
            VisionContext.IMAGE_BYTES,
            VisionContext.ANDROID_SCREENSHOT_BYTES,
        }:
            if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
                raise CapabilityProtocolError(
                    "image_bytes must be non-empty bytes for the selected context"
                )
            image_bytes = bytes(image_bytes)
        elif context == VisionContext.IMAGE_PATH:
            if not isinstance(image_path, (str, Path)) or not str(image_path).strip():
                raise CapabilityProtocolError(
                    "image_path is required for image_path context"
                )
            image_path = Path(image_path)
        elif context == VisionContext.PLAYWRIGHT_PAGE and page is None:
            raise CapabilityProtocolError(
                "page is required for playwright_page context"
            )
        elif context == VisionContext.CDP:
            cdp_endpoint = _non_blank_string(
                cdp_endpoint,
                "cdp_endpoint",
                CapabilityProtocolError,
            )

        page_url = parameters.get("page_url", "")
        if not isinstance(page_url, str):
            raise CapabilityProtocolError("page_url must be a string")
        roi = parameters.get("roi")
        if roi is not None and not isinstance(roi, dict):
            raise CapabilityProtocolError("roi must be a dictionary")

        visual_request = VisualChallengeRequest(
            challenge_type=challenge_type,
            context=context,
            page=page,
            cdp_endpoint=cdp_endpoint,
            page_url=page_url,
            image_bytes=image_bytes,
            image_path=image_path,
            roi=roi,
            provider=provider,
            timeout_ms=timeout_ms,
            metadata=visual_metadata,
        )

        try:
            visual_result = await asyncio.wait_for(
                self.visual_solver.solve(visual_request),
                timeout=timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            result = CapabilityResult(
                success=False,
                provider="slidex",
                error_code="timeout",
                retryable=True,
                metadata={"capability_version": self.manifest.version},
            )
        else:
            result = CapabilityResult(
                success=visual_result.success,
                provider="slidex",
                data=visual_result.to_dict(),
                error_code=visual_result.error_code,
                retryable=visual_result.retryable,
                artifacts=[
                    ArtifactHandle(
                        artifact_type=artifact.artifact_type,
                        path=artifact.path,
                        metadata=redact_sensitive(artifact.metadata),
                    )
                    for artifact in visual_result.artifacts
                ],
                metadata={
                    "capability_version": self.manifest.version,
                    "visual_provider": visual_result.provider,
                },
            )

        capability_event = CapabilityEndEvent(
            capability=request.capability,
            operation=request.operation,
            provider=result.provider,
            success=result.success,
            task_id=request.metadata.get("task_id"),
            error_code=result.error_code,
        ).to_envelope()
        return CapabilityResult(
            success=result.success,
            provider=result.provider,
            data=result.data,
            error_code=result.error_code,
            retryable=result.retryable,
            artifacts=result.artifacts,
            events=[capability_event],
            metadata=result.metadata,
        )
