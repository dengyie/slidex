"""Regression tests for 0.6.1 / 0.6.2 high-availability root-cause fixes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from slidex._async_budget import await_with_budget
from slidex._cookies import cookie_domain_for_url, parse_cookie_header, select_cookies_for_url
from slidex._sanitize import sanitize_pure_user_id
from slidex._slide_geometry import clamp_travel, points_from_recorded, scale_recorded_points
from slidex._slide_result import interpret_slide_json
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
from slidex.vision import solver as vision_solver


async def _ignore_cancel_sleep(seconds: float) -> None:
    """Playwright close/detach 同类：取消后仍继续跑完。"""
    end = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < end:
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            continue


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
            assert ok is False
            assert elapsed < 2.5
            assert solver._profile_lock_held is False
            assert lock.locked() is True
        finally:
            if lock.locked():
                lock.release()

    @pytest.mark.asyncio
    async def test_acquire_success_holds_lock(self):
        solver = SliderSolver(config=SlidexConfig(wait_timeout=1, telemetry_enabled=False))
        lock = solver._get_profile_lock("ha-lock-ok")
        ok = await solver._acquire_profile_lock("ha-lock-ok")
        try:
            assert ok is True
            assert solver._profile_lock_held is True
            assert lock.locked() is True
        finally:
            solver._release_profile_lock("ha-lock-ok")
        assert solver._profile_lock_held is False
        assert lock.locked() is False

    @pytest.mark.asyncio
    async def test_abandon_releases_lock_won_after_timeout_decision(self):
        """超时判定后 acquire 已完成：必须把锁交回去，不能占死 profile。"""
        solver = SliderSolver(config=SlidexConfig(wait_timeout=1, telemetry_enabled=False))
        lock = solver._get_profile_lock("ha-lock-race")
        task = asyncio.ensure_future(lock.acquire())
        await task
        assert lock.locked() is True
        await solver._abandon_lock_acquire(lock, task, acquired=False)
        assert solver._profile_lock_held is False
        assert lock.locked() is False

    @pytest.mark.asyncio
    async def test_release_does_not_drop_someone_elses_lock(self):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        lock = solver._get_profile_lock("ha-lock-other")
        await lock.acquire()
        try:
            solver._profile_lock_held = False
            solver._release_profile_lock("ha-lock-other")
            assert lock.locked() is True
        finally:
            lock.release()

    @pytest.mark.asyncio
    async def test_legacy_on_response_success_false_is_failure(self):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        response = type("Response", (), {})()
        response.url = "https://example.com/_____tmd_____/slide"

        async def body():
            return b'{"success": false, "code": 0}'

        response.body = body
        await solver._on_response(response)
        ok, code = await solver._wait_slide_outcome(0.1, success_code=0)
        assert ok is False
        assert code == 0


class TestAliyunProviderHa:
    @pytest.mark.asyncio
    async def test_validate_success_false_wins_over_code_zero(self):
        response = type("Response", (), {})()
        response.url = "https://example.com/_____tmd_____/slide"

        async def body():
            return b'{"success": false, "code": 0}'

        response.body = body
        assert await AliyunNoCaptchaProvider().validate_response(response) is False

    def test_interpret_slide_json_success_false_beats_code_zero(self):
        assert interpret_slide_json({"success": False, "code": 0}) is False
        assert interpret_slide_json({"success": "false", "code": 0}) is False
        assert interpret_slide_json({"success": " FALSE ", "code": 0}) is False
        assert interpret_slide_json({"code": 0}) is True
        assert interpret_slide_json({"success": True, "code": 1}) is True
        assert interpret_slide_json({"code": 1}, success_code=0) is False
        assert interpret_slide_json([]) is None
        assert interpret_slide_json("not-json-object") is None

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
            last = None

            def __init__(self, **kwargs):
                self.closed = False
                SlowSlider.last = self

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
        assert SlowSlider.last.closed is True

    @pytest.mark.asyncio
    async def test_slider_timeout_does_not_wait_out_hanging_close(self, monkeypatch):
        monkeypatch.setattr(vision_solver, "_VISION_CLOSE_BUDGET_S", 0.05)

        class HangCloseSlider:
            def __init__(self, **kwargs):
                pass

            async def solve_on_existing_page(self, cdp_endpoint, page_url=""):
                await asyncio.sleep(1)
                return True, {"session": "late"}

            def get_telemetry_summary(self):
                return {"run_id": "r", "status": "success"}

            def get_telemetry_dir(self):
                return "/tmp"

            async def close(self):
                await _ignore_cancel_sleep(1.2)

        started = asyncio.get_running_loop().time()
        solver = VisualChallengeSolver(slider_solver_factory=lambda **kw: HangCloseSlider())
        result = await solver.solve(
            VisualChallengeRequest(
                challenge_type=ChallengeType.SLIDER_CAPTCHA,
                context=VisionContext.CDP,
                cdp_endpoint="ws://localhost:9222/devtools/browser/1",
                timeout_ms=50,
            )
        )
        elapsed = asyncio.get_running_loop().time() - started
        assert result.error_code == "timeout"
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_ocr_executor_busy_returns_without_queueing(self, monkeypatch):
        monkeypatch.setattr(vision_solver, "_vision_slots", __import__("threading").BoundedSemaphore(0))
        solver = VisualChallengeSolver()
        result = await solver.solve(
            VisualChallengeRequest(
                challenge_type=ChallengeType.OCR_TEXT,
                context=VisionContext.IMAGE_BYTES,
                image_bytes=b"x",
                timeout_ms=80,
            )
        )
        assert result.success is False
        assert result.error_code == "executor_busy"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_ocr_timeout_ms_returns_timeout_error(self):
        import threading

        started = asyncio.get_running_loop().time()
        entered = threading.Event()
        release = threading.Event()

        class SlowOcr:
            def extract(self, **kwargs):
                entered.set()
                release.wait(timeout=5)
                return type(
                    "R",
                    (),
                    {
                        "text": "late",
                        "confidence": 1.0,
                        "provider": "slow",
                        "language": None,
                        "boxes": [],
                        "metadata": {},
                    },
                )()

        solver = VisualChallengeSolver(ocr_extractor=SlowOcr())
        try:
            result = await solver.solve(
                VisualChallengeRequest(
                    challenge_type=ChallengeType.OCR_TEXT,
                    context=VisionContext.IMAGE_BYTES,
                    image_bytes=b"not-an-image",
                    timeout_ms=80,
                )
            )
            elapsed = asyncio.get_running_loop().time() - started
            assert result.success is False
            assert result.error_code == "timeout"
            assert result.retryable is True
            assert elapsed < 1.0
            assert entered.wait(timeout=1)
        finally:
            release.set()


class TestAwaitBudget:
    @pytest.mark.asyncio
    async def test_budget_returns_without_waiting_out_uncancellable_work(self):
        started = asyncio.get_running_loop().time()
        result = await await_with_budget(_ignore_cancel_sleep(1.2), 0.05)
        elapsed = asyncio.get_running_loop().time() - started
        assert result is None
        assert elapsed < 1.0


class TestCloseKillsProcessTree:
    @pytest.mark.asyncio
    async def test_close_kills_tree_when_context_hangs(self, monkeypatch):
        solver = SliderSolver(config=SlidexConfig(telemetry_enabled=False))
        solver.CLOSE_TIMEOUT_S = 0.05
        solver._browser_pid = 4242

        class HangContext:
            async def close(self):
                await _ignore_cancel_sleep(1.2)

        solver.context = HangContext()
        killed = []

        def fake_kill(pid):
            killed.append(pid)
            return 1

        monkeypatch.setattr("slidex.solver.kill_chromium_process_tree", fake_kill)
        monkeypatch.setattr("slidex.solver.ensure_profile_chromium_closed", AsyncMock(return_value=0))
        started = asyncio.get_running_loop().time()
        await solver._close()
        elapsed = asyncio.get_running_loop().time() - started
        assert killed == [4242]
        assert solver.context is None
        assert elapsed < 1.0
