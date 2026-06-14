"""
验证 PROVIDER_GUIDE.md 中的示例代码是否可运行

这个测试不运行实际的浏览器，只验证代码语法和类型正确性
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from slidex import CaptchaProvider, ProviderElements, SolveResult, SliderSolver
from playwright.async_api import Page, Response
from typing import List, Tuple, Optional


class TestProviderGuideExamples:
    """测试文档中的示例代码"""

    def test_step1_basic_provider_structure(self):
        """步骤 1: 基本 Provider 结构"""
        # 文档示例代码
        class MyCustomProvider(CaptchaProvider):
            name = "my-custom"
            description = "My custom CAPTCHA provider"

            def __init__(self):
                super().__init__()
                self._result: Optional[bool] = None

            async def detect(self, page: Page) -> bool:
                return False

            async def locate_elements(self, page: Page) -> ProviderElements:
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300
                )

            async def extract_images(self, page: Page, elements: ProviderElements) -> Tuple[bytes, bytes]:
                return b"", b""

            async def perform_slide(self, page: Page, elements: ProviderElements, gap_x: int, trajectory: List) -> None:
                pass

            def validate_response(self, response: Response) -> Optional[bool]:
                return None

        # 验证可以实例化
        provider = MyCustomProvider()
        assert provider.name == "my-custom"
        assert provider._result is None

    @pytest.mark.asyncio
    async def test_step2_detect_implementation(self):
        """步骤 2: detect() 实现"""
        class MyCustomProvider(CaptchaProvider):
            name = "my-custom"

            async def detect(self, page: Page) -> bool:
                """文档中的 detect() 示例"""
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
                except Exception:
                    return False

            async def locate_elements(self, page: Page) -> ProviderElements:
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300
                )

            async def extract_images(self, page: Page, elements: ProviderElements) -> Tuple[bytes, bytes]:
                return b"", b""

            async def perform_slide(self, page: Page, elements: ProviderElements, gap_x: int, trajectory: List) -> None:
                pass

            def validate_response(self, response: Response) -> Optional[bool]:
                return None

        provider = MyCustomProvider()
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.evaluate = AsyncMock(return_value=False)

        # 应该返回 False（未检测到）
        result = await provider.detect(page)
        assert result is False

    @pytest.mark.asyncio
    async def test_step3_locate_elements_implementation(self):
        """步骤 3: locate_elements() 实现"""
        class MyCustomProvider(CaptchaProvider):
            name = "my-custom"

            async def detect(self, page: Page) -> bool:
                return True

            async def locate_elements(self, page: Page) -> ProviderElements:
                """文档中的 locate_elements() 示例"""
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
                    metadata={"version": "v2"},
                )

            async def extract_images(self, page: Page, elements: ProviderElements) -> Tuple[bytes, bytes]:
                return b"", b""

            async def perform_slide(self, page: Page, elements: ProviderElements, gap_x: int, trajectory: List) -> None:
                pass

            def validate_response(self, response: Response) -> Optional[bool]:
                return None

        provider = MyCustomProvider()
        page = AsyncMock()

        # Mock 元素
        btn_mock = AsyncMock()
        track_mock = AsyncMock()
        track_mock.bounding_box = AsyncMock(return_value={"width": 350})

        page.wait_for_selector = AsyncMock(return_value=btn_mock)
        page.query_selector = AsyncMock(side_effect=[track_mock, None, None])

        elements = await provider.locate_elements(page)

        assert elements.slider_btn == btn_mock
        assert elements.slider_track == track_mock
        assert elements.track_width_px == 350
        assert elements.metadata == {"version": "v2"}

    def test_step8_register_provider(self):
        """步骤 8: 注册 Provider"""
        from slidex import SliderSolver

        class MyCustomProvider(CaptchaProvider):
            name = "my-custom"

            async def detect(self, page: Page) -> bool:
                return True

            async def locate_elements(self, page: Page) -> ProviderElements:
                return ProviderElements(
                    slider_btn=AsyncMock(),
                    slider_track=AsyncMock(),
                    bg_img=None,
                    piece_img=None,
                    track_width_px=300
                )

            async def extract_images(self, page: Page, elements: ProviderElements) -> Tuple[bytes, bytes]:
                return b"", b""

            async def perform_slide(self, page: Page, elements: ProviderElements, gap_x: int, trajectory: List) -> None:
                pass

            def validate_response(self, response: Response) -> Optional[bool]:
                return None

        # 文档示例：注册
        SliderSolver.register_provider(
            "my-custom",
            MyCustomProvider,
            detection_priority=50,  # 文档中正确的参数名
        )

        # 验证注册成功
        providers = SliderSolver.list_providers()
        assert "my-custom" in providers


@pytest.fixture(autouse=True)
def cleanup_doc_test_providers():
    """清理文档测试 providers"""
    yield
    from slidex.providers import ProviderRegistry

    if "my-custom" in ProviderRegistry._providers:
        del ProviderRegistry._providers["my-custom"]
        ProviderRegistry._detection_order = [
            (p, n) for p, n in ProviderRegistry._detection_order if n != "my-custom"
        ]
