"""Aliyun NoCaptcha Provider"""

import base64
import json
from typing import List, Optional, Tuple
from playwright.async_api import Page, Response
from loguru import logger

from slidex.providers import CaptchaProvider, ProviderElements, SolveResult
from slidex.vision.models import ChallengeType, ProviderManifest, VisionContext
from slidex._frames import iter_search_targets, wait_in_targets, query_in_targets


_SLIDER_BTN = "#nc_1_n1z, .nc_iconfont, [id*=nc_][id*=n1z]"
_SLIDER_TRACK = "#nc_1_n1t, .nc_scale, [class*=scale]"
_BG_IMG = "#nc_1_n1t img, .nc_scale img, img[id*=bg]"
_PIECE_IMG = ".nc_iconfont, #nc_1_n1z img, img[id*=slide]"
_WRAPPER = "#nc_1_wrapper, [id^=nc_][id$=_wrapper]"


class AliyunNoCaptchaProvider(CaptchaProvider):
    """阿里云 NoCaptcha 滑块验证码"""

    name = "aliyun-nocaptcha"
    description = "Aliyun NoCaptcha slider CAPTCHA"
    manifest = ProviderManifest(
        name=name,
        version="0.1.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )
    # 浏览器内 Aliyun 图匹配的供应商校正（拼图中心 → 拖动行程）
    IMAGE_OFFSET_CORRECTION = -35

    def __init__(self):
        super().__init__()
        self._challenge_scope = None

    async def detect(self, page: Page) -> bool:
        """检测是否是 Aliyun NoCaptcha（主文档 + iframe）。"""
        self._challenge_scope = None
        try:
            targets = await iter_search_targets(page)
            for target in targets:
                try:
                    nc_wrapper = await target.query_selector(_WRAPPER)
                except Exception:
                    nc_wrapper = None
                if nc_wrapper:
                    self._challenge_scope = target
                    return True
                try:
                    has_nc = await target.evaluate("() => window._nocaptcha !== undefined")
                except Exception:
                    has_nc = False
                if has_nc:
                    self._challenge_scope = target
                    return True

            iframes = await page.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if not src or ("aliyuncs.com" not in src and "/_____tmd_____" not in src):
                    continue
                frame = None
                try:
                    frame = await iframe.content_frame()
                except Exception:
                    frame = None
                if frame is None:
                    logger.warning(
                        "Aliyun NoCaptcha iframe detected but content_frame is unavailable"
                    )
                    continue
                try:
                    wrapper = await frame.query_selector(_WRAPPER)
                except Exception:
                    wrapper = None
                has_nc = False
                if wrapper is None:
                    try:
                        has_nc = await frame.evaluate("() => window._nocaptcha !== undefined")
                    except Exception:
                        has_nc = False
                if wrapper or has_nc:
                    self._challenge_scope = frame
                    return True
                logger.debug("Aliyun-looking iframe src without challenge DOM, skip")
            return False
        except Exception as e:
            logger.debug(f"AliyunNoCaptchaProvider.detect() error: {e}")
            return False

    def _targets(self, page: Page):
        if self._challenge_scope is not None:
            return [self._challenge_scope]
        return [page]

    async def locate_elements(self, page: Page) -> ProviderElements:
        """在 detect() 锁定的 frame（或主文档）上定位元素。"""
        targets = self._targets(page)
        if self._challenge_scope is None:
            targets = await iter_search_targets(page)

        slider_btn, used = await wait_in_targets(targets, _SLIDER_BTN, timeout=10000)
        if not slider_btn:
            raise RuntimeError("Slider button not found")
        scope_targets = [used] if used is not None else targets

        slider_track, _ = await query_in_targets(scope_targets, _SLIDER_TRACK)
        if not slider_track:
            slider_track, _ = await query_in_targets(targets, _SLIDER_TRACK)
        if not slider_track:
            raise RuntimeError("Slider track not found")

        bg_img, _ = await query_in_targets(scope_targets, _BG_IMG)
        piece_img, _ = await query_in_targets(scope_targets, _PIECE_IMG)

        track_box = await slider_track.bounding_box()
        track_width_px = int(track_box["width"]) if track_box else 300
        self._challenge_scope = used or self._challenge_scope

        return ProviderElements(
            slider_btn=slider_btn,
            slider_track=slider_track,
            bg_img=bg_img,
            piece_img=piece_img,
            track_width_px=track_width_px,
            metadata={"in_iframe": used is not None and used is not page},
        )

    async def extract_images(
        self, page: Page, elements: ProviderElements
    ) -> Tuple[bytes, bytes]:
        if elements.bg_img:
            bg_src = await elements.bg_img.get_attribute("src")
            if bg_src and bg_src.startswith("data:image"):
                bg_bytes = base64.b64decode(bg_src.split(",", 1)[1])
            else:
                bg_bytes = await elements.bg_img.screenshot()
        else:
            bg_bytes = await elements.slider_track.screenshot()

        if elements.piece_img:
            piece_bytes = await elements.piece_img.screenshot()
        else:
            piece_bytes = await elements.slider_btn.screenshot()

        return bg_bytes, piece_bytes

    async def find_gap(
        self,
        bg_bytes: bytes,
        piece_bytes: bytes,
    ) -> Tuple[Optional[int], float]:
        from slidex._image_match import SliderImageMatcher

        return SliderImageMatcher.find_gap_with_confidence(
            bg_bytes, piece_bytes, offset_correction=self.IMAGE_OFFSET_CORRECTION
        )

    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
    ) -> None:
        """执行滑动。trajectory 为相对位移 (dx, dy, delay_ms)。"""
        self.bind_response_listener(page)

        btn_box = await elements.slider_btn.bounding_box()
        if not btn_box:
            raise RuntimeError("Cannot get slider button bounding box")

        start_x = btn_box["x"] + btn_box["width"] / 2
        start_y = btn_box["y"] + btn_box["height"] / 2

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.wait_for_timeout(50)

        for x, y, ts_ms in trajectory:
            await page.mouse.move(start_x + x, start_y + y)
            delay = 10 if ts_ms is None else max(0, min(int(ts_ms), 50))
            if delay:
                await page.wait_for_timeout(delay)

        await page.wait_for_timeout(50)
        await page.mouse.up()

    async def validate_response(self, response: Response) -> Optional[bool]:
        url = response.url
        if "/slide?" not in url and "/_____tmd_____/slide" not in url:
            return None

        try:
            body = await response.body()
            text = body.decode("utf-8", errors="ignore")
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            success_flag = data.get("success")
            if success_flag in (False, 0, "false", "fail"):
                return False
            if success_flag in (True, 1, "success", "ok"):
                return True
            code = data.get("code")
            if code == 0:
                return True
            if code is not None:
                return False
            return False
        except Exception as e:
            logger.debug(f"validate_response error: {e}")
            return None


__all__ = ["AliyunNoCaptchaProvider"]
