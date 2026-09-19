"""纯图片滑块求解 — 无浏览器场景下的缺口定位能力

面向只拿得到图片（截图、网络抓包图像、裁剪产物）的场景：
输入背景图与可选拼图块图，输出缺口几何信息（gap_x / gap_box）、置信度与候选列表，
不启动浏览器、不产生 cookie。

多策略检测管线（auto 模式下按可用性自动选择，全部失败返回 success=False）：

1. ``template_edge`` — 提供拼图块图时：Canny 边缘 + matchTemplate，
   与浏览器内 provider 使用的 ``_image_match`` 同源方案，但直接返回缺口左上角 box，
   不做供应商相关的 offset 校正（校正是调用方/供应商层的职责）。
2. ``yolo`` — 可选深度学习后端：安装 ``captcha-recognizer`` extra 后启用
   （社区项目 chenwei-zhao/captcha-recognizer，YOLO/ONNX 缺口检测，
   支持无块图与多缺口）。未安装时静默跳过。
3. ``contour`` — 无块图且无深度学习后端时的零依赖几何检测：
   Canny 边缘 → 轮廓筛选（尺寸/长宽比/矩形贴合度）→ 评分排序；
   轮廓无结果时回退到列边缘能量剖面定位。启发式方法，置信度上限压在 0.8。

所有方法均为同步 CPU 密集型；异步调用方应通过线程池调度
（``VisualChallengeSolver`` 已用 ``asyncio.to_thread`` 处理）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from loguru import logger

ImageSource = Union[bytes, bytearray, str, Path, np.ndarray]

# 无块图轮廓检测的尺寸/形状阈值（相对背景图宽度的比例 + 绝对像素下限）
_CONTOUR_MIN_WIDTH_RATIO = 0.04
_CONTOUR_MAX_WIDTH_RATIO = 0.55
_CONTOUR_MIN_ASPECT = 0.3
_CONTOUR_MAX_ASPECT = 4.0
_CONTOUR_MIN_AREA_PX = 150

# heuristic 方法的置信度上限（避免与模板匹配/YOLO 的经验置信度混排时虚高）
_CONTOUR_CONFIDENCE_CAP = 0.8
_COLUMN_PROFILE_CONFIDENCE = 0.45

ERROR_IMAGE_DECODE = "image_decode_failed"
ERROR_MISSING_BACKGROUND = "missing_background_image"
ERROR_PIECE_LARGER_THAN_BG = "piece_larger_than_background"
ERROR_NO_GAP_FOUND = "gap_not_found"
ERROR_INVALID_ROI = "invalid_roi"


@dataclass
class SliderImageResult:
    """纯图片滑块求解结果

    gap_x 语义：缺口（即拼图块目标位置）左边缘在背景图坐标系中的 x 像素。
    若拼图块初始位置不在图像 x=0 处，实际滑动距离需由调用方减去初始偏移；
    可用 ``distance_scale``（实际行程像素 / 图像像素）做渲染缩放换算。
    """

    success: bool
    gap_x: Optional[int] = None
    distance_px: Optional[int] = None
    confidence: float = 0.0
    method: Optional[str] = None
    gap_box: Optional[List[int]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "gap_x": self.gap_x,
            "distance_px": self.distance_px,
            "confidence": self.confidence,
            "method": self.method,
            "gap_box": list(self.gap_box) if self.gap_box else None,
            "candidates": list(self.candidates),
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
        }


class SliderImageSolver:
    """图片滑块求解器（同步 API）"""

    def __init__(
        self,
        *,
        min_confidence: float = 0.3,
        allow_yolo_backend: bool = True,
    ):
        self.min_confidence = min_confidence
        self.allow_yolo_backend = allow_yolo_backend

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def solve(
        self,
        background: ImageSource,
        piece: Optional[ImageSource] = None,
        *,
        distance_scale: Optional[float] = None,
        roi: Optional[Dict[str, float]] = None,
    ) -> SliderImageResult:
        """从图片定位滑块缺口。

        Args:
            background: 背景图（bytes / 路径 / ndarray，ndarray 为 BGR）。
            piece: 拼图块图，可选。提供时优先走模板匹配；缺失时走无块图检测。
            distance_scale: 图像像素到实际滑动行程的换算比例，可选。
            roi: 检测区域 ``{"x","y","width","height"}``（原图像素），可选。
                传入时先裁剪再做检测，返回坐标平移回原图坐标系；
                适合从整页截图里只检测验证码区域。
        """
        started = time.time()

        bg_img = self._decode(background)
        if bg_img is None:
            return self._failure(
                ERROR_MISSING_BACKGROUND if background is None else ERROR_IMAGE_DECODE,
                started,
            )

        piece_img = self._decode(piece) if piece is not None else None
        if piece is not None and piece_img is None:
            return self._failure(ERROR_IMAGE_DECODE, started)

        roi_offset: Optional[Tuple[int, int]] = None
        if roi is not None:
            cropped = self._crop_roi(bg_img, roi)
            if cropped is None:
                return self._failure(ERROR_INVALID_ROI, started)
            bg_img, roi_offset = cropped

        candidates: List[Dict[str, Any]] = []

        if piece_img is not None:
            if (
                piece_img.shape[0] >= bg_img.shape[0]
                or piece_img.shape[1] >= bg_img.shape[1]
            ):
                return self._failure(ERROR_PIECE_LARGER_THAN_BG, started)
            candidate = self._detect_template_edge(bg_img, piece_img)
            if candidate:
                candidates.append(candidate)
            if self._candidate_sufficient(candidate):
                return self._finish(candidates, started, distance_scale, roi_offset)
            # 模板匹配置信度不足时，用无块图检测交叉验证
            for fallback in self._no_piece_candidates(bg_img):
                candidates.append(fallback)
            return self._finish(candidates, started, distance_scale, roi_offset)

        candidates.extend(self._no_piece_candidates(bg_img))
        return self._finish(candidates, started, distance_scale, roi_offset)

    # ------------------------------------------------------------------
    # 策略实现
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_template_edge(
        bg_img: np.ndarray, piece_img: np.ndarray
    ) -> Optional[Dict[str, Any]]:
        """Canny 边缘 + matchTemplate：返回缺口左上角（不供应商校正）。"""
        try:
            bg_gray = (
                cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
                if len(bg_img.shape) == 3
                else bg_img
            )
            piece_gray = (
                cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)
                if len(piece_img.shape) == 3
                else piece_img
            )

            edge_bg = cv2.Canny(bg_gray, 100, 200)
            edge_piece = cv2.Canny(piece_gray, 100, 200)
            if not edge_piece.any():
                logger.warning("[slider-image] 拼图块图无边缘信息，模板匹配跳过")
                return None

            edge_bg_rgb = cv2.cvtColor(edge_bg, cv2.COLOR_GRAY2RGB)
            edge_piece_rgb = cv2.cvtColor(edge_piece, cv2.COLOR_GRAY2RGB)

            result = cv2.matchTemplate(
                edge_bg_rgb, edge_piece_rgb, cv2.TM_CCOEFF_NORMED
            )
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            piece_h, piece_w = edge_piece.shape[:2]
            return {
                "method": "template_edge",
                "gap_box": [
                    int(max_loc[0]),
                    int(max_loc[1]),
                    int(max_loc[0] + piece_w),
                    int(max_loc[1] + piece_h),
                ],
                "gap_x": int(max_loc[0]),
                "confidence": float(max_val),
            }
        except Exception as exc:  # noqa: BLE001 — 图像内容不可控，兜底降级到下一策略
            logger.warning(f"[slider-image] template_edge 检测失败: {exc}")
            return None

    def _no_piece_candidates(self, bg_img: np.ndarray) -> List[Dict[str, Any]]:
        """无块图检测：YOLO 后端（可选）→ 轮廓几何 → 列能量剖面。"""
        candidates: List[Dict[str, Any]] = []

        if self.allow_yolo_backend:
            yolo = self._detect_with_recognizer(bg_img)
            if yolo:
                candidates.extend(yolo)

        if not self._candidate_sufficient(candidates[0] if candidates else None):
            contour = self._detect_contour(bg_img)
            if contour:
                candidates.append(contour)
            else:
                profile = self._detect_column_profile(bg_img)
                if profile:
                    candidates.append(profile)

        return candidates

    def _detect_with_recognizer(self, bg_img: np.ndarray) -> List[Dict[str, Any]]:
        """可选深度学习后端：captcha-recognizer（YOLO/ONNX）。

        未安装或调用失败时返回空列表，静默回退到零依赖策略。
        """
        try:
            from captcha_recognizer.slider import Slider  # type: ignore[import-not-found]
        except ImportError:
            return []
        except Exception as exc:  # noqa: BLE001 — 包存在但损坏时不拖垮求解
            logger.warning(f"[slider-image] captcha_recognizer 导入失败: {exc}")
            return []

        try:
            box, confidence = Slider().identify(source=bg_img, show=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[slider-image] captcha_recognizer 推理失败: {exc}")
            return []

        if not box or confidence is None:
            return []
        x1, y1, x2, y2 = (int(round(float(v))) for v in box)
        return [
            {
                "method": "yolo",
                "gap_box": [x1, y1, x2, y2],
                "gap_x": x1,
                "confidence": float(confidence),
            }
        ]

    @staticmethod
    def _detect_contour(bg_img: np.ndarray) -> Optional[Dict[str, Any]]:
        """无块图几何检测：Canny → 轮廓筛选 → 矩形贴合度评分。"""
        try:
            gray = (
                cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
                if len(bg_img.shape) == 3
                else bg_img
            )
            bg_w = gray.shape[1]

            edges = cv2.Canny(gray, 100, 200)
            kernel = np.ones((3, 3), np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=1)

            contours, _ = cv2.findContours(
                edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            min_w = max(12, int(bg_w * _CONTOUR_MIN_WIDTH_RATIO))
            max_w = int(bg_w * _CONTOUR_MAX_WIDTH_RATIO)

            best: Optional[Dict[str, Any]] = None
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if not (min_w <= w <= max_w):
                    continue
                if not (_CONTOUR_MIN_AREA_PX <= w * h):
                    continue
                aspect = w / float(h) if h else 0.0
                if not (_CONTOUR_MIN_ASPECT <= aspect <= _CONTOUR_MAX_ASPECT):
                    continue

                # 矩形贴合度：轮廓面积 / 外接矩形面积，缺口是实心矩形时应接近 1
                area = cv2.contourArea(contour)
                fill = area / float(w * h) if w * h else 0.0
                score = 0.5 * fill + 0.3 * min(1.0, area / 5000.0) + 0.2
                confidence = min(_CONTOUR_CONFIDENCE_CAP, max(0.2, score))
                candidate = {
                    "method": "contour",
                    "gap_box": [int(x), int(y), int(x + w), int(y + h)],
                    "gap_x": int(x),
                    "confidence": round(float(confidence), 4),
                }
                if best is None or candidate["confidence"] > best["confidence"]:
                    best = candidate

            return best
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[slider-image] contour 检测失败: {exc}")
            return None

    @staticmethod
    def _detect_column_profile(bg_img: np.ndarray) -> Optional[Dict[str, Any]]:
        """轮廓检测无结果时的兜底：列方向边缘能量峰值定位。

        假设缺口左边缘在图像中部产生竖直边缘能量尖峰；
        排除最左侧 10%（拼图块起始区），对低纹理背景较有效。
        """
        try:
            gray = (
                cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
                if len(bg_img.shape) == 3
                else bg_img
            )
            h, w = gray.shape[:2]

            edges = cv2.Canny(gray, 100, 200)
            column_energy = edges.sum(axis=0).astype(np.float64)

            # 平滑后找峰；起始区置零
            kernel_size = max(3, w // 40) | 1
            smoothed = cv2.blur(column_energy.reshape(1, -1), (kernel_size, 1)).ravel()
            left_exclusion = int(w * 0.1)
            smoothed[:left_exclusion] = 0.0

            peak_x = int(np.argmax(smoothed))
            peak_value = float(smoothed[peak_x])
            if peak_value <= 0:
                return None

            # 能量峰值宽度 → 缺口宽度估计（半高宽度，夹到合理范围）
            half = peak_value / 2.0
            right = peak_x
            while right < w - 1 and smoothed[right] > half and right - peak_x < w // 3:
                right += 1
            width = max(12, min(int((right - peak_x) * 2), w // 2))

            top = int(h * 0.15)
            bottom = int(h * 0.9)
            return {
                "method": "column_profile",
                "gap_box": [peak_x, top, min(peak_x + width, w), bottom],
                "gap_x": peak_x,
                "confidence": _COLUMN_PROFILE_CONFIDENCE,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[slider-image] column_profile 检测失败: {exc}")
            return None

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _crop_roi(
        bg_img: np.ndarray, roi: Any
    ) -> Optional[Tuple[np.ndarray, Tuple[int, int]]]:
        """校验并裁剪 ROI（原图像素，边界自动收敛）；返回 (裁剪图, 裁剪原点)。

        ROI 非法（缺字段 / 非数值 / 尺寸非正 / 有效区域过小）返回 None。
        """
        try:
            x = int(round(float(roi["x"])))
            y = int(round(float(roi["y"])))
            width = int(round(float(roi["width"])))
            height = int(round(float(roi["height"])))
        except (KeyError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        img_h, img_w = bg_img.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(img_w, x + width), min(img_h, y + height)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return bg_img[y0:y1, x0:x1], (x0, y0)

    @staticmethod
    def _translate_candidates(
        candidates: List[Dict[str, Any]], offset: Tuple[int, int]
    ) -> None:
        """ROI 裁剪检测后，把候选坐标平移回原图坐标系（原地修改）。"""
        dx, dy = offset
        for item in candidates:
            box = item.get("gap_box")
            if box and len(box) == 4:
                item["gap_box"] = [
                    int(box[0]) + dx,
                    int(box[1]) + dy,
                    int(box[2]) + dx,
                    int(box[3]) + dy,
                ]
            if item.get("gap_x") is not None:
                item["gap_x"] = int(item["gap_x"]) + dx

    def _finish(
        self,
        candidates: List[Dict[str, Any]],
        started: float,
        distance_scale: Optional[float],
        roi_offset: Optional[Tuple[int, int]],
    ) -> SliderImageResult:
        if roi_offset is not None:
            self._translate_candidates(candidates, roi_offset)
        return self._success(
            self._pick_best(candidates), candidates, started, distance_scale, roi_offset
        )

    @staticmethod
    def _decode(source: ImageSource) -> Optional[np.ndarray]:
        """bytes / 路径 / ndarray → BGR ndarray；失败返回 None。"""
        try:
            if isinstance(source, np.ndarray):
                return source if len(source.shape) == 3 else cv2.cvtColor(
                    source, cv2.COLOR_GRAY2BGR
                )
            if isinstance(source, (bytes, bytearray)):
                arr = np.frombuffer(bytes(source), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                return img
            if isinstance(source, (str, Path)):
                data = Path(source).read_bytes()
                arr = np.frombuffer(data, np.uint8)
                return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as exc:  # noqa: BLE001 — 统一归为解码失败
            logger.warning(f"[slider-image] 图像解码失败: {exc}")
        return None

    def _candidate_sufficient(self, candidate: Optional[Dict[str, Any]]) -> bool:
        return bool(candidate) and float(candidate["confidence"]) >= self.min_confidence

    @staticmethod
    def _pick_best(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        return max(candidates, key=lambda item: float(item["confidence"]))

    def _success(
        self,
        best: Optional[Dict[str, Any]],
        candidates: List[Dict[str, Any]],
        started: float,
        distance_scale: Optional[float],
        roi_offset: Optional[Tuple[int, int]] = None,
    ) -> SliderImageResult:
        elapsed_ms = round(max(0.0, (time.time() - started) * 1000), 1)
        # best 需同时存在且置信度过阈值；否则视为未定位到缺口
        if not self._candidate_sufficient(best):
            return SliderImageResult(
                success=False,
                error_code=ERROR_NO_GAP_FOUND,
                candidates=candidates,
                metadata={"elapsed_ms": elapsed_ms},
            )

        distance_px = best["gap_x"]
        metadata: Dict[str, Any] = {"elapsed_ms": elapsed_ms}
        if roi_offset is not None:
            metadata["roi_offset"] = list(roi_offset)
        if distance_scale is not None:
            metadata["distance_actual_px"] = int(round(distance_px * distance_scale))
            metadata["distance_scale"] = float(distance_scale)

        return SliderImageResult(
            success=True,
            gap_x=best["gap_x"],
            distance_px=distance_px,
            confidence=round(float(best["confidence"]), 4),
            method=best["method"],
            gap_box=list(best["gap_box"]),
            candidates=candidates,
            metadata=metadata,
        )

    def _failure(self, error_code: str, started: float) -> SliderImageResult:
        return SliderImageResult(
            success=False,
            error_code=error_code,
            metadata={"elapsed_ms": round(max(0.0, (time.time() - started) * 1000), 1)},
        )


__all__ = ["SliderImageSolver", "SliderImageResult"]
