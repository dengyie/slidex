"""Provider-aware SliderSolver integration layer"""

from typing import Optional, Tuple, Dict
from loguru import logger
from playwright.async_api import Page

from slidex.providers import ProviderRegistry, CaptchaProvider
from slidex.providers.builtin import *  # auto-register built-in providers
from slidex._trajectory import generate_trajectory, trajectory_to_points
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex._slide_geometry import clamp_travel, points_from_recorded
from slidex._cookies import select_cookies_for_url


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
                self._emit_telemetry_event("provider_detect_failed", provider_name="auto")
                return False
            logger.info(f"[{self.pure_user_id}] detected provider: {self._provider.name}")
            self._emit_telemetry_event("provider_selected", provider_name=self._provider.name, selected_by="auto")
        else:
            # 手动指定
            try:
                self._provider = ProviderRegistry.get(self._provider_name)
                logger.info(f"[{self.pure_user_id}] using provider: {self._provider.name}")
                self._emit_telemetry_event("provider_selected", provider_name=self._provider.name, selected_by="manual")
            except ValueError as e:
                logger.error(f"[{self.pure_user_id}] {e}")
                self._emit_telemetry_event("provider_init_failed", provider_name=self._provider_name, reason=str(e))
                return False

        # 调用 provider 初始化钩子
        try:
            await self._provider.on_init(page)
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] provider on_init failed: {e}, falling back to legacy")
            self._emit_telemetry_event("provider_init_failed", provider_name=self._provider.name, reason=str(e))
            self._provider = None  # 清空 provider，回退到 legacy
            return False  # 初始化失败，回退到 legacy 模式
        return True

    async def _solve_with_provider(self, page: Page) -> Tuple[bool, Optional[Dict]]:
        """使用 provider 求解"""
        if not self._provider:
            raise RuntimeError("Provider not initialized")

        audit = self._install_url_audit(page)
        try:
            # 1. 定位元素
            elements = await self._provider.locate_elements(page)
            metadata_str = f", metadata={elements.metadata}" if elements.metadata else ""
            logger.debug(f"[{self.pure_user_id}] elements located, track_width={elements.track_width_px}px{metadata_str}")

            # scale 型（nc.js"拖到最右边"）没有缺口：travel=轨道满行程，
            # 图像匹配出的"缺口"是背景纹理伪匹配（生产实测恒 87px/conf 0.31）
            slider_type = (elements.metadata or {}).get("slider_type")
            if slider_type == "scale":
                btn_box = await elements.slider_btn.bounding_box()
                track_box = await elements.slider_track.bounding_box()
                if not btn_box or not track_box:
                    logger.warning(f"[{self.pure_user_id}] scale slider: cannot get boxes")
                    self._emit_telemetry_event("provider_gap_not_found", provider_name=self._provider.name)
                    return False, None
                travel = int(max(0.0, track_box["width"] - btn_box["width"]))
                logger.info(
                    f"[{self.pure_user_id}] scale slider detected: travel=full {travel}px "
                    f"(track={track_box['width']:.0f}, btn={btn_box['width']:.0f})"
                )
                self._emit_telemetry_event(
                    "distance_detected",
                    distance=travel,
                    source="provider",
                    provider_name=self._provider.name,
                    slider_type="scale",
                )
                points = None  # 走合成轨迹
            else:
                travel, points = await self._jigsaw_travel_and_points(page, elements)

            if travel is None or travel <= 0:
                logger.warning(f"[{self.pure_user_id}] cannot determine travel (travel={travel})")
                return False, None

            # 4. 生成轨迹（相对位移；按 cookie + 目标距离匹配并缩放；
            # scale 型已在上一步跳过图像匹配，points=None 走合成轨迹）
            if points is None:
                try:
                    trajectory_dir = self._config.get_trajectory_dir()
                    trajectory_pool = SliderTrajectoryPool(trajectory_dir)
                    recorded_traj = trajectory_pool.load_best_trajectory(self.pure_user_id, travel)
                    if recorded_traj is None:
                        recorded_traj = trajectory_pool.load_best_trajectory("default", travel)
                except Exception as e:
                    logger.warning(f"[{self.pure_user_id}] trajectory pool error: {e}, using synthetic")
                    recorded_traj = None

                points = points_from_recorded(recorded_traj, travel)
                if points:
                    logger.debug(f"[{self.pure_user_id}] using recorded relative trajectory ({len(points)} points)")
                else:
                    logger.debug(f"[{self.pure_user_id}] generating synthetic trajectory")
                    trajectory = generate_trajectory(
                        distance=travel,
                        attempt=1,
                    )
                    # 合成轨迹转成相对位移；provider.perform_slide 会再加按钮起点
                    points = trajectory_to_points(trajectory, start_x=0, start_y=0)

            try:
                # 5. 执行滑动（滑动前装页面内网络打点，滑动后读回）
                await self._install_net_tap(page)
                await self._provider.perform_slide(page, elements, travel, points)
                logger.debug(f"[{self.pure_user_id}] slide performed")

                # 6. 等待结果
                result = await self._provider.get_result(page, timeout_ms=5000)
            finally:
                await self._provider.cleanup_after_result(page)
            if result.success:
                logger.success(f"[{self.pure_user_id}] provider solve success!")
            else:
                logger.warning(f"[{self.pure_user_id}] provider solve failed: {result.error}")
            self._emit_telemetry_event(
                "provider_result",
                provider_name=self._provider.name,
                success=result.success,
                error=result.error,
                cookie_count=len(result.cookies or {}),
            )

            if result.success:
                # 7. x5sec settle：阿里 punish 流程的放行票据在通过后的页面续行
                # （回跳/重载链的 Set-Cookie）里下发，/slide 响应本身不带。
                result_cookies = await self._settle_x5sec(page, result.cookies)
                return True, result_cookies
            return False, result.cookies

        except Exception as e:
            logger.error(f"[{self.pure_user_id}] provider solve error: {e}", exc_info=True)
            self._emit_telemetry_event("provider_solve_error", provider_name=self._provider.name, reason=str(e))
            return False, None
        finally:
            audit.uninstall()
            await self._dump_net_tap(page)

    async def _settle_x5sec(self, page: Page, cookies: Optional[Dict]) -> Optional[Dict]:
        """滑动通过后等待 punish 流程下发 x5sec（0.6.9）。

        /slide 返回 success 后，nc.js 会回跳/重载原 mtop punish 链路，x5sec 由
        该跳转链的 Set-Cookie 下发——不在 /slide 响应里，也不立即出现在 context
        cookies。在浏览器存活窗口内：快轮询 context.cookies()，无果则用原
        verify_url 重载一次（等价 nc.js 的回跳）再轮询。只对 punish 类 URL 启用；
        拿不到时原样返回（严格判定由 bot 侧兜底）。
        """
        merged = dict(cookies or {})
        if merged.get("x5sec") or not self._requires_validation_cookie(self._verify_url or ""):
            return merged

        async def _poll_x5sec(deadline_s: float) -> Optional[Dict]:
            import asyncio as _asyncio
            loop = _asyncio.get_event_loop()
            end = loop.time() + deadline_s
            while loop.time() < end:
                try:
                    jar = await page.context.cookies() or []
                except Exception:
                    return None  # 页面/context 已关
                fresh = select_cookies_for_url([c for c in jar if isinstance(c, dict)], self._verify_url or "")
                if fresh.get("x5sec"):
                    return fresh
                await _asyncio.sleep(0.5)
            return None

        logger.debug(f"[{self.pure_user_id}] waiting for x5sec settle...")
        fresh = await _poll_x5sec(8.0)
        if fresh:
            logger.info(f"[{self.pure_user_id}] x5sec settled after slide pass")
            self._emit_telemetry_event("x5sec_settled", source="poll")
            merged.update(fresh)
            return merged

        # nc.js 回跳等价：用原 verify_url 带通过后的会话状态再走一遍 punish 入口
        try:
            logger.info(f"[{self.pure_user_id}] x5sec not settled, reloading punish page once")
            await page.reload(wait_until="load", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] punish reload failed: {e}")
        fresh = await _poll_x5sec(3.0)
        if fresh:
            logger.info(f"[{self.pure_user_id}] x5sec settled after punish reload")
            self._emit_telemetry_event("x5sec_settled", source="reload")
            merged.update(fresh)
        else:
            logger.warning(f"[{self.pure_user_id}] x5sec still absent after settle window")
            self._emit_telemetry_event("x5sec_settle_missed")
        return merged

    async def _jigsaw_travel_and_points(self, page: Page, elements):
        """拼图缺口型：图像匹配缺口位置（clip 到轨道行程）。返回 (travel, None)，
        points 统一在调用点由轨迹池/合成轨迹生成。"""
        # 2. 提取图像
        bg_bytes, piece_bytes = await self._provider.extract_images(page, elements)
        logger.debug(f"[{self.pure_user_id}] images extracted, bg={len(bg_bytes)} bytes, piece={len(piece_bytes)} bytes")

        # 3. 图像匹配
        try:
            gap_x, confidence = await self._provider.find_gap(bg_bytes, piece_bytes)
        except Exception as e:
            logger.error(f"[{self.pure_user_id}] find_gap error: {e}")
            self._emit_telemetry_event("provider_find_gap_failed", provider_name=self._provider.name, reason=str(e))
            return None, None

        if gap_x is None:
            logger.warning(f"[{self.pure_user_id}] gap not found")
            self._emit_telemetry_event("provider_gap_not_found", provider_name=self._provider.name)
            return None, None

        travel = int(clamp_travel(gap_x, elements.track_width_px))
        logger.info(
            f"[{self.pure_user_id}] gap detected at x={gap_x}px, "
            f"travel={travel}px, confidence={confidence:.2f}"
        )
        self._emit_telemetry_event(
            "distance_detected",
            distance=travel,
            source="provider",
            provider_name=self._provider.name,
            confidence=round(confidence, 4),
        )
        return travel, None

    # JS 网络打点：patchright 下 page.on("console") 依赖 Runtime.enable，被刻意
    # 屏蔽（容器实测 console.log 零事件），而 page.on("response") 只能看见主进程
    # 的 HTTP 往返——sendBeacon/WS/worker 内请求全部不可见。滑动前在页面里装
    # tap 记录 fetch/XHR/beacon/WS/console，失败后 evaluate 读回，即可分辨
    # “校验请求根本没发出” vs “走了 page.on 看不见的通道”。
    _NET_TAP_JS = """
    () => {
      if (window.__slidexNet) { window.__slidexNet.length = 0; return; }
      const log = (window.__slidexNet = []);
      const rec = (kind, detail) => {
        try { if (log.length < 400) log.push(kind + ' ' + String(detail).slice(0, 250)); } catch (e) {}
      };
      const origFetch = window.fetch;
      if (origFetch) {
        window.fetch = function (input, init) {
          try {
            const url = typeof input === 'string' ? input : (input && input.url) || '';
            const m = (init && init.method) || (input && input.method) || 'GET';
            rec('fetch', m + ' ' + url);
          } catch (e) {}
          return origFetch.apply(this, arguments);
        };
      }
      const OrigOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function (method, url) {
        try { rec('xhr', method + ' ' + url); } catch (e) {}
        return OrigOpen.apply(this, arguments);
      };
      try {
        const origBeacon = navigator.sendBeacon;
        if (origBeacon) {
          navigator.sendBeacon = function (url, data) {
            try { rec('beacon', url); } catch (e) {}
            return origBeacon.apply(this, arguments);
          };
        }
      } catch (e) {}
      try {
        const OrigWS = window.WebSocket;
        if (OrigWS) {
          window.WebSocket = function (url, protocols) {
            try { rec('ws', url); } catch (e) {}
            return new OrigWS(url, protocols);
          };
          window.WebSocket.prototype = OrigWS.prototype;
        }
      } catch (e) {}
      const origLog = {};
      ['log', 'info', 'warn', 'error'].forEach((level) => {
        origLog[level] = console[level];
        console[level] = function () {
          try { rec('console.' + level, Array.prototype.slice.call(arguments).join(' ')); } catch (e) {}
          return origLog[level].apply(console, arguments);
        };
      });
    }
    """

    _NET_TAP_READ_JS = "() => (window.__slidexNet || []).slice()"

    # Resource Timing 清点：worker / service worker 内的请求不经过页面 window 的
    # fetch/XHR，tap 记不到，但主 frame 的 resource entries 里会有
    _RESOURCE_TIMING_JS = """
    () => {
      try {
        return performance.getEntriesByType('resource').slice(-30).map((e) =>
          (e.initiatorType || '?') + ' ' + (e.name || '').slice(0, 200));
      } catch (err) { return []; }
    }
    """

    async def _install_net_tap(self, page: Page) -> None:
        try:
            await page.evaluate(self._NET_TAP_JS)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] net tap install failed: {e}")

    async def _dump_net_tap(self, page: Page) -> None:
        try:
            entries = await page.evaluate(self._NET_TAP_READ_JS)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] net tap read failed: {e}")
            return
        if not entries:
            logger.warning(
                f"[{self.pure_user_id}] net tap: 0 events — 页面在滑动窗口内没有任何 "
                "fetch/XHR/beacon/WS/console 活动（校验请求很可能根本没发出）"
            )
            self._emit_telemetry_event("provider_net_tap", count=0)
        else:
            logger.info(f"[{self.pure_user_id}] net tap: {len(entries)} events during slide window")
            for line in entries[:60]:
                logger.info(f"[{self.pure_user_id}] net tap | {line}")
            # console 兜底成功信号：page.on("console") 在 patchright 下失效，页面内
            # wrapper 是唯一能捕获「验证通过」标志的通道
            for line in entries:
                text = str(line)
                if "验证通过" in text or "captchaVerifyParam" in text:
                    logger.success(f"[{self.pure_user_id}] net tap console SUCCESS marker: {text[:200]}")
                    self._emit_telemetry_event("slide_console_success", source="net_tap", detail=text[:200])
                    break
            self._emit_telemetry_event(
                "provider_net_tap", count=len(entries), sample=[str(x)[:200] for x in entries[-20:]]
            )
        # 重置缓冲：下次尝试从零计数，不重复上报
        await self._install_net_tap(page)
        # Resource Timing 兜底清点（page 主 frame 视角，含 worker 发起的请求）
        try:
            resources = await page.evaluate(self._RESOURCE_TIMING_JS)
            if resources:
                logger.info(f"[{self.pure_user_id}] resource timing tail: {len(resources)} entries")
                for line in resources[-15:]:
                    logger.info(f"[{self.pure_user_id}] resource | {line}")
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] resource timing read failed: {e}")

    def _install_url_audit(self, page: Page):
        """滑动窗口全量响应审计：结果捕获面 miss（code=-1 且无 tmd slide 包）
        时，这里落出滑动期间真实经过的 verify 端点，供下一步钉 pattern。"""
        state = {"responses": []}

        def _on_response(response):
            try:
                state["responses"].append(
                    {
                        "url": response.url[:300],
                        "status": response.status,
                        "method": response.request.method,
                    }
                )
            except Exception:
                pass

        try:
            page.on("response", _on_response)
        except Exception:
            pass

        class _Audit:
            def uninstall(self_inner):
                try:
                    page.remove_listener("response", _on_response)
                except Exception:
                    pass
                hits = state["responses"]
                if hits:
                    summary = [f"{r['method']} {r['status']} {r['url']}" for r in hits[-40:]]
                    logger.info(
                        f"[{self.pure_user_id}] url audit: {len(hits)} responses during provider slide"
                    )
                    for line in summary:
                        logger.info(f"[{self.pure_user_id}] url audit | {line}")
                    self._emit_telemetry_event(
                        "provider_url_audit",
                        count=len(hits),
                        sample=[r["url"] for r in hits[-20:]],
                    )

        return _Audit()

    @classmethod
    def register_provider(cls, name: str, provider_class, detection_priority: int = 100):
        """注册自定义 provider"""
        ProviderRegistry.register(name, provider_class, detection_priority)

    @classmethod
    def list_providers(cls):
        """列出所有已注册 provider"""
        return ProviderRegistry.list_providers()


__all__ = ["ProviderSolverMixin"]
