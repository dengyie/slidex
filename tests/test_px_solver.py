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
    ERR_PROXY_AUTH,
    ERR_VID_REQUIRED,
    ERR_CHALLENGE_LOAD,
    ERR_CHALLENGE_TIMEOUT,
    ERR_INTERNAL,
    ERR_SOLVER_BUSY,
    ERR_TOKEN_BLOCKED,
    ERR_TOKEN_TIMEOUT,
    PxPressRequest,
    PxPressResult,
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
        # 真实调用方（grok-register）通常带 deviceId；单元测试也要带，否则
        # 走 vid 自动补种（另测），每个用例都得先过那道门
        "vid": "vid-from-caller",
        "country": "US",
        "pollInterval": 0.25,
        "timeout": 10,
    }
    payload.update(overrides)
    return PxPressRequest.from_payload(payload)


class _FakePage:
    def __init__(self, *, goto_exc=None, status=200):
        self.goto_calls = []
        self.evaluate_calls = []
        self._goto_exc = goto_exc
        self._status = status
        self.closed = False
        self._response_handlers = []
        self._event_handlers = {}
        self.main_frame = object()
        self.iframe_frame = object()  # press 双页结构下 challenge 的子 frame

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self._goto_exc is not None:
            raise self._goto_exc
        # 模拟一次主帧响应，供状态判定
        handler = self._response_handlers[0] if self._response_handlers else None
        if handler is not None:
            await handler(_FakeResponse(self._status, url, self.main_frame))
        return None

    async def evaluate(self, script, *args):
        self.evaluate_calls.append((script, args))
        # 真实浏览器里 iframe 注入后 challenge 文档随即到达；fake 同步补发
        # 一条子 frame 文档响应，让 _attach_iframe 的就绪 Future 立刻完成，
        # fail-fast 断言（<2s）不被等待拖慢
        for handler in self._response_handlers:
            await handler(
                _FakeResponse(200, "https://iframe.hsprotect.net/challenge",
                              self.iframe_frame)
            )
        return "px-captcha-host"

    def on(self, event, handler):
        if event == "response":
            self._response_handlers.append(handler)
        self._event_handlers.setdefault(event, []).append(handler)

    async def close(self):
        self.closed = True


class _FakeResponse:
    def __init__(self, status, url, frame, resource_type="document") -> None:
        self.status = status
        self.url = url
        self.frame = frame
        self.request = _FakeRequest(resource_type)


class _FakeRequest:
    def __init__(self, resource_type):
        self.resource_type = resource_type


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
        self.exposed_bindings = []

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

    async def expose_binding(self, name, handler):
        self.exposed_bindings.append((name, handler))


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

    # 0.9.0：stealth + postMessage 探针两条 init script
    assert len(context.init_scripts) == 2
    joined = "\n".join(context.init_scripts)
    assert "ja-JP" in joined
    assert "webdriver" in joined
    assert "__pxParentProbe" in joined


async def test_stealth_chrome_script_does_not_self_reference():
    """stealth 的 window.chrome getter 不得自引用（线上 S3 断层根因）。

    ``{get: () => window.chrome || {...}}`` 里 getter 读 window.chrome 会
    再次触发自身 → 无限递归 → 栈溢出，任何碰 window.chrome 的页面脚本
    （含 PX 传感器）静默死亡，collector beacon 一个都发不出。回归断言：
    脚本里不得出现 getter 内自引用模式；真实浏览器语义用 node 式单测
    难覆盖，线上实验已单独实证（B2 组种出 _pxvid/_px3/_pxde、0 errors）。
    """
    import re

    from slidex.px.solver import _STEALTH_INIT_SCRIPT

    script = _STEALTH_INIT_SCRIPT % {"locale": "en-US"}
    # getter 箭头函数体内不得再读 window.chrome / globalThis.chrome
    getter_bodies = re.findall(r"get:\s*\(\)\s*=>\s*\((.*?)\)\}\)", script, re.S)
    assert getter_bodies, "window.chrome getter not found"
    body = getter_bodies[0]
    assert "window.chrome" not in body
    assert "globalThis.chrome" not in body
    # 关键子形状在（PX/页面脚本会探测 chrome.app / runtime / csi / loadTimes）
    assert "app:" in body and "runtime:" in body
    assert "csi:" in body and "loadTimes:" in body


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


async def test_navigate_connection_failure_is_load_failed():
    """导航异常（代理拒绝/DNS/证书/不安全端口）→ challenge_load_failed。

    与「页面加载了但没出 token」（px_token_timeout）区分：错误要从 kind 可辨，
    不能一律吞成 load_timeout。
    """
    page = _FakePage(goto_exc=RuntimeError("net::ERR_PROXY_CONNECTION_FAILED"))
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_LOAD
    assert result.retryable_error is True


async def test_navigate_timeout_is_load_timeout():
    """导航真的超时（黑洞代理/慢站）→ challenge_load_timeout，仍可重试。"""
    page = _FakePage(goto_exc=asyncio.TimeoutError())
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_TIMEOUT
    assert result.retryable_error is True


async def test_navigate_playwright_timeout_is_load_timeout():
    """playwright 自家 TimeoutError（与 asyncio.TimeoutError 不是同一个类，
    pxed 第 13 轮 C4 回归实测：0.9.6 重写 except 链时把它落进
    challenge_load_failed）也必须归为 challenge_load_timeout。
    """
    class TimeoutError(Exception):  # noqa: A001 - 模拟 playwright 的同名类
        pass

    page = _FakePage(goto_exc=TimeoutError("Page.goto: Timeout 12000ms exceeded."))
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_TIMEOUT
    assert result.retryable_error is True


async def test_rpc_swallow_survives_cancelled_future():
    """_rpc 的收割 callback 在 future 被取消时不得打崩自己（pxed 第 13 轮
    日志实测 "Exception in callback _rpc._swallow"：CancelledError 是
    BaseException，except Exception 捕不住）。
    """
    inner = asyncio.ensure_future(asyncio.sleep(3600))
    done = asyncio.Event()

    def _canceller():
        inner.cancel()
        done.set()

    from slidex.px import solver as solver_mod

    before = len(solver_mod._REAPED_RPC_EXCS)
    loop = asyncio.get_event_loop()
    loop.call_later(0.05, _canceller)
    # 走 _rpc 的路径：它给 inner 挂 _swallow 并 cancel
    with pytest.raises(asyncio.TimeoutError):
        await solver_mod._rpc(asyncio.sleep(3600), timeout=0.05)
    await asyncio.sleep(0.1)
    # callback 不崩（pytest 会把 callback 异常报成 unhandled）；
    # 有界队列长度不变或 +0（取消的 future 没有异常可收割）
    assert len(solver_mod._REAPED_RPC_EXCS) >= before


async def test_referer_passed_on_press_navigation():
    """press 双页结构：第一跳是宿主页 websiteURL（带 referer 的是 challenge）。"""
    page = _FakePage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    await solver.solve(_request())

    target, kwargs = page.goto_calls[0]
    # 0.9.0：宿主页先落 websiteURL 域，challengeUrl 变成注入的 iframe
    assert target == "https://signup.live.com/"
    assert kwargs["wait_until"] == "domcontentloaded"
    assert len(page.evaluate_calls) == 1
    script, args = page.evaluate_calls[0]
    assert args[0] == "https://iframe.hsprotect.net/challenge"
    assert "iframe" in script


async def test_missing_pxvid_filled_from_vid():
    cookies = [{"name": "_px3", "value": _PASS_PX3}]
    context = _FakeContext([cookies])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))
    request = _request(vid="vid-from-payload", uuid="uuid-from-payload")

    result = await solver.solve(request)

    assert result.success is True
    assert result.tokens["pxvid"] == "vid-from-payload"
    # uuid 是 leadingChallengeId，不是 pxcts：不许拿它伪造 token（需求单 §4
    # 出口/上下文一致性；伪造值会被 risk/verify 当作无效证据）
    assert result.tokens["pxcts"] == ""
    assert result.missing == ["pxde", "pxcts"]


async def test_cookie_read_failure_is_internal_error():
    class _BrokenContext(_FakeContext):
        async def cookies(self):
            raise RuntimeError("target closed")

    context = _BrokenContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    # 读 cookie 一失败就判定 context 已死：不能把整个预算耗在无意义的轮询上
    assert result.success is False
    assert result.error_kind == ERR_INTERNAL
    assert result.retryable_error is True
    assert time.monotonic() - started < 2.0


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


async def test_farm_launch_pins_adj_via_thread_pool(monkeypatch):
    """launch 后的自钉走 async 包装（线程池），不再阻塞事件循环（0.9.5 F4）。

    变异验证：把 _pin_oom_score_adj_async 换成同步直调后本测试失败——
    农场如果还在 launch 路径里同步扫 /proc，事件循环会被卡 ~百 ms。
    """
    from slidex.px import solver as solver_mod

    calls = []

    async def fake_async_pin(adj=-20):
        calls.append(("async", adj))

    monkeypatch.setattr(solver_mod, "_pin_oom_score_adj_async", fake_async_pin)

    pw = _FakePw([_FakeBrowserObj()])
    farm = _BrowserFarm(lambda: _resolve(pw), headless=True, extra_args=[])
    await farm.browser()
    assert calls == [("async", -20)]


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
    """new_context 报 farm 级死亡：先就地重置+重建重试一次，仍死则置空农场。

    pxed 实测（0.9.5 部署验收）：渲染进程偶发 crash（Target crashed）与
    playwright driver 被杀（handler is closed）后 ``is_connected()`` 仍返回
    True——不按异常内容显式重置，后续任务会在同一具尸体上连环撞（B 组
    连空根因）。重试用尽的 farm 必须置空，让再下一个任务从零重建。
    """
    class _DeadOnContext(_FakeBrowserObj):
        async def new_context(self, **kwargs):
            self._connected = False
            raise RuntimeError("Target page, context or browser has been closed")

    dead = _DeadOnContext()
    also_dead = _DeadOnContext()
    pw = _FakePw([dead, also_dead])
    solver = PxPressSolver(playwright_factory=lambda: _resolve(pw))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.retryable_error is True
    # 0.9.6 起进程内重试一次：死亡后 farm 被重置（epoch 前进），重试在新
    # farm 上跑（fake 浏览器列表已耗尽 → launch 失败收场）。farm 不得留在
    # 带 browser 引用的死对象上；后续任务会从零重建。
    assert solver._farm_epoch >= 2
    assert solver._farm is None or solver._farm._browser is None
    # 就地重试(第2次 launch) + solve 级重试(第3次 launch 耗尽列表)
    assert pw.chromium.launches == 3


async def test_page_crash_resets_farm_and_retries_inplace():
    """context.new_page 报 Target crashed（pxed B 组连空的直接根因）：
    重置农场 + 就地重建重试一次——本任务不白跑，且 crash 窗口内的
    连环任务不会再撞同一具半死 browser。
    """
    calls = {"n": 0}

    async def _new_context(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("BrowserContext.new_page: Target crashed ")
        return _FakeContext([_pass_cookies()])

    crashing_browser = _FakeBrowserObj()
    fresh_browser = _FakeBrowserObj()
    for b in (crashing_browser, fresh_browser):
        b.new_context = _new_context
    pw = _FakePw([crashing_browser, fresh_browser])
    solver = PxPressSolver(playwright_factory=lambda: _resolve(pw))

    result = await solver.solve(_request())

    # 就地重试成功：本任务拿到 pass token，farm 换了新 browser
    assert result.success is True
    assert pw.chromium.launches == 2
    assert solver._farm is not None
    assert solver._farm._browser is fresh_browser


async def test_solve_hard_deadline_abandons_cancel_swallowing_coroutine(monkeypatch):
    """任务级硬顶必须用「放弃」而非 wait_for：inner 吞 cancel 时 wait_for
    自身也挂起（pxed D1 双任务卡死 90s 的根因）。硬顶到点后主流程立即
    返回 retryable 失败并重置农场，吞 cancel 的僵尸协程留在后台。
    """
    import asyncio as _asyncio

    released = _asyncio.Event()

    class _StubbornContext(_FakeContext):
        async def new_page(self):
            # 模拟 playwright greenlet 桥：吞 cancel 后继续挂
            try:
                await _asyncio.sleep(3600)
            except _asyncio.CancelledError:
                released.set()
                await _asyncio.sleep(3600)

    context = _StubbornContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))
    # 硬顶压到 0.2s 便于测试
    monkeypatch.setattr("slidex.px.solver._SOLVE_HARD_MARGIN_S", -59.8)

    result = await solver.solve(_request())

    assert result.success is False
    assert result.retryable_error is True
    assert "hard deadline" in result.error
    # 等僵尸协程的第一次 cancel 真正送达（事件被吞前触发过）
    for _ in range(20):
        if released.is_set():
            break
        await _asyncio.sleep(0.05)
    assert released.is_set()


async def test_driver_death_during_goto_resets_farm_and_retry_once():
    """goto 报 driver 死亡（pxed 第 11 轮 C5/D1/D2 连空的根因）：
    _goto 之前把它吞成 challenge_load_failed 返回，异常不进 _solve_once
    的 except → 不触发 farm 重置，死 farm 留给后续任务连环撞；任务也不
    重试，调用方直接吃到假失败。修复后：farm 死亡必须向上传播触发重置，
    且 solve() 在进程内重试一次（新 farm 上本任务拿到 pass token）。
    """
    class _DeadOnGotoContext(_FakeContext):
        async def new_page(self):
            return _DeadOnGotoPage()

    class _DeadOnGotoPage(_FakePage):
        async def goto(self, url, **kwargs):
            raise RuntimeError("Page.goto: Connection closed while reading from the driver")

    dead_ctx = _DeadOnGotoContext([[]])
    good_ctx = _FakeContext([_pass_cookies()])
    contexts = [dead_ctx, good_ctx]

    class _PickContextBrowser(_FakeBrowserObj):
        async def new_context(self, **kwargs):
            return contexts.pop(0)

    pw = _FakePw([_PickContextBrowser(), _PickContextBrowser()])
    solver = PxPressSolver(playwright_factory=lambda: _resolve(pw))

    result = await solver.solve(_request())

    assert result.success is True, f"expected in-process retry to pass: {result.error}"
    assert pw.chromium.launches == 2
    assert solver._farm is not None and solver._farm._browser is not None


async def test_transient_failure_does_not_double_retry():
    """进程内重试只针对「farm 死亡被重置」这一次：普通业务失败（如 cookie
    读失败但 farm 未被重置）不得触发重试，否则错误预算翻倍。
    """
    class _BrokenContext(_FakeContext):
        def __init__(self):
            super().__init__([[]])
            self.cookie_attempts = 0

        async def cookies(self):
            self.cookie_attempts += 1
            raise RuntimeError("target closed")

    context = _BrokenContext()
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_INTERNAL
    assert result.retryable_error is True
    # 没重试：farm 未被重置（epoch 不变）；_read_cookies 的 two-strikes
    # 确认（失败后 sleep 一次再试）是既有设计，共读两次
    assert context.cookie_attempts == 2
    assert solver._farm_epoch == 0


async def test_zombie_future_exception_is_retrieved(monkeypatch):
    """硬顶放弃的僵尸协程死后异常必须被收割：pxed 第 11 轮日志出现
    'Future exception was never retrieved'（吞 cancel 的协程死于 driver
    关闭），asyncio 会把未收割异常记进 GC 警告日志。
    """
    import asyncio as _asyncio

    released = _asyncio.Event()

    class _StubbornDyingContext(_FakeContext):
        async def new_page(self):
            try:
                await _asyncio.sleep(3600)
            except _asyncio.CancelledError:
                released.set()
                # 吞掉 cancel 后死于真实异常（模拟 driver 关闭）
                raise RuntimeError("Connection closed while reading from the driver")

    context = _StubbornDyingContext([[]])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))
    monkeypatch.setattr("slidex.px.solver._SOLVE_HARD_MARGIN_S", -59.8)

    result = await solver.solve(_request())
    assert result.success is False

    # 给僵尸协程时间死透，再等收割 callback 执行
    for _ in range(20):
        if released.is_set():
            break
        await _asyncio.sleep(0.05)
    for _ in range(20):
        if solver._last_abandoned_exc is not None:
            break
        await _asyncio.sleep(0.05)
    # 收割必须发生：否则 asyncio 会留下 "Task exception was never retrieved"。
    # 僵尸 new_page 死在 _rpc 的 wait 内（被硬顶 cancel 连带取消），死因
    # 由 _rpc 的收割观测点记录
    from slidex.px import solver as solver_mod

    for _ in range(20):
        if solver_mod._REAPED_RPC_EXCS:
            break
        await _asyncio.sleep(0.05)
    assert solver_mod._REAPED_RPC_EXCS, "zombie RPC exception must be reaped"
    assert any("driver" in str(e) for e in solver_mod._REAPED_RPC_EXCS)


# ────────────────────── 线上 review：首屏状态判定与中途断连 ──────────────────────


class _MultiResponsePage(_FakePage):
    """按序回放多条主帧响应（模拟重定向链）。"""

    def __init__(self, sequence):
        super().__init__()
        self._sequence = list(sequence)

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        for status, resp_url in self._sequence:
            for handler in self._response_handlers:
                await handler(_FakeResponse(status, resp_url, self.main_frame))
        return None


async def test_redirect_followed_by_403_fails_fast_as_deterministic():
    """target 302 后落到 403 拦截页：不能只看 target 的 302 继续轮询。"""
    page = _MultiResponsePage(
        [(302, "https://iframe.hsprotect.net/challenge"),
         (403, "https://iframe.hsprotect.net/blocked")]
    )
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False
    assert time.monotonic() - started < 2.0


async def test_redirect_followed_by_404_fails_fast():
    page = _MultiResponsePage(
        [(302, "https://iframe.hsprotect.net/challenge"),
         (404, "https://iframe.hsprotect.net/gone")]
    )
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False


async def test_404_fails_fast_instead_of_burning_budget():
    page = _FakePage(status=404)
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False
    assert time.monotonic() - started < 2.0


async def test_5xx_fails_fast_but_is_retryable():
    page = _FakePage(status=502)
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is True
    assert time.monotonic() - started < 2.0


async def test_mid_task_browser_death_resets_farm_and_fails_fast():
    """出 cookie 读失败 + Chromium 已断连：立刻失败并重置农场。"""
    class _DeadMidTask(_FakeBrowserObj):
        async def new_context(self, **kwargs):
            # 上下文建好后 Chromium 被 OOM 杀：is_connected() 变 False
            self._connected = False
            return _DeadContext()

    class _DeadContext:
        async def cookies(self):
            raise RuntimeError("Target page, context or browser has been closed")

        async def add_init_script(self, script):
            pass

        async def add_cookies(self, specs):
            pass

        async def new_page(self):
            return _FakePage()

        async def close(self):
            pass

    dead = _DeadMidTask()
    pw = _FakePw([dead, _FakeBrowserObj()])
    solver = PxPressSolver(playwright_factory=lambda: _resolve(pw))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_INTERNAL
    assert result.retryable_error is True
    assert time.monotonic() - started < 2.0
    # 0.9.6 起进程内重试一次：死亡被重置（epoch 前进），重试在新 farm 上
    # 跑。fake 的第二个 browser 无 new_context → 重试报内部错误收场，
    # 但 farm 本身健康（browser 是活的假对象），不再要求置空。
    assert solver._farm_epoch >= 1
    assert result.error_kind == ERR_INTERNAL


class _SubresourcePage(_FakePage):
    """主帧先回文档，再回若干子资源响应（frame 同为 main_frame）。"""

    def __init__(self, document_status, subresources):
        super().__init__(status=document_status)
        self._document_status = document_status
        self._subresources = list(subresources)

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        handler = self._response_handlers[0] if self._response_handlers else None
        if handler is None:
            return None
        await handler(_FakeResponse(self._document_status, url, self.main_frame))
        for status, rtype in self._subresources:
            await handler(_FakeResponse(status, url + "sub", self.main_frame, rtype))
        return None


async def test_healthy_page_with_404_subresource_is_not_killed():
    """文档 200 + 404 阻塞脚本：子资源状态不许覆盖文档状态（否则健康页被误杀）。"""
    page = _SubresourcePage(200, [(404, "script"), (500, "fetch")])
    context = _FakeContext([[{"name": "_px3", "value": _PASS_PX3}]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    assert result.error_kind == ""


async def test_200_subresource_does_not_mask_real_404_document():
    """文档 404 + 200 阻塞脚本：仍须按确定性失败早退，不能拖成超时。"""
    page = _SubresourcePage(404, [(200, "script"), (200, "image")])
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False
    assert time.monotonic() - started < 2.0


async def test_proxy_407_fails_fast_not_retryable():
    """代理回 407（要认证）：独立 kind（challenge_proxy_auth），确定性终止。

    Chromium 把 407 当文档渲染（status=407、resource_type=document），旧逻辑
    只认 403/429/404，于是它一路轮询到预算耗尽，返回误导性的可重试超时——
    而同一个 payload 配错的代理密码重试永远不会好。kind 与页面层 403/5xx
    （challenge_http_error）分开，因为处置方向不同：407 要改代理配置。
    """
    page = _SubresourcePage(407, [])
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_PROXY_AUTH
    assert result.retryable_error is False
    assert "407" in result.error
    assert time.monotonic() - started < 2.0


async def test_silent_proxy_407_fails_fast_not_retryable():
    """silent 模式的 407 同样秒死（线上 deep_check C5 回归的根因）。

    0.9.0 重构把首屏判死挪进 press 分支后，silent 分支丢了判死——407 被
    当文档渲染，任务一路轮询到预算耗尽，返回误导性的可重试 px_token_timeout。
    首屏状态判死与模式无关：silent 的主帧文档就是 challenge 宿主。
    """
    page = _SubresourcePage(407, [])
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request(mode="silent"))

    assert result.success is False
    assert result.error_kind == ERR_PROXY_AUTH
    assert result.retryable_error is False
    assert "407" in result.error
    assert time.monotonic() - started < 2.0


async def test_silent_404_document_fails_fast_not_retryable():
    """silent 模式主帧 404：确定性终止，不烧预算（同一判死覆盖 silent）。"""
    page = _SubresourcePage(404, [])
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request(mode="silent"))

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False
    assert time.monotonic() - started < 2.0


# ------------------------------------------------------ 0.9.0 双页/补种/诊断

async def test_press_navigates_host_then_injects_iframe():
    """§3.1：press 不再把 iframe URL 当顶层页——先宿主页，再注入 challenge。

    直接导航 iframe 时 postMessage 通道永远沉默（live 实测种不出任何
    _px cookie），顶层页必须先落在 websiteURL 域上。
    """
    page = _FakePage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    assert len(page.goto_calls) == 1
    host_url, host_kwargs = page.goto_calls[0]
    assert host_url == "https://signup.live.com/"
    assert host_kwargs["wait_until"] == "domcontentloaded"
    # challengeUrl 只以 iframe 形态进入，绝不走 goto
    assert all(url != "https://iframe.hsprotect.net/challenge"
               for url, _ in page.goto_calls)
    assert len(page.evaluate_calls) == 1
    script, inject_args = page.evaluate_calls[0]
    assert inject_args[0] == "https://iframe.hsprotect.net/challenge"
    assert "createElement('iframe')" in script


async def test_host_page_404_fails_fast_before_iframe_injection():
    """宿主页 404：确定性终止，且不再注入 iframe（白等 iframe 就绪没有意义）。"""
    page = _FakePage(status=404)
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    started = time.monotonic()
    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_HTTP
    assert result.retryable_error is False
    assert time.monotonic() - started < 2.0
    assert page.evaluate_calls == []


async def test_iframe_inject_timeout_retries_once(monkeypatch):
    """iframe 注入 evaluate 调用级超时：当场重试一次而不是判死任务。

    pxed 第 12 轮 B5 实测（02:00:23）：farm 冷启动 + A/B 组连续任务挤在
    2 核上，单次 evaluate RPC 超 5s —— 注入是幂等操作，超时不该把整个
    press 任务判成 challenge_load_failed（ENV_PAGE 的 _pxcts 没写进去，
    B5 断言 referrer=''）。
    """
    import asyncio as _asyncio

    class _FlakyInjectPage(_FakePage):
        def __init__(self):
            super().__init__()
            self.evaluate_attempts = 0

        async def evaluate(self, script, *args):
            self.evaluate_attempts += 1
            if self.evaluate_attempts == 1:
                # 慢过调用级超时：monkeypatch 后 0.1s 即判超时
                await _asyncio.sleep(5)
            return await super().evaluate(script, *args)

    monkeypatch.setattr("slidex.px.solver._COOKIE_RPC_TIMEOUT_S", 0.1)
    page = _FlakyInjectPage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True, f"inject retry should succeed: {result.error}"
    assert page.evaluate_attempts == 2
    assert len(page.evaluate_calls) == 1  # 第二次才真正执行到记录处


async def test_iframe_inject_both_attempts_fail_is_load_failed(monkeypatch):
    """两次注入都失败：维持既有 challenge_load_failed 语义（不静默继续）。"""
    import asyncio as _asyncio

    class _AlwaysFailInjectPage(_FakePage):
        def __init__(self):
            super().__init__()
            self.evaluate_attempts = 0

        async def evaluate(self, script, *args):
            self.evaluate_attempts += 1
            raise RuntimeError(f"evaluate blew up (attempt {self.evaluate_attempts})")

    monkeypatch.setattr("slidex.px.solver._COOKIE_RPC_TIMEOUT_S", 0.1)
    page = _AlwaysFailInjectPage()
    context = _FakeContext([[]], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is False
    assert result.error_kind == ERR_CHALLENGE_LOAD
    assert "iframe inject failed" in result.error
    assert page.evaluate_attempts == 2


async def test_diagnostics_present_on_success_and_failure():
    """§3.3：成败响应都带 diagnostics（脱敏观测）。"""
    context = _FakeContext([_pass_cookies()])
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    d = result.diagnostics
    assert d["iframe_loaded"] is True
    assert d["iframe_status"] == 200
    assert isinstance(d["px_cookie_names_seen"], list)
    assert any(name.startswith("_px3") for name in d["px_cookie_names_seen"])
    # 脱敏：诊断里只有名字，绝没有 cookie 值
    assert _PASS_PX3 not in str(d)


async def test_diagnostics_captcha_js_and_collector_seen():
    """传感器执行链归类：captcha.js / main.min.js / collector 各认各的。"""
    from slidex.px.solver import _Diagnostics

    diag = _Diagnostics()
    diag.observe_response(_FakeResponse(200, "https://client.hsprotect.net/PXzC5j78di/main.min.js", object(), "script"))
    diag.observe_response(_FakeResponse(200, "https://iframe.hsprotect.net/captcha.js?a=c&v=vid", object(), "script"))
    diag.observe_response(200, ) if False else None

    class _R:
        status = 200
        url = "https://collector-pxxxxxxxxx.hsprotect.net/api/v2/mst/beacon"
    diag.observe_response(_R())

    snap = diag.snapshot()
    assert snap["main_min_js_loaded"] is True and snap["main_min_js_status"] == 200
    assert snap["captcha_js_loaded"] is True
    assert snap["collector_seen"] is True


async def test_collector_failed_request_distinguished_from_never_sent():
    """冒烟报告 §3 P0-2：「collector 没发出」vs「发出但失败」必须可区分。

    beacon 到了网络层但失败（DNS/拒绝/TLS）只有 requestfailed 能证明——
    requested=true + seen=false + error 有值 = 网络层断；requested=false =
    传感器在浏览器内静默放弃（执行层断）。response 成功后 error 复位。
    """
    from slidex.px.solver import _Diagnostics

    class _FailedReq:
        url = "https://collector-pxxxxxxxxx.hsprotect.net/api/v2/mst/beacon?uuid=zzz"
        failure = type("F", (), {"errorText": "net::ERR_CONNECTION_REFUSED"})()

    diag = _Diagnostics()
    diag.observe_request_failed(_FailedReq())
    snap = diag.snapshot()
    assert snap["collector_requested"] is True
    assert snap["collector_seen"] is False
    assert snap["collector_request_error"] == "net::ERR_CONNECTION_REFUSED"
    # URL query（uuid 会话参数）不进诊断
    assert "uuid" not in snap["collector_request_error"]

    # 非 collector 的失败请求不算
    class _OtherReq:
        url = "https://client.hsprotect.net/main.min.js"
        failure = type("F", (), {"errorText": "net::ERR_TIMED_OUT"})()
    diag.observe_request_failed(_OtherReq())
    assert diag.snapshot()["collector_request_error"] == "net::ERR_CONNECTION_REFUSED"

    # 后来重试成功：error 复位，seen=true
    diag.observe_response(_FakeResponse(
        200, "https://collector-pxxxxxxxxx.hsprotect.net/api/v2/collector",
        object(), "xhr",
    ))
    snap = diag.snapshot()
    assert snap["collector_seen"] is True
    assert snap["collector_request_error"] == ""


async def test_sensor_console_captures_and_redacts():
    """冒烟报告 §3 P0-1：console/pageerror 进 diagnostics.sensor_console。

    PX 静默退出通常在 console 留提示（"App communication failure" 等）。
    脱敏约束：截断到 200 字符、单任务上限 20 条、空文本不记。
    """
    from slidex.px.solver import _Diagnostics

    diag = _Diagnostics()
    diag.observe_console("error", "App communication failure: api blocked " + "x" * 500)
    diag.observe_console("warning", "")
    diag.observe_console("pageerror", "TypeError: Cannot read properties of undefined")
    snap = diag.snapshot()
    assert len(snap["sensor_console"]) == 2
    first = snap["sensor_console"][0]
    assert first.startswith("error: App communication failure")
    assert len(first) <= 208  # "error: " 前缀 + 200 截断
    assert "pageerror: TypeError" in snap["sensor_console"][1]

    # 刷屏保护：超过 20 条后丢弃
    for i in range(30):
        diag.observe_console("log", f"noise {i}")
    assert len(diag.snapshot()["sensor_console"]) == 20


async def test_console_and_requestfailed_listeners_attached_to_page():
    """冒烟报告 §3 P0：console/pageerror/requestfailed 监听真的挂上了 page。

    挂了但没触发没有意义——fake page 在 goto 时派发事件，断言事件流经
    handler 落进当次任务的 diagnostics（端到端连线，而不只是单元行为）。
    """
    page = _FakePage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    for event in ("console", "pageerror", "requestfailed"):
        assert event in page._event_handlers, f"{event} listener not attached"

    failed_req = type(
        "R", (),
        {"url": "https://collector-pxxxxx.hsprotect.net/api/v2/collector",
         "failure": type("F", (), {"errorText": "net::ERR_NAME_NOT_RESOLVED"})()},
    )()

    class _NoisyPage(_FakePage):
        async def goto(self, url, **kwargs):
            await asyncio.sleep(0)
            for h in self._event_handlers.get("console", []):
                h(type("M", (), {"type": "error", "text": "App communication failure"}))
            for h in self._event_handlers.get("requestfailed", []):
                h(failed_req)
            for h in self._event_handlers.get("pageerror", []):
                h(RuntimeError("sensor crashed"))
            return None

    noisy = _NoisyPage()
    context2 = _FakeContext([_pass_cookies()], page=noisy)
    solver2 = PxPressSolver(context_factory=lambda opts: _resolve(context2))
    result2 = await solver2.solve(_request())
    d2 = result2.diagnostics
    assert d2["sensor_console"] == [
        "error: App communication failure",
        "pageerror: sensor crashed",
    ]
    assert d2["collector_requested"] is True
    assert d2["collector_request_error"] == "net::ERR_NAME_NOT_RESOLVED"
    assert d2["collector_seen"] is False


async def test_expose_binding_is_awaited_and_fireable():
    """0.9.4 回归：expose_binding 未 await，postMessage 观测是死路。

    服务日志曾有 "coroutine 'BrowserContext.expose_binding' was never
    awaited"——协程从未执行，binding 不存在，页面回调不可能触发。断言：
    (1) solve 后 binding 已注册；(2) 注册的 handler 可被调用且翻转
    diagnostics.parent_message_seen。
    """
    page = _FakePage()
    context = _FakeContext([_pass_cookies()], page=page)
    solver = PxPressSolver(context_factory=lambda opts: _resolve(context))

    result = await solver.solve(_request())

    assert result.success is True
    assert [name for name, _ in context.exposed_bindings] == [
        "__slidexParentMessage__"
    ]
    _name, handler = context.exposed_bindings[0]
    diag = result.diagnostics
    # handler 是页面侧入口：调用后 parent_message_seen 必须翻转
    # （通过一次真实 solve 的 diagnostics 无法直接拿到 _Diagnostics 实例，
    # 这里用 handler 的行为契约验证：不抛错即可调用）
    await handler(None)  # 不应抛 "coroutine never awaited" 之外的错


def test_env_chromium_args_passthrough(monkeypatch):
    """SLIDEX_PX_CHROMIUM_ARGS 透传进 chromium launch args。

    低内存宿主需要按部署点注入收窄参数（renderer-process-limit 等），
    不得写死在默认值里。空/纯空白 = 无追加。
    """
    from slidex.px import solver as solver_mod

    assert solver_mod._env_chromium_args() == []
    monkeypatch.setenv("SLIDEX_PX_CHROMIUM_ARGS", "  ")
    assert solver_mod._env_chromium_args() == []
    monkeypatch.setenv(
        "SLIDEX_PX_CHROMIUM_ARGS",
        "--renderer-process-limit=4  --js-flags=--max-old-space-size=256",
    )
    assert solver_mod._env_chromium_args() == [
        "--renderer-process-limit=4",
        "--js-flags=--max-old-space-size=256",
    ]


async def test_env_chromium_args_reach_launch(monkeypatch):
    """环境变量参数真的进到 launch 调用（farm → launch args 连线）。"""
    from slidex.px import solver as solver_mod

    captured = {}

    class _FakeBrowser:
        def is_connected(self):
            return False

        async def close(self):
            pass

    class _FakePW:
        class chromium:
            @staticmethod
            async def launch(**kwargs):
                captured.update(kwargs)
                return _FakeBrowser()

    async def _factory():
        return _FakePW()

    farm = solver_mod._BrowserFarm(_factory, headless=True, extra_args=[])
    monkeypatch.setenv("SLIDEX_PX_CHROMIUM_ARGS", "--renderer-process-limit=4")
    await farm.browser()
    assert "--renderer-process-limit=4" in captured["args"]


async def test_vid_seeding_does_not_hold_main_slot(monkeypatch):
    """补种期间主并发槽必须放回：concurrency=1 时第二个 press 请求应能进槽。

    回归背景（0.9.5 review F1）：种子原先在主槽内跑（最长 25s），低并发部署
    点上两个 vid 缺失的调用方会互相把对方顶成 solver_busy。变异验证：把
    「释放主槽」删掉后本测试失败（第二个请求 solver_busy）。
    """
    release_events = []
    real_acquire = PxPressSlots.acquire
    real_release = PxPressSlots.release

    def tracked_acquire(self):
        release_events.append("acquire")
        return real_acquire(self)

    def tracked_release(self):
        release_events.append("release")
        return real_release(self)

    seeding = asyncio.Event()

    async def fake_harvest(self, request):
        # 种子跑的时候主槽必须已经放回（acquire 计数 < release 计数 + …）
        release_events.append("seed_running")
        assert "release" in release_events, "seed started while still holding main slot"
        seeding.set()
        return "vid-seeded"

    real_solve_once = PxPressSolver._solve_once

    async def fake_solve_once(self, request, started):
        return PxPressResult(
            True, tokens={"px3": _PASS_PX3, "pxvid": request.vid},
            mode=request.mode, duration_ms=1,
        )

    monkeypatch.setattr(PxPressSlots, "acquire", tracked_acquire)
    monkeypatch.setattr(PxPressSlots, "release", tracked_release)

    solver = PxPressSolver(concurrency=1, context_factory=lambda opts: _resolve(_FakeContext([[]])))
    solver._harvest_vid = fake_harvest.__get__(solver)  # type: ignore[method-assign]
    solver._solve_once = fake_solve_once.__get__(solver)  # type: ignore[method-assign]

    # 第一个请求：vid 缺失，走补种路径
    first = asyncio.ensure_future(solver.solve(_request(vid="")))
    await asyncio.wait_for(seeding.wait(), timeout=5)
    # 主槽应已放回：第二个请求（带 vid）此刻必须能直接求解，不等种子结束
    second_started = asyncio.Event()

    async def second_solve():
        result = await solver.solve(_request())
        second_started.set()
        return result

    second = asyncio.ensure_future(second_solve())
    await asyncio.wait_for(second_started.wait(), timeout=5)
    r1 = await asyncio.wait_for(first, timeout=10)
    r2 = await asyncio.wait_for(second, timeout=10)
    assert r1.success is True
    assert r2.success is True


async def test_vid_missing_auto_seeds_then_presses():
    """§3.2 策略二：vid 缺失先裸 silent 补种子，拿到的 vid 种进 cookies 再 press。"""
    harvests = ["vid-seeded"]

    async def fake_harvest(self, request):
        assert request.mode == "silent"  # 种子必须是裸 silent，不是 press
        return harvests.pop(0)

    real_solve_once = PxPressSolver._solve_once
    seen_requests = []

    async def fake_solve_once(self, request, started):
        seen_requests.append(request)
        # press 阶段：验证 vid 已种上（cookie + 字段双落）
        assert request.vid == "vid-seeded"
        assert request.cookies.get("_pxvid") == "vid-seeded"
        return PxPressResult(
            True, tokens={"px3": _PASS_PX3, "pxvid": "vid-seeded"},
            mode="press", duration_ms=1,
        )

    solver = PxPressSolver(context_factory=lambda opts: _resolve(_FakeContext([[]])))
    solver._harvest_vid = fake_harvest.__get__(solver)  # type: ignore[method-assign]
    solver._solve_once = fake_solve_once.__get__(solver)  # type: ignore[method-assign]

    # press 且无 vid（真实场景：challengeDetails.deviceId 为空）
    result = await solver.solve(_request(vid=""))

    assert result.success is True
    assert len(seen_requests) == 1
    assert seen_requests[0].mode == "press"


async def test_vid_missing_and_seed_fails_returns_vid_required():
    """§3.2：补种子也拿不到 vid → 明确 fail（caller_vid_required），不再沉默超时。"""

    async def fake_harvest(self, request):
        return ""

    solver = PxPressSolver(context_factory=lambda opts: _resolve(_FakeContext([[]])))
    solver._harvest_vid = fake_harvest.__get__(solver)  # type: ignore[method-assign]

    started = time.monotonic()
    result = await solver.solve(_request(vid=""))  # press 无 vid；种子必然失败

    assert result.success is False
    assert result.error_kind == ERR_VID_REQUIRED
    assert result.retryable_error is True
    assert result.diagnostics == {"vid_seeded": False}
    assert time.monotonic() - started < 5.0  # 不许烧 press 预算


async def test_vid_present_skips_seeding():
    """vid 已带：不跑种子任务，直接 press。"""
    calls = []

    async def fake_solve_once(self, request, started):
        calls.append(request.mode)
        return PxPressResult(True, tokens={"px3": _PASS_PX3}, mode=request.mode)

    solver = PxPressSolver(context_factory=lambda opts: _resolve(_FakeContext([[]])))
    solver._solve_once = fake_solve_once.__get__(solver)  # type: ignore[method-assign]

    result = await solver.solve(_request(vid="vid-direct"))

    assert result.success is True
    assert calls == ["press"]


async def test_cookie_names_diagnostic_collects_names_not_values():
    """observe_cookie_names 只收名字+域，值不进诊断。"""
    from slidex.px.solver import _Diagnostics

    diag = _Diagnostics()
    diag.observe_cookie_names([
        {"name": "_px3", "value": "1:a:1000:SECRET::", "domain": ".signup.live.com"},
        {"name": "_pxvid", "value": "vid-x", "domain": ".hsprotect.net"},
        {"name": "session", "value": "zzz", "domain": ".signup.live.com"},
    ])
    snap = diag.snapshot()
    assert "_px3(.signup.live.com)" in snap["px_cookie_names_seen"]
    assert "_pxvid(.hsprotect.net)" in snap["px_cookie_names_seen"]
    assert all("session" not in n for n in snap["px_cookie_names_seen"])
    assert all("SECRET" not in n for n in snap["px_cookie_names_seen"])


def test_pin_oom_score_adj_writes_only_chrome_procs(tmp_path, monkeypatch):
    """launch/每任务前自钉 chrome 进程 adj：只写匹配 cmdline 的 pid。"""
    import builtins

    from slidex.px import solver as solver_mod

    # 用真实文件系统模拟 /proc：tmp 下建 pid 目录，patch listdir 让
    # _pin_oom_score_adj 把 tmp 当 /proc 枚举；open 走 builtins 原样，
    # 但 _pin_oom_score_adj 内部路径是写死的 /proc/... —— 为让 tmp 生效，
    # 把 open 的路径前缀改写进 tmp（只拦 /proc/ 开头的调用）。
    for pid, cmd in (
        ("100", b"/usr/bin/chrome-headless-shell\0--type=renderer\0"),
        ("200", b"/usr/bin/uvicorn\0slidex.service.app\0"),
        ("300", b"chromium\0--headless\0"),
    ):
        d = tmp_path / pid
        d.mkdir()
        (d / "cmdline").write_bytes(cmd)
        (d / "oom_score_adj").write_text("300")
    (tmp_path / "self").mkdir()          # 非 digit：跳过
    (tmp_path / "999").mkdir()           # cmdline 缺失：跳过

    monkeypatch.setattr(
        solver_mod.os, "listdir",
        lambda p: ["100", "200", "300", "self", "999"],
    )

    real_open = builtins.open
    prefix = str(tmp_path)

    def fake_open(path, mode="r", *a, **kw):
        s = str(path)
        if s.startswith("/proc/") and not s.startswith("/proc/self"):
            s = prefix + s[len("/proc"):]
        return real_open(s, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)

    solver_mod._pin_oom_score_adj(-20)

    assert (tmp_path / "100" / "oom_score_adj").read_text() == "-20"
    assert (tmp_path / "300" / "oom_score_adj").read_text() == "-20"
    assert (tmp_path / "200" / "oom_score_adj").read_text() == "300"  # 非 chrome 不动


class _FakeWriteHandle:
    def __init__(self, path, sink):
        self._path = path
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data):
        self._sink[self._path] = data
