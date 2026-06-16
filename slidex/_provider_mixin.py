"""Provider-aware SliderSolver integration layer"""

from typing import Optional, Tuple, Dict
from loguru import logger
from playwright.async_api import Page

from slidex.providers import ProviderRegistry, CaptchaProvider
from slidex.providers.builtin import *  # auto-register built-in providers
from slidex._trajectory import generate_trajectory, trajectory_to_points
from slidex._trajectory_pool import SliderTrajectoryPool


class ProviderSolverMixin:
    """
    Provider 集成 Mixin，为 SliderSolver 添加 provider 支持。

    使用方式：
      solver = SliderSolver(provider="auto")  # 自动检测
      solver = SliderSolver(provider="geetest")  # 手动指定
      solver = SliderSolver(selectors={...})  # 向后兼容：使用 legacy 模式
    """

    def __init__(self, provider: Optional[str] = None, **kwargs):
        self._provider_name = provider
        self._provider: Optional[CaptchaProvider] = None
        self._use_provider_mode = provider is not None
        super().__init__(**kwargs)

    async def _detect_and_init_provider(self, page: Page) -> bool:
        """检测并初始化 provider"""
        if not self._use_provider_mode:
            return False

        if self._provider_name == "auto":
            # 自动检测
            self._provider = await ProviderRegistry.auto_detect(page)
            if not self._provider:
                logger.warning(f"[{self.pure_user_id}] auto-detect failed, falling back to legacy mode")
                return False
            logger.info(f"[{self.pure_user_id}] detected provider: {self._provider.name}")
        else:
            # 手动指定
            try:
                self._provider = ProviderRegistry.get(self._provider_name)
                logger.info(f"[{self.pure_user_id}] using provider: {self._provider.name}")
            except ValueError as e:
                logger.error(f"[{self.pure_user_id}] {e}")
                return False

        # 调用 provider 初始化钩子
        try:
            await self._provider.on_init(page)
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] provider on_init failed: {e}, falling back to legacy")
            self._provider = None  # 清空 provider，回退到 legacy
            return False  # 初始化失败，回退到 legacy 模式
        return True

    async def _solve_with_provider(self, page: Page) -> Tuple[bool, Optional[Dict]]:
        """使用 provider 求解"""
        if not self._provider:
            raise RuntimeError("Provider not initialized")

        try:
            # 1. 定位元素
            elements = await self._provider.locate_elements(page)
            metadata_str = f", metadata={elements.metadata}" if elements.metadata else ""
            logger.debug(f"[{self.pure_user_id}] elements located, track_width={elements.track_width_px}px{metadata_str}")

            # 2. 提取图像
            bg_bytes, piece_bytes = await self._provider.extract_images(page, elements)
            logger.debug(f"[{self.pure_user_id}] images extracted, bg={len(bg_bytes)} bytes, piece={len(piece_bytes)} bytes")

            # 3. 图像匹配
            try:
                gap_x, confidence = await self._provider.find_gap(bg_bytes, piece_bytes)
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] find_gap error: {e}")
                return False, None

            if gap_x is None:
                logger.warning(f"[{self.pure_user_id}] gap not found")
                return False, None

            logger.info(f"[{self.pure_user_id}] gap detected at x={gap_x}px, confidence={confidence:.2f}")

            # 4. 生成轨迹（优先使用录制轨迹）
            try:
                trajectory_dir = self._config.get_trajectory_dir()
                trajectory_pool = SliderTrajectoryPool(trajectory_dir)
                recorded_traj = trajectory_pool.get_random_trajectory()
            except Exception as e:
                logger.warning(f"[{self.pure_user_id}] trajectory pool error: {e}, using synthetic")
                recorded_traj = None

            if recorded_traj:
                logger.debug(f"[{self.pure_user_id}] using recorded trajectory")
                # 录制的轨迹已经是绝对坐标，包含 (x, y, timestamp)
                points = recorded_traj["points"]
            else:
                logger.debug(f"[{self.pure_user_id}] generating synthetic trajectory")
                trajectory = generate_trajectory(
                    distance=gap_x,
                    attempt=1,
                )
                # 生成的轨迹是相对坐标，需要转换为绝对坐标
                # 但 provider 模式下不知道起始坐标，直接使用相对坐标 (0, 0 起点)
                points = trajectory_to_points(trajectory, start_x=0, start_y=0)

            try:
                # 5. 执行滑动
                await self._provider.perform_slide(page, elements, gap_x, points)
                logger.debug(f"[{self.pure_user_id}] slide performed")

                # 6. 等待结果
                result = await self._provider.get_result(page, timeout_ms=5000)
            finally:
                await self._provider.cleanup_after_result(page)
            if result.success:
                logger.success(f"[{self.pure_user_id}] provider solve success!")
            else:
                logger.warning(f"[{self.pure_user_id}] provider solve failed: {result.error}")

            return result.success, result.cookies

        except Exception as e:
            logger.error(f"[{self.pure_user_id}] provider solve error: {e}", exc_info=True)
            return False, None

    @classmethod
    def register_provider(cls, name: str, provider_class, detection_priority: int = 100):
        """注册自定义 provider"""
        ProviderRegistry.register(name, provider_class, detection_priority)

    @classmethod
    def list_providers(cls):
        """列出所有已注册 provider"""
        return ProviderRegistry.list_providers()


__all__ = ["ProviderSolverMixin"]
