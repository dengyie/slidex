"""本地 OCR 降级引擎（RapidOCR / onnxruntime），可选依赖。

为什么是 RapidOCR 而不是 PaddleOCR：slidex 核心依赖刻意保持轻
（loguru / opencv-headless / numpy / playwright / psutil），PaddleOCR 会把
paddlepaddle 拖进来，破坏这个约束；RapidOCR 走 onnxruntime，几十 MB 量级，
放进 ``vision`` extra 合适。**未安装时本模块导入成功但调用返回
``ocr_local_unavailable``**，让降级链自然回落到多模态通道，而不是 import 期
炸掉整个包。

与 ``LlmVisionExtractor`` 的分工：本引擎返回**全图所有**字的字框
（``OcrResult.text`` 为全部识别文本），目标的挑选交给
``slidex.vision.point_select`` 的规划器——纯本地、无网络、零成本，适合
当第一遍；不可信时再升级多模态复核。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from slidex.ocr.models import OcrBox, OcrResult


def _load_image(image_bytes: Optional[bytes], image_path: Optional[Path]) -> Optional[Any]:
    """读图交给 OpenCV（已是核心依赖，不新增）。返回 None 表示无可用输入。"""
    import numpy as np

    if image_bytes:
        buf = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return image
    if image_path:
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        return image
    return None


# 延迟 import：cv2 只在本模块真正被调用时才需要，离线加载包定义时不必付出
# 它的导入成本，也避免未装 OpenCV 时影响 slidex 其余功能。
try:  # pragma: no cover - 取决于运行环境是否装了 opencv
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]


class RapidOcrExtractor:
    """本地 OCR 抽取器，实现 ``OcrTextExtractor`` 协议。"""

    def __init__(self, *, engine: Any = None):
        self._engine = engine

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception as exc:
            logger.debug(f"local ocr unavailable: {exc}")
            return None
        try:
            self._engine = RapidOCR()
        except Exception as exc:
            logger.warning(f"local ocr init failed: {exc}")
            return None
        return self._engine

    def extract(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[Path] = None,
        roi: Optional[dict] = None,
        language: Optional[str] = None,
    ) -> OcrResult:
        engine = self._ensure_engine()
        if engine is None:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="rapidocr",
                language=language,
                metadata={"error_code": "ocr_local_unavailable"},
            )
        if cv2 is None:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="rapidocr",
                language=language,
                metadata={"error_code": "opencv_unavailable"},
            )

        image = _load_image(image_bytes, image_path)
        if image is None:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="rapidocr",
                language=language,
                metadata={"error_code": "missing_image_input"},
            )

        height, width = image.shape[:2]
        try:
            raw, _elapsed = engine(image)
        except Exception as exc:
            logger.warning(f"local ocr inference failed: {exc}")
            return OcrResult(
                text="",
                confidence=0.0,
                provider="rapidocr",
                language=language,
                metadata={"error_code": "ocr_inference_failed"},
            )

        boxes: list = []
        for entry in raw or []:
            # RapidOCR 每项为 [四角点, 文本, 置信度]
            try:
                quad, text, conf = entry[0], str(entry[1]), float(entry[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not text.strip():
                continue
            xs = [float(p[0]) for p in quad]
            ys = [float(p[1]) for p in quad]
            left, top = min(xs), min(ys)
            box_w, box_h = max(xs) - left, max(ys) - top
            # 归一化到 0..1，与多模态引擎的坐标语义对齐，规划器才能
            # 无差别地消费两种引擎的结果。
            boxes.append(
                OcrBox(
                    text=text.strip(),
                    x=left / width if width else 0.0,
                    y=top / height if height else 0.0,
                    width=box_w / width if width else 0.0,
                    height=box_h / height if height else 0.0,
                    confidence=conf,
                )
            )

        return OcrResult(
            text="".join(b.text for b in boxes),
            confidence=min((b.confidence for b in boxes), default=0.0),
            boxes=boxes,
            language=language or os.environ.get("SLIDEX_OCR_LANGUAGE") or "ch",
            provider="rapidocr",
            metadata={"box_count": len(boxes)},
        )