"""Tests for provider system"""

import pytest
from slidex import (
    SliderSolver,
    ProviderRegistry,
    CaptchaProvider,
    AliyunNoCaptchaProvider,
    GeeTestProvider,
)


class TestProviderRegistry:
    def test_builtin_providers_registered(self):
        providers = ProviderRegistry.list_providers()
        assert "aliyun-nocaptcha" in providers
        assert "geetest" in providers

    def test_get_provider(self):
        provider = ProviderRegistry.get("aliyun-nocaptcha")
        assert isinstance(provider, AliyunNoCaptchaProvider)
        assert provider.name == "aliyun-nocaptcha"

    def test_get_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            ProviderRegistry.get("nonexistent")

    def test_register_custom_provider(self):
        class TestProvider(CaptchaProvider):
            name = "test"
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

        ProviderRegistry.register("test-custom", TestProvider, detection_priority=999)
        assert "test-custom" in ProviderRegistry.list_providers()

        provider = ProviderRegistry.get("test-custom")
        assert isinstance(provider, TestProvider)


class TestProviderIntegration:
    def test_solver_with_provider_param(self):
        solver = SliderSolver(provider="aliyun-nocaptcha")
        assert solver._use_provider_mode is True
        assert solver._provider_name == "aliyun-nocaptcha"

    def test_solver_with_auto_provider(self):
        solver = SliderSolver(provider="auto")
        assert solver._use_provider_mode is True
        assert solver._provider_name == "auto"

    def test_solver_without_provider_legacy_mode(self):
        solver = SliderSolver()
        assert solver._use_provider_mode is False

    def test_solver_register_provider_classmethod(self):
        class MyProvider(CaptchaProvider):
            name = "my-provider"
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

        SliderSolver.register_provider("my-custom", MyProvider)
        assert "my-custom" in SliderSolver.list_providers()


class TestProviderClasses:
    def test_aliyun_provider_attributes(self):
        p = AliyunNoCaptchaProvider()
        assert p.name == "aliyun-nocaptcha"
        assert p.description

    def test_geetest_provider_attributes(self):
        p = GeeTestProvider()
        assert p.name == "geetest"
        assert p.description
