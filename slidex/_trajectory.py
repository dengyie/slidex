#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""滑块轨迹生成 — 基于物理模型的四阶段轨迹算法"""

import random
import math
from typing import List, Tuple
from loguru import logger


def generate_trajectory(
    distance: float,
    attempt: int = 1,
    press_hold_ms: float = 0.0,
    overshoot_back: bool = False,
) -> List[Tuple[float, float, float]]:
    """
    生成人类化滑动轨迹

    返回: [(x, y, delay_ms), ...]  相对位移 + 步骤间延迟(ms)

    press_hold_ms: 按下后保持不动的时长（真实人手按下到开始拖动的延迟，
        kanxue 成功案例用 1200ms；0 走默认 100-200ms 起始停顿）
    overshoot_back: 终点先过冲 3-6px 再回拖（收尾减速与回拖修正，
        释放前模拟人手抖动）
    """
    traj = []

    # === 起始停顿 ===
    if press_hold_ms > 0:
        traj.append((0, 0, press_hold_ms))
    else:
        traj.append((0, 0, random.uniform(100, 200)))

    # === 滑动阶段 ===
    steps = random.randint(10, 15)
    jitter = 2.0 + attempt * 0.8

    # 四阶段：slow_start(0-20%) -> fast(20-60%) -> medium(60-85%) -> fine_tune(85-100%)
    for i in range(steps):
        p = (i + 1) / steps

        if p <= 0.20:
            t = p / 0.20
            eased = 0.02 + 0.13 * (t ** 1.8)
        elif p <= 0.60:
            t = (p - 0.20) / 0.40
            eased = 0.15 + 0.60 * t
        elif p <= 0.85:
            t = (p - 0.60) / 0.25
            eased = 0.75 + 0.20 * t
        else:
            t = (p - 0.85) / 0.15
            eased = 0.95 + 0.05 * t

        x = distance * eased

        # Y 轴向下漂移 + 正弦抖动
        drift = -0.5 - p * 4.0
        y = drift + math.sin(p * math.pi * random.uniform(1.5, 3.5)) * jitter * (0.4 + 0.6 * p)

        # 随机尖刺
        if random.random() < 0.06:
            y += random.uniform(-jitter * 1.8, jitter * 1.8)

        # 步骤间延迟
        if i == 0:
            delay = random.uniform(35, 55)
        elif i >= steps - 2:
            delay = random.uniform(40, 60)
        elif random.random() < 0.07:
            delay = random.uniform(55, 75)
        else:
            delay = random.uniform(25, 45)

        traj.append((x, y, delay))

    # === 终点停顿 ===
    traj.append((distance, 0, random.uniform(50, 120)))

    # === 过冲回拖 + 释放前抖动（模拟人手收尾修正） ===
    if overshoot_back:
        over = random.uniform(3.0, 6.0)
        back = random.uniform(2.0, 3.5)
        jitter = random.uniform(-1.0, 1.0)
        # 过冲点 → 回拖点 → 释放位（带 ±1px 手抖）
        traj.append((distance + over, random.uniform(-1.5, 1.5), random.uniform(60, 110)))
        traj.append((distance + over - back, random.uniform(-1.0, 1.0), random.uniform(50, 90)))
        traj.append((distance + jitter, 0.0, random.uniform(40, 80)))

    total_ms = sum(d for _, _, d in traj)
    logger.debug(
        f"trajectory: dist={distance:.0f}px, steps={len(traj)}, "
        f"time={total_ms:.0f}ms, attempt={attempt}"
    )

    return traj


def trajectory_to_points(
    trajectory: List[Tuple[float, float, float]],
    start_x: float,
    start_y: float,
) -> List[Tuple[float, float, float]]:
    """将相对轨迹转为绝对坐标"""
    return [(start_x + x, start_y + y, d) for x, y, d in trajectory]


__all__ = ['generate_trajectory', 'trajectory_to_points']
