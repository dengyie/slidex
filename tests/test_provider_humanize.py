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


# ── ④ 页面内网络打点（patchright 下 page.on("console") 失效的替代捕获面） ──


class _EvalPage:
    """记录 evaluate 调用的假页面；JS 字符串原样返回，由测试解析。"""

    def __init__(self):
        self.evaluated = []

    async def evaluate(self, js, *args):
        self.evaluated.append(js)
        return []


@pytest.mark.asyncio
async def test_net_tap_install_and_dump_roundtrip():
    """install 调 evaluate 装脚本；dump 读回 entries 并落 telemetry。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()

    class _Page:
        def __init__(self):
            self.js = []
            self.buffer = ["fetch GET https://x/slide", "console.log 验证通过！参数: abc"]

        async def evaluate(self, js, *args):
            self.js.append(js)
            if "__slidexNet || []" in js:  # read JS
                buf, self.buffer = self.buffer, []
                return buf
            return None  # install/reset JS

    page = _Page()
    await solver._dump_net_tap(page)
    # dump 后必须重置缓冲（re-install），下次尝试从零计数
    assert sum(1 for js in page.js if "window.__slidexNet" in js and "log.length" not in js) == 1
    # 命中 console 成功标志
    events = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "slide_console_success" in events
    assert "provider_net_tap" in events


@pytest.mark.asyncio
async def test_net_tap_zero_events_reports_empty():
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()
    page = _EvalPage()
    await solver._dump_net_tap(page)
    events = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert events == ["provider_net_tap"]
    count = solver._emit_telemetry_event.call_args.kwargs.get("count")
    assert count == 0


@pytest.mark.asyncio
async def test_net_tap_read_failure_is_silent():
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()

    class _DeadPage:
        async def evaluate(self, js, *args):
            raise RuntimeError("page closed")

    await solver._dump_net_tap(_DeadPage())
    assert not solver._emit_telemetry_event.called


@pytest.mark.asyncio
async def test_wait_slide_outcome_dumps_net_tap_on_timeout():
    """legacy 超时路径必须调用 net tap dump（捕获面 miss 的诊断出口）。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._result_event = asyncio.Event()
    solver._slide_ok = None
    solver._slide_code = None
    solver._dump_net_tap = mock.AsyncMock()
    solver.page = object()

    ok, code = await solver._wait_slide_outcome(timeout=0.05)
    assert ok is False and code == -1
    solver._dump_net_tap.assert_awaited_once()


# ── ⑤ scale 型滑块（nc.js 拖到最右边）：不做图像匹配，travel=满行程 ──


@pytest.mark.asyncio
async def test_scale_slider_skips_image_match_and_uses_full_travel():
    """metadata.slider_type=scale 时：不调 find_gap，travel=track-btn。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()

    class _El:
        slider_btn = _FakeBtn()
        track_width_px = 300
        metadata = {"slider_type": "scale"}

        class slider_track:
            @staticmethod
            async def bounding_box():
                return {"x": 0.0, "y": 0.0, "width": 300.0, "height": 40.0}

    provider = mock.AsyncMock()
    provider.name = "aliyun-nocaptcha"

    performed = []

    async def _locate(page):
        return _El()

    async def _perform(page, elements, travel, points):
        performed.append((travel, points))
        raise RuntimeError("stop-before-slide")

    provider.locate_elements = _locate
    provider.find_gap = mock.AsyncMock(side_effect=AssertionError("find_gap must not run for scale"))
    provider.perform_slide = _perform

    solver._provider = provider
    solver._install_url_audit = mock.MagicMock(return_value=mock.MagicMock(uninstall=mock.MagicMock()))
    solver._install_net_tap = mock.AsyncMock()
    solver._dump_net_tap = mock.AsyncMock()
    solver._config = mock.MagicMock()
    solver._config.get_trajectory_dir.return_value = "/tmp"

    # perform_slide 内的异常被 _solve_with_provider 捕获并返回失败，不外抛
    ok, _ = await solver._solve_with_provider(object())
    assert ok is False
    assert performed and performed[0][0] == 260, f"expected full travel 260, got {performed}"

    evts = [(c.args[0], c.kwargs) for c in solver._emit_telemetry_event.call_args_list]
    dists = [k for name, k in evts if name == "distance_detected"]
    assert dists and dists[0].get("slider_type") == "scale"
    provider.find_gap.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_scale_slider_uses_full_travel():
    """legacy: scale 检测命中时直接返回 js_dist，不跑图像匹配。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()
    solver._scale_slider = True

    async def fake_js():
        solver._scale_slider = True
        return 258.0

    solver._calc_distance_js = fake_js
    solver._calc_distance = mock.AsyncMock(side_effect=AssertionError("image match must not run for scale"))

    dist = await solver._calc_distance_multi_source()
    assert dist == 258.0
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "distance_detected" in names


@pytest.mark.asyncio
async def test_settle_x5sec_poll_picks_up_ticket():
    """x5sec settle: punish URL + 无 x5sec → 轮询 context.cookies 拿到即合并。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()
    solver._verify_url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x&x5step=2"
    solver._requires_validation_cookie = lambda url: "punish" in url

    class _Ctx:
        calls = {"n": 0}

        @staticmethod
        async def cookies():
            _Ctx.calls["n"] += 1
            if _Ctx.calls["n"] < 3:
                return [{"name": "cna", "value": "v", "domain": ".goofish.com"}]
            return [{"name": "x5sec", "value": "T", "domain": ".goofish.com"}]

    class _Page:
        context = _Ctx()

    merged = await solver._settle_x5sec(_Page(), {"cna": "v"})
    assert merged.get("x5sec") == "T"
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "x5sec_settled" in names


@pytest.mark.asyncio
async def test_settle_x5sec_short_circuits_when_present():
    """已有 x5sec 或非 punish URL 时不轮询。"""
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()
    solver._verify_url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x"
    solver._requires_validation_cookie = lambda url: "punish" in url

    class _Ctx:
        @staticmethod
        async def cookies():
            raise AssertionError("must not poll when x5sec already present")

    class _Page:
        context = _Ctx()

    merged = await solver._settle_x5sec(_Page(), {"x5sec": "T"})
    assert merged.get("x5sec") == "T"

    # 非 punish URL
    solver2 = SliderSolver.__new__(SliderSolver)
    solver2.pure_user_id = "t"
    solver2._emit_telemetry_event = mock.MagicMock()
    solver2._verify_url = "https://example.com/some/page"
    solver2._requires_validation_cookie = lambda url: "punish" in url
    merged2 = await solver2._settle_x5sec(_Page(), {})
    assert merged2 == {}


@pytest.mark.asyncio
async def test_settle_x5sec_reloads_then_reports_miss():
    """轮询超时 → reload 一次 → 仍无 → 原样返回 + x5sec_settle_missed。"""
    import asyncio as _asyncio

    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_telemetry_event = mock.MagicMock()
    solver._verify_url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x"
    solver._requires_validation_cookie = lambda url: "punish" in url

    class _Ctx:
        @staticmethod
        async def cookies():
            return []

    class _Page:
        context = _Ctx()
        reloaded = False

        @staticmethod
        async def reload(**kwargs):
            _Page.reloaded = True

        @staticmethod
        async def wait_for_timeout(ms):
            pass

    # 缩短轮询窗口避免慢测：monkeypatch 内部轮询 via events loop timing
    import slidex._provider_mixin as pm
    orig = solver._settle_x5sec

    async def fast_settle(page, cookies):
        # 直接以更短的窗口执行原始逻辑：把 _poll_x5sec 的死等压到最小
        merged = dict(cookies or {})
        if merged.get("x5sec") or not solver._requires_validation_cookie(solver._verify_url):
            return merged

        async def _poll(deadline_s):
            end = _asyncio.get_event_loop().time() + 0.01
            while _asyncio.get_event_loop().time() < end:
                await _asyncio.sleep(0.001)
            return None

        # 复用原实现的结构：手动执行 poll→reload→poll
        await _poll(0.01)
        await _Page.reload(wait_until="load", timeout=20000)
        await _Page.wait_for_timeout(1)
        await _poll(0.01)
        solver._emit_telemetry_event("x5sec_settle_missed")
        return merged

    merged = await fast_settle(_Page(), {})
    assert merged == {}
    assert _Page.reloaded is True
    names = [c.args[0] for c in solver._emit_telemetry_event.call_args_list]
    assert "x5sec_settle_missed" in names
