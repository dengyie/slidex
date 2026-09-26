"""0.6.24: 拖动事件流水线派发 — 顺序、节奏还原与回退语义。

背景（用户实测）：CDP 真机模式下拖动很卡顿、不连贯。根因 = 顺序执行路径
每个事件一次完整隧道往返（30-150ms/次），设计好的 600ms 平滑时间线被
RTT 撕成 2-6 秒台阶。流水线派发按设计间隔 fire-and-forget，事件节奏与
RTT 解耦——本文件用带延迟的假会话钉住这一性质。
"""
import asyncio
import time

import pytest

from slidex._drag import build_drag_events, dispatch_drag_timeline


def _make_timeline_points():
    # 首点 = 按下后按住停顿 800ms，其后 8 个位移点、设计间隔 30ms
    return [(100.0, 50.0, 800.0)] + [(100.0 + 25.0 * i, 50.0 + i, 30.0) for i in range(1, 9)]


def test_build_drag_events_order_and_buttons():
    timeline = build_drag_events(100.0, 50.0, _make_timeline_points(), extra_overshoot=True)
    types = [p["type"] for _, p in timeline]
    assert types[0] == "mouseMoved"           # 接近移动
    assert "mousePressed" in types
    assert types[-1] == "mouseReleased"
    pressed_idx = types.index("mousePressed")
    for _, p in timeline[pressed_idx + 1:-1]:
        assert p["buttons"] == 1              # 拖拽期间 mousemove 携带按住态
    assert timeline[-1][1]["buttons"] == 0    # 松键
    # 末端握持（0.6.18 真人要领）：release 前的 gap 在 0.45-1.10s 量级
    assert timeline[-1][0] >= 300.0
    # 过冲/回拖/回中三步存在
    assert types.count("mouseMoved") >= 8 + 3 + 2


def test_build_drag_events_no_double_overshoot():
    t_with = build_drag_events(0.0, 0.0, _make_timeline_points(), extra_overshoot=True)
    t_without = build_drag_events(0.0, 0.0, _make_timeline_points(), extra_overshoot=False)
    # legacy 轨迹已烘焙过冲点序，extra_overshoot=False 不再追加三步
    assert len(t_with) == len(t_without) + 3


@pytest.mark.asyncio
async def test_dispatch_pipeline_keeps_designed_rhythm_under_latency():
    """核心性质：会话每次 send 带 60ms 模拟 RTT 时，事件间隔仍 ≈ 设计间隔、
    总时长 ≈ 设计时间线 + 一次 RTT。顺序 await 到底会多出 N×60ms（必炸）。"""
    sent_at = []
    sent_types = []

    class _SlowSession:
        async def send(self, method, params=None):
            await asyncio.sleep(0.06)         # 模拟隧道往返
            sent_at.append(time.monotonic())
            sent_types.append(params["type"])

    timeline = build_drag_events(100.0, 50.0, _make_timeline_points(), extra_overshoot=False)
    designed = [g for g, _ in timeline]
    designed_total = sum(designed) / 1000.0

    t0 = time.monotonic()
    await dispatch_drag_timeline(_SlowSession(), timeline)
    wall = time.monotonic() - t0

    assert wall < designed_total + 0.5        # 顺序实现 ≈ designed_total + 10×0.06+，此处必炸
    assert len(sent_at) == len(timeline)
    for i in range(1, len(sent_at)):
        gap = sent_at[i] - sent_at[i - 1]
        assert abs(gap - designed[i] / 1000.0) < 0.12
    assert sent_types[0] == "mouseMoved"
    assert sent_types[-1] == "mouseReleased"


@pytest.mark.asyncio
async def test_slide_playwright_uses_pipeline_when_cdp_available():
    """legacy 路径：CDP 会话在 → 走流水线且不触碰 page.mouse（容器 patchright
    无会话时才走顺序路径）。"""
    from slidex.solver import SliderSolver

    calls = []

    class _Session:
        async def send(self, method, params=None):
            calls.append(params["type"])

    class _NoMouse:
        def __getattr__(self, name):
            raise AssertionError(f"page.mouse.{name} touched under pipeline mode")

    s = SliderSolver.__new__(SliderSolver)
    s.pure_user_id = "t"
    s._cdp = _Session()
    s.page = _NoMouse()

    await s._slide_playwright(258.0, 1, None, 100.0, 50.0)

    assert calls, "pipeline dispatched nothing"
    assert calls[-1] == "mouseReleased"
    assert "mousePressed" in calls
