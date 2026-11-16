#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""滑块轨迹生成 — legacy 公开 API，委托 ``_humanize`` 拟人运动学内核。

需求 docs/slider-humanization-requirements-2026-11-16 §2：旧四阶段分段
easing（中段 20%–60% 纯线性匀速 + 三个一阶不连续折角）是滑块风控最经典
的统计签名，全量替换为共享内核的 minimum-jerk 母线 + Fitts 时长 + gamma
间隔 + 子动作合成 + 生理性震颤。本模块只保留参数翻译与 API 兼容层。
"""

import random
from typing import List, Tuple

from loguru import logger

from slidex._humanize import compose_human_points

def generate_trajectory(
    distance: float,
    attempt: int = 1,
    press_hold_ms: float = 0.0,
    overshoot_back: bool = False,
) -> List[Tuple[float, float, float]]:
    """
    生成人类化滑动轨迹（minimum-jerk 内核，R1-R6 全条目）。

    返回: [(x, y, delay_ms), ...]  相对位移 + 步骤间延迟(ms)

    press_hold_ms: 按下后保持不动的时长（真实人手按下到开始拖动的延迟，
        kanxue 成功案例用 1200ms；0 走默认 100-200ms 起始停顿）
    overshoot_back: 终点先过冲再回拖（收尾减速与回拖修正，释放前模拟
        人手抖动）；False 时仍按 R5 的 30-60% 概率出现概率化过冲
    attempt: 重试轮次——旧语义放大 Y 抖动（attempt 越大抖动越大），内核
        jitter_amp 按 attempt 线性放大保留该语义
    """
    traj = compose_human_points(
        distance,
        press_hold_ms=float(press_hold_ms) if press_hold_ms > 0 else 0.0,
        overshoot_back=bool(overshoot_back),
        jitter_amp=1.0 + max(0, attempt - 1) * 0.8,
    )

    # API 兼容：部分调用方假定每步 delay < 200ms（旧 uniform(25,75) 语义）。
    # 内核 gamma 长尾/犹豫停顿可能超 200ms——>200ms 的样本拆成多个 <200ms
    # 的连续点。位移按各段时间占比在「前一样本位置 → 目标位置」间线性
    # 插值：v = dx/dt 守恒（若整段位移挂第一段，该样本速度虚增拆分倍数）。
    bounded: List[Tuple[float, float, float]] = []
    prev_x = prev_y = 0.0
    for x, y, delay in traj:
        d = float(delay)
        n_full = int(d // 195.0)
        if n_full == 0:
            bounded.append((x, y, max(1.0, d)))
            prev_x, prev_y = float(x), float(y)
            continue
        rem = d - n_full * 195.0
        chunks = [195.0] * n_full
        if rem >= 1.0:
            chunks.append(rem)
        else:
            chunks[-1] += rem
        total = sum(chunks)
        covered = 0.0
        for c in chunks[:-1]:
            covered += c
            frac = covered / total
            bounded.append((prev_x + (x - prev_x) * frac,
                            prev_y + (y - prev_y) * frac, c))
        bounded.append((x, y, chunks[-1]))
        prev_x, prev_y = float(x), float(y)

    total_ms = sum(d for _, _, d in bounded)
    logger.debug(
        f"trajectory: dist={distance:.0f}px, steps={len(bounded)}, "
        f"time={total_ms:.0f}ms, attempt={attempt}"
    )

    return bounded


def trajectory_to_points(
    trajectory: List[Tuple[float, float, float]],
    start_x: float,
    start_y: float,
) -> List[Tuple[float, float, float]]:
    """将相对轨迹转为绝对坐标"""
    return [(start_x + x, start_y + y, d) for x, y, d in trajectory]


__all__ = ['generate_trajectory', 'trajectory_to_points']


def slide_end_hold_range() -> Tuple[float, float]:
    """0.6.18 真人要领（用户 2026-09-25 实测）：拖到终点滑块变绿后不能立刻
    松开鼠标——验证在松键时刻评估，到达即松会被拒（code=300 世界）。
    自动轨迹必须在终点握住随机时长再 mouseup。env 可调（秒）。"""
    from slidex._env import env_float

    lo = max(0.0, env_float("SLIDEX_SLIDE_END_HOLD_MIN", 0.45))
    hi = max(0.0, env_float("SLIDEX_SLIDE_END_HOLD_MAX", 1.10))
    return (lo, max(lo, hi))


__all__ = ['generate_trajectory', 'trajectory_to_points', 'slide_end_hold_range']
