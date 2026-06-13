"""Integration tests for provider system with mocked Playwright"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from slidex import SliderSolver, AliyunNoCaptchaProvider, GeeTestProvider


class TestProviderDetection:
    """Test provider auto-detection logic"""

    @pytest.mark.asyncio
    async def test_auto_detection_aliyun(self):
        """Provider auto mode should detect Aliyun NoCaptcha"""
        solver = SliderSolver(provider="auto")

        # Mock page with Aliyun characteristics
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=MagicMock())  # #nc_1_wrapper exists
        page.query_selector_all = AsyncMock(return_value=[])
        page.evaluate = AsyncMock(return_value=False)

        result = await solver._detect_and_init_provider(page)
        assert result is True
        assert solver._provider is not None
        assert solver._provider.name == "aliyun-nocaptcha"

    @pytest.mark.asyncio
    async def test_auto_detection_geetest(self):
        """Provider auto mode should detect GeeTest"""
        solver = SliderSolver(provider="auto")

        # Mock page with GeeTest characteristics (no Aliyun)
        page = AsyncMock()
        # Aliyun detection will fail (no nc_1_wrapper, no iframes, no _nocaptcha)
        async def mock_query_selector(selector):
            if "nc_1" in selector or "nc_" in selector:
                return None  # No Aliyun elements
            if "geetest" in selector:
                return MagicMock()  # GeeTest panel exists
            return None

        page.query_selector = AsyncMock(side_effect=mock_query_selector)
        page.query_selector_all = AsyncMock(return_value=[])  # No iframes

        # Mock evaluate: no _nocaptcha, but has initGeetest
        async def mock_evaluate(script):
            if "_nocaptcha" in script:
                return False  # No Aliyun
            if "initGeetest" in script:
                return True  # Has GeeTest
            return False

        page.evaluate = AsyncMock(side_effect=mock_evaluate)

        result = await solver._detect_and_init_provider(page)
        assert result is True
        assert solver._provider is not None
        assert solver._provider.name == "geetest"

    @pytest.mark.asyncio
    async def test_detection_failure_returns_false(self):
        """Auto detection should return False when no provider matches"""
        solver = SliderSolver(provider="auto")

        # Mock page with no recognizable CAPTCHA
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.query_selector_all = AsyncMock(return_value=[])
        page.evaluate = AsyncMock(return_value=False)

        result = await solver._detect_and_init_provider(page)
        assert result is False
        assert solver._provider is None


class TestProviderSelection:
    """Test manual provider selection"""

    @pytest.mark.asyncio
    async def test_manual_provider_aliyun(self):
        """Manual provider selection should work"""
        solver = SliderSolver(provider="aliyun-nocaptcha")

        page = AsyncMock()
        result = await solver._detect_and_init_provider(page)

        assert result is True
        assert solver._provider.name == "aliyun-nocaptcha"

    @pytest.mark.asyncio
    async def test_manual_provider_geetest(self):
        """Manual provider selection for GeeTest"""
        solver = SliderSolver(provider="geetest")

        page = AsyncMock()
        result = await solver._detect_and_init_provider(page)

        assert result is True
        assert solver._provider.name == "geetest"

    @pytest.mark.asyncio
    async def test_invalid_provider_returns_false(self):
        """Invalid provider name should return False"""
        solver = SliderSolver(provider="nonexistent")

        page = AsyncMock()
        result = await solver._detect_and_init_provider(page)

        assert result is False
        assert solver._provider is None


class TestProviderModeIntegration:
    """Test provider mode integration with solver"""

    def test_solver_without_provider_uses_legacy(self):
        """Solver without provider= should use legacy mode"""
        solver = SliderSolver()
        assert solver._use_provider_mode is False
        assert solver._provider is None

    def test_solver_with_provider_enables_mode(self):
        """Solver with provider= should enable provider mode"""
        solver = SliderSolver(provider="auto")
        assert solver._use_provider_mode is True
        assert solver._provider_name == "auto"

    def test_solver_with_manual_provider(self):
        """Solver with specific provider name"""
        solver = SliderSolver(provider="geetest")
        assert solver._use_provider_mode is True
        assert solver._provider_name == "geetest"


class TestProviderClassMethods:
    """Test SliderSolver class methods for provider management"""

    def test_list_providers(self):
        """list_providers should return registered providers"""
        providers = SliderSolver.list_providers()
        assert "aliyun-nocaptcha" in providers
        assert "geetest" in providers
        assert isinstance(providers, list)

    def test_register_custom_provider(self):
        """Custom providers can be registered"""
        from slidex import CaptchaProvider

        class TestProvider(CaptchaProvider):
            name = "test-integration"

            async def detect(self, page):
                return False

            async def locate_elements(self, page):
                pass

            async def extract_images(self, page, elements):
                pass

            async def perform_slide(self, page, elements, gap_x, trajectory):
                pass

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("test-integration", TestProvider, detection_priority=999)

        providers = SliderSolver.list_providers()
        assert "test-integration" in providers
