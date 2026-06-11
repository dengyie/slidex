# Slidex

<p align="center">
  <strong>通用滑块验证码求解库</strong><br>
  <em>Generic Slider CAPTCHA Solver</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
</p>

> **[English](#features-english)**

Slidex 是一个通用的滑块验证码自动求解库。支持连接已有浏览器（CDP 模式）或独立启动浏览器，内置图像识别、轨迹模拟、反检测和远程人工兜底。适用于批量账号验证、自动化流程中遇到滑块验证码的场景。

## 安装

```bash
pip install -e .
playwright install chromium
pip install -e ".[remote]"   # 可选：远程控制 API
```

## 快速开始

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

## 技术概览

- **图像识别**：OpenCV Canny 边缘检测 + 模板匹配定位缺口，JS 计算交叉验证
- **轨迹模拟**：4 阶段物理模型（慢启动→加速→中速→微调），支持真人轨迹录制回放
- **反检测**：Chromium 启动参数 + JS 注入隐藏自动化特征
- **可配置选择器**：通过 `selectors={}` 参数适配不同验证码供应商（Aliyun、GeeTest、Shumei 等）

---

## 接入指南

### 1. CDP 模式（推荐，连接已有浏览器）

适用于已有 Playwright/浏览器会话的场景（如 TypeScript 项目）。不启动新浏览器，通过 CDP 协议连接：

```python
from slidex import SliderSolver

solver = SliderSolver(
    cookie_id="user_123",
    trajectory_mode="auto",
    selectors={  # 可选：覆盖默认选择器
        "slider_btn": ".geetest_slider_button",
        "slider_track": ".geetest_slider_track",
    },
)

success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",  # 可选：先导航到此 URL
)

await solver.close()
```

### 2. CLI 调用（适合 TypeScript/Node 子进程调用）

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --selectors '{"slider_btn": ".geetest_slider_button"}'
```

输出 JSON：

```json
{"success": true, "cookies": {...}, "elapsed_ms": 3200.5, "error": null}
```

### 3. TypeScript 集成示例

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

### 4. 自定义选择器

默认适配 Aliyun NoCaptcha。其他供应商需要传入对应选择器：

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

完整配置项见源码 `slidex/solver.py` 中的 `DEFAULT_SELECTORS`。

### 5. 远程人工兜底

自动求解失败时，可通过 WebSocket 将验证码推送给人工操作：

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# 打开 http://localhost:8000/api/captcha/control
```

### 6. 回调接口

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

### 7. 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SLIDEX_MAX_CONCURRENT` | `3` | 最大并发数 |
| `SLIDEX_BROWSER_DATA_DIR` | `~/.slidex/browser_data` | 浏览器数据路径 |
| `SLIDEX_TRAJ_POOL_DIR` | `~/.slidex/trajectories` | 轨迹存储路径 |
| `SLIDEX_REMOTE_ENABLED` | `1` | 启用远程人工兜底 |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | 远程兜底超时（秒） |

---

## License

MIT

---

<a id="features-english"></a>

## English Summary

Slidex is a generic slider CAPTCHA solver. It connects to an existing browser via CDP or launches its own, with built-in image recognition, trajectory simulation, anti-detection, and remote human fallback.

### Quick Start

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="user", cookies_str="...")
success, cookies = await solver.solve("https://...")
```

### CDP Mode (connect to existing browser)

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
