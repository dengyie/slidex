from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Protocol, runtime_checkable

from slidex.ocr.models import OcrBox, OcrResult


@runtime_checkable
class OcrTextExtractor(Protocol):
    def extract(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[Path] = None,
        roi: Optional[Dict[str, float]] = None,
        language: Optional[str] = None,
    ) -> OcrResult:
        ...


class FakeOcrExtractor:
    def __init__(
        self,
        text: str = "",
        confidence: float = 1.0,
        language: Optional[str] = None,
    ):
        self.text = text
        self.confidence = confidence
        self.language = language

    def extract(
        self,
        *,
        image_bytes: Optional[bytes] = None,
        image_path: Optional[Path] = None,
        roi: Optional[Dict[str, float]] = None,
        language: Optional[str] = None,
    ) -> OcrResult:
        if image_bytes is None and image_path is None:
            return OcrResult(
                text="",
                confidence=0.0,
                provider="fake",
                language=language or self.language,
                metadata={"error_code": "missing_image_input"},
            )

        input_kind = "image_bytes" if image_bytes is not None else "image_path"
        metadata = {"input": input_kind}
        if image_path is not None:
            metadata["image_path"] = str(image_path)

        box_source = roi or {"x": 0, "y": 0, "width": 0, "height": 0}
        box = OcrBox(
            text=self.text,
            x=float(box_source.get("x", 0)),
            y=float(box_source.get("y", 0)),
            width=float(box_source.get("width", 0)),
            height=float(box_source.get("height", 0)),
            confidence=self.confidence,
        )

        return OcrResult(
            text=self.text,
            confidence=self.confidence,
            boxes=[box],
            language=language or self.language,
            provider="fake",
            metadata=metadata,
        )
