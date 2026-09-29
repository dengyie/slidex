"""Orthogonal slide-gesture library (0.6.28).

Five synthetic families are generated independently. They must not call
``generate_trajectory`` and must not copy the four-phase eased curve in
``_trajectory.py``. ``generate_trajectory`` itself stays untouched.

``GestureSession.next`` is the only planner the solve loops ask:
- ``last_code is None`` → first available in ``FIRST_TRY_ORDER``
- ``last_code == -1`` → do not switch (human keeps the same recording
  but re-scales points to this attempt's distance; synthetic resamples
  the same family)
- any other code (300, success:false code:0, other packet failures) →
  consume the current family, pick the next unused orthogonal family
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

from loguru import logger

from slidex._slide_geometry import points_from_recorded
from slidex._trajectory_pool import SliderTrajectoryPool

Point = Tuple[float, float, float]


ARCHETYPE_HUMAN = "human_replay"
ARCHETYPE_MINIMUM_JERK = "minimum_jerk"
ARCHETYPE_BALLISTIC = "ballistic_corrective"
ARCHETYPE_OVERSHOOT = "overshoot_snapback"
ARCHETYPE_PAUSE = "pause_hold"
ARCHETYPE_TREMOR = "tremor_dense"

SYNTHETIC_ARCHETYPES = (
    ARCHETYPE_MINIMUM_JERK,
    ARCHETYPE_BALLISTIC,
    ARCHETYPE_OVERSHOOT,
    ARCHETYPE_PAUSE,
    ARCHETYPE_TREMOR,
)

FIRST_TRY_ORDER = (ARCHETYPE_HUMAN,) + SYNTHETIC_ARCHETYPES

_PACKET_MISS = -1


@dataclass
class GesturePlan:
    """One attempt's choreography, relative to the press origin."""

    archetype: str
    points: Sequence[Sequence[float]]
    extra_overshoot: bool
    end_hold_scale: float = 1.0
    source_file: Optional[str] = None

    def __post_init__(self) -> None:
        self.points = tuple(
            (float(p[0]), float(p[1]) if len(p) > 1 else 0.0, float(p[2]) if len(p) > 2 else 0.0)
            for p in self.points
        )
        self.end_hold_scale = max(0.0, float(self.end_hold_scale))


def upgrade_points_to_cumulative(
    points: Sequence[Sequence[float]],
    distance: float,
    tolerance: float = 2.0,
) -> List[List[float]]:
    """Lift legacy adjacent-diff traces to origin-relative cumulative x/y.

    Decision uses only last-x vs ``distance`` and sum(x) vs ``distance``.
    Mid-trace ``|x|`` is never a classifier (those values collide for both
    encodings).
    """
    if not points:
        return []
    parsed: List[List[float]] = []
    for point in points:
        if not point:
            continue
        x = float(point[0])
        y = float(point[1]) if len(point) > 1 else 0.0
        delay = float(point[2]) if len(point) > 2 else 0.0
        parsed.append([x, y, delay])
    if not parsed:
        return []
    dist = float(distance or 0.0)
    tol = max(float(tolerance), 0.02 * abs(dist) if dist else float(tolerance))
    last_x = parsed[-1][0]
    sum_x = sum(p[0] for p in parsed)
    if abs(last_x - dist) <= tol:
        return parsed
    if abs(sum_x - dist) <= tol:
        cx = 0.0
        cy = 0.0
        out: List[List[float]] = []
        for x, y, delay in parsed:
            cx += x
            cy += y
            out.append([cx, cy, delay])
        return out
    return parsed


def generate_archetype(name: str, distance: float) -> GesturePlan:
    """Build a synthetic plan. ``name`` must be one of ``SYNTHETIC_ARCHETYPES``."""
    dist = float(distance)
    if name == ARCHETYPE_MINIMUM_JERK:
        return GesturePlan(
            archetype=name,
            points=_minimum_jerk(dist),
            extra_overshoot=False,
            end_hold_scale=1.0,
        )
    if name == ARCHETYPE_BALLISTIC:
        return GesturePlan(
            archetype=name,
            points=_ballistic_corrective(dist),
            extra_overshoot=True,
            end_hold_scale=1.0,
        )
    if name == ARCHETYPE_OVERSHOOT:
        return GesturePlan(
            archetype=name,
            points=_overshoot_snapback(dist),
            extra_overshoot=False,
            end_hold_scale=random.uniform(1.4, 1.8),
        )
    if name == ARCHETYPE_PAUSE:
        return GesturePlan(
            archetype=name,
            points=_pause_hold(dist),
            extra_overshoot=True,
            end_hold_scale=1.0,
        )
    if name == ARCHETYPE_TREMOR:
        return GesturePlan(
            archetype=name,
            points=_tremor_dense(dist),
            extra_overshoot=True,
            end_hold_scale=1.0,
        )
    raise ValueError(f"unknown gesture archetype: {name!r}")


def _press_hold_ms() -> float:
    return random.uniform(100.0, 200.0)


def _y_wiggle(tau: float, amp: float) -> float:
    """Small vertical noise that dies at the endpoints. Not the legacy spike model."""
    envelope = math.sin(math.pi * min(max(tau, 0.0), 1.0))
    return amp * envelope * random.uniform(-1.0, 1.0)


def _minimum_jerk(distance: float) -> List[Point]:
    """Flash-Hogan 5th-order point-to-point: bell velocity, monotonic, no overshoot."""
    steps = random.randint(12, 18)
    traj: List[Point] = [(0.0, 0.0, _press_hold_ms())]
    duration = random.uniform(420.0, 680.0)
    for i in range(1, steps + 1):
        tau = i / steps
        # Flash & Hogan 1985: 10t^3 - 15t^4 + 6t^5
        eased = tau * tau * tau * (10.0 + tau * (-15.0 + 6.0 * tau))
        x = distance * eased
        delay = max(16.0, duration / steps + random.uniform(-6.0, 6.0))
        y = 0.0 if i == steps else _y_wiggle(tau, 1.2)
        traj.append((x, y, delay))
    if traj[-1][0] != distance:
        traj.append((distance, 0.0, random.uniform(40.0, 90.0)))
    else:
        last = traj[-1]
        traj[-1] = (distance, 0.0, last[2])
    return traj


def _ballistic_corrective(distance: float) -> List[Point]:
    """Fast dash to 85-92% of travel, pause >= 80ms, then a slow correction."""
    peak_ratio = random.uniform(0.85, 0.92)
    peak = distance * peak_ratio
    n_fast = random.randint(6, 9)
    n_slow = random.randint(4, 6)
    traj: List[Point] = [(0.0, 0.0, _press_hold_ms())]
    for i in range(1, n_fast + 1):
        tau = i / n_fast
        # quadratic ease-in: distinct from minimum-jerk's 5th-order polynomial
        x = peak * (tau * tau)
        delay = random.uniform(14.0, 28.0)
        traj.append((x, _y_wiggle(tau * 0.5, 1.6), delay))
    traj.append((peak, _y_wiggle(peak_ratio, 0.8), random.uniform(80.0, 140.0)))
    remain = distance - peak
    for i in range(1, n_slow + 1):
        tau = i / n_slow
        x = peak + remain * (0.5 - 0.5 * math.cos(math.pi * tau))
        delay = random.uniform(45.0, 75.0)
        y = 0.0 if i == n_slow else _y_wiggle(0.5 + 0.5 * tau, 0.7)
        traj.append((x, y, delay))
    last = traj[-1]
    traj[-1] = (distance, 0.0, last[2])
    return traj


def _overshoot_snapback(distance: float) -> List[Point]:
    """Overshoot 8-18px inside the point sequence, then snap back. No extra tail."""
    over = random.uniform(8.0, 18.0)
    peak = distance + over
    n_out = random.randint(8, 12)
    n_back = random.randint(3, 5)
    traj: List[Point] = [(0.0, 0.0, _press_hold_ms())]
    for i in range(1, n_out + 1):
        tau = i / n_out
        # smoothstep, not the legacy four-phase piecewise
        s = tau * tau * (3.0 - 2.0 * tau)
        x = peak * s
        delay = random.uniform(22.0, 40.0)
        traj.append((x, _y_wiggle(tau, 1.4), delay))
    traj.append((peak, random.uniform(-1.2, 1.2), random.uniform(40.0, 70.0)))
    for i in range(1, n_back + 1):
        tau = i / n_back
        x = peak - over * tau
        delay = random.uniform(30.0, 55.0)
        y = 0.0 if i == n_back else _y_wiggle(1.0 - tau, 0.6)
        traj.append((x, y, delay))
    last = traj[-1]
    traj[-1] = (distance, 0.0, last[2])
    return traj


def _pause_hold(distance: float) -> List[Point]:
    """Cosine ease with a >= 200ms pause once x is in the 40-60% band."""
    steps = random.randint(11, 16)
    traj: List[Point] = [(0.0, 0.0, _press_hold_ms())]
    paused = False
    band = max(abs(distance), 1.0)
    for i in range(1, steps + 1):
        tau = i / steps
        x = distance * (0.5 - 0.5 * math.cos(math.pi * tau))
        in_band = 0.40 * band <= abs(x) <= 0.60 * band
        if in_band and not paused:
            delay = random.uniform(200.0, 320.0)
            paused = True
        else:
            delay = random.uniform(24.0, 48.0)
        y = 0.0 if i == steps else _y_wiggle(tau, 1.1)
        traj.append((x, y, delay))
    if not paused:
        mid = traj[len(traj) // 2]
        traj[len(traj) // 2] = (mid[0], mid[1], max(mid[2], 200.0))
    last = traj[-1]
    traj[-1] = (distance, 0.0, last[2])
    return traj


def _tremor_dense(distance: float) -> List[Point]:
    """28-40 dense steps with high-frequency y tremor."""
    steps = random.randint(28, 40)
    traj: List[Point] = [(0.0, 0.0, _press_hold_ms())]
    for i in range(1, steps + 1):
        tau = i / steps
        x = distance * tau
        if i < steps:
            x += random.uniform(-0.45, 0.45)
            x = min(max(x, 0.0), distance)
        y = 0.0 if i == steps else random.uniform(-2.4, 2.4)
        delay = random.uniform(12.0, 26.0)
        traj.append((x, y, delay))
    last = traj[-1]
    traj[-1] = (distance, 0.0, last[2])
    return traj


class GestureSession:
    """One planner per solve. Retry loops must not construct a second session."""

    def __init__(
        self,
        pool: Optional[SliderTrajectoryPool],
        cookie_id: str,
        trajectory_mode: str = "auto",
    ) -> None:
        self.pool = pool
        self.cookie_id = cookie_id
        self.trajectory_mode = trajectory_mode or "auto"
        self._used_archetypes: Set[str] = set()
        self._used_human_files: Set[str] = set()
        self._current_plan: Optional[GesturePlan] = None
        self._current_archetype: Optional[str] = None
        self._current_human_rec: Optional[dict] = None

    def next(self, distance: float, last_code: Optional[int]) -> Optional[GesturePlan]:
        dist = float(distance or 0.0)
        if last_code == _PACKET_MISS and self._current_plan is not None and self._current_archetype:
            if self._current_archetype == ARCHETYPE_HUMAN:
                # Same recording, but re-scale to this attempt's measured travel.
                # Packet miss is not "reuse the old pixel sequence".
                plan = self._rebuild_human(dist)
                if plan is not None:
                    self._current_plan = plan
                    logger.debug(
                        f"gesture session: code=-1 replaying human file "
                        f"rescaled to dist={dist:.0f}"
                    )
                    return plan
                logger.debug("gesture session: code=-1 replaying human points")
                return self._current_plan
            plan = generate_archetype(self._current_archetype, dist)
            self._current_plan = plan
            logger.debug(f"gesture session: code=-1 resampling {self._current_archetype}")
            return plan

        if last_code is not None and last_code != _PACKET_MISS:
            # 合成家族 consume 后永不回绕；human 只排除已用文件，池中还有未用条则继续 human。
            if self._current_archetype and self._current_archetype != ARCHETYPE_HUMAN:
                self._used_archetypes.add(self._current_archetype)
            elif self._current_archetype == ARCHETYPE_HUMAN and self._peek_human(dist) is None:
                self._used_archetypes.add(ARCHETYPE_HUMAN)
            self._current_plan = None
            self._current_archetype = None
            self._current_human_rec = None

        archetype = self._first_available(dist)
        if archetype is None:
            return None
        plan = self._build(archetype, dist)
        if plan is None:
            if archetype == ARCHETYPE_HUMAN:
                self._used_archetypes.add(ARCHETYPE_HUMAN)
                return self.next(dist, last_code=300 if last_code is None else last_code)
            return None
        self._current_plan = plan
        self._current_archetype = archetype
        return plan

    def _first_available(self, distance: float) -> Optional[str]:
        for name in FIRST_TRY_ORDER:
            if name in self._used_archetypes:
                continue
            if name == ARCHETYPE_HUMAN:
                if self.trajectory_mode == "generated":
                    continue
                if self._peek_human(distance) is None:
                    continue
                return name
            if self.trajectory_mode == "recorded":
                continue
            return name
        return None

    def _peek_human(self, distance: float) -> Optional[dict]:
        return self._load_human(distance, consume=False)

    def _load_human(self, distance: float, consume: bool) -> Optional[dict]:
        if self.pool is None:
            return None
        cookies = [self.cookie_id]
        if self.cookie_id != "default":
            cookies.append("default")
        for cid in cookies:
            try:
                rec = self.pool.load_unused_human(
                    cid,
                    distance,
                    exclude_files=self._used_human_files,
                    tolerance=0.10,
                )
            except Exception as e:
                logger.debug(f"gesture session: load_unused_human failed: {e}")
                rec = None
            if rec:
                if consume:
                    path = rec.get("_file")
                    if path:
                        self._used_human_files.add(os.path.basename(str(path)))
                        self._used_human_files.add(str(path))
                return rec
        return None

    def _build(self, archetype: str, distance: float) -> Optional[GesturePlan]:
        if archetype != ARCHETYPE_HUMAN:
            self._current_human_rec = None
            return generate_archetype(archetype, distance)
        rec = self._load_human(distance, consume=True)
        if not rec:
            return None
        self._current_human_rec = rec
        return self._plan_from_human_rec(rec, distance)

    def _rebuild_human(self, distance: float) -> Optional[GesturePlan]:
        rec = self._current_human_rec
        if not rec:
            return None
        return self._plan_from_human_rec(rec, distance)

    @staticmethod
    def _plan_from_human_rec(rec: dict, distance: float) -> Optional[GesturePlan]:
        raw = rec.get("points") or []
        rec_dist = float(rec.get("distance") or distance or 0.0)
        upgraded = upgrade_points_to_cumulative(raw, rec_dist)
        scaled = points_from_recorded({**rec, "points": upgraded}, distance, tolerance=0.10)
        if not scaled:
            return None
        return GesturePlan(
            archetype=ARCHETYPE_HUMAN,
            points=scaled,
            extra_overshoot=False,
            end_hold_scale=1.0,
            source_file=rec.get("_file"),
        )


__all__ = [
    "ARCHETYPE_BALLISTIC",
    "ARCHETYPE_HUMAN",
    "ARCHETYPE_MINIMUM_JERK",
    "ARCHETYPE_OVERSHOOT",
    "ARCHETYPE_PAUSE",
    "ARCHETYPE_TREMOR",
    "FIRST_TRY_ORDER",
    "GesturePlan",
    "GestureSession",
    "SYNTHETIC_ARCHETYPES",
    "generate_archetype",
    "upgrade_points_to_cumulative",
]
