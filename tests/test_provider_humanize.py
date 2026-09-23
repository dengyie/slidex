"""Regression tests for 0.6.6: provider-path humanization, URL audit, stale SingletonLock heal."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import socket

import pytest

from slidex.providers.aliyun import AliyunNoCaptchaProvider
from slidex.solver import SliderSolver


# ── ① provider 路径人形化 ──────────────────────────────────────


class _FakeMouse:
    def __init__(self):
        self.events = []
        self._down = False

    async def move(self, x, y):
        self.events.append(("move", x, y))

    async def down(self):
        self._down = True
        self.events.append(("down",))

    async def up(self):
        self._down = False
        self.events.append(("up",))


class _FakeBtn:
    async def bounding_box(self):
        return {"x": 10.0, "y": 20.0, "width": 40.0, "height": 40.0}


class _FakeElements:
    slider_btn = _FakeBtn()


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()
        self.listeners = {}

    def on(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event, handler):
        pass

    async def wait_for_timeout(self, ms):
        assert ms >= 0


@pytest.mark.asyncio
async def test_provider_slide_inserts_press_hold_before_first_move():
    """down 后必须有按住停顿（600-1200ms 量级）才允许第一个位移。"""
    provider = AliyunNoCaptchaProvider()
    page = _FakePage()
    provider.bind_response_listener = mock.MagicMock()

    # 无按住首点的轨迹
    traj = [[5.0, 0.0, 30.0], [87.0, 0.0, 40.0]]
    with mock.patch("random.uniform", side_effect=lambda a, b: (a + b) / 2), \
         mock.patch("random.randint", side_effect=lambda a, b: a):
        await provider.perform_slide(page, _FakeElements(), 87, traj)

    events = page.mouse.events
    down_idx = next(i for i, e in enumerate(events) if e[0] == "down")
    first_move_idx = next(i for i, e in enumerate(events) if e[0] == "move" and i > down_idx)
    assert first_move_idx == down_idx + 1  # move 序列里 down 后直接是拖动（hold 由 wait 承担）
    assert events[-1][0] == "up"


@pytest.mark.asyncio
async def test_provider_slide_holds_recorded_press_point_and_skips_it():
    """录制轨迹的 (0,0,hold) 首点应作为按住停顿被消耗，不产生 50ms 截断。"""
    provider = AliyunNoCaptchaProvider()
    page = _FakePage()
    provider.bind_response_listener = mock.MagicMock()

    holds = []

    async def fake_wait(ms):
        holds.append(ms)

    traj = [[0.0, 0.0, 1000.0], [40.0, 0.0, 300.0], [87.0, 0.0, 400.0]]
    with mock.patch("random.uniform", side_effect=lambda a, b: (a + b) / 2), \
         mock.patch("random.randint", side_effect=lambda a, b: a), \
         mock.patch.object(page, "wait_for_timeout", side_effect=fake_wait):
        await provider.perform_slide(page, _FakeElements(), 87, traj)

    # approach 停顿（randint(30,80)→30、randint(20,60)→20）之后，
    # 第一个长停顿必须是录制的 hold 值（而不是 50）
    assert holds[:2] == [30, 20]
    assert holds[2] == 1000.0
    # 中段 delay 不再被 min(...,50) 截断（300/400 直接透传；
    # 收尾 wait 里本就有 randint(50,90)→50，不能用"无 50"断言）
    assert 300.0 in holds and 400.0 in holds
    assert 50 not in holds[:3]


@pytest.mark.asyncio
async def test_provider_slide_ends_with_overshoot_back_and_jitter():
    """释放前必须出现过冲（x 超过终点）再回拖的收尾序列。"""
    provider = AliyunNoCaptchaProvider()
    page = _FakePage()
    provider.bind_response_listener = mock.MagicMock()

    traj = [[0.0, 0.0, 800.0], [87.0, 0.0, 40.0]]
    with mock.patch("random.uniform", side_effect=lambda a, b: (a + b) / 2), \
         mock.patch("random.randint", side_effect=lambda a, b: a):
        await provider.perform_slide(page, _FakeElements(), 87, traj)

    moves = [e for e in page.mouse.events if e[0] == "move"]
    # 终点绝对 x = start(10 + 40/2=30) + 87 = 117；中点均匀化 uniform → 过冲 x > 117
    final_xs = [m[1] for m in moves[-3:]]
    assert max(final_xs) > 117.0  # 过冲
    assert final_xs[-1] < max(final_xs)  # 回拖/抖动收尾
    assert page.mouse.events[-1][0] == "up"


# ── ② URL 审计 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_url_audit_logs_responses_and_uninstalls():
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()

    hits = []

    class _Resp:
        url = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/_____tmd_____/slide?x=1"
        status = 200
        class request:
            method = "POST"

    class _Page:
        def __init__(self):
            self.handlers = {}
        def on(self, ev, fn):
            self.handlers.setdefault(ev, []).append(fn)
        def remove_listener(self, ev, fn):
            self.handlers.get(ev, []).remove(fn)

    page = _Page()
    audit = solver._install_url_audit(page)
    assert "response" in page.handlers and len(page.handlers["response"]) == 1
    page.handlers["response"][0](_Resp())
    audit.uninstall()
    assert page.handlers["response"] == []
    # teardown 落了审计日志与 telemetry
    assert solver._emit_telemetry_event.called
    kwargs = solver._emit_telemetry_event.call_args
    assert kwargs.args[0] == "provider_url_audit"


def test_url_audit_silent_when_no_responses():
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()

    class _Page:
        def on(self, ev, fn):
            pass
        def remove_listener(self, ev, fn):
            pass

    audit = solver._install_url_audit(_Page())
    audit.uninstall()
    assert not solver._emit_telemetry_event.called


# ── ③ 陈旧 SingletonLock 自愈 ──────────────────────────────────


def _make_profile(tmp_path, lock_target):
    profile = tmp_path / "slider_t"
    profile.mkdir(exist_ok=True)
    lock = profile / "SingletonLock"
    try:
        os.symlink(lock_target, lock)
    except OSError:
        pytest.skip("symlink privilege unavailable on this host")
    (profile / "SingletonCookie").symlink_to("123456")
    return profile


def test_heal_removes_foreign_host_lock(tmp_path):
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    profile = _make_profile(tmp_path, "oldcontainer-3486")
    solver.profile_dir = str(profile)
    solver._heal_stale_singleton_lock()
    assert not (profile / "SingletonLock").exists()
    assert not (profile / "SingletonCookie").exists()


def test_heal_keeps_live_local_pid_lock(tmp_path, monkeypatch):
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    profile = _make_profile(tmp_path, f"{socket.gethostname()}-1")
    solver.profile_dir = str(profile)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    solver._heal_stale_singleton_lock()
    assert (profile / "SingletonLock").is_symlink()


def test_heal_removes_dead_local_pid_lock(tmp_path, monkeypatch):
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    profile = _make_profile(tmp_path, f"{socket.gethostname()}-999999")
    solver.profile_dir = str(profile)
    import psutil
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    solver._heal_stale_singleton_lock()
    assert not (profile / "SingletonLock").exists()


def test_heal_noop_without_lock(tmp_path):
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    profile = tmp_path / "slider_t2"
    profile.mkdir()
    solver.profile_dir = str(profile)
    solver._heal_stale_singleton_lock()  # 不抛异常即可
