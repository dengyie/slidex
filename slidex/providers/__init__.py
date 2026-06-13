"""Provider 抽象基类和注册表"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Type
from playwright.async_api import Page, ElementHandle, Response
from loguru import logger
import threading


@dataclass
class ProviderElements:
    """Provider 定位的 DOM 元素"""
    slider_btn: ElementHandle
    slider_track: ElementHandle
    bg_img: Optional[ElementHandle]
    piece_img: Optional[ElementHandle]
    track_width_px: int
    metadata: Optional[Dict] = None  # 供 provider 存储额外信息

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SolveResult:
    """求解结果"""
    success: bool
    cookies: Optional[Dict]
    error: Optional[str] = None
    need_retry: bool = False
    confidence: float = 0.0  # 匹配置信度


class CaptchaProvider(ABC):
    """验证码供应商抽象基类"""

    name: str = "base"
    description: str = "Base CAPTCHA Provider"

    def __init__(self):
        self._last_response: Optional[Response] = None

    @abstractmethod
    async def detect(self, page: Page) -> bool:
        """
        检测当前页面是否是该供应商的验证码。

        实现建议：
        - 检查特定 DOM 元素（iframe src、class name）
        - 检查 JS 全局变量（window.GeeTest、window._nocaptcha）
        - 检查网络请求特征（URL 路径、Host）
        """
        pass

    @abstractmethod
    async def locate_elements(self, page: Page) -> ProviderElements:
        """
        定位关键 DOM 元素。

        返回 ProviderElements 包含：
        - slider_btn: 滑块按钮
        - slider_track: 滑动轨道
        - bg_img: 背景图（可选，某些供应商用 canvas）
        - piece_img: 拼图块（可选）
        - track_width_px: 轨道像素宽度
        """
        pass

    @abstractmethod
    async def extract_images(self, page: Page, elements: ProviderElements) -> Tuple[bytes, bytes]:
        """
        提取背景图和拼图块的图像数据。

        Returns:
            (bg_bytes, piece_bytes) — PNG/JPEG 格式

        实现建议：
        - img 标签：await element.screenshot()
        - canvas：await page.evaluate("canvas => canvas.toDataURL()", canvas)
        """
        pass

    @abstractmethod
    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> None:
        """
        执行滑动操作。

        Args:
            gap_x: 缺口 X 坐标
            trajectory: [(x, y, timestamp_ms), ...]
        """
        pass

    @abstractmethod
    def validate_response(self, response: Response) -> Optional[bool]:
        """
        从网络响应判断验证结果。

        Returns:
            True: 成功
            False: 失败
            None: 非验证结果响应，忽略
        """
        pass

    async def get_result(
        self,
        page: Page,
        timeout_ms: int = 5000,
    ) -> SolveResult:
        """
        等待验证结果（子类可覆盖此方法实现自定义等待逻辑）

        默认实现：等待 validate_response 返回 True/False
        """
        return SolveResult(
            success=False,
            cookies=None,
            error="get_result not implemented",
        )


class ProviderRegistry:
    """Provider 注册表"""

    _providers: Dict[str, Type[CaptchaProvider]] = {}
    _detection_order: List[Tuple[int, str]] = []  # 自动检测顺序：(priority, name)
    _lock = threading.Lock()

    @classmethod
    def register(
        cls,
        name: str,
        provider_class: Type[CaptchaProvider],
        detection_priority: int = 100,
    ):
        """
        注册 Provider。

        Args:
            name: provider 名称（如 "aliyun-nocaptcha"）
            provider_class: Provider 类
            detection_priority: 检测优先级（越小越优先）
        """
        with cls._lock:
            cls._providers[name] = provider_class
            cls._detection_order.append((detection_priority, name))
            cls._detection_order.sort()
            logger.info(f"Registered provider: {name} (priority={detection_priority})")

    @classmethod
    def get(cls, name: str) -> CaptchaProvider:
        """获取 Provider 实例"""
        if name not in cls._providers:
            available = ", ".join(cls._providers.keys())
            raise ValueError(f"Unknown provider '{name}'. Available: {available}")
        return cls._providers[name]()

    @classmethod
    async def auto_detect(cls, page: Page) -> Optional[CaptchaProvider]:
        """
        自动检测当前页面使用的验证码供应商。

        按 detection_priority 顺序遍历所有已注册 provider。
        """
        for _, name in cls._detection_order:
            provider = cls._providers[name]()
            try:
                if await provider.detect(page):
                    logger.info(f"Auto-detected provider: {name}")
                    return provider
            except Exception as e:
                logger.warning(f"Provider {name} detect() failed: {e}")

        logger.warning("No provider detected")
        return None

    @classmethod
    def list_providers(cls) -> List[str]:
        """列出所有已注册 provider"""
        return list(cls._providers.keys())


__all__ = [
    "CaptchaProvider",
    "ProviderElements",
    "SolveResult",
    "ProviderRegistry",
]
