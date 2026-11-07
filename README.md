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

> 生态开发基线：见 [`automation-kit/docs/development.md`](https://github.com/dengyie/automation-kit/blob/main/docs/development.md)。

**特性**：
- 🎯 **多供应商支持** — 内置 Aliyun NoCaptcha、AliyunCaptcha.js 2.0、GeeTest 适配器，自动检测
- 🔎 **统一视觉接口** — `slidex.vision` 统一描述 slider、OCR、manual fallback
- 🧩 **hCaptcha 兼容** — 9 宫格二选 / 区域点选 / 拖放三型，复用多模态视觉通道
- 🔑 **PX press-token** — PerimeterX press/silent 四件套（`px3/pxde/pxvid/pxcts`，`:1000:` 判据），内置可自托管打码站（`/v1/solve`、captcha.run 兼容 `/v2/tasks`、`/health`）
- 🖼️ **纯图片滑块识别** — `SliderImageSolver` 只给图片即可定位缺口，无需浏览器/网络
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
pip install -e ".[vision]"   # 可选：纯图片无块图缺口检测的 YOLO 后端
pip install -e ".[server]"   # 可选：自建打码站（FastAPI/uvicorn）
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

### hCaptcha

hCaptcha 与滑块族是两套协议：挑战运行在
`iframe[src^="https://newassets.hcaptcha.com/captcha/v1/"]` 内，判决走
`/checkcaptcha/` 的 `{"pass": bool}`，成功产物是 `h-captcha-response` token
（不是 cookie）。因此它不走 `CaptchaProvider` 流水线，而是作为
`ChallengeType.HCAPTCHA` 由 `VisualChallengeSolver` 分派。识别复用同一套
`SLIDEX_VISION_*` 多模态通道，无需额外配置。

```python
from slidex.vision import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision import VisualChallengeSolver

solver = VisualChallengeSolver()
result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.HCAPTCHA,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=page,                       # 必填：hCaptcha 全程在活页面上
        question_language="auto",         # 题面语种：auto 中文优先，再回退 en
        hcaptcha={"max_rounds": 3},       # 可选：轮次上限
    )
)

if result.success:
    token = await page.input_value("textarea[name='h-captcha-response']")
```

支持的三型挑战：

| 题型 | 判定依据 | 页面动作 |
|------|---------|---------|
| `image_label_binary` | `.task` 数量 = 9 | 点击命中的格子中心 |
| `image_label_area_select` | `.task` 数量 = 0 且拖放判定不成立 | 按归一化坐标点击 |
| `image_drag_drop` | 引擎判定为拖放 | 贝塞尔路径拖拽（起止缓动 + 微抖动） |

判决与失败处理：

- `success=True` 表示 `/checkcaptcha/` 回了 `pass`（或已拿到
  `h-captcha-response` token）。
- **提交后不重试**：`hcaptcha_failed` / `hcaptcha_verdict_pending` 一律
  `retryable=False`——同一题重复提交只会追加判罚样本。只有
  `hcaptcha_challenge_timeout` / `screenshot_failed`（尚未触碰页面）标为可重试。
- 提交后若题面画面**原地不动**，执行器直接停手；只有画面已刷新（进入下一
  crumb）才继续解下一页。

能力边界（不夸大）：

- 只覆盖**视觉/答题层**。通过率同时受浏览器指纹与 IP 信誉影响，那属于
  stealth 层，不在本能力的职责内。
- **不逆向** `hsw.js`、不解析 msgpack 密文载荷：优先读 `/getcaptcha/` 的明文
  JSON；拿不到时直接截挑战画面 + 读 DOM 题面，两条路都只依赖公开页面结构。

### PX press-token 与自建打码站

针对 PerimeterX（PX）的 press/silent 挑战，slidex 提供一个可自托管的打码站：
headless Chromium 打开 hsprotect iframe（或站点页），等 PX JS 自行写入
`_px3`，再从 cookie jar 抽出四件套 `{"px3","pxde","pxvid","pxcts"}`。
成功判据只有一条：**`px3` 必须含 `:1000:`**（拒绝级常见 `:200:`）。

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

启动打码站（`slidex[server]`）：

```bash
SLIDEX_CAPTCHA_KEYS=your-key uvicorn slidex.service.app:app --host 0.0.0.0 --port 8000
```

三种入口：

| 接口 | 用途 |
|------|------|
| `POST /v1/solve` | 同步求解，`{"type":"px_press", ...}` → `{"code":0,"data":{四件套}}` |
| `POST /v2/tasks/` + `GET /v2/tasks/{id}` | captcha.run 兼容的异步任务协议 |
| `GET /health` | `{ok, active_tasks, done_24h, fail_24h, types}` |

```bash
curl -s http://127.0.0.1:8000/v1/solve \
  -H 'Authorization: Bearer your-key' -H 'Content-Type: application/json' \
  -d '{"type":"px_press","appId":"PXzC5j78di",
       "challengeUrl":"https://iframe.hsprotect.net/...",
       "websiteURL":"https://signup.live.com/","country":"US"}'
```

错误语义分两层：HTTP 状态码表达传输/鉴权/参数（400/401/422/429/500），
响应体 `code` 表达求解结果（0 成功；1001 通用失败；1002 超时；1003 IP/appId
被拦；1004 并发已满）。求解失败通常是 HTTP 200 + 非零 `code`，并带
`data.kind` 与 `data.retryable`，调用方据此决定是否重试。

要点：

- **同 IP 铁律**：任务里只要带了 `proxy`，浏览器 context 与所有出站请求都走它；
  出口不一致会让 token 直接作废。空 proxy 表示直连，仅限本机调试。
- **不落盘**：任务只存在内存里（30 分钟 TTL），完成即弃；日志只打印 token
  前缀与长度，不输出完整 cookie/token。
- **并发**：`SLIDEX_PX_CONCURRENCY`（默认 5），排队超时返回 `solver_busy`。
- **兼容**：`type` 支持别名 `px_press` / `press` / `pxcaptcha2` / `silent` 等
  （大小写与连字符不敏感）；`geetest_v3` 转调 `SliderSolver` 保留旧调用方。

### OCR API

```python
from slidex.ocr import FakeOcrExtractor

extractor = FakeOcrExtractor(text="A12", confidence=0.95, language="zh-CN")
result = extractor.extract(
    image_bytes=b"...png bytes...",
    roi={"x": 10, "y": 20, "width": 100, "height": 32},
)
```

### 纯图片滑块 API（无浏览器）

只拿得到图片（截图、抓包图像、裁剪产物）时，直接定位缺口，不启动浏览器、不产生 cookie：

```python
from slidex.vision import SliderImageSolver

solver = SliderImageSolver()

# 有拼图块图：Canny 边缘 + 模板匹配（method="template_edge"）
result = solver.solve("bg.png", "piece.png")

# 无块图：YOLO 后端（装了 slidex[vision]）→ 轮廓几何 → 列能量剖面
result = solver.solve(b"...png bytes...")

# 从整页截图里只检测验证码区域（ROI，坐标自动平移回原图）
result = solver.solve(
    "full_page.png",
    roi={"x": 320, "y": 180, "width": 300, "height": 160},
    distance_scale=1.5,  # 可选：图像像素 → 实际滑动行程换算
)

print(result.to_dict())
# {"success": True, "gap_x": 182, "confidence": 0.87, "method": "template_edge",
#  "gap_box": [182, 40, 222, 84], "candidates": [...], ...}
```

多策略检测管线（`auto` 按可用性自动选择，全失败返回 `success=False`）：

| 方法 | 触发条件 | 依赖 |
|------|---------|------|
| `template_edge` | 提供拼图块图 | 内置（OpenCV） |
| `yolo` | 无块图，装了 `slidex[vision]` | `captcha-recognizer` |
| `contour` | 无块图，无 YOLO 后端 | 内置（OpenCV） |
| `column_profile` | 轮廓无结果的兜底 | 内置（OpenCV） |

> `gap_x` 是缺口左边缘在图像坐标系的 x 像素，不做供应商 offset 校正（校正是调用方/供应商层职责）。
> 实际滑动距离需减去拼图块初始偏移，并按 `distance_scale` 做渲染缩放换算。

也可走统一视觉 API（`VisualChallengeSolver`）或 CLI：

```python
from slidex.vision import (
    ChallengeType, VisionContext, VisualChallengeRequest, VisualChallengeSolver,
)

solver = VisualChallengeSolver()
result = await solver.solve(
    VisualChallengeRequest(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.IMAGE_BYTES,
        image_bytes=b"...bg png...",
        piece_image_bytes=b"...piece png...",  # 可选
        roi={"x": 320, "y": 180, "width": 300, "height": 160},  # 可选
        metadata={"distance_scale": 1.5},  # 可选
    )
)
```

```bash
python -m slidex.scripts.slide_solve_image \
  --background bg.png \
  [--piece piece.png] \
  [--roi 320,180,300,160] \
  [--distance-scale 1.5]
```

### automation-kit 能力适配

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
| 阿里云 Captcha.js 2.0 拼图（z.ai 系登录） | `aliyun-captcha-js` | ✅ 内置 |
| 极验 GeeTest v3/v4 | `geetest` | ✅ 内置 |
| 数美 Shumei | `shumei` | 📝 待实现 |
| 顶象 Dingxiang | `dingxiang` | 📝 待实现 |
| 自定义 | 你的 Provider | 🔌 [按生态总纲实现](https://github.com/dengyie/automation-kit/blob/main/docs/development.md) |

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

#### z.ai 系登录（AliyunCaptcha.js 2.0）：一站式取参

```python
from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

provider = AliyunCaptchaJsProvider()
# 武装全新控件 → 缺口检测 → 二次反解 → 拖拽 → 捕获 captchaVerifyParam（失败自动重试）
result = await provider.acquire_verify_param(page, "1nl1cfkp")   # z.ai 登录场景
if result["param"]:
    # 拿 param 后由调用方 POST 业务端点（如 z.ai /auths/signin 的 captcha_verify_param）
    ...
```

适用 API 登录类集成（z.ai 登录只放行手机号 + 滑块，param 由本 provider 注入的
success 回调写入 `window.__slidexCaptchaParam`）。注意运行时红线：控件会话分钟级
过期（尽快拖）；失败会换 cert 并重置弹窗，重试前必须重新定位——这些已内化在
`acquire_verify_param` 的重试循环里。

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

Provider 的公共边界、取消能力、资源所有权和 automation-kit 接入规范统一维护在
[生态开发总纲](https://github.com/dengyie/automation-kit/blob/main/docs/development.md)。

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
| `SLIDEX_HCAPTCHA_MAX_ROUNDS` | `3` | hCaptcha 单次挑战最多解几轮 |
| `SLIDEX_HCAPTCHA_TILE_PADDING` | `3.0` | 九宫格点击抖动上限（像素） |
| `SLIDEX_HCAPTCHA_PROMPT` | 空（内置提示词） | 覆盖 hCaptcha 判题提示词 |
| `SLIDEX_CAPTCHA_KEYS` | 空（开放模式） | 打码站 API key，逗号分隔多把 |
| `CAPTCHA_RUN_KEY` | 空 | 兼容 captcha.run 的单 key 写法（`SLIDEX_CAPTCHA_KEYS` 优先） |
| `SLIDEX_PX_CONCURRENCY` | `5` | PX 求解并发上限 |
| `SLIDEX_PX_HEADLESS` | `1` | PX 求解是否 headless（调试时可置 0） |

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

安装 `automation-kit` extra 后，通过唯一的 capability provider 适配器返回
`CapabilityResult`、artifact 和 `capability.end` 事件：

```python
from slidex.integrations.automation_kit import SlidexVisualCapability
```

---

## License

MIT
