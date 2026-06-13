"""Aliyun NoCaptcha Provider"""

import base64
import json
from typing import List, Optional, Tuple
from playwright.async_api import Page, Response
from loguru import logger

from slidex.providers import CaptchaProvider, ProviderElements, SolveResult


class AliyunNoCaptchaProvider(CaptchaProvider):
    """阿里云 NoCaptcha 滑块验证码"""

    name = "aliyun-nocaptcha"
    description = "Aliyun NoCaptcha slider CAPTCHA"

    def __init__(self):
        super().__init__()
        self._result: Optional[bool] = None

    async def detect(self, page: Page) -> bool:
        """检测是否是 Aliyun NoCaptcha"""
        try:
            # 特征 1: nc_1_wrapper DOM
            nc_wrapper = await page.query_selector("#nc_1_wrapper, [id^=nc_][id$=_wrapper]")
            if nc_wrapper:
                return True

            # 特征 2: 检查 iframe src
            iframes = await page.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src and ("aliyuncs.com" in src or "/_____tmd_____" in src):
                    return True

            # 特征 3: JS 全局变量
            has_nc = await page.evaluate("() => window._nocaptcha !== undefined")
            if has_nc:
                return True

            return False
        except Exception as e:
            logger.debug(f"AliyunNoCaptchaProvider.detect() error: {e}")
            return False

    async def locate_elements(self, page: Page) -> ProviderElements:
        """定位元素"""
        # 滑块按钮
        slider_btn = await page.wait_for_selector(
            "#nc_1_n1z, .nc_iconfont, [id*=nc_][id*=n1z]",
            timeout=10000,
        )
        if not slider_btn:
            raise RuntimeError("Slider button not found")

        # 滑动轨道
        slider_track = await page.query_selector(
            "#nc_1_n1t, .nc_scale, [class*=scale]"
        )
        if not slider_track:
            raise RuntimeError("Slider track not found")

        # 背景图
        bg_img = await page.query_selector(
            "#nc_1_n1t img, .nc_scale img, img[id*=bg]"
        )

        # 拼图块（部分场景有独立拼图块，有些直接用滑块按钮）
        piece_img = await page.query_selector(
            ".nc_iconfont, #nc_1_n1z img, img[id*=slide]"
        )

        # 轨道宽度
        track_box = await slider_track.bounding_box()
        track_width_px = int(track_box["width"]) if track_box else 300

        return ProviderElements(
            slider_btn=slider_btn,
            slider_track=slider_track,
            bg_img=bg_img,
            piece_img=piece_img,
            track_width_px=track_width_px,
        )

    async def extract_images(
        self, page: Page, elements: ProviderElements
    ) -> Tuple[bytes, bytes]:
        """提取图像"""
        # 背景图
        if elements.bg_img:
            bg_src = await elements.bg_img.get_attribute("src")
            if bg_src and bg_src.startswith("data:image"):
                # data URL
                bg_bytes = base64.b64decode(bg_src.split(",", 1)[1])
            else:
                # 截图
                bg_bytes = await elements.bg_img.screenshot()
        else:
            # fallback: 截取轨道区域
            bg_bytes = await elements.slider_track.screenshot()

        # 拼图块
        if elements.piece_img:
            piece_bytes = await elements.piece_img.screenshot()
        else:
            # fallback: 使用滑块按钮
            piece_bytes = await elements.slider_btn.screenshot()

        return bg_bytes, piece_bytes

    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> None:
        """执行滑动"""
        # 注册响应监听
        self._result = None

        async def response_handler(response: Response):
            result = self.validate_response(response)
            if result is not None:
                self._result = result

        page.on("response", response_handler)

        try:
            # 获取滑块中心坐标
            btn_box = await elements.slider_btn.bounding_box()
            if not btn_box:
                raise RuntimeError("Cannot get slider button bounding box")

            start_x = btn_box["x"] + btn_box["width"] / 2
            start_y = btn_box["y"] + btn_box["height"] / 2

            # 开始拖动
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            await page.wait_for_timeout(50)

            # 执行轨迹
            for x, y, ts_ms in trajectory:
                target_x = start_x + x
                target_y = start_y + y
                await page.mouse.move(target_x, target_y)
                await page.wait_for_timeout(10)

            await page.wait_for_timeout(50)
            await page.mouse.up()

        finally:
            page.remove_listener("response", response_handler)

    def validate_response(self, response: Response) -> Optional[bool]:
        """验证响应"""
        url = response.url
        if "/slide?" not in url and "/_____tmd_____/slide" not in url:
            return None

        try:
            body = response.body()
            text = body.decode("utf-8", errors="ignore")
            data = json.loads(text)

            if isinstance(data, dict):
                # 成功条件
                if data.get("success") or data.get("code") == 0:
                    return True
                # 失败
                return False

        except Exception as e:
            logger.debug(f"validate_response error: {e}")

        return None

    async def get_result(self, page: Page, timeout_ms: int = 5000) -> SolveResult:
        """等待结果"""
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


__all__ = ["AliyunNoCaptchaProvider"]
