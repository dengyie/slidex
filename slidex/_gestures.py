"""Orthogonal slide-gesture library (0.6.28).

Five synthetic families are generated independently. They must not call
``generate_trajectory`` and must not copy the four-phase eased curve in
``_trajectory.py``. ``generate_trajectory`` itself stays untouched.

``GestureSession.next`` is the only planner the solve loops ask:
- ``last_code is None`` → first available in ``FIRST_TRY_ORDER``
- ``last_code == -1`` → do not switch (human keeps the same recording
  but re-scales points to this attempt's distance; synthetic resamples
  the same family)
- ``last_code == -2`` → upstream explicitly rejected (F001/F015 receipt):
  consume the current family/recording and switch — replaying the same
  session just feeds the risk engine more confirmation samples
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

from slidex._humanize import apply_replay_noise, compose_human_points, replay_time_scale, skeleton_params
from slidex._slide_geometry import (
    RECORDED_GAP_MIN_MS,
    normalize_recorded_delays,
    points_from_recorded,
    scale_recorded_points,
    upgrade_points_to_cumulative,
)
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

# 结果码语义（与 solver/_slide_result 对齐）：
# -1 = packet miss（结果捕获面 miss / 超时）——同录制重放或同家族重采样；
# -2 = 上游显式拒绝回执（verifyCode F001/F015，UPSTREAM_REJECT_CODE）——
#      同会话重拖无意义，换下一个家族/录制，绝不原路重放。
_PACKET_MISS = -1
_UPSTREAM_REJECT = -2


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
    """Flash-Hogan 5th-order point-to-point: bell velocity, monotonic, no overshoot.

    R1-R6 迭代后该家族直接委托 ``_humanize`` 共享内核（Fitts 时长、gamma
    间隔、子动作合成、AR(1) 路径噪声、R6 独立骨架参数），与其他引擎共用
    同一套运动学原语，不再各自维护一份 inline easing 造成统计漂移。家族
    契约靠两件事保持：clip_to_distance 抵墙 + 单调钳制（回退 ≤0.8px 的
    AR(1) 采样被压平，主段严格单调）；overshoot_probability=0 关闭 R5
    概率化过冲尾（本家族的过冲由 ARCHETYPE_OVERSHOOT 负责区分）。
    """
    sk = skeleton_params()
    pts = compose_human_points(
        distance,
        steps=sk["steps"],
        skew=sk["skew"],
        press_hold_ms=_press_hold_ms(),
        overshoot_probability=0.0,
        clip_to_distance=True,
    )
    sign = -1.0 if distance < 0 else 1.0
    dist = abs(float(distance or 0.0))
    traj: List[Point] = []
    run = 0.0
    for i, (x, _y, d) in enumerate(pts):
        run = min(max(run, abs(x)), dist)
        traj.append((sign * run, 0.0 if i == len(pts) - 1 else _y, d))
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
        self._replay_template: Optional[List[List[float]]] = None
        self._replay_template_dist: float = 0.0

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

        if last_code == _UPSTREAM_REJECT:
            # R7：上游显式拒绝（F001/F015 回执）。与 packet miss 区分——
            # 同会话原路重放等于给风控追加确认样本，必须 consume 当前
            # 家族/录制后换下一个（human 耗尽则落到合成家族）。
            if self._current_archetype and self._current_archetype != ARCHETYPE_HUMAN:
                self._used_archetypes.add(self._current_archetype)
            self._current_plan = None
            self._current_archetype = None
            self._current_human_rec = None
            logger.debug(
                f"gesture session: code=-2 upstream rejected — "
                f"switching family (was {self._current_archetype})"
            )

        if last_code is not None and last_code != _PACKET_MISS:
            # 合成家族 consume 后永不回绕。human 不在这里预判耗尽：_first_available
            # 会以完全相同的输入（_used_human_files 未变）再探测一次，这里提前判
            # 等于每次重试多扫一遍全池；耗尽时两个入口殊途同归地跳过 human。
            if self._current_archetype and self._current_archetype != ARCHETYPE_HUMAN:
                self._used_archetypes.add(self._current_archetype)
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
                if not getattr(self.pool, "enabled", True):
                    continue
                if getattr(self.pool, "recorded_only_mode", False):
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
                    # 探测（consume=False）不能按消费记 INFO / 写 last_used：
                    # 否则一次求解打两条一样的「loaded」，peek 还会推进 LRU 时钟
                    # （2026-11-11 23:09 生产日志实证）。
                    peek=not consume,
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
        self._replay_template = None  # 新录制 → 重新个体化（R8）
        return self._plan_from_human_rec(rec, distance)

    def _rebuild_human(self, distance: float) -> Optional[GesturePlan]:
        rec = self._current_human_rec
        if not rec:
            return None
        return self._plan_from_human_rec(rec, distance)

    def _plan_from_human_rec(self, rec: dict, distance: float) -> Optional[GesturePlan]:
        file = rec.get("_file")
        template = self._replay_template
        if template is not None:
            # code=-1 重放：个体化模板（已含时间缩放 + AR(1) 噪声）按新
            # 距离重标定，同录制同距离的重复重放逐点一致（重放契约）。
            rescaled = scale_recorded_points(
                template, self._replay_template_dist, distance, tolerance=0.10
            )
            return GesturePlan(
                archetype=ARCHETYPE_HUMAN,
                points=rescaled,
                extra_overshoot=False,
                end_hold_scale=1.0,
                source_file=file,
            )
        raw = rec.get("points") or []
        rec_dist = float(rec.get("distance") or distance or 0.0)
        upgraded = upgrade_points_to_cumulative(raw, rec_dist)
        timed = normalize_recorded_delays(upgraded)
        if not timed:
            return None
        scaled = points_from_recorded({**rec, "points": timed}, distance, tolerance=0.10)
        if not scaled:
            return None
        # R8：回放个体化——真人录制按 ×0.8-1.25 时间缩放（同一录制不再以
        # 恒定节奏回放）+ AR(1) 路径噪声叠加（σ≈0.35px，终点强制回目标位）。
        # 缩放后逐点钳回采样率下限（RECORDED_GAP_MIN_MS）：归一化把密集录制
        # 贴到 8ms 下限，×0.8 会压到 6.4ms——采样率冲出设计带、与「真人
        # 采样密度上限」的池契约冲突。
        ts = replay_time_scale()
        gap_min = RECORDED_GAP_MIN_MS
        scaled = [[p[0], p[1], max(float(p[2]) * ts, gap_min)] for p in scaled]
        scaled = apply_replay_noise(scaled, distance)
        self._replay_template = scaled
        self._replay_template_dist = distance
        logger.info(
            f"gesture session: human replay source=pool:{os.path.basename(str(file))} "
            f"time_scale={ts:.2f}"
        )
        return GesturePlan(
            archetype=ARCHETYPE_HUMAN,
            points=scaled,
            extra_overshoot=False,
            end_hold_scale=1.0,
            source_file=file,
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
