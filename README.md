# Slidex

Standalone slider captcha solver extracted from xianyubot.

## Features

- **Two solver engines**: `SliderSolver` (async, lightweight) and `XianyuSliderStealth` (sync, full-featured)
- **Multi-source distance detection**: OpenCV image matching → JS DOM calculation → CSS fallback
- **Human-like trajectories**: physics-based 4-phase trajectory generation + recorded trajectory replay pool
- **Anti-detection**: Chromium stealth args + JS injection to hide automation fingerprints
- **Remote human fallback**: WebSocket-based remote control with a web frontend

## Installation

```bash
pip install -e .

# With remote control API support:
pip install -e ".[remote]"

# With dev dependencies:
pip install -e ".[dev]"
```

Install Playwright browsers:

```bash
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

## Configuration

```python
from slidex import SlidexConfig

config = SlidexConfig(
    max_concurrent=3,           # max concurrent sliders
    wait_timeout=60,            # queue wait timeout
    trajectory_pool_enabled=True,
    remote_captcha_enabled=True,
    remote_captcha_timeout=180,
    browser_data_dir="/path/to/browser_profiles",
)

# Or from environment variables:
config = SlidexConfig.from_env()
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SLIDEX_MAX_CONCURRENT` | 3 | Max concurrent slider verifications |
| `SLIDEX_WAIT_TIMEOUT` | 60 | Queue wait timeout (seconds) |
| `SLIDEX_TRAJ_POOL_DIR` | ~/.slidex/trajectories | Trajectory storage path |
| `SLIDEX_BROWSER_DATA_DIR` | ~/.slidex/browser_data | Browser profile path |
| `SLIDEX_REMOTE_ENABLED` | 1 | Enable remote human fallback |
| `SLIDEX_REMOTE_TIMEOUT` | 180 | Remote fallback timeout (seconds) |

## Callbacks

For integration with external systems:

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_risk_log_update=lambda **kwargs: db.update_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

## CLI Demo

```bash
python -m demo.verify --cookie-str "your_cookie" --rounds 5 --headless
```

## Remote Control API

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# Open http://localhost:8000/api/captcha/control
```

## License

MIT
