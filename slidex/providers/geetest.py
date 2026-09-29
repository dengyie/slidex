"""GeeTest (极验) Provider"""

import json
import random
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse
from playwright.async_api import Page, Response
from loguru import logger

from slidex._trajectory import slide_end_hold_range

from slidex.providers import CaptchaProvider, ProviderElements, SolveResult
from slidex.vision.models import ChallengeType, ProviderManifest, VisionContext
from slidex._frames import iter_search_targets


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

    def __init__(self, host_markers: Optional[Iterable[str]] = None):
        super().__init__()
        self._version: Optional[str] = None  # "v3" or "v4"
        # 私有化部署自定义域（不含 geetest 字样）的 host 特征扩展点
        self._extra_host_markers = tuple(host_markers or ())

    async def detect(self, page: Page) -> bool:
        """检测是否是 GeeTest（主文档 + iframe）。"""
        self._challenge_scope = None
        try:
            for target in await iter_search_targets(page):
                try:
                    geetest_el = await target.query_selector(
                        ".geetest_panel, .geetest_holder, .geetest_box, [class*=geetest]"
                    )
                except Exception:
                    geetest_el = None
                if geetest_el:
                    self._challenge_scope = target
                    return True
                try:
                    has_geetest = await target.evaluate(
                        "() => window.initGeetest !== undefined || window.initGeetest4 !== undefined"
                    )
                except Exception:
                    has_geetest = False
                if has_geetest:
                    try:
                        has_v4 = await target.evaluate("() => window.initGeetest4 !== undefined")
                    except Exception:
                        has_v4 = False
                    self._version = "v4" if has_v4 else "v3"
                    self._challenge_scope = target
                    return True
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

        scope = self._challenge_scope or page
        slider_btn = await scope.wait_for_selector(slider_btn_selector, timeout=10000)
        if not slider_btn:
            raise RuntimeError("GeeTest slider button not found")

        slider_track = await scope.query_selector(slider_track_selector)
        if not slider_track:
            raise RuntimeError("GeeTest slider track not found")

        # GeeTest 使用 canvas
        bg_canvas = await scope.query_selector(canvas_bg_selector)
        piece_canvas = await scope.query_selector(canvas_slice_selector)

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
        import base64

        # 在元素所属 frame 上取图，避免 iframe 场景下 page.evaluate 拿错上下文。
        if elements.bg_img:
            bg_data_url = await elements.bg_img.evaluate(
                "(canvas) => canvas.toDataURL('image/png')"
            )
            bg_bytes = base64.b64decode(bg_data_url.split(",", 1)[1])
        else:
            raise RuntimeError("GeeTest background canvas not found")

        if elements.piece_img:
            piece_data_url = await elements.piece_img.evaluate(
                "(canvas) => canvas.toDataURL('image/png')"
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
        extra_overshoot: bool = True,
        end_hold_scale: float = 1.0,
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

        hold_ms = 100.0
        slide_points = trajectory
        if trajectory and trajectory[0][0] == 0 and trajectory[0][1] == 0 and trajectory[0][2] > 0:
            hold_ms = float(trajectory[0][2])
            slide_points = trajectory[1:]
        await page.wait_for_timeout(int(hold_ms))

        for x, y, ts_ms in slide_points:
            await page.mouse.move(start_x + x, start_y + y)
            delay = 15 if ts_ms is None else max(0, int(float(ts_ms)))
            if delay:
                await page.wait_for_timeout(delay)

        end_x = start_x + slide_points[-1][0] if slide_points else start_x
        end_y = start_y + slide_points[-1][1] if slide_points else start_y
        if extra_overshoot and slide_points:
            overshoot = random.uniform(3.0, 6.0)
            back = random.uniform(2.0, 3.5)
            await page.mouse.move(end_x + overshoot, end_y + random.uniform(-1.5, 1.5))
            await page.wait_for_timeout(random.randint(60, 110))
            await page.mouse.move(end_x + overshoot - back, end_y + random.uniform(-1.0, 1.0))
            await page.wait_for_timeout(random.randint(50, 90))
            await page.mouse.move(end_x + random.uniform(-1.0, 1.0), end_y)

        end_hold_lo, end_hold_hi = slide_end_hold_range()
        scale = max(0.0, float(end_hold_scale or 1.0))
        await page.wait_for_timeout(int(random.uniform(end_hold_lo, end_hold_hi) * 1000 * scale))
        await page.mouse.up()

    # GeeTest 验证响应的 URL 特征：host 属于 geetest 域（含私有化部署的自定义域），
    # 且路径是已知端点。host 匹配是根因约束——旧逻辑对任意站点的 /verify 都会
    # 抢答，导致同页其他验证码的结果被误读为 GeeTest 的成败。官方域（geetest.com/
    # geetest.cn/geevisit.com）单独白名单；其余任意站点的 /verify /ajax.php /
    # slider 一律不认，私有化部署的 CDN/代理域通过 host_markers 参数注入。
    _GEETEST_HOST_MARKERS = ("geetest", "gee-test", "gt4.")
    _GEETEST_OFFICIAL_HOST_SUFFIXES = ("geetest.com", "geetest.cn", "geevisit.com")
    _GEETEST_VERIFY_PATHS = ("/ajax.php", "/api/v4/slider", "/verify")

    def _is_geetest_response_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # /verify 是很多站点自己的校验端点，仅当 host 带 geetest 特征时才认
        markers = self._GEETEST_HOST_MARKERS + self._extra_host_markers
        if any(marker in host for marker in markers):
            return parsed.path in self._GEETEST_VERIFY_PATHS
        # 官方域（geetest.com/cn、geevisit.com，含子域）的已知端点
        if any(
            host == suffix or host.endswith("." + suffix)
            for suffix in self._GEETEST_OFFICIAL_HOST_SUFFIXES
        ):
            return parsed.path in self._GEETEST_VERIFY_PATHS
        # 其余主机一律不算 GeeTest 结果（旧逻辑对任意站点 /ajax.php 也会抢答）
        return False

    async def validate_response(self, response: Response) -> Optional[bool]:
        """验证响应"""
        url = response.url

        # GeeTest v3: /ajax.php?gt=...
        # GeeTest v4: /api/v4/slider
        if not self._is_geetest_response_url(url):
            return None

        try:
            body = await response.body()
            text = body.decode("utf-8", errors="ignore")
            data = json.loads(text)

            # v3: {"success": 1, "message": "success"}
            # v4: {"code": 0, "status": "success"}
            if isinstance(data, dict):
                raw_code = data.get("code")
                if raw_code is not None:
                    try:
                        self._result_code = int(raw_code)
                    except (TypeError, ValueError):
                        pass
                if data.get("success") == 1 or data.get("status") == "success":
                    if self._result_code is None:
                        self._result_code = 0
                    return True
                if data.get("success") == 0 or data.get("status") == "fail":
                    if self._result_code is None:
                        self._result_code = 1
                    return False

        except Exception as e:
            logger.debug(f"GeeTest validate_response error: {e}")

        return None


__all__ = ["GeeTestProvider"]
