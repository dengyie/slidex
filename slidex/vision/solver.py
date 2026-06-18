from __future__ import annotations

import inspect
import time
from pathlib import Path
from typing import Callable, Optional

from slidex.ocr import FakeOcrExtractor, OcrTextExtractor
from slidex.solver import SliderSolver
from slidex.vision.models import (
    ChallengeType,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)


class VisualChallengeSolver:
    def __init__(
        self,
        *,
        ocr_extractor: Optional[OcrTextExtractor] = None,
        slider_solver_factory: Optional[Callable[..., SliderSolver]] = None,
    ):
        self.ocr_extractor = ocr_extractor or FakeOcrExtractor()
        self.slider_solver_factory = slider_solver_factory or SliderSolver

    async def solve(self, request: VisualChallengeRequest) -> VisualChallengeResult:
        started = time.time()
        if request.challenge_type in {ChallengeType.OCR_TEXT, ChallengeType.IMAGE_TEXT}:
            return self._solve_ocr(request, started)
        if request.challenge_type == ChallengeType.SLIDER_CAPTCHA:
            return await self._solve_slider(request, started)
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=request.provider,
            duration_ms=self._duration_ms(started),
            error_code="unsupported_challenge_type",
            retryable=False,
            artifacts=[],
            metadata={"context": request.context.value},
        )

    def _solve_ocr(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        result = self.ocr_extractor.extract(
            image_bytes=request.image_bytes,
            image_path=request.image_path,
            roi=request.roi,
            language=request.metadata.get("language"),
        )
        success = bool(result.text)
        return VisualChallengeResult(
            success=success,
            challenge_type=request.challenge_type,
            provider=result.provider,
            confidence=result.confidence,
            duration_ms=self._duration_ms(started),
            error_code=None if success else result.metadata.get("error_code", "ocr_failed"),
            retryable=not success,
            cookies=None,
            artifacts=[],
            metadata={
                "text": result.text,
                "language": result.language,
                "boxes": [box.__dict__ for box in result.boxes],
                **result.metadata,
            },
        )

    async def _solve_slider(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        slider = self.slider_solver_factory(
            cookie_id=str(request.metadata.get("cookie_id", "default")),
            provider=request.provider,
        )
        try:
            if request.context == VisionContext.CDP:
                success, cookies = await slider.solve_on_existing_page(
                    cdp_endpoint=request.cdp_endpoint or "",
                    page_url=request.page_url,
                )
            elif request.context == VisionContext.PLAYWRIGHT_PAGE:
                success, cookies = await slider.solve_on_page(request.page, page_url=request.page_url)
            else:
                return VisualChallengeResult(
                    success=False,
                    challenge_type=request.challenge_type,
                    provider=request.provider,
                    duration_ms=self._duration_ms(started),
                    error_code="unsupported_slider_context",
                    retryable=False,
                )

            telemetry = slider.get_telemetry_summary()
            return VisualChallengeResult(
                success=success,
                challenge_type=request.challenge_type,
                provider=str(telemetry.get("provider_name") or request.provider),
                confidence=float(telemetry.get("confidence") or 0.0),
                duration_ms=self._duration_ms(started),
                error_code=None if success else str(telemetry.get("failure_reason") or "solve_failed"),
                retryable=not success,
                cookies=cookies,
                artifacts=[
                    VisionArtifact(
                        artifact_type="telemetry",
                        path=Path("telemetry") / f"{telemetry.get('run_id', 'unknown')}.json",
                        metadata={"run_id": str(telemetry.get("run_id", ""))},
                    )
                ],
                metadata={"telemetry": telemetry},
            )
        finally:
            close = getattr(slider, "close", None)
            if close:
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round(max(0.0, (time.time() - started) * 1000), 1)
