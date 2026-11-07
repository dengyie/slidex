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

> Ecosystem development baseline: see [`automation-kit/docs/development.md`](https://github.com/dengyie/automation-kit/blob/main/docs/development.md).

**Features**:
- 🎯 **Multi-provider support** — Built-in Aliyun NoCaptcha, AliyunCaptcha.js 2.0, GeeTest adapters with auto-detection
- 🔎 **Unified vision API** — `slidex.vision` models slider, OCR, and manual fallback challenges
- 🧩 **hCaptcha support** — 9-grid binary select, area select, and drag-drop, on the same multimodal channel
- 🔑 **PX press-token** — PerimeterX press/silent quadruple (`px3/pxde/pxvid/pxcts`, `:1000:` gate) via Chromium + a self-hosted solving station (`/v1/solve`, captcha.run-compatible `/v2/tasks`, `/health`)
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
pip install -e ".[vision]"   # optional: YOLO backend for image-only gap detection
pip install -e ".[server]"   # optional: self-hosted solving station (FastAPI/uvicorn)
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

### hCaptcha

hCaptcha is a different protocol from the slider family: challenges live inside
`iframe[src^="https://newassets.hcaptcha.com/captcha/v1/"]`, the verdict comes
from `/checkcaptcha/` as `{"pass": bool}`, and the success artifact is an
`h-captcha-response` token (not a cookie). It therefore does not go through the
`CaptchaProvider` pipeline — it is dispatched as `ChallengeType.HCAPTCHA` by
`VisualChallengeSolver`, reusing the same `SLIDEX_VISION_*` multimodal channel.

```python
from slidex.vision import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision import VisualChallengeSolver

solver = VisualChallengeSolver()
result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.HCAPTCHA,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=page,                       # required: hCaptcha is live-page only
        question_language="auto",        # auto → zh first, then en
        hcaptcha={"max_rounds": 3},      # optional: round cap
    )
)

if result.success:
    token = await page.input_value("textarea[name='h-captcha-response']")
```

Supported challenge types:

| Type | Detected by | Page action |
|------|-------------|-------------|
| `image_label_binary` | `.task` count == 9 | click the center of selected tiles |
| `image_label_area_select` | `.task` count == 0 and not a drag | click normalized coordinates |
| `image_drag_drop` | engine reports a drag | bezier drag (eased ends + micro jitter) |

Verdict and failure handling:

- `success=True` means `/checkcaptcha/` returned `pass` (or an
  `h-captcha-response` token is present).
- **No retry after submit**: `hcaptcha_failed` / `hcaptcha_verdict_pending` are
  `retryable=False`. Re-submitting the same answer only adds a penalized sample.
  Only `hcaptcha_challenge_timeout` / `screenshot_failed` (page not yet touched)
  are marked retryable.
- If the challenge image does not change after submit, the executor stops; it
  only continues to another page (crumb) when the image actually refreshed.

Scope limits (no over-promising):

- Covers the **visual/answering layer** only. Pass rate also depends on browser
  fingerprint and IP reputation, which belongs to the stealth layer.
- **No** `hsw.js` reversing and **no** msgpack payload decryption: it prefers
  the plain JSON from `/getcaptcha/` and falls back to screenshotting the
  challenge and reading the prompt from DOM — both rely only on public page
  structure.

### PX press-token & self-hosted solving station

For PerimeterX (PX) press/silent challenges, slidex ships a self-hostable
solving station: a headless Chromium opens the hsprotect iframe (or the site
page), waits for PX JS to write `_px3`, then harvests the quadruple
`{"px3","pxde","pxvid","pxcts"}` from the cookie jar. The only success
criterion: **`px3` must contain `:1000:`** (rejected tokens usually carry
`:200:`).

```python
from slidex.px import PxPressRequest, PxPressSolver

solver = PxPressSolver(headless=True, concurrency=5)
result = await solver.solve(
    PxPressRequest.from_payload(
        {
            "appId": "PXzC5j78di",
            "challengeUrl": "https://iframe.hsprotect.net/...",
            "websiteURL": "https://signup.live.com/",
            "country": "US",
            "proxy": {"host": "1.2.3.4", "port": 8000,
                      "login": "u", "password": "p"},
        }
    )
)
if result.success:
    px3, pxde, pxvid, pxcts = (result.tokens[k] for k in ("px3", "pxde", "pxvid", "pxcts"))
```

Start the station (`slidex[server]`):

```bash
SLIDEX_CAPTCHA_KEYS=your-key uvicorn slidex.service.app:app --host 0.0.0.0 --port 8000
```

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/solve` | Synchronous solve: `{"type":"px_press", ...}` → `{"code":0,"data":{quadruple}}` |
| `POST /v2/tasks/` + `GET /v2/tasks/{id}` | captcha.run-compatible async task protocol |
| `GET /health` | `{ok, active_tasks, done_24h, fail_24h, types}` |

```bash
curl -s http://127.0.0.1:8000/v1/solve \
  -H 'Authorization: Bearer your-key' -H 'Content-Type: application/json' \
  -d '{"type":"px_press","appId":"PXzC5j78di",
       "challengeUrl":"https://iframe.hsprotect.net/...",
       "websiteURL":"https://signup.live.com/","country":"US"}'
```

Two error layers: HTTP status covers transport/auth/params (400/401/422/429/500),
while the body `code` describes the solve result (0 ok; 1001 generic failure;
1002 timeout; 1003 IP/appId blocked; 1004 concurrency exhausted). A failed solve
is usually HTTP 200 with a non-zero `code` plus `data.kind` and
`data.retryable`, so callers can decide whether to retry.

Key rules:

- **Same-IP rule**: whenever `proxy` is present, the browser context and every
  outbound request use it; a mismatched egress voids the tokens. An empty proxy
  means direct egress — local testing only.
- **No persistence**: tasks live in memory only (30-min TTL) and are dropped on
  completion; logs print token prefixes and lengths, never full cookie/token values.
- **Concurrency**: `SLIDEX_PX_CONCURRENCY` (default 5); queue overflow returns
  `solver_busy`.
- **Compatibility**: `type` accepts aliases such as `px_press` / `press` /
  `pxcaptcha2` / `silent` (case- and hyphen-insensitive); `geetest_v3` routes to
  `SliderSolver` for existing callers.

### OCR API

```python
from slidex.ocr import FakeOcrExtractor

extractor = FakeOcrExtractor(text="seat-a12", confidence=0.95, language="en")
result = extractor.extract(
    image_path="captcha.png",
    roi={"x": 10, "y": 20, "width": 100, "height": 32},
)
```

### automation-kit Capability Adapter

```python
from automation_core.capabilities import (
    CapabilityExecutor,
    CapabilityRegistry,
    CapabilityRequest,
)
from slidex.integrations.automation_kit import SlidexVisualCapability

registry = CapabilityRegistry()
registry.register(SlidexVisualCapability())
executor = CapabilityExecutor(registry)

result = await executor.aexecute(
    CapabilityRequest(
        capability="visual.challenge",
        operation="solve",
        parameters={
            "challenge_type": "ocr_text",
            "context": "image_bytes",
            "image_bytes": b"...png bytes...",
        },
        metadata={"run_id": "run-1", "task_id": "visual-1"},
    )
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
| Aliyun Captcha.js 2.0 jigsaw (z.ai logins) | `aliyun-captcha-js` | ✅ Built-in |
| GeeTest v3/v4 | `geetest` | ✅ Built-in |
| Shumei | `shumei` | 📝 TODO |
| Dingxiang | `dingxiang` | 📝 TODO |
| Custom | Your provider | 🔌 [Use the ecosystem baseline](https://github.com/dengyie/automation-kit/blob/main/docs/development.md) |

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

#### z.ai-style logins (AliyunCaptcha.js 2.0): one-shot verify-param

```python
from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

provider = AliyunCaptchaJsProvider()
# arm a fresh widget -> gap detection -> quadratic inverse -> drag -> capture captchaVerifyParam
result = await provider.acquire_verify_param(page, "1nl1cfkp")   # z.ai login scene
if result["param"]:
    # POST the param to your business endpoint (e.g. z.ai /auths/signin)
    ...
```

Runtime rules are internalized (the widget session expires in minutes — drag
promptly; failures rotate the cert and reset the popup, so each retry re-arms
and re-detects).

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

The ecosystem baseline documents the public provider boundary, cancellation,
resource ownership, and automation-kit integration contract:
[development guide](https://github.com/dengyie/automation-kit/blob/main/docs/development.md).

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
| `SLIDEX_HCAPTCHA_MAX_ROUNDS` | `3` | Max rounds per hCaptcha challenge |
| `SLIDEX_HCAPTCHA_TILE_PADDING` | `3.0` | Tile click jitter cap (px) |
| `SLIDEX_HCAPTCHA_PROMPT` | empty (built-in prompt) | Override the hCaptcha solving prompt |
| `SLIDEX_CAPTCHA_KEYS` | empty (open mode) | Station API keys, comma-separated |
| `CAPTCHA_RUN_KEY` | empty | Single-key alias for captcha.run compatibility (`SLIDEX_CAPTCHA_KEYS` wins) |
| `SLIDEX_PX_CONCURRENCY` | `5` | Max concurrent PX solves |
| `SLIDEX_PX_HEADLESS` | `1` | Run PX solving headless (set 0 for debugging) |

### 11. Artifacts and automation-kit adapters

```python
from slidex.integrations.automation_kit import SlidexVisualCapability
from slidex.vision import build_artifact_path, safe_artifact_metadata
```

---

## License

MIT
