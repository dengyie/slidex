<!-- markdownlint-disable-file MD033 MD045 -->
<div align="center">

# SlideX

**High-Performance Headless CAPTCHA Solver, Computer Vision Challenge Engine & PerimeterX Verification Gate**

[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-009688.svg?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Driver-Playwright-2EAD33.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev/)
[![OpenCV](https://img.shields.io/badge/Vision-OpenCV-5C3EE8.svg?style=flat-square&logo=opencv&logoColor=white)](https://opencv.org/)
[![Tests](https://img.shields.io/badge/tests-1013%20passed-brightgreen.svg?style=flat-square)]()

[English](./README.md) · [简体中文](./README.zh-CN.md) · [Core Features](#core-features) · [Architecture](#system-architecture) · [Quick Start](#quick-start) · [PerimeterX Station](#perimeterx-solving-station) · [API Reference](#api-specification)

<br>

<p align="center">
  <b>Eliminates fragile browser automation failures and brittle coordinate guesswork caused by anti-bot verification challenges.</b><br>
  Integrates headless Playwright worker pools, Chrome CDP remote debugging sessions, OpenCV image matching, and multimodal visual models into a unified verification gate.<br>
  Features native adapters for Aliyun NoCaptcha / Captcha.js 2.0, GeeTest v3/v4, hCaptcha multimodal challenges, and a self-hosted PerimeterX press/silent token solving station.
</p>

</div>

---

## Motivation & Architecture Comparison

Automated web pipelines and crawler systems frequently encounter multi-vendor verification challenges that degrade throughput and trigger IP blacklisting:

| Evaluation Dimension | SlideX Engine | Traditional Script / Selector Hacks | Third-Party Commercial Cloud Solvers |
| :--- | :--- | :--- | :--- |
| **Solving Architecture** | Unified multimodal vision pipeline + physics-based trajectory engine | Fixed selector clicks + hardcoded delays | Remote third-party HTTP round-trip API |
| **PerimeterX (PX) Support** | Native `px3/pxde/pxvid/pxcts` extraction with strict `:1000:` verification gate | Unsupported or prone to instant detection | High per-call tariff ($2-$5/k), external latency |
| **Verification Evidence** | Cryptographic token confirmation (`px3`, `h-captcha-response`, cookies) | Blind execution heuristic (zero exceptions = success) | Opaque external verdict without audit trails |
| **Driver Flexibility** | Headless Playwright, external Chrome CDP, and pure image buffer inputs | Tied strictly to a single browser driver | No local browser integration |
| **Anti-Detection Posture** | Real physics humanization, Bezier easing, and stealth JS injection | High-velocity linear movements trigger bot scores | Dependent on external proxy fleet and fingerprinters |
| **Self-Hosting & Privacy** | 100% self-hosted microservice, local memory-only token storage | Ad-hoc local code | External data leakage of session cookies and tokens |

---

## Core Features

| Module | Feature Highlight | Technical Implementation & Advantage |
| :--- | :--- | :--- |
| **Universal Slider Engine** | Multi-vendor automatic detection | Built-in adapters for Aliyun NoCaptcha, Aliyun Captcha.js 2.0, and GeeTest v3/v4 with automated DOM signature sniffing. |
| **Multimodal Vision Pipeline** | Unified challenge abstraction | `slidex.vision` standardizes sliders, OCR text extraction, and manual fallback sessions under a single request model. |
| **hCaptcha Integration** | Full challenge family coverage | Resolves 9-grid binary selection, normalized area selection, and drag-and-drop challenges via multimodal vision channels. |
| **PerimeterX Solving Station** | Native press and silent tokens | Self-hosted FastAPI service harvesting `px3/pxde/pxvid/pxcts` with strict `:1000:` pass criterion. |
| **Pure Image Gap Detector** | Browserless zero-overhead solving | Multi-tier pipeline combining OpenCV Canny template matching, YOLO neural detection, contour geometry, and column profiles. |
| **Human Physics Simulation** | Eased trajectory replay | Four-phase physical velocity model (slow start, acceleration, mid-glide, micro-adjustments) with recorded human replays. |
| **Chrome CDP Integration** | Non-intrusive session reuse | Connects directly to running browser sessions via CDP (`--remote-debugging-port`), avoiding session invalidation. |
| **Enterprise Microservice** | Synchronous and asynchronous APIs | Exposes high-throughput `/v1/solve` and captcha.run-compatible `/v2/tasks` with bounded memory retention (30-min TTL). |

---

## System Architecture

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   Upstream Applications / API Callers                   │
│         (automation-kit / Crawler Pipelines / HTTP Microservices)       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      SlideX Visual Challenge Hub                        │
│  ├─ VisualChallengeSolver         ├─ Provider Registry (Sniffer)       │
│  ├─ Physics Trajectory Engine      ├─ Anti-Detection Stealth Injector   │
│  └─ Diagnostic Tracing Filter     └─ Bounded In-Memory Semaphore Gate  │
└───────┬───────────────────────────┬───────────────────────────┬────────┘
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│ Slider Engine │           │ Vision & OCR  │           │  PX Station   │
│ Aliyun / Gee  │           │ hCaptcha /    │           │ Press / Silent│
│ Canny / YOLO  │           │ Multimodal    │           │ :1000: Gate   │
└───────┬───────┘           └───────┬───────┘           └───────┬───────┘
        │                           │                           │
        └───────────────────────────┼───────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      Execution & Runtime Backends                      │
│  ├─ Headless Playwright Fleet      ├─ Chrome Remote Debugging (CDP)    │
│  ├─ OpenCV Matrix Processing       └─ Dedicated Outbound Proxy Egress  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Supported CAPTCHA Ecosystem

| Vendor / Challenge | Identifier | Mode | Status |
| :--- | :--- | :--- | :--- |
| **Aliyun NoCaptcha** | `aliyun-nocaptcha` | In-page DOM / Slider | Built-in |
| **Aliyun Captcha.js 2.0** | `aliyun-captcha-js` | Slider / Verify-Param | Built-in |
| **GeeTest v3 / v4** | `geetest` | Slider / Fullpage | Built-in |
| **hCaptcha (9-Grid Binary)** | `image_label_binary` | Multimodal Vision | Built-in |
| **hCaptcha (Area Select)** | `image_label_area_select` | Normalized Coordinates | Built-in |
| **hCaptcha (Drag & Drop)** | `image_drag_drop` | Bezier Trajectory Drag | Built-in |
| **PerimeterX (PX Press)** | `px_press` | Chromium Headless Frame | Built-in |
| **PerimeterX (PX Silent)** | `silent` | Seed Harvest / Background | Built-in |
| **Pure Image Gap Detection** | `SliderImageSolver` | Canny / YOLO / Contour | Built-in |
| **Optical Character Reader** | `slidex.ocr` | Bounding Box OCR | Built-in |

---

## Quick Start

### 1. Prerequisites

- Python 3.10+ (Python 3.11 recommended)
- Chromium / Google Chrome

```bash
# Clone repository
git clone https://github.com/dengyie/slidex.git
cd slidex

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install core package and Playwright browser binaries
pip install -e .
playwright install chromium

# Install optional capability groups
pip install -e ".[server]"          # Self-hosted FastAPI microservice
pip install -e ".[vision]"          # YOLO neural gap detection model
pip install -e ".[automation-kit]"  # Native automation-kit integration
```

### 2. Basic Python Usage

#### Auto-Detection Mode

```python
from slidex import SliderSolver

# Sniffs captcha provider dynamically from target page
solver = SliderSolver(cookie_id="session_01", provider="auto")
success, cookies = await solver.solve("https://example.com/verification")
```

#### Pure Image Gap Detection (No Browser Required)

```python
from slidex.vision import SliderImageSolver

solver = SliderImageSolver()

# Template matching with background and puzzle piece
result = solver.solve("background.png", "piece.png")

# Autonomous gap detection without piece image
result = solver.solve(b"...raw image bytes...")

print(result.to_dict())
# {"success": True, "gap_x": 182, "confidence": 0.87, "method": "template_edge", ...}
```

#### Multimodal hCaptcha Solving

```python
from slidex.vision import ChallengeType, VisionContext, VisualChallengeRequest, VisualChallengeSolver

solver = VisualChallengeSolver()
result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.HCAPTCHA,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=page,
        question_language="auto",
        hcaptcha={"max_rounds": 3},
    )
)

if result.success:
    token = await page.input_value("textarea[name='h-captcha-response']")
```

---

## PerimeterX Solving Station

SlideX includes a production-grade self-hosted solving station that extracts PerimeterX press/silent token quadruples (`px3`, `pxde`, `pxvid`, `pxcts`) under an isolated headless Chromium environment.

### 1. Launching the Station

```bash
SLIDEX_CAPTCHA_KEYS="prod-secret-token" \
SLIDEX_PX_CONCURRENCY=5 \
uvicorn slidex.service.app:app --host 0.0.0.0 --port 8077
```

### 2. Synchronous Solving API (`POST /v1/solve`)

```bash
curl -s http://127.0.0.1:8077/v1/solve \
  -H "Authorization: Bearer prod-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "px_press",
    "appId": "PXzC5j78di",
    "challengeUrl": "https://iframe.hsprotect.net/...",
    "websiteURL": "https://signup.live.com/",
    "country": "US",
    "proxy": {
      "host": "proxy.example.com",
      "port": 8080,
      "login": "username",
      "password": "password"
    }
  }'
```

#### Success Response
```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "px3": "e0b8...:1000:...",
    "pxde": "5a2f...",
    "pxvid": "c138-...",
    "pxcts": "1731112345",
    "diagnostics": {
      "iframe_loaded": true,
      "collector_reported": true
    }
  }
}
```

### 3. Asynchronous API (`POST /v2/tasks` & `GET /v2/tasks/{taskId}`)

Provides full captcha.run protocol compatibility for long-budget tasks and idempotent dispatching.

---

## API Specification

### Error Handling & Retries

HTTP status codes represent transport, authentication, or parsing errors (400, 401, 422, 429, 500). Task execution verdicts are communicated via the payload `code` and structured error classifiers:

| Failure Kind | Retryable | Caller Action |
| :--- | :---: | :--- |
| `px_token_timeout` / `challenge_load_timeout` | Yes | Request a fresh `challengeUrl` and retry |
| `challenge_load_failed` | Yes | Verify outbound proxy connectivity |
| `challenge_proxy_auth` | No | Correct proxy credentials (HTTP 407 error) |
| `challenge_http_error` | Conditional | Retry on HTTP 5xx; abort on HTTP 4xx |
| `px_token_blocked` | Yes | Rotate proxy egress IP or rotate `vid` |
| `caller_vid_required` | Yes | Supply upstream `deviceId` or trigger silent harvest |
| `solver_busy` | Yes | Apply exponential backoff (concurrency limit reached) |

---

## Production Deployment

A systemd service unit and release script are provided in the `deploy/` directory:

```bash
# Service unit configuration: /etc/systemd/system/slidex-captcha.service
[Unit]
Description=SlideX CAPTCHA Solving Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/slidex
ExecStart=/opt/slidex/.venv/bin/uvicorn slidex.service.app:app --host 0.0.0.0 --port 8077 --workers 1
Restart=always
RestartSec=5
MemoryMax=1200M

[Install]
WantedBy=multi-user.target
```

Execute end-to-end acceptance tests:
```bash
python check/deep_check.py --endpoint http://127.0.0.1:8077 --key prod-secret-token
```

---

## Technology Stack Inventory

- **Core Runtime**: Python 3.10+ / Python 3.11
- **Web API Engine**: FastAPI / Starlette / Uvicorn
- **Browser Automation**: Playwright / Google Chrome DevTools Protocol (CDP)
- **Computer Vision**: OpenCV (cv2) / NumPy / SciPy
- **Trajectory Modeling**: Custom Cubic Bezier curves & human biomechanical jitter simulation
- **Process Management**: Systemd / Linux cgroups (`MemoryMax=1200M`)

---

## Contributing & License

Pull requests and issues are welcome. Ensure that all unit and regression test suites pass before submitting code changes:

```bash
pytest tests/ -q
```

Licensed under the [MIT License](LICENSE).
