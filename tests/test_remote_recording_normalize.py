"""0.10.2 / P2-4: 远程面板录制入库前的时序归一化。

``finish_recording`` 吃的是人工面板经 WebSocket 转发的事件墙钟：面板/链路慢
的时候一次拖动跨十几到几十秒（线上实证 13.8-48.4s）。这些点直接入池，回放
侧会把它当成"人形节奏"原样播——用户看到的就是走走停停卡顿 16s 的轨迹。

入库口必须与 ingest（``human_events_to_points``）同一套语义：
  1. 几何逐点保留（相对位移、终点、点序）；
  2. 纯位移段回到设计带，单点间隙有上限，死停顿被折叠；
  3. ``duration_ms`` 汇报归一化后的纯位移时长，不是录制墙钟；
  4. 静态/无位移录制不入池（返回 None），避免污染池并淘汰真实录制。
"""
from __future__ import annotations

import pytest

from slidex import _slide_geometry as geo
from slidex.remote import CaptchaRemoteController


def _feed(ctrl: CaptchaRemoteController, sid: str, events):
    """events: [(event_type, x, y, timestamp_s)] —— 直接灌事件，绕开 loop 时钟。"""
    ctrl.session_recordings[sid] = [
        {"event_type": t, "x": x, "y": y, "timestamp": ts}
        for (t, x, y, ts) in events
    ]


def _crawl(n=120, span_s=16.1, x0=640.0, x1=898.0):
    """复刻线上慢录制：n 个 move 铺在 span_s 里，单调右移到 258px。"""
    events = [("down", x0, 500.0, 0.0)]
    for i in range(1, n + 1):
        events.append(("move", x0 + (x1 - x0) * i / n, 500.0, span_s * i / n))
    events.append(("up", x1, 500.0, span_s))
    return events


def test_slow_panel_drag_is_normalized_into_design_band():
    ctrl = CaptchaRemoteController()
    _feed(ctrl, "s1", _crawl())
    rec = ctrl.finish_recording("s1")

    assert rec is not None
    assert rec["distance"] == pytest.approx(258.0, abs=0.1)
    # 墙钟 16.1s 不该出现在入库结果里
    motion = geo.recorded_motion_ms(rec["points"])
    assert motion <= geo.RECORDED_MOTION_TARGET_MS + 1.0
    assert rec["duration_ms"] == pytest.approx(motion, abs=0.2)
    # 采样密度也被压回 ≤125Hz（0.11.0）
    assert len(rec["points"]) - 1 <= motion / geo.RECORDED_GAP_MIN_MS + 1


def test_geometry_and_endpoint_survive_normalization():
    ctrl = CaptchaRemoteController()
    events = _crawl()
    _feed(ctrl, "s2", events)
    rec = ctrl.finish_recording("s2")

    assert rec["points"][-1][0] == pytest.approx(258.0)   # 释放位 = 面板 up 位置
    assert rec["points"][-1][1] == pytest.approx(0.0)
    # 单点间隙不超过上限，首点是 press-hold 上限
    assert max(p[2] for p in rec["points"][1:]) <= geo.RECORDED_GAP_MAX_MS
    assert rec["points"][0][2] <= geo.RECORDED_PRESS_HOLD_MAX_MS


def test_fast_panel_drag_keeps_its_rhythm():
    ctrl = CaptchaRemoteController()
    events = [("down", 600.0, 500.0, 0.0)]
    for i in range(1, 25):
        events.append(("move", 600.0 + 258.0 * i / 24, 500.0, 0.031 * i))
    events.append(("up", 858.0, 500.0, 0.75))
    _feed(ctrl, "s3", events)
    rec = ctrl.finish_recording("s3")

    assert rec is not None
    # 已经够快：不被拖慢，也不被抽稀到 8ms 以外的粗粒度
    motion = geo.recorded_motion_ms(rec["points"])
    assert 400.0 <= motion <= geo.RECORDED_MOTION_TARGET_MS * geo.RECORDED_MOTION_BAND_TOLERANCE


def test_static_recording_is_discarded():
    ctrl = CaptchaRemoteController()
    # down/move/up 都在同一坐标：没有任何位移
    _feed(ctrl, "s4", [
        ("down", 700.0, 500.0, 0.0),
        ("move", 700.0, 500.0, 0.4),
        ("move", 700.0, 500.0, 0.8),
        ("up", 700.0, 500.0, 1.2),
    ])
    rec = ctrl.finish_recording("s4")

    assert rec is None, "无位移录制会污染池并挤掉真实录制"
    assert "s4" not in ctrl.session_recordings
