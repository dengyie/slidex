"""GeeTest (极验) Provider"""

import json
import asyncio
from typing import List, Optional, Tuple
from playwright.async_api import Page, Response
from loguru import logger

from slidex.providers import CaptchaProvider, ProviderElements, SolveResult
from slidex.vision.models import ChallengeType, ProviderManifest, VisionContext


class GeeTestProvider(CaptchaProvider):
    """极验 (GeeTest) 滑块验证码 — 支持 v3 和 v4"""

    name = "geetest"
    description = "GeeTest slider CAPTCHA (v3/v4)"
    manifest = ProviderManifest(
        name=name,
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )

    def __init__(self):
        super().__init__()
        self._result: Optional[bool] = None
        self._version: Optional[str] = None  # "v3" or "v4"
        self._response_handler = None

    async def detect(self, page: Page) -> bool:
        """检测是否是 GeeTest"""
        try:
            # 特征 1: DOM class
            geetest_el = await page.query_selector(
                ".geetest_panel, .geetest_holder, .geetest_box, [class*=geetest]"
            )
            if geetest_el:
                return True

            # 特征 2: JS 全局变量
            has_geetest = await page.evaluate(
                "() => window.initGeetest !== undefined || window.initGeetest4 !== undefined"
            )
            if has_geetest:
                # 判断版本
                has_v4 = await page.evaluate("() => window.initGeetest4 !== undefined")
                self._version = "v4" if has_v4 else "v3"
                return True

            # 特征 3: 网络请求
            # (需要在 page 初始化时监听，这里暂时跳过)

            return False
        except Exception as e:
            logger.debug(f"GeeTestProvider.detect() error: {e}")
            return False

    async def locate_elements(self, page: Page) -> ProviderElements:
        """定位元素"""
        # v3 和 v4 选择器略有不同
        if self._version == "v4":
            slider_btn_selector = ".geetest_slider_button, [class*=slider_button]"
            slider_track_selector = ".geetest_slider, [class*=slider_track]"
            canvas_bg_selector = ".geetest_canvas_bg canvas, canvas[class*=bg]"
            canvas_slice_selector = ".geetest_canvas_slice canvas, canvas[class*=slice]"
        else:
            # v3 默认选择器
            slider_btn_selector = ".geetest_slider_button"
            slider_track_selector = ".geetest_slider_track"
            canvas_bg_selector = ".geetest_canvas_bg canvas"
            canvas_slice_selector = ".geetest_canvas_slice canvas"

        slider_btn = await page.wait_for_selector(slider_btn_selector, timeout=10000)
        if not slider_btn:
            raise RuntimeError("GeeTest slider button not found")

        slider_track = await page.query_selector(slider_track_selector)
        if not slider_track:
            raise RuntimeError("GeeTest slider track not found")

        # GeeTest 使用 canvas
        bg_canvas = await page.query_selector(canvas_bg_selector)
        piece_canvas = await page.query_selector(canvas_slice_selector)

        # 轨道宽度
        track_box = await slider_track.bounding_box()
        track_width_px = int(track_box["width"]) if track_box else 300

        return ProviderElements(
            slider_btn=slider_btn,
            slider_track=slider_track,
            bg_img=bg_canvas,
            piece_img=piece_canvas,
            track_width_px=track_width_px,
            metadata={"version": self._version},
        )

    async def extract_images(
        self, page: Page, elements: ProviderElements
    ) -> Tuple[bytes, bytes]:
        """提取图像（从 canvas）"""
        # 背景 canvas → data URL → bytes
        if elements.bg_img:
            bg_data_url = await page.evaluate(
                "(canvas) => canvas.toDataURL('image/png')", elements.bg_img
            )
            import base64
            bg_bytes = base64.b64decode(bg_data_url.split(",", 1)[1])
        else:
            raise RuntimeError("GeeTest background canvas not found")

        # 拼图块 canvas
        if elements.piece_img:
            piece_data_url = await page.evaluate(
                "(canvas) => canvas.toDataURL('image/png')", elements.piece_img
            )
            piece_bytes = base64.b64decode(piece_data_url.split(",", 1)[1])
        else:
            raise RuntimeError("GeeTest slice canvas not found")

        return bg_bytes, piece_bytes

    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> None:
        """执行滑动"""
        self._result = None

        def response_handler(response: Response):
            async def _handle():
                result = await self.validate_response(response)
                if result is not None:
                    self._result = result

            asyncio.create_task(_handle())

        self._response_handler = response_handler
        page.on("response", response_handler)

        btn_box = await elements.slider_btn.bounding_box()
        if not btn_box:
            raise RuntimeError("Cannot get slider button bounding box")

        start_x = btn_box["x"] + btn_box["width"] / 2
        start_y = btn_box["y"] + btn_box["height"] / 2

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.wait_for_timeout(100)

        for x, y, ts_ms in trajectory:
            await page.mouse.move(start_x + x, start_y + y)
            await page.wait_for_timeout(15)

        await page.wait_for_timeout(100)
        await page.mouse.up()

    async def cleanup_after_result(self, page: Page) -> None:
        if self._response_handler:
            page.remove_listener("response", self._response_handler)
            self._response_handler = None

    async def validate_response(self, response: Response) -> Optional[bool]:
        """验证响应"""
        url = response.url

        # GeeTest v3: /ajax.php?gt=...
        # GeeTest v4: /api/v4/slider
        if "/ajax.php" not in url and "/api/v4/slider" not in url and "/verify" not in url:
            return None

        try:
            body = await response.body()
            text = body.decode("utf-8", errors="ignore")
            data = json.loads(text)

            # v3: {"success": 1, "message": "success"}
            # v4: {"code": 0, "status": "success"}
            if isinstance(data, dict):
                if data.get("success") == 1 or data.get("status") == "success":
                    return True
                if data.get("success") == 0 or data.get("status") == "fail":
                    return False

        except Exception as e:
            logger.debug(f"GeeTest validate_response error: {e}")

        return None


__all__ = ["GeeTestProvider"]
