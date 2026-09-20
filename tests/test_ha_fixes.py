"""Regression tests for 0.6.1 high-availability root-cause fixes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from slidex._cookies import cookie_domain_for_url, parse_cookie_header, select_cookies_for_url
from slidex._sanitize import sanitize_pure_user_id
from slidex._slide_geometry import clamp_travel, points_from_recorded, scale_recorded_points
from slidex.config import SlidexConfig
from slidex.providers.aliyun import AliyunNoCaptchaProvider
from slidex.providers.geetest import GeeTestProvider
from slidex.solver import SliderSolver
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)


class TestCookieDomainAndSnapshot:
    def test_cookie_domain_from_verify_url(self):
        assert cookie_domain_for_url("https://h5api.m.goofish.com/punish") == ".goofish.com"
        assert cookie_domain_for_url("https://login.taobao.com/member/login.jhtml") == ".taobao.com"
        assert cookie_domain_for_url("https://passport.example.com.cn/x") == ".example.com.cn"
        assert cookie_domain_for_url("") == ".goofish.com"

    def test_parse_cookie_header_uses_target_domain(self):
        cookies = parse_cookie_header("a=1; b=2", ".taobao.com")
        assert {c["name"]: c["domain"] for c in cookies} == {
            "a": ".taobao.com",
            "b": ".taobao.com",
        }

    def test_select_cookies_prefers_matching_host_and_specificity(self):
        jar = [
            {"name": "session", "value": "other", "domain": ".example.com", "path": "/"},
            {"name": "session", "value": "goofish", "domain": ".goofish.com", "path": "/"},
            {"name": "session", "value": "api", "domain": "h5api.m.goofish.com", "path": "/"},
            {"name": "x5sec", "value": "ticket", "domain": ".goofish.com", "path": "/"},
        ]
        selected = select_cookies_for_url(
            jar, "https://h5api.m.goofish.com/h5/api/_____tmd_____/punish"
        )
        assert selected["session"] == "api"
        assert selected["x5sec"] == "ticket"
        assert "other" not in selected.values()

    def test_select_cookies_without_url_does_not_last_write_wins(self):
        jar = [
            {"name": "session", "value": "short", "domain": ".com", "path": "/"},
            {"name": "session", "value": "specific", "domain": "h5api.m.goofish.com", "path": "/im"},
        ]
        selected = select_cookies_for_url(jar, "")
        assert selected["session"] == "specific"

    @pytest.mark.asyncio
    async def test_inject_cookies_uses_verify_url_domain(self):
        solver = SliderSolver(cookies_str="sid=abc")
        solver._verify_url = "https://login.taobao.com/member/login.jhtml"
        added = []

        class FakeContext:
            async def add_cookies(self, cookies):
                added.extend(cookies)

        solver.context = FakeContext()
        await solver._inject_cookies()
        assert added[0]["domain"] == ".taobao.com"
        assert added[0]["name"] == "sid"

    @pytest.mark.asyncio
    async def test_get_cookies_filters_by_verify_url(self):
        class FakeContext:
            async def cookies(self):
                return [
                    {"name": "session", "value": "wrong", "domain": ".example.com", "path": "/"},
                    {"name": "session", "value": "right", "domain": ".goofish.com", "path": "/"},
                ]

        solver = SliderSolver()
        solver.context = FakeContext()
        solver._verify_url = "https://www.goofish.com/im"
        assert await solver._get_cookies() == {"session": "right"}


class TestSlideGeometry:
    def test_clamp_travel_uses_js_as_max_not_gap(self):
        assert clamp_travel(180, 120) == 120
        assert clamp_travel(80, 120) == 80
        assert clamp_travel(80, None) == 80
        assert clamp_travel(0, 120) == 0

    def test_scale_recorded_points_when_distance_differs(self):
        points = [[0, 0, 10], [100, 2, 20], [200, 0, 10]]
        scaled = scale_recorded_points(points, 200, 100)
        assert scaled[1][0] == pytest.approx(50)
        assert scaled[1][1] == pytest.approx(2)
        assert scaled[2][0] == pytest.approx(100)

    def test_points_from_recorded_within_tolerance_unscaled(self):
        recorded = {"points": [[0, 0, 10], [105, 1, 10]], "distance": 100}
        points = points_from_recorded(recorded, 100)
        assert points[1][0] == pytest.approx(105)


class TestSanitizeReservedDeviceNames:
    def test_reserved_stem_before_extension(self):
        assert sanitize_pure_user_id("CON") == "CON_"
        assert sanitize_pure_user_id("CON.txt") == "CON.txt_"
        assert sanitize_pure_user_id("com1.log") == "com1.log_"
        assert sanitize_pure_user_id("console.txt") == "console.txt"
        assert sanitize_pure_user_id("COM10") == "COM10"


class TestProfileLockBoundedAcquire:
    @pytest.mark.asyncio
    async def test_acquire_times_out_instead_of_unbounded_wait(self):
        solver = SliderSolver(config=SlidexConfig(wait_timeout=1, telemetry_enabled=False))
        lock = solver._get_profile_lock("ha-lock-test")
        await lock.acquire()
        try:
            started = asyncio.get_running_loop().time()
            ok = await solver._acquire_profile_lock("ha-lock-test")
            elapsed = asyncio.get_running_loop().time() - started
        finally:
            lock.release()
        assert ok is False
        assert elapsed < 2.5


class TestAliyunProviderHa:
    @pytest.mark.asyncio
    async def test_validate_success_false_wins_over_code_zero(self):
        response = type("Response", (), {})()
        response.url = "https://example.com/_____tmd_____/slide"

        async def body():
            return b'{"success": false, "code": 0}'

        response.body = body
        assert await AliyunNoCaptchaProvider().validate_response(response) is False

    @pytest.mark.asyncio
    async def test_detect_iframe_without_content_frame_is_not_adapted(self):
        iframe = AsyncMock()
        iframe.get_attribute = AsyncMock(return_value="https://cdn.aliyuncs.com/nocaptcha")
        iframe.content_frame = AsyncMock(return_value=None)
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.query_selector_all = AsyncMock(return_value=[iframe])
        page.evaluate = AsyncMock(return_value=False)

        assert await AliyunNoCaptchaProvider().detect(page) is False

    @pytest.mark.asyncio
    async def test_detect_wrapper_inside_iframe(self):
        frame = AsyncMock()
        frame.query_selector = AsyncMock(return_value=object())
        frame.evaluate = AsyncMock(return_value=False)
        iframe = AsyncMock()
        iframe.content_frame = AsyncMock(return_value=frame)
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.evaluate = AsyncMock(return_value=False)
        page.query_selector_all = AsyncMock(return_value=[iframe])

        provider = AliyunNoCaptchaProvider()
        assert await provider.detect(page) is True
        assert provider._challenge_scope is frame


class TestGeeTestFrameScope:
    @pytest.mark.asyncio
    async def test_locate_uses_challenge_scope_not_main_page(self):
        scope = MagicMock()
        btn = object()
        track = MagicMock()
        track.bounding_box = AsyncMock(return_value={"width": 280})
        scope.wait_for_selector = AsyncMock(return_value=btn)
        scope.query_selector = AsyncMock(side_effect=[track, None, None])
        page = MagicMock()
        page.wait_for_selector = AsyncMock(side_effect=AssertionError("must not query main page"))
        page.query_selector = AsyncMock(side_effect=AssertionError("must not query main page"))

        provider = GeeTestProvider()
        provider._challenge_scope = scope
        elements = await provider.locate_elements(page)
        assert elements.slider_btn is btn
        assert elements.track_width_px == 280


class TestDistanceJsIsClampOnly:
    @pytest.mark.asyncio
    async def test_image_gap_is_used_and_js_only_clamps(self):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        solver._calc_distance_js = AsyncMock(return_value=120.0)
        solver._calc_distance = AsyncMock(return_value=180.0)
        travel = await solver._calc_distance_multi_source()
        assert travel == 120.0

    @pytest.mark.asyncio
    async def test_image_gap_below_max_travel_is_kept(self):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        solver._calc_distance_js = AsyncMock(return_value=200.0)
        solver._calc_distance = AsyncMock(return_value=132.0)
        travel = await solver._calc_distance_multi_source()
        assert travel == 132.0


class TestRemoteFallbackFinally:
    @pytest.mark.asyncio
    async def test_close_session_runs_after_poll_error_timeout(self, monkeypatch):
        from slidex.remote import captcha_controller

        solver = SliderSolver(
            cookie_id="ha_user",
            config=SlidexConfig(remote_captcha_timeout=0.05, remote_captcha_poll_interval=0.01),
        )
        solver.page = object()
        close = AsyncMock()
        monkeypatch.setattr(captcha_controller, "create_session", AsyncMock(return_value={"token": "t"}))
        monkeypatch.setattr(captcha_controller, "check_completion", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(captcha_controller, "close_session", close)
        success, cookies = await solver._fallback_to_remote("https://example.com/captcha")
        assert success is False
        assert cookies is None
        close.assert_awaited()


class TestTrajectoryHttp400:
    @pytest.mark.asyncio
    async def test_too_few_points_stays_400(self):
        from slidex.remote import captcha_controller
        from slidex import api

        captcha_controller.active_sessions.clear()
        captcha_controller.active_sessions["s1"] = {"token": "secret", "cookie_id": "u1"}
        request = api.TrajectorySubmitRequest(
            session_id="s1",
            cookie_id="u1",
            points=[[0, 0, 10]],
            distance=100,
        )
        with pytest.raises(HTTPException) as exc:
            await api.submit_trajectory(request, x_captcha_token="secret")
        assert exc.value.status_code == 400


class TestControlPageCache:
    @pytest.mark.asyncio
    async def test_session_control_page_is_not_cached(self):
        from slidex.remote import captcha_controller
        from slidex import api

        captcha_controller.active_sessions.clear()
        captcha_controller.control_tickets.clear()
        captcha_controller.active_sessions["s1"] = {"token": "secret"}
        ticket = captcha_controller.issue_control_ticket("s1")
        response = await api.captcha_control_page_with_session("s1", ticket=ticket)
        assert "no-store" in response.headers.get("cache-control", "").lower()


class TestVisionTimeout:
    @pytest.mark.asyncio
    async def test_slider_timeout_ms_returns_timeout_error(self):
        class SlowSlider:
            def __init__(self, **kwargs):
                self.closed = False

            async def solve_on_existing_page(self, cdp_endpoint, page_url=""):
                await asyncio.sleep(1)
                return True, {"session": "late"}

            def get_telemetry_summary(self):
                return {"run_id": "r", "status": "success"}

            def get_telemetry_dir(self):
                return "/tmp"

            async def close(self):
                self.closed = True

        solver = VisualChallengeSolver(slider_solver_factory=lambda **kw: SlowSlider())
        result = await solver.solve(
            VisualChallengeRequest(
                challenge_type=ChallengeType.SLIDER_CAPTCHA,
                context=VisionContext.CDP,
                cdp_endpoint="ws://localhost:9222/devtools/browser/1",
                timeout_ms=50,
            )
        )
        assert result.success is False
        assert result.error_code == "timeout"
        assert result.retryable is True


class TestCloseKillsProcessTree:
    @pytest.mark.asyncio
    async def test_close_kills_tree_when_context_hangs(self, monkeypatch):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        solver.CLOSE_TIMEOUT_S = 0.05
        solver._browser_pid = 4242

        class HangContext:
            async def close(self):
                await asyncio.sleep(5)

        solver.context = HangContext()
        killed = []

        def fake_kill(pid):
            killed.append(pid)
            return 1

        monkeypatch.setattr("slidex.solver.kill_chromium_process_tree", fake_kill)
        monkeypatch.setattr("slidex.solver.ensure_profile_chromium_closed", AsyncMock(return_value=0))
        await solver._close()
        assert killed == [4242]
        assert solver.context is None
