# Slidex

<p align="center">
  <strong>Vision Challenge Platform for automation-kit</strong><br>
  <em>automation-kit 视觉能力平台</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
</p>

<p align="center">
  <a href="README.md">中文</a> | <strong>English</strong>
</p>

Slidex has evolved from a slider CAPTCHA solver into the visual capability platform for the `automation-kit` ecosystem. The current revision unifies slider CAPTCHA solving, OCR, screenshot evidence, manual fallback sessions, and telemetry/artifact contracts, while keeping `SliderSolver` compatible for existing integrations.

> Delivery status: the current revision passes repository-level automated verification and is ready for integration handoff; perform one real browser smoke test against the target site before production rollout.

**Features**:
- 🎯 **Multi-provider support** — Built-in Aliyun NoCaptcha, GeeTest adapters with auto-detection
- 🔎 **Unified vision API** — `slidex.vision` models slider, OCR, and manual fallback challenges
- 🧾 **Built-in OCR surface** — `slidex.ocr` exposes `OcrTextExtractor`, `OcrResult`, and `FakeOcrExtractor`
- 🔌 **Plugin-based extension** — Implement custom providers in 10 minutes
- 🌐 **CDP mode** — Connect to existing browser, ideal for TypeScript/Node integration
- ♻️ **Session reuse** — Supports CDP, existing Playwright `Page`, and image bytes/path inputs
- 🧠 **Intelligent solving** — OpenCV + physics trajectory + recorded replay
- 🛡️ **Anti-detection** — Stealth args + JS injection

## Installation

```bash
pip install -e .
playwright install chromium
pip install -e ".[remote]"   # optional: remote control API
pip install -e ".[automation-kit]"  # optional: native automation-kit adapter
```

## Quick Start

### Auto-detection (Recommended)

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="my_user", provider="auto")
success, cookies = await solver.solve("https://...verification_url...")
```

### Manual Provider Selection

```python
# Aliyun NoCaptcha
solver = SliderSolver(provider="aliyun-nocaptcha")

# GeeTest
solver = SliderSolver(provider="geetest")
```

### Unified Vision API

```python
from slidex.ocr import FakeOcrExtractor
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)

solver = VisualChallengeSolver(
    ocr_extractor=FakeOcrExtractor(text="seat-a12", confidence=0.98)
)

ocr_result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.OCR_TEXT,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"fake-image",
    )
)
```

### OCR API

```python
from slidex.ocr import FakeOcrExtractor

extractor = FakeOcrExtractor(text="seat-a12", confidence=0.95, language="en")
result = extractor.extract(
    image_path="captcha.png",
    roi={"x": 10, "y": 20, "width": 100, "height": 32},
)
```

## Technical Overview

- **Provider abstraction**: Unified interface for different vendors, built-in Aliyun & GeeTest, plugin-extensible
- **Image recognition**: OpenCV Canny + template matching + JS DOM cross-validation
- **Trajectory**: 4-phase physics model + recorded human trajectory replay
- **Anti-detection**: Chromium stealth args + JS injection

---

## Supported Providers

| Provider | Name | Status |
|----------|------|--------|
| Aliyun NoCaptcha | `aliyun-nocaptcha` | ✅ Built-in |
| GeeTest v3/v4 | `geetest` | ✅ Built-in |
| Shumei | `shumei` | 📝 TODO |
| Dingxiang | `dingxiang` | 📝 TODO |
| Custom | Your provider | 🔌 [10-min guide](docs/PROVIDER_GUIDE.md) |

---

## Integration Guide

### 1. Auto-detection (Recommended)

Slidex automatically detects the CAPTCHA provider:

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="user_123", provider="auto")
success, cookies = await solver.solve("https://...")
```

### 2. Manual Provider

When you know which provider the site uses:

```python
solver = SliderSolver(provider="geetest")
solver = SliderSolver(provider="aliyun-nocaptcha")
```

### 3. CDP Mode (Connect to Existing Browser)

For existing Playwright/browser sessions (e.g. TypeScript projects):

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="user_123", provider="auto")

success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",
)

await solver.close()
```

### 4. Custom Provider

Implement a new provider adapter in 10 minutes:

```python
from slidex import CaptchaProvider, SliderSolver

class MyProvider(CaptchaProvider):
    name = "my-custom"
    
    async def detect(self, page):
        return await page.query_selector(".my-captcha") is not None
    
    async def locate_elements(self, page):
        # Locate elements...
    
    async def extract_images(self, page, elements):
        # Extract images...
    
    async def perform_slide(self, page, elements, gap_x, trajectory):
        # Perform slide...
    
    async def validate_response(self, response):
        # Validate result...

# Register and use
SliderSolver.register_provider("my-custom", MyProvider)
solver = SliderSolver(provider="my-custom")
```

See [Provider Guide](docs/PROVIDER_GUIDE.md) for details.

### 5. CLI (for TypeScript/Node subprocess)

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --provider auto
```

Output JSON (backward compatible plus unified visual result fields):

```json
{
  "success": true,
  "challenge_type": "slider_captcha",
  "provider": "geetest",
  "confidence": 0.93,
  "duration_ms": 3200.5,
  "error_code": null,
  "retryable": false,
  "cookies": {"session": "abc"},
  "artifacts": [{"artifact_type": "telemetry", "path": "telemetry/run-id.json"}],
  "metadata": {"telemetry": {"status": "success"}},
  "elapsed_ms": 3200.5,
  "error": null,
  "telemetry": {"status": "success"}
}
```

### 6. TypeScript Integration

```typescript
import { execSync } from 'child_process';

const cdpEndpoint = browser.wsEndpoint();
const result = JSON.parse(
  execSync(`python -m slidex.scripts.slide_solve_cdp \
    --cdp-endpoint ${cdpEndpoint} \
    --page-url "${pageUrl}" \
    --provider auto`).toString()
);

if (result.success) {
  console.log(`Solved in ${result.elapsed_ms}ms`);
}
```

### 7. Legacy Mode (Custom Selectors)

Backward compatible: manually configure selectors without using providers:

```python
solver = SliderSolver(
    selectors={
        "slider_btn": ".my-slider-button",
        "slider_track": ".my-slider-track",
        "bg_img": ".my-bg-image img",
        "piece_img": ".my-piece-image img",
        "result_url_pattern": ["/api/v4/slider"],
        "success_code": 0,
    }
)
```

See `DEFAULT_SELECTORS` in `slidex/solver.py` for all options.

### 8. Remote Human Fallback

When auto-solve fails, push to human operator via WebSocket:

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# Open http://localhost:8000/api/captcha/control
```

The platform session contract also exposes:

- `challenge_type` for the current visual challenge
- `audit` records for session creation, mouse events, and completion
- `ManualFallbackSession` for direct SDK-side manual results

### 9. Callbacks

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

### 10. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SLIDEX_MAX_CONCURRENT` | `3` | Max concurrent sliders |
| `SLIDEX_BROWSER_DATA_DIR` | `~/.slidex/browser_data` | Browser data path |
| `SLIDEX_TRAJ_POOL_DIR` | `~/.slidex/trajectories` | Trajectory storage path |
| `SLIDEX_REMOTE_ENABLED` | `1` | Enable remote human fallback |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | Remote fallback timeout (seconds) |
| `SLIDEX_TELEMETRY_ENABLED` | `1` | Enable structured E2E telemetry |
| `SLIDEX_TELEMETRY_DIR` | `~/.slidex/telemetry` | Telemetry JSONL output directory |

### 11. Artifacts and automation-kit adapters

```python
from slidex.integrations.automation_kit import to_action_result, to_artifacts, to_events
from slidex.vision import build_artifact_path, safe_artifact_metadata
```

---

## License

MIT
