"""hCaptcha 纯规划器 / 解析器 — 无页面依赖，坐标与二义判定的回归锁定。

规划器（plan_grid_clicks / plan_drag_gesture）与解析器（parse_*）都是纯
函数：不碰浏览器、不发网络。绝大多数 hCaptcha 回归都发生在这一层（坐标
换算、夹取、1-based/0-based 二义、容错解析），因此单测只覆盖这层也能兜住
大部分风险——执行器只负责把 engine 的答案翻译成页面动作。
"""
import math

import pytest

from slidex.vision.hcaptcha import (
    DragGesture,
    parse_drag_answer,
    parse_grid_answer,
    parse_points_answer,
    plan_drag_gesture,
    plan_grid_clicks,
)


# ────────────────────── plan_grid_clicks ──────────────────────


def test_grid_click_centers_are_3x3_centroids():
    plan = plan_grid_clicks([True, False, False, False, False, False, False, False, True])
    assert plan.ok
    assert len(plan.points) == 2
    # 第 1 格（index 0）中心 = (0+0.5)/3, (0+0.5)/3
    assert plan.points[0].x == pytest.approx(1.0 / 6.0)
    assert plan.points[0].y == pytest.approx(1.0 / 6.0)
    # 第 9 格（index 8）中心 = (2+0.5)/3
    assert plan.points[1].x == pytest.approx(5.0 / 6.0)
    assert plan.points[1].y == pytest.approx(5.0 / 6.0)
    assert plan.points[0].box_index == 0
    assert plan.points[1].box_index == 8


def test_grid_click_all_false_falls_back_to_one_tile():
    """全空必须兜底点一格：hCaptcha 对「一格未选就提交」几乎必然判空作答。"""
    plan = plan_grid_clicks([False] * 9)
    assert plan.ok
    assert len(plan.points) == 1
    assert plan.points[0].box_index == 0
    assert any("fallback" in note for note in plan.notes)


def test_grid_click_fallback_index_respected_and_clamped():
    plan = plan_grid_clicks([False] * 9, fallback_index=8)
    assert plan.points[0].box_index == 8
    plan = plan_grid_clicks([False] * 9, fallback_index=99)
    assert plan.points[0].box_index == 8  # 夹取到最后一个合法格


def test_grid_click_wrong_length_is_error():
    plan = plan_grid_clicks([True] * 8)
    assert not plan.ok
    assert plan.error_code == "grid_size_mismatch"
    assert plan.points == []


def test_grid_click_propagates_confidence():
    plan = plan_grid_clicks([True] + [False] * 8, confidence=0.42)
    assert plan.confidence == pytest.approx(0.42)
    assert plan.points[0].confidence == pytest.approx(0.42)


# ────────────────────── plan_drag_gesture ──────────────────────


def test_drag_gesture_endpoints_exact_and_length():
    gesture = plan_drag_gesture((0.1, 0.2), (0.8, 0.9), steps=25, jitter=0.0)
    assert gesture.ok
    assert len(gesture.points) == 26
    # 首末点必须精确等于起止点（抖动/浮点误差不能把落点推离目标）。
    assert gesture.points[0] == (pytest.approx(0.1), pytest.approx(0.2))
    assert gesture.points[-1] == (pytest.approx(0.8), pytest.approx(0.9))
    assert gesture.start == (pytest.approx(0.1), pytest.approx(0.2))
    assert gesture.end == (pytest.approx(0.8), pytest.approx(0.9))


def test_drag_gesture_deterministic_without_jitter():
    a = plan_drag_gesture((0.2, 0.2), (0.7, 0.7), steps=25, jitter=0.0)
    b = plan_drag_gesture((0.2, 0.2), (0.7, 0.7), steps=25, jitter=0.0)
    assert a.points == b.points
    # 每个点都在 [0,1] 内（含边界）。
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in a.points)


def test_drag_gesture_is_not_a_straight_line():
    """二次贝塞尔 + 垂直控制点偏移：中段应偏离直线，而不是机械匀速直线。"""
    gesture = plan_drag_gesture((0.1, 0.5), (0.9, 0.5), steps=25, jitter=0.0)
    # 水平拖拽的竖直线偏不应恒等于 0.5。
    ys = [y for _x, y in gesture.points[1:-1]]
    assert any(abs(y - 0.5) > 1e-3 for y in ys)


def test_drag_gesture_zero_distance_is_degenerate():
    gesture = plan_drag_gesture((0.5, 0.5), (0.5, 0.5), steps=25)
    assert not gesture.ok
    assert gesture.error_code == "degenerate_drag_path"
    assert gesture.points == []


def test_drag_gesture_clamps_out_of_range():
    gesture = plan_drag_gesture((-0.5, 1.5), (2.0, -1.0), steps=10, jitter=0.0)
    assert gesture.start == (0.0, 1.0)
    assert gesture.end == (1.0, 0.0)
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in gesture.points)


# ────────────────────── parse_grid_answer ──────────────────────


def test_grid_parse_dict_1_based():
    assert parse_grid_answer('{"selected":[1,4,9]}') == [
        True, False, False, True, False, False, False, False, True
    ]


def test_grid_parse_bool_list():
    assert parse_grid_answer(
        '{"tiles":[true,false,false,false,false,false,false,false,true]}'
    ) == [True, False, False, False, False, False, False, False, True]


def test_grid_parse_bare_list_zero_based():
    # 出现 0 → 整体按 0-based 解释。
    assert parse_grid_answer("[0,1,0,0,0,0,0,0,0]") == [True, True] + [False] * 7


def test_grid_parse_zero_based_uses_zero():
    assert parse_grid_answer('{"selected":[0]}') == [True] + [False] * 8


def test_grid_parse_plain_text_indices():
    assert parse_grid_answer("1,3,5") == [True, False, True, False, True] + [False] * 4
    assert parse_grid_answer("1 3 5") == parse_grid_answer("1,3,5")
    assert parse_grid_answer("1，3，5") == parse_grid_answer("1,3,5")


def test_grid_parse_markdown_fence():
    text = '```json\n{"selected":[2,8]}\n```'
    assert parse_grid_answer(text) == [False, True, False, False, False, False, False, True, False]


def test_grid_parse_length_mismatch_bool_is_none():
    assert parse_grid_answer("[true,false]") is None


def test_grid_parse_garbage_is_none():
    assert parse_grid_answer("我也看不懂这个") is None
    assert parse_grid_answer("") is None


def test_grid_parse_explicit_empty_selection_is_all_false():
    """「模型明确说没有目标」必须传成 9 个 False，交给 planner 兜底点一格；
    否则这句合法回答会被误判成解析失败。"""
    assert parse_grid_answer('{"selected":[]}') == [False] * 9


def test_grid_parse_all_out_of_range_is_none():
    assert parse_grid_answer("[99]") is None


def test_grid_parse_out_of_range_indices_ignored():
    assert parse_grid_answer("[1,99]") == [True] + [False] * 8


# ────────────────────── parse_points_answer ──────────────────────


def test_points_parse_dict_list():
    points = parse_points_answer('{"points":[{"x":0.42,"y":0.61}]}')
    assert points is not None and len(points) == 1
    assert points[0].x == pytest.approx(0.42)
    assert points[0].y == pytest.approx(0.61)


def test_points_parse_pairs_list():
    points = parse_points_answer("[[0.1,0.2],[0.8,0.9]]")
    assert points is not None and len(points) == 2
    assert (points[0].x, points[0].y) == (pytest.approx(0.1), pytest.approx(0.2))


def test_points_parse_clamps_and_drops_invalid():
    points = parse_points_answer('[{"x":1.5,"y":-0.2},{"x":"a","y":0.5}]')
    assert points is not None and len(points) == 1
    assert points[0].x == pytest.approx(1.0)
    assert points[0].y == pytest.approx(0.0)


def test_points_parse_empty_points_is_none():
    assert parse_points_answer('{"points":[]}') is None


# ────────────────────── parse_drag_answer ──────────────────────


def test_drag_parse_dict():
    gesture = parse_drag_answer('{"start":{"x":0.22,"y":0.48},"end":{"x":0.71,"y":0.52}}')
    assert gesture is not None and gesture.ok
    assert gesture.start == (pytest.approx(0.22), pytest.approx(0.48))
    assert gesture.end == (pytest.approx(0.71), pytest.approx(0.52))


def test_drag_parse_pairs():
    gesture = parse_drag_answer("[[0.1,0.2],[0.9,0.8]]")
    assert gesture is not None and gesture.ok


def test_drag_parse_unsupported_returns_none():
    assert parse_drag_answer('{"unsupported":true}') is None


def test_drag_parse_degenerate_same_point_is_none():
    assert parse_drag_answer('{"start":{"x":0.5,"y":0.5},"end":{"x":0.5,"y":0.5}}') is None


def test_drag_parse_garbage_is_none():
    assert parse_drag_answer("NaN NaN") is None
