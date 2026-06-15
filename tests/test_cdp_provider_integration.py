"""
测试 CDP + Provider 组合 (Mock 模式，无需真实浏览器)

验证:
1. CDP 连接 + Provider 自动检测
2. CDP 连接 + 手动指定 Provider
3. CDP 模式下的 Provider 生命周期
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from slidex import SliderSolver, CaptchaProvider, ProviderElements
from slidex.providers import ProviderRegistry


class MockCDPProvider(CaptchaProvider):
    """用于 CDP 测试的 Mock Provider"""
    name = "mock-cdp"

    def __init__(self):
        super().__init__()
        self.init_called = False
        self.cleanup_called = False

    async def on_init(self, page):
        self.init_called = True

    async def on_cleanup(self):
        self.cleanup_called = True

    async def detect(self, page):
        return True

    async def locate_elements(self, page):
        return ProviderElements(
            slider_btn=AsyncMock(),
            slider_track=AsyncMock(),
            bg_img=None,
            piece_img=None,
            track_width_px=300,
            metadata={"cdp_mode": True}
        )

    async def extract_images(self, page, elements):
        return b"fake_bg", b"fake_piece"

    async def find_gap(self, bg_bytes, piece_bytes):
        return 100, 0.9

    async def perform_slide(self, page, elements, gap_x, trajectory):
        # 验证 CDP 模式下的元素操作
        assert elements.metadata["cdp_mode"] is True
        # Mock 模式下直接设置结果
        self._result = True

    def validate_response(self, response):
        return True


@pytest.mark.asyncio
async def test_cdp_with_auto_provider():
    """CDP 模式 + provider='auto'"""
    # 注册测试 provider
    SliderSolver.register_provider("mock-cdp", MockCDPProvider, detection_priority=1)

    solver = SliderSolver(
        cookie_id="cdp_test",
        provider="auto"
    )

    # Mock CDP 连接
    page = AsyncMock()
    page.context.cookies = AsyncMock(return_value=[
        {"name": "session", "value": "abc123"}
    ])

    # 检测并初始化 provider
    result = await solver._detect_and_init_provider(page)
    assert result is True
    assert solver._provider is not None
    assert solver._provider.name == "mock-cdp"

    # 验证 on_init 被调用
    assert solver._provider.init_called is True

    # 尝试求解
    success, cookies = await solver._solve_with_provider(page)

    # 验证结果
    assert success is True
    assert cookies == {"session": "abc123"}


@pytest.mark.asyncio
async def test_cdp_with_manual_provider():
    """CDP 模式 + 手动指定 provider"""
    SliderSolver.register_provider("mock-cdp", MockCDPProvider, detection_priority=1)

    solver = SliderSolver(
        cookie_id="cdp_manual",
        provider="mock-cdp"  # 手动指定
    )

    page = AsyncMock()
    page.context.cookies = AsyncMock(return_value=[])

    # 初始化（不是自动检测）
    result = await solver._detect_and_init_provider(page)
    assert result is True
    assert solver._provider.name == "mock-cdp"

    # 求解
    success, cookies = await solver._solve_with_provider(page)
    assert success is True


@pytest.mark.asyncio
async def test_cdp_provider_lifecycle():
    """CDP 模式下 Provider 生命周期钩子"""
    SliderSolver.register_provider("mock-cdp", MockCDPProvider, detection_priority=1)

    solver = SliderSolver(provider="mock-cdp")
    page = AsyncMock()

    # 初始化
    await solver._detect_and_init_provider(page)
    assert solver._provider.init_called is True

    # 清理
    await solver.close()
    assert solver._provider.cleanup_called is True


@pytest.mark.asyncio
async def test_cdp_with_provider_metadata():
    """CDP 模式下 Provider metadata 传递"""
    SliderSolver.register_provider("mock-cdp", MockCDPProvider, detection_priority=1)

    solver = SliderSolver(provider="mock-cdp")
    page = AsyncMock()
    page.context.cookies = AsyncMock(return_value=[])

    await solver._detect_and_init_provider(page)

    # 求解时 metadata 应该正确传递
    success, cookies = await solver._solve_with_provider(page)
    assert success is True

    # metadata 中应该包含 cdp_mode 标记
    # (在 perform_slide 中验证)


@pytest.mark.asyncio
async def test_cdp_fallback_to_legacy():
    """CDP 模式下 provider 失败应该回退到 legacy"""
    # 使用无效的 provider 名称
    solver = SliderSolver(
        provider="nonexistent-cdp",
        selectors={"slider_btn": ".fallback-btn"}
    )

    page = AsyncMock()

    # provider 初始化失败
    result = await solver._detect_and_init_provider(page)
    assert result is False
    assert solver._provider is None

    # 应该能回退到 legacy 模式
    # (在实际使用中会调用 legacy 的 _run_legacy_solve_loop)


@pytest.mark.asyncio
async def test_cdp_with_multiple_providers():
    """CDP 模式下多个 provider 的优先级"""
    # 注册两个 provider，优先级不同
    class HighPriorityProvider(CaptchaProvider):
        name = "high-priority"
        async def detect(self, page): return True
        async def locate_elements(self, page): pass
        async def extract_images(self, page, elements): pass
        async def perform_slide(self, page, elements, gap_x, trajectory): pass
        def validate_response(self, response): return None

    class LowPriorityProvider(CaptchaProvider):
        name = "low-priority"
        async def detect(self, page): return True
        async def locate_elements(self, page): pass
        async def extract_images(self, page, elements): pass
        async def perform_slide(self, page, elements, gap_x, trajectory): pass
        def validate_response(self, response): return None

    SliderSolver.register_provider("high-priority", HighPriorityProvider, detection_priority=5)
    SliderSolver.register_provider("low-priority", LowPriorityProvider, detection_priority=10)

    solver = SliderSolver(provider="auto")
    page = AsyncMock()

    # 应该选择优先级高的
    result = await solver._detect_and_init_provider(page)
    assert result is True
    assert solver._provider.name == "high-priority"


@pytest.fixture(autouse=True)
def cleanup_cdp_test_providers():
    """清理 CDP 测试 providers"""
    yield
    test_providers = ["mock-cdp", "high-priority", "low-priority"]
    for name in test_providers:
        if name in ProviderRegistry._providers:
            del ProviderRegistry._providers[name]
            ProviderRegistry._detection_order = [
                (p, n) for p, n in ProviderRegistry._detection_order if n != name
            ]
