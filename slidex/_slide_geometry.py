"""滑动距离与录制轨迹的单一语义：相对位移像素。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


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
