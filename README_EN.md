# Slidex

<p align="center">
  <strong>Generic Slider CAPTCHA Solver</strong><br>
  <em>通用滑块验证码求解库</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
</p>

<p align="center">
  <a href="README.md">中文</a> | <strong>English</strong>
</p>

Slidex is a generic slider CAPTCHA solver library. It supports connecting to an existing browser (CDP mode) or launching its own, with built-in image recognition, trajectory simulation, anti-detection, and remote human fallback. Suitable for batch account verification and automation flows that encounter slider CAPTCHAs.

## Installation

```bash
pip install -e .
playwright install chromium
pip install -e ".[remote]"   # optional: remote control API
```

## Quick Start

```python
from slidex import SlidexConfig, SliderSolver

solver = SliderSolver(
    cookie_id="my_user",
    cookies_str="your_cookie_string",
    headless=True,
    config=SlidexConfig(),
)

success, cookies = await solver.solve("https://...verification_url...")
```

## Technical Overview

- **Image recognition**: OpenCV Canny edge detection + template matching, cross-validated with JS calculation
- **Trajectory simulation**: 4-phase physics model (slow start → accelerate → medium → fine-tune), with recorded trajectory replay
- **Anti-detection**: Chromium launch args + JS injection to hide automation fingerprints
- **Configurable selectors**: Adapt to different CAPTCHA providers (Aliyun, GeeTest, Shumei, etc.) via `selectors={}`

---

## Integration Guide

### 1. CDP Mode (Recommended — connect to existing browser)

For scenarios with an existing Playwright/browser session (e.g. TypeScript projects). No new browser is launched:

```python
from slidex import SliderSolver

solver = SliderSolver(
    cookie_id="user_123",
    trajectory_mode="auto",
    selectors={  # optional: override default selectors
        "slider_btn": ".geetest_slider_button",
        "slider_track": ".geetest_slider_track",
    },
)

success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",  # optional: navigate to this URL first
)

await solver.close()
```

### 2. CLI (for TypeScript/Node subprocess calls)

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --selectors '{"slider_btn": ".geetest_slider_button"}'
```

Output JSON:

```json
{"success": true, "cookies": {...}, "elapsed_ms": 3200.5, "error": null}
```

### 3. TypeScript Integration

```typescript
import { execSync } from 'child_process';

const cdpEndpoint = browser.wsEndpoint();
const result = JSON.parse(
  execSync(`python -m slidex.scripts.slide_solve_cdp \
    --cdp-endpoint ${cdpEndpoint} \
    --page-url "${pageUrl}" \
    --selectors '${JSON.stringify({
      slider_btn: ".geetest_slider_button",
      slider_track: ".geetest_slider_track",
    })}'`).toString()
);

if (result.success) {
  console.log(`Solved in ${result.elapsed_ms}ms`);
}
```

### 4. Custom Selectors

Default selectors are for Aliyun NoCaptcha. For other providers:

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

See `DEFAULT_SELECTORS` in `slidex/solver.py` for all available keys.

### 5. Remote Human Fallback

When auto-solve fails, push the CAPTCHA to a human operator via WebSocket:

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# Open http://localhost:8000/api/captcha/control
```

### 6. Callbacks

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

### 7. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SLIDEX_MAX_CONCURRENT` | `3` | Max concurrent sliders |
| `SLIDEX_BROWSER_DATA_DIR` | `~/.slidex/browser_data` | Browser data path |
| `SLIDEX_TRAJ_POOL_DIR` | `~/.slidex/trajectories` | Trajectory storage path |
| `SLIDEX_REMOTE_ENABLED` | `1` | Enable remote human fallback |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | Remote fallback timeout (seconds) |

---

## License

MIT
