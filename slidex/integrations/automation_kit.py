from __future__ import annotations

import asyncio
import inspect
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
            CapabilityExecutionProfile,
        )
        from automation_core.drivers import ArtifactHandle
    except ImportError as exc:
        raise ImportError(
            "automation-kit>=0.5.0 is required. Install slidex[automation-kit] "
            "to use SlidexVisualCapability."
        ) from exc
    return (
        CapabilityManifest,
        CapabilityProtocolError,
        CapabilityResult,
        ArtifactHandle,
        CapabilityExecutionProfile,
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
            default_cancellation="cooperative",
            metadata={"implementation": "slidex"},
        )
        self.visual_solver = visual_solver or VisualChallengeSolver()

    def execution_profile(self, request):
        (
            _,
            CapabilityProtocolError,
            _,
            _,
            CapabilityExecutionProfile,
        ) = _import_automation_kit_types()
        parameters = dict(getattr(request, "parameters", {}) or {})
        context_name = parameters.get("context")
        challenge_type = parameters.get("challenge_type")
        if context_name in {"image_bytes", "android_screenshot_bytes", "image_path"} or (
            challenge_type in {"ocr_text", "image_text"}
        ):
            return CapabilityExecutionProfile(
                cancellation="unsupported",
                blocking=True,
            )
        return CapabilityExecutionProfile(cancellation="cooperative")

    async def execute(self, request, context):
        (
            _,
            CapabilityProtocolError,
            CapabilityResult,
            ArtifactHandle,
            _,
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
        vision_context = _enum_parameter(
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
        visual_metadata["run_id"] = context.run_id
        if context.task_id is not None:
            visual_metadata["task_id"] = context.task_id
        if context.correlation_id is not None:
            visual_metadata["correlation_id"] = context.correlation_id

        image_bytes = parameters.get("image_bytes")
        image_path = parameters.get("image_path")
        piece_image_bytes = parameters.get("piece_image_bytes")
        piece_image_path = parameters.get("piece_image_path")
        page = parameters.get("page")
        cdp_endpoint = parameters.get("cdp_endpoint")

        if vision_context in {
            VisionContext.IMAGE_BYTES,
            VisionContext.ANDROID_SCREENSHOT_BYTES,
        }:
            if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
                raise CapabilityProtocolError(
                    "image_bytes must be non-empty bytes for the selected context"
                )
            image_bytes = bytes(image_bytes)
            if piece_image_bytes is not None:
                if not isinstance(piece_image_bytes, (bytes, bytearray)):
                    raise CapabilityProtocolError(
                        "piece_image_bytes must be bytes when provided"
                    )
                piece_image_bytes = bytes(piece_image_bytes)
        elif vision_context == VisionContext.IMAGE_PATH:
            if not isinstance(image_path, (str, Path)) or not str(image_path).strip():
                raise CapabilityProtocolError(
                    "image_path is required for image_path context"
                )
            image_path = Path(image_path)
            if piece_image_path is not None:
                if not isinstance(piece_image_path, (str, Path)) or not str(
                    piece_image_path
                ).strip():
                    raise CapabilityProtocolError(
                        "piece_image_path must be a path string when provided"
                    )
                piece_image_path = Path(piece_image_path)
        elif vision_context == VisionContext.PLAYWRIGHT_PAGE and page is None:
            raise CapabilityProtocolError(
                "page is required for playwright_page context"
            )
        elif vision_context == VisionContext.CDP:
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
            context=vision_context,
            page=page,
            cdp_endpoint=cdp_endpoint,
            page_url=page_url,
            image_bytes=image_bytes,
            image_path=image_path,
            piece_image_bytes=piece_image_bytes,
            piece_image_path=piece_image_path,
            roi=roi,
            provider=provider,
            timeout_ms=timeout_ms,
            metadata=visual_metadata,
        )

        try:
            visual_result = await self.visual_solver.solve(visual_request)
        except asyncio.CancelledError:
            raise

        return CapabilityResult(
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


class SlidexManualCapability:
    """Expose the manual fallback flow as a second ``visual.challenge`` provider.

    Wraps :class:`~slidex.vision.manual.ManualFallbackSession`: ``execute``
    opens a manual session for the challenge and hands it to an injected
    async ``completer`` (a human or agent driver) whose returned metadata is
    converted into the visual result. Without a completer — unattended runs —
    the provider returns a typed ``manual_fallback_pending`` failure so the
    kit failover walk and retry policies see a deterministic outcome instead
    of hanging. Manifest priority stays below the auto solver; the kit
    resolver therefore prefers ``SlidexVisualCapability`` and only routes
    here when the auto provider fails or its breaker opens.
    """

    def __init__(self, *, completer=None, default_timeout_s: int = 300):
        (
            CapabilityManifest,
            _,
            _,
            _,
            _,
        ) = _import_automation_kit_types()
        if completer is not None and not callable(completer):
            raise TypeError("completer must be a callable or None")
        if isinstance(default_timeout_s, bool) or not isinstance(
            default_timeout_s, int
        ) or default_timeout_s <= 0:
            raise ValueError("default_timeout_s must be a positive integer")
        self.manifest = CapabilityManifest(
            name="visual.challenge",
            version="1.0.0",
            operations=("solve",),
            platforms=("web", "android", "image"),
            default_cancellation="cooperative",
            priority=-10,
            metadata={"implementation": "slidex-manual"},
        )
        self.completer = completer
        self.default_timeout_s = default_timeout_s

    def execution_profile(self, request):
        (
            _,
            _,
            _,
            _,
            CapabilityExecutionProfile,
        ) = _import_automation_kit_types()
        # With a completer the call blocks on human/agent input and may take
        # arbitrarily long; without one it returns immediately.
        return CapabilityExecutionProfile(
            cancellation="cooperative",
            blocking=self.completer is not None,
        )

    async def execute(self, request, context):
        (
            _,
            CapabilityProtocolError,
            CapabilityResult,
            _,
            _,
        ) = _import_automation_kit_types()
        from uuid import uuid4

        from slidex.vision.manual import ManualFallbackSession

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
        session_id = parameters.get("session_id")
        if session_id is not None:
            session_id = _non_blank_string(
                session_id, "session_id", CapabilityProtocolError
            )
        else:
            session_id = uuid4().hex
        token = parameters.get("token", "")
        if not isinstance(token, str):
            raise CapabilityProtocolError("token must be a string")
        timeout_s = parameters.get("timeout_s", self.default_timeout_s)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or timeout_s <= 0:
            raise CapabilityProtocolError("timeout_s must be a positive integer")

        session = ManualFallbackSession(
            session_id=session_id,
            challenge_type=challenge_type,
            token=token,
            timeout_s=timeout_s,
        )
        common_metadata = {
            "capability_version": self.manifest.version,
            "session_id": session_id,
        }
        if self.completer is None:
            # Unattended runs cannot complete a manual session. A typed
            # non-retryable failure keeps the runtime from pointless retries
            # (and session_id churn); it intentionally counts toward the kit
            # health threshold so unattended deployments stop probing the
            # manual path — the breaker re-probes after its cooldown, so an
            # attended configuration recovers automatically.
            return CapabilityResult(
                success=False,
                provider="slidex-manual",
                error_code="manual_fallback_pending",
                retryable=False,
                data=session.to_dict(),
                metadata=common_metadata,
            )

        completion = self.completer(session)
        if inspect.isawaitable(completion):
            completion = await completion
        if not isinstance(completion, dict):
            # The completer is runtime-injected code: a wrong return shape is
            # a provider runtime failure, not a wiring/contract violation —
            # return a typed failure so the kit health ledger records it (a
            # broken completer trips the breaker instead of re-running every
            # walk) and the auto provider's business failure stays visible.
            return CapabilityResult(
                success=False,
                provider="slidex-manual",
                error_code="manual_completer_invalid",
                retryable=False,
                data=session.to_dict(),
                metadata={
                    **common_metadata,
                    "reason": "completer must return a metadata dictionary",
                    "completer_type": type(self.completer).__name__,
                },
            )
        visual_result = session.complete_metadata(completion)
        return CapabilityResult(
            success=visual_result.success,
            provider="slidex-manual",
            data=visual_result.to_dict(),
            error_code=visual_result.error_code,
            retryable=visual_result.retryable,
            metadata={**common_metadata, "visual_provider": "manual"},
        )
