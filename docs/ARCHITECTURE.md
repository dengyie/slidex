# Slidex 架构设计

## 概览

Slidex 当前已经形成四条主路径：

1. `SliderSolver` 主求解流程
2. `CaptchaProvider` 插件式供应商适配层
3. `slidex.vision` / `slidex.ocr` 统一视觉契约层
4. 远程人工兜底 API 与控制页

当前代码目标是：

- 自动检测或手动指定验证码供应商
- 通过统一视觉模型承接 slider、OCR、manual fallback
- 在 provider 模式和 legacy 模式之间平滑切换
- 自动求解失败时进入远程人工兜底
- 保持轨迹池、会话状态与安全边界可控

## 核心模块

### `SliderSolver`

`slidex/solver.py` 中的 `SliderSolver` 是总入口，负责：

- 浏览器初始化与页面加载
- provider 模式和 legacy 模式调度
- CDP 模式复用已有浏览器
- 失败后切换到远程人工兜底
- 轨迹池与配置集成

典型入口：

```python
solver = SliderSolver(cookie_id="user_123", provider="auto")
success, cookies = await solver.solve("https://...")
```

CDP 模式：

```python
success, cookies = await solver.solve_on_existing_page(
    cdp_endpoint="ws://localhost:9222/devtools/browser/xxx",
    page_url="https://...",
)
```

已有 Playwright `Page` 复用：

```python
success, cookies = await solver.solve_on_page(page, page_url="https://...")
```

### `slidex.vision`

`slidex/vision` 提供平台级公共契约：

- `ChallengeType`
- `VisionContext`
- `VisualChallengeRequest`
- `VisualChallengeResult`
- `VisualChallengeSolver`
- `ProviderManifest` / `ProviderDecision`
- `build_artifact_path()` / `safe_artifact_metadata()`
- `ManualFallbackSession`

`VisualChallengeSolver` 当前负责：

- `slider_captcha` 路由到既有 `SliderSolver`
- `ocr_text` / `image_text` 路由到 OCR extractor
- 输出统一 `VisualChallengeResult`

### `slidex.ocr`

`slidex/ocr` 是独立于浏览器生命周期的 OCR 表面，当前包含：

- `OcrTextExtractor` 协议
- `OcrResult`
- `FakeOcrExtractor`

输入模式已覆盖：

- `image_bytes`
- `image_path`
- ROI 区域参数
- 未来保留 `android_screenshot_bytes` 语义位于 `VisionContext`

### `ProviderSolverMixin`

`slidex/_provider_mixin.py` 负责把 provider 体系接入 `SliderSolver`：

- 自动检测 provider
- 调用 `locate_elements()` / `extract_images()` / `find_gap()` / `perform_slide()`
- 统一等待 `get_result()`
- 在成功、失败和异常路径都执行 `cleanup_after_result()`

### `CaptchaProvider`

当前 provider 抽象定义在 `slidex/providers/__init__.py`，真实契约如下：

```python
class CaptchaProvider(ABC):
    @abstractmethod
    async def detect(self, page: Page) -> bool: ...

    @abstractmethod
    async def locate_elements(self, page: Page) -> ProviderElements: ...

    @abstractmethod
    async def extract_images(
        self, page: Page, elements: ProviderElements
    ) -> Tuple[bytes, bytes]: ...

    async def find_gap(
        self, bg_bytes: bytes, piece_bytes: bytes
    ) -> Tuple[Optional[int], float]: ...

    @abstractmethod
    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> None: ...

    @abstractmethod
    async def validate_response(self, response: Response) -> Optional[bool]: ...

    async def get_result(self, page: Page, timeout_ms: int = 5000) -> SolveResult: ...

    async def cleanup_after_result(self, page: Page) -> None: ...
```

关键点：

- `validate_response()` 是异步接口，因为 Playwright `response.body()` 需要 `await`
- `find_gap()` 默认返回 `(gap_x, confidence)`，底层使用 `SliderImageMatcher.find_gap_with_confidence()`
- 内置 provider 应把临时事件监听器的释放放到 `cleanup_after_result()`，而不是在 `perform_slide()` 结束时立即移除

## 数据结构

### `VisualChallengeResult`

```python
@dataclass
class VisualChallengeResult:
    success: bool
    challenge_type: ChallengeType
    provider: str
    confidence: float = 0.0
    duration_ms: float = 0.0
    error_code: Optional[str] = None
    retryable: bool = False
    cookies: Optional[Dict[str, str]] = None
    artifacts: List[VisionArtifact] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

说明：

- SDK 序列化默认会对 `cookies` 与敏感 metadata 做脱敏
- CLI/CDP 输出为兼容现有调用方，会保留原始 `cookies` 顶层字段

### `ProviderElements`

```python
@dataclass
class ProviderElements:
    slider_btn: ElementHandle
    slider_track: ElementHandle
    bg_img: Optional[ElementHandle]
    piece_img: Optional[ElementHandle]
    track_width_px: int
    metadata: Optional[Dict] = None
```

### `SolveResult`

```python
@dataclass
class SolveResult:
    success: bool
    cookies: Optional[Dict]
    error: Optional[str] = None
    need_retry: bool = False
    confidence: float = 0.0
```

## Provider 注册与检测

`ProviderRegistry` 负责：

- 注册 provider 类
- 维护 `detection_priority`
- 自动检测时按优先级依次尝试

当前内置 provider：

1. `aliyun-nocaptcha`
2. `geetest`

说明：

- 文档中过去提到的 `geetest-v3` / `geetest-v4` 独立类并不存在于当前实现
- v3/v4 由单个 `GeeTestProvider` 在运行时区分
- provider 现在还带有 `ProviderManifest`，可按 `ChallengeType` 和 `VisionContext` 过滤

## 远程人工兜底

远程人工兜底由两部分组成：

1. `slidex/remote.py`
2. `slidex/api.py` + `slidex/_html/captcha_control.html`

流程：

1. `SliderSolver` 自动求解失败
2. 创建带随机 token 的远程 session
3. 通过通知回调发送控制页 URL
4. 前端控制页通过 token 建立 WebSocket
5. 人工完成拖动后提交轨迹

当前平台化补充：

- session state 增加 `challenge_type`
- session state 增加 `audit`
- `ManualFallbackSession` 可以直接生成统一的人工完成结果

### 安全边界

当前实现包含以下安全约束：

- 远程 session 使用随机 token 鉴权
- WebSocket 与 HTTP 控制接口都校验 token
- 控制页日志不再打印明文 token
- 轨迹提交不信任请求中的 `cookie_id`，而是使用服务端 session 绑定值
- 轨迹池会对 `cookie_id` 做净化，阻止路径逃逸

## 轨迹池

`slidex/_trajectory_pool.py` 负责：

- 持久化录制轨迹
- 随机选取历史轨迹
- 记录最近使用信息
- 将轨迹按 `cookie_id` 隔离存储

安全改动后：

- `cookie_id` 会被限制为字母数字和 `-_.`
- 持久化目录必须位于轨迹池基目录下

## 当前交付判断

从仓库层面看，当前版本满足：

- 全量自动化测试通过
- slider、OCR、artifact、manual fallback 的核心平台契约已落地
- provider 模式与远程控制关键安全边界已补齐
- automation-kit optional adapter 已验证无硬依赖与有依赖两条路径
- 文档契约与当前代码基本同步

仍建议在正式上线前补做：

- 目标站点真实验证码冒烟
- 部署环境依赖安装验证
- 人工兜底页面的浏览器级验收
