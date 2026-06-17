from unittest.mock import AsyncMock, MagicMock

import pytest

from slidex.ocr import FakeOcrExtractor
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)


@pytest.mark.asyncio
async def test_visual_solver_routes_ocr_text_to_extractor():
    solver = VisualChallengeSolver(ocr_extractor=FakeOcrExtractor(text="大麦", confidence=0.9))

    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.OCR_TEXT,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=b"fake",
        )
    )

    assert result.success is True
    assert result.challenge_type == ChallengeType.OCR_TEXT
    assert result.provider == "fake"
    assert result.confidence == 0.9
    assert result.metadata["text"] == "大麦"


@pytest.mark.asyncio
async def test_visual_solver_routes_slider_to_existing_page():
    slider = MagicMock()
    slider.solve_on_existing_page = AsyncMock(return_value=(True, {"session": "abc"}))
    slider.get_telemetry_summary.return_value = {"run_id": "r1", "status": "success"}

    solver = VisualChallengeSolver(slider_solver_factory=lambda **_: slider)
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.CDP,
            cdp_endpoint="ws://localhost:9222/devtools/browser/1",
            page_url="https://example.test",
            provider="auto",
        )
    )

    assert result.success is True
    assert result.challenge_type == ChallengeType.SLIDER_CAPTCHA
    assert result.cookies == {"session": "abc"}
    assert result.metadata["telemetry"]["status"] == "success"


@pytest.mark.asyncio
async def test_visual_solver_routes_slider_to_playwright_page():
    page = object()
    slider = MagicMock()
    slider.solve_on_page = AsyncMock(return_value=(True, {"session": "xyz"}))
    slider.get_telemetry_summary.return_value = {"run_id": "r2", "status": "success"}

    solver = VisualChallengeSolver(slider_solver_factory=lambda **_: slider)
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.PLAYWRIGHT_PAGE,
            page=page,
            page_url="https://example.test",
            provider="auto",
        )
    )

    assert result.success is True
    assert result.cookies == {"session": "xyz"}
    slider.solve_on_page.assert_called_once_with(page, page_url="https://example.test")
