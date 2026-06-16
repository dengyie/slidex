"""Additional integration tests for error handling"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from slidex import SliderSolver, CaptchaProvider, ProviderElements
from slidex.providers import ProviderRegistry
from typing import Optional, Tuple


@pytest.fixture(autouse=True)
def cleanup_test_providers():
    """每个测试后清理测试用 provider"""
    yield
    # 清理测试 provider
    test_providers = [
        "failing-init",
        "failing-gap",
        "crashing-detect",
        "cleanup-tracking",
        "slide-failure-cleanup",
    ]
    for name in test_providers:
        if name in ProviderRegistry._providers:
            del ProviderRegistry._providers[name]
            ProviderRegistry._detection_order = [
                (p, n) for p, n in ProviderRegistry._detection_order if n != name
            ]


class FailingInitProvider(CaptchaProvider):
    """Provider that fails during on_init()"""

    name = "failing-init"

    async def detect(self, page):
        return True

    async def on_init(self, page):
        raise RuntimeError("Simulated init failure")

    async def locate_elements(self, page):
        return ProviderElements(
            slider_btn=AsyncMock(),
            slider_track=AsyncMock(),
            bg_img=None,
            piece_img=None,
            track_width_px=300
        )

    async def extract_images(self, page, elements):
        return b"fake", b"fake"

    async def perform_slide(self, page, elements, gap_x, trajectory):
        pass

    def validate_response(self, response):
        return None


class FailingGapProvider(CaptchaProvider):
    """Provider that fails during find_gap()"""

    name = "failing-gap"

    async def detect(self, page):
        return True

    async def locate_elements(self, page):
        return ProviderElements(
            slider_btn=AsyncMock(),
            slider_track=AsyncMock(),
            bg_img=None,
            piece_img=None,
            track_width_px=300
        )

    async def extract_images(self, page, elements):
        return b"fake_bg", b"fake_piece"

    async def find_gap(self, bg_bytes: bytes, piece_bytes: bytes) -> Tuple[Optional[int], float]:
        raise ValueError("Simulated image decode failure")

    async def perform_slide(self, page, elements, gap_x, trajectory):
        pass

    def validate_response(self, response):
        return None


class TestProviderErrorHandling:
    """Test error handling in provider system"""

    @pytest.mark.asyncio
    async def test_provider_on_init_failure_fallback(self):
        """Provider on_init() 失败应该回退到 legacy 模式"""
        SliderSolver.register_provider("failing-init", FailingInitProvider, detection_priority=1)

        solver = SliderSolver(
            provider="failing-init",
            selectors={"slider_btn": ".fallback-btn"}  # 提供 legacy 配置
        )

        page = AsyncMock()
        result = await solver._detect_and_init_provider(page)

        # on_init 失败应该返回 False，触发 legacy 回退
        assert result is False
        assert solver._provider is None

    @pytest.mark.asyncio
    async def test_find_gap_exception_handled(self):
        """find_gap() 抛出异常应该被捕获，返回 (False, None)"""
        SliderSolver.register_provider("failing-gap", FailingGapProvider, detection_priority=1)

        solver = SliderSolver(provider="failing-gap")

        # Mock page with context
        page = AsyncMock()
        page.context.cookies = AsyncMock(return_value=[])

        # 初始化 provider（不会失败）
        await solver._detect_and_init_provider(page)
        assert solver._provider is not None

        # 尝试求解（find_gap 会失败）
        success, cookies = await solver._solve_with_provider(page)

        # 应该优雅处理异常
        assert success is False
        assert cookies is None

    @pytest.mark.asyncio
    async def test_provider_detect_exception_handled(self):
        """Provider detect() 抛出异常应该被视为检测失败"""
        from slidex import CaptchaProvider

        class CrashingDetectProvider(CaptchaProvider):
            name = "crashing-detect"

            async def detect(self, page):
                raise Exception("Simulated detection crash")

            async def locate_elements(self, page):
                pass

            async def extract_images(self, page, elements):
                pass

            async def perform_slide(self, page, elements, gap_x, trajectory):
                pass

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("crashing-detect", CrashingDetectProvider, detection_priority=1)

        solver = SliderSolver(provider="auto")
        page = AsyncMock()

        # auto 检测应该跳过崩溃的 provider
        result = await solver._detect_and_init_provider(page)

        # 可能检测到其他 provider 或失败
        # 关键是不应该崩溃
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_provider_cleanup_on_close(self):
        """Solver close() 应该调用 provider.on_cleanup()"""
        from slidex import CaptchaProvider

        cleanup_called = False

        class CleanupTrackingProvider(CaptchaProvider):
            name = "cleanup-tracking"

            async def detect(self, page):
                return True

            async def on_cleanup(self):
                nonlocal cleanup_called
                cleanup_called = True

            async def locate_elements(self, page):
                pass

            async def extract_images(self, page, elements):
                pass

            async def perform_slide(self, page, elements, gap_x, trajectory):
                pass

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("cleanup-tracking", CleanupTrackingProvider)

        solver = SliderSolver(provider="cleanup-tracking")
        page = AsyncMock()

        await solver._detect_and_init_provider(page)
        assert solver._provider is not None

        await solver.close()

        # on_cleanup 应该被调用
        assert cleanup_called is True

    @pytest.mark.asyncio
    async def test_cleanup_after_result_runs_when_slide_fails(self):
        """perform_slide() 抛出异常时也应该清理 provider 临时资源"""
        cleanup_called = False

        class SlideFailureCleanupProvider(CaptchaProvider):
            name = "slide-failure-cleanup"

            async def detect(self, page):
                return True

            async def locate_elements(self, page):
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300,
                )

            async def extract_images(self, page, elements):
                return b"fake_bg", b"fake_piece"

            async def find_gap(self, bg_bytes: bytes, piece_bytes: bytes) -> Tuple[Optional[int], float]:
                return 120, 0.95

            async def perform_slide(self, page, elements, gap_x, trajectory):
                raise RuntimeError("Simulated slide failure")

            async def cleanup_after_result(self, page):
                nonlocal cleanup_called
                cleanup_called = True

            async def validate_response(self, response):
                return None

        SliderSolver.register_provider("slide-failure-cleanup", SlideFailureCleanupProvider)

        solver = SliderSolver(provider="slide-failure-cleanup")
        page = AsyncMock()

        await solver._detect_and_init_provider(page)
        success, cookies = await solver._solve_with_provider(page)

        assert success is False
        assert cookies is None
        assert cleanup_called is True
