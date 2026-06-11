# Slidex

<p align="center">
  <strong>通用滑块验证码求解库</strong><br>
  <em>Generic Slider CAPTCHA Solver</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Alpha">
</p>

> **[English](#features-english)**

---

## 功能特性

| 特性 | 说明 |
|------|------|
| **双引擎求解** | `SliderSolver`（异步 CDP）+ `XianyuSliderStealth`（同步，全功能） |
| **CDP 模式** | 连接已有浏览器实例，不启动新进程，适合 TypeScript/Node 集成 |
| **可配置选择器** | 支持 Aliyun NoCaptcha、GeeTest、Shumei 等不同验证码供应商 |
| **多源距离检测** | OpenCV 图像匹配 → JS DOM 计算 → CSS 宽度估算，链式 fallback |
| **人类轨迹模拟** | 4 阶段物理模型生成（慢启动→加速→中速→微调）+ 真人轨迹录制回放池 |
| **反检测** | Chromium 启动参数 + JS 注入（隐藏 webdriver/Canvas/WebGL 指纹） |
| **远程人工兜底** | WebSocket 实时截图 + 人工操作，失败时自动触发并录制轨迹 |
| **并发管理** | 内置 `SliderConcurrencyManager`，控制最大并发数和队列超时 |

## 安装

```bash
pip install -e .
pip install -e ".[remote]"   # 带远程控制 API
pip install -e ".[dev]"       # 开发依赖
playwright install chromium
```

## 快速开始

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

## CDP 模式（连接已有浏览器）

集成到已有的 Playwright/浏览器会话时（如 TypeScript 项目），使用 CDP 模式：

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="user_123", trajectory_mode="auto")

success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",  # 可选：先导航到此 URL
)
```

CLI 方式：

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --selectors '{"slider_btn": ".geetest_slider_button"}'
```

## 可配置选择器

覆盖默认 Aliyun NoCaptcha 选择器，适配其他验证码供应商：

```python
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

| 键 | 默认值 | 说明 |
|----|--------|------|
| `slider_btn` | `#nc_1_n1z` | 滑块按钮 |
| `slider_track` | `#nc_1_n1t` | 滑轨 |
| `bg_img` | `#nc_1_n1t img, ...` | 背景图 |
| `piece_img` | `.nc_iconfont, ...` | 缺口图 |
| `track_width` | `.nc_scale, [class*=track]` | 轨道宽度 |
| `slider_alt` | `(".nc_iconfont", ...)` | 备选滑块选择器 |
| `result_url_pattern` | `("/slide?", ...)` | 结果 URL 匹配模式 |
| `success_code` | `0` | 成功响应码 |

## 配置管理

```python
from slidex import SlidexConfig

config = SlidexConfig(
    max_concurrent=3,
    wait_timeout=60,
    trajectory_pool_enabled=True,
    remote_captcha_enabled=True,
    remote_captcha_timeout=180,
    browser_data_dir="/path/to/browser_profiles",
)

# 或从环境变量加载
config = SlidexConfig.from_env()
```

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SLIDEX_MAX_CONCURRENT` | `3` | 最大并发数 |
| `SLIDEX_WAIT_TIMEOUT` | `60` | 队列等待超时（秒） |
| `SLIDEX_TRAJ_POOL_DIR` | `~/.slidex/trajectories` | 轨迹存储路径 |
| `SLIDEX_BROWSER_DATA_DIR` | `~/.slidex/browser_data` | 浏览器数据路径 |
| `SLIDEX_REMOTE_ENABLED` | `1` | 启用远程人工兜底 |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | 远程兜底超时（秒） |

## 回调接口

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_risk_log_update=lambda **kwargs: db.update_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

## 远程控制 API

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# 打开 http://localhost:8000/api/captcha/control
```

## TypeScript 集成示例

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

## 项目结构

```
slidex/
├── solver.py            # 核心求解器（CDP 模式 + 选择器配置）
├── config.py            # SlidexConfig 配置管理
├── _image_match.py      # OpenCV 图像匹配（Canny + 模板匹配）
├── _trajectory.py       # 轨迹生成（4 阶段物理模型）
├── _trajectory_pool.py  # 轨迹池（录制/回放/LRU 轮转）
├── _stealth_patch.py    # 反检测参数和 JS 注入脚本
├── _concurrency.py      # 并发管理器
├── remote.py            # 远程人工兜底控制器
├── api.py               # FastAPI 路由
├── stealth.py           # XianyuSliderStealth 增强版求解器
└── scripts/
    └── slide_solve_cdp.py  # CDP 模式 CLI 入口
```

## License

MIT

---

<a id="features-english"></a>

## Features (English)

| Feature | Description |
|---------|-------------|
| **Dual solver engines** | `SliderSolver` (async CDP) + `XianyuSliderStealth` (sync, full-featured) |
| **CDP mode** | Connect to existing browser — no new process, ideal for TypeScript/Node integration |
| **Configurable selectors** | Support Aliyun NoCaptcha, GeeTest, Shumei, and other CAPTCHA providers |
| **Multi-source distance** | OpenCV image matching → JS DOM → CSS width, chain fallback |
| **Human-like trajectory** | 4-phase physics model + recorded trajectory replay pool |
| **Anti-detection** | Chromium stealth args + JS injection (webdriver/Canvas/WebGL) |
| **Remote fallback** | WebSocket screenshot + manual control, auto-triggered on failure |
| **Concurrency** | Built-in `SliderConcurrencyManager` |

### Quick Start

```python
from slidex import SlidexConfig, SliderSolver

solver = SliderSolver(cookie_id="user", cookies_str="...", headless=True, config=SlidexConfig())
success, cookies = await solver.solve("https://...")
```

### CDP Mode

```python
solver = SliderSolver(cookie_id="user_123")
success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",
)
```

### CLI

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --selectors '{"slider_btn": ".geetest_slider_button"}'
```
