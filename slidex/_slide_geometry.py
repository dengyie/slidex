"""滑动距离与录制轨迹的单一语义：相对位移像素。"""

from __future__ import annotations

import math

from typing import Any, Dict, List, Optional, Sequence


# ── AliyunCaptcha.js 2.0 拼图几何（zchat.z.ai 实测 2026-11-02）───────────────────
# 新一代 AliyunCaptcha.js 弹窗拼图（DOM 前缀 aliyunCaptcha-*，场景如 z.ai 登录
# 1nl1cfkp）的拼图块 CSS left 不是滑块位移的 1:1 投影，而是一条二次曲线：
#
#     piece_left = A·knob² + B·knob
#
# 其中 knob 为 `#aliyunCaptcha-sliding-slider` 的 computed left（= 指针相对位移，
# 0..260 夹紧，轨道 300 − 滑块 40）。两次独立标定 g(200)=157.396 完全吻合，
# 并经 5 组观测与一次成功验证（knob=235 → piece=214.142）复核。
# 服务器判定用的是释放时刻拼图块的 left（非滑块位置），因此把缺口位置换算成
# 滑块行程必须用下面的反函数；滑块在大幅/回退跳变时会滞后，终点要以小步逼近。
ALIYUN_JIGSAW_PIECE_A = 0.00355029
ALIYUN_JIGSAW_PIECE_B = 0.076924


def aliyun_jigsaw_piece_left(knob: float) -> float:
    """滑块位移（computed left, px）→ 拼图块 CSS left（px）。"""
    k = max(0.0, float(knob))
    return ALIYUN_JIGSAW_PIECE_A * k * k + ALIYUN_JIGSAW_PIECE_B * k


def aliyun_jigsaw_knob_for_piece(piece_left: float) -> float:
    """拼图块目标 CSS left（px）→ 所需滑块位移（px）。

    二次方程 A·k² + B·k = L 的正根；L<=0 返回 0。调用方需再按
    轨道行程（track − 滑块宽度，实测 260）夹紧。
    """
    target = float(piece_left)
    if target <= 0:
        return 0.0
    a = ALIYUN_JIGSAW_PIECE_A
    b = ALIYUN_JIGSAW_PIECE_B
    return (-b + math.sqrt(b * b + 4.0 * a * target)) / (2.0 * a)


def clamp_travel(distance: float, max_travel: Optional[float]) -> float:
    """缺口行程用 JS 轨道可滑动最大值夹紧，JS 本身不是缺口位置。"""
    if distance is None:
        return 0.0
    value = float(distance)
    if value <= 0:
        return 0.0
    if max_travel is not None and max_travel > 0:
        return min(value, float(max_travel))
    return value


def scale_recorded_points(
    points: Sequence[Sequence[float]],
    recorded_distance: float,
    target_distance: float,
    tolerance: float = 0.10,
) -> List[List[float]]:
    """录制轨迹是相对位移。距离差超过 tolerance 时按比例缩放 x（保留 y 与 delay）。"""
    scaled: List[List[float]] = []
    rec = float(recorded_distance or 0.0)
    target = float(target_distance or 0.0)
    factor = 1.0
    if rec > 0 and target > 0 and abs(rec - target) / max(target, 1.0) > tolerance:
        factor = target / rec
    for point in points:
        if not point:
            continue
        x = float(point[0]) * factor
        y = float(point[1]) if len(point) > 1 else 0.0
        delay = float(point[2]) if len(point) > 2 else 0.0
        scaled.append([x, y, delay])
    return scaled


def points_from_recorded(
    recorded: Optional[Dict[str, Any]],
    target_distance: float,
    tolerance: float = 0.10,
) -> Optional[List[List[float]]]:
    if not recorded:
        return None
    raw = recorded.get("points")
    if not raw:
        return None
    rec_dist = recorded.get("distance", target_distance)
    return scale_recorded_points(raw, rec_dist, target_distance, tolerance)


# ── 录制轨迹的时序归一化（0.10.2）───────────────────────────────────────
# 录制器记的是「用户从按下到松开的世界时钟」：CDP 真机模式下人是在远端画面里
# 拖的，一次手动救场拖动跨 13-17s 很常见（线上池 50 条全部如此，单点间隙最大
# 1.24s）。回放侧只缩放 x、原样保留 delay，于是自动拖动变成 16.7s 的走走停停
# ——用户侧看到的就是"轨迹很卡"。
#
# 几何（形状、终点、过冲）是录制最值钱的部分，必须原样保留；只有时间轴需要
# 回到生成器同一设计带（~0.6-0.8s 的纯位移段）。规则：
#   1. 死停顿折叠：连续同坐标之间的间隙不是"移动"，压到 ≤ DEAD_HOLD_MAX_MS
#      （保留停顿存在，去掉落体感冻结的 1s+ 空档）
#   2. 慢录制压缩：纯位移段超出设计带时整体按比例压缩（只压缩不放慢）
#   3. 单点间隙上限：归一化后仍超过 GAP_MAX_MS 的一律夹紧
# 归一化幂等：二次调用不再改动。
RECORDED_MOTION_TARGET_MS = 650.0
RECORDED_MOTION_BAND_TOLERANCE = 1.15
RECORDED_GAP_MAX_MS = 120.0
RECORDED_DEAD_HOLD_MAX_MS = 40.0
RECORDED_PRESS_HOLD_MAX_MS = 1200.0


def recorded_motion_ms(points: Sequence[Sequence[float]]) -> float:
    """纯位移段时间：只计"位置真的变了"的点所带的间隙（死停顿不计）。

    首点是按下到第一次采样的间隔（press-hold），不是位移，不计入。
    """
    total = 0.0
    prev_x = prev_y = None
    for idx, point in enumerate(points):
        if not point:
            continue
        x = float(point[0])
        y = float(point[1]) if len(point) > 1 else 0.0
        gap = float(point[2]) if len(point) > 2 else 0.0
        moved = prev_x is None or abs(x - prev_x) > 1e-6 or abs(y - prev_y) > 1e-6
        if idx > 0 and moved:
            total += gap
        prev_x, prev_y = x, y
    return total


def normalize_recorded_delays(
    points: Sequence[Sequence[float]],
    target_total_ms: float = RECORDED_MOTION_TARGET_MS,
    tolerance: float = RECORDED_MOTION_BAND_TOLERANCE,
    gap_max_ms: float = RECORDED_GAP_MAX_MS,
    dead_hold_max_ms: float = RECORDED_DEAD_HOLD_MAX_MS,
    press_hold_max_ms: float = RECORDED_PRESS_HOLD_MAX_MS,
) -> List[List[float]]:
    """把录制轨迹的时间轴拉回设计带（几何不动）。返回新列表，不改入参。

    只有纯位移段参与压缩判据：首点（按下停顿）单独夹到 press-hold 上限，
    同坐标的死停顿夹到 dead-hold 上限。录制已经够快（纯位移段 <=
    target*tolerance）时保持原节奏，只做间隙夹紧；慢录制整体压缩。
    没有真实位移时返回空列表（调用方当次弃用该录制）。

    归一化幂等：二次调用不再改动。
    """
    if not points:
        return []
    parsed: List[List[float]] = []
    for point in points:
        if not point:
            continue
        parsed.append([
            float(point[0]),
            float(point[1]) if len(point) > 1 else 0.0,
            max(0.0, float(point[2]) if len(point) > 2 else 0.0),
        ])
    if not parsed:
        return []

    # 1) 首点 = 按下停顿；其余点里同坐标的间隙是死停顿
    parsed[0][2] = min(parsed[0][2], max(1.0, float(press_hold_max_ms)))
    prev_x, prev_y = parsed[0][0], parsed[0][1]
    for idx in range(1, len(parsed)):
        x, y, gap = parsed[idx]
        if abs(x - prev_x) <= 1e-6 and abs(y - prev_y) <= 1e-6:
            parsed[idx][2] = min(gap, dead_hold_max_ms)
        prev_x, prev_y = x, y

    motion = recorded_motion_ms(parsed)
    if motion <= 0:
        return []

    # 2) 慢录制压缩到设计带（只压缩，不放慢）
    target = max(1.0, float(target_total_ms or RECORDED_MOTION_TARGET_MS))
    if motion > target * max(1.0, float(tolerance)):
        scale = target / motion
        for idx in range(1, len(parsed)):
            parsed[idx][2] *= scale

    # 3) 单点间隙上限（首点的 press-hold 不受这道夹）
    cap = max(1.0, float(gap_max_ms))
    for idx in range(1, len(parsed)):
        parsed[idx][2] = min(parsed[idx][2], cap)
    return parsed
