<!-- markdownlint-disable-file MD033 MD045 -->
<div align="center">

# SlideX

**高性能 Headless 验证码求解平台、多模态视觉答题引擎与 PerimeterX 验证穿透中枢**

[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-009688.svg?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Driver-Playwright-2EAD33.svg?style=flat-square&logo=playwright&logoColor=white)](https://playwright.dev/)
[![OpenCV](https://img.shields.io/badge/Vision-OpenCV-5C3EE8.svg?style=flat-square&logo=opencv&logoColor=white)](https://opencv.org/)
[![Tests](https://img.shields.io/badge/tests-1013%20passed-brightgreen.svg?style=flat-square)]()

[English](./README.md) · [简体中文](./README.zh-CN.md) · [核心特性](#核心特性) · [系统架构](#系统架构全景) · [快速上手](#快速上手) · [打码站服务](#perimeterx-打码站服务) · [API 规范](#api-交互与错误规范)

<br>

<p align="center">
  <b>彻底终结现代自动化爬虫与业务流中频繁遭遇的验证码阻断与脆弱的坐标猜测。</b><br>
  将 Headless Playwright 驱动池、Chrome CDP 真实会话复用、OpenCV 图像匹配与多模态大模型视觉通道深度整合为统一防御穿透中枢。<br>
  原生支持阿里云 NoCaptcha / 2.0、极验 GeeTest、hCaptcha 多模态题型，以及内置针对 PerimeterX 的独立打码站（精准生成 px3/pxde/pxvid/pxcts 四件套）。
</p>

</div>

---

## 痛点与架构对比

自动化任务常面临频繁更新的风控拦截与滑块反爬机制。传统硬编码脚本维护成本极高，极易被标记为机器人：

| 评估维度 | SlideX 平台 | 传统脚本 / 选择器暴力点击 | 第三方商业打码平台 |
| :--- | :--- | :--- | :--- |
| **求解机制** | 统一多模态视觉管线 + 真实物理动力学生物轨迹 | 固定选择器点击 + 线性硬编码等待 | 远程 HTTP 接口黑盒中转 |
| **PerimeterX (PX) 支持** | 原生提取 `px3/pxde/pxvid/pxcts`，`:1000:` 强判据门禁 | 无法应对，直接触发拒绝态 | 高昂按次收费（每千次 $2-$5），延迟极高 |
| **证据与确定性** | 业务级凭据确认（`px3`、`h-captcha-response`、Token） | 仅以未报错作为成功判据（大量伪成功） | 不透传传感器上下文，无从排查 |
| **会话复用模式** | 支持 Headless、桌面 Chrome CDP 会话与无头图片内存流 | 强绑定单一本地浏览器上下文 | 无本地浏览器控制能力 |
| **反检测与指纹** | 四阶段真实物理模型、贝塞尔缓动与全套 JS 隐匿注入 | 机械匀速移动，易被设备指纹捕获 | 强依赖外部代理池质量 |
| **数据安全性** | 100% 局域网私有化部署，内存级 Token（30 分钟 TTL） | 本地脚本，碎片化 | 敏感登录态 Cookie 上传第三方公网 |

---

## 核心特性

| 模块 | 特性亮点 | 技术实现与优势 |
| :--- | :--- | :--- |
| **通用滑块求解** | 多厂商智能自动识别 | 内置 Aliyun NoCaptcha、Aliyun Captcha.js 2.0、GeeTest v3/v4 适配器，支持 DOM 签名探测与自动路由。 |
| **多模态视觉接口** | 统一挑战上下文抽象 | `slidex.vision` 统一标准化滑块缺口定位、OCR 字符抽取与人工接管会话，上层应用一套契约通调。 |
| **hCaptcha 深度适配** | 全题型答题流闭环 | 原生支持 9 宫格二选一、归一化坐标点选以及贝塞尔曲线拖放交互，无缝接入视觉识别通道。 |
| **PerimeterX 打码站** | 生产级四件套凭证生产 | 内置独立 FastAPI 服务，在独立隔离 Chromium 容器内捕获 `px3/pxde/pxvid/pxcts`，以 `:1000:` 为判定准则。 |
| **纯图片缺口检测** | 零浏览器无头轻量计算 | 级联检测管线：OpenCV Canny 边缘匹配 → YOLO 神经网络模型 → 几何轮廓分析 → 列能量投影兜底。 |
| **拟人生理轨迹引擎** | 4 阶段动力学模拟 | 模拟真人操作：慢启动 → 匀加速 → 匀速滑动 → 像素级微调回弹，配合贝塞尔曲线生成不可预测轨迹。 |
| **Chrome CDP 桥接** | 无感复用真实浏览器会话 | 直连日常 Chrome 调试端口，静默创建并销毁任务标签页，原生穿透 Cloudflare Turnstile 等严苛反爬策略。 |
| **微服务化 API** | 同步/异步全兼容调度 | 提供高并发低延迟 `/v1/solve` 与 captcha.run 兼容的 `/v2/tasks`，内存安全隔离防泄露。 |

---

## 系统架构全景

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   上层调用方 / 业务中枢 / 爬虫管线                      │
│         (automation-kit / 自动化测试 / 定时调度 / HTTP 客户端)          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        SlideX 视觉与求解中枢                           │
│  ├─ VisualChallengeSolver 统一抽象  ├─ 供应商特征嗅探器 (Registry)      │
│  ├─ 拟人物理轨迹模拟引擎            ├─ 反检测 Stealth JS 注入矩阵       │
│  └─ 传感器诊断与脱敏观测器          └─ 进程内并发信号量与排队守护       │
└───────┬───────────────────────────┬───────────────────────────┬────────┘
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│ 通用滑块求解器│           │ 视觉与 OCR    │           │ PX 打码工作站 │
│ 阿里云 / 极验 │           │ hCaptcha /    │           │ Press / Silent│
│ Canny / YOLO  │           │ 多模态答题    │           │ :1000: 门限   │
└───────┬───────┘           └───────┬───────┘           └───────┬───────┘
        │                           │                           │
        └───────────────────────────┼───────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          底层执行与网络底座                            │
│  ├─ Headless Playwright 驱动池     ├─ Chrome Remote Debugging (CDP)    │
│  ├─ OpenCV 矩阵计算引擎            └─ 出口代理会话池 (同一 IP 绑定)    │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 支持的验证码生态

| 供应商 / 题型 | 标识符 | 模式 | 运行状态 |
| :--- | :--- | :--- | :--- |
| **阿里云 NoCaptcha** | `aliyun-nocaptcha` | 网页 DOM 注入 / 滑块 | 内置支持 |
| **阿里云 Captcha.js 2.0** | `aliyun-captcha-js` | 滑块 / 参数透传 | 内置支持 |
| **极验 GeeTest v3 / v4** | `geetest` | 拼图滑块 / 完整页面 | 内置支持 |
| **hCaptcha (九宫格识别)** | `image_label_binary` | 多模态视觉匹配 | 内置支持 |
| **hCaptcha (坐标区域选点)** | `image_label_area_select` | 归一化点击 | 内置支持 |
| **hCaptcha (拖拽匹配)** | `image_drag_drop` | 贝塞尔轨迹拖拽 | 内置支持 |
| **PerimeterX (PX Press)** | `px_press` | Chromium 隔离渲染 | 内置支持 |
| **PerimeterX (PX Silent)** | `silent` | 种子捕获 / 静默嗅探 | 内置支持 |
| **纯图片缺口检测** | `SliderImageSolver` | Canny / YOLO / 轮廓 | 内置支持 |
| **光学字符识别 (OCR)** | `slidex.ocr` | 区域字符提取 | 内置支持 |

---

## 快速上手

### 1. 环境准备

- Python 3.10+ (推荐 Python 3.11)
- Chromium 或 Google Chrome

```bash
# 克隆仓库
git clone https://github.com/dengyie/slidex.git
cd slidex

# 创建并激活独立虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装核心模块与 Playwright 驱动
pip install -e .
playwright install chromium

# 按需安装扩展依赖组
pip install -e ".[server]"          # 自建打码微服务 (FastAPI / Uvicorn)
pip install -e ".[vision]"          # YOLO 神经网络缺口定位模型
pip install -e ".[automation-kit]"  # automation-kit 原生能力组
```

### 2. Python 快速调用

#### 自动探测模式

```python
from slidex import SliderSolver

# 自动嗅探目标页面的验证码类型并调度适配器
solver = SliderSolver(cookie_id="session_01", provider="auto")
success, cookies = await solver.solve("https://example.com/login_check")
```

#### 纯图片缺口定位 (无需浏览器环境)

```python
from slidex.vision import SliderImageSolver

solver = SliderImageSolver()

# 模板匹配：背景图 + 拼图滑块
result = solver.solve("background.png", "piece.png")

# 无切片滑块图：直接定位缺口
result = solver.solve(b"...raw image bytes...")

print(result.to_dict())
# {"success": True, "gap_x": 182, "confidence": 0.87, "method": "template_edge", ...}
```

#### 多模态 hCaptcha 求解

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

## PerimeterX 打码站服务

针对 PerimeterX（PX）防爬盾，SlideX 提供完整的独立打码站能力。通过在无头 Chromium 容器内加载真实目标站点 iframe，捕获 `px3`、`pxde`、`pxvid`、`pxcts` 四件套，并且严格验证 `px3` 必须包含 `:1000:`（拒绝级常为 `:200:`）。

### 1. 启动服务

```bash
SLIDEX_CAPTCHA_KEYS="prod-secret-token" \
SLIDEX_PX_CONCURRENCY=5 \
uvicorn slidex.service.app:app --host 0.0.0.0 --port 8077
```

### 2. 同步求解接口 (`POST /v1/solve`)

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

#### 成功响应样例
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

### 3. 异步任务接口 (`POST /v2/tasks` 与 `GET /v2/tasks/{taskId}`)

全面兼容 captcha.run 协议标准，支持任务排队轮询与 `taskId` 幂等下发。

---

## API 交互与错误规范

HTTP 状态码严格表达传输与认证层状态（400/401/422/429/500），业务求解结果由响应体中的 `code` 与结构化字段承载：

| 错误类别 (Kind) | 是否可重试 | 调用方标准动作 |
| :--- | :---: | :--- |
| `px_token_timeout` / `challenge_load_timeout` | 是 | 刷新并获取新的 `challengeUrl` 后重试 |
| `challenge_load_failed` | 是 | 检查出口代理网络连通性后重试 |
| `challenge_proxy_auth` | 否 | 修正代理用户名与密码（HTTP 407 错误） |
| `challenge_http_error` | 按需 | 5xx 状态码可重试，4xx 参数错误直接终止 |
| `px_token_blocked` | 是 | 轮换出口代理 IP 或轮换 `vid` 凭据 |
| `caller_vid_required` | 是 | 补充上游 `deviceId` 或先触发 silent 拿凭证 |
| `solver_busy` | 是 | 启动退避策略后重试（并发池打满） |

> **同 IP 铁律**：带 `proxy` 的任务在后续使用 token 的所有业务流程中，必须走相同的出口 IP，否则生成的验证码将立刻作废失效。

---

## 生产部署实践

生产环境资产位于 `deploy/` 目录：

```bash
# Systemd 服务单元配置：/etc/systemd/system/slidex-captcha.service
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

执行全链路验收校验：
```bash
python check/deep_check.py --endpoint http://127.0.0.1:8077 --key prod-secret-token
```

---

## 全栈技术清单

- **核心运行环境**：Python 3.10+ / Python 3.11
- **API 驱动引擎**：FastAPI / Starlette / Uvicorn
- **浏览器自动化底座**：Playwright / Google Chrome DevTools Protocol (CDP)
- **机器视觉与算法**：OpenCV (cv2) / NumPy / SciPy
- **轨迹动力学算法**：三次贝塞尔拟合 + 生物微颤动物理模拟
- **进程与资源隔离**：Systemd / Linux Cgroup (`MemoryMax=1200M`)

---

## 参与贡献与许可证

欢迎提交 Issue 和 Pull Request！
在提交代码前，请确保全量测试通过：

```bash
pytest tests/ -q
```

本项目基于 [MIT License](LICENSE) 协议发布。
