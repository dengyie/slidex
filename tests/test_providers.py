"""Tests for provider system"""

import pytest
import cv2
import numpy as np
from unittest.mock import AsyncMock, MagicMock
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
            async def perform_slide(self, page, elements, gap_x, trajectory, cdp_session=None):
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
            async def perform_slide(self, page, elements, gap_x, trajectory, cdp_session=None):
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

    @pytest.mark.asyncio
    async def test_aliyun_validate_response_awaits_body(self):
        response = type("Response", (), {})()
        response.url = "https://example.com/_____tmd_____/slide"

        async def body():
            return b'{"code": 0}'

        response.body = body

        result = await AliyunNoCaptchaProvider().validate_response(response)
        assert result is True

    @pytest.mark.asyncio
    async def test_aliyun_cdp_applies_end_hold_scale(self, monkeypatch):
        captured = {}

        async def fake_dispatch(session, timeline):
            captured["gap"] = timeline[-1][0]

        monkeypatch.setattr("slidex._drag.dispatch_drag_timeline", fake_dispatch)
        monkeypatch.setattr("slidex._drag.slide_end_hold_range", lambda: (0.50, 0.50))

        provider = AliyunNoCaptchaProvider()
        slider_btn = AsyncMock()
        slider_btn.bounding_box = AsyncMock(
            return_value={"x": 0.0, "y": 0.0, "width": 40.0, "height": 40.0}
        )
        elements = type("Elements", (), {"slider_btn": slider_btn})()
        page = MagicMock()
        page.on = MagicMock()

        class _Sess:
            async def send(self, method, params=None):
                return None

        await provider.perform_slide(
            page,
            elements,
            100,
            [(0, 0, 50), (100, 0, 30)],
            cdp_session=_Sess(),
            extra_overshoot=False,
            end_hold_scale=1.6,
        )
        assert captured["gap"] == pytest.approx(500.0 * 1.6)

    @pytest.mark.asyncio
    async def test_mixin_forwards_end_hold_scale(self):
        from slidex._gestures import GesturePlan

        solver = SliderSolver(provider="aliyun-nocaptcha")
        received = {}

        async def fake_slide(
            page,
            elements,
            gap_x,
            trajectory,
            extra_overshoot=True,
            end_hold_scale=1.0,
            cdp_session=None,
        ):
            received["extra_overshoot"] = extra_overshoot
            received["end_hold_scale"] = end_hold_scale
            received["cdp_session"] = cdp_session

        solver._provider = MagicMock()
        solver._provider.perform_slide = fake_slide
        plan = GesturePlan(
            archetype="overshoot_snapback",
            points=[(0, 0, 50), (100, 0, 30)],
            extra_overshoot=False,
            end_hold_scale=1.6,
        )
        await solver._call_perform_slide(None, None, 100, list(plan.points), plan)
        assert received.get("end_hold_scale") == pytest.approx(1.6)
        assert received.get("extra_overshoot") is False

    @pytest.mark.asyncio
    async def test_geetest_validate_response_awaits_body(self):
        response = type("Response", (), {})()
        response.url = "https://api.geetest.com/api/v4/slider"

        async def body():
            return b'{"status": "success"}'

        response.body = body

        provider = GeeTestProvider()
        result = await provider.validate_response(response)
        assert result is True
        assert provider._result_code == 0

    @pytest.mark.asyncio
    async def test_geetest_validate_response_fail_writes_code(self):
        response = type("Response", (), {})()
        response.url = "https://api.geetest.com/api/v4/slider"

        async def body():
            return b'{"status": "fail"}'

        response.body = body
        provider = GeeTestProvider()
        result = await provider.validate_response(response)
        assert result is False
        assert provider._result_code == 1

    @pytest.mark.asyncio
    async def test_get_result_packet_fail_without_numeric_code_is_not_minus_one(self):
        from slidex.providers import SolveResult

        class PacketFailProvider(CaptchaProvider):
            name = "packet-fail"

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

        provider = PacketFailProvider()
        provider._result = False
        provider._result_code = None
        provider._result_event = __import__("asyncio").Event()
        provider._result_event.set()
        page = MagicMock()
        page.context.cookies = AsyncMock(return_value=[])
        page.url = "https://example.com/"
        result = await provider.get_result(page, timeout_ms=200)
        assert isinstance(result, SolveResult)
        assert result.success is False
        assert result.code == 1

    @pytest.mark.asyncio
    async def test_get_result_timeout_still_minus_one(self):
        class SilentProvider(CaptchaProvider):
            name = "silent"

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

        provider = SilentProvider()
        provider._result = None
        provider._result_code = None
        provider._result_event = __import__("asyncio").Event()
        page = MagicMock()
        result = await provider.get_result(page, timeout_ms=50)
        assert result.success is False
        assert result.code == -1

    @pytest.mark.asyncio
    async def test_base_provider_find_gap_decodes_image_bytes(self):
        class ByteProvider(CaptchaProvider):
            name = "byte-provider"

            async def detect(self, page):
                return False

            async def locate_elements(self, page):
                pass

            async def extract_images(self, page, elements):
                pass

            async def perform_slide(self, page, elements, gap_x, trajectory, cdp_session=None):
                pass

            def validate_response(self, response):
                return None

        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 100:150] = 0
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)
        _, bg_bytes = cv2.imencode(".png", background)
        _, piece_bytes = cv2.imencode(".png", puzzle_piece)

        gap_x, confidence = await ByteProvider().find_gap(
            bg_bytes.tobytes(),
            piece_bytes.tobytes(),
        )

        assert gap_x is not None
        assert confidence >= 0.0

    @pytest.mark.asyncio
    async def test_builtin_provider_listener_survives_until_result_wait(self):
        provider = AliyunNoCaptchaProvider()
        page = MagicMock()
        response_handlers = []

        def on(event_name, handler):
            assert event_name == "response"
            response_handlers.append(handler)

        def remove_listener(event_name, handler):
            assert event_name == "response"
            response_handlers.remove(handler)

        page.on.side_effect = on
        page.remove_listener.side_effect = remove_listener
        page.wait_for_timeout = AsyncMock()
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.mouse.down = AsyncMock()
        page.mouse.up = AsyncMock()
        page.context = MagicMock()
        page.context.cookies = AsyncMock(return_value=[])

        slider_btn = AsyncMock()
        slider_btn.bounding_box = AsyncMock(
            return_value={"x": 10, "y": 10, "width": 40, "height": 40}
        )
        elements = type("Elements", (), {"slider_btn": slider_btn})()

        await provider.perform_slide(page, elements, 10, [(0, 0, 10), (10, 0, 10)])
        assert len(response_handlers) == 1

        response = type("Response", (), {})()
        response.url = "https://example.com/_____tmd_____/slide"

        async def body():
            return b'{"code": 0}'

        response.body = body
        response_handlers[0](response)

        result = await provider.get_result(page, timeout_ms=500)
        await provider.cleanup_after_result(page)

        assert result.success is True
        assert response_handlers == []
