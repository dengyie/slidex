"""0.6.28: orthogonal gesture library — family shapes, session rotation, human skip."""
from __future__ import annotations

import ast
import random
import tempfile
from pathlib import Path

import pytest

from slidex._gestures import (
    ARCHETYPE_BALLISTIC,
    ARCHETYPE_HUMAN,
    ARCHETYPE_MINIMUM_JERK,
    ARCHETYPE_OVERSHOOT,
    ARCHETYPE_PAUSE,
    ARCHETYPE_TREMOR,
    FIRST_TRY_ORDER,
    GestureSession,
    SYNTHETIC_ARCHETYPES,
    generate_archetype,
    upgrade_points_to_cumulative,
)
from slidex._trajectory_pool import SliderTrajectoryPool
import slidex._gestures as gestures_mod


DIST = 158.0


def _xs(plan):
    return [p[0] for p in plan.points]


def _delays(plan):
    return [p[2] for p in plan.points]


def test_first_try_order_lists_human_then_five_synthetics():
    assert FIRST_TRY_ORDER[0] == ARCHETYPE_HUMAN
    assert tuple(FIRST_TRY_ORDER[1:]) == SYNTHETIC_ARCHETYPES
    assert SYNTHETIC_ARCHETYPES == (
        ARCHETYPE_MINIMUM_JERK,
        ARCHETYPE_BALLISTIC,
        ARCHETYPE_OVERSHOOT,
        ARCHETYPE_PAUSE,
        ARCHETYPE_TREMOR,
    )


def test_synthetic_generators_do_not_call_or_copy_legacy_eased():
    src = Path(gestures_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"generate_trajectory"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = {a.name for a in node.names}
            assert not names & forbidden
        if isinstance(node, ast.Name) and node.id == "generate_trajectory":
            pytest.fail("synthetic library must not reference generate_trajectory")
    assert "0.02" not in src or "0.13" not in src
    assert "t ** 1.8" not in src and "t**1.8" not in src
    assert "0.15 + 0.60" not in src


def test_archetype_velocity_shapes_are_distinguishable():
    random.seed(7)
    jerk = generate_archetype(ARCHETYPE_MINIMUM_JERK, DIST)
    ball = generate_archetype(ARCHETYPE_BALLISTIC, DIST)
    over = generate_archetype(ARCHETYPE_OVERSHOOT, DIST)
    pause = generate_archetype(ARCHETYPE_PAUSE, DIST)
    tremor = generate_archetype(ARCHETYPE_TREMOR, DIST)

    # minimum_jerk: Flash-Hogan, monotonic, last x = distance, no overshoot
    jx = _xs(jerk)
    assert jx[0] == 0.0
    assert jx[-1] == pytest.approx(DIST)
    assert all(jx[i] <= jx[i + 1] + 1e-9 for i in range(len(jx) - 1))
    assert max(jx) == pytest.approx(DIST)
    assert jerk.extra_overshoot is False

    # ballistic: dash to 85-92% then delay >= 80ms then slow correction
    bx = _xs(ball)
    assert bx[-1] == pytest.approx(DIST)
    pause_pts = [p for p in ball.points[1:] if p[2] >= 80.0 and 0.85 * DIST <= p[0] <= 0.92 * DIST + 1e-6]
    assert pause_pts, "ballistic must pause at 85-92% for >=80ms"
    peak = pause_pts[0]
    after = [p for p in ball.points if p[0] > peak[0] + 1e-6]
    assert after, "slow correction after ballistic peak"
    assert all(p[2] >= 40.0 for p in after)

    # overshoot_snapback: 8-18px overshoot in-point, baked, longer end hold
    ox = _xs(over)
    assert max(ox) - DIST >= 8.0 - 1e-6
    assert max(ox) - DIST <= 18.0 + 1e-6
    assert ox[-1] == pytest.approx(DIST)
    assert over.extra_overshoot is False
    assert over.end_hold_scale >= 1.4

    # pause_hold: a delay >= 200ms while x is in 40-60%
    pauses = [p for p in pause.points if p[2] >= 200.0 and 0.40 * DIST <= p[0] <= 0.60 * DIST]
    if not pauses:
        pauses = [p for p in pause.points[1:] if p[2] >= 200.0]
    assert pauses, "pause_hold must contain a >=200ms hold"
    assert pause.points[-1][0] == pytest.approx(DIST)

    # tremor_dense: 28-40 move steps
    move_n = len(tremor.points) - 1
    assert 28 <= move_n <= 40
    assert tremor.points[-1][0] == pytest.approx(DIST)

    # families must not collapse to the same delay / step fingerprint
    fingerprints = {
        ("jerk", len(jerk.points), round(max(_delays(jerk)), 1), jerk.extra_overshoot),
        ("ball", len(ball.points), round(max(_delays(ball)), 1), ball.extra_overshoot),
        ("over", len(over.points), round(max(_xs(over)), 1), over.extra_overshoot),
        ("pause", len(pause.points), round(max(_delays(pause)), 1), pause.extra_overshoot),
        ("tremor", len(tremor.points), round(max(_delays(tremor)), 1), tremor.extra_overshoot),
    }
    assert len(fingerprints) == 5


def test_session_does_not_repeat_synthetic_archetype():
    session = GestureSession(None, "u", trajectory_mode="generated")
    seen = []
    code = None
    for _ in range(len(SYNTHETIC_ARCHETYPES)):
        plan = session.next(DIST, code)
        assert plan is not None
        assert plan.archetype not in seen
        seen.append(plan.archetype)
        code = 300
    assert seen == list(SYNTHETIC_ARCHETYPES)
    assert session.next(DIST, 300) is None


def test_code_300_switches_family():
    session = GestureSession(None, "u", trajectory_mode="generated")
    a = session.next(DIST, None)
    b = session.next(DIST, 300)
    assert a is not None and b is not None
    assert a.archetype != b.archetype
    c = session.next(DIST, 0)  # other packet failure code
    assert c is not None
    assert c.archetype != b.archetype


def test_code_minus_1_does_not_switch_family():
    session = GestureSession(None, "u", trajectory_mode="generated")
    a = session.next(DIST, None)
    b = session.next(DIST, -1)
    c = session.next(DIST, -1)
    assert a is not None and b is not None and c is not None
    assert a.archetype == b.archetype == c.archetype == ARCHETYPE_MINIMUM_JERK
    # synthetic -1 resamples points (same family, not necessarily identical samples)
    d = session.next(DIST, 300)
    assert d is not None
    assert d.archetype != a.archetype


def test_human_used_file_is_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        pool = SliderTrajectoryPool(tmp)
        pool.save_trajectory([[0, 0, 100], [DIST, 0, 40]], "u1", DIST, True, source="human_cdp")
        pool.save_trajectory([[0, 0, 80], [DIST, 1, 40]], "u1", DIST, True, source="human_cdp")
        first = pool.load_unused_human("u1", DIST, exclude_files=set(), tolerance=0.10)
        assert first is not None
        used = {first["_file"], Path(first["_file"]).name}
        second = pool.load_unused_human("u1", DIST, exclude_files=used, tolerance=0.10)
        assert second is not None
        assert second["_file"] != first["_file"]
        both = {first["_file"], second["_file"]}
        assert pool.load_unused_human("u1", DIST, exclude_files=both, tolerance=0.10) is None

        session = GestureSession(pool, "u1", trajectory_mode="recorded")
        p1 = session.next(DIST, None)
        p2 = session.next(DIST, 300)
        assert p1 is not None and p1.archetype == ARCHETYPE_HUMAN
        assert p2 is not None and p2.archetype == ARCHETYPE_HUMAN
        assert p1.source_file != p2.source_file
        assert session.next(DIST, 300) is None  # recorded: humans exhausted, no synthetic wrap


def test_recorded_mode_does_not_fall_back_to_generate_trajectory():
    session = GestureSession(None, "u", trajectory_mode="recorded")
    assert session.next(DIST, None) is None


def test_upgrade_diff_trace_uses_last_or_sum_not_mid_abs():
    # already cumulative: last x == distance
    cum = [[0.0, 0.0, 800.0], [30.0, 1.0, 30.0], [158.0, 0.0, 640.0]]
    assert upgrade_points_to_cumulative(cum, 158.0) == cum

    # adjacent diffs whose sum is distance (legacy human_events_to_points)
    diffs = [[0.0, 0.0, 800.0], [30.0, 1.0, 30.0], [128.0, -1.0, 30.0], [0.0, 0.0, 640.0]]
    upgraded = upgrade_points_to_cumulative(diffs, 158.0)
    assert upgraded[-1][0] == pytest.approx(158.0)
    assert upgraded[1][0] == pytest.approx(30.0)
    assert upgraded[2][0] == pytest.approx(158.0)

    # neither last nor sum matches: keep as-is (do not guess from mid |x|)
    weird = [[0.0, 0.0, 10.0], [80.0, 0.0, 10.0], [40.0, 0.0, 10.0]]
    assert upgrade_points_to_cumulative(weird, 158.0) == weird


def test_human_minus_1_replays_same_points():
    with tempfile.TemporaryDirectory() as tmp:
        pool = SliderTrajectoryPool(tmp)
        pool.save_trajectory([[0, 0, 100], [DIST, 0, 40]], "u1", DIST, True, source="human_cdp")
        session = GestureSession(pool, "u1", trajectory_mode="recorded")
        a = session.next(DIST, None)
        b = session.next(DIST, -1)
        assert a is not None and b is not None
        assert a.archetype == b.archetype == ARCHETYPE_HUMAN
        assert list(a.points) == list(b.points)
        assert a.source_file == b.source_file


def test_human_minus_1_rescales_to_new_distance():
    with tempfile.TemporaryDirectory() as tmp:
        pool = SliderTrajectoryPool(tmp)
        pool.save_trajectory([[0, 0, 100], [DIST, 0, 40]], "u1", DIST, True, source="human_cdp")
        session = GestureSession(pool, "u1", trajectory_mode="recorded")
        a = session.next(DIST, None)
        b = session.next(180.0, -1)
        assert a is not None and b is not None
        assert a.archetype == b.archetype == ARCHETYPE_HUMAN
        assert a.source_file == b.source_file
        assert a.points[-1][0] == pytest.approx(DIST)
        assert b.points[-1][0] == pytest.approx(180.0)
