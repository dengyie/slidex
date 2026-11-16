#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拟人运动学内核 — 需求 docs/slider-humanization-requirements-2026-11-16.zh-CN.md

z.ai 实证（2026-11-16）：几何全对（conf 0.97）仍被 F001 拒——被拒原因在
运动学统计签名，不在落点。本模块把人手运动学（minimum-jerk 主导 + 生理性
震颤 + Fitts 定律时长 + gamma 间隔 + 子动作修正）收敛成一套共享原语，
供两条轨迹引擎复用，消除双引擎漂移：

- ``slidex._trajectory.generate_trajectory``（legacy / 公开 API）
- ``slidex._gestures`` 合成家族（provider 模式，z.ai 实际执行路径）

核心构造（修复速度尖峰的根因）：位移采样按**时间比例**取 min-jerk 曲线
（τ 网格 = gamma 间隔的累计归一），dx ∝ dt·f'(τ)——大 dt 配大步、小 dt 配
小步，测得速度 = f'(τ) 与采样率解耦，天然无尖峰。犹豫/子动作停顿一律是
**零位移 hold 点**，不进入运动网格（进入就会制造 dx∝dt 的伪速度尖峰）。

设计红线（验收硬指标，见 tests/test_humanize.py）：
- 显著速度（>0.15·vmax）相邻比 < 2.5；无 ≥4 步 ±1% 匀速连段
- 间隔分布近似 gamma（右偏、长尾），禁用 uniform 作主采样
- 总时长 ∝ log2(D/W)（Fitts），个体速度人设由调用方会话内保持一致
- Y 轴 8-12Hz 多模态阻尼叠加；X 轴 AR(1) 路径噪声，终点收敛
"""

import math
import random
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float, float]


# ── R1: minimum-jerk 母线 ────────────────────────────────────────────────────

def minimum_jerk_ease(tau: float) -> float:
    """Flash & Hogan 五次点对点曲线：10τ³−15τ⁴+6τ⁵。

    速度钟形、加速度处处连续、起终点加速度为零——替代四阶段分段
    easing 的三个硬拐点（一阶不连续是经典统计签名）。
    """
    t = min(max(float(tau), 0.0), 1.0)
    return t * t * t * (10.0 + t * (-15.0 + 6.0 * t))


def asymmetric_tau(tau: float, skew: float) -> float:
    """±5% 速度不对称（前段慢/后段慢个体差异）：幂函数 warp τ。

    skew<0 → 前段慢（后段快）；skew>0 → 前段快。|skew| ≤ 0.15，
    端点不动（0→0, 1→1），单调性保持。
    """
    p = 1.0 + float(skew)
    t = min(max(float(tau), 0.0), 1.0)
    if abs(p - 1.0) < 1e-9:
        return t
    return t ** p


# ── R3: Fitts 定律时长 + gamma 间隔 ─────────────────────────────────────────

def fitts_duration_ms(distance: float, speed_scale: float = 1.0) -> float:
    """总时长基线 T = clamp(320 + 900·log2((D+40)/40)/3.5, 380, 1500) ms。

    speed_scale 为个体速度系数（0.75 快手 … 1.5 慢手），同一会话内一致
    （GestureSession 持有），跨会话独立采样。
    """
    d = abs(float(distance or 0.0))
    base = 320.0 + 900.0 * math.log2((d + 40.0) / 40.0) / 3.5
    base = min(1500.0, max(380.0, base))
    return base * min(2.0, max(0.3, float(speed_scale or 1.0)))


def gamma_intervals(n: int, mean_ms: float) -> List[float]:
    """事件间隔从 gamma(shape=2.2) 采样，按均值归一——右偏、长尾。

    替代 random.uniform 的矩形直方图。下限 8ms（浏览器事件循环粒度）。
    """
    n = max(1, int(n))
    mean = max(8.0, float(mean_ms))
    shape = 2.2
    scale = mean / shape
    raw = [random.gammavariate(shape, scale) for _ in range(n)]
    total = sum(raw) or 1.0
    return [max(8.0, v * mean * n / total) for v in raw]


def draw_hesitation_ms(probability: float = 0.08) -> float:
    """5-10% 概率抽中 100-300ms 犹豫停顿（零位移 hold 点），否则 0。"""
    if random.random() < probability:
        return random.uniform(100.0, 300.0)
    return 0.0


# ── R4: 震颤与路径噪声 ──────────────────────────────────────────────────────

def tremor_offset(t_ms: float, duration_ms: float) -> float:
    """Y 轴生理性震颤：2-3 个 8-12Hz 阻尼振荡叠加（幅度 0.4-1.2px，随机相位）。

    频谱宽带多模态，替代解析单正弦的单一谱线尖峰。包络指数衰减，
    终点处趋零（配合调用方的终点收敛）。
    """
    dur = max(1.0, float(duration_ms))
    t = float(t_ms)
    y = 0.0
    for _ in range(random.randint(2, 3)):
        freq = random.uniform(8.0, 12.0)
        amp = random.uniform(0.4, 1.2)
        phase = random.uniform(0.0, 2.0 * math.pi)
        decay = math.exp(-2.2 * t / dur)
        y += amp * decay * math.sin(2.0 * math.pi * freq * t / 1000.0 + phase)
    return y


def ar1_noise(n: int, sigma: float = 0.4, rho: float = 0.7) -> List[float]:
    """X 轴 AR(1) 路径噪声（σ≈0.4px，ρ≈0.7）：真实指点对理想路径的偏离。

    末 3 点线性收敛到零（调用方把末点强制回目标位，避免终点飞出）。
    """
    n = max(1, int(n))
    out: List[float] = []
    prev = 0.0
    sd = float(sigma) * math.sqrt(max(1e-9, 1.0 - float(rho) * float(rho)))
    for _ in range(n):
        prev = float(rho) * prev + random.gauss(0.0, sd)
        out.append(prev)
    taper_n = min(3, n)
    for i in range(n - taper_n, n):
        k = (i - (n - taper_n) + 1) / (taper_n + 1)
        out[i] *= (1.0 - k)
    return out


def nudge_monotonic(xs: Sequence[float], max_back: float = 0.8) -> List[float]:
    """约束 X 序列的回退量 ≤ max_back px（人手采样抖动上限，非机器零回退）。

    与池质检 ``_x_monotonic_forward`` 的 1px 容差对齐——超过它会被判
    非人类几何拒收入池/拒回放。
    """
    out: List[float] = []
    prev = float("-inf")
    for x in xs:
        x = float(x)
        if out and x < prev - max_back:
            x = prev - max_back
        out.append(x)
        prev = x
    return out


# ── R2: 子动作（sub-movement）合成 ─────────────────────────────────────────

def sample_correction_plan() -> int:
    """修正次数抽样：0 次 ~25%，1 次 ~50%，2 次 ~25%。"""
    r = random.random()
    if r < 0.25:
        return 0
    if r < 0.75:
        return 1
    return 2


def submovement_spans(distance: float) -> List[Tuple[float, float]]:
    """把总距离切成 2-4 个前向衔接的子动作区间 [(start_frac, end_frac)]。

    首子动作 ballistic 覆盖 85-98%；其余修正子动作各覆盖剩余的 50-100%，
    末段强制到 1.0。全前向 → x 单调不降（修正子动作是"续走"不是回拉）。
    distance<=0 时返回单段 [(0,1)]。
    """
    if float(distance or 0.0) <= 0:
        return [(0.0, 1.0)]
    n_corr = sample_correction_plan()
    spans: List[Tuple[float, float]] = []
    pos = random.uniform(0.85, 0.98)
    spans.append((0.0, pos))
    for i in range(n_corr):
        remaining = 1.0 - pos
        if remaining <= 1e-6:
            break
        cover = random.uniform(0.5, 1.0)
        if i == n_corr - 1:
            cover = 1.0  # 末段必须到 1.0
        pos = pos + remaining * cover
        spans.append((spans[-1][1], min(1.0, pos)))
    if not spans or spans[-1][1] < 1.0:
        spans.append((spans[-1][1] if spans else 0.0, 1.0))
    return spans


def submovement_pause_ms() -> float:
    """子动作之间 30% 概率停 0-180ms，否则 10-40ms 自然衔接（hold 点）。"""
    if random.random() < 0.30:
        return random.uniform(0.0, 180.0)
    return random.uniform(10.0, 40.0)


# ── R5: 过冲概率化 ─────────────────────────────────────────────────────────

def maybe_overshoot_points(
    distance: float,
    *,
    probability: float = 0.45,
    max_double: float = 0.15,
) -> List[Point]:
    """终点过冲回拖点序（概率化）：出现概率由调用方给（30-60%）；
    幅度 uniform(2,9)px、回拖 uniform(1,4)px；15% 概率两次修正。

    幅度按 |distance| 缩短（短距拖动过冲 ≤12% 路程，cap 30px）——固定
    9px 在 30px 短距上等于 30% 路程的猛烈折返，制造速度尖峰。
    末段回 distance 的距离 > 3px 时拆成两段（速度比约束内的连贯回拖），
    避免单步大回拉。
    """
    if random.random() >= probability:
        return []
    dist_abs = abs(float(distance))
    amp_cap = min(1.0, dist_abs / 60.0) if dist_abs > 0 else 1.0
    rounds = 2 if random.random() < max_double else 1
    pts: List[Point] = []
    pos = float(distance)
    for i in range(rounds):
        over = random.uniform(2.0, 9.0) * amp_cap
        back = random.uniform(1.0, 4.0) * amp_cap
        pts.append((pos + over, random.uniform(-1.5, 1.5), random.uniform(60.0, 110.0)))
        settle = pos + over - back
        if i == rounds - 1:
            settle = pos + random.uniform(-0.8, 0.8) * amp_cap  # 释放前手抖，不再修正
        pts.append((settle, random.uniform(-1.0, 1.0), random.uniform(50.0, 90.0)))
        pos = settle
    # 回 distance：单步 > 3px 拆两段，回拖速度与过冲段同量级
    ret_dx = float(distance) - pos
    ret_dt = random.uniform(40.0, 80.0)
    if abs(ret_dx) > 3.0:
        first_dt = ret_dt * random.uniform(0.45, 0.6)
        pts.append((pos + ret_dx * 0.55, random.uniform(-0.8, 0.8), first_dt))
        pts.append((float(distance), 0.0, ret_dt - first_dt))
    else:
        pts.append((float(distance), 0.0, ret_dt))
    return pts


# ── R6: 骨架多样性 ─────────────────────────────────────────────────────────

def persona_speed_scale() -> float:
    """个体速度人设系数（0.75 快手 … 1.5 慢手）。GestureSession 构造时采一次，
    会话内复用；独立调用方每次 fresh。"""
    return random.uniform(0.75, 1.5)


def skeleton_params(steps_lo: int = 12, steps_hi: int = 22) -> dict:
    """一次拖动的独立骨架参数（R6：跨会话/同会话重试均不得共享）。"""
    return {
        "steps": random.randint(steps_lo, steps_hi),
        "skew": random.uniform(-0.05, 0.05),
    }


# ── 速度工具（测试与构建共用）──────────────────────────────────────────────

def velocity_series(points: Sequence[Sequence[float]]) -> List[float]:
    """逐样本事件速度 px/ms：v_i = (x_i − x_{i−1}) / delay_i。

    delay 语义与 ``_drag.build_drag_events`` 对齐：delay_i 是第 i 个
    move 派发**之前**的等待，所以第 i 段位移的真实速度配 delay_i。
    """
    out: List[float] = []
    for i in range(1, len(points)):
        dx = float(points[i][0]) - float(points[i - 1][0])
        dt = float(points[i][2])
        out.append(dx / dt if dt > 0 else 0.0)
    return out


def bridge_cap(xs: List[float], dts: Sequence[float], ratio_limit: float = 2.4) -> None:
    """终态速度比打磨（就地修改 xs）：显著速度相邻比 > ratio_limit 时收缩后者。

    与 ``_cap_velocity_ratios``（生成期、dx/dt 配对数组）不同，本函数跑在
    xs 终态（噪声 + nudge + 收敛之后），以与 ``velocity_series`` 完全一致的
    配对 (x_i−x_{i−1})/dts_i 检查——两套配对错位正是 cap 看不见尖峰的根因。
    显著 = |v| > 0.15·vmax；跨过不显著样本的桥接由 last_sig_v 记忆实现
    （hold/gamma 长尾后的短间隔大步会被压回 2.4×前显著速度）。
    """
    n = len(xs)
    if n < 3:
        return
    vmax = max(
        abs((xs[i] - xs[i - 1]) / dts[i])
        for i in range(1, n)
        if dts[i] > 0
    ) if any(d > 0 for d in dts[1:]) else 0.0
    if vmax <= 0:
        return
    sig = 0.15 * vmax
    last_sig_v: Optional[float] = None
    for i in range(1, n):
        dt = dts[i]
        if dt <= 0:
            continue
        v = (xs[i] - xs[i - 1]) / dt
        if (
            last_sig_v is not None
            and v * last_sig_v > 0
            and abs(v) > abs(last_sig_v) * ratio_limit
        ):
            target = last_sig_v * ratio_limit * dt
            xs[i] = xs[i - 1] + (target if v > 0 else -target)
            v = target / dt
        if abs(v) > sig:
            last_sig_v = v


def _cap_velocity_ratios(dxs: List[float], dts: List[float], ratio_limit: float = 2.4) -> float:
    """显著速度相邻比超限时收缩后者的 dx（永不放大），返回未偿位移赤字。

    显著 = |v| > 0.15·局部峰值速度。赤字由后续样本偿还（偿还同样受比
    限制约束），残余落到终点收敛段。返回值 ≥ 0。
    """
    if not dxs:
        return 0.0
    vmax = max(abs(dx / dt) for dx, dt in zip(dxs, dts) if dt > 0) if any(
        dt > 0 for dt in dts
    ) else 0.0
    if vmax <= 0:
        return 0.0
    sig = 0.15 * vmax
    deficit = 0.0
    prev_v = 0.0
    for i, (dx, dt) in enumerate(zip(dxs, dts)):
        if dt <= 0:
            continue
        v = (dx + deficit) / dt
        capped = dx + deficit
        if (
            abs(prev_v) > sig
            and abs(v) > sig
            and prev_v * v > 0
            and abs(v) > abs(prev_v) * ratio_limit
        ):
            capped = prev_v * ratio_limit * dt
        deficit = max(0.0, (dx + deficit) - capped)
        dxs[i] = capped
        prev_v = capped / dt
    return deficit


# ── R8: 真人池回放增强 ─────────────────────────────────────────────────────

def replay_time_scale() -> float:
    """真人录制回放的时间缩放因子（×0.8-1.25，个体节奏差异）。"""
    return random.uniform(0.8, 1.25)


def apply_replay_noise(
    points: Sequence[Sequence[float]],
    distance: float,
    sigma: float = 0.35,
) -> List[List[float]]:
    """对回放轨迹叠加 AR(1) 路径噪声（σ≈0.35px），末点强制回 distance。

    输入/输出均为池格式 [[dx, dy, delay_ms], ...]（累计位移语义）。
    噪声后 nudge（回退 ≤0.8px），不改动 delay。
    """
    pts = [[float(p[0]), float(p[1]) if len(p) > 1 else 0.0,
            float(p[2]) if len(p) > 2 else 0.0] for p in points]
    n = len(pts)
    if n < 3 or sigma <= 0:
        return pts
    noise = ar1_noise(n, sigma=sigma)
    out: List[List[float]] = []
    for i, p in enumerate(pts):
        out.append([p[0] + noise[i], p[1], p[2]])
    xs = nudge_monotonic([p[0] for p in out])
    for i, p in enumerate(out):
        p[0] = xs[i]
    # 噪声 + nudge 钳制会把"源里确实移动了"的样本压成与前一点完全重合
    # （x、y 双双相等）——回放时这段被计成死停顿（recorded_motion_ms 不计），
    # 纯位移段时间缩水、采样率冲出设计带。真人录制不会产生逐位重合的连续
    # 采样：对源里确实前进、输出却重合的点，前推 0.02px（远小于池质检
    # 1px 容差）。
    for i in range(1, n):
        moved_src = pts[i][0] - pts[i - 1][0] > 1e-6 or pts[i][1] - pts[i - 1][1] > 1e-6
        dup_out = (abs(out[i][0] - out[i - 1][0]) <= 1e-6
                   and abs(out[i][1] - out[i - 1][1]) <= 1e-6)
        if moved_src and dup_out:
            out[i][0] = out[i - 1][0] + 0.02
    out[-1][0] = float(distance)  # 终点回目标位（几何契约）
    return out


# ── 总装 ───────────────────────────────────────────────────────────────────

def compose_human_points(
    distance: float,
    *,
    speed_scale: Optional[float] = None,
    steps: Optional[int] = None,
    skew: Optional[float] = None,
    press_hold_ms: float = 0.0,
    overshoot_back: bool = False,
    overshoot_probability: Optional[float] = None,
    jitter_amp: float = 1.0,
    submovements: bool = True,
    noise_sigma: float = 0.4,
    clip_to_distance: bool = False,
) -> List[Point]:
    """拟人拖动点序总装：[(x, y, delay_ms), ...]，相对按下点。

    组合 R1（min-jerk 母线 + ±5% 不对称）、R2（2-4 子动作）、R3（Fitts
    时长 + gamma 间隔 + 犹豫 hold）、R4（震颤 + AR(1) 路径噪声，末 3 点
    收敛）、R6（骨架参数独立随机）。

    distance==0：只保留起始停顿与终点（契约：所有 x=0）。
    distance<0：镜像生成（x 取负，终点 = distance）。
    clip_to_distance：把 x 夹到 [0, |distance|]（min-jerk 家族用——保持
    单调与 max==distance 的家族契约；噪声越界被夹平是"抵墙"自然形态）。
    """
    press = float(press_hold_ms) if press_hold_ms > 0 else random.uniform(100.0, 200.0)
    traj: List[Point] = [(0.0, 0.0, press)]
    raw_dist = float(distance or 0.0)
    if raw_dist == 0:
        traj.append((0.0, 0.0, random.uniform(50.0, 120.0)))
        return traj
    sign = 1.0
    dist = raw_dist
    if dist < 0:
        sign, dist = -1.0, -dist

    scale = float(speed_scale) if speed_scale is not None else persona_speed_scale()
    if steps is None:
        steps = random.randint(12, 22)
    if skew is None:
        skew = random.uniform(-0.05, 0.05)
    steps = max(10, int(steps))

    total_ms = fitts_duration_ms(dist, scale)
    move_budget = max(200.0, total_ms - press - random.uniform(50.0, 120.0))

    # 子动作切分 → 每段自己的 min-jerk 小曲线；时长/点数按 d^0.6 分配
    # （修正段单位距离更慢，匹配人手"末段精细修正"）
    spans = submovement_spans(dist) if submovements else [(0.0, 1.0)]
    seg_lens = [(e - s) for (s, e) in spans]
    weight = sum(l ** 0.6 for l in seg_lens) or 1.0
    seg_steps = [max(4, round(steps * (l ** 0.6) / weight)) for l in seg_lens]
    while sum(seg_steps) > steps + len(seg_steps) * 2:
        i = seg_steps.index(max(seg_steps))
        seg_steps[i] -= 1
    while sum(seg_steps) < steps:
        i = seg_steps.index(min(seg_steps))
        seg_steps[i] += 1
    seg_times = [move_budget * (l ** 0.6) / weight for l in seg_lens]

    # 运动网格：每段内部 τ = gamma 间隔累计归一（时间比例采样——核心）
    dxs: List[float] = []
    dts: List[float] = []
    kinds: List[str] = []  # "move" | "hold"
    for (s_frac, e_frac), n_seg, seg_t in zip(spans, seg_steps, seg_times):
        if s_frac > 0 and submovements:
            pause = submovement_pause_ms()
            if pause > 0:
                dxs.append(0.0)
                dts.append(pause)
                kinds.append("hold")
        span_d = (e_frac - s_frac) * dist
        intervals = gamma_intervals(n_seg, seg_t / n_seg)
        t_cum = 0.0
        t_total = sum(intervals)
        eased_prev = 0.0
        for k in range(n_seg):
            t_cum += intervals[k]
            tau = min(1.0, t_cum / t_total)
            eased = minimum_jerk_ease(asymmetric_tau(tau, skew))
            dxs.append(span_d * (eased - eased_prev))
            dts.append(intervals[k])
            kinds.append("move")
            eased_prev = eased

    # 速度比 cap 全序列统一做（跨子动作边界的过渡也要受约束），残余赤字
    # 由均匀重标定吸收：整体缩放所有 dx 不改变相邻速度比，终点精确落位。
    _cap_velocity_ratios(dxs, dts)
    cum: List[float] = []
    pos = 0.0
    for dx in dxs:
        pos += dx
        cum.append(pos)
    if cum[-1] <= 1e-6:
        cum[-1] = 1e-6
    k_scale = dist / cum[-1]
    cum = [c * k_scale for c in cum]

    # X：AR(1) 路径噪声 + 回退约束。
    # 噪声必须在**速度比 cap 之前**并入 cum：AR(1) 噪声按 √(dt/mean) 加权后，
    # 大 dt 样本上的噪声跳变仍会制造 cap 看不见的新尖峰（实测 max 相邻比
    # 2.94 > 2.5）——先并噪声再统一 cap，cap 的赤字偿还覆盖噪声跳变。
    n = len(cum)
    noise = ar1_noise(n, sigma=noise_sigma) if n > 3 else [0.0] * n
    mean_dt = sum(dts) / max(1, len(dts))
    xs = [
        cum[i] + noise[i] * math.sqrt(max(dts[i], 1.0) / mean_dt)
        for i in range(n)
    ]
    if clip_to_distance:
        xs = [min(max(x, 0.0), dist) for x in xs]
    xs = nudge_monotonic(xs, max_back=0.8)

    # cap #2：噪声并入后的终态速度比打磨。赤字由后续样本偿还，整体缩放
    # 回终点精确落位（缩放不改变相邻速度比）。
    f_dxs = [xs[i] - xs[i - 1] for i in range(1, n)]
    _cap_velocity_ratios(f_dxs, dts)
    pos = 0.0
    for i, dx in enumerate(f_dxs):
        pos += dx
        xs[i + 1] = pos
    k_end = dist / (xs[-1] or 1.0)
    xs = [v * k_end for v in xs]

    # 终点收敛（R4：mouseup 前 3-4 点收敛到目标 ±0.5px）。cap #2 后残余
    # 只有缩放残差 + 终点强制项，量级 ≤~1px，摊进末 3 点不产生速度尖峰。
    if n >= 3:
        residual = max(-1.0, min(5.0, dist - xs[-1]))
        for frac, idx in ((0.2, n - 3), (0.45, n - 2), (1.0, n - 1)):
            xs[idx] = min(xs[idx] + residual * frac, dist if idx == n - 1 else dist + 1.0)
        xs[-1] = dist
    else:
        xs.append(dist)
        dts.append(random.uniform(30.0, 60.0))
        kinds.append("move")

    # ±2.5% 的微抖打破极少数 gamma 抽样偶然形成的 ≥4 步平顶；随后再做终态
    # 速度 cap，保证时序扰动不会破坏相邻速度比。
    for i in range(len(dts)):
        dts[i] *= random.uniform(0.975, 1.025)

    # Y：缓慢下漂 + 震颤（时间驱动）+ attempt 尖刺
    t_abs = press
    ys: List[float] = []
    for i in range(len(xs)):
        t_abs += dts[i]
        p = (i + 1) / max(1, len(xs))
        drift = -0.5 - p * 4.0
        y = drift + tremor_offset(t_abs, move_budget) * jitter_amp
        if random.random() < 0.06:
            y += random.uniform(-jitter_amp * 1.8, jitter_amp * 1.8)
        ys.append(y)

    # 装配：xs/dts/ys 均按内部样本一一对应（hold 的 dx=0 → xs[i]=前一点位置，
    # 天然零位移），无需游标。错位会把位移推迟一个样本，制造速度尖峰。
    traj.extend((sign * xs[i], ys[i], dts[i]) for i in range(len(xs)))
    # 终点停顿（hold 到目标位再进入收尾）
    traj.append((sign * dist, 0.0, random.uniform(50.0, 120.0)))

    # 过冲回拖（R5 概率化）：overshoot_back=True 强制；否则按 30-60% 概率
    if overshoot_back:
        tail = maybe_overshoot_points(sign * dist, probability=1.0, max_double=0.15)
    elif overshoot_probability is not None and overshoot_probability > 0:
        tail = maybe_overshoot_points(sign * dist, probability=overshoot_probability)
    elif overshoot_probability == 0:
        tail = []
    else:
        tail = maybe_overshoot_points(sign * dist, probability=random.uniform(0.30, 0.60))
    traj.extend(tail)

    # 终态打磨（含过冲尾段）：以 velocity_series 同款配对 (x_i−x_{i−1})/dt_i
    # 收缩残余速度尖峰（噪声 + gamma 长尾 + 收敛 splice + 过冲回拖的联合
    # 产物）。cap 的显著阈值依赖全局 vmax，每轮收缩后阈值可能下降，迭代
    # 到稳定（有界）。cap 收缩会让个别步 dx 变负（噪声翻号），人手主拖动段
    # 的采样回退上限与 nudge/池质检对齐（≤0.8px），nudge 又会轻微改速度——
    # 两者交替迭代到不动点；过冲尾段（peak 之后）的刻意回拖不受约束。
    full_xs = [p[0] for p in traj]
    full_dts = [p[2] for p in traj]
    for _ in range(40):
        before = full_xs[:]
        bridge_cap(full_xs, full_dts)
        peak = max(full_xs)
        nudged = nudge_monotonic(full_xs[: full_xs.index(peak) + 1], max_back=0.8)
        for i, v in enumerate(nudged):
            full_xs[i] = v
        if all(abs(a - b) <= 1e-9 for a, b in zip(before, full_xs)):
            break
    full_xs[-1] = sign * dist
    traj = [
        (full_xs[i], traj[i][1], full_dts[i])
        for i in range(len(traj))
    ]
    return traj


__all__ = [
    "minimum_jerk_ease",
    "asymmetric_tau",
    "fitts_duration_ms",
    "gamma_intervals",
    "draw_hesitation_ms",
    "tremor_offset",
    "ar1_noise",
    "nudge_monotonic",
    "sample_correction_plan",
    "submovement_spans",
    "submovement_pause_ms",
    "maybe_overshoot_points",
    "persona_speed_scale",
    "skeleton_params",
    "velocity_series",
    "bridge_cap",
    "replay_time_scale",
    "apply_replay_noise",
    "compose_human_points",
]
