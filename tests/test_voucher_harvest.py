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


class _LivePage:
    def is_closed(self):
        return False


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
        seen["t0"] = getattr(solver, "_solve_t0", None)
        return True, {"x5sec": "1"}

    async def _fake_close():
        return None

    solver._connect_existing_browser = _fake_connect
    solver._run_solve_loop = _fake_loop
    solver._close_cdp_only = _fake_close

    ok, cookies = await solver._solve_on_existing_impl("http://127.0.0.1:9222", "")
    assert (ok, cookies) == (True, {"x5sec": "1"})
    assert seen["voucher"] is None
    assert seen["t0"] is not None  # 0.6.20: 入口必须记录起算时间供人工等待预算截断


@pytest.mark.asyncio
async def test_cdp_manual_wait_settles_voucher_when_user_drags():
    """0.6.17: 自动拖动耗尽后进入人工等待期，等待期间票据到达 → 收割成功。"""
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 5.0

    calls = {"n": 0}

    async def _fake_cookies():
        calls["n"] += 1
        # 第 2 次轮询时模拟用户拖过：票据头已被 _on_response 捕获
        if calls["n"] >= 2:
            solver._bx_voucher = "x5sec=manual_pass; Path=/;"
        return {"unb": "1"}

    async def _fake_settle(page, cookies):
        return {"unb": "1", "x5sec": "manual_pass"}

    solver._get_cookies = _fake_cookies
    solver._settle_x5sec = _fake_settle
    solver.page = _LivePage()

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert ok is True
    assert cookies == {"unb": "1", "x5sec": "manual_pass"}
    steps = [c.args[1:3] for c in solver._emit_step.call_args_list]
    assert ("manual_wait", "ok") in steps


@pytest.mark.asyncio
async def test_cdp_manual_wait_harvests_x5sec_from_jar():
    """人工拖过后 checkCookie 把 x5sec 写进 jar（无票据头）→ 轮询捕获。"""
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 5.0

    calls = {"n": 0}

    async def _fake_cookies():
        calls["n"] += 1
        if calls["n"] >= 2:
            return {"unb": "1", "x5sec": "jar_pass"}
        return {"unb": "1"}

    async def _fake_settle(page, cookies):
        raise AssertionError("settle should not be called without voucher")

    solver._get_cookies = _fake_cookies
    solver._settle_x5sec = _fake_settle
    solver.page = _LivePage()

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert ok is True
    assert cookies == {"unb": "1", "x5sec": "jar_pass"}


@pytest.mark.asyncio
async def test_cdp_manual_wait_expires_returns_failure():
    """等待期内用户始终没拖 → 超时返回失败，流程照旧降级。"""
    import time as _time
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 0.5

    async def _fake_cookies():
        return {"unb": "1"}

    solver._get_cookies = _fake_cookies
    solver.page = _LivePage()

    t0 = _time.monotonic()
    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)
    assert _time.monotonic() - t0 >= 0.4


@pytest.mark.asyncio
async def test_cdp_manual_wait_exits_when_page_closed():
    """用户中途关掉 Chrome → 立即退出等待，不再空转。"""
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 30.0

    async def _fake_cookies():
        return {"unb": "1"}

    class _ClosedPage:
        def is_closed(self):
            return True

    solver._get_cookies = _fake_cookies
    solver.page = _ClosedPage()

    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)


@pytest.mark.asyncio
async def test_cdp_manual_wait_capped_by_watchdog_budget():
    """0.6.20: 自动阶段吃掉看门狗预算后，人工等待按剩余时间截断，
    不能等到一半被看门狗 cancel（cancel 出口不结算票据，成果整体丢弃）。"""
    import time as _time
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 300.0
    solver.SOLVE_WATCHDOG_TIMEOUT_S = 600.0
    # 自动阶段已耗 590s：剩余 10s 再扣 15s 余量 → 等待应立即让位（0s）
    solver._solve_t0 = _time.monotonic() - 590.0

    async def _fake_cookies():
        return {"unb": "1"}

    solver._get_cookies = _fake_cookies
    solver.page = _LivePage()

    t0 = _time.monotonic()
    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)
    assert _time.monotonic() - t0 < 5.0  # 未按 300s 空等


@pytest.mark.asyncio
async def test_cdp_manual_wait_uncapped_when_budget_plenty():
    """自动阶段很快（典型 ~90s）时，人工等待仍是足额配置值（此处用 1.2s 验证不被截断逻辑波及）。"""
    import time as _time
    solver = _make_solver()
    solver.MANUAL_VOUCHER_WAIT_S = 1.2
    solver.SOLVE_WATCHDOG_TIMEOUT_S = 600.0
    solver._solve_t0 = _time.monotonic() - 90.0  # 剩余 495s > 1.2s，不截断

    async def _fake_cookies():
        return {"unb": "1"}

    solver._get_cookies = _fake_cookies
    solver.page = _LivePage()

    t0 = _time.monotonic()
    ok, cookies = await solver._fallback_or_fail("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)
    assert _time.monotonic() - t0 >= 1.0  # 足额等待，未被预算逻辑提前砍掉
