"""PX press/silent 求解器 —— 需求单 §5 方案 (1)：headless Chromium 农场。

对一个任务：按 payload 注入 UA/时区/语言/sticky proxy 与 cookies，开
hsprotect iframe（或站点页），等 PX JS 自行产 ``_px3``，然后从 cookie
jar 抽四件套。全程一个 context 一个出口，绝不持久化页面/cookie，日志
只出前缀与长度（``slidex.px.cookies.redact_token``）。

可测试性：``playwright_factory`` 默认走真 Playwright，测试注入 fake，
``browser`` 对象只需要 ``new_context``；context 只需 ``new_page / cookies /
close``；page 只需 ``goto / wait_for_timeout / close / on``。真实链路有
tests/test_px_sim.py 用本地 Chromium + 假 PX 页兜底。
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from loguru import logger

from .cookies import extract_px_tokens, is_pass_token, redact_token
from .models import (
    ERR_BROWSER_LAUNCH,
    ERR_CHALLENGE_HTTP,
    ERR_CHALLENGE_LOAD,
    ERR_PROXY_AUTH,
    ERR_CHALLENGE_TIMEOUT,
    ERR_INTERNAL,
    ERR_SOLVER_BUSY,
    ERR_TOKEN_BLOCKED,
    ERR_TOKEN_TIMEOUT,
    ERR_VID_REQUIRED,
    PxPressRequest,
    PxPressResult,
)

# 已打开 challenge 页但只拿到非通过级 px3 时的宽限：持续这么久仍无 :1000:
# 就按「被拦」（px_blocked）fail，而不是把无段 token 当成功交给调用方。
_BLOCKED_GRACE_S = 10.0
# challenge 页首跳预算：hsprotect 首屏不该比这更慢，超过即视为没连通
_NAV_TIMEOUT_S = 45.0
# 宿主页（websiteURL）首跳预算：signup.live.com 首屏，同上
_HOST_NAV_TIMEOUT_S = 30.0
# iframe 注入后等待其文档就绪的预算（challengeUrl iframe 一般 1–2s 内出）
_IFRAME_WAIT_S = 20.0
# Playwright 时区/语言校验失败时的兜底（映射表之外的未知值会抛）
_FALLBACK_TIMEZONE = "America/New_York"
_FALLBACK_LOCALE = "en-US"

# iframe 插入宿主页的样式：PX 官方 challenge iframe 的可见尺寸，
# 隐藏（display:none）会让部分传感器走不出工作分支
_IFRAME_STYLE = "border:0;width:320px;height:110px;position:fixed;bottom:8px;left:8px;z-index:2147483647;"


class PxPressSlots:
    """并发/存活统计（服务层 /health 用它，纯内存计 24h 已收敛不出舱）。"""

    def __init__(self, *, concurrency: int = 5) -> None:
        self.concurrency = max(1, int(concurrency))
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._active = 0
        self.window_s = 24 * 3600

    def ensure_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)
        return self._semaphore

    @property
    def active(self) -> int:
        return self._active

    def acquire(self) -> None:
        self._active += 1

    def release(self) -> None:
        self._active = max(0, self._active - 1)


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid) and geteuid() == 0


def _default_launch_args(*, is_root: bool) -> List[str]:
    """Chromium 启动参数。

    ``--no-sandbox`` 只在 root 下自动补：Chromium 以 root 运行会直接拒绝启动
    （"Running as root without --no-sandbox is not supported"），而线上服务
    进程通常就是 root。非 root 下不加，避免无谓地削弱沙箱。
    """
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        "--disable-dev-shm-usage",
    ]
    if is_root:
        args.append("--no-sandbox")
    return args


class _BrowserFarm:
    """共享一个 Chromium 进程、每任务一个独立 context（proxy 在 context 上）。"""

    def __init__(self, playwright_factory, *, headless: bool, extra_args: List[str]) -> None:
        self._playwright_factory = playwright_factory
        self._headless = headless
        self._extra_args = list(extra_args or ())
        self._pw: Any = None
        self._browser: Any = None
        self._guard = asyncio.Lock()

    async def browser(self) -> Any:
        browser = self._browser
        if browser is not None:
            # Chromium 进程可能已被外部杀掉（OOM/Manually/OOM-killer）：这
            # 时对象还在但已断连，复用只会让每个任务都挂在 TargetClosed 上。
            try:
                if not browser.is_connected():
                    logger.warning("px: chromium disconnected, relaunching")
                    await self._close_browser()
                    browser = None
            except Exception:  # pragma: no cover
                await self._close_browser()
                browser = None
        if browser is not None:
            return browser
        async with self._guard:
            if self._browser is not None:
                return self._browser
            try:
                self._pw = await self._playwright_factory()
                launch_args = _default_launch_args(is_root=_is_root()) + self._extra_args
                self._browser = await self._pw.chromium.launch(
                    headless=self._headless,
                    args=launch_args,
                )
            except Exception:
                # 启动失败要把 playwright driver（node 进程）也清掉，否则
                # 下次 launch 会叠加泄漏 node 子进程
                await self._close_browser()
                raise
            logger.info("px: chromium farm ready (headless={})", self._headless)
            return self._browser

    async def _close_browser(self) -> None:
        browser, self._browser = self._browser, None
        pw, self._pw = self._pw, None
        if browser is not None:
            try:
                await browser.close()
            except Exception:  # pragma: no cover - 关闭竞态
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:  # pragma: no cover
                pass

    async def close(self) -> None:
        await self._close_browser()


# 反自动化探针的最小集合：隐藏 webdriver 标记 + 固定语言。深指纹对抗
# （canvas/WebGL/音效）属 stealth 层选题，明确不在本版范围内。
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['%(locale)s']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(window, 'chrome', {get: () => window.chrome || {runtime: {}}});
"""


def _is_document_response(response: Any) -> bool:
    """主帧响应是否为「文档」本身（而非 script/img/fetch 等子资源）。

    Playwright 的子资源 ``response.frame`` 也指向 main_frame，因此 frame 相等
    不足以区分。拿不到 ``request.resource_type`` 时（测试注入的老式假对象）
    按文档处理，保持既有测试语义不变。
    """
    request = getattr(response, "request", None)
    if request is None:
        return True
    try:
        resource_type = getattr(request, "resource_type", None)
    except Exception:
        return False
    # 某些绑定把 resource_type 实现为方法
    if callable(resource_type):
        try:
            resource_type = resource_type()
        except Exception:
            return False
    if not isinstance(resource_type, str):
        return True
    return resource_type == "document"


def _is_timeout_exc(exc: BaseException) -> bool:
    """异常是否为「超时」而非「连不上」。

    Playwright 的 ``TimeoutError`` 与 ``asyncio.TimeoutError`` 不是同一个类，
    用一个基类判断会漏；按类名后缀识别对两者都成立，且不会误吞别的东西。
    """
    for klass in type(exc).__mro__:
        if klass.__name__.endswith("TimeoutError"):
            return True
    return False


class _Diagnostics:
    """一次任务的传感器执行链观测（需求单 v2 §3.3，全部脱敏）。

    只记「发生了没有 / 状态码 / 名字」，绝不记 cookie 值与消息内容——
    ``px_cookie_names_seen`` 只有名字+域，postMessage 只记类型不记负载。
    """

    def __init__(self) -> None:
        self.iframe_loaded: bool = False
        self.iframe_status: Optional[int] = None
        self.captcha_js_loaded: bool = False
        self.captcha_js_status: Optional[int] = None
        self.main_min_js_loaded: bool = False
        self.main_min_js_status: Optional[int] = None
        self.collector_seen: bool = False
        self.parent_message_seen: bool = False
        self.px_cookie_names_seen: List[str] = []

    def observe_response(self, response: Any) -> None:
        """按 URL 特征归类一条响应（任务 context 内所有 frame 的都算）。"""
        try:
            url = str(getattr(response, "url", "") or "")
            status = int(getattr(response, "status", 0) or 0)
        except Exception:
            return
        low = url.lower()
        if "captcha.js" in low:
            self.captcha_js_loaded = True
            self.captcha_js_status = status
        elif "main.min.js" in low:
            self.main_min_js_loaded = True
            self.main_min_js_status = status
        elif "collector-" in low and ("hsprotect.net" in low or "px-client.net" in low):
            self.collector_seen = True

    def note_iframe_document(self, status: int) -> None:
        self.iframe_loaded = True
        self.iframe_status = status

    def observe_cookie_names(self, cookies: Any) -> None:
        """轮询顺手记 jar 里出现过的 _px* 名字+域（不记值）。"""
        try:
            items = cookies if isinstance(cookies, (list, tuple)) else ()
        except Exception:
            return
        for item in items:
            try:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                if not name.lower().lstrip("_").startswith("px"):
                    continue
                entry = f"{name}({item.get('domain') or '?'})"
                if entry not in self.px_cookie_names_seen:
                    self.px_cookie_names_seen.append(entry)
            except Exception:  # pragma: no cover - 单条坏数据不拖垮采集
                continue

    def snapshot(self) -> Dict[str, Any]:
        return {
            "iframe_loaded": self.iframe_loaded,
            "iframe_status": self.iframe_status,
            "captcha_js_loaded": self.captcha_js_loaded,
            "captcha_js_status": self.captcha_js_status,
            "main_min_js_loaded": self.main_min_js_loaded,
            "main_min_js_status": self.main_min_js_status,
            "collector_seen": self.collector_seen,
            "parent_message_seen": self.parent_message_seen,
            "px_cookie_names_seen": list(self.px_cookie_names_seen),
        }


# 注入到每个 frame 的监听脚本：iframe 收到父页 postMessage 只记「有过」，
# 不记内容（脱敏）。服务侧通过 context.expose_binding 把 window.__pxParentSeen
# 的变化带回 Python——sensor 的 message 事件也可能带 data，只上报布尔。
_PARENT_MESSAGE_PROBE = """
(() => {
  if (window.__pxParentProbe) return;
  window.__pxParentProbe = true;
  window.addEventListener('message', () => {
    try { window.__pxParentSeen = true; } catch (e) { /* ignore */ }
    try { __slidexParentMessage__(); } catch (e) { /* binding 未就绪 */ }
  });
})();
"""


class PxPressSolver:
    """一次调用解一道挑战；并发上限 = ``concurrency``。

    ``context_factory`` 仅供测试注入（返回 context-like），默认由内部
    ``_BrowserFarm`` 生产真实 context。
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        concurrency: int = 5,
        require_1000: bool = True,
        poll_interval_s: float = 2.0,
        playwright_factory: Optional[Callable[[], Awaitable[Any]]] = None,
        context_factory: Optional[Callable[..., Awaitable[Any]]] = None,
        slots: Optional[PxPressSlots] = None,
        queue_timeout_s: float = 30.0,
    ) -> None:
        self._headless = headless
        self._require_1000 = require_1000
        self._poll_interval_s = max(0.25, float(poll_interval_s))
        self._queue_timeout_s = max(0.0, float(queue_timeout_s))
        self._playwright_factory = playwright_factory
        self._context_factory = context_factory
        self._slots = slots or PxPressSlots(concurrency=concurrency)
        self._farm: Optional[_BrowserFarm] = None
        self._closed = False

    # ------------------------------------------------------------------ 对外
    @property
    def active_tasks(self) -> int:
        return self._slots.active

    async def solve(self, request: PxPressRequest) -> PxPressResult:
        started = time.monotonic()
        sem = self._slots.ensure_semaphore()
        if self._queue_timeout_s > 0:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=self._queue_timeout_s)
            except asyncio.TimeoutError:
                # 排队超预算：让调用方拿到明确的 busy，而不是干等到自身超时
                return PxPressResult(
                    False, mode=request.mode, error_kind=ERR_SOLVER_BUSY,
                    error="solver_busy: concurrency slots exhausted", retryable=True,
                )
        else:
            await sem.acquire()
        self._slots.acquire()
        try:
            # vid 缺失不再裸导航沉默超时（需求单 v2 §3.2）：press 先自动补种子，
            # 拿到初始 vid 后种进 caller cookies 再求解。silent 是宿主页自证
            # 模式，vid 本就可选（v1 契约），种子种子套娃没有意义——照常直跑。
            if request.vid or request.mode != "press":
                effective = request
            else:
                seeded_vid = await self._seed_vid(request)
                if not seeded_vid:
                    return PxPressResult(
                        False, mode=request.mode, error_kind=ERR_VID_REQUIRED,
                        error=(
                            "vid is empty and auto-seed via bare silent failed; "
                            "caller should send challengeDetails.deviceId"
                        ),
                        retryable=True, duration_ms=self._ms(started),
                        diagnostics={"vid_seeded": False},
                    )
                cookies = dict(request.cookies)
                cookies.setdefault("_pxvid", seeded_vid)
                effective = dataclasses.replace(
                    request, vid=seeded_vid, cookies=cookies
                )
            return await self._solve_once(effective, started)
        finally:
            self._slots.release()
            sem.release()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._farm is not None:
            await self._farm.close()

    async def _reset_farm(self) -> None:
        """进程没了时丢弃农场，下一个任务自动重建；没有农场则空操作。"""
        farm, self._farm = self._farm, None
        if farm is not None:
            await farm.close()

    @staticmethod
    def _connected(browser: Any) -> bool:
        """浏览器是否还活着；拿不到判断依据（测试注入）按活着处理。"""
        if browser is None:
            return True
        try:
            return bool(browser.is_connected())
        except Exception:  # pragma: no cover
            return False

    # ------------------------------------------------------------------ 内部
    async def _browser(self) -> Any:
        if self._closed:
            raise RuntimeError("solver closed")
        if self._farm is None:
            if self._context_factory is not None:
                # 测试注入：不需要真浏览器农场
                return None
            factory = self._playwright_factory or self._default_playwright_factory()
            self._farm = _BrowserFarm(factory, headless=self._headless, extra_args=[])
        if self._farm is None:
            return None
        return await self._farm.browser()

    async def _solve_once(self, request: PxPressRequest, started: float) -> PxPressResult:
        deadline = started + request.solve_timeout_s
        mode = request.mode
        diag = _Diagnostics()
        try:
            browser = await self._browser()
        except Exception as exc:
            logger.warning(f"px: browser launch failed: {exc}")
            return PxPressResult(
                False, mode=mode, error_kind=ERR_BROWSER_LAUNCH,
                error=f"browser_launch_failed: {exc}", retryable=True,
                duration_ms=self._ms(started),
            )

        # 必须在 try 之前初始化：_context_options / new_context 抛错时
        # finally 仍会引用它们，否则会变成 UnboundLocalError 掩盖真实原因。
        context = None
        page = None
        try:
            context_options = self._context_options(request)
            if self._context_factory is not None:
                context = await self._context_factory(context_options)
            else:
                if browser is None:  # pragma: no cover
                    raise RuntimeError("no browser")
                try:
                    context = await browser.new_context(**context_options)
                except Exception:
                    # 只有「浏览器真的断了」才重置农场：new_context 参数错误
                    # 不能连坐杀掉其它在跑任务的 context
                    connected = True
                    try:
                        connected = bool(browser.is_connected())
                    except Exception:  # pragma: no cover
                        connected = False
                    if not connected and self._farm is not None:
                        logger.warning("px: chromium gone mid-task, resetting farm")
                        await self._farm.close()
                        self._farm = None
                    raise
            add_init_script = getattr(context, "add_init_script", None)
            if callable(add_init_script):
                try:
                    await add_init_script(
                        _STEALTH_INIT_SCRIPT
                        % {"locale": context_options.get("locale") or _FALLBACK_LOCALE}
                    )
                    # 父页→iframe 的 postMessage 只记「发生过」，不记内容
                    await add_init_script(_PARENT_MESSAGE_PROBE)
                except Exception as exc:  # pragma: no cover - 注入失败不阻断
                    logger.debug(f"px: stealth init script skipped: {exc}")
            self._expose_parent_probe(context, diag)
            specs = self._cookie_specs(request)
            if specs:
                try:
                    await context.add_cookies(specs)
                    logger.info(
                        "px: injected cookies {} (domains redacted, {} specs)",
                        sorted({s["name"] for s in specs}), len(specs),
                    )
                except Exception as exc:  # pragma: no cover - 坏 cookie 名/域
                    logger.warning(f"px: add_cookies skipped: {exc}")

            page = await context.new_page()
            page_status: Dict[str, int] = {}

            async def _on_response(response: Any) -> None:
                diag.observe_response(response)
                try:
                    frame = response.frame
                    if frame is not None and frame == page.main_frame:
                        # 只认主帧的「文档」响应：子资源（script/img/fetch）的
                        # response.frame 同样是 main_frame，若无条件记录，一个
                        # 404 的阻塞脚本会把健康页判死，一个 200 的脚本又会
                        # 把真 404 页从 fail-fast 拖成超时（实测两个方向都会错）。
                        if _is_document_response(response):
                            # 重定向链后 target 是 302，真正渲染的是最终文档
                            # （可能是 PX 的 403 拦截页），所以留最后一条。
                            page_status["last"] = int(response.status)
                            # press 双页结构下 iframe 是子 frame，这里到不了；
                            # 但 silent 单页时 target 就是主帧文档——状态码
                            # 兼作 iframe_loaded 的观测（合成页测试同源）。
                except Exception:  # pragma: no cover
                    pass

            page.on("response", _on_response)

            if mode == "press":
                # 双页结构（需求单 v2 §3.1）：顶层页先落 websiteURL 域并做
                # fail-fast 判死（403/404/407/5xx），通过后才注入 challenge
                # iframe——否则一个 404 的 challengeUrl 也会白等 iframe 就绪。
                host_kwargs = self._nav_kwargs(request, _HOST_NAV_TIMEOUT_S)
                nav_kind, nav_reason = await self._goto(
                    page, request.website_url, host_kwargs
                )
                if not nav_kind:
                    # 首屏状态判死不分模式（0.9.1 修：silent 丢判死是线上
                    # deep_check C5 回归的根因，407/404 烧满预算变可重试超时）
                    fatal = self._first_paint_kill(
                        started=started, mode=mode, diag=diag,
                        status=self._pick_status(page_status),
                    )
                    if fatal is not None:
                        return fatal
                    nav_kind, nav_reason = await self._attach_iframe(request, page, diag)
            else:
                nav = await self._navigate_silent(request, page, page_status, diag)
                nav_kind, nav_reason = nav
                if not nav_kind:
                    fatal = self._first_paint_kill(
                        started=started, mode=mode, diag=diag,
                        status=self._pick_status(page_status),
                    )
                    if fatal is not None:
                        return fatal
            # 导航失败按「超时 / 连不上」分两类上报；首屏判死已在上面做过。
            if nav_kind == ERR_CHALLENGE_TIMEOUT:
                return self._fail(
                    started, mode, diag, ERR_CHALLENGE_TIMEOUT,
                    f"challenge page did not load within budget: {nav_reason}",
                    retryable=True,
                )
            if nav_kind:
                return self._fail(
                    started, mode, diag, ERR_CHALLENGE_LOAD,
                    f"challenge page unreachable: {nav_reason}", retryable=True,
                )

            blocked_since: Optional[float] = None
            read_failures = 0
            while time.monotonic() < deadline:
                cookies = await self._read_cookies(context, log_failure=(read_failures == 0))
                if cookies is None:
                    # cookie 读不出来：context/browser 多半已经死了（Chromium 被
                    # OOM 杀、context 被关）。继续轮询只会把整个预算耗光并刷屏
                    # 警告，必须尽快失败并把农场重置，让下一个任务拿新进程。
                    read_failures += 1
                    if read_failures == 1 and not self._connected(browser):
                        logger.warning("px: chromium gone mid-task, resetting farm")
                        await self._reset_farm()
                    if read_failures >= 2 or not self._connected(browser):
                        return self._fail(
                            started, mode, diag, ERR_INTERNAL,
                            "browser/context lost mid-task (cookie read failed)",
                            retryable=True,
                        )
                    await asyncio.sleep(request.poll_interval_s or self._poll_interval_s)
                    continue
                read_failures = 0
                diag.observe_cookie_names(cookies)
                tokens = extract_px_tokens(cookies)
                px3 = tokens.get("px3", "")
                if px3 and is_pass_token(px3):
                    tokens = self._fill_from_request(tokens, request)
                    missing = [f for f in ("px3", "pxde", "pxvid", "pxcts") if not tokens.get(f)]
                    logger.info(
                        "px: {} pass token px3={} missing={} cookies_seen={}",
                        mode, redact_token(px3), missing, diag.px_cookie_names_seen,
                    )
                    return PxPressResult(
                        True, tokens=tokens, mode=mode, duration_ms=self._ms(started),
                        missing=missing, diagnostics=diag.snapshot(),
                    )
                if px3 and not is_pass_token(px3):
                    # 有非通过级 token：给 PX 一点时间升级，别急着当成功
                    if blocked_since is None:
                        blocked_since = time.monotonic()
                        logger.info(f"px: {mode} px3 without :1000: px3={redact_token(px3)}")
                    if time.monotonic() - blocked_since > _BLOCKED_GRACE_S:
                        return self._fail(
                            started, mode, diag, ERR_TOKEN_BLOCKED,
                            "px3 lacks :1000: (possibly IP/appId blocked)", retryable=True,
                        )
                else:
                    blocked_since = None
                await asyncio.sleep(request.poll_interval_s or self._poll_interval_s)

            return self._fail(
                started, mode, diag, ERR_TOKEN_TIMEOUT,
                "no pass token within budget", retryable=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"px: {mode} solve error: {exc}")
            return self._fail(
                started, mode, diag, ERR_INTERNAL,
                f"solver_internal_error: {exc}", retryable=True,
            )
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:  # pragma: no cover
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:  # pragma: no cover
                    pass

    @staticmethod
    def _fail(
        started: float,
        mode: str,
        diag: _Diagnostics,
        kind: str,
        message: str,
        *,
        retryable: bool,
    ) -> PxPressResult:
        """失败终态：统一把诊断观测挂上，调用方在 fail 响应里直接可见。"""
        return PxPressResult(
            False, mode=mode, error_kind=kind, error=message,
            retryable=retryable, duration_ms=PxPressSolver._ms(started),
            diagnostics=diag.snapshot(),
        )

    @staticmethod
    def _expose_parent_probe(context: Any, diag: _Diagnostics) -> None:
        """把页面侧的 postMessage 观测接到 _Diagnostics。

        ``expose_binding`` 在 context 级别生效（每个 frame 都有）；拿不到该方法
        （测试注入的假 context）就静默跳过——诊断是尽力而为，不能因采集失败
        拖垮求解。
        """
        expose = getattr(context, "expose_binding", None)
        if not callable(expose):
            return

        async def _on_parent_message(source: Any) -> None:
            diag.parent_message_seen = True

        try:
            expose("__slidexParentMessage__", _on_parent_message)
        except Exception:  # pragma: no cover - 重复绑定等竞态
            pass

    async def _navigate_silent(
        self,
        request: PxPressRequest,
        page: Any,
        page_status: Dict[str, int],
        diag: _Diagnostics,
    ) -> Tuple[str, str]:
        """silent 模式：直接导航 websiteURL（宿主页本身就是传感器页）。

        返回 ``(kind, reason)``：``kind == ""`` 表示成功。
        """
        kind, reason = await self._goto(
            page, request.website_url, self._nav_kwargs(request, _NAV_TIMEOUT_S),
            referer=request.referer,
        )
        if not kind:
            # silent 的主帧文档就是 challenge 宿主，200 即视为 iframe_loaded
            diag.note_iframe_document(page_status.get("last", 0))
        return kind, reason

    def _nav_kwargs(self, request: PxPressRequest, budget_s: float) -> Dict[str, Any]:
        return {
            "wait_until": "domcontentloaded",
            "timeout": int(min(budget_s, request.solve_timeout_s) * 1000),
        }

    @staticmethod
    async def _goto(
        page: Any, url: str, kwargs: Dict[str, Any], *, referer: str = ""
    ) -> Tuple[str, str]:
        """单页导航；失败时区分「超时」与「连不上」（同 0.8.4 的语义）。"""
        call_kwargs = dict(kwargs)
        if referer:
            call_kwargs["referer"] = referer
        try:
            await page.goto(url, **call_kwargs)
            return "", ""
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:300]
            kind = ERR_CHALLENGE_TIMEOUT if _is_timeout_exc(exc) else ERR_CHALLENGE_LOAD
            # proxy CONNECT 失败 / DNS / 证书 / 不安全端口都落到这里
            logger.warning(f"px: navigate {url!r:.80} failed ({kind}): {reason}")
            return kind, reason

    async def _attach_iframe(
        self, request: PxPressRequest, page: Any, diag: _Diagnostics
    ) -> Tuple[str, str]:
        """把 challengeUrl 作为 iframe 插入宿主页，等它的文档出现。

        iframe 装载情况记进诊断（iframe_loaded / iframe_status）；就绪判定是
        「challengeUrl 的 frame 拿到 document 响应」，拿不到时并不 fail——
        只要传感器加载了（captcha.js / main.min.js 任一出现）就有解出的机会。
        """
        loop = asyncio.get_event_loop()
        iframe_ready: asyncio.Future = loop.create_future()
        challenge = request.challenge_url
        main_frame = getattr(page, "main_frame", None)

        async def _on_iframe_response(response: Any) -> None:
            try:
                frame = response.frame
                if frame is None or frame == main_frame:
                    return
                if not _is_document_response(response):
                    return
                frame_url = str(getattr(frame, "url", "") or "")
                if challenge and (frame_url.startswith(challenge) or challenge.startswith(frame_url)):
                    diag.note_iframe_document(int(getattr(response, "status", 0) or 0))
                    if not iframe_ready.done():
                        iframe_ready.set_result(True)
            except Exception:  # pragma: no cover
                pass

        try:
            page.on("response", _on_iframe_response)
        except Exception:  # pragma: no cover - 事件注册失败不阻断
            pass

        inject = (
            "(src) => {"
            "const f = document.createElement('iframe');"
            "f.src = src; f.id = 'px-captcha-host';"
            "f.setAttribute('style', %r);"
            "(document.body || document.documentElement).appendChild(f);"
            "return f.id;}" % _IFRAME_STYLE
        )
        try:
            await page.evaluate(inject, challenge)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(f"px: iframe inject failed: {reason}")
            return ERR_CHALLENGE_LOAD, f"iframe inject failed: {reason}"

        budget = min(_IFRAME_WAIT_S, max(2.0, request.solve_timeout_s / 4))
        try:
            await asyncio.wait_for(iframe_ready, timeout=budget)
        except asyncio.TimeoutError:
            # iframe 文档没等到：大概率 challengeUrl 不可达或被代理拦。
            # 交给上层按诊断判断（若 captcha.js 都没出现，基本无解）。
            logger.warning(f"px: iframe document not seen within {budget:.0f}s")
        return "", ""

    @staticmethod
    def _pick_status(page_status: Dict[str, int]) -> Optional[int]:
        """最后一条主帧状态：重定向链后这是最终文档（PX 拦截页落在最后）。"""
        if not page_status:
            return None
        return page_status.get("last")

    def _first_paint_kill(
        self,
        *,
        started: float,
        mode: str,
        diag: _Diagnostics,
        status: Optional[int],
    ) -> Optional[PxPressResult]:
        """首屏状态判死，press/silent 共用（与模式无关的页面层事实）。

        返回 ``None`` 表示页面健康，继续求解；否则给出确定性失败结果：
        403/429/404 是拦截/写错 URL（不重试），407 是代理要认证（不重试，
        同 payload 重试永远不会好），>=500 是上游故障（可重试）。
        """
        if status in (403, 429, 404):
            return self._fail(
                started, mode, diag, ERR_CHALLENGE_HTTP,
                f"host page status={status}", retryable=False,
            )
        if status == 407:
            return self._fail(
                started, mode, diag, ERR_PROXY_AUTH,
                "proxy requires authentication (407)", retryable=False,
            )
        if status is not None and status >= 500:
            return self._fail(
                started, mode, diag, ERR_CHALLENGE_HTTP,
                f"upstream responded {status}", retryable=True,
            )
        return None

    async def _read_cookies(
        self, context: Any, *, log_failure: bool = True
    ) -> Optional[List[Dict[str, Any]]]:
        """读整个 context cookie jar；失败返回 None（context/browser 已死）。"""
        try:
            return await context.cookies()
        except Exception as exc:
            # 失败说明 context/browser 可能已死：返回 None 交给上层快速失败，
            # 只在首次刷一条警告，避免每个 poll 周期都刷屏
            if log_failure:
                logger.warning(f"px: cookie read failed: {exc}")
            return None

    async def _seed_vid(self, request: PxPressRequest) -> Optional[str]:
        """vid 缺失时自动补种子（需求单 v2 §3.2 策略二）。

        用裸 silent 在 websiteURL 上跑一小段：PX main.min.js 会从
        collector 领一个初始 vid 写进 jar（_pxvid）。种子只需要 vid 本身，
        不需要通过级 px3——所以不复用 ``_solve_once``（它只把成功终态的
        token 吐出来，press 才需要 :1000:），单独轮询 _pxvid 出现即回。

        预算硬顶 25s；拿不到返回 None。
        """
        if not request.website_url:
            return None
        seeded = dataclasses.replace(
            request, vid="", mode="silent", challenge_url="",
            solve_timeout_s=25.0, poll_interval_s=max(0.5, request.poll_interval_s),
        )
        logger.info("px: vid missing, seeding via bare silent on {}", request.website_url)
        vid = await self._harvest_vid(seeded)
        if vid:
            logger.info("px: vid seeded ok ({})", redact_token(vid))
        else:
            logger.warning("px: vid seed failed (no _pxvid within budget)")
        return vid or None

    async def _harvest_vid(self, request: PxPressRequest) -> str:
        """裸 silent 跑一段，从 jar 里等 _pxvid 首次出现。只做种子，不求 token。"""
        started = time.monotonic()
        deadline = started + request.solve_timeout_s
        browser = None
        context = None
        page = None
        try:
            browser = await self._browser()
            context_options = self._context_options(request)
            if self._context_factory is not None:
                context = await self._context_factory(context_options)
            else:
                if browser is None:  # pragma: no cover
                    raise RuntimeError("no browser")
                context = await browser.new_context(**context_options)
            add_init_script = getattr(context, "add_init_script", None)
            if callable(add_init_script):
                try:
                    await add_init_script(
                        _STEALTH_INIT_SCRIPT
                        % {"locale": context_options.get("locale") or _FALLBACK_LOCALE}
                    )
                except Exception:  # pragma: no cover
                    pass
            specs = self._cookie_specs(request)
            if specs:
                try:
                    await context.add_cookies(specs)
                except Exception:  # pragma: no cover
                    pass
            page = await context.new_page()
            nav_kind, nav_reason = await self._goto(
                page, request.website_url, self._nav_kwargs(request, _NAV_TIMEOUT_S)
            )
            if nav_kind:
                logger.warning(f"px: vid seed navigate failed: {nav_reason}")
                return ""
            while time.monotonic() < deadline:
                cookies = await self._read_cookies(context, log_failure=False)
                if cookies is None:
                    return ""
                tokens = extract_px_tokens(cookies)
                if tokens.get("pxvid"):
                    return tokens["pxvid"]
                await asyncio.sleep(request.poll_interval_s or self._poll_interval_s)
            return ""
        except Exception as exc:
            logger.warning(f"px: vid seed error: {exc}")
            return ""
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:  # pragma: no cover
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:  # pragma: no cover
                    pass

    def _fill_from_request(
        self, tokens: Dict[str, str], request: PxPressRequest
    ) -> Dict[str, str]:
        out = dict(tokens)
        if not out.get("pxvid") and request.vid:
            # vid 就是调用方手里的 deviceId（= PX vid），缺失时补上合理
            out["pxvid"] = request.vid
        return out

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    # ----------------------------------------------------------- 选项构造
    def _context_options(self, request: PxPressRequest) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "user_agent": request.user_agent,
            "locale": request.locale or _FALLBACK_LOCALE,
            "ignore_https_errors": True,
            "viewport": {"width": 1280, "height": 800},
            "color_scheme": "light",
        }
        if request.timezone:
            options["timezone_id"] = request.timezone
        if request.uses_proxy:
            options["proxy"] = request.playwright_proxy()
        return options

    def _cookie_specs(self, request: PxPressRequest) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        site_url = self._site_url_for_cookies(request)
        for name, value in request.cookies.items():
            if not name or not value:
                continue
            base_name = str(name).lstrip("_").lower()
            if base_name in ("px3", "pxde", "pxvid", "pxcts"):
                # PX 同域读 cookie：同时放到站点域与 hsprotect 域
                specs.append({"name": name, "value": value, "domain": ".hsprotect.net", "path": "/"})
            if site_url:
                spec: Dict[str, Any] = {"name": name, "value": value}
                if site_url.startswith("http"):
                    spec["url"] = site_url
                specs.append(spec)
        return specs

    @staticmethod
    def _site_url_for_cookies(request: PxPressRequest) -> str:
        url = (request.website_url or "").strip()
        if url.startswith("https://") or url.startswith("http://"):
            return url
        return "https://signup.live.com/"

    @staticmethod
    def _default_playwright_factory():
        from playwright.async_api import async_playwright

        async def _start():
            return await async_playwright().start()

        return _start