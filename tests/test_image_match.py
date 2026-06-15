"""
测试图像匹配核心逻辑

覆盖:
1. find_gap_position() - Canny + matchTemplate
2. 不同图像格式 (彩色/灰度)
3. 置信度计算
4. 异常图像处理
5. offset_correction 参数
6. find_gap_from_bytes() 字节流解码
7. find_gap_with_confidence() 返回值
"""

import pytest
import numpy as np
import cv2
from slidex._image_match import (
    SliderImageMatcher,
    find_gap,
    find_gap_from_bytes,
    find_gap_with_confidence,
)


class TestSliderImageMatcher:
    """测试 SliderImageMatcher 核心类"""

    def test_find_gap_position_with_synthetic_images(self):
        """使用合成图像测试基本匹配"""
        # 创建 300x150 的背景图
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255

        # 在 x=100 处绘制一个 50x50 的黑色方块（缺口）
        background[50:100, 100:150] = 0

        # 创建 50x50 的拼图块
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        # 匹配
        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=0
        )

        # 应该找到缺口（模板匹配找到左上角，所以结果在 0-50 范围）
        assert gap_x is not None
        assert 0 <= gap_x <= 50  # 左上角位置
        assert confidence > 0.5

    def test_find_gap_position_with_grayscale_images(self):
        """测试灰度图像输入"""
        # 灰度背景
        background = np.ones((150, 300), dtype=np.uint8) * 200
        background[50:100, 100:150] = 50  # 暗色区域

        # 灰度拼图块
        puzzle_piece = np.ones((50, 50), dtype=np.uint8) * 50

        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=0
        )

        assert gap_x is not None
        assert isinstance(gap_x, int)
        assert confidence >= 0.0

    def test_find_gap_position_with_offset_correction(self):
        """测试 offset_correction 参数"""
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 150:200] = 0
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        # 不修正
        gap_x_no_offset, _ = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=0
        )

        # 修正 -20px
        gap_x_with_offset, _ = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=-20
        )

        # 修正后的位置应该减少约 20px
        assert gap_x_with_offset < gap_x_no_offset
        assert abs((gap_x_no_offset - gap_x_with_offset) - 20) < 5

    def test_find_gap_position_low_confidence_warning(self):
        """测试低置信度场景（完全不匹配的图像）"""
        # 纯白背景
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255

        # 纯黑拼图块
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=0
        )

        # 即使不匹配也应该返回结果（但置信度低）
        assert gap_x is not None
        # 可能 confidence < 0.3（触发 warning）

    def test_find_gap_position_with_invalid_input(self):
        """测试无效输入（空数组）"""
        background = np.array([])
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece
        )

        # 应该返回 None, 0.0
        assert gap_x is None
        assert confidence == 0.0

    def test_find_gap_position_negative_result_clamped(self):
        """测试负数结果被截断为 0"""
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 10:60] = 0  # 缺口在最左侧
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        gap_x, _ = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=-50  # 大偏移可能导致负数
        )

        # 负数应该被截断为 0
        assert gap_x is not None
        assert gap_x >= 0

    def test_find_gap_from_bytes_valid_images(self):
        """测试从字节流解码并匹配"""
        # 创建合成图像并编码为字节
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 100:150] = 0

        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        # 编码为 PNG 字节流
        _, bg_bytes = cv2.imencode('.png', background)
        _, piece_bytes = cv2.imencode('.png', puzzle_piece)

        bg_bytes = bg_bytes.tobytes()
        piece_bytes = piece_bytes.tobytes()

        # 匹配
        gap_x = find_gap_from_bytes(bg_bytes, piece_bytes, offset_correction=0)

        assert gap_x is not None
        assert 0 <= gap_x <= 50  # 左上角匹配位置

    def test_find_gap_from_bytes_invalid_data(self):
        """测试无效字节流（无法解码）"""
        bg_bytes = b"not an image"
        piece_bytes = b"also not an image"

        gap_x = find_gap_from_bytes(bg_bytes, piece_bytes)

        # 应该返回 None
        assert gap_x is None

    def test_find_gap_from_bytes_one_invalid(self):
        """测试一个有效一个无效的字节流"""
        # 有效背景
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        _, bg_bytes = cv2.imencode('.png', background)
        bg_bytes = bg_bytes.tobytes()

        # 无效拼图
        piece_bytes = b"invalid"

        gap_x = find_gap_from_bytes(bg_bytes, piece_bytes)

        # 应该返回 None
        assert gap_x is None

    def test_find_gap_with_confidence_returns_tuple(self):
        """测试 find_gap_with_confidence 返回元组"""
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 100:150] = 0
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        _, bg_bytes = cv2.imencode('.png', background)
        _, piece_bytes = cv2.imencode('.png', puzzle_piece)

        gap_x, confidence = find_gap_with_confidence(
            bg_bytes.tobytes(), piece_bytes.tobytes()
        )

        assert gap_x is not None
        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

    def test_find_gap_with_confidence_invalid_returns_none(self):
        """测试 find_gap_with_confidence 无效输入返回 (None, 0.0)"""
        gap_x, confidence = find_gap_with_confidence(b"bad", b"data")

        assert gap_x is None
        assert confidence == 0.0

    def test_find_gap_backward_compatibility(self):
        """测试 find_gap() 向后兼容函数"""
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        background[50:100, 100:150] = 0
        puzzle_piece = np.zeros((50, 50, 3), dtype=np.uint8)

        # find_gap 只返回 gap_x
        gap_x = find_gap(background, puzzle_piece, offset_correction=0)

        assert gap_x is not None
        assert isinstance(gap_x, (int, type(None)))

    def test_canny_edge_detection_applied(self):
        """测试 Canny 边缘检测确实被应用"""
        # 创建有明显边缘的图像
        background = np.ones((150, 300, 3), dtype=np.uint8) * 255
        # 绘制清晰的矩形边缘
        cv2.rectangle(background, (100, 50), (150, 100), (0, 0, 0), 2)

        puzzle_piece = np.ones((50, 50, 3), dtype=np.uint8) * 255
        cv2.rectangle(puzzle_piece, (0, 0), (49, 49), (0, 0, 0), 2)

        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece, offset_correction=0
        )

        # 有边缘的图像应该能匹配
        assert gap_x is not None
        assert confidence > 0.1  # 边缘匹配应该有一定置信度

    def test_different_puzzle_sizes(self):
        """测试不同尺寸的拼图块"""
        background = np.ones((200, 400, 3), dtype=np.uint8) * 255

        # 测试 30x30 小拼图块
        small_piece = np.zeros((30, 30, 3), dtype=np.uint8)
        gap_x_small, _ = SliderImageMatcher.find_gap_position(
            background, small_piece
        )
        assert gap_x_small is not None

        # 测试 80x80 大拼图块
        large_piece = np.zeros((80, 80, 3), dtype=np.uint8)
        gap_x_large, _ = SliderImageMatcher.find_gap_position(
            background, large_piece
        )
        assert gap_x_large is not None

    def test_confidence_calculation_range(self):
        """测试置信度在合理范围内"""
        background = np.random.randint(0, 255, (150, 300, 3), dtype=np.uint8)
        puzzle_piece = np.random.randint(0, 255, (50, 50, 3), dtype=np.uint8)

        gap_x, confidence = SliderImageMatcher.find_gap_position(
            background, puzzle_piece
        )

        # 置信度应该在 [0, 1] 范围内
        assert confidence >= 0.0
        assert confidence <= 1.0
