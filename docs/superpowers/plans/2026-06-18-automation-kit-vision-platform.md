# Slidex Automation-Kit Vision Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `slidex` from a slider CAPTCHA solver into the optional vision capability platform for the `dengyie/automation-kit` ecosystem while preserving existing `SliderSolver` compatibility.

**Architecture:** Keep `automation_core` business-agnostic and free of `slidex`, OCR, CAPTCHA, vendor, and browser-specific imports. Add platform APIs inside `slidex` (`slidex.vision`, `slidex.ocr`, `slidex.artifacts`) and provide a one-way optional adapter in `slidex.integrations.automation_kit` that maps `VisualChallengeResult` into automation-kit `ActionResult`, `EventEnvelope`, and `ArtifactHandle`-shaped data.

**Tech Stack:** Python dataclasses, enums, Playwright page reuse, existing CDP solver path, JSON-serializable artifacts, pytest, optional extras for `automation-kit` integration and future OCR backends.

---

## Current Evidence And Boundary Notes

### Slidex Current State

- Current version is `0.3.0` in `pyproject.toml`.
- Existing public solver surface is `SliderSolver.solve(...)` and `SliderSolver.solve_on_existing_page(...)`.
- Existing provider surface is `CaptchaProvider`, `ProviderRegistry`, `ProviderElements`, and `SolveResult`.
- Existing providers are `aliyun-nocaptcha` and `geetest`.
- Existing execution contexts include self-managed browser, CDP endpoint, remote manual fallback, trajectory pool, and telemetry.
- Current worktree has uncommitted telemetry edits in:
  - `README.md`
  - `slidex/_provider_mixin.py`
  - `slidex/config.py`
  - `slidex/remote.py`
  - `slidex/scripts/slide_solve_cdp.py`
  - `slidex/solver.py`
  - `tests/test_cli.py`
  - `tests/test_slider_solver.py`

### automation-kit Recon

The inspected `dengyie/automation-kit` repository exposes:

- `automation_core.drivers.ActionResult(success: bool, message: str = "", data: Optional[Any] = None)`
- `automation_core.drivers.ArtifactHandle(artifact_type: str, path: Path, metadata: Dict[str, str])`
- `automation_core.events.EventEnvelope(event_type: str, task_id: Optional[str], payload: Dict[str, Any])`
- `automation_core.events.ArtifactEvent`, `ErrorEvent`, `TaskStartEvent`, `TaskEndEvent`
- `automation_runner.reports` redacts sensitive keys containing `authorization`, `cookie`, `password`, `secret`, or `token`
- `tests/structure/test_boundaries.py` forbids business terms and concrete driver terms inside `automation_core`
- `docs/artifacts.md` defines artifact storage as `<artifact-root>/<run-id>/<artifact-type>/<artifact-name>`
- `docs/ecosystem.md` currently lists `automation-plugin-ocr` as the optional OCR plugin
- `docs/compatibility.md` currently includes `automation-plugin-ocr` in the verification matrix

### Non-Negotiable Architecture Boundary

- Do not add `slidex` imports to `automation_core`.
- Do not add OCR, CAPTCHA, visual, vendor, `geetest`, `aliyun`, browser, Appium, Selenium, Damai, or Dianping concepts to `automation_core`.
- Keep application-level usage dependency-injected. Applications choose whether to instantiate `slidex` capabilities.
- `slidex` maps outward to automation-kit contracts; automation-kit does not map inward to `slidex`.

---

## Target Public API

### `slidex.vision`

```python
from slidex.vision import (
    ChallengeType,
    VisualChallengeRequest,
    VisualChallengeResult,
    VisualChallengeSolver,
)
```

Required `ChallengeType` values:

```python
class ChallengeType(str, Enum):
    SLIDER_CAPTCHA = "slider_captcha"
    OCR_TEXT = "ocr_text"
    IMAGE_TEXT = "image_text"
    VISUAL_ELEMENT = "visual_element"
    MANUAL_FALLBACK = "manual_fallback"
```

Required `VisualChallengeResult` fields:

```python
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
```

### `slidex.ocr`

```python
from slidex.ocr import OcrTextExtractor, OcrResult, FakeOcrExtractor
```

Required `OcrResult` fields:

```python
@dataclass
class OcrResult:
    text: str
    confidence: float = 0.0
    boxes: List[OcrBox] = field(default_factory=list)
    language: Optional[str] = None
    provider: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
```

### `slidex.integrations.automation_kit`

```python
from slidex.integrations.automation_kit import (
    to_action_result,
    to_artifacts,
    to_events,
)
```

This module must import automation-kit only inside functions or under `TYPE_CHECKING`, so `slidex` core remains usable without `automation-kit` installed.

---

## File Structure

### New Files

- `slidex/vision/__init__.py`
  - Re-export platform API.
- `slidex/vision/models.py`
  - Define `ChallengeType`, `VisionContext`, `VisionArtifact`, `VisualChallengeRequest`, `VisualChallengeResult`, `ProviderManifest`, `ProviderDecision`.
- `slidex/vision/solver.py`
  - Define `VisualChallengeSolver` orchestration facade.
- `slidex/vision/providers.py`
  - Provide manifest-aware registry helpers that wrap or extend current `ProviderRegistry`.
- `slidex/vision/artifacts.py`
  - Normalize artifact metadata and redaction helpers.
- `slidex/ocr/__init__.py`
  - Re-export OCR API.
- `slidex/ocr/models.py`
  - Define `OcrBox`, `OcrResult`, `OcrInput`.
- `slidex/ocr/extractors.py`
  - Define `OcrTextExtractor` protocol/base class and `FakeOcrExtractor`.
- `slidex/integrations/__init__.py`
  - Namespace package for optional integrations.
- `slidex/integrations/automation_kit.py`
  - Map `VisualChallengeResult` to automation-kit-compatible result/event/artifact shapes.
- `tests/test_vision_models.py`
  - Unit tests for unified visual models.
- `tests/test_vision_solver.py`
  - Unit tests for `VisualChallengeSolver`.
- `tests/test_ocr.py`
  - Unit tests for OCR API and fake extractor.
- `tests/test_provider_manifest.py`
  - Unit tests for provider manifest filtering and decision records.
- `tests/test_automation_kit_integration.py`
  - Unit tests proving optional adapter works with and without automation-kit installed.
- `docs/automation-kit-vision-platform.md`
  - Human-facing architecture and migration documentation.

### Existing Files To Modify

- `slidex/__init__.py`
  - Export new platform API without breaking existing exports.
- `slidex/providers/__init__.py`
  - Add `manifest` support to `CaptchaProvider` and filtering methods to `ProviderRegistry`.
- `slidex/providers/aliyun.py`
  - Add provider manifest.
- `slidex/providers/geetest.py`
  - Add provider manifest.
- `slidex/solver.py`
  - Add a direct Playwright `Page` entry point while preserving `solve()` and `solve_on_existing_page()`.
- `slidex/scripts/slide_solve_cdp.py`
  - Add unified `VisualChallengeResult`-compatible output fields.
- `pyproject.toml`
  - Add optional extras for `automation-kit` and future OCR providers.
- `README.md`
  - Update positioning from slider-only library to automation-kit vision platform.
- `README_EN.md`
  - Mirror README changes.
- `docs/ARCHITECTURE.md`
  - Update architecture to include `vision`, `ocr`, optional integration, and manifest registry.
- `docs/PROVIDER_GUIDE.md`
  - Update provider guide with manifest requirements and artifact rules.

### Cross-Repository Follow-Up Files

These are not edited from this `slidex` plan unless the worker is in the corresponding repository:

- `automation-kit/docs/ecosystem.md`
  - Replace `automation-plugin-ocr` recommendation with `slidex` vision capability platform.
- `automation-kit/docs/compatibility.md`
  - Add `slidex` to compatibility matrix and remove `automation-plugin-ocr` as the preferred direction.
- `automation-kit/tests/structure/test_boundaries.py`
  - Add assertions that `automation_core` does not import `slidex`, OCR, CAPTCHA, or visual-provider terms.
- `automation-app-damai`
  - Replace `automation_plugin_ocr.fake.FakeOcrExtractor` imports with `slidex.ocr.FakeOcrExtractor`.
- `automation-app-dianping`
  - Add a negative capability test proving no visual integration is required when not enabled.

---

## Version Roadmap

### `slidex 0.4`: Unified Vision API + OCR

Goal: add `slidex.vision`, `slidex.ocr`, fake OCR, unified results, Playwright page reuse, and compatibility-preserving slider routing.

Exit criteria:

- `VisualChallengeSolver.solve(...)` handles `slider_captcha`, `ocr_text`, and `image_text`.
- `SliderSolver.solve()` remains compatible.
- `OcrTextExtractor` supports `image_bytes`, `image_path`, and ROI arguments.
- `FakeOcrExtractor` works offline.
- CLI and Python SDK expose consistent `VisualChallengeResult` fields.

### `slidex 0.5`: automation-kit Optional Integration + Artifact Protocol

Goal: provide stable artifact structures, optional automation-kit mapping, provider manifests, provider filtering, and decision telemetry.

Exit criteria:

- `slidex.integrations.automation_kit` works when automation-kit is installed.
- `slidex` imports and tests pass without automation-kit installed.
- Provider registry lists and filters by challenge type and context.
- `VisualChallengeResult.artifacts` is stable and sensitive metadata is redacted.
- automation-kit docs point vision capability to `slidex`.

### `slidex 0.6`: Generic Manual Fallback

Goal: generalize remote manual fallback beyond slider CAPTCHA.

Exit criteria:

- `slider_captcha` enters manual fallback and returns `VisualChallengeResult`.
- `ocr_text` can enter manual confirm/correction flow.
- fallback session token, timeout, audit, and telemetry rules are stable.
- failure paths return error artifacts or telemetry artifacts.

### `slidex 1.0`: Protocol Freeze

Goal: freeze public platform contracts for automation-kit consumers.

Exit criteria:

- `VisualChallengeRequest`, `VisualChallengeResult`, `VisionArtifact`, `ProviderManifest`, `OcrResult`, and integration adapter behavior are versioned and documented.
- provider package split strategy is documented.
- compatibility tests cover automation-kit, damai, dianping, and slidex.

---

## Task 1: Add Unified Vision Models

**Files:**

- Create: `slidex/vision/models.py`
- Create: `slidex/vision/__init__.py`
- Test: `tests/test_vision_models.py`

- [ ] **Step 1: Write the failing model tests**

Add `tests/test_vision_models.py`:

```python
from pathlib import Path

from slidex.vision import (
    ChallengeType,
    ProviderManifest,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)


def test_visual_challenge_result_serializes_core_fields():
    result = VisualChallengeResult(
        success=True,
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        provider="geetest",
        confidence=0.91,
        duration_ms=1234.5,
        cookies={"session": "abc"},
        artifacts=[
            VisionArtifact(
                artifact_type="telemetry",
                path=Path("artifacts/run-1/telemetry/events.jsonl"),
                metadata={"source": "unit"},
            )
        ],
        metadata={"slide_code": 0},
    )

    payload = result.to_dict()

    assert payload["success"] is True
    assert payload["challenge_type"] == "slider_captcha"
    assert payload["provider"] == "geetest"
    assert payload["confidence"] == 0.91
    assert payload["cookies"] == {"session": "[redacted]"}
    assert payload["artifacts"][0]["artifact_type"] == "telemetry"
    assert payload["metadata"]["slide_code"] == 0


def test_request_accepts_multiple_context_inputs():
    request = VisualChallengeRequest(
        challenge_type=ChallengeType.OCR_TEXT,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"fake-image",
        roi={"x": 1, "y": 2, "width": 3, "height": 4},
        metadata={"language": "zh-CN"},
    )

    assert request.challenge_type == ChallengeType.OCR_TEXT
    assert request.context == VisionContext.IMAGE_BYTES
    assert request.image_bytes == b"fake-image"
    assert request.roi["width"] == 3


def test_provider_manifest_declares_capabilities():
    manifest = ProviderManifest(
        name="geetest",
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )

    assert manifest.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.CDP)
    assert not manifest.supports(ChallengeType.OCR_TEXT, VisionContext.IMAGE_BYTES)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest -q tests/test_vision_models.py
```

Expected:

```text
ModuleNotFoundError: No module named 'slidex.vision'
```

- [ ] **Step 3: Implement the models**

Create `slidex/vision/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


SENSITIVE_TERMS = ("authorization", "cookie", "password", "secret", "token")


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
        return {
            "success": self.success,
            "challenge_type": self.challenge_type.value,
            "provider": self.provider,
            "confidence": self.confidence,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "cookies": redact_sensitive(self.cookies or {}),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": redact_sensitive(self.metadata),
        }
```

Create `slidex/vision/__init__.py`:

```python
from slidex.vision.models import (
    ChallengeType,
    ProviderManifest,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
    redact_sensitive,
)

__all__ = [
    "ChallengeType",
    "ProviderManifest",
    "VisionArtifact",
    "VisionContext",
    "VisualChallengeRequest",
    "VisualChallengeResult",
    "redact_sensitive",
]
```

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
pytest -q tests/test_vision_models.py
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
git add slidex/vision tests/test_vision_models.py
git commit -m "feat(阶段0.4): add unified vision models"
```

---

## Task 2: Add OCR API And Fake Extractor

**Files:**

- Create: `slidex/ocr/models.py`
- Create: `slidex/ocr/extractors.py`
- Create: `slidex/ocr/__init__.py`
- Test: `tests/test_ocr.py`

- [ ] **Step 1: Write the failing OCR tests**

Add `tests/test_ocr.py`:

```python
from pathlib import Path

from slidex.ocr import FakeOcrExtractor, OcrResult, OcrTextExtractor


def test_fake_ocr_extracts_from_image_bytes():
    extractor = FakeOcrExtractor(text="大麦", confidence=0.98, language="zh-CN")

    result = extractor.extract(image_bytes=b"fake-png")

    assert isinstance(extractor, OcrTextExtractor)
    assert isinstance(result, OcrResult)
    assert result.text == "大麦"
    assert result.confidence == 0.98
    assert result.language == "zh-CN"
    assert result.provider == "fake"
    assert result.metadata["input"] == "image_bytes"


def test_fake_ocr_extracts_from_image_path(tmp_path):
    image_path = tmp_path / "captcha.png"
    image_path.write_bytes(b"fake-png")
    extractor = FakeOcrExtractor(text="验票")

    result = extractor.extract(image_path=image_path)

    assert result.text == "验票"
    assert result.metadata["input"] == "image_path"
    assert result.metadata["image_path"] == str(image_path)


def test_fake_ocr_accepts_roi():
    extractor = FakeOcrExtractor(text="A12")

    result = extractor.extract(
        image_bytes=b"fake-png",
        roi={"x": 10, "y": 20, "width": 30, "height": 40},
    )

    assert result.boxes[0].text == "A12"
    assert result.boxes[0].x == 10
    assert result.boxes[0].width == 30


def test_ocr_requires_image_input():
    extractor = FakeOcrExtractor(text="unused")

    result = extractor.extract()

    assert result.text == ""
    assert result.confidence == 0.0
    assert result.metadata["error_code"] == "missing_image_input"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
pytest -q tests/test_ocr.py
```

Expected:

```text
ModuleNotFoundError: No module named 'slidex.ocr'
```

- [ ] **Step 3: Implement OCR models**

Create `slidex/ocr/models.py`:

```python
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
```

- [ ] **Step 4: Implement fake OCR extractor**

Create `slidex/ocr/extractors.py`:

```python
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
```

Create `slidex/ocr/__init__.py`:

```python
from slidex.ocr.extractors import FakeOcrExtractor, OcrTextExtractor
from slidex.ocr.models import OcrBox, OcrInput, OcrResult

__all__ = [
    "FakeOcrExtractor",
    "OcrBox",
    "OcrInput",
    "OcrResult",
    "OcrTextExtractor",
]
```

- [ ] **Step 5: Run OCR tests**

Run:

```bash
pytest -q tests/test_ocr.py
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Commit**

```bash
git add slidex/ocr tests/test_ocr.py
git commit -m "feat(阶段0.4): add OCR API and fake extractor"
```

---

## Task 3: Add VisualChallengeSolver

**Files:**

- Create: `slidex/vision/solver.py`
- Modify: `slidex/vision/__init__.py`
- Modify: `slidex/solver.py`
- Test: `tests/test_vision_solver.py`

- [ ] **Step 1: Write failing solver tests**

Add `tests/test_vision_solver.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from slidex.ocr import FakeOcrExtractor
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)


@pytest.mark.asyncio
async def test_visual_solver_routes_ocr_text_to_extractor():
    solver = VisualChallengeSolver(ocr_extractor=FakeOcrExtractor(text="大麦", confidence=0.9))

    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.OCR_TEXT,
            context=VisionContext.IMAGE_BYTES,
            image_bytes=b"fake",
        )
    )

    assert result.success is True
    assert result.challenge_type == ChallengeType.OCR_TEXT
    assert result.provider == "fake"
    assert result.confidence == 0.9
    assert result.metadata["text"] == "大麦"


@pytest.mark.asyncio
@patch("slidex.vision.solver.SliderSolver")
async def test_visual_solver_routes_slider_to_existing_page(mock_slider_class):
    slider = AsyncMock()
    slider.solve_on_existing_page = AsyncMock(return_value=(True, {"session": "abc"}))
    slider.get_telemetry_summary.return_value = {"run_id": "r1", "status": "success"}
    mock_slider_class.return_value = slider

    solver = VisualChallengeSolver()
    result = await solver.solve(
        VisualChallengeRequest(
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            context=VisionContext.CDP,
            cdp_endpoint="ws://localhost:9222/devtools/browser/1",
            page_url="https://example.test",
            provider="auto",
        )
    )

    assert result.success is True
    assert result.challenge_type == ChallengeType.SLIDER_CAPTCHA
    assert result.cookies == {"session": "abc"}
    assert result.metadata["telemetry"]["status"] == "success"
    mock_slider_class.assert_called_once()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest -q tests/test_vision_solver.py
```

Expected:

```text
ImportError: cannot import name 'VisualChallengeSolver'
```

- [ ] **Step 3: Add direct Playwright page entry point to SliderSolver**

Modify `slidex/solver.py` with this method:

```python
async def solve_on_page(self, page, page_url: str = "") -> Tuple[bool, Optional[dict]]:
    """Solve on an already-owned Playwright Page without taking browser lifecycle ownership."""
    self.last_fallback_used = None
    self._is_cdp_mode = True
    self.page = page
    self.context = page.context
    self._emit_telemetry_event("solve_started", mode="playwright_page", page_url=page_url)
    try:
        try:
            self._cdp = await self.context.new_cdp_session(self.page)
        except Exception:
            self._cdp = None
        success, cookies = await self._run_solve_loop(page_url)
        self._finalize_telemetry(
            success=success,
            status="success" if success else "failed",
            cookies=cookies,
            extra={"failure_reason": None if success else "solve_failed"},
        )
        return success, cookies
    except Exception as exc:
        self._finalize_telemetry(
            success=False,
            status="exception",
            cookies=None,
            extra={"failure_reason": str(exc)},
        )
        return False, None
```

Add a compatibility test in `tests/test_slider_solver.py`:

```python
def test_solve_on_page_method_exists():
    import inspect

    assert hasattr(SliderSolver, "solve_on_page")
    assert inspect.iscoroutinefunction(SliderSolver.solve_on_page)
```

- [ ] **Step 4: Implement VisualChallengeSolver**

Create `slidex/vision/solver.py`:

```python
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from slidex.ocr import FakeOcrExtractor, OcrTextExtractor
from slidex.solver import SliderSolver
from slidex.vision.models import (
    ChallengeType,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
)


class VisualChallengeSolver:
    def __init__(
        self,
        *,
        ocr_extractor: Optional[OcrTextExtractor] = None,
        slider_solver_factory=SliderSolver,
    ):
        self.ocr_extractor = ocr_extractor or FakeOcrExtractor()
        self.slider_solver_factory = slider_solver_factory

    async def solve(self, request: VisualChallengeRequest) -> VisualChallengeResult:
        started = time.time()
        if request.challenge_type in {ChallengeType.OCR_TEXT, ChallengeType.IMAGE_TEXT}:
            return self._solve_ocr(request, started)
        if request.challenge_type == ChallengeType.SLIDER_CAPTCHA:
            return await self._solve_slider(request, started)
        return VisualChallengeResult(
            success=False,
            challenge_type=request.challenge_type,
            provider=request.provider,
            duration_ms=self._duration_ms(started),
            error_code="unsupported_challenge_type",
            retryable=False,
            artifacts=[],
            metadata={"context": request.context.value},
        )

    def _solve_ocr(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        result = self.ocr_extractor.extract(
            image_bytes=request.image_bytes,
            image_path=request.image_path,
            roi=request.roi,
            language=request.metadata.get("language"),
        )
        success = bool(result.text)
        return VisualChallengeResult(
            success=success,
            challenge_type=request.challenge_type,
            provider=result.provider,
            confidence=result.confidence,
            duration_ms=self._duration_ms(started),
            error_code=None if success else result.metadata.get("error_code", "ocr_failed"),
            retryable=not success,
            cookies=None,
            artifacts=[],
            metadata={
                "text": result.text,
                "language": result.language,
                "boxes": [box.__dict__ for box in result.boxes],
                **result.metadata,
            },
        )

    async def _solve_slider(self, request: VisualChallengeRequest, started: float) -> VisualChallengeResult:
        slider = self.slider_solver_factory(
            cookie_id=str(request.metadata.get("cookie_id", "default")),
            provider=request.provider,
        )
        if request.context == VisionContext.CDP:
            success, cookies = await slider.solve_on_existing_page(
                cdp_endpoint=request.cdp_endpoint or "",
                page_url=request.page_url,
            )
        elif request.context == VisionContext.PLAYWRIGHT_PAGE:
            success, cookies = await slider.solve_on_page(request.page, page_url=request.page_url)
        else:
            return VisualChallengeResult(
                success=False,
                challenge_type=request.challenge_type,
                provider=request.provider,
                duration_ms=self._duration_ms(started),
                error_code="unsupported_slider_context",
                retryable=False,
            )

        telemetry = slider.get_telemetry_summary()
        return VisualChallengeResult(
            success=success,
            challenge_type=request.challenge_type,
            provider=str(telemetry.get("provider_name") or request.provider),
            confidence=float(telemetry.get("confidence") or 0.0),
            duration_ms=self._duration_ms(started),
            error_code=None if success else str(telemetry.get("failure_reason") or "solve_failed"),
            retryable=not success,
            cookies=cookies,
            artifacts=[
                VisionArtifact(
                    artifact_type="telemetry",
                    path=Path("telemetry") / f"{telemetry.get('run_id', 'unknown')}.json",
                    metadata={"run_id": str(telemetry.get("run_id", ""))},
                )
            ],
            metadata={"telemetry": telemetry},
        )

    @staticmethod
    def _duration_ms(started: float) -> float:
        return round(max(0.0, (time.time() - started) * 1000), 1)
```

Modify `slidex/vision/__init__.py`:

```python
from slidex.vision.solver import VisualChallengeSolver

__all__.append("VisualChallengeSolver")
```

- [ ] **Step 5: Run vision solver tests**

Run:

```bash
pytest -q tests/test_vision_solver.py tests/test_slider_solver.py::TestCDPMode::test_solve_on_page_method_exists
```

Expected:

```text
3 passed
```

- [ ] **Step 6: Commit**

```bash
git add slidex/vision slidex/solver.py tests/test_vision_solver.py tests/test_slider_solver.py
git commit -m "feat(阶段0.4): add visual challenge solver"
```

---

## Task 4: Add Provider Manifest And Capability Filtering

**Files:**

- Modify: `slidex/providers/__init__.py`
- Modify: `slidex/providers/aliyun.py`
- Modify: `slidex/providers/geetest.py`
- Create: `tests/test_provider_manifest.py`

- [ ] **Step 1: Write failing provider manifest tests**

Add `tests/test_provider_manifest.py`:

```python
from slidex import AliyunNoCaptchaProvider, GeeTestProvider, ProviderRegistry
from slidex.vision import ChallengeType, VisionContext


def test_builtin_provider_manifests_declare_slider_capability():
    aliyun = AliyunNoCaptchaProvider().manifest
    geetest = GeeTestProvider().manifest

    assert aliyun.name == "aliyun-nocaptcha"
    assert aliyun.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.PLAYWRIGHT_PAGE)
    assert geetest.name == "geetest"
    assert geetest.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.CDP)


def test_registry_lists_provider_manifests():
    manifests = ProviderRegistry.list_manifests()
    names = {manifest.name for manifest in manifests}

    assert "aliyun-nocaptcha" in names
    assert "geetest" in names


def test_registry_filters_by_challenge_type_and_context():
    providers = ProviderRegistry.filter(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
    )

    names = {provider.name for provider in providers}
    assert {"aliyun-nocaptcha", "geetest"}.issubset(names)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest -q tests/test_provider_manifest.py
```

Expected:

```text
AttributeError: 'AliyunNoCaptchaProvider' object has no attribute 'manifest'
```

- [ ] **Step 3: Add manifest support**

Modify `slidex/providers/__init__.py`:

```python
from slidex.vision import ChallengeType, ProviderManifest, VisionContext

class CaptchaProvider(ABC):
    manifest = ProviderManifest(
        name="base",
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["telemetry"],
    )
```

Add to `ProviderRegistry`:

```python
@classmethod
def list_manifests(cls):
    return [cls.get(name).manifest for _, name in cls._detection_order]

@classmethod
def filter(cls, *, challenge_type, context):
    providers = []
    for _, name in cls._detection_order:
        provider = cls.get(name)
        if provider.manifest.supports(challenge_type, context):
            providers.append(provider)
    return providers
```

Modify `slidex/providers/aliyun.py`:

```python
from slidex.vision import ChallengeType, ProviderManifest, VisionContext

manifest = ProviderManifest(
    name="aliyun-nocaptcha",
    version="0.1.0",
    challenge_types=[ChallengeType.SLIDER_CAPTCHA],
    contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
    requires_network=False,
    produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
)
```

Modify `slidex/providers/geetest.py` with the same shape and `name="geetest"`.

- [ ] **Step 4: Run provider manifest tests**

Run:

```bash
pytest -q tests/test_provider_manifest.py tests/test_providers.py tests/test_provider_integration.py
```

Expected:

```text
All selected tests pass
```

- [ ] **Step 5: Commit**

```bash
git add slidex/providers tests/test_provider_manifest.py
git commit -m "feat(阶段0.5): add provider manifests"
```

---

## Task 5: Add automation-kit Optional Integration

**Files:**

- Create: `slidex/integrations/__init__.py`
- Create: `slidex/integrations/automation_kit.py`
- Modify: `pyproject.toml`
- Test: `tests/test_automation_kit_integration.py`

- [ ] **Step 1: Write failing integration tests**

Add `tests/test_automation_kit_integration.py`:

```python
from pathlib import Path

from slidex.integrations.automation_kit import to_action_result, to_artifacts, to_events
from slidex.vision import ChallengeType, VisionArtifact, VisualChallengeResult


def _result():
    return VisualChallengeResult(
        success=True,
        challenge_type=ChallengeType.OCR_TEXT,
        provider="fake",
        confidence=0.88,
        duration_ms=12.3,
        artifacts=[
            VisionArtifact(
                artifact_type="ocr_result",
                path=Path("artifacts/run-1/ocr/result.json"),
                metadata={"token": "secret", "source": "unit"},
            )
        ],
        metadata={"text": "大麦"},
    )


def test_to_action_result_returns_automation_shape_without_import_requirement():
    action = to_action_result(_result())

    assert action.success is True
    assert action.message == "ocr_text solved by fake"
    assert action.data["challenge_type"] == "ocr_text"
    assert action.data["metadata"]["text"] == "大麦"


def test_to_artifacts_redacts_sensitive_metadata():
    artifacts = to_artifacts(_result())

    assert artifacts[0].artifact_type == "ocr_result"
    assert artifacts[0].metadata["token"] == "[redacted]"
    assert artifacts[0].metadata["source"] == "unit"


def test_to_events_returns_task_scoped_events():
    events = to_events(_result(), task_id="task-1", task_name="vision")

    assert [event.event_type for event in events] == ["artifact", "task.end"]
    assert events[0].task_id == "task-1"
    assert events[-1].payload["outcome"] == "succeeded"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest -q tests/test_automation_kit_integration.py
```

Expected:

```text
ModuleNotFoundError: No module named 'slidex.integrations'
```

- [ ] **Step 3: Implement adapter with lazy imports**

Create `slidex/integrations/__init__.py`:

```python
__all__ = []
```

Create `slidex/integrations/automation_kit.py`:

```python
from __future__ import annotations

from typing import List

from slidex.vision import VisualChallengeResult, redact_sensitive


def to_action_result(result: VisualChallengeResult):
    from automation_core.drivers import ActionResult

    payload = result.to_dict()
    return ActionResult(
        success=result.success,
        message=f"{result.challenge_type.value} solved by {result.provider}",
        data=payload,
    )


def to_artifacts(result: VisualChallengeResult):
    from automation_core.drivers import ArtifactHandle

    return [
        ArtifactHandle(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            metadata=redact_sensitive(artifact.metadata),
        )
        for artifact in result.artifacts
    ]


def to_events(result: VisualChallengeResult, *, task_id: str, task_name: str) -> List[object]:
    from automation_core.events import ArtifactEvent, TaskEndEvent

    events = [
        ArtifactEvent(
            task_name=task_name,
            task_id=task_id,
            artifact_type=artifact.artifact_type,
            path=str(artifact.path),
        ).to_envelope()
        for artifact in result.artifacts
    ]
    events.append(
        TaskEndEvent(
            task_name=task_name,
            task_id=task_id,
            outcome="succeeded" if result.success else "failed",
        ).to_envelope()
    )
    return events
```

Modify `pyproject.toml`:

```toml
[project.optional-dependencies]
automation-kit = [
    "automation-kit>=0.1.0",
]
```

- [ ] **Step 4: Run integration tests**

Run:

```bash
pytest -q tests/test_automation_kit_integration.py
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
git add slidex/integrations pyproject.toml tests/test_automation_kit_integration.py
git commit -m "feat(阶段0.5): add automation-kit adapter"
```

---

## Task 6: Standardize Artifacts And Telemetry Evidence

**Files:**

- Create: `slidex/vision/artifacts.py`
- Modify: `slidex/vision/models.py`
- Modify: `slidex/solver.py`
- Test: `tests/test_vision_artifacts.py`

- [ ] **Step 1: Write failing artifact tests**

Add `tests/test_vision_artifacts.py`:

```python
from slidex.vision.artifacts import build_artifact_path, safe_artifact_metadata


def test_build_artifact_path_sanitizes_components(tmp_path):
    path = build_artifact_path(
        root=tmp_path,
        run_id="../run 1",
        artifact_type="../telemetry",
        name="../events.jsonl",
    )

    assert path == tmp_path / "run_1" / "telemetry" / "events.jsonl"


def test_safe_artifact_metadata_redacts_sensitive_keys():
    metadata = safe_artifact_metadata(
        {
            "source": "unit",
            "token": "secret",
            "nested": {"cookie": "abc"},
        }
    )

    assert metadata["source"] == "unit"
    assert metadata["token"] == "[redacted]"
    assert metadata["nested"]["cookie"] == "[redacted]"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest -q tests/test_vision_artifacts.py
```

Expected:

```text
ModuleNotFoundError: No module named 'slidex.vision.artifacts'
```

- [ ] **Step 3: Implement artifact helpers**

Create `slidex/vision/artifacts.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from slidex.vision.models import redact_sensitive


def _sanitize_component(value: str, field_name: str) -> str:
    cleaned = str(value).replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."}:
        raise ValueError(f"invalid {field_name}")
    return cleaned.replace(" ", "_")


def build_artifact_path(root: Path, run_id: str, artifact_type: str, name: str) -> Path:
    return (
        root
        / _sanitize_component(run_id, "run_id")
        / _sanitize_component(artifact_type, "artifact_type")
        / _sanitize_component(name, "artifact name")
    )


def safe_artifact_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return redact_sensitive(metadata)
```

- [ ] **Step 4: Run artifact tests**

Run:

```bash
pytest -q tests/test_vision_artifacts.py
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add slidex/vision/artifacts.py tests/test_vision_artifacts.py
git commit -m "feat(阶段0.5): standardize vision artifacts"
```

---

## Task 7: Platformize Manual Fallback

**Files:**

- Create: `slidex/vision/manual.py`
- Modify: `slidex/remote.py`
- Modify: `slidex/api.py`
- Test: `tests/test_manual_fallback.py`

- [ ] **Step 1: Write failing manual fallback tests**

Add `tests/test_manual_fallback.py`:

```python
import pytest

from slidex.vision import ChallengeType
from slidex.vision.manual import ManualFallbackSession


def test_manual_fallback_session_redacts_token():
    session = ManualFallbackSession(
        session_id="s1",
        challenge_type=ChallengeType.OCR_TEXT,
        token="secret-token",
        timeout_s=60,
    )

    payload = session.to_dict()

    assert payload["session_id"] == "s1"
    assert payload["challenge_type"] == "ocr_text"
    assert payload["token"] == "[redacted]"
    assert payload["timeout_s"] == 60


@pytest.mark.asyncio
async def test_manual_fallback_can_complete_ocr_correction():
    session = ManualFallbackSession(
        session_id="s1",
        challenge_type=ChallengeType.OCR_TEXT,
        token="secret-token",
        timeout_s=60,
    )

    result = session.complete_text("人工修正")

    assert result.success is True
    assert result.challenge_type == ChallengeType.OCR_TEXT
    assert result.provider == "manual"
    assert result.metadata["text"] == "人工修正"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest -q tests/test_manual_fallback.py
```

Expected:

```text
ModuleNotFoundError: No module named 'slidex.vision.manual'
```

- [ ] **Step 3: Implement manual session model**

Create `slidex/vision/manual.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from slidex.vision.models import ChallengeType, VisualChallengeResult


@dataclass
class ManualFallbackSession:
    session_id: str
    challenge_type: ChallengeType
    token: str
    timeout_s: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "session_id": self.session_id,
            "challenge_type": self.challenge_type.value,
            "token": "[redacted]",
            "timeout_s": self.timeout_s,
        }

    def complete_text(self, text: str) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=True,
            challenge_type=self.challenge_type,
            provider="manual",
            confidence=1.0,
            metadata={"text": text, "session_id": self.session_id},
        )
```

- [ ] **Step 4: Integrate with existing remote controller**

Modify `slidex/remote.py` so active sessions store:

```python
"challenge_type": challenge_type,
"audit": [],
```

Add a method:

```python
def record_audit(self, session_id: str, event: str, **metadata):
    if session_id in self.active_sessions:
        self.active_sessions[session_id].setdefault("audit", []).append(
            {"event": event, "metadata": metadata}
        )
```

Do not remove the current slider-specific control page in this task; generic UI work belongs in a later task.

- [ ] **Step 5: Run manual fallback tests**

Run:

```bash
pytest -q tests/test_manual_fallback.py tests/test_api_security.py
```

Expected:

```text
All selected tests pass
```

- [ ] **Step 6: Commit**

```bash
git add slidex/vision/manual.py slidex/remote.py tests/test_manual_fallback.py
git commit -m "feat(阶段0.6): add generic manual fallback model"
```

---

## Task 8: Cross-Repository Migration Design

**Files In automation-kit Repository:**

- Modify: `docs/ecosystem.md`
- Modify: `docs/compatibility.md`
- Modify: `tests/structure/test_boundaries.py`

**Files In automation-app-damai Repository:**

- Modify imports from `automation_plugin_ocr.fake.FakeOcrExtractor` to `slidex.ocr.FakeOcrExtractor`
- Add optional capability test for enabled vision.

**Files In automation-app-dianping Repository:**

- Add negative capability test proving absence of `slidex` does not affect non-vision workflows.

- [ ] **Step 1: automation-kit boundary test**

In `automation-kit/tests/structure/test_boundaries.py`, add:

```python
def test_core_has_no_slidex_or_vision_terms():
    core_text = _read_core_text().lower()

    forbidden = [
        "slidex",
        "captcha",
        "ocr",
        "geetest",
        "aliyun",
        "visualchallenge",
    ]

    for term in forbidden:
        assert term not in core_text
```

- [ ] **Step 2: automation-kit docs**

In `automation-kit/docs/ecosystem.md`, replace the plugin section with:

```markdown
Visual capabilities such as OCR, CAPTCHA handling, screenshot analysis, and
manual visual fallback are provided by `slidex`. `automation-kit` does not
depend on `slidex`; applications inject it when they need vision capability.
```

In `automation-kit/docs/compatibility.md`, update the matrix:

```text
slidex:
  pytest -q
  verifies optional automation-kit adapter, OCR fake extractor, provider manifests
```

- [ ] **Step 3: automation-app-damai migration**

Replace:

```python
from automation_plugin_ocr.fake import FakeOcrExtractor
```

with:

```python
from slidex.ocr import FakeOcrExtractor
```

Add a test:

```python
def test_slidex_fake_ocr_capability_enabled():
    extractor = FakeOcrExtractor(text="damai")
    result = extractor.extract(image_bytes=b"fake")

    assert result.text == "damai"
    assert result.provider == "fake"
```

- [ ] **Step 4: automation-app-dianping non-vision test**

In `automation-app-dianping/tests/test_workflow.py`, keep the existing workflow contract test and add these source-level optionality tests:

```python
def test_dianping_package_has_no_visual_platform_dependency_terms():
    package_root = Path(__file__).resolve().parents[1] / "automation_app_dianping"
    source_text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in package_root.rglob("*.py")
    )

    forbidden = [
        "slidex",
        "automation_plugin_ocr",
        "automation-plugin-ocr",
        "captcha",
        "ocr",
        "geetest",
        "aliyun",
    ]

    for term in forbidden:
        assert term not in source_text


def test_dianping_pyproject_has_no_visual_platform_dependency():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject_text = pyproject_path.read_text(encoding="utf-8").lower()

    forbidden = [
        "slidex",
        "automation-plugin-ocr",
        "automation_plugin_ocr",
    ]

    for term in forbidden:
        assert term not in pyproject_text
```

These tests are repository-specific to the current `automation-app-dianping` shape: `automation_app_dianping/workflow.py` only creates a generic `ManagedWorkflow` with `launch_app` and screenshot artifact steps, and `tests/test_workflow.py` already proves the workflow runs through the public runner contract with `FakeSession`.

- [ ] **Step 5: Run cross-repo verification**

Run in each repository:

```bash
pytest -q
```

Expected:

```text
All suites pass without automation-kit core depending on slidex
```

Commit messages:

```bash
git commit -m "docs: route visual capabilities to slidex"
git commit -m "feat: use slidex fake OCR capability"
git commit -m "test: keep dianping vision integration optional"
```

---

## Verification Matrix

### Slidex

```bash
pytest -q
pytest -q tests/test_vision_models.py tests/test_ocr.py tests/test_vision_solver.py
pytest -q tests/test_provider_manifest.py tests/test_automation_kit_integration.py
```

Required result:

```text
All tests pass
```

### automation-kit

```bash
pytest -q
```

Required result:

```text
Full suite passes without installing slidex
```

### automation-app-damai

```bash
pytest -q
```

Required result:

```text
Suite passes with slidex installed and vision capability enabled
```

### automation-app-dianping

```bash
pytest -q
```

Required result:

```text
Suite passes without slidex integration enabled
```

---

## Quality Gates Per Phase

Each phase must end with:

1. `pytest -q`
2. `python3 /Users/mango/.agents/skills/production-code-quality-review/scripts/collect-review-context.py --repo /Users/mango/project/codex/slidex`
3. Production code quality review summary containing:
   - severe issues
   - improvement suggestions
   - score
   - pass status
4. Atomic commit using one of:
   - `feat(阶段0.4): ...`
   - `feat(阶段0.5): ...`
   - `feat(阶段0.6): ...`
   - `docs(阶段0.5): ...`

---

## Spec Coverage Checklist

- P0 core boundary: covered by Cross-Repository Migration Design and automation-kit boundary tests.
- P0 unified visual challenge API: covered by Tasks 1 and 3.
- P0 OCR migration: covered by Task 2.
- P0 session reuse: covered by Task 3 and `solve_on_page`.
- P1 provider manifest: covered by Task 4.
- P1 artifact and observability: covered by Task 6 and current telemetry work.
- P1 manual fallback platformization: covered by Task 7.
- P1 automation-kit optional integration: covered by Task 5.
- P2 provider ecosystem split: documented in Version Roadmap and deferred until after protocol freeze.
- Migration requirements: covered by Task 8.

---

## Execution Record

### 2026-06-18: Phase 0.4/0.5 Checkpoint

**Completed:**

- Added `slidex.ocr.OcrTextExtractor`, `OcrResult`, `OcrBox`, `OcrInput`, and `FakeOcrExtractor`.
- Added `slidex.vision` models for `ChallengeType`, `VisionContext`, `VisionArtifact`, `ProviderManifest`, `ProviderDecision`, `VisualChallengeRequest`, and `VisualChallengeResult`.
- Added `VisualChallengeSolver` routing for `ocr_text`, `image_text`, and `slider_captcha`.
- Added Playwright `Page` reuse entry point through `SliderSolver.solve_on_page(...)`.
- Added provider manifests for built-in Aliyun NoCaptcha and GeeTest providers.
- Added optional `slidex.integrations.automation_kit` adapters for action result, artifact, and event shapes.
- Added native adapter verification that passes when `automation-kit` is available and skips when it is not installed.

**Decision Record:**

- Problem: `automation-app-damai` latest upstream changed the capability flag from OCR-specific naming to `visual_challenges`.
  Choice: keep the upstream `visual_challenges` capability name and use `slidex.ocr.FakeOcrExtractor` as the injected fake visual solver in tests.
  Reason: this preserves the platform boundary: apps depend on a generic visual capability, while OCR is one provider capability inside `slidex`.
  Risk: downstream callers using the old `ocr` capability key must migrate to `visual_challenges`.
- Problem: external repositories changed while this phase was in progress.
  Choice: stash local migration edits, pull latest upstream with `--ff-only`, then replay only the still-needed patches.
  Reason: avoids overwriting upstream work and keeps the local integration aligned with current repository state.
  Risk: external patches are currently in `/tmp/slidex-integration/*` and need to be committed/pushed from those repositories if they are part of the release train.
- Problem: native automation-kit adapter tests would make `slidex` core depend on `automation-kit`.
  Choice: use conditional `pytest.importorskip(...)` for native adapter success tests and keep dict-shaped adapter tests always-on.
  Reason: proves optional integration without violating the no-hard-dependency requirement.
  Risk: CI should include one job with `automation-kit` installed or on `PYTHONPATH` to exercise the native path.

**Verification:**

- `pytest -q`: `231 passed, 1 skipped`.
- `PYTHONPATH=/tmp/slidex-integration/automation-kit pytest -q tests/test_automation_kit_integration.py`: `5 passed`.
- `/tmp/slidex-integration/automation-kit`: `302 passed` with `pytest -q -o addopts=''`.
- `/tmp/slidex-integration/automation-app-damai`: `9 passed` with local `slidex` on `PYTHONPATH`.
- `/tmp/slidex-integration/automation-app-dianping`: `5 passed`.
- `/tmp/slidex-integration/automation-plugin-ocr`: `2 passed`.

**Production Code Quality Review:**

- Mode: `checkpoint`.
- Severe issues: none found.
- Improvement suggestions: add a CI matrix job that installs/loads `automation-kit` to run native adapter tests instead of relying only on local `PYTHONPATH` verification.
- Quality score: 88/100.
- Pass status: passed.

**Todo Status:**

- Phase 0.4 OCR API: done.
- Phase 0.4 unified visual API: done for OCR and slider routing.
- Phase 0.4 session reuse: done for Playwright page and existing CDP path.
- Phase 0.5 optional automation-kit adapter: done for action result, artifact, and event conversion.
- Phase 0.5 artifact standardization: done for path sanitization and metadata redaction.
- Phase 0.6 manual fallback platformization: done for session audit, challenge type, and OCR correction completion.
- Phase 0.6 CLI/API unified output: done for `VisualChallengeResult` fields plus backward-compatible solver output.
- Remaining: final hardening review and any future provider split work.

### 2026-06-18: Phase 0.5/0.6 Checkpoint

**Completed:**

- Added `slidex.vision.artifacts.build_artifact_path(...)` and `safe_artifact_metadata(...)`.
- Added `slidex.vision.manual.ManualFallbackSession` for OCR correction and generic manual completion metadata.
- Extended remote session state with `challenge_type` and `audit` records.
- Exposed `challenge_type` and `audit` via session info APIs.
- Unified CLI/CDP JSON output around `VisualChallengeResult` fields while preserving backward-compatible `cookies`, `elapsed_ms`, `error`, and `telemetry`.

**Decision Record:**

- Problem: CLI consumers already depend on solved cookies and legacy top-level fields.
  Choice: output the unified visual result contract and keep `cookies`, `elapsed_ms`, `error`, and `telemetry` at top level as compatibility aliases.
  Reason: satisfies the new SDK/CLI contract requirement without breaking existing CDP integrations.
  Risk: callers should eventually migrate to `duration_ms` and `error_code` to avoid dual-field ambiguity.
- Problem: remote manual fallback must become platform-aware, but the current UI/controller is still slider-specific.
  Choice: platformize the session/audit model first and leave the UI surface unchanged in this phase.
  Reason: preserves existing remote control behavior while establishing the stable session contract needed by future OCR/manual flows.
  Risk: the current browser UI still reflects slider semantics until a later UX pass.

**Verification:**

- `pytest -q tests/test_vision_artifacts.py tests/test_manual_fallback.py tests/test_cli.py tests/test_api_security.py`: `25 passed`.
- `pytest -q`: `238 passed, 1 skipped`.
- `PYTHONPATH=/tmp/slidex-integration/automation-kit pytest -q tests/test_automation_kit_integration.py`: `5 passed`.

**Production Code Quality Review:**

- Mode: `checkpoint`.
- Severe issues: none found.
- Improvement suggestions: keep a dedicated CI job for native automation-kit adapter coverage and add a future browser/UI test once manual fallback becomes truly challenge-generic at the front-end layer.
- Quality score: 91/100.
- Pass status: passed.

**Todo Status:**

- Phase 0.5 artifact standardization: done.
- Phase 0.6 manual fallback platformization: done for model/session/audit contract.
- Phase 0.6 CLI/API unified output: done.
- Remaining: final hardening review and delivery audit.

### 2026-06-18: Final Delivery Audit

**Cross-Repository Traceability:**

- `automation-kit`: `c6619d8 docs: 完善 slidex 视觉平台兼容矩阵`
- `automation-app-damai`: `2826ce7 feat: 使用 slidex 视觉能力测试入口`
- `automation-app-dianping`: `0d646ca test: 保持 dianping 视觉能力可选`
- `automation-plugin-ocr`: `0b4f9c1 docs: 明确 OCR 插件归档迁移策略`

**Requirement Evidence:**

- `automation-plugin-ocr` 不再被应用仓引用: verified by `rg -n "automation-plugin-ocr|automation_plugin_ocr" .` returning no matches in damai and dianping.
- `automation-app-damai` 使用 `slidex.ocr.FakeOcrExtractor`: covered by `/tmp/slidex-integration/automation-app-damai/tests/test_workflow.py`.
- `automation-kit` 文档推荐 `slidex` 作为视觉能力平台: covered by `/tmp/slidex-integration/automation-kit/docs/ecosystem.md` and `docs/compatibility.md`.
- `slidex` 同时支持 `slider_captcha` and `ocr_text`: covered by `tests/test_vision_solver.py`.
- `VisualChallengeResult` 可映射为 automation-kit action result / artifact / event: covered by `tests/test_automation_kit_integration.py` and native-path verification with `PYTHONPATH=/tmp/slidex-integration/automation-kit`.
- artifact and metadata redaction helpers: covered by `tests/test_vision_artifacts.py`.
- manual fallback unified result model and session audit fields: covered by `tests/test_manual_fallback.py` and `tests/test_api_security.py`.
- CLI/CDP unified visual output while preserving compatibility fields: covered by `tests/test_cli.py`.

**Final Verification Commands:**

- `pytest -q`
- `PYTHONPATH=/tmp/slidex-integration/automation-kit pytest -q tests/test_automation_kit_integration.py`
- `/tmp/slidex-integration/automation-kit`: `PYTHONPATH=/tmp/slidex-integration/automation-kit pytest -q -o addopts=''`
- `/tmp/slidex-integration/automation-app-damai`: `PYTHONPATH=/Users/mango/project/codex/slidex:/tmp/slidex-integration/automation-kit:/tmp/slidex-integration/automation-app-damai pytest -q -o addopts=''`
- `/tmp/slidex-integration/automation-app-dianping`: `PYTHONPATH=/tmp/slidex-integration/automation-kit:/tmp/slidex-integration/automation-app-dianping pytest -q -o addopts=''`
- `/tmp/slidex-integration/automation-plugin-ocr`: `PYTHONPATH=/tmp/slidex-integration/automation-plugin-ocr pytest -q -o addopts=''`

**Final Review Status:**

- Mode: `final`.
- Severe issues: one documentation/implementation mismatch found and fixed: README used `--provider auto` before the CDP CLI accepted `--provider`.
- Fix: added `--provider` support to `slide_solve_cdp.py` and test coverage in `tests/test_cli.py`.
- Quality score: 93/100.
- Pass status: passed.

**Remaining Risks:**

- Real target-site E2E validation still depends on a live user-side browser/session and target CAPTCHA availability.
- `automation-plugin-ocr` can be archived on GitHub after the external commits are pushed.
