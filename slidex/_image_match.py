#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha 滑块缺口定位 - 基于 OpenCV 图像匹配
参考: GeekedTest (xKiian/GeekedTest) 的 Canny + matchTemplate 方案
"""

import cv2
import numpy as np
from typing import Optional, Tuple
from loguru import logger


class SliderImageMatcher:
    """滑块缺口图像匹配器"""

    @staticmethod
    def find_gap_position(
        background: np.ndarray,
        puzzle_piece: np.ndarray,
        offset_correction: int = -35,
    ) -> Tuple[Optional[int], float]:
        """
        使用 Canny 边缘检测 + 模板匹配定位滑块缺口位置。

        Returns:
            (gap_x, confidence) — 缺口 X 坐标和匹配置信度
            失败时返回 (None, 0.0)
        """
        try:
            if len(background.shape) == 3:
                bg_gray = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)
            else:
                bg_gray = background

            if len(puzzle_piece.shape) == 3:
                piece_gray = cv2.cvtColor(puzzle_piece, cv2.COLOR_BGR2GRAY)
            else:
                piece_gray = puzzle_piece

            edge_bg = cv2.Canny(bg_gray, 100, 200)
            edge_piece = cv2.Canny(piece_gray, 100, 200)

            edge_bg_rgb = cv2.cvtColor(edge_bg, cv2.COLOR_GRAY2RGB)
            edge_piece_rgb = cv2.cvtColor(edge_piece, cv2.COLOR_GRAY2RGB)

            result = cv2.matchTemplate(
                edge_bg_rgb, edge_piece_rgb, cv2.TM_CCOEFF_NORMED
            )

            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            confidence = max_val

            if confidence < 0.3:
                logger.warning(
                    f"图像匹配置信度过低: {confidence:.2f}，可能匹配失败"
                )

            piece_h, piece_w = edge_piece.shape[:2]
            center_x = max_loc[0] + piece_w // 2
            gap_x = center_x + offset_correction

            logger.info(
                f"图像匹配结果: 中心={center_x}px, "
                f"修正后={gap_x}px, 置信度={confidence:.3f}"
            )

            return max(0, gap_x), float(confidence)

        except Exception as e:
            logger.error(f"图像匹配失败: {e}")
            return None, 0.0

    @staticmethod
    def find_gap_from_bytes(
        bg_bytes: bytes,
        piece_bytes: bytes,
        offset_correction: int = -35,
    ) -> Optional[int]:
        try:
            bg_arr = np.frombuffer(bg_bytes, np.uint8)
            bg_img = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)

            piece_arr = np.frombuffer(piece_bytes, np.uint8)
            piece_img = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)

            if bg_img is None or piece_img is None:
                logger.error("无法解码图片数据")
                return None

            gap_x, _ = SliderImageMatcher.find_gap_position(
                bg_img, piece_img, offset_correction
            )
            return gap_x
        except Exception as e:
            logger.error(f"从字节匹配失败: {e}")
            return None

    @staticmethod
    def find_gap_with_confidence(
        bg_bytes: bytes,
        piece_bytes: bytes,
        offset_correction: int = -35,
    ) -> Tuple[Optional[int], float]:
        """与 find_gap_from_bytes 相同逻辑，但同时返回匹配置信度"""
        try:
            bg_arr = np.frombuffer(bg_bytes, np.uint8)
            bg_img = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
            piece_arr = np.frombuffer(piece_bytes, np.uint8)
            piece_img = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)
            if bg_img is None or piece_img is None:
                return None, 0.0
            return SliderImageMatcher.find_gap_position(
                bg_img, piece_img, offset_correction
            )
        except Exception as e:
            logger.error(f"find_gap_with_confidence 失败: {e}")
            return None, 0.0


# 便捷函数
def find_gap(background, puzzle_piece, offset_correction=-35):
    """向后兼容：只返回 gap_x"""
    gap_x, _ = SliderImageMatcher.find_gap_position(background, puzzle_piece, offset_correction)
    return gap_x

find_gap_from_bytes = SliderImageMatcher.find_gap_from_bytes
find_gap_with_confidence = SliderImageMatcher.find_gap_with_confidence

__all__ = ['SliderImageMatcher', 'find_gap', 'find_gap_from_bytes', 'find_gap_with_confidence']
