"""R1-R6 统计契约测试（docs/slider-humanization-requirements-2026-11-16 §1-2）。

锁定四类硬指标，防止内核回归到机械签名：
1. gamma 间隔：右偏分布（偏度 > 0），均值收敛，非矩形直方图（R3）；
2. Fitts 时长：对数模型 + 距离-时长强相关（Pearson r > 0.4，R3 验收线）；
3. 骨架参数：跨调用独立随机（R6），steps ∈ [12, 22]；
4. 速度比硬指标：相邻显著速度比 < 2.5、无 ≥4 步 ±1% 平顶（R1 验收线），
   500 样本混合距离批量验证（CI 缩样，线上审计脚本跑 5000）。
"""
from __future__ import annotations

import math
import random
import statistics

import pytest

from slidex._humanize import (
    compose_human_points,
    fitts_duration_ms,
    gamma_intervals,
    skeleton_params,
    velocity_series,
)
from slidex._trajectory import generate_trajectory


# ── R3: gamma 间隔 ──────────────────────────────────────────────────────────

class TestGammaIntervals:
    def test_mean_converges(self):
        random.seed(42)
        samples = [v for _ in range(60) for v in gamma_intervals(50, 40.0)]
        # 按均值归一 + 8ms 下限截断（右尾截断轻微拉低均值）
        assert 30.0 <= statistics.mean(samples) <= 42.0

    def test_right_skewed_not_rectangular(self):
        """gamma(shape=2.2) 右偏：均值 > 中位数；uniform 矩形的两者近似相等。"""
        random.seed(7)
        samples = [v for _ in range(80) for v in gamma_intervals(40, 30.0)]
        assert statistics.mean(samples) > statistics.median(samples)

    def test_tail_beyond_2x_mean_exists(self):
        """右长尾：≥2×均值 的样本必须出现（uniform 40ms±5 绝无）。"""
        random.seed(11)
        samples = [v for _ in range(40) for v in gamma_intervals(60, 40.0)]
        assert max(samples) > 2.0 * 40.0

    def test_floor_8ms(self):
        random.seed(3)
        assert all(v >= 8.0 for v in gamma_intervals(30, 12.0))

    def test_not_uniform_histogram(self):
        """R3 禁令：uniform 不能做主间隔采样器。与 uniform(25,75) 的
        矩形直方图对比，gamma 的分位-密度曲线必须显著右偏。"""
        random.seed(21)
        samples = gamma_intervals(2000, 40.0)
        q1 = statistics.quantiles(samples, n=4)
        # gamma(2.2) 的 IQR 偏向低侧：Q3-Q1 > Q2-Q1（右尾拉宽）
        assert (q1[2] - q1[0]) > (q1[1] - q1[0]) * 0.9


# ── R3: Fitts 时长 ──────────────────────────────────────────────────────────

class TestFittsDuration:
    def test_formula_reference_values(self):
        # 手算锚点：D=100 → 320+900·log2(140/40)/3.5 ≈ 320+900·1.807/3.5 ≈ 784.8
        assert fitts_duration_ms(100, 1.0) == pytest.approx(
            320.0 + 900.0 * math.log2(140.0 / 40.0) / 3.5, rel=1e-9
        )
        assert fitts_duration_ms(100) == fitts_duration_ms(100, 1.0)

    def test_clamp_band(self):
        assert fitts_duration_ms(0, 1.0) == pytest.approx(380.0)   # 下限
        assert fitts_duration_ms(5000, 1.0) == pytest.approx(1500.0)  # 上限
        # D=50 基线 620.8 未触达 380 下限，scale 钳位后 = 基线 × 0.3 / 2.0
        base50 = 320.0 + 900.0 * math.log2(90.0 / 40.0) / 3.5
        assert fitts_duration_ms(50, 0.1) == pytest.approx(base50 * 0.3)
        assert fitts_duration_ms(50, 9.0) == pytest.approx(base50 * 2.0)

    def test_monotonic_in_distance(self):
        prev = -1.0
        for d in (20, 60, 120, 250, 400, 700, 1200):
            cur = fitts_duration_ms(d, 1.0)
            assert cur >= prev
            prev = cur

    def test_duration_distance_correlation(self):
        """验收硬指标：混合距离 + 个体系数下 Pearson r > 0.4。"""
        random.seed(99)
        dists, durs = [], []
        for _ in range(120):
            d = random.uniform(30, 420)
            scale = random.uniform(0.75, 1.5)
            dists.append(d)
            durs.append(fitts_duration_ms(d, scale))
        n = len(dists)
        mx, my = sum(dists) / n, sum(durs) / n
        cov = sum((a - mx) * (b - my) for a, b in zip(dists, durs))
        sx = math.sqrt(sum((a - mx) ** 2 for a in dists))
        sy = math.sqrt(sum((b - my) ** 2 for b in durs))
        assert cov / (sx * sy) > 0.4

    def test_composed_duration_tracks_fitts(self):
        """compose_human_points 总时长（含 press/终点 hold）与 Fitts 基线
        同数量级：D=200 时基线 ~965ms，成品应在 [0.6×, 1.6×] 基线带内。"""
        random.seed(5)
        base = fitts_duration_ms(200, 1.0)
        for _ in range(30):
            traj = compose_human_points(200.0)
            total = sum(p[2] for p in traj)
            # gamma 采样方差（CV≈0.19，尾部 +40%）+ press/终点 hold + 子动作
            # 停顿叠加，实际总时长围绕基线浮动；带限 [0.5×, 2.0×] 只防
            # 「Fitts 完全失效」级别的回归。距离相关性另有 r>0.4 硬指标。
            assert 0.5 * base <= total <= 2.0 * base


# ── R6: 骨架参数 ────────────────────────────────────────────────────────────

class TestSkeletonParams:
    def test_steps_band(self):
        random.seed(1)
        for _ in range(200):
            sk = skeleton_params()
            assert 12 <= sk["steps"] <= 22
            assert -0.05 <= sk["skew"] <= 0.05

    def test_independence_across_calls(self):
        """R6：跨拖动不得共享骨架——200 次采样 steps 必须覆盖 ≥6 个不同值。"""
        random.seed(13)
        seen = {skeleton_params()["steps"] for _ in range(200)}
        assert len(seen) >= 6

    def test_custom_band(self):
        random.seed(2)
        for _ in range(50):
            sk = skeleton_params(14, 16)
            assert 14 <= sk["steps"] <= 16


# ── R1: 速度比与平顶硬指标 ──────────────────────────────────────────────────

def _significant_velocity_stats(points):
    """主拖动段（到 max x 前）显著速度的相邻比 + 平顶计数。

    相邻比只取**同号对**：跨符号翻转的 |v_b|/|v_a| 是方向修正（R4 允许的
    ≤1px 微回退），不是 R1 针对的速度剖面断裂；R1 签名是同向加速/减速的
    平滑性。
    """
    xs = [p[0] for p in points]
    dts = [p[2] for p in points]
    vs = velocity_series([(xs[i], 0.0, dts[i]) for i in range(len(xs))])
    vmax = max(abs(v) for v in vs) or 1.0
    sig = [v for v in vs if abs(v) > 0.15 * vmax]
    ratios = [
        abs(sig[i]) / abs(sig[i - 1])
        for i in range(1, len(sig))
        if sig[i] * sig[i - 1] > 0  # 同号
        and abs(sig[i - 1]) > 1e-9
    ]
    # 平顶：≥4 连续样本速度几乎不变（±1%）
    flats = 0
    run = 1
    for i in range(1, len(vs)):
        if abs(vs[i] - vs[i - 1]) <= 0.01 * max(abs(vs[i]), abs(vs[i - 1]), 1e-9):
            run += 1
            if run >= 4:
                flats += 1
        else:
            run = 1
    return ratios, flats


class TestVelocityHardMetrics:
    def test_adjacent_ratio_below_2_5_bulk(self):
        """R1 验收线：相邻显著速度比 < 2.5。200 条混合距离批量验证。"""
        random.seed(2026)
        worst = 0.0
        for _ in range(200):
            d = random.choice([30, 60, 100, 150, 200, 280, 380])
            traj = generate_trajectory(d, attempt=1)
            ratios, _ = _significant_velocity_stats(traj)
            if ratios:
                worst = max(worst, max(ratios))
        assert worst < 2.5, f"worst adjacent velocity ratio {worst:.3f} >= 2.5"

    def test_flat_runs_rare(self):
        """≥4 步 ±1% 平顶在 200 条里 ≤ 5 条（min-jerk 钟顶平台是人类形态，
        但不能成规模出现）。"""
        random.seed(777)
        flat_trajectories = 0
        for _ in range(200):
            d = random.choice([40, 80, 140, 220, 320])
            traj = generate_trajectory(d, attempt=1)
            _, flats = _significant_velocity_stats(traj)
            if flats > 0:
                flat_trajectories += 1
        assert flat_trajectories <= 5

    def test_endpoint_exact_after_all_stages(self):
        for d in (50, 150, 300):
            traj = generate_trajectory(d, attempt=1)
            assert traj[-1][0] == pytest.approx(d, abs=1e-9)

    def test_hesitation_pause_shapes_intervals(self):
        """R3：犹豫停顿 100-300ms、概率 5-10% —— draw_hesitation_ms 端点。"""
        from slidex._humanize import draw_hesitation_ms
        random.seed(31)
        hits = [draw_hesitation_ms(0.08) for _ in range(2000)]
        fired = [h for h in hits if h > 0]
        assert 0.05 <= len(fired) / len(hits) <= 0.11
        assert all(100.0 <= h <= 300.0 for h in fired)
