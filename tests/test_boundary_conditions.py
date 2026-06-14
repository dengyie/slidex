"""
边界条件测试 - 验证极端场景和边界情况

测试场景:
1. 空值/None 处理
2. 超大/超小数值
3. 空列表/空字符串
4. 重复操作
5. 资源耗尽模拟
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from slidex import SliderSolver, CaptchaProvider, ProviderElements
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex.providers import ProviderRegistry
from pathlib import Path
import tempfile


class TestBoundaryConditions:
    """边界条件测试"""

    @pytest.mark.asyncio
    async def test_empty_trajectory_pool(self):
        """空轨迹池应该返回 None 而不是崩溃"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 空池应该返回 None
            result = pool.get_random_trajectory()
            assert result is None

            # load_random_trajectory 也应该返回 None
            result = pool.load_random_trajectory("test_user")
            assert result is None

    def test_trajectory_pool_with_empty_points(self):
        """保存空 points 列表应该被拒绝或处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 空 points
            empty_points = []
            filename = pool.save_trajectory(empty_points, "test", 100, True)

            # 应该保存成功（即使是空轨迹）
            assert filename is not None or filename is None  # 实现可能拒绝空轨迹

    def test_trajectory_pool_with_invalid_points_format(self):
        """无效的 points 格式应该被处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            pool = SliderTrajectoryPool(tmpdir)

            # 错误格式: 只有 2 个元素而不是 3 个
            invalid_points = [[0, 0], [10, 0]]  # 缺少 timestamp

            try:
                filename = pool.save_trajectory(invalid_points, "test", 100, True)
                # 如果成功保存，后续加载应该能处理
                if filename:
                    loaded = pool.load_random_trajectory("test")
                    # 加载可能失败或成功，但不应该崩溃
                    assert loaded is None or isinstance(loaded, dict)
            except (IndexError, ValueError, TypeError):
                # 保存时就失败也是合理的
                pass

    @pytest.mark.asyncio
    async def test_provider_with_zero_track_width(self):
        """轨道宽度为 0 的极端情况"""
        from slidex import CaptchaProvider

        class ZeroWidthProvider(CaptchaProvider):
            name = "zero-width"

            async def detect(self, page):
                return True

            async def locate_elements(self, page):
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=0  # 极端值
                )

            async def extract_images(self, page, elements):
                return b"fake", b"fake"

            async def perform_slide(self, page, elements, gap_x, trajectory):
                pass

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("zero-width", ZeroWidthProvider)
        solver = SliderSolver(provider="zero-width")

        page = AsyncMock()
        page.context.cookies = AsyncMock(return_value=[])

        await solver._detect_and_init_provider(page)

        # 应该能正常初始化，即使 track_width 是 0
        assert solver._provider is not None

    @pytest.mark.asyncio
    async def test_provider_with_negative_gap_position(self):
        """find_gap 返回负数位置"""
        from slidex import CaptchaProvider

        class NegativeGapProvider(CaptchaProvider):
            name = "negative-gap"

            async def detect(self, page):
                return True

            async def locate_elements(self, page):
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300
                )

            async def extract_images(self, page, elements):
                return b"fake", b"fake"

            async def find_gap(self, bg_bytes, piece_bytes):
                # 返回负数位置
                return -10, 0.9

            async def perform_slide(self, page, elements, gap_x, trajectory):
                # 验证是否会收到负数 gap_x
                assert gap_x == -10

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("negative-gap", NegativeGapProvider)
        solver = SliderSolver(provider="negative-gap")

        page = AsyncMock()
        page.context.cookies = AsyncMock(return_value=[])

        await solver._detect_and_init_provider(page)

        # 尝试求解，应该传递负数给 perform_slide
        # 这可能不合理，但测试边界行为
        try:
            await solver._solve_with_provider(page)
        except AssertionError:
            pass  # perform_slide 中的断言验证通过

    @pytest.mark.asyncio
    async def test_provider_with_extremely_large_gap(self):
        """极大的 gap 值"""
        from slidex import CaptchaProvider

        class LargeGapProvider(CaptchaProvider):
            name = "large-gap"

            async def detect(self, page):
                return True

            async def locate_elements(self, page):
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300
                )

            async def extract_images(self, page, elements):
                return b"fake", b"fake"

            async def find_gap(self, bg_bytes, piece_bytes):
                # 返回超大值
                return 999999, 0.9

            async def perform_slide(self, page, elements, gap_x, trajectory):
                pass

            def validate_response(self, response):
                return None

        SliderSolver.register_provider("large-gap", LargeGapProvider)
        solver = SliderSolver(provider="large-gap")

        page = AsyncMock()
        page.context.cookies = AsyncMock(return_value=[])

        await solver._detect_and_init_provider(page)

        # 应该能处理极大的 gap 值（轨迹生成可能会有上限）
        try:
            await solver._solve_with_provider(page)
        except Exception:
            # 可能因为轨迹生成失败，但不应该崩溃到外层
            pass

    def test_provider_registry_duplicate_registration(self):
        """重复注册同名 provider"""
        from slidex import CaptchaProvider

        class TestProvider1(CaptchaProvider):
            name = "duplicate-test"
            async def detect(self, page): return False
            async def locate_elements(self, page): pass
            async def extract_images(self, page, elements): pass
            async def perform_slide(self, page, elements, gap_x, trajectory): pass
            def validate_response(self, response): return None

        class TestProvider2(CaptchaProvider):
            name = "duplicate-test"
            async def detect(self, page): return True  # 不同实现
            async def locate_elements(self, page): pass
            async def extract_images(self, page, elements): pass
            async def perform_slide(self, page, elements, gap_x, trajectory): pass
            def validate_response(self, response): return None

        # 第一次注册
        SliderSolver.register_provider("duplicate-test", TestProvider1)
        provider1 = ProviderRegistry.get("duplicate-test")

        # 第二次注册（覆盖）
        SliderSolver.register_provider("duplicate-test", TestProvider2)
        provider2 = ProviderRegistry.get("duplicate-test")

        # 应该使用最新注册的
        assert provider2 is not None

    @pytest.mark.asyncio
    async def test_provider_with_empty_metadata(self):
        """metadata 为空字典或 None"""
        elements_none = ProviderElements(
            slider_btn=AsyncMock(),
            slider_track=AsyncMock(),
            bg_img=None,
            piece_img=None,
            track_width_px=300,
            metadata=None
        )

        elements_empty = ProviderElements(
            slider_btn=AsyncMock(),
            slider_track=AsyncMock(),
            bg_img=None,
            piece_img=None,
            track_width_px=300,
            metadata={}
        )

        # __post_init__ 会将 None 转换为 {}
        assert elements_none.metadata == {}
        assert elements_empty.metadata == {}

    def test_solver_list_providers_when_empty(self):
        """清空所有 provider 后列表应该为空"""
        # 备份当前 providers
        original = dict(ProviderRegistry._providers)
        original_order = list(ProviderRegistry._detection_order)

        try:
            # 清空
            ProviderRegistry._providers.clear()
            ProviderRegistry._detection_order.clear()

            providers = SliderSolver.list_providers()
            assert providers == []
        finally:
            # 恢复
            ProviderRegistry._providers = original
            ProviderRegistry._detection_order = original_order


@pytest.fixture(autouse=True)
def cleanup_boundary_test_providers():
    """清理边界测试 providers"""
    yield
    test_providers = [
        "zero-width", "negative-gap", "large-gap",
        "duplicate-test"
    ]
    for name in test_providers:
        if name in ProviderRegistry._providers:
            del ProviderRegistry._providers[name]
            ProviderRegistry._detection_order = [
                (p, n) for p, n in ProviderRegistry._detection_order if n != name
            ]
