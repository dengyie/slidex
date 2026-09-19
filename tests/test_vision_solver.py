from unittest.mock import AsyncMock, MagicMock

import cv2
import pytest

from slidex.ocr import FakeOcrExtractor
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)
from tests.test_slider_image import (
    NOTCH,
    crop_notch,
    encode_png,
    make_notched_background,
    make_textured_background,
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
async def test_visual_solver_closes_slider_after_existing_page_route():
    slider = MagicMock()
    slider.solve_on_existing_page = AsyncMock(return_value=(True, {"session": "abc"}))
    slider.get_telemetry_summary.return_value = {"run_id": "r1", "status": "success"}
    slider.close = AsyncMock()

    solver = VisualChallengeSolver(slider_solver_factory=lambda **_: slider)
    await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.CDP,
            cdp_endpoint="ws://localhost:9222/devtools/browser/1",
            page_url="https://example.test",
            provider="auto",
        )
    )

    slider.close.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_visual_solver_closes_slider_after_playwright_page_route():
    page = object()
    slider = MagicMock()
    slider.solve_on_page = AsyncMock(return_value=(True, {"session": "xyz"}))
    slider.get_telemetry_summary.return_value = {"run_id": "r2", "status": "success"}
    slider.close = AsyncMock()

    solver = VisualChallengeSolver(slider_solver_factory=lambda **_: slider)
    await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.PLAYWRIGHT_PAGE,
            page=page,
            page_url="https://example.test",
            provider="auto",
        )
    )

    slider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_visual_solver_routes_slider_image_bytes():
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=encode_png(make_notched_background()),
        )
    )

    assert result.success is True
    assert result.provider == "slidex-image"
    assert result.metadata["gap_x"] == pytest.approx(NOTCH[0], abs=6)
    assert result.metadata["context"] == "image_bytes"
    assert result.cookies is None


@pytest.mark.asyncio
async def test_visual_solver_slider_image_bytes_with_piece():
    bg = make_textured_background()
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=encode_png(bg),
            piece_image_bytes=encode_png(crop_notch(bg)),
        )
    )

    assert result.success is True
    assert result.metadata["method"] == "template_edge"
    assert result.metadata["gap_x"] == pytest.approx(NOTCH[0], abs=4)


@pytest.mark.asyncio
async def test_visual_solver_routes_slider_image_path(tmp_path):
    image_path = tmp_path / "bg.png"
    image_path.write_bytes(encode_png(make_notched_background()))

    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_PATH,
            image_path=image_path,
        )
    )

    assert result.success is True
    assert result.provider == "slidex-image"
    assert result.metadata["gap_x"] == pytest.approx(NOTCH[0], abs=6)


@pytest.mark.asyncio
async def test_visual_solver_slider_image_missing_bytes():
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_BYTES,
        )
    )

    assert result.success is False
    assert result.error_code == "missing_image_bytes"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_visual_solver_slider_image_invalid_distance_scale():
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=encode_png(make_notched_background()),
            metadata={"distance_scale": "abc"},
        )
    )

    assert result.success is False
    assert result.error_code == "invalid_distance_scale"


@pytest.mark.asyncio
async def test_visual_solver_slider_image_invalid_roi():
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=encode_png(make_notched_background()),
            roi={"x": 10},
        )
    )

    assert result.success is False
    assert result.error_code == "invalid_roi"


@pytest.mark.asyncio
async def test_visual_solver_routes_android_screenshot_bytes():
    """dianping REQ-004: 安卓截图整屏 bytes 走无块图缺口检测。"""
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.ANDROID_SCREENSHOT_BYTES,
            image_bytes=encode_png(make_notched_background()),
        )
    )

    assert result.success is True
    assert result.provider == "slidex-image"
    assert result.metadata["gap_x"] == pytest.approx(NOTCH[0], abs=6)
    assert result.metadata["context"] == "android_screenshot_bytes"


@pytest.mark.asyncio
async def test_visual_solver_android_screenshot_missing_bytes():
    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.ANDROID_SCREENSHOT_BYTES,
        )
    )

    assert result.success is False
    assert result.error_code == "missing_image_bytes"


@pytest.mark.asyncio
async def test_visual_solver_slider_image_bool_distance_scale_rejected():
    """回归：bool 是 int 子类，distance_scale=True/False 必须被拒（修 P3）。"""
    solver = VisualChallengeSolver()
    for bad in (True, False):
        result = await solver.solve(
            VisualChallengeRequest(
                challenge_type=ChallengeType.SLIDER_CAPTCHA,
                context=VisionContext.IMAGE_BYTES,
                image_bytes=encode_png(make_notched_background()),
                metadata={"distance_scale": bad},
            )
        )
        assert result.success is False
        assert result.error_code == "invalid_distance_scale"
