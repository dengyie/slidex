"""0.6.16: 手动通过收割 — bx-x5sec 票据不得随失败路径丢弃。

CDP 模式下真人在外部浏览器拖过滑块时，checkCookie 链在页面层走通、
bx-x5sec 票据被 _on_response 捕获；但 provider 结果等待器只认自己流水线
的完成信号（生产日志：timeout waiting for result），legacy 循环又只见
"滑块已消失"，最后 _fallback_or_fail 把已捕获的票据当失败丢弃
（2026-09-25 生产实测，账号 1926782908）。失败出口必须先尝试结算票据，
x5sec 落地才返回成功；同时每个 solve 入口重置 _bx_voucher，防止上一轮
残留票据造成跨轮误判。
"""
from unittest import mock

import pytest

from slidex.solver import SliderSolver


def _make_solver() -> SliderSolver:
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_step = mock.MagicMock()
    solver._emit_telemetry_event = mock.MagicMock()
    solver._finalize_telemetry = mock.MagicMock()
    solver._is_cdp_mode = True
    solver._bx_voucher = None
    solver.trajectory_mode = "auto"
    solver.page = None
    return solver


@pytest.mark.asyncio
async def test_fallback_or_fail_harvests_captured_voucher():
    solver = _make_solver()
    solver._bx_voucher = "x5sec=abc_def; Path=/;"

    async def _fake_cookies():
        return {"unb": "1"}

    async def _fake_settle(page, cookies):
        return {"unb": "1", "x5sec": "abc_def"}

    solver._get_cookies = _fake_cookies
    solver._settle_x5sec = _fake_settle

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert ok is True
    assert cookies == {"unb": "1", "x5sec": "abc_def"}
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "manual_voucher_harvested" in names
    assert "fallback_skipped" not in names


@pytest.mark.asyncio
async def test_fallback_or_fail_without_voucher_cdp_returns_failure():
    solver = _make_solver()
    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "manual_voucher_harvested" not in names


@pytest.mark.asyncio
async def test_fallback_or_fail_voucher_without_x5sec_still_fails():
    solver = _make_solver()
    solver._bx_voucher = "other=1; Path=/;"

    async def _fake_cookies():
        return {"unb": "1"}

    async def _fake_settle(page, cookies):
        return {"unb": "1"}

    solver._get_cookies = _fake_cookies
    solver._settle_x5sec = _fake_settle

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)


@pytest.mark.asyncio
async def test_fallback_or_fail_settle_exception_returns_failure():
    solver = _make_solver()
    solver._bx_voucher = "x5sec=abc; Path=/;"

    async def _boom():
        raise RuntimeError("context closed")

    solver._get_cookies = _boom

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)


@pytest.mark.asyncio
async def test_solve_on_existing_impl_resets_stale_voucher():
    solver = _make_solver()
    solver._bx_voucher = "x5sec=stale; Path=/;"
    seen = {}

    async def _fake_connect(endpoint, page_url):
        return None

    async def _fake_loop(verify_url):
        seen["voucher"] = solver._bx_voucher
        return True, {"x5sec": "1"}

    async def _fake_close():
        return None

    solver._connect_existing_browser = _fake_connect
    solver._run_solve_loop = _fake_loop
    solver._close_cdp_only = _fake_close

    ok, cookies = await solver._solve_on_existing_impl("http://127.0.0.1:9222", "")
    assert (ok, cookies) == (True, {"x5sec": "1"})
    assert seen["voucher"] is None
