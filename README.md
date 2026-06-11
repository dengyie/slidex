# Slidex

<p align="center">
  <strong>Generic Slider CAPTCHA Solver</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Alpha">
</p>

---

## Features

| Feature | Description |
|---------|-------------|
| **Dual solver engines** | `SliderSolver` (async CDP) + `XianyuSliderStealth` (sync, full-featured) |
| **CDP mode** | Connect to an existing browser instance — no new process, ideal for TypeScript/Node integration |
| **Configurable selectors** | Support Aliyun NoCaptcha, GeeTest, Shumei, and other CAPTCHA providers |
| **Multi-source distance detection** | OpenCV image matching → JS DOM calculation → CSS width estimation, chain fallback |
| **Human-like trajectory** | 4-phase physics model (slow start → accelerate → medium → fine-tune) + recorded trajectory replay pool |
| **Anti-detection** | Chromium launch args + JS injection (hide webdriver/Canvas/WebGL fingerprints) |
| **Remote human fallback** | WebSocket real-time screenshot + manual control, auto-triggered on failure with trajectory recording |
| **Concurrency management** | Built-in `SliderConcurrencyManager` for max concurrent count and queue timeout |

## Installation

```bash
# Basic
pip install -e .

# With remote control API
pip install -e ".[remote]"

# Dev dependencies
pip install -e ".[dev]"

# Install Playwright browsers
playwright install chromium
```

## Quick Start

```python
from slidex import SlidexConfig, SliderSolver

config = SlidexConfig()
solver = SliderSolver(
    cookie_id="my_user",
    cookies_str="your_cookie_string",
    headless=True,
    config=config,
)

success, cookies = await solver.solve("https://...verification_url...")
```

## CDP Mode (Connect to Existing Browser)

When integrating with an existing Playwright/browser session (e.g. from a TypeScript project), use CDP mode instead of launching a new browser:

```python
from slidex import SliderSolver

solver = SliderSolver(
    cookie_id="user_123",
    trajectory_mode="auto",
)

# Connect to an already-running browser via CDP
success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",  # optional: navigate to this URL first
)
```

Or via CLI:

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --selectors '{"slider_btn": ".geetest_slider_button"}'
```

## Configurable Selectors

Override the default Aliyun NoCaptcha selectors for other CAPTCHA providers:

```python
from slidex import SliderSolver

# GeeTest example
solver = SliderSolver(
    selectors={
        "slider_btn": ".geetest_slider_button",
        "slider_track": ".geetest_slider_track",
        "bg_img": ".geetest_canvas_bg canvas",
        "piece_img": ".geetest_canvas_slice canvas",
        "result_url_pattern": ["/api/v4/slider"],
        "success_code": 0,
    }
)
```

### Selector Keys

| Key | Default | Description |
|-----|---------|-------------|
| `slider_btn` | `#nc_1_n1z` | Slider button selector |
| `slider_track` | `#nc_1_n1t` | Slider track selector |
| `bg_img` | `#nc_1_n1t img, ...` | Background image selector |
| `piece_img` | `.nc_iconfont, ...` | Puzzle piece image selector |
| `track_width` | `.nc_scale, [class*=track]` | Track width selector |
| `slider_alt` | `(".nc_iconfont", ...)` | Fallback slider selectors (tuple) |
| `result_url_pattern` | `("/slide?", ...)` | Result URL match patterns (tuple) |
| `success_code` | `0` | Success response code |

## Configuration

```python
from slidex import SlidexConfig

config = SlidexConfig(
    max_concurrent=3,           # max concurrent sliders
    wait_timeout=60,            # queue wait timeout (seconds)
    trajectory_pool_enabled=True,
    remote_captcha_enabled=True,
    remote_captcha_timeout=180,
    browser_data_dir="/path/to/browser_profiles",
)

# Or from environment variables
config = SlidexConfig.from_env()
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SLIDEX_MAX_CONCURRENT` | `3` | Max concurrent slider verifications |
| `SLIDEX_WAIT_TIMEOUT` | `60` | Queue wait timeout (seconds) |
| `SLIDEX_TRAJ_POOL_DIR` | `~/.slidex/trajectories` | Trajectory storage path |
| `SLIDEX_BROWSER_DATA_DIR` | `~/.slidex/browser_data` | Browser profile path |
| `SLIDEX_REMOTE_ENABLED` | `1` | Enable remote human fallback |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | Remote fallback timeout (seconds) |

## Callbacks

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_risk_log_update=lambda **kwargs: db.update_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

## Remote Control API

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# Open http://localhost:8000/api/captcha/control
```

## Project Structure

```
slidex/
├── solver.py            # Core solver (CDP mode + selector config)
├── config.py            # SlidexConfig management
├── _image_match.py      # OpenCV image matching (Canny + template matching)
├── _trajectory.py       # Trajectory generation (4-phase physics model)
├── _trajectory_pool.py  # Trajectory pool (record/replay/LRU rotation)
├── _stealth_patch.py    # Anti-detection args and JS injection scripts
├── _concurrency.py      # Concurrency manager
├── remote.py            # Remote human fallback controller
├── api.py               # FastAPI routes
├── stealth.py           # XianyuSliderStealth enhanced solver
└── scripts/
    └── slide_solve_cdp.py  # CDP mode CLI entry point
```

## TypeScript Integration

```typescript
import { execSync } from 'child_process';

// When encountering a CAPTCHA in a Playwright session
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

## License

MIT
