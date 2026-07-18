from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


SENSITIVE_TERMS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "x5sec",
    "x5secdata",
)


class ChallengeType(str, Enum):
    SLIDER_CAPTCHA = "slider_captcha"
    OCR_TEXT = "ocr_text"
    IMAGE_TEXT = "image_text"
    VISUAL_ELEMENT = "visual_element"
    MANUAL_FALLBACK = "manual_fallback"


class VisionContext(str, Enum):
    PLAYWRIGHT_PAGE = "playwright_page"
    CDP = "cdp"
    IMAGE_BYTES = "image_bytes"
    IMAGE_PATH = "image_path"
    ANDROID_SCREENSHOT_BYTES = "android_screenshot_bytes"
    MANUAL = "manual"


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        safe = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(term in lowered for term in SENSITIVE_TERMS):
                safe[key] = "[redacted]"
            else:
                safe[key] = redact_sensitive(nested)
        return safe
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


@dataclass(frozen=True)
class VisionArtifact:
    artifact_type: str
    path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "path": str(self.path),
            "metadata": redact_sensitive(self.metadata),
        }


@dataclass(frozen=True)
class ProviderManifest:
    name: str
    version: str
    challenge_types: List[ChallengeType]
    contexts: List[VisionContext]
    requires_network: bool = False
    produces_artifacts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "challenge_types": [item.value for item in self.challenge_types],
            "contexts": [item.value for item in self.contexts],
            "requires_network": self.requires_network,
            "produces_artifacts": list(self.produces_artifacts),
        }

    def supports(self, challenge_type: ChallengeType, context: VisionContext) -> bool:
        return challenge_type in self.challenge_types and context in self.contexts


@dataclass(frozen=True)
class ProviderDecision:
    challenge_type: ChallengeType
    context: VisionContext
    requested_provider: str
    selected_provider: Optional[str]
    candidates: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "challenge_type": self.challenge_type.value,
            "context": self.context.value,
            "requested_provider": self.requested_provider,
            "selected_provider": self.selected_provider,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


@dataclass
class VisualChallengeRequest:
    challenge_type: ChallengeType
    context: VisionContext
    page: Optional[Any] = None
    cdp_endpoint: Optional[str] = None
    page_url: str = ""
    image_bytes: Optional[bytes] = None
    image_path: Optional[Path] = None
    roi: Optional[Dict[str, float]] = None
    provider: str = "auto"
    timeout_ms: int = 30_000
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualChallengeResult:
    success: bool
    challenge_type: ChallengeType
    provider: str
    confidence: float = 0.0
    duration_ms: float = 0.0
    error_code: Optional[str] = None
    retryable: bool = False
    cookies: Optional[Dict[str, str]] = None
    artifacts: List[VisionArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        safe_cookies = {
            key: "[redacted]"
            for key in (self.cookies or {})
        }
        return {
            "success": self.success,
            "challenge_type": self.challenge_type.value,
            "provider": self.provider,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "cookies": safe_cookies,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": redact_sensitive(self.metadata),
        }
