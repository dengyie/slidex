"""Provider 抽象基类和注册表"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Type
from playwright.async_api import Page, ElementHandle, Response
from loguru import logger
import asyncio
import threading

from slidex.vision.models import (
    ChallengeType,
    ProviderDecision,
    ProviderManifest,
    VisionContext,
)


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
    manifest = ProviderManifest(
        name="base",
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )

    def __init__(self):
        self._last_response: Optional[Response] = None
        self._result: Optional[bool] = None
        self._result_event: Optional[asyncio.Event] = None
        self._response_handler = None
        self._response_tasks: Set[asyncio.Task] = set()
        self._challenge_scope = None

    def bind_response_listener(self, page: Page) -> None:
        """注册响应监听：校验协程入集合，完成时丢弃，cleanup 可取消。"""
        self._result = None
        self._result_event = asyncio.Event()

        async def _handle(response: Response) -> None:
            try:
                result = await self.validate_response(response)
                if result is not None:
                    self._result = result
                    if self._result_event is not None:
                        self._result_event.set()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"{self.name} response handler error: {exc}")

        def response_handler(response: Response) -> None:
            try:
                task = asyncio.get_running_loop().create_task(_handle(response))
            except RuntimeError:
                return
            self._response_tasks.add(task)
            task.add_done_callback(self._response_tasks.discard)

        self._response_handler = response_handler
        page.on("response", response_handler)

    async def on_init(self, page: Page) -> None:
        """
        Provider 初始化钩子（在检测成功后调用一次）

        子类可覆盖此方法执行初始化操作：
        - 预热模型
        - 缓存检测结果
        - 建立连接

        默认实现：无操作
        """
        pass

    async def on_cleanup(self) -> None:
        """
        Provider 清理钩子（在 solver 关闭时调用）

        子类可覆盖此方法执行清理操作：
        - 关闭连接
        - 保存状态
        - 释放资源

        默认实现：无操作
        """
        pass

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

    async def find_gap(
        self,
        bg_bytes: bytes,
        piece_bytes: bytes
    ) -> Tuple[Optional[int], float]:
        """
        从图像中查找缺口位置（可覆盖此方法实现自定义算法）

        默认实现：OpenCV Canny 边缘检测 + 模板匹配
        子类可覆盖以使用：
        - 深度学习模型
        - OCR 识别
        - 其他图像处理算法

        Args:
            bg_bytes: 背景图字节（PNG/JPEG）
            piece_bytes: 拼图块字节（PNG/JPEG）

        Returns:
            (gap_x, confidence) — 缺口位置（像素），匹配置信度 0-1
        """
        from slidex._image_match import SliderImageMatcher
        return SliderImageMatcher.find_gap_with_confidence(bg_bytes, piece_bytes)

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
    async def validate_response(self, response: Response) -> Optional[bool]:
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
        等待验证结果（默认实现：轮询 self._result）

        子类只需在 perform_slide() 中注册响应监听器，
        通过 validate_response() 设置 self._result = True/False。
        如需自定义等待逻辑，可覆盖此方法。

        Args:
            page: Playwright page 对象
            timeout_ms: 超时时间（毫秒）

        Returns:
            SolveResult 包含 success, cookies, error
        """
        from slidex._cookies import select_cookies_for_url

        event = self._result_event
        if event is not None:
            try:
                await asyncio.wait_for(event.wait(), timeout=max(0.05, timeout_ms / 1000.0))
            except asyncio.TimeoutError:
                return SolveResult(
                    success=False,
                    cookies=None,
                    error=f"{self.name}: timeout waiting for result",
                )
        else:
            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < timeout_ms / 1000:
                if self._result is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                return SolveResult(
                    success=False,
                    cookies=None,
                    error=f"{self.name}: timeout waiting for result",
                )

        cookies = await page.context.cookies()
        page_url = getattr(page, "url", "")
        if not isinstance(page_url, str):
            page_url = ""
        return SolveResult(
            success=bool(self._result),
            cookies=select_cookies_for_url(cookies, page_url),
        )

    async def cleanup_after_result(self, page: Page) -> None:
        """卸掉响应监听并取消未完成的 body 读取任务。"""
        if self._response_handler:
            try:
                page.remove_listener("response", self._response_handler)
            except Exception:
                pass
            self._response_handler = None
        for task in list(self._response_tasks):
            if not task.done():
                task.cancel()
        self._response_tasks.clear()


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
    def get_manifest(cls, name: str) -> ProviderManifest:
        """获取 provider manifest。"""
        provider = cls.get(name)
        manifest = getattr(provider, "manifest", None)
        if manifest is None:
            return CaptchaProvider.manifest
        return manifest

    @classmethod
    def list_manifests(cls) -> List[ProviderManifest]:
        """列出所有 provider manifest。"""
        return [cls.get_manifest(name) for name in cls.list_providers()]

    @classmethod
    def find_providers(
        cls,
        *,
        challenge_type: ChallengeType,
        context: VisionContext,
    ) -> List[str]:
        """按 challenge type 和执行上下文过滤 provider。"""
        matched = []
        for name in cls.list_providers():
            manifest = cls.get_manifest(name)
            if manifest.supports(challenge_type, context):
                matched.append(name)
        return matched

    @classmethod
    def build_decision(
        cls,
        *,
        challenge_type: ChallengeType,
        context: VisionContext,
        requested_provider: str,
        selected_provider: Optional[str],
        candidates: List[str],
        reason: str,
    ) -> ProviderDecision:
        """构造可写入 telemetry/artifact 的 provider 决策记录。"""
        return ProviderDecision(
            challenge_type=challenge_type,
            context=context,
            requested_provider=requested_provider,
            selected_provider=selected_provider,
            candidates=list(candidates),
            reason=reason,
        )

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
    "ProviderManifest",
    "ProviderDecision",
]
