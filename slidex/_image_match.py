#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha 滑块缺口定位 - 基于 OpenCV 图像匹配
参考: GeekedTest (xKiian/GeekedTest) 的 Canny + matchTemplate 方案
"""

import cv2
import numpy as np
from typing import Dict, Optional, Tuple
from loguru import logger


class SliderImageMatcher:
    """滑块缺口图像匹配器"""

    @staticmethod
    def find_gap_position(
        background: np.ndarray,
        puzzle_piece: np.ndarray,
        offset_correction: int = 0,
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
        offset_correction: int = 0,
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
        offset_correction: int = 0,
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
def find_gap(background, puzzle_piece, offset_correction=0):
    """向后兼容：只返回 gap_x"""
    gap_x, _ = SliderImageMatcher.find_gap_position(background, puzzle_piece, offset_correction)
    return gap_x

find_gap_from_bytes = SliderImageMatcher.find_gap_from_bytes
find_gap_with_confidence = SliderImageMatcher.find_gap_with_confidence


class AliyunJigsawMatcher:
    """AliyunCaptcha.js 2.0 拼图缺口定位 — 边缘 × 对比度 × 内容 三信号共识检测器。

    与经典 nc 拼图不同，AliyunCaptcha.js 2.0 的拼图块是带 alpha 通道的整条
    PNG（52×200，形状只占其中一小段），背景 296×200 被拉伸到 300 宽渲染。
    OpenCV 模板匹配在该素材上容易产生扁平/伪峰（z.ai 实测 2026-11-02：原始
    亮度均值会被照片亮区污染，候选区面积只有真实缺口的 1/4）。

    本检测器利用缺口的三个物理特性，不依赖模板相似度：

    1. **轮廓-边缘**：缺口在背景图上留下同形状的压痕硬边。把拼图块 alpha
       轮廓沿 x 平移，对 Sobel 梯度幅值沿轮廓积分——形状对齐时出现孤立尖峰
       （实测 174 vs 次高 111）。
    2. **填充-对比度**：缺口区域比周围亮（挖空叠白）。形状内部均值减去
       外环均值，对"照片本身亮"鲁棒（局部对比而非绝对亮度）。
    3. **内容一致**：拼图块像素本就是从缺口处抽取的素材，带 alpha 掩码的
       SQDIFF 应在真实缺口处出现强极小值（z.ai 实测 2026-11-02 复盘：亮色
       照片上"边缘+对比度"双信号仍可能选错峰——F015，第三信号提供共识）。

    三信号各自 min-max 归一后等权相加；置信度取峰-中位差。返回的 gap_x 是
    压痕左缘在背景图像素坐标（含 bx0 修正），由 provider 再换算 CSS 与行程。
    """

    ALPHA_THRESHOLD = 200

    @staticmethod
    def find_gap_contour_contrast(
        bg_bgra: "np.ndarray",
        piece_bgra: "np.ndarray",
    ) -> Tuple[Optional[int], float, Dict]:
        """从 BGRA 数组定位缺口。

        Returns:
            (gap_x, confidence, debug) — gap_x 为压痕左缘在背景图上的像素 x；
            confidence ∈ [0,1]；debug 含 bx0 / 三信号 top5。
            失败返回 (None, confidence, debug)。
        """
        debug: Dict = {}
        try:
            if bg_bgra is None or piece_bgra is None or bg_bgra.ndim < 2 or piece_bgra.ndim < 3:
                return None, 0.0, debug
            h, w = bg_bgra.shape[:2]
            pw, ph = int(piece_bgra.shape[1]), int(piece_bgra.shape[0])
            if pw <= 0 or ph <= 0 or pw >= w:
                return None, 0.0, debug
            bg_bgr = bg_bgra[:, :, :3]
            bg_gray = cv2.cvtColor(bg_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
            alpha = piece_bgra[:, :, 3]
            mask = (alpha > AliyunJigsawMatcher.ALPHA_THRESHOLD).astype(np.uint8)
            if int(mask.sum()) < 30:
                return None, 0.0, debug
            # 全不透明守卫：截图兜底（omit_background 未开）会产出 alpha 全 255
            # 的拼图条，mask 退化为整条矩形——三信号会在背景纹理上给出"高置信"
            # 的错误峰而非检测失败（0.6.44 review P2）。alpha 无形状信息时直接
            # 判失败，交由 provider 回落经典模板匹配。
            if float(mask.mean()) > 0.9:
                debug["reason"] = "opaque_piece"
                logger.warning(
                    "AliyunJigsawMatcher: piece alpha 全不透明（截图兜底？），"
                    "无形状信息，拒绝轮廓检测"
                )
                return None, 0.0, debug
            # 形状最小 x（拼图条内形状起点，实测恒为 2）
            cols = np.where(mask.any(axis=0))[0]
            bx0 = int(cols[0])

            # 信号 1：Sobel 幅值 × 轮廓
            grad_x = cv2.Sobel(bg_gray, cv2.CV_32F, 1, 0, ksize=3)
            grad_y = cv2.Sobel(bg_gray, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.magnitude(grad_x, grad_y)

            # 轮廓 = mask − 腐蚀(mask)；内填 = 腐蚀；外环 = 膨胀(mask) − mask
            eroded = cv2.erode(mask, np.ones((3, 3), np.uint8))
            ring = cv2.dilate(mask, np.ones((5, 5), np.uint8)) - mask
            contour = mask - eroded
            cy, cx = np.nonzero(contour)
            fy, fx = np.nonzero(eroded)
            ry, rx = np.nonzero(ring)
            if len(cx) < 20:
                return None, 0.0, debug

            max_dx = w - pw
            dxs = np.arange(max_dx + 1, dtype=np.int64)

            def _shifted_mean(ys: np.ndarray, xs_pts: np.ndarray, plane: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
                """点集沿 x 平移 dx 后在 plane 上取均值：(max_dx+1,) 均值与有效计数。

                向量化替代逐 dx 循环（0.6.45：245 次 × 多次花式索引 → 一次 2D
                gather）；y 越界的点（拼图条高于背景的异常素材）一次性剔除，
                原实现对这种情况会直接 IndexError。
                """
                keep = (ys >= 0) & (ys < h)
                ys_k, xs_k = ys[keep], xs_pts[keep]
                shifted = xs_k[:, None] + dxs[None, :]
                valid = (shifted >= 0) & (shifted < w)
                np.clip(shifted, 0, w - 1, out=shifted)
                acc = np.where(valid, plane[ys_k[:, None], shifted], 0.0)
                counts = valid.sum(axis=0)
                means = acc.sum(axis=0) / np.maximum(counts, 1)
                means[counts == 0] = 0.0
                return means.astype(np.float32), counts

            edge, _ = _shifted_mean(cy, cx, mag)
            fill_mean, fill_counts = _shifted_mean(fy, fx, bg_gray)
            ring_mean, ring_counts = _shifted_mean(ry, rx, bg_gray)
            contrast = np.where(
                (fill_counts > 0) & (ring_counts > 0), fill_mean - ring_mean, 0.0
            ).astype(np.float32)

            # 信号 3：内容一致（掩码 SQDIFF，取逆 → 相似度）。保持全分辨率：
            # 实测半分辨率（INTER_AREA）会混合形状边界，模板与压痕失配，
            # 内容信号反投错误峰（imprint_220 偏 43px）——检测正确性优先，
            # 该信号的 7.4ms 开销不省（0.6.45 尝试后回退）。
            content = np.zeros(max_dx + 1, np.float32)
            if ph == h and int(mask.sum()) >= 30:
                mask3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR).astype(np.float32) / 255.0
                piece_bgr = piece_bgra[:, :, :3].astype(np.float32)
                try:
                    sq = cv2.matchTemplate(bg_bgr.astype(np.float32), piece_bgr,
                                           cv2.TM_SQDIFF, mask=mask3).ravel()
                    if np.isfinite(sq).all() and float(sq.max()) > 0:
                        content[: len(sq)] = (float(sq.max()) - sq) / float(sq.max())
                except Exception:
                    pass  # OpenCV 版本不带 mask 参数时静默退化为双信号

            def _norm(v: np.ndarray) -> np.ndarray:
                lo, hi = float(v.min()), float(v.max())
                return (v - lo) / (hi - lo) if hi > lo else np.zeros_like(v)

            # 内容信号区分度门控：真实生成是"压痕叠白"而非纹理拷贝，信号可能
            # 全程平坦——平坦信号 min-max 归一后是纯噪声，会污染融合。只有
            # 存在真实凹点（峰显著高于中位）时才纳入。
            content_gate = float(content.max()) - float(np.median(content)) > 0.15
            combined = _norm(edge) + _norm(np.clip(contrast, 0, None))
            if content_gate:
                combined = combined + _norm(content)
            debug["content_gated"] = not content_gate
            best = int(combined.argmax())
            hi = float(combined.max())
            med = float(np.median(combined))
            confidence = (hi - med) / (hi + 1e-6)
            debug = {
                "bx0": bx0,
                "best_dx": best,
                "edge_top5": [(int(i), round(float(edge[i]), 1)) for i in np.argsort(edge)[::-1][:5]],
                "contrast_top5": [(int(i), round(float(contrast[i]), 1)) for i in np.argsort(contrast)[::-1][:5]],
                "content_top5": [(int(i), round(float(content[i]), 3)) for i in np.argsort(content)[::-1][:5]],
                "combined_best": round(hi, 3),
            }
            if confidence < 0.2:
                logger.warning(f"AliyunJigsawMatcher 置信度过低: {confidence:.2f}（峰不够孤立）")
                return None, float(confidence), debug
            return int(best + bx0), float(min(1.0, confidence)), debug
        except Exception as e:
            logger.error(f"AliyunJigsawMatcher 失败: {e}")
            return None, 0.0, debug

    @staticmethod
    def find_gap_from_bytes(
        bg_bytes: bytes,
        piece_bytes: bytes,
    ) -> Tuple[Optional[int], float, Dict]:
        """bytes 版入口（拼图块必须带 alpha——data:URL 原图即满足）。"""
        try:
            bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
            piece = cv2.imdecode(np.frombuffer(piece_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
            if bg is None or piece is None:
                return None, 0.0, {}
            if bg.ndim == 2:
                bg = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGRA)
            elif bg.shape[2] == 3:
                bg = cv2.cvtColor(bg, cv2.COLOR_BGR2BGRA)
            if piece.ndim == 2:
                return None, 0.0, {}
            elif piece.shape[2] == 3:
                piece = cv2.cvtColor(piece, cv2.COLOR_BGR2BGRA)  # 无 alpha → 全不透明
            return AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        except Exception as e:
            logger.error(f"AliyunJigsawMatcher.find_gap_from_bytes 失败: {e}")
            return None, 0.0, {}


__all__ = ['SliderImageMatcher', 'AliyunJigsawMatcher',
           'find_gap', 'find_gap_from_bytes', 'find_gap_with_confidence']
