"""0.6.14: solve 全程硬看门狗。

生产（1GB 内存 VPS）中页面渲染失败后 solve 在死驱动连接上挂死 16h+，token
刷新任务随之整体卡死。看门狗超预算必须：cancel/遗弃求解任务、OS 级强杀
浏览器进程树、释放 profile 锁、返回 (False, None) 让编排器降级。
"""
import asyncio
from pathlib import Path
from unittest import mock

import pytest

from slidex.solver import SliderSolver
from slidex import solver as solver_module


def _make_solver() -> SliderSolver:
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver.profile_dir = Path("/tmp/slidex-test-profile")
    solver.SOLVE_WATCHDOG_TIMEOUT_S = 0.05
    solver.SCREENSHOT_BUDGET_S = 0.05
    solver._emit_step = mock.MagicMock()
    solver._emit_telemetry_event = mock.MagicMock()
    solver._finalize_telemetry = mock.MagicMock()
    solver._release_profile_lock = mock.MagicMock()
    return solver


@pytest.mark.asyncio
async def test_solve_watchdog_fires_and_hard_kills():
    solver = _make_solver()
    killed = {"called": False}

    async def _hung_impl(verify_url):
        await asyncio.sleep(5)
        return True, {}

    async def _fake_kill():
        killed["called"] = True

    solver._solve_impl = _hung_impl
    solver._hard_kill_browser = _fake_kill

    ok, cookies = await solver.solve("https://x/punish?x5secdata=1")
    assert (ok, cookies) == (False, None)
    assert killed["called"] is True
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "solve_watchdog_fired" in names
    assert solver._finalize_telemetry.call_args.kwargs.get("status") == "watchdog_timeout"
    solver._release_profile_lock.assert_called_once()


@pytest.mark.asyncio
async def test_solve_passes_through_impl_result():
    solver = _make_solver()
    solver._solve_impl = mock.AsyncMock(return_value=(True, {"x5sec": "1"}))
    solver._hard_kill_browser = mock.AsyncMock()

    ok, cookies = await solver.solve("https://x/punish?x5secdata=1")
    assert ok is True
    assert cookies == {"x5sec": "1"}
    solver._hard_kill_browser.assert_not_awaited()
    solver._finalize_telemetry.assert_not_called()


@pytest.mark.asyncio
async def test_cdp_solve_watchdog_detaches_without_killing():
    solver = _make_solver()

    async def _hung(cdp, page_url):
        await asyncio.sleep(5)
        return True, {}

    solver._solve_on_existing_impl = mock.AsyncMock(side_effect=_hung)
    solver._close_cdp_only = mock.AsyncMock()
    solver._hard_kill_browser = mock.AsyncMock()

    ok, cookies = await solver.solve_on_existing_page("ws://localhost:9222/x", "")
    assert (ok, cookies) == (False, None)
    solver._close_cdp_only.assert_awaited_once()
    solver._hard_kill_browser.assert_not_awaited()
    assert solver._finalize_telemetry.call_args.kwargs.get("status") == "watchdog_timeout"


@pytest.mark.asyncio
async def test_hard_kill_browser_stops_driver_and_kills_pids():
    solver = _make_solver()
    solver._playwright = mock.MagicMock()
    solver._browser_pid = 333
    solver.context = mock.MagicMock()

    found = [111, 222]
    killed = []
    monkey_found = mock.patch.object(solver_module, "find_chromium_pids_by_user_data_dir", return_value=found)
    monkey_kill = mock.patch.object(solver_module, "kill_chromium_process_tree", side_effect=lambda pid: killed.append(pid))
    with monkey_found, monkey_kill:
        await solver._hard_kill_browser()

    assert sorted(killed) == [111, 222, 333]
    assert solver._browser_pid is None
    assert solver._playwright is None
    assert solver.context is None
    solver._playwright  # driver stop was budgeted, not left dangling


@pytest.mark.asyncio
async def test_save_debug_screenshot_budget_abandons_hung_shot(tmp_path):
    solver = _make_solver()
    solver._config = mock.MagicMock()
    solver._config.get_debug_screenshot_dir.return_value = str(tmp_path)
    solver.page = mock.MagicMock()
    solver.page.screenshot = mock.AsyncMock(side_effect=asyncio.sleep(30))

    await asyncio.wait_for(solver._save_debug_screenshot("tag"), timeout=5)
    solver.page.screenshot.assert_awaited_once()
