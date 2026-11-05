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
