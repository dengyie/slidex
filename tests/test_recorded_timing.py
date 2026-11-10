"""0.10.2: 录制轨迹时序归一化 — 回放节奏与网络/录制墙钟解耦。

根因（线上实证 2026-11-10，1926782908）：CDP 真机模式里人是在远端画面里拖的，
DRAG_RECORDER_JS 记的是"按下到松开的世界时钟"。线上轨迹池 50 条 human 全部
13.8-17.3s、单点间隙最大 1.24s；回放侧只缩放几何、原样保留 delay，于是每次
自动拖动的时间线是 16.7 秒的走走停停（设计带 0.6-0.9s）——用户侧"轨迹很卡"。

性质（本文件钉住）：
  1. 几何（每点 x/y、终点、点序）逐字保留——录制最值钱的部分不动；
  2. 纯位移段回到设计带，单点间隙有上限；
  3. 已经够快的录制不被拖慢（只压缩，不放慢）；
  4. 幂等：归一化结果再归一化不再变化；
  5. ingest（human_events_to_points）、池加载（GestureSession）、派发口
     （_replay_recorded_cdp）三处都会归一化——存量脏文件不修也能自愈。
"""
import pytest

from slidex import _slide_geometry as geo
from slidex._drag import build_drag_events, human_events_to_points
from slidex._gestures import GestureSession
from slidex._trajectory_pool import SliderTrajectoryPool


def _slow_recording(n=29, total_ms=16113.4, distance=258.0):
    """复刻线上坏录制：n 个点、单点间隙近 1.2s（几何单调右移）。"""
    gap = total_ms / (n - 1)
    pts = []
    for i in range(n):
        x = round(distance * i / (n - 1), 2)
        y = round(1.0 if i % 5 == 0 else 0.0, 2)
        pts.append([x, y, gap if i else 0.0])
    return pts


def _timeline_total(points, extra_overshoot=False):
    timeline = build_drag_events(
        100.0, 100.0,
        [(100.0 + float(x), 100.0 + float(y), float(d or 0.0)) for x, y, d in points],
        extra_overshoot=extra_overshoot,
    )
    return sum(g for g, _ in timeline)


def test_normalize_compresses_slow_recording_into_design_band():
    pts = _slow_recording()
    out = geo.normalize_recorded_delays(pts)
    assert len(out) == len(pts)
    motion = geo.recorded_motion_ms(out)
    assert motion <= geo.RECORDED_MOTION_TARGET_MS + 1.0
    assert motion >= 300.0                       # 不能被压成瞬移
    assert max(p[2] for p in out) <= geo.RECORDED_GAP_MAX_MS


def test_normalize_preserves_geometry_and_endpoint():
    pts = _slow_recording()
    out = geo.normalize_recorded_delays(pts)
    assert [(p[0], p[1]) for p in out] == [(p[0], p[1]) for p in pts]
    assert out[-1][0] == pts[-1][0]              # 释放位不动（差 1px 就判错）
    assert out[-1][1] == pts[-1][1]


def test_normalize_is_idempotent():
    once = geo.normalize_recorded_delays(_slow_recording())
    twice = geo.normalize_recorded_delays(once)
    assert twice == once


def test_normalize_keeps_fast_recording_rhythm():
    # 已经符合设计带（650*1.15 以内）的录制不该被拉长
    pts = [[10.0, 0.0, 40.0], [120.0, 2.0, 40.0], [258.0, 0.0, 40.0]]
    out = geo.normalize_recorded_delays(pts)
    assert out == pts


def test_normalize_collapses_dead_holds():
    # 按下后按住不动 40s，再 300ms 拖到位：死停顿不是"移动"
    pts = [[0.0, 0.0, 40000.0], [100.0, 1.0, 30.0], [258.0, 0.0, 30.0]]
    out = geo.normalize_recorded_delays(pts)
    # 首点是 press-hold（夹到 1.2s 上限），不是位移，不参与压缩
    assert out[0][2] <= geo.RECORDED_PRESS_HOLD_MAX_MS
    assert geo.recorded_motion_ms(out) == pytest.approx(60.0)


def test_normalize_rejects_static_recording():
    pts = [[0.0, 0.0, 100.0], [0.0, 0.0, 100.0]]
    assert geo.normalize_recorded_delays(pts) == []


def test_human_events_to_points_normalizes_wall_clock():
    # 一次 16s 远端手动拖动 → 入池后纯位移段回设计带，几何保持
    events = [{"dt": 0, "x": 100.0, "y": 50.0, "buttons": 1}]
    steps = 20
    for i in range(1, steps + 1):
        events.append({
            "dt": 800.0 * i,
            "x": 100.0 + 258.0 * i / steps,
            "y": 50.0 + (1.0 if i % 4 == 0 else 0.0),
            "buttons": 1,
        })
    events.append({"dt": 16000.0, "x": 358.0, "y": 50.0, "buttons": 0})

    pts, distance, duration = human_events_to_points(events)
    assert pts and distance == pytest.approx(258.0)
    assert duration <= geo.RECORDED_MOTION_TARGET_MS + 1.0
    assert duration == pytest.approx(geo.recorded_motion_ms(pts))
    # 首点是 press-hold 单独夹（上限 1.2s）；位移段间隙有上限
    assert max(p[2] for p in pts[1:]) <= geo.RECORDED_GAP_MAX_MS
    assert pts[-1][0] == pytest.approx(258.0)     # 终点仍是录制终点


def test_pool_loaded_human_replay_is_in_design_band(tmp_path):
    """存量脏文件自愈：池里存的是 16s 录制，加载后的 plan 时间线仍在设计带。"""
    pool = SliderTrajectoryPool(base_dir=str(tmp_path))
    pool.save_trajectory(_slow_recording(), "probe", 258.0, True, "u", 16113.4,
                         source="human_cdp")

    session = GestureSession(pool, "probe")
    plan = session.next(258.0, None)
    assert plan is not None and plan.archetype == "human_replay"
    total = _timeline_total(plan.points)
    # 纯位移段 ≤650ms + 接近/过冲 ≤0.2s + 末端握持 0.45-1.10s
    assert total <= 2000.0
    assert max(float(p[2]) for p in plan.points) <= geo.RECORDED_GAP_MAX_MS


def test_exported_normalized_recording_replays_fast(tmp_path):
    """入池时已归一化 → 首次加载回放同样在设计带（不是双重压缩）。"""
    normalized = geo.normalize_recorded_delays(_slow_recording())
    pool = SliderTrajectoryPool(base_dir=str(tmp_path))
    pool.save_trajectory(normalized, "probe", 258.0, True, "u",
                         geo.recorded_motion_ms(normalized), source="human_cdp")
    session = GestureSession(pool, "probe")
    plan = session.next(258.0, None)
    assert plan is not None
    assert _timeline_total(plan.points) <= 2000.0


@pytest.mark.asyncio
async def test_replay_recorded_cdp_normalizes_dirty_points(tmp_path):
    """派发口兜底：外部直接喂 16s 点序，实际派发时间线仍回到设计带。"""
    from slidex.solver import SliderSolver

    events = []

    class _Session:
        async def send(self, method, params=None):
            events.append(dict(params))

    class _NoMouse:
        def __getattr__(self, name):
            raise AssertionError(f"page.{name} touched under pipeline mode")

    s = SliderSolver.__new__(SliderSolver)
    s.pure_user_id = "t"
    s._cdp = _Session()
    s.page = _NoMouse()

    ok = await s._replay_recorded_cdp(_slow_recording(), 100.0, 50.0)
    assert ok is True
    # 时间线在设计带内：位移段 ≤650 + 接近/握持
    assert len(events) >= 20
    assert events[-1]["type"] == "mouseReleased"
    # 终点（松键位）仍是录制末点
    assert events[-1]["x"] == pytest.approx(100.0 + 258.0)