"""
Slidex Provider 架构设计

## 核心概念

Provider = 验证码供应商适配器，封装：
  - 检测逻辑（识别当前页面是否是该供应商）
  - 元素定位（DOM 选择器）
  - 求解流程（图像匹配 → 轨迹 → 验证）
  - 结果解析（成功/失败/需重试）

## 接口设计

```python
class CaptchaProvider(ABC):
    name: str  # "aliyun-nocaptcha", "geetest-v3", "shumei"
    
    @abstractmethod
    async def detect(self, page: Page) -> bool:
        \"\"\"检测当前页面是否是该供应商的验证码\"\"\"
        
    @abstractmethod
    async def locate_elements(self, page: Page) -> ProviderElements:
        \"\"\"定位关键 DOM 元素\"\"\"
        
    @abstractmethod
    async def extract_images(self, elements: ProviderElements) -> Tuple[bytes, bytes]:
        \"\"\"提取背景图和拼图块\"\"\"
        
    @abstractmethod
    async def solve(
        self, 
        page: Page,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> SolveResult:
        \"\"\"执行滑动并解析结果\"\"\"
        
    @abstractmethod
    def validate_response(self, response) -> Optional[bool]:
        \"\"\"从网络响应判断成功/失败\"\"\"
```

## 数据类

```python
@dataclass
class ProviderElements:
    slider_btn: ElementHandle
    slider_track: ElementHandle
    bg_img: ElementHandle
    piece_img: Optional[ElementHandle]
    track_width_px: int

@dataclass
class SolveResult:
    success: bool
    cookies: Optional[Dict]
    error: Optional[str]
    need_retry: bool = False
```

## ProviderRegistry

```python
class ProviderRegistry:
    _providers: Dict[str, Type[CaptchaProvider]] = {}
    
    @classmethod
    def register(cls, name: str, provider_class: Type[CaptchaProvider]):
        cls._providers[name] = provider_class
        
    @classmethod
    def get(cls, name: str) -> CaptchaProvider:
        return cls._providers[name]()
        
    @classmethod
    async def auto_detect(cls, page: Page) -> Optional[CaptchaProvider]:
        \"\"\"遍历所有 provider，返回第一个匹配的\"\"\"
        for provider_class in cls._providers.values():
            provider = provider_class()
            if await provider.detect(page):
                return provider
        return None
```

## SliderSolver 集成

```python
# 方式 1：自动检测
solver = SliderSolver(cookie_id="user")
success, cookies = await solver.solve(url, provider="auto")  # 自动检测供应商

# 方式 2：手动指定
solver = SliderSolver(cookie_id="user", provider="geetest-v3")
success, cookies = await solver.solve(url)

# 方式 3：自定义 provider
class MyProvider(CaptchaProvider):
    ...

SliderSolver.register_provider("my-custom", MyProvider)
solver = SliderSolver(provider="my-custom")
```

## 内置 Provider

1. AliyunNoCaptchaProvider（当前默认）
2. GeeTestV3Provider（极验 3.0）
3. GeeTestV4Provider（极验 4.0）
4. ShumeiProvider（数美）
5. DingxiangProvider（顶象）

## 扩展点

- 自定义检测逻辑（JS 注入、DOM 特征、网络请求）
- 自定义图像处理（OCR、深度学习模型）
- 自定义轨迹算法（强化学习、真人数据拟合）
- Provider 插件热加载
"""
