from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class OcrBox:
    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 0.0


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float = 0.0
    boxes: List[OcrBox] = field(default_factory=list)
    language: Optional[str] = None
    provider: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OcrInput:
    image_bytes: Optional[bytes] = None
    image_path: Optional[Path] = None
    roi: Optional[Dict[str, float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
