# Provider 开发指南

## 什么是 Provider？

Provider 是 Slidex 视觉平台里 `slider_captcha` 类挑战的供应商适配器，封装了特定供应商的：
- **检测逻辑** — 识别当前页面使用的验证码类型
- **元素定位** — 找到滑块、轨道、图像等 DOM 元素
- **图像提取** — 从 img/canvas 提取背景图和拼图块
- **滑动执行** — 模拟鼠标轨迹完成验证
- **结果解析** — 判断验证成功/失败

## 为什么需要 Provider？

不同供应商的验证码差异很大：
- **DOM 结构**：Aliyun 用 img，GeeTest 用 canvas，Shumei 用自定义元素
- **验证流程**：有的需要点击启动，有的自动加载
- **结果判定**：响应 URL、JSON 字段、状态码都不同

Provider 抽象层让你可以：
- **开箱即用**：内置 Aliyun、GeeTest 适配器
- **自动检测**：`provider="auto"` 自动识别当前网站
- **快速扩展**：10 分钟实现一个新供应商适配器

对于 OCR、截图识别、人工兜底等非 slider 场景，不应实现为 `CaptchaProvider`；这些能力属于 `slidex.ocr` 或 `slidex.vision.manual` 层。

---

## 快速开始

### 使用内置 Provider

```python
from slidex import SliderSolver

# 自动检测（推荐）
solver = SliderSolver(cookie_id="user", provider="auto")
success, cookies = await solver.solve("https://...")

# 手动指定
solver = SliderSolver(provider="geetest")
success, cookies = await solver.solve("https://...")

# Legacy 模式（向后兼容）
solver = SliderSolver(selectors={"slider_btn": ".my-btn"})
```

### 查看已注册 Provider

```python
from slidex import SliderSolver

print(SliderSolver.list_providers())
# ['aliyun-nocaptcha', 'geetest']
```

查看 manifest：

```python
from slidex import ProviderRegistry
from slidex.vision import ChallengeType, VisionContext

print(ProviderRegistry.list_manifests())
print(
    ProviderRegistry.find_providers(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
    )
)
```

---

## 实现自定义 Provider

### 第 1 步：继承 CaptchaProvider

```python
from slidex import CaptchaProvider, ProviderElements, SolveResult
from playwright.async_api import Page, Response
from typing import List, Tuple, Optional

class MyCustomProvider(CaptchaProvider):
    name = "my-custom"
    description = "My custom CAPTCHA provider"
    
    def __init__(self):
        super().__init__()
        self._result: Optional[bool] = None
```

### 第 2 步：实现 detect()

检测当前页面是否是该供应商的验证码。

```python
async def detect(self, page: Page) -> bool:
    """
    返回 True 表示当前页面是该供应商。
    
    检测方法（按优先级）：
    1. DOM 特征（class name、id、iframe src）
    2. JS 全局变量（window.MyProvider）
    3. 网络请求特征（需要在 page 初始化时监听）
    """
    try:
        # 方法 1: DOM 特征
        el = await page.query_selector(".my-captcha-wrapper")
        if el:
            return True
        
        # 方法 2: JS 全局变量
        has_js = await page.evaluate("() => window.MyCaptcha !== undefined")
        if has_js:
            return True
        
        return False
    except Exception as e:
        logger.debug(f"MyCustomProvider.detect() error: {e}")
        return False
```

### 第 3 步：实现 locate_elements()

定位关键 DOM 元素。

```python
async def locate_elements(self, page: Page) -> ProviderElements:
    """
    返回 ProviderElements，包含：
    - slider_btn: 滑块按钮（必须）
    - slider_track: 滑动轨道（必须）
    - bg_img: 背景图（可选，canvas 类型可以传 canvas 元素）
    - piece_img: 拼图块（可选）
    - track_width_px: 轨道像素宽度（必须）
    - metadata: 额外数据（可选，dict）
    """
    slider_btn = await page.wait_for_selector(".my-slider-btn", timeout=10000)
    if not slider_btn:
        raise RuntimeError("Slider button not found")
    
    slider_track = await page.query_selector(".my-slider-track")
    if not slider_track:
        raise RuntimeError("Slider track not found")
    
    bg_canvas = await page.query_selector(".my-bg-canvas")
    piece_canvas = await page.query_selector(".my-piece-canvas")
    
    track_box = await slider_track.bounding_box()
    track_width_px = int(track_box["width"]) if track_box else 300
    
    return ProviderElements(
        slider_btn=slider_btn,
        slider_track=slider_track,
        bg_img=bg_canvas,
        piece_img=piece_canvas,
        track_width_px=track_width_px,
        metadata={"version": "v2"},  # 可选
    )
```

### 第 4 步：实现 extract_images()

提取背景图和拼图块的图像数据。

```python
async def extract_images(
    self, page: Page, elements: ProviderElements
) -> Tuple[bytes, bytes]:
    """
    返回 (bg_bytes, piece_bytes)，格式为 PNG/JPEG。
    
    提取方法：
    - img 标签：await element.screenshot() 或解析 src（data URL / http URL）
    - canvas：await page.evaluate("canvas => canvas.toDataURL()", canvas)
    """
    import base64
    
    # 背景图（canvas）
    if elements.bg_img:
        bg_data_url = await page.evaluate(
            "(canvas) => canvas.toDataURL('image/png')", 
            elements.bg_img
        )
        bg_bytes = base64.b64decode(bg_data_url.split(",", 1)[1])
    else:
        raise RuntimeError("Background image not found")
    
    # 拼图块（canvas）
    if elements.piece_img:
        piece_data_url = await page.evaluate(
            "(canvas) => canvas.toDataURL('image/png')", 
            elements.piece_img
        )
        piece_bytes = base64.b64decode(piece_data_url.split(",", 1)[1])
    else:
        raise RuntimeError("Piece image not found")
    
    return bg_bytes, piece_bytes
```

### 第 5 步：自定义图像匹配（可选）

如果需要使用自定义图像匹配算法（如深度学习模型、OCR），可覆盖 `find_gap()` 方法：

```python
async def find_gap(
    self,
    bg_bytes: bytes,
    piece_bytes: bytes
) -> Tuple[Optional[int], float]:
    """
    自定义图像匹配算法
    
    默认：OpenCV Canny 边缘检测 + 模板匹配
    可覆盖以使用：ML 模型、OCR、其他算法
    
    Returns:
        (gap_x, confidence) — 缺口位置（像素），置信度 0-1
    """
    # 使用自定义算法
    gap_x = await self.ml_model.predict(bg_bytes, piece_bytes)
    confidence = 0.95
    return gap_x, confidence
```

**默认行为**: 调用 `SliderImageMatcher.find_gap_with_confidence(bg_bytes, piece_bytes)` 使用 OpenCV。

### 第 6 步：实现 perform_slide()

执行滑动操作。

```python
import asyncio

async def perform_slide(
    self,
    page: Page,
    elements: ProviderElements,
    gap_x: int,
    trajectory: List[Tuple[int, int, int]],
) -> None:
    """
    执行滑动。
    
    Args:
        gap_x: 缺口 X 坐标（像素）
        trajectory: [(x, y, timestamp_ms), ...] 相对坐标
    """
    # 注册响应监听
    self._result = None
    
    def response_handler(response: Response):
        async def _handle():
            result = await self.validate_response(response)
            if result is not None:
                self._result = result

        asyncio.create_task(_handle())

    self._response_handler = response_handler
    page.on("response", response_handler)

    # 获取滑块中心坐标
    btn_box = await elements.slider_btn.bounding_box()
    if not btn_box:
        raise RuntimeError("Cannot get slider button bounding box")

    start_x = btn_box["x"] + btn_box["width"] / 2
    start_y = btn_box["y"] + btn_box["height"] / 2

    # 按下
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()
    await page.wait_for_timeout(100)

    # 执行轨迹
    for x, y, ts_ms in trajectory:
        await page.mouse.move(start_x + x, start_y + y)
        await page.wait_for_timeout(15)

    # 松开
    await page.wait_for_timeout(100)
    await page.mouse.up()
```

为避免结果响应在 `perform_slide()` 返回后才到达，监听器应保留到 `get_result()` 完成。推荐同时实现一个清理钩子：

```python
async def cleanup_after_result(self, page: Page) -> None:
    if self._response_handler:
        page.remove_listener("response", self._response_handler)
        self._response_handler = None
```

### 第 7 步：实现 validate_response()

从网络响应判断验证结果。

```python
async def validate_response(self, response: Response) -> Optional[bool]:
    """
    返回：
    - True: 验证成功
    - False: 验证失败
    - None: 非验证结果响应，忽略
    """
    url = response.url
    
    # 只关心验证结果接口
    if "/my/verify" not in url:
        return None
    
    try:
        body = await response.body()
        text = body.decode("utf-8", errors="ignore")
        data = json.loads(text)
        
        if isinstance(data, dict):
            # 根据供应商返回格式判断
            if data.get("status") == "success":
                return True
            if data.get("status") == "fail":
                return False
    
    except Exception as e:
        logger.debug(f"validate_response error: {e}")
    
    return None
```

### 第 8 步：实现 get_result()

等待验证结果（可选，覆盖默认实现）。

```python
async def get_result(self, page: Page, timeout_ms: int = 5000) -> SolveResult:
    """
    等待验证结果。
    
    默认实现：轮询 self._result（由 validate_response 设置）
    """
    import asyncio
    
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout_ms / 1000:
        if self._result is not None:
            cookies = await page.context.cookies()
            return SolveResult(
                success=self._result,
                cookies={c["name"]: c["value"] for c in cookies},
            )
        await asyncio.sleep(0.1)
    
    return SolveResult(
        success=False,
        cookies=None,
        error="Timeout waiting for result",
    )
```

### 第 9 步：注册 Provider

```python
from slidex import SliderSolver

SliderSolver.register_provider(
    "my-custom",
    MyCustomProvider,
    detection_priority=50,  # 数字越小，自动检测优先级越高
)

# 使用
solver = SliderSolver(provider="my-custom")
success, cookies = await solver.solve("https://...")
```

### 第 10 步：声明 Provider Manifest

每个 provider 都应该声明自己的 manifest，用于平台层的能力过滤与决策记录：

```python
from slidex.vision import ChallengeType, ProviderManifest, VisionContext

class MyCustomProvider(CaptchaProvider):
    name = "my-custom"
    manifest = ProviderManifest(
        name="my-custom",
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )
```

---

## 生命周期钩子（可选）

Provider 提供两个生命周期钩子用于初始化和清理：

### `on_init(page)` — 初始化钩子

在 provider 被检测并选中后调用一次。用于：
- 预热机器学习模型
- 缓存检测结果
- 建立连接

```python
class MyProvider(CaptchaProvider):
    async def on_init(self, page: Page):
        """Provider 初始化"""
        # 预热模型
        self.model = load_ml_model()
        logger.info(f"{self.name} model loaded")
```

### `on_cleanup()` — 清理钩子

在 `solver.close()` 时调用。用于：
- 关闭连接
- 保存状态
- 释放资源

```python
class MyProvider(CaptchaProvider):
    async def on_cleanup(self):
        """Provider 清理"""
        if hasattr(self, 'model'):
            self.model.close()
        logger.info(f"{self.name} cleaned up")
```

**默认行为**: 两个钩子的默认实现都是无操作（pass），子类可按需覆盖。

---

## Provider Registry 注意事项

### 单例行为

`ProviderRegistry` 是进程级全局单例。所有 `SliderSolver` 实例共享同一注册表：

```python
# 进程 A 中
SliderSolver.register_provider("custom", MyProvider)

# 进程 B 中（同一 Python 进程）
solver = SliderSolver(provider="custom")  # 可以访问进程 A 注册的 provider
```

**适用场景**：
- 单租户应用（大多数场景）
- Worker 进程各自独立
- 插件式架构（所有用户共享同一组 provider）

**不适用场景**：
- 多租户 SaaS（租户 A 不应看到租户 B 的自定义 provider）
- 需要运行时动态隔离

**未来扩展**：如需租户隔离，可传入自定义 registry 实例：
```python
my_registry = ProviderRegistry()
solver = SliderSolver(provider_registry=my_registry)  # 未来特性（当前未实现）
```

**线程安全**：`ProviderRegistry.register()` 使用 `threading.Lock()` 保护并发注册，可安全在多线程环境中调用。

---

## 主流供应商配置示例

### Aliyun NoCaptcha（内置）

```python
solver = SliderSolver(provider="aliyun-nocaptcha")
```

DOM 特征：
- `#nc_1_wrapper`
- iframe src 包含 `aliyuncs.com`

### GeeTest 极验（内置）

```python
solver = SliderSolver(provider="geetest")
```

DOM 特征：
- `.geetest_panel`
- `window.initGeetest` 或 `window.initGeetest4`

### Shumei 数美（待实现）

DOM 特征：
- `.shumei_captcha`
- `window.smCaptcha`

### Dingxiang 顶象（待实现）

DOM 特征：
- `[class*=dx-captcha]`
- `window._dx`

---

## 调试技巧

### 1. 检查元素定位

```python
elements = await provider.locate_elements(page)
await elements.slider_btn.screenshot(path="slider_btn.png")
await elements.bg_img.screenshot(path="bg.png")
```

### 2. 查看网络请求

```python
page.on("response", lambda r: print(f"{r.status} {r.url}"))
```

### 3. 打印 Provider 检测结果

```python
from slidex import ProviderRegistry

provider = await ProviderRegistry.auto_detect(page)
print(f"Detected: {provider.name if provider else 'None'}")
```

---

## 最佳实践

1. **检测逻辑要精准**：避免误判，优先检查供应商特有的 DOM/JS 特征
2. **元素定位要健壮**：支持多个候选选择器，处理动态 class name
3. **图像提取要完整**：确保图像清晰、尺寸正确
4. **轨迹要自然**：集成 trajectory_pool，复用真人轨迹
5. **错误处理要友好**：返回清晰的错误信息，方便调试

---

## 贡献你的 Provider

欢迎提交 PR 贡献新的 Provider 适配器：

1. Fork 仓库
2. 在 `slidex/providers/` 下创建新文件（如 `shumei.py`）
3. 实现 Provider 类
4. 在 `slidex/providers/builtin.py` 注册
5. 添加测试到 `tests/test_providers.py`
6. 更新 `README.md` 和 `CHANGELOG.md`
7. 提交 PR

---

## 参考资料

- [automation-kit 平台开发总纲](https://github.com/dengyie/automation-kit/blob/main/docs/development.md) — 跨仓架构、能力协议与开发门禁
- [slidex/providers/__init__.py](../slidex/providers/__init__.py) — Provider 基类
- [slidex/providers/aliyun.py](../slidex/providers/aliyun.py) — Aliyun 参考实现
- [slidex/providers/geetest.py](../slidex/providers/geetest.py) — GeeTest 参考实现
