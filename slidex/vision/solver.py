from __future__ import annotations

import inspect
import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Optional

from slidex.ocr import FakeOcrExtractor, OcrTextExtractor
from slidex.solver import SliderSolver
from slidex.vision.models import (
    ChallengeType,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)
from slidex.vision.slider_image import SliderImageResult, SliderImageSolver

# slider_captcha 走纯图片求解（无浏览器）的上下文：只拿得到图片 bytes/path。
# ANDROID_SCREENSHOT_BYTES 复用 image_bytes 字段承载整屏截图。
_IMAGE_SLIDER_CONTEXTS = {
    VisionContext.IMAGE_BYTES,
    VisionContext.IMAGE_PATH,
    VisionContext.ANDROID_SCREENSHOT_BYTES,
}


class VisualChallengeSolver:
    def __init__(
        self,
        *,
        ocr_extractor: Optional[OcrTextExtractor] = None,
        slider_solver_factory: Optional[Callable[..., SliderSolver]] = None,
        slider_image_solver: Optional[SliderImageSolver] = None,
    ):
        self.ocr_extractor = ocr_extractor or FakeOcrExtractor()
        self.slider_solver_factory = slider_solver_factory or SliderSolver
        self.slider_image_solver = slider_image_solver or SliderImageSolver()

    async def solve(self, request: VisualChallengeRequest) -> VisualChallengeResult:
        started = time.time()
        if request.challenge_type in {ChallengeType.OCR_TEXT, ChallengeType.IMAGE_TEXT}:
            # OCR extractors are synchronous/CPU-bound. Isolate them so the
            # caller event loop remains responsive under Provider V2 runtime.
            return await asyncio.to_thread(self._solve_ocr, request, started)
        if request.challenge_type == ChallengeType.SLIDER_CAPTCHA:
            if request.context in _IMAGE_SLIDER_CONTEXTS:
                # 纯图片求解是 CPU 密集型，隔离到线程避免阻塞事件循环。
                # ANDROID_SCREENSHOT_BYTES 也走这里：安卓截图无 CDP/Page，
                # 只有整屏 bytes，走无块图缺口检测（解 dianping REQ-004）。
                return await asyncio.to_thread(
                    self._solve_slider_image, request, started
                )
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

    def _solve_slider_image(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        """纯图片滑块求解（IMAGE_BYTES / IMAGE_PATH 上下文），同步 CPU-bound。"""
        use_path = request.context == VisionContext.IMAGE_PATH
        if use_path and not request.image_path:
            return self._slider_image_error(request, started, "missing_image_path")
        if not use_path and not request.image_bytes:
            # IMAGE_BYTES 与 ANDROID_SCREENSHOT_BYTES 都从 image_bytes 承载
            return self._slider_image_error(request, started, "missing_image_bytes")

        background: Any = request.image_path if use_path else request.image_bytes
        piece: Optional[Any] = None
        if request.piece_image_bytes is not None:
            piece = request.piece_image_bytes
        elif request.piece_image_path is not None:
            piece = request.piece_image_path

        distance_scale = request.metadata.get("distance_scale")
        # bool 是 int 子类，需显式排除，否则 True/False 会被当 1.0/0.0
        if distance_scale is not None and (
            isinstance(distance_scale, bool)
            or not isinstance(distance_scale, (int, float))
        ):
            return self._slider_image_error(request, started, "invalid_distance_scale")

        result = self.slider_image_solver.solve(
            background,
            piece,
            distance_scale=float(distance_scale) if distance_scale is not None else None,
            roi=request.roi,
        )
        return VisualChallengeResult(
            success=result.success,
            challenge_type=request.challenge_type,
            provider="slidex-image",
            confidence=result.confidence,
            duration_ms=self._duration_ms(started),
            error_code=result.error_code,
            retryable=not result.success,
            cookies=None,
            artifacts=[],
            metadata={
                "context": request.context.value,
                "gap_x": result.gap_x,
                "distance_px": result.distance_px,
                "method": result.method,
                "gap_box": result.gap_box,
                "candidates": result.candidates,
                **result.metadata,
            },
        )

    @staticmethod
    def _slider_image_error(
        request: VisualChallengeRequest,
        started: float,
        error_code: str,
    ) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider="slidex-image",
            duration_ms=VisualChallengeSolver._duration_ms(started),
            error_code=error_code,
            retryable=True,
            cookies=None,
            artifacts=[],
            metadata={"context": request.context.value},
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
                        path=Path(slider.get_telemetry_dir()) / f"{telemetry.get('run_id', 'unknown')}.json",
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
