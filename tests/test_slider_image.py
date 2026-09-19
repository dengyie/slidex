"""纯图片滑块求解器（SliderImageSolver）单元测试

合成图策略（保证确定性，不依赖网络/模型权重）：
- 模板匹配用例：全噪点背景 + 从缺口处直接裁剪的拼图块（纹理精确一致），
  matchTemplate 应在缺口原位取得唯一峰值。
- 无块图/ROI 用例：纯白背景 + 黑色实心矩形缺口（Canny 强边缘），
  轮廓检测应稳定命中。
"""

import cv2
import numpy as np
import pytest

from slidex.vision.slider_image import (
    ERROR_IMAGE_DECODE,
    ERROR_INVALID_ROI,
    ERROR_NO_GAP_FOUND,
    ERROR_PIECE_LARGER_THAN_BG,
    SliderImageResult,
    SliderImageSolver,
)

# 缺口矩形：(x, y, width, height)
NOTCH = (180, 40, 40, 44)


def make_notched_background() -> np.ndarray:
    """纯白背景 + 黑色实心缺口（轮廓检测友好）"""
    bg = np.full((150, 300), 255, dtype=np.uint8)
    nx, ny, nw, nh = NOTCH
    bg[ny : ny + nh, nx : nx + nw] = 0
    return bg


def make_textured_background() -> np.ndarray:
    """全噪点背景（模板匹配用，缺口纹理与背景一致以保证唯一峰值）"""
    rng = np.random.default_rng(42)
    return rng.integers(0, 255, size=(150, 300), dtype=np.uint8)


def crop_notch(bg: np.ndarray) -> np.ndarray:
    nx, ny, nw, nh = NOTCH
    return bg[ny : ny + nh, nx : nx + nw].copy()


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


@pytest.fixture
def solver():
    return SliderImageSolver(allow_yolo_backend=False)


class TestSliderImageSolver:
    def test_template_edge_with_piece(self, solver):
        bg = make_textured_background()
        result = solver.solve(bg, crop_notch(bg))

        assert result.success is True
        assert result.method == "template_edge"
        assert result.gap_x == pytest.approx(NOTCH[0], abs=4)
        assert result.confidence >= 0.5
        assert result.gap_box is not None
        assert result.gap_box[0] == pytest.approx(NOTCH[0], abs=4)

    def test_contour_without_piece(self, solver):
        result = solver.solve(make_notched_background())

        assert result.success is True
        assert result.method == "contour"
        assert result.gap_x == pytest.approx(NOTCH[0], abs=6)
        assert result.confidence <= 0.8  # 启发式方法置信度上限

    def test_contour_via_png_bytes(self, solver):
        result = solver.solve(encode_png(make_notched_background()))

        assert result.success is True
        assert result.gap_x == pytest.approx(NOTCH[0], abs=6)

    def test_distance_scale_conversion(self, solver):
        bg = make_textured_background()
        result = solver.solve(bg, crop_notch(bg), distance_scale=2.0)

        assert result.success is True
        assert result.metadata["distance_actual_px"] == result.distance_px * 2

    def test_roi_crops_and_translates_back(self, solver):
        bg = make_notched_background()
        roi = {"x": 150, "y": 20, "width": 100, "height": 90}
        result = solver.solve(bg, roi=roi)

        assert result.success is True
        assert result.metadata["roi_offset"] == [150, 20]
        assert result.gap_x == pytest.approx(NOTCH[0], abs=6)
        assert result.gap_box[0] == pytest.approx(NOTCH[0], abs=6)

    def test_roi_invalid_returns_error(self, solver):
        bg = make_notched_background()
        for roi in (
            {"x": 10, "y": 10, "width": 50},  # 缺 height
            {"x": 10, "y": 10, "width": 0, "height": 50},  # 尺寸非正
            {"x": 10, "y": 10, "width": "abc", "height": 50},  # 非数值
            [1, 2, 3, 4],  # 非 dict
        ):
            result = solver.solve(bg, roi=roi)
            assert result.success is False
            assert result.error_code == ERROR_INVALID_ROI

    def test_roi_without_gap(self, solver):
        bg = make_notched_background()
        roi = {"x": 10, "y": 10, "width": 100, "height": 100}  # 纯白区域
        result = solver.solve(bg, roi=roi)

        assert result.success is False
        assert result.error_code == ERROR_NO_GAP_FOUND

    def test_missing_background(self, solver):
        result = solver.solve(None)
        assert result.success is False
        assert result.error_code == "missing_background_image"

    def test_background_decode_failure(self, solver):
        result = solver.solve(b"not-an-image")
        assert result.success is False
        assert result.error_code == ERROR_IMAGE_DECODE

    def test_piece_decode_failure(self, solver):
        result = solver.solve(make_notched_background(), b"not-an-image")
        assert result.success is False
        assert result.error_code == ERROR_IMAGE_DECODE

    def test_piece_larger_than_background(self, solver):
        bg = np.full((100, 200), 255, dtype=np.uint8)
        piece = np.full((100, 200), 0, dtype=np.uint8)
        result = solver.solve(bg, piece)
        assert result.success is False
        assert result.error_code == ERROR_PIECE_LARGER_THAN_BG

    def test_result_to_dict(self, solver):
        result = solver.solve(make_notched_background())
        payload = result.to_dict()

        assert set(payload) == {
            "success",
            "gap_x",
            "distance_px",
            "confidence",
            "method",
            "gap_box",
            "candidates",
            "error_code",
            "metadata",
        }
        assert payload["success"] is True

    def test_candidates_reported_on_success(self, solver):
        bg = make_notched_background()
        result = solver.solve(bg)

        assert result.candidates
        best = max(result.candidates, key=lambda item: item["confidence"])
        assert best["method"] == result.method


class TestMinConfidenceGate:
    """回归：min_confidence 必须真正参与候选取舍与成功判定（修 P2 死配置）。"""

    def test_high_threshold_rejects_low_confidence_candidate(self):
        # contour 置信度 ~0.72，阈值抬到 0.99 应判定未定位到缺口
        solver = SliderImageSolver(allow_yolo_backend=False, min_confidence=0.99)
        result = solver.solve(make_notched_background())

        assert result.success is False
        assert result.error_code == ERROR_NO_GAP_FOUND
        # 候选仍应记录，供调用方诊断
        assert result.candidates

    def test_low_threshold_accepts_candidate(self):
        solver = SliderImageSolver(allow_yolo_backend=False, min_confidence=0.1)
        result = solver.solve(make_notched_background())

        assert result.success is True
        assert result.method == "contour"

    def test_default_threshold_accepts_contour(self):
        # 默认 0.3，contour ~0.72 应通过
        result = SliderImageSolver(allow_yolo_backend=False).solve(
            make_notched_background()
        )
        assert result.success is True


class TestColumnProfileFallback:
    """回归：contour 无结果时回退到 column_profile（此前无直接覆盖）。"""

    def test_column_profile_detects_single_vertical_edge(self):
        # 低纹理背景 + 一条竖直强边缘（无闭合轮廓），contour 拿不到矩形，
        # 触发 column_profile 兜底
        bg = np.full((120, 300), 255, dtype=np.uint8)
        bg[:, 200:203] = 0  # 竖线，非闭合矩形
        result = SliderImageSolver(allow_yolo_backend=False).solve(bg)

        # 允许 contour 或 column_profile，但断言兜底路径能产出候选并命中竖线附近
        assert result.success is True
        assert result.gap_x == pytest.approx(200, abs=10)

    def test_column_profile_direct(self):
        solver = SliderImageSolver(allow_yolo_backend=False)
        bg = np.full((120, 300), 255, dtype=np.uint8)
        bg[:, 180:183] = 0
        candidate = solver._detect_column_profile(bg)

        assert candidate is not None
        assert candidate["method"] == "column_profile"
        assert candidate["gap_x"] == pytest.approx(180, abs=10)
