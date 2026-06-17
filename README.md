# Slidex

<p align="center">
  <strong>automation-kit 视觉能力平台</strong><br>
  <em>Vision Challenge Platform for automation-kit</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
</p>

<p align="center">
  <strong>中文</strong> | <a href="README_EN.md">English</a>
</p>

Slidex 已从滑块验证码求解库升级为 `automation-kit` 生态的视觉能力平台。当前版本统一承接滑块验证码、OCR、截图识别证据、人工兜底会话和相关 telemetry/artifact 契约，同时保持 `SliderSolver` 对旧接入方式的兼容。

> 交付状态：当前版本已经通过仓库级自动化测试，可用于集成与验收交付；上线前仍建议在目标站点完成一次真实浏览器冒烟验证。

**特性**：
- 🎯 **多供应商支持** — 内置 Aliyun NoCaptcha、GeeTest 适配器，自动检测
- 🔎 **统一视觉接口** — `slidex.vision` 统一描述 slider、OCR、manual fallback
- 🧾 **OCR 能力内建** — `slidex.ocr` 提供 `OcrTextExtractor` / `OcrResult` / `FakeOcrExtractor`
- 🔌 **插件式扩展** — 10 分钟实现自定义 Provider，无需修改核心代码
- 🌐 **CDP 模式** — 连接已有浏览器，适合 TypeScript/Node 集成
- ♻️ **会话复用** — 支持 CDP、已有 Playwright `Page`、图片 bytes/path
- 🧠 **智能求解** — OpenCV 图像匹配 + 物理轨迹模拟 + 真人轨迹回放
- 🛡️ **反检测** — Stealth 参数 + JS 注入隐藏自动化特征

## 安装

```bash
pip install -e .
playwright install chromium
pip install -e ".[remote]"   # 可选：远程控制 API
pip install -e ".[automation-kit]"  # 可选：native automation-kit 适配
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

### 统一视觉 API

```python
from slidex.ocr import FakeOcrExtractor
from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)

solver = VisualChallengeSolver(
    ocr_extractor=FakeOcrExtractor(text="大麦", confidence=0.98)
)

ocr_result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.OCR_TEXT,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"fake-image",
    )
)

slider_result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
        cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
        page_url="https://...",
    )
)
```

### OCR API

```python
from slidex.ocr import FakeOcrExtractor

extractor = FakeOcrExtractor(text="A12", confidence=0.95, language="zh-CN")
result = extractor.extract(
    image_bytes=b"...png bytes...",
    roi={"x": 10, "y": 20, "width": 100, "height": 32},
)
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

输出 JSON（兼容旧字段，同时包含统一视觉结果字段）：

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

当前平台化会话契约还提供：

- `challenge_type`: 当前人工兜底所处理的视觉挑战类型
- `audit`: 会话创建、鼠标事件、完成状态的审计记录
- `ManualFallbackSession`: 可在 SDK 层直接构造统一的人工结果

```python
from slidex.vision import ChallengeType, ManualFallbackSession

session = ManualFallbackSession(
    session_id="session-1",
    challenge_type=ChallengeType.OCR_TEXT,
    token="secret-token",
    timeout_s=60,
)

result = session.complete_text("人工修正结果")
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
| `SLIDEX_TELEMETRY_ENABLED` | `1` | 启用结构化 E2E 埋点 |
| `SLIDEX_TELEMETRY_DIR` | `~/.slidex/telemetry` | telemetry JSONL 输出目录 |

### 11. E2E 数据监控

真实使用方开始调用后，建议同时接两条数据通道：

```python
from slidex import SlidexConfig, SliderSolver

def on_risk_log(**payload):
    # 适合写数据库/消息队列/日志平台
    db.insert("slidex_runs", payload)
    return payload["run_id"]

def on_risk_log_update(payload):
    # 适合实时看事件流
    stream.publish("slidex-events", payload)

config = SlidexConfig(
    on_risk_log=on_risk_log,
    on_risk_log_update=on_risk_log_update,
)

solver = SliderSolver(cookie_id="user_123", provider="auto", config=config)
```

监控重点建议直接盯这几项：

- `success` / `status`: 成功率与最终状态
- `elapsed_ms`: 单次求解耗时
- `provider_name`: 供应商分布
- `distance` / `distance_source`: 距离计算来源是否异常漂移
- `slide_code`: 验证接口返回码
- `fallback_used`: 人工兜底占比
- `failure_reason`: 失败原因聚类
- `cookie_count`: 求解后上下文是否产出有效 cookie

如果你走 CLI/CDP 集成，`python -m slidex.scripts.slide_solve_cdp` 现在也会在 JSON 输出中附带 `telemetry` 字段，可直接上报。

### 12. Artifact 与 automation-kit 适配

`slidex.vision` 当前提供稳定 artifact 辅助函数：

```python
from pathlib import Path
from slidex.vision import build_artifact_path, safe_artifact_metadata

artifact_path = build_artifact_path(
    root=Path("artifacts"),
    run_id="run-1",
    artifact_type="telemetry",
    name="events.jsonl",
)

metadata = safe_artifact_metadata({"token": "secret", "source": "unit"})
```

安装 `automation-kit` extra 后，还可以把统一视觉结果直接转成 action result / artifact / event：

```python
from slidex.integrations.automation_kit import to_action_result, to_artifacts, to_events
```

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
