# Slidex

<p align="center">
  <strong>通用滑块验证码求解库</strong><br>
  <em>Generic Slider CAPTCHA Solver</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
</p>

<p align="center">
  <strong>中文</strong> | <a href="README_EN.md">English</a>
</p>

Slidex 是一个专业的滑块验证码自动求解库。支持多供应商自动适配、CDP 模式集成、图像识别、轨迹模拟和反检测。适用于批量账号验证、自动化流程中遇到滑块验证码的场景。

> 交付状态：当前版本已经通过仓库级自动化测试，可用于集成与验收交付；上线前仍建议在目标站点完成一次真实浏览器冒烟验证。

**特性**：
- 🎯 **多供应商支持** — 内置 Aliyun NoCaptcha、GeeTest 适配器，自动检测
- 🔌 **插件式扩展** — 10 分钟实现自定义 Provider，无需修改核心代码
- 🌐 **CDP 模式** — 连接已有浏览器，适合 TypeScript/Node 集成
- 🧠 **智能求解** — OpenCV 图像匹配 + 物理轨迹模拟 + 真人轨迹回放
- 🛡️ **反检测** — Stealth 参数 + JS 注入隐藏自动化特征

## 安装

```bash
pip install -e .
playwright install chromium
pip install -e ".[remote]"   # 可选：远程控制 API
```

## 快速开始

### 自动检测模式（推荐）

```python
from slidex import SliderSolver

# 自动识别验证码供应商
solver = SliderSolver(cookie_id="my_user", provider="auto")
success, cookies = await solver.solve("https://...verification_url...")
```

### 手动指定供应商

```python
# Aliyun NoCaptcha
solver = SliderSolver(provider="aliyun-nocaptcha")

# GeeTest 极验
solver = SliderSolver(provider="geetest")
```

### Legacy 模式（向后兼容）

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

- **Provider 抽象**：统一接口适配不同供应商，内置 Aliyun、GeeTest，支持插件扩展
- **图像识别**：OpenCV Canny 边缘检测 + 模板匹配定位缺口，JS DOM 交叉验证
- **轨迹模拟**：4 阶段物理模型（慢启动→加速→中速→微调），真人轨迹录制回放
- **反检测**：Chromium Stealth 启动参数 + JS 注入隐藏自动化特征

---

## 支持的验证码供应商

| 供应商 | Provider 名称 | 状态 |
|--------|--------------|------|
| 阿里云 NoCaptcha | `aliyun-nocaptcha` | ✅ 内置 |
| 极验 GeeTest v3/v4 | `geetest` | ✅ 内置 |
| 数美 Shumei | `shumei` | 📝 待实现 |
| 顶象 Dingxiang | `dingxiang` | 📝 待实现 |
| 自定义 | 你的 Provider | 🔌 [10 分钟实现](docs/PROVIDER_GUIDE.md) |

---

## 接入指南

### 1. 自动检测模式（推荐）

最省心的方式，Slidex 自动识别当前网站使用的验证码供应商：

```python
from slidex import SliderSolver

solver = SliderSolver(cookie_id="user_123", provider="auto")
success, cookies = await solver.solve("https://...")
```

### 2. 手动指定供应商

当你明确知道网站使用哪家供应商时：

```python
# GeeTest 极验
solver = SliderSolver(provider="geetest")

# Aliyun NoCaptcha
solver = SliderSolver(provider="aliyun-nocaptcha")
```

适用于已有 Playwright/浏览器会话的场景（如 TypeScript 项目）。不启动新浏览器，通过 CDP 协议连接：

```python
from slidex import SliderSolver

solver = SliderSolver(
    cookie_id="user_123",
    provider="auto",  # CDP 模式也支持自动检测
)

success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",  # 可选：先导航到此 URL
)

await solver.close()
```

### 4. 自定义 Provider

10 分钟实现一个新供应商适配器：

```python
from slidex import CaptchaProvider, SliderSolver

class MyProvider(CaptchaProvider):
    name = "my-custom"
    
    async def detect(self, page):
        return await page.query_selector(".my-captcha") is not None
    
    async def locate_elements(self, page):
        # 定位元素...
    
    async def extract_images(self, page, elements):
        # 提取图像...
    
    async def perform_slide(self, page, elements, gap_x, trajectory):
        # 执行滑动...
    
    async def validate_response(self, response):
        # 判断结果...

# 注册并使用
SliderSolver.register_provider("my-custom", MyProvider)
solver = SliderSolver(provider="my-custom")
```

详见 [Provider 开发指南](docs/PROVIDER_GUIDE.md)。

### 5. CLI 调用（适合 TypeScript/Node 子进程调用）

```bash
python -m slidex.scripts.slide_solve_cdp \
  --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
  --page-url "https://..." \
  --provider auto  # 自动检测
```

输出 JSON：

```json
{"success": true, "cookies": {...}, "elapsed_ms": 3200.5, "error": null}
```

### 6. TypeScript 集成示例

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

### 7. Legacy 模式（自定义选择器）

向后兼容：手动配置选择器，不使用 Provider：

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

### 8. 远程人工兜底

自动求解失败时，可通过 WebSocket 将验证码推送给人工操作：

```bash
pip install -e ".[remote]"
uvicorn slidex.api:router --port 8000
# 打开 http://localhost:8000/api/captcha/control
```

### 9. 回调接口

```python
config = SlidexConfig(
    on_risk_log=lambda **kwargs: db.save_log(kwargs),
    on_notification=lambda cookie_id, msg, title: send_alert(msg),
)
```

### 10. 环境变量配置

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

## 阶段总结

### 阶段 1：生产交付收口

- 完成情况：修复远程人工兜底安全边界、Provider 结果监听竞态、轨迹目录逃逸；补齐安全与回归测试；同步更新中英文 README 与 Provider 文档。
- 决策记录：
  - 问题：仓库内没有独立 `todo` 或阶段计划文件，但目标要求按阶段闭环推进。
  - 选择：将“代码契约与交付文档一致性”定义为当前收口阶段，优先修复会直接影响交付可信度的文档失配。
  - 理由：当前代码已具备可验证行为，最大的交付风险来自接口文档与实际实现不一致。
  - 风险：尚未在真实第三方验证码站点执行端到端人工验收，首次上线仍需目标环境冒烟验证。
- 审查问题与修复：
  - 已修复：远程控制 session token 缺失鉴权、轨迹 `cookie_id` 路径穿越、控制页 token 日志泄漏、Provider 监听器过早卸载与异常路径未清理。
  - 已修复：Provider 文档仍使用旧的同步 `validate_response()` / 旧监听生命周期示例。
- todo 状态：仓库内未发现独立 todo 文件；已按现有开发文档完成当前可验证收口项。
- 下一阶段风险：真实站点兼容性、部署环境依赖安装、人工兜底链路的浏览器级冒烟尚需目标环境确认。
