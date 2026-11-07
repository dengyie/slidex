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
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from .cookies import extract_px_tokens, is_pass_token, redact_token
from .models import (
    ERR_BROWSER_LAUNCH,
    ERR_CHALLENGE_HTTP,
    ERR_CHALLENGE_TIMEOUT,
    ERR_INTERNAL,
    ERR_SOLVER_BUSY,
    ERR_TOKEN_BLOCKED,
    ERR_TOKEN_TIMEOUT,
    PxPressRequest,
    PxPressResult,
)

# 已打开 challenge 页但只拿到非通过级 px3 时的宽限：持续这么久仍无 :1000:
# 就按「被拦」（px_blocked）fail，而不是把无段 token 当成功交给调用方。
_BLOCKED_GRACE_S = 10.0
# challenge 页首跳预算：hsprotect 首屏不该比这更慢，超过即视为没连通
_NAV_TIMEOUT_S = 45.0
# Playwright 时区/语言校验失败时的兜底（映射表之外的未知值会抛）
_FALLBACK_TIMEZONE = "America/New_York"
_FALLBACK_LOCALE = "en-US"


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
            return await self._solve_once(request, started)
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
                except Exception as exc:  # pragma: no cover - 注入失败不阻断
                    logger.debug(f"px: stealth init script skipped: {exc}")
            specs = self._cookie_specs(request)
            if specs:
                try:
                    await context.add_cookies(specs)
                except Exception as exc:  # pragma: no cover - 坏 cookie 名/域
                    logger.warning(f"px: add_cookies skipped: {exc}")

            page = await context.new_page()
            page_status: Dict[str, int] = {}

            async def _on_response(response: Any) -> None:
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
                except Exception:  # pragma: no cover
                    pass

            page.on("response", _on_response)

            nav_ok = await self._navigate(request, page)
            # 首屏状态码本身就能判死：403/429/404 是确定性终止（PX 拦截 /
            # challengeUrl 写错），>=500 是上游故障（可重试）。这些都不该再
            # 耗满预算去轮询 cookie；导航失败一律按可重试超时处理。
            status = self._pick_status(page_status)
            if status in (403, 429, 404):
                return PxPressResult(
                    False, mode=mode, error_kind=ERR_CHALLENGE_HTTP,
                    error=f"challenge page status={status}",
                    retryable=False, duration_ms=self._ms(started),
                )
            if status == 407:
                # 代理要认证。这既不是页面的问题，也不是"还没种上 token"——
                # 浏览器把 407 当文档渲染了，轮询到预算耗尽只会得到一个
                # 误导性的 px_token_timeout，而同一个 payload 重试永远一样。
                return PxPressResult(
                    False, mode=mode, error_kind=ERR_CHALLENGE_HTTP,
                    error="proxy requires authentication (407)",
                    retryable=False, duration_ms=self._ms(started),
                )
            if status is not None and status >= 500:
                return PxPressResult(
                    False, mode=mode, error_kind=ERR_CHALLENGE_HTTP,
                    error=f"upstream responded {status}",
                    retryable=True, duration_ms=self._ms(started),
                )
            if not nav_ok:
                return PxPressResult(
                    False, mode=mode, error_kind=ERR_CHALLENGE_TIMEOUT,
                    error="challenge page did not load",
                    retryable=True, duration_ms=self._ms(started),
                )

            blocked_since: Optional[float] = None
            read_failures = 0
            while time.monotonic() < deadline:
                tokens = await self._read_tokens(context, log_failure=(read_failures == 0))
                if tokens is None:
                    # cookie 读不出来：context/browser 多半已经死了（Chromium 被
                    # OOM 杀、context 被关）。继续轮询只会把整个预算耗光并刷屏
                    # 警告，必须尽快失败并把农场重置，让下一个任务拿新进程。
                    read_failures += 1
                    if read_failures == 1 and not self._connected(browser):
                        logger.warning("px: chromium gone mid-task, resetting farm")
                        await self._reset_farm()
                    if read_failures >= 2 or not self._connected(browser):
                        return PxPressResult(
                            False, mode=mode, error_kind=ERR_INTERNAL,
                            error="browser/context lost mid-task (cookie read failed)",
                            retryable=True, duration_ms=self._ms(started),
                        )
                    await asyncio.sleep(request.poll_interval_s or self._poll_interval_s)
                    continue
                read_failures = 0
                px3 = tokens.get("px3", "")
                if px3 and is_pass_token(px3):
                    tokens = self._fill_from_request(tokens, request)
                    missing = [f for f in ("px3", "pxde", "pxvid", "pxcts") if not tokens.get(f)]
                    logger.info(
                        "px: {} pass token px3={} missing={}",
                        mode, redact_token(px3), missing,
                    )
                    return PxPressResult(
                        True, tokens=tokens, mode=mode, duration_ms=self._ms(started),
                        missing=missing,
                    )
                if px3 and not is_pass_token(px3):
                    # 有非通过级 token：给 PX 一点时间升级，别急着当成功
                    if blocked_since is None:
                        blocked_since = time.monotonic()
                        logger.info(f"px: {mode} px3 without :1000: px3={redact_token(px3)}")
                    if time.monotonic() - blocked_since > _BLOCKED_GRACE_S:
                        return PxPressResult(
                            False, mode=mode, error_kind=ERR_TOKEN_BLOCKED,
                            error="px3 lacks :1000: (possibly IP/appId blocked)",
                            retryable=True, duration_ms=self._ms(started),
                        )
                else:
                    blocked_since = None
                await asyncio.sleep(request.poll_interval_s or self._poll_interval_s)

            return PxPressResult(
                False, mode=mode, error_kind=ERR_TOKEN_TIMEOUT,
                error="no pass token within budget",
                retryable=True, duration_ms=self._ms(started),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"px: {mode} solve error: {exc}")
            return PxPressResult(
                False, mode=mode, error_kind=ERR_INTERNAL,
                error=f"solver_internal_error: {exc}", retryable=True,
                duration_ms=self._ms(started),
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

    async def _navigate(self, request: PxPressRequest, page: Any) -> bool:
        target = request.target_url
        nav_timeout_ms = int(min(_NAV_TIMEOUT_S, request.solve_timeout_s) * 1000)
        try:
            kwargs: Dict[str, Any] = {
                "wait_until": "domcontentloaded",
                "timeout": nav_timeout_ms,
            }
            if request.referer:
                kwargs["referer"] = request.referer
            await page.goto(target, **kwargs)
            return True
        except Exception as exc:
            # proxy CONNECT / DNS / 证书失败都会落到这里；统一按可重试超时处理
            logger.warning(f"px: navigate {target!r:.80} failed: {exc}")
            return False

    @staticmethod
    def _pick_status(page_status: Dict[str, int]) -> Optional[int]:
        """最后一条主帧状态：重定向链后这是最终文档（PX 拦截页落在最后）。"""
        if not page_status:
            return None
        return page_status.get("last")

    async def _read_tokens(
        self, context: Any, *, log_failure: bool = True
    ) -> Optional[Dict[str, str]]:
        try:
            cookies = await context.cookies()
        except Exception as exc:
            # 失败说明 context/browser 可能已死：返回 None 交给上层快速失败，
            # 只在首次刷一条警告，避免每个 poll 周期都刷屏
            if log_failure:
                logger.warning(f"px: cookie read failed: {exc}")
            return None
        return extract_px_tokens(cookies)

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