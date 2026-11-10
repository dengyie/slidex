"""Recorded-trajectory geometry verdict (0.11.0).

Thresholds come from a blind pass over the 50 recordings in the online pool for
1926782908, not from guesswork. The measured spread (peak/distance is the
column that separates the two clusters cleanly):

    healthy 40: peak/d <= 1.024, retro/d <= 0.023, jump/d <= 0.240
    dirty   10: peak/d >= 1.047 (989-992), 1.004 but jump/d = 0.853 (997/998),
                1.000 but retro/d = 0.508 (957/958), 2.36 / 2.11 (1000/1001)

So "no healthy trace ever reaches them" is the property worth freezing: every
healthily shaped trace must clear the reject line with room to spare, and the
four recorded corruption families must still be named.
"""

import json
import math
import os
import types

import pytest

from slidex import _slide_geometry as geo
from slidex._gestures import upgrade_points_to_cumulative
from slidex._trajectory_pool import SliderTrajectoryPool


def _human_trace(distance=258.0, n=35, tremor=0.6):
    """Single-direction human drag: smoothstep velocity + sub-pixel tremor."""
    pts = []
    for i in range(1, n + 1):
        t = i / n
        ease = t * t * (3 - 2 * t)
        pts.append([distance * ease, tremor * math.sin(i * 0.9), 12.0])
    pts[-1][0] = distance
    return pts


def _dense_trace(distance=258.0, n=24):
    """Healthiest online sample: steep start, jump 11% of travel."""
    return [[distance * i / n, 0.0, 10.0] for i in range(1, n + 1)]


def _chunky_trace(distance=258.0):
    """Worst healthy online sample (995/996): 28 points -> jump 24% of travel."""
    return [[distance * i / 24.0, 0.0, 16.0] for i in range(1, 25)]



def _abs_coords_1000(distance=258.0):
    """trajectory_1000: 0..258 slider travel, then a pass in page coordinates.

    peak/d = 2.36, snap-back of 350px; release still ends at `distance`. The
    geometry gate must catch it on the peak, before any replay happens.
    """
    pts = [[distance * i / 99.0, 0.0, 10.0] for i in range(1, 100)]
    peak = 608.0
    for j in range(1, 47):
        pts.append([peak + (distance - peak) * j / 46.0, 0.0, 10.0])
    pts[-1][0] = distance
    return pts


def _abs_coords_1001(distance=258.0):
    """trajectory_1001: two sweeps, second one leaving the release spot.

    peak/d = 2.11 (545px), retro = 341px inside a 234-point trace. Same family
    as 1000; only the numbers differ.
    """
    pts = []
    for i in range(1, 118):
        pts.append([545.0 * i / 117.0, 0.0, 10.0])
    for j in range(1, 117):
        pts.append([258.0 + (545.0 - 258.0) * (117 - j) / 116.0, 0.0, 10.0])
    pts[-1][0] = distance
    return pts


def _snapback_wave(distance=258.0):
    """trajectory_957/958/989-992: retrace to 0.49-0.98 of travel, then finish.

    peak stays inside the span gate (1.00-1.05), so only the retro rule can
    name it -- this is the family that a peak-only check would miss.
    """
    pts = [[distance * i / 20.0, 0.0, 10.0] for i in range(1, 21)]
    pts.append([distance * 1.02, 0.0, 10.0])
    pts.append([distance * 0.45, 0.0, 10.0])
    pts.append([distance, 0.0, 10.0])
    return pts


def _teleport_hole(distance=258.0):
    """trajectory_997/998: one 85%-of-travel step among dense sampling.

    Forward-only, ends exactly at `distance`, peak == distance: no overshoot
    and no retrace, so only the adjacent-step rule can name it.
    """
    pts = [[distance * 0.05 * i / 21.0, 0.0, 10.0] for i in range(1, 22)]
    pts.append([distance * 0.90, 0.0, 10.0])
    pts.append([distance, 0.0, 10.0])
    return pts


HEALTHY = (_human_trace, _dense_trace, _chunky_trace)
DIRTY = {
    "overshoot": [_abs_coords_1000, _abs_coords_1001],
    "non_monotonic": [_snapback_wave],
    "jump": [_teleport_hole],
}


def test_thresholds_pinned():
    # 阈值是线上盲测定出来的，改它们必须有新的线上证据
    assert geo.RECORDED_JUMP_FRACTION == pytest.approx(0.35)
    assert geo.RECORDED_RETRO_RATIO == pytest.approx(0.35)
    assert geo.RECORDED_SPAN_RATIO == pytest.approx(1.15)


@pytest.mark.parametrize("make", HEALTHY)
def test_healthy_traces_pass(make):
    assert geo.recorded_geometry_verdict(make(), 258.0) is None


@pytest.mark.parametrize(
    "reason,make",
    [(reason, make) for reason, makers in sorted(DIRTY.items()) for make in makers],
)
def test_dirty_traces_are_named(reason, make):
    # 原因码要指对是哪条判据踩的：线上只能靠它从 result 日志定位
    assert geo.recorded_geometry_verdict(make(), 258.0) == reason


@pytest.mark.parametrize("make", HEALTHY)
def test_healthy_headroom_is_at_least_ten_percent(make):
    # 健康簇最坏样本离拒收线要有余量，否则阈值等于贴着噪声定的
    xs = [p[0] for p in make()]
    jump = max(abs(xs[i] - xs[i - 1]) for i in range(1, len(xs)))
    assert jump / 258.0 <= geo.RECORDED_JUMP_FRACTION * 0.9
    assert max(xs) / 258.0 <= geo.RECORDED_SPAN_RATIO * 0.9


@pytest.mark.parametrize(
    "reason,make",
    [(reason, make) for reason, makers in sorted(DIRTY.items()) for make in makers],
)
def test_dirty_traces_are_dirty_at_any_distance(reason, make):
    # 阈值按 distance 归一化：60px 的小滑块要用同一套规则判，不能靠绝对像素放过
    assert geo.recorded_geometry_verdict(make(60.0), 60.0) == reason
    assert geo.recorded_geometry_verdict(make(120.0), 120.0) == reason


class TestDegenerateShapes:
    def test_empty(self):
        assert geo.recorded_geometry_verdict([], 258.0) == "no_points"

    def test_single_point(self):
        assert geo.recorded_geometry_verdict([[258.0, 0.0, 40.0]], 258.0) == "no_points"

    def test_tap_without_motion(self):
        assert geo.recorded_geometry_verdict([[50.0, 0.0, 30.0]] * 6, 258.0) == "no_motion"

    def test_end_point_off_distance(self):
        # 增量编码的末点必须等于 distance；差 40px 说明 distance 与 points 不同源
        pts = _human_trace()
        pts[-1][0] = 218.0
        assert geo.recorded_geometry_verdict(pts, 258.0) == "distance_mismatch"

    def test_negative_clock(self):
        pts = _human_trace()
        pts[3][2] = -1.0
        assert geo.recorded_geometry_verdict(pts, 258.0) == "invalid_clock"

    def test_without_distance_only_span_forms_survive(self):
        # 没有 distance 时跳变只能按整体 span 归一化：单向单步轨迹（最大步
        # 就是 span + 抖动）相对量永远很小，这条判据刻意不在这里拍脑袋。
        # 有无 distance 的健康录制都必须放行，这是对称性最低要求。
        assert geo.recorded_geometry_verdict(_teleport_hole()) is None
        assert geo.recorded_geometry_verdict(_human_trace()) is None
        assert geo.recorded_geometry_verdict(_human_trace(), 258.0) is None


class TestReplayClassifier:
    """upgrade_points_to_cumulative 的输入是"点列"，判据只看末点与 sum。"""

    def test_cumulative_trace_is_passed_through_untouched(self):
        # 已是累计坐标（末点=distance）且单调：一个字节都不许改
        pts = _human_trace()
        assert upgrade_points_to_cumulative(pts, 258.0) == pts

    def test_genuine_adjacent_diffs_are_still_summed(self):
        # 真·增量编码（每点只带本段位移）必须继续被累加，收紧不能一刀放行
        pts = [[2.3, 0.0, 9.0] for _ in range(112)]
        pts[-1][0] = 258.0 - sum(p[0] for p in pts[:-1])
        out = upgrade_points_to_cumulative(pts, 258.0)
        assert out[-1][0] == pytest.approx(258.0, abs=1e-6)
        xs = [p[0] for p in out]
        assert all(xs[i] >= xs[i - 1] for i in range(1, len(xs)))

    def test_absolute_trace_is_not_lifted_by_sum(self):
        # 线上 1000/1001 的实测 sum 是 46892 / 73636（远超 distance）：
        # legacy sum 判据并不会把它们当增量编码，回放飞出的是几何本身。
        # 这条用例锁住"末点对上就不走 sum 分支"，避免以后有人把判据改成按
        # peak 猜编码时把它抬起来。
        pts = _abs_coords_1001()
        out = upgrade_points_to_cumulative(pts, 258.0)
        assert max(p[0] for p in out) == pytest.approx(max(p[0] for p in pts))
        assert sum(p[0] for p in out) == pytest.approx(sum(p[0] for p in pts))


class TestPoolGate:
    """写池拒收 + 加载侧挡存量脏文件。"""

    def _pool(self, tmp_path, max_per_cookie=50):
        cfg = types.SimpleNamespace(
            trajectory_pool_enabled=True,
            trajectory_pool_max_per_cookie=max_per_cookie,
            trajectory_pool_min_pool_size=0,
            trajectory_pool_rotation_strategy="lru",
            trajectory_pool_recorded_only_mode=False,
        )
        return SliderTrajectoryPool.from_config(cfg, base_dir=str(tmp_path))

    def _write(self, pool, name, points, distance=258.0, source="human"):
        path = pool._cookie_dir("c1") / name
        path.write_text(
            json.dumps(
                {
                    "distance": distance,
                    "success": True,
                    "source": source,
                    "points": points,
                }
            ),
            encoding="utf-8",
        )
        return path

    @pytest.mark.parametrize(
        "make", [_teleport_hole, _snapback_wave, _abs_coords_1000]
    )
    def test_dirty_recording_never_lands(self, tmp_path, make):
        pool = self._pool(tmp_path)
        assert pool.save_trajectory(make(), "c1", 258.0, True, source="human") is None
        assert pool._load_all("c1") == []

    def test_healthy_recording_lands(self, tmp_path):
        pool = self._pool(tmp_path)
        name = pool.save_trajectory(_human_trace(), "c1", 258.0, True, source="human")
        assert name
        assert len(pool._load_all("c1")) == 1

    def test_existing_dirty_file_is_filtered_on_load(self, tmp_path):
        # 存量脏录制（0.11.0 前写入、写池时还没有几何校验）必须先被挡住：
        # 它一旦被抽中回放，就是一次飞出。
        pool = self._pool(tmp_path)
        self._write(pool, "trajectory_001.json", _human_trace())
        self._write(pool, "trajectory_002.json", _teleport_hole())
        loaded = pool._load_all("c1")
        assert [os.path.basename(r["_file"]) for r in loaded] == ["trajectory_001.json"]
        rec = pool.load_unused_human("c1", 258.0)
        assert rec is not None
        assert os.path.basename(rec["_file"]) == "trajectory_001.json"

    def test_all_dirty_pool_falls_through_to_generator(self, tmp_path):
        # 池里只剩脏录制时 load_unused_human 必须返回 None（回退生成器轨迹），
        # 而不是硬塞一条坏轨迹
        pool = self._pool(tmp_path)
        self._write(pool, "trajectory_001.json", _teleport_hole())
        self._write(pool, "trajectory_002.json", _snapback_wave())
        assert pool.load_unused_human("c1", 258.0) is None
