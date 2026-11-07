"""PxPressSolver — 用假 browser/context 覆盖全部分支，零网络零浏览器。

覆盖点：
- 通过级 token（px3 含 :1000:）→ success，四件套齐全
- 非通过级 px3 → 宽限后判 blocked（retryable=True）
- 一直没 token → timeout
- 浏览器启动失败 → browser_launch_failed（retryable）
- 并发槽耗尽 → solver_busy
- proxy/时区/locale/UA 真的传进 context options
- navigate 403 → challenge_http_error 且不可重试
- 缺字段用 request.vid/uuid 兜底
"""

import asyncio
import time

import pytest

from slidex.px.models import (
    ERR_BROWSER_LAUNCH,
    ERR_CHALLENGE_HTTP,
    ERR_INTERNAL,
    ERR_SOLVER_BUSY,
    ERR_TOKEN_BLOCKED,
    ERR_TOKEN_TIMEOUT,
    PxPressRequest,
)
from slidex.px.solver import (
    PxPressSlots,
    PxPressSolver,
    _BrowserFarm,
    _default_launch_args,
)

_PASS_PX3 = "1:a:1000:abcdef::"


def _request(**overrides) -> PxPressRequest:
    payload = {
        "appId": "PXzC5j78di",
        "challengeUrl": "https://iframe.hsprotect.net/challenge",
        "websiteURL": "https://signup.live.com/",
        "country": "US",
        "pollInterval": 0.25,
        "timeout": 10,
    }
    payload.update(overrides)
    return PxPressRequest.from_payload(payload)


class _FakePage:
    def __init__(self, *, goto_exc=None, status=200):
        self.goto_calls = []
        self._goto_exc = goto_exc
        self._status = status
        self.closed = False
        self._response_handlers = []
        self.main_frame = object()

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self._goto_exc is not None:
            raise self._goto_exc
        # 模拟一次主帧响应，供状态判定
        handler = self._response_handlers[0] if self._response_handlers else None
        if handler is not None:
            await handler(_FakeResponse(self._status, url, self.main_frame))
        return None

    def on(self, event, handler):
        if event == "response":
            self._response_handlers.append(handler)

    async def close(self):
        self.closed = True


class _FakeResponse:
    def __init__(self, status, url, frame):
        self.status = status
        self.url = url
        self.frame = frame


class _FakeContext:
    """可编排 cookie 序列：每次 cookies() 取下一个快照，用尽后重复最后一个。"""

    def __init__(self, snapshots, *, page=None):
        self._snapshots = list(snapshots)
        self._index = 0
        self.calls = 0
        self.closed = False
        self.init_scripts = []
        self.added_cookies = []
        self.page = page or _FakePage()

    async def cookies(self):
        snap = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        self.calls += 1
        return snap

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    async def add_cookies(self, specs):
        self.added_cookies.extend(specs)

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


def _pass_cookies():
    return [
        {"name": "_px3", "value": _PASS_PX3},
        {"name": "_pxde", "value": "{}"},
        {"name": "_pxvid", "value": "vid-1"},
        {"name": "_pxcts", "value": "cts-1"},
    ]


async def test_pass_token_succeeds_and_closes_context():
    context = _FakeContext([_pass_cookies()])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    assert result.tokens["px3"] == _PASS_PX3
    assert result.missing == []
    assert result.mode == "press"
    assert context.closed is True
    assert context.page.closed is True


async def _resolve(value):
    return value


async def test_non_pass_token_blocks_after_grace(monkeypatch):
    # 缩短宽限，避免测试真的等 10s
    monkeypatch.setattr("slidex.px.solver._BLOCKED_GRACE_S", 0.05)
    cookies = [{"name": "_px3", "value": "1:a:200:low::"}]
    context = _FakeContext([cookies])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_TOKEN_BLOCKED
    assert result.retryable_error is True


async def test_no_token_times_out():
    context = _FakeContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    request = _request(timeout=10)
    # 通过 monkeypatch 收紧 deadline：直接改 request 的 timeout 由 10 起跳，
    # 这里改用极小 timeout 的模型实例
    request.solve_timeout_s = 0.3
    started = time.monotonic()
    result = await solver.solve(request)

    assert result.success is False
    assert result.error_kind == ERR_TOKEN_TIMEOUT
    assert result.retryable_error is True
    assert time.monotonic() - started < 5


async def test_browser_launch_failure_is_retryable():
    async def _boom():
        raise RuntimeError("no chromium")

    solver = PxPressSolver(playwright_factory=_boom)

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_BROWSER_LAUNCH
    assert result.retryable_error is True


async def test_solver_busy_when_slots_exhausted():
    slots = PxPressSlots(concurrency=1)
    # 占掉唯一槽位
    await slots.ensure_semaphore().acquire()

    context = _FakeContext([_pass_cookies()])
    solver = PxPressSolver(
        context_factory=lambda opts: _resolve(context),
        slots=slots,
        queue_timeout_s=0.05,
    )

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_SOLVER_BUSY
    assert result.retryable_error is True
    # 归还槽位，避免污染
    slots.ensure_semaphore().release()


async def test_context_options_carry_proxy_timezone_locale_ua():
    captured = {}

    async def _factory(options):
        captured.update(options)
        return _FakeContext([_pass_cookies()])

    solver = PxPressSolver(context_factory=_factory)
    request = _request(
        country="JP",
        proxy={"host": "10.0.0.1", "port": 8080, "login": "u", "password": "p"},
        userAgent="UA/custom",
    )

    result = await solver.solve(request)

    assert result.success is True
    assert captured["timezone_id"] == "Asia/Tokyo"
    assert captured["locale"] == "ja-JP"
    assert captured["user_agent"] == "UA/custom"
    assert captured["proxy"] == {
        "server": "http://10.0.0.1:8080",
        "username": "u",
        "password": "p",
    }


async def test_no_proxy_means_no_proxy_key():
    captured = {}

    async def _factory(options):
        captured.update(options)
        return _FakeContext([_pass_cookies()])

    solver = PxPressSolver(context_factory=_factory)
    await solver.solve(_request())

    assert "proxy" not in captured


async def test_stealth_init_script_injected_with_locale():
    context = _FakeContext([_pass_cookies()])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    await solver.solve(_request(country="JP"))

    assert len(context.init_scripts) == 1
    assert "ja-JP" in context.init_scripts[0]
    assert "webdriver" in context.init_scripts[0]


async def test_px_cookies_injected_on_both_domains():
    context = _FakeContext([_pass_cookies()])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))
    request = _request(cookies={"_px3": _PASS_PX3, "other": "x"})

    await solver.solve(request)

    hsprotect = [s for s in context.added_cookies if s.get("domain") == ".hsprotect.net"]
    with_url = [s for s in context.added_cookies if s.get("url")]
    assert any(s["name"] == "_px3" for s in hsprotect)
    assert any(s["name"] == "_px3" for s in with_url)
    assert any(s["name"] == "other" for s in with_url)
    # 非 PX cookie 不应被打到 hsprotect 域
    assert not any(s["name"] == "other" for s in hsprotect)


async def test_navigate_403_maps_to_challenge_http_error():
    page = _FakePage(status=403)
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    # 403 是确定性拦截，不该让调用方盲目重试
    assert result.retryable_error is False


async def test_navigate_failure_without_status_is_timeout():
    page = _FakePage(goto_exc=RuntimeError("net::ERR_PROXY_CONNECTION_FAILED"))
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == "challenge_load_timeout"
    assert result.retryable_error is True


async def test_referer_passed_on_press_navigation():
    page = _FakePage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    await solver.solve(_request())

    target, kwargs = page.goto_calls[0]
    assert target == "https://iframe.hsprotect.net/challenge"
    assert kwargs["referer"] == "https://signup.live.com/"
    assert kwargs["wait_until"] == "domcontentloaded"


async def test_missing_fields_filled_from_vid_and_uuid():
    cookies = [{"name": "_px3", "value": _PASS_PX3}]
    context = _FakeContext([cookies])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))
    request = _request(vid="vid-from-payload", uuid="uuid-from-payload")

    result = await solver.solve(request)

    assert result.success is True
    assert result.tokens["pxvid"] == "vid-from-payload"
    assert result.tokens["pxcts"] == "uuid-from-payload"
    assert result.missing == ["pxde"]


async def test_cookie_read_failure_is_internal_error():
    class _BrokenContext(_FakeContext):
        async def cookies(self):
            raise RuntimeError("target closed")

    context = _BrokenContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    # _read_tokens 吞掉异常返回空 token → 最终 timeout 而非 crash
    assert result.success is False
    assert result.error_kind in {ERR_TOKEN_TIMEOUT, ERR_INTERNAL}


async def test_cancellation_propagates():
    class _HangContext(_FakeContext):
        async def new_page(self):
            await asyncio.sleep(3600)

    context = _HangContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(solver.solve(_request()), timeout=0.2)


async def test_close_is_idempotent():
    solver = PxPressSolver(context_factory=lambda opts: _resolve(_FakeContext([[]])))
    await solver.close()
    await solver.close()


async def test_active_tasks_tracks_in_flight_solve():
    started = asyncio.Event()

    class _SlowContext(_FakeContext):
        async def new_page(self):
            started.set()
            await asyncio.sleep(0.2)
            return self.page

    context = _SlowContext([_pass_cookies()])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    task = asyncio.create_task(solver.solve(_request()))
    await started.wait()
    assert solver.active_tasks == 1
    await task
    assert solver.active_tasks == 0


# ────────────────────── Chromium 启动参数（线上 root） ──────────────────────


def test_root_gets_no_sandbox_flag():
    """线上服务进程通常是 root：不带 --no-sandbox 时 Chromium 直接拒绝启动。"""
    args = _default_launch_args(is_root=True)
    assert "--no-sandbox" in args
    assert "--disable-dev-shm-usage" in args
    assert "--disable-blink-features=AutomationControlled" in args


def test_non_root_keeps_sandbox():
    args = _default_launch_args(is_root=False)
    assert "--no-sandbox" not in args
    assert "--disable-dev-shm-usage" in args


# ────────────────────── Chromium 崩溃自愈 ──────────────────────


class _FakeBrowserObj:
    def __init__(self, *, connected: bool = True) -> None:
        self._connected = connected
        self.close_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False


class _FakeChromium:
    def __init__(self, browsers) -> None:
        self._browsers = list(browsers)
        self.launches = 0

    async def launch(self, **kwargs):
        self.launches += 1
        item = self._browsers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakePw:
    def __init__(self, browsers) -> None:
        self.chromium = _FakeChromium(browsers)
        self.stopped = 0

    async def stop(self) -> None:
        self.stopped += 1


async def test_dead_browser_is_relaunched():
    """OOM 杀掉 Chromium 后，下一次求解必须拿到新进程而不是永久失败。"""
    first = _FakeBrowserObj()
    second = _FakeBrowserObj()
    pw = _FakePw([first, second])
    farm = _BrowserFarm(lambda: _resolve(pw), headless=True, extra_args=[])

    got = await farm.browser()
    assert got is first
    first._connected = False  # 被外部杀掉

    got2 = await farm.browser()
    assert got2 is second
    assert first.close_calls == 1
    assert pw.chromium.launches == 2


async def test_launch_failure_does_not_leak_driver():
    """首发失败要把 playwright driver 一起关掉，否则 node 进程逐个泄漏。"""
    pw_bad = _FakePw([RuntimeError("no chromium")])
    pw_good = _FakePw([_FakeBrowserObj()])
    made = [pw_bad, pw_good]

    async def _factory():
        return made.pop(0)

    farm = _BrowserFarm(_factory, headless=True, extra_args=[])

    with pytest.raises(RuntimeError):
        await farm.browser()
    assert farm._pw is None and farm._browser is None
    assert pw_bad.stopped == 1

    browser = await farm.browser()
    assert browser is not None
    assert pw_bad.chromium.launches == 1


async def test_browser_gone_mid_task_resets_farm_and_is_retryable():
    class _DeadOnContext(_FakeBrowserObj):
        async def new_context(self, **kwargs):
            self._connected = False
            raise RuntimeError("Target page, context or browser has been closed")

    dead = _DeadOnContext()
    pw = _FakePw([dead])
    solver = PxPressSolver(playwright_factory=lambda: _resolve(pw))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.retryable_error is True
    # 农场必须被重置，否则后续任务全挂在断连的 browser 上
    assert solver._farm is None
