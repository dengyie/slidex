"""Round-2 review fixes (0.11.2): malformed points, NaN, gate/decode agreement.

Every test here maps to a reproduced failure from the production review:

* one pool file with a ``null`` coordinate made ``_load_all`` raise, which
  silently disabled *all* human replay for that cookie (the gesture session
  catches the exception and falls back to synthetic);
* NaN coordinates passed ``recorded_geometry_verdict`` (``nan > limit`` is
  always False), got persisted, and were replayed as ``nan``;
* the write path promised "returns None on refusal" but raised instead, so the
  HTTP layer answered 500 and the panel showed "unknown".
"""

import json
import math

import pytest
from loguru import logger

from slidex._slide_geometry import (
    recorded_geometry_verdict,
    recorded_trace_verdict,
)
from slidex._trajectory_pool import SliderTrajectoryPool

NAN = float("nan")
INF = float("inf")


def _healthy(distance: float = 100.0, steps: int = 14):
    """Dense cumulative trace: monotonic, last point == distance."""
    return [[distance * i / steps, 0.0, 50.0] for i in range(steps + 1)]


class TestNonFiniteCoordinates:
    """NaN/Inf must be rejected, not silently accepted."""

    @pytest.mark.parametrize(
        "points",
        [
            [[0, 0, 10], [50, 0, 10], [NAN, 0, 10], [100, 0, 10]],
            [[NAN, 0, 10], [NAN, 0, 10]],
            [[0, 0, 10], [INF, 0, 10], [100, 0, 10]],
            [[0, 0, 10], [50, INF, 10], [100, 0, 10]],
            [[0, 0, 10], [50, 0, NAN], [100, 0, 10]],
        ],
    )
    def test_non_finite_points_are_rejected(self, points):
        assert recorded_geometry_verdict(points, 100.0) == "invalid_points"

    @pytest.mark.parametrize("distance", [NAN, INF, -INF])
    def test_non_finite_distance_is_rejected(self, distance):
        # NaN distance used to switch off every distance-based check.
        assert recorded_geometry_verdict(_healthy(), distance) == "invalid_points"

    @pytest.mark.parametrize(
        "bad",
        [None, "abc", {"x": 1}, [1, 2]],
    )
    def test_non_numeric_coordinate_is_rejected(self, bad):
        # The JS recorder serialises NaN through JSON.stringify -> null.
        assert recorded_geometry_verdict([[0, 0, 10], [bad, 0, 10]], 100.0) == "invalid_points"

    def test_healthy_trace_still_passes(self):
        assert recorded_geometry_verdict(_healthy(), 100.0) is None


class TestPoolLoadIsolation:
    """One bad file must not blind the whole cookie's pool."""

    def _pool(self, tmp_path):
        pool = SliderTrajectoryPool(base_dir=str(tmp_path))
        return pool

    def test_malformed_file_does_not_hide_siblings(self, tmp_path):
        pool = self._pool(tmp_path)
        cdir = pool._cookie_dir("u")
        (cdir / "trajectory_001.json").write_text(json.dumps(
            {"distance": 100.0, "points": _healthy(), "success": True,
             "recorded_at": "2026-11-01T00:00:00"}))
        (cdir / "trajectory_002.json").write_text(
            '{"distance": 100.0, "points": [[0,0,10],[null,0,10],[100,0,10]],'
            ' "success": true, "recorded_at": "2026-11-02T00:00:00"}'
        )
        records = pool._load_all("u")
        assert [r["_file"].rsplit("/", 1)[-1] for r in records] == ["trajectory_001.json"]

    def test_malformed_points_on_save_returns_none(self, tmp_path):
        pool = self._pool(tmp_path)
        assert pool.save_trajectory(
            [[0, 0, 10], [None, 0, 10], [100, 0, 10]], "u", 100.0, True
        ) is None
        assert list((tmp_path / "u").glob("trajectory_*.json")) == []

    def test_nan_recording_is_not_written(self, tmp_path):
        pool = self._pool(tmp_path)
        assert pool.save_trajectory(
            [[0, 0, 10], [NAN, 0, 10], [100, 0, 10]], "u", 100.0, True
        ) is None
        assert list((tmp_path / "u").glob("trajectory_*.json")) == []

    def test_hand_written_nan_file_is_filtered_on_load(self, tmp_path):
        # A file written before this fix (or by hand) must not re-enter replay.
        pool = self._pool(tmp_path)
        cdir = pool._cookie_dir("u")
        (cdir / "trajectory_001.json").write_text(
            '{"distance": 100.0, "points": [[NaN,0,10],[NaN,0,10]], "success": true}'
        )
        assert pool._load_all("u") == []

    def test_dirty_warning_is_emitted_once_per_file(self, tmp_path):
        from slidex import _trajectory_pool as pool_mod

        pool_mod._DIRTY_WARNED.clear()
        pool = self._pool(tmp_path)
        cdir = pool._cookie_dir("u")
        (cdir / "trajectory_001.json").write_text(json.dumps(
            {"distance": 100.0, "points": [[0, 0, 10], [200, 0, 10], [100, 0, 10]],
             "success": True}))
        messages = []
        sink_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
        try:
            for _ in range(5):
                pool._load_all("u")
        finally:
            logger.remove(sink_id)
        assert len([m for m in messages if "skip dirty recording" in m]) == 1


class TestGateDecodeAgreement:
    """The pool gate must judge the geometry the replayer would actually use."""

    def test_legacy_delta_trace_is_accepted_after_decode(self):
        # 112 small deltas summing to 258, as an old recorder wrote them.
        pts = [[2.3, 0.0, 9.0] for _ in range(112)]
        pts[-1][0] = 258.0 - sum(p[0] for p in pts[:-1])
        # Raw form is not a cumulative trace; judging it directly would reject it.
        assert recorded_geometry_verdict(pts, 258.0) == "distance_mismatch"
        assert recorded_trace_verdict(pts, 258.0) is None

    def test_absolute_coordinate_garbage_is_still_rejected(self):
        # Same shape as online trajectory_1000/1001: absolute coords with a
        # distant sum. Decoding is identity here, so the gate must still fire.
        pts = [[0, 0, 10], [258, 0, 10], [608, 0, 10], [258, 0, 10]]
        assert recorded_trace_verdict(pts, 258.0) == "overshoot"

    def test_healthy_trace_is_unchanged_by_decoding(self):
        pts = _healthy()
        assert recorded_trace_verdict(pts, 100.0) is None
