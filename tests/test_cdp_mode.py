"""0.6.15: CDP 模式连接外部真实浏览器时的会话身份保障。

punish 的 x5secdata 绑定 bot 侧会话：借外部浏览器（用户 PC Chrome 经反向
隧道）的是设备指纹，连接后必须注入 bot 的账号会话 cookie，否则票据发到
浏览器自己的会话上，bot 合并无效。
"""
import asyncio
import sys
from unittest import mock

import pytest

from slidex.solver import SliderSolver
from slidex import solver as solver_module


class _FakePage:
    def __init__(self):
        self.init_scripts = []
        self.handlers = {}
        self.gotos = []
        self.closed = False

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    def on(self, event, handler):
        self.handlers[event] = handler

    async def goto(self, url, **kwargs):
        self.gotos.append((url, kwargs))

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.cookies_added = []
        self.created_pages = []

    async def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    async def new_cdp_session(self, page):
        raise RuntimeError("no cdp session in fake")

    async def new_page(self):
        fresh = _FakePage()
        self.pages.append(fresh)
        self.created_pages.append(fresh)
        return fresh


class _FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def connect_over_cdp(self, endpoint, timeout=None):
        # 0.6.19 起带 timeout 参数（冻结标签快速失败）；fakes 需兼容
        return self._browser


class _FakePW:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)


class _FakePWFactory:
    def __init__(self, browser):
        self._browser = browser

    async def start(self):
        return _FakePW(self._browser)


def _make_solver() -> SliderSolver:
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver.cookies_str = "unb=123; sgcookie=xyz; x5sec=abc"
    solver.page = None
    solver.context = None
    solver._playwright = None
    solver._cdp = None
    solver._on_response = lambda r: None
    solver._on_console = lambda c: None
    return solver


@pytest.mark.asyncio
async def test_connect_existing_browser_injects_account_cookies(monkeypatch):
    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x"

    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePWFactory(browser))

    url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x"
    await solver._connect_existing_browser("http://localhost:9222", url)

    names = {c.get("name") for c in context.cookies_added}
    assert {"unb", "sgcookie", "x5sec"} <= names
    domains = {c.get("domain") for c in context.cookies_added}
    assert all(d and d.startswith(".") for d in domains)
    # 0.6.21: 专用标签页——不得复用/劫持用户已打开的页面
    assert solver.context is context
    assert solver.page is not page
    assert solver.page in context.created_pages
    assert page.gotos == []  # 用户原页未被导航
    assert solver.page.gotos and solver.page.gotos[0][0] == url


@pytest.mark.asyncio
async def test_connect_existing_browser_without_cookies_skips_inject(monkeypatch):
    solver = _make_solver()
    solver.cookies_str = ""
    solver._verify_url = "https://h5api.m.goofish.com/h5/mtop.x/1.0/_____tmd_____/punish?x5secdata=x"

    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePWFactory(browser))

    await solver._connect_existing_browser("http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x")
    assert context.cookies_added == []
    assert solver.page is not page  # 无 cookie 注入路径同样使用专用标签页


@pytest.mark.asyncio
async def test_close_cdp_only_closes_owned_page_only():
    """0.6.21: 自开标签页用完即关；调用方持有/用户原有页面绝不动。"""
    solver = _make_solver()
    user_page = _FakePage()
    owned = _FakePage()

    class _CDP:
        async def detach(self):
            return None

    class _PW:
        async def stop(self):
            return None

    solver.page = owned
    solver._cdp_owned_page = True
    solver._cdp = _CDP()
    solver._playwright = _PW()
    await solver._close_cdp_only()
    assert owned.closed is True
    assert solver._cdp is None and solver._playwright is None
    assert solver._cdp_owned_page is False

    # 调用方持有页（solve_on_page 语义 / 用户原页）：不得关闭
    solver.page = user_page
    solver._cdp_owned_page = False
    solver._cdp = _CDP()
    solver._playwright = _PW()
    await solver._close_cdp_only()
    assert user_page.closed is False


@pytest.mark.asyncio
async def test_solve_on_existing_page_serializes_same_endpoint():
    """0.6.21: 同一 CDP endpoint 串行——并发进入会互抢页面、互注账号 cookie。"""
    import asyncio
    import uuid

    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_step = mock.MagicMock()
    solver._emit_telemetry_event = mock.MagicMock()
    solver._finalize_telemetry = mock.MagicMock()
    state = {"inside": 0, "max": 0}

    async def _impl(cdp, url):
        state["inside"] += 1
        state["max"] = max(state["max"], state["inside"])
        await asyncio.sleep(0.05)
        state["inside"] -= 1
        return True, {"x5sec": "1"}

    async def _close():
        return None

    solver._solve_on_existing_impl = _impl
    solver._close_cdp_only = _close

    ep = f"http://cdp-{uuid.uuid4().hex}:9222"
    await asyncio.gather(
        solver.solve_on_existing_page(ep, ""),
        solver.solve_on_existing_page(ep, ""),
    )
    assert state["max"] == 1  # 串行，无重叠


@pytest.mark.asyncio
async def test_solve_on_existing_page_parallel_across_endpoints():
    """不同外部浏览器（不同 endpoint）互不阻塞。"""
    import asyncio
    import uuid

    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver._emit_step = mock.MagicMock()
    solver._emit_telemetry_event = mock.MagicMock()
    solver._finalize_telemetry = mock.MagicMock()
    state = {"inside": 0, "max": 0}

    async def _impl(cdp, url):
        state["inside"] += 1
        state["max"] = max(state["max"], state["inside"])
        await asyncio.sleep(0.05)
        state["inside"] -= 1
        return True, {"x5sec": "1"}

    async def _close():
        return None

    solver._solve_on_existing_impl = _impl
    solver._close_cdp_only = _close

    await asyncio.gather(
        solver.solve_on_existing_page(f"http://cdp-{uuid.uuid4().hex}:9222", ""),
        solver.solve_on_existing_page(f"http://cdp-{uuid.uuid4().hex}:9222", ""),
    )
    assert state["max"] == 2  # 并行，互不阻塞


def test_wake_cdp_targets_activates_page_targets(monkeypatch):
    """唤醒睡眠/冻结标签：向 /json 获取 targets 并对所有 page 目标调用 /json/activate/<id>"""
    import io
    import json
    from urllib.request import Request

    called_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        called_urls.append(url)
        if url.endswith("/json"):
            targets = [
                {"id": "tab1", "type": "page", "title": "Tab 1"},
                {"id": "tab2", "type": "page", "title": "Tab 2"},
                {"id": "sw1", "type": "service_worker", "title": "SW"},
                {"id": "iframe1", "type": "iframe", "title": "IFrame"},
            ]
            return io.BytesIO(json.dumps(targets).encode("utf-8"))
        # /json/activate/tab1 etc.
        return io.BytesIO(b"Target activated")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    woken = SliderSolver._wake_cdp_targets("http://127.0.0.1:9222")
    assert woken == 2
    assert "http://127.0.0.1:9222/json" in called_urls
    assert "http://127.0.0.1:9222/json/activate/tab1" in called_urls
    assert "http://127.0.0.1:9222/json/activate/tab2" in called_urls
    assert "http://127.0.0.1:9222/json/activate/sw1" not in called_urls


def test_wake_cdp_targets_swallows_network_errors(monkeypatch):
    """CDP 端点异常或网络不可达时静默返回 0,不抛出异常阻断流程"""
    import urllib.request

    def fake_urlopen(req, timeout=None):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    woken = SliderSolver._wake_cdp_targets("http://127.0.0.1:9222")
    assert woken == 0


def test_wake_cdp_targets_does_not_rely_on_active_field(monkeypatch):
    """Chrome 的 /json 没有 active 字段(devtools_http_handler.cc 的 SerializeDescriptor),
    因此不得据此跳过"当前标签"或做焦点还原:所有 page 目标都要被激活,且不追加还原调用。"""
    import io
    import json
    from urllib.request import Request

    called_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        called_urls.append(url)
        if url.endswith("/json"):
            targets = [
                # 即便某个版本真的带上 active,也不依赖它(当前实现一律激活全部 page)
                {"id": "user-tab", "type": "page", "active": True},
                {"id": "other-a", "type": "page"},
                {"id": "worker", "type": "service_worker"},
            ]
            return io.BytesIO(json.dumps(targets).encode("utf-8"))
        return io.BytesIO(b"Target activated")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    woken = SliderSolver._wake_cdp_targets("http://127.0.0.1:9222")
    assert woken == 2
    activates = [u for u in called_urls if "/json/activate/" in u]
    assert sorted(activates) == [
        "http://127.0.0.1:9222/json/activate/other-a",
        "http://127.0.0.1:9222/json/activate/user-tab",
    ]


def test_wake_cdp_targets_returns_zero_without_page_targets(monkeypatch):
    """没有 page 目标时返回 0 —— 调用方据此判定"唤醒没做成",不再浪费一轮握手超时。"""
    import io
    import json
    from urllib.request import Request

    called_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        called_urls.append(url)
        targets = [{"id": "worker", "type": "service_worker"}]
        return io.BytesIO(json.dumps(targets).encode("utf-8"))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert SliderSolver._wake_cdp_targets("http://127.0.0.1:9222") == 0
    assert not [u for u in called_urls if "/json/activate/" in u]


def test_cdp_connect_timeout_parsing(monkeypatch):
    """空值/非数字/非正数一律回落 45s,合法值原样生效——写错环境变量不得抛 ValueError。"""
    monkeypatch.delenv("SLIDEX_CDP_CONNECT_TIMEOUT", raising=False)
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "")
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "   ")
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "abc")
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "0")
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "-3")
    assert SliderSolver._cdp_connect_timeout_s() == 45.0
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "7.5")
    assert SliderSolver._cdp_connect_timeout_s() == 7.5
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "8")
    assert SliderSolver._cdp_connect_timeout_s() == 8.0


def test_cdp_probe_timeout_parsing(monkeypatch):
    """探针预算同样容错,并可被 env 覆盖——慢链路(反向隧道)靠它调,不必改常数发版。"""
    monkeypatch.delenv("SLIDEX_CDP_PROBE_TIMEOUT", raising=False)
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "")
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "   ")
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "abc")
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "0")
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "-1")
    assert SliderSolver._cdp_probe_timeout_s() == SliderSolver._PROBE_TIMEOUT_S
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "12.5")
    assert SliderSolver._cdp_probe_timeout_s() == 12.5
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "9")
    assert SliderSolver._cdp_probe_timeout_s() == 9.0


def test_probe_default_budget_has_headroom_over_tunnel_latency():
    """默认预算必须明显大于生产隧道的实测往返(2.05~2.12s)。

    0.6.37 的 2.0s 恰好卡在实测值之下,导致六个标签全判"连不上"、安全阀把探针
    整体降级——功能静默失效。这里把"必须留有余量"锁进测试,防止再被调回贴着
    实测延迟的数值。
    """
    assert SliderSolver._PROBE_TIMEOUT_S >= 3.0


@pytest.mark.asyncio
async def test_connect_existing_browser_uses_parsed_timeout_env(monkeypatch):
    """解析后的超时值必须真的传给 connect_over_cdp(毫秒)。"""
    monkeypatch.setenv("SLIDEX_CDP_CONNECT_TIMEOUT", "12")
    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/punish?x5secdata=x"
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FlakyChromium(browser, failures=0)
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FlakyPWFactory(chromium))

    await solver._connect_existing_browser("http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x")
    assert chromium.timeouts == [12000.0]


def test_cdp_pre_activate_defaults_off_and_env_enables(monkeypatch):
    monkeypatch.delenv("SLIDEX_CDP_PRE_ACTIVATE", raising=False)
    assert SliderSolver._cdp_pre_activate_enabled() is False
    monkeypatch.setenv("SLIDEX_CDP_PRE_ACTIVATE", "1")
    assert SliderSolver._cdp_pre_activate_enabled() is True
    monkeypatch.setenv("SLIDEX_CDP_PRE_ACTIVATE", "off")
    assert SliderSolver._cdp_pre_activate_enabled() is False


@pytest.mark.asyncio
async def test_connect_existing_browser_does_not_wake_by_default(monkeypatch):
    """默认不主动 activate 任何标签:消除「打开前先切一遍 tab」的抖动。"""
    import urllib.request

    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/punish?x5secdata=x"
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePWFactory(browser))
    monkeypatch.delenv("SLIDEX_CDP_PRE_ACTIVATE", raising=False)

    def _boom(*a, **k):
        raise AssertionError("default path must not touch /json targets")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    await solver._connect_existing_browser("http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x")
    assert solver.page in context.created_pages


class _FlakyChromium:
    """首次 connect 失败、第二次成功——验证有限重试。"""

    def __init__(self, browser, failures=1):
        self._browser = browser
        self._failures = failures
        self.calls = 0
        self.timeouts = []

    async def connect_over_cdp(self, endpoint, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.calls <= self._failures:
            raise RuntimeError("BrowserType.connect_over_cdp: Timeout 45000ms exceeded.")
        return self._browser


class _FlakyPWFactory:
    def __init__(self, chromium):
        self._chromium = chromium

    async def start(self):
        class _PW:
            pass

        pw = _PW()
        pw.chromium = self._chromium
        pw.stopped = False

        async def _stop():
            pw.stopped = True

        pw.stop = _stop
        return pw


@pytest.mark.asyncio
async def test_connect_existing_browser_retries_once_after_wake(monkeypatch):
    """connect 超时(多为冻结标签)时唤醒一次并重试,第二次成功即正常继续。"""
    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/punish?x5secdata=x"
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FlakyChromium(browser, failures=1)
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FlakyPWFactory(chromium))
    monkeypatch.delenv("SLIDEX_CDP_PRE_ACTIVATE", raising=False)

    wake_calls = []
    monkeypatch.setattr(
        SliderSolver, "_wake_cdp_targets",
        staticmethod(lambda ep: wake_calls.append(ep) or 1),
    )

    await solver._connect_existing_browser("http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x")
    assert chromium.calls == 2
    assert wake_calls  # 重试前唤醒过一次
    assert solver.page in context.created_pages


@pytest.mark.asyncio
async def test_connect_existing_browser_skips_retry_when_wake_activates_nothing(monkeypatch):
    """唤醒一个 page 目标都没成功 → 失败与冻结标签无关,直接上抛,不再白等第二个超时。"""
    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/punish?x5secdata=x"
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FlakyChromium(browser, failures=5)
    factory = _FlakyPWFactory(chromium)
    started = []
    _orig_start = factory.start

    async def _start():
        pw = await _orig_start()
        started.append(pw)
        return pw

    factory.start = _start
    monkeypatch.setattr(solver_module, "async_playwright", lambda: factory)
    monkeypatch.setattr(
        SliderSolver, "_wake_cdp_targets",
        staticmethod(lambda ep: 0),
    )

    with pytest.raises(RuntimeError):
        await solver._connect_existing_browser(
            "http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x"
        )
    assert chromium.calls == 1  # 唤醒没做成 → 不重试
    assert solver._playwright is None
    assert started and started[0].stopped is True


@pytest.mark.asyncio
async def test_connect_existing_browser_raises_after_second_failure(monkeypatch):
    """唤醒成功、重试仍失败则上抛首个异常,并停掉 playwright,不白等第三个超时。"""
    solver = _make_solver()
    solver._verify_url = "https://h5api.m.goofish.com/punish?x5secdata=x"
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(context)
    chromium = _FlakyChromium(browser, failures=5)
    factory = _FlakyPWFactory(chromium)
    started = []
    _orig_start = factory.start

    async def _start():
        pw = await _orig_start()
        started.append(pw)
        return pw

    factory.start = _start
    monkeypatch.setattr(solver_module, "async_playwright", lambda: factory)
    monkeypatch.setattr(
        SliderSolver, "_wake_cdp_targets",
        staticmethod(lambda ep: 2),
    )

    with pytest.raises(RuntimeError):
        await solver._connect_existing_browser(
            "http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x"
        )
    assert chromium.calls == 2  # 只重试一次
    assert solver._playwright is None
    assert started and started[0].stopped is True


# ---------------------------------------------------------------------------
# 0.6.37: 连接前冻结标签探针 + 解冻 + 可选重建自愈
#
# 动机:0.6.32 的兜底唤醒只在 connect 失败之后跑,必然先白等一个完整握手超时
# (默认 45s),而且它切的是**全部** page 标签。裸 WebSocket 探针可以在 connect
# 之前先问一句"你还活着吗",于是只对真冻的标签动手。
# ---------------------------------------------------------------------------

_WS_A = "ws://127.0.0.1:9222/devtools/page/alive"
_WS_B = "ws://127.0.0.1:9222/devtools/page/frozen-b"
_WS_C = "ws://127.0.0.1:9222/devtools/page/frozen-c"

_PAGE_TARGETS = [
    {"id": "alive", "type": "page", "title": "A", "url": "https://a.example/", "webSocketDebuggerUrl": _WS_A},
    {"id": "frozen-b", "type": "page", "title": "B", "url": "https://b.example/", "webSocketDebuggerUrl": _WS_B},
    {"id": "frozen-c", "type": "page", "title": "C", "url": "https://c.example/#x", "webSocketDebuggerUrl": _WS_C},
    {"id": "sw", "type": "service_worker", "url": "https://a.example/sw.js"},
]


class _FakeWS:
    """裸 WS 对端:记录发出去的帧,按需给应答或抛错。"""

    def __init__(self, reply=None, exc=None):
        self.sent = []
        self.closed = False
        self._reply = reply
        self._exc = exc

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._exc is not None:
            raise self._exc
        return self._reply

    async def close(self):
        self.closed = True


class _FakeWSConn:
    """既可 `await` 也可 `async with` —— 探针走的是 await 形式。"""

    def __init__(self, ws):
        self._ws = ws

    def __await__(self):
        async def _connected():
            return self._ws

        return _connected().__await__()

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc_info):
        return False


def _install_websockets(monkeypatch, handler):
    """替换 sys.modules["websockets"];handler(url, kwargs) -> _FakeWS,返回调用记录。"""
    calls = []

    class _WSModule:
        @staticmethod
        def connect(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeWSConn(handler(url, kwargs))

    monkeypatch.setitem(sys.modules, "websockets", _WSModule)
    return calls


def _install_cdp_http(monkeypatch, targets, json_error=None):
    """假 DevTools HTTP:GET /json 返回 targets,其余返回 200;记录 (method, url)。"""
    import io
    import json as _json
    import urllib.request
    from urllib.request import Request

    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        calls.append((getattr(req, "method", None), url))
        if url.endswith("/json"):
            if json_error is not None:
                raise json_error
            return io.BytesIO(_json.dumps(targets).encode("utf-8"))
        return io.BytesIO(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _alive_reply():
    return '{"id":1,"result":{"type":"number","value":1}}'


def _timeout_handler(url, kwargs):
    return _FakeWS(exc=asyncio.TimeoutError())


def test_cdp_probe_frozen_defaults_off_and_env_enables(monkeypatch):
    """探针默认关闭——它会切前台(尽管只切真冻的),不能替用户决定。"""
    monkeypatch.delenv("SLIDEX_CDP_PROBE_FROZEN", raising=False)
    assert SliderSolver._cdp_probe_frozen_enabled() is False
    monkeypatch.setenv("SLIDEX_CDP_PROBE_FROZEN", "1")
    assert SliderSolver._cdp_probe_frozen_enabled() is True
    monkeypatch.setenv("SLIDEX_CDP_PROBE_FROZEN", "off")
    assert SliderSolver._cdp_probe_frozen_enabled() is False


def test_cdp_rebuild_frozen_defaults_off_and_env_enables(monkeypatch):
    """重建会关掉用户标签,默认必须关闭,且只能显式开启。"""
    monkeypatch.delenv("SLIDEX_CDP_REBUILD_FROZEN", raising=False)
    assert SliderSolver._cdp_rebuild_frozen_enabled() is False
    monkeypatch.setenv("SLIDEX_CDP_REBUILD_FROZEN", "true")
    assert SliderSolver._cdp_rebuild_frozen_enabled() is True
    monkeypatch.setenv("SLIDEX_CDP_REBUILD_FROZEN", "0")
    assert SliderSolver._cdp_rebuild_frozen_enabled() is False


def test_cdp_http_base_maps_ws_and_bare_endpoints():
    """/json 一族只挂 http(s),ws/wss 与裸 host:port 都要能映射过去。"""
    assert SliderSolver._cdp_http_base("http://127.0.0.1:9222") == "http://127.0.0.1:9222"
    assert SliderSolver._cdp_http_base("ws://127.0.0.1:9222/devtools/browser/abc") == "http://127.0.0.1:9222"
    assert SliderSolver._cdp_http_base("wss://tunnel.example:443/devtools/browser/a") == "https://tunnel.example:443"
    assert SliderSolver._cdp_http_base("127.0.0.1:9222") == "http://127.0.0.1:9222"
    assert SliderSolver._cdp_http_base("") is None


def test_page_targets_filters_type_and_ws_url():
    assert [t["id"] for t in SliderSolver._page_targets(_PAGE_TARGETS)] == ["alive", "frozen-b", "frozen-c"]
    assert [t["id"] for t in SliderSolver._page_targets(_PAGE_TARGETS, require_ws=True)] == [
        "alive", "frozen-b", "frozen-c",
    ]
    assert SliderSolver._page_targets(None) == []
    assert SliderSolver._page_targets([{"id": "x", "type": "page"}], require_ws=True) == []
    assert SliderSolver._page_targets(["junk", {"type": "page"}]) == []


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_true_only_on_reply(monkeypatch):
    """判活标准是"收到应答",不是"WS 连得上"——冻结标签的 WS 握手照样能成功。"""
    calls = _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_cdp_target_alive(_WS_A) is True
    assert calls and calls[0][0] == _WS_A
    assert calls[0][1]["open_timeout"] == SliderSolver._PROBE_TIMEOUT_S


@pytest.mark.asyncio
async def test_probe_respects_env_budget(monkeypatch):
    """env 覆盖的预算必须真的落到 websockets.connect 上,否则这个旋钮只是个摆设。"""
    monkeypatch.setenv("SLIDEX_CDP_PROBE_TIMEOUT", "11.5")
    calls = _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_cdp_target_alive(_WS_A) is True
    assert calls[0][1]["open_timeout"] == 11.5
    assert calls[0][1]["close_timeout"] == 11.5


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_false_on_timeout(monkeypatch):
    _install_websockets(monkeypatch, _timeout_handler)
    assert await SliderSolver._probe_cdp_target_alive(_WS_B) is False


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_false_on_connect_error(monkeypatch):
    def handler(url, kw):
        raise OSError("ws handshake failed")

    _install_websockets(monkeypatch, handler)
    assert await SliderSolver._probe_cdp_target_alive(_WS_B) is False


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_false_on_garbage_reply(monkeypatch):
    """应答不是本探针的 id 就不能算活(比如撞上了别人的 WS 会话)。"""
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply="not json"))
    assert await SliderSolver._probe_cdp_target_alive(_WS_A) is False
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply='{"id":7,"result":{}}'))
    assert await SliderSolver._probe_cdp_target_alive(_WS_A) is False


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_false_without_websockets(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", None)
    assert await SliderSolver._probe_cdp_target_alive(_WS_A) is False


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_false_without_ws_url(monkeypatch):
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_cdp_target_alive("") is False


@pytest.mark.asyncio
async def test_probe_cdp_target_state_tristate(monkeypatch):
    """三态:应答=alive、只连不答=frozen、连不上=unknown。

    「连不上」绝不能折成「冻了」—— 那会让自愈把所有标签都切一遍前台。
    """
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_cdp_target_state(_WS_A) == "alive"

    _install_websockets(monkeypatch, _timeout_handler)
    assert await SliderSolver._probe_cdp_target_state(_WS_A) == "frozen"

    def boom(url, kw):
        raise OSError("handshake refused")

    _install_websockets(monkeypatch, boom)
    assert await SliderSolver._probe_cdp_target_state(_WS_A) == "unknown"

    monkeypatch.setitem(sys.modules, "websockets", None)
    assert await SliderSolver._probe_cdp_target_state(_WS_A) == "unknown"
    assert await SliderSolver._probe_cdp_target_state("") == "unknown"


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_noop_when_no_handshake_succeeds(monkeypatch):
    """安全阀:所有目标都连不上(探针本身不可用/版本不兼容)时不做任何判定。

    否则「探针坏掉」会被翻译成「所有标签都冻了」,把用户每个 tab 都切一遍前台。
    """
    _install_cdp_http(monkeypatch, _PAGE_TARGETS)

    def boom(url, kw):
        raise OSError("handshake refused")

    _install_websockets(monkeypatch, boom)
    assert await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222") == []


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_counts_handshake_failure_as_frozen_when_probe_works(monkeypatch):
    """探针本身可用(有目标应答)时,连不上的那个目标仍算冻结——它同样会挂住握手。"""
    _install_cdp_http(monkeypatch, _PAGE_TARGETS)

    def handler(url, kw):
        if url == _WS_A:
            return _FakeWS(reply=_alive_reply())
        if url == _WS_B:
            raise OSError("handshake refused")
        return _FakeWS(exc=asyncio.TimeoutError())

    _install_websockets(monkeypatch, handler)
    frozen = await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222")
    assert [t["id"] for t in frozen] == ["frozen-b", "frozen-c"]


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_returns_only_unresponsive_pages(monkeypatch):
    _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    probed = []

    def handler(url, kw):
        probed.append(url)
        return _FakeWS(reply=_alive_reply()) if url == _WS_A else _FakeWS(exc=asyncio.TimeoutError())

    _install_websockets(monkeypatch, handler)
    frozen = await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222")
    assert [t["id"] for t in frozen] == ["frozen-b", "frozen-c"]
    assert set(probed) == {_WS_A, _WS_B, _WS_C}  # service_worker 不在探活之列


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_noop_without_websockets(monkeypatch):
    """没装 websockets 时绝不能把全部标签当冻结——那会退化成"全切前台"。"""
    _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    monkeypatch.setitem(sys.modules, "websockets", None)
    assert await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222") == []


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_noop_when_endpoint_unreachable(monkeypatch):
    _install_cdp_http(monkeypatch, [], json_error=ConnectionRefusedError("refused"))
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222") == []


@pytest.mark.asyncio
async def test_probe_frozen_cdp_targets_noop_when_pages_lack_ws_url(monkeypatch):
    _install_cdp_http(monkeypatch, [{"id": "p", "type": "page", "url": "https://a.example/"}])
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await SliderSolver._probe_frozen_cdp_targets("http://127.0.0.1:9222") == []


@pytest.mark.asyncio
async def test_probe_cdp_target_alive_against_real_ws_server():
    """真 websockets 服务端对照(不用 fake):应答的判活、只连不答的判冻。

    fake 能证明逻辑分支,但证明不了 `websockets.connect(open_timeout=/close_timeout=)`
    这套实参在当前装的版本上真的成立 —— 这条测试补上那一段。
    """
    ws_lib = pytest.importorskip("websockets")
    import json as _json

    async def reply_handler(ws, *args):
        try:
            async for raw in ws:
                msg = _json.loads(raw)
                await ws.send(_json.dumps({"id": msg.get("id"), "result": {"type": "number", "value": 1}}))
        except Exception:
            pass

    async def silent_handler(ws, *args):
        # 收下帧但不回:模拟冻结标签「WS 连得上、渲染进程不答」
        try:
            async for _ in ws:
                await asyncio.sleep(1.5)
        except Exception:
            pass

    async with ws_lib.serve(reply_handler, "127.0.0.1", 0) as reply_srv:
        async with ws_lib.serve(silent_handler, "127.0.0.1", 0) as silent_srv:
            reply_port = reply_srv.sockets[0].getsockname()[1]
            silent_port = silent_srv.sockets[0].getsockname()[1]
            assert await SliderSolver._probe_cdp_target_alive(f"ws://127.0.0.1:{reply_port}/devtools/page/a") is True
            assert await SliderSolver._probe_cdp_target_alive(
                f"ws://127.0.0.1:{silent_port}/devtools/page/b", timeout_s=0.3
            ) is False


def test_rebuild_cdp_target_puts_new_then_closes_old(monkeypatch):
    """Chrome 111+ 的 /json/new 只接受 PUT;URL 必须整段 quote,否则 & / # 会被截断。"""
    from urllib.parse import quote

    calls = _install_cdp_http(monkeypatch, [])
    url = "https://b.example/q?a=1&b=2#frag"
    assert SliderSolver._rebuild_cdp_target("http://127.0.0.1:9222", {"id": "frozen-b", "url": url}) is True
    assert [m for m, _ in calls] == ["PUT", None]  # 先开后关,顺序不能反
    assert calls[0][1] == "http://127.0.0.1:9222/json/new?" + quote(url, safe="")
    assert calls[1][1] == "http://127.0.0.1:9222/json/close/frozen-b"


def test_rebuild_cdp_target_false_when_reopen_fails(monkeypatch):
    """重开失败时不得再关旧标签——否则用户的页就被彻底弄丢了。"""
    import urllib.request
    from urllib.request import Request

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url if isinstance(req, Request) else str(req))
        raise OSError("405 Method Not Allowed")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    target = {"id": "frozen-b", "url": "https://b.example/"}
    assert SliderSolver._rebuild_cdp_target("http://127.0.0.1:9222", target) is False
    assert len(calls) == 1  # 没有 /json/close


def test_rebuild_cdp_target_defaults_to_about_blank(monkeypatch):
    calls = _install_cdp_http(monkeypatch, [])
    assert SliderSolver._rebuild_cdp_target("http://127.0.0.1:9222", {"id": "frozen-b"}) is True
    assert calls[0][1] == "http://127.0.0.1:9222/json/new?about%3Ablank"


@pytest.mark.asyncio
async def test_self_heal_activates_only_frozen_targets(monkeypatch):
    """核心承诺:活标签绝不被切到前台,只有真冻的那些被 activate。"""
    solver = _make_solver()
    calls = _install_cdp_http(monkeypatch, _PAGE_TARGETS)

    def handler(url, kw):
        return _FakeWS(reply=_alive_reply()) if url == _WS_A else _FakeWS(exc=asyncio.TimeoutError())

    _install_websockets(monkeypatch, handler)
    healed = await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222")
    assert healed == 2
    activates = sorted(u for _, u in calls if "/json/activate/" in u)
    assert activates == [
        "http://127.0.0.1:9222/json/activate/frozen-b",
        "http://127.0.0.1:9222/json/activate/frozen-c",
    ]


@pytest.mark.asyncio
async def test_self_heal_noop_when_nothing_frozen(monkeypatch):
    solver = _make_solver()
    calls = _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    _install_websockets(monkeypatch, lambda url, kw: _FakeWS(reply=_alive_reply()))
    assert await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222") == 0
    assert not [u for _, u in calls if "/json/activate/" in u]


@pytest.mark.asyncio
async def test_self_heal_never_rebuilds_by_default(monkeypatch):
    """默认绝不关闭用户标签:解冻后不再复探、不重建。"""
    solver = _make_solver()
    monkeypatch.delenv("SLIDEX_CDP_REBUILD_FROZEN", raising=False)
    calls = _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    _install_websockets(monkeypatch, _timeout_handler)
    assert await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222") == 3
    assert not [u for _, u in calls if "/json/close/" in u or "/json/new?" in u]


@pytest.mark.asyncio
async def test_self_heal_rebuilds_still_frozen_when_enabled(monkeypatch):
    """SLIDEX_CDP_REBUILD_FROZEN=1:复探仍冻的标签才重建,URL 用原 URL。"""
    solver = _make_solver()
    monkeypatch.setattr(SliderSolver, "_REBUILD_SETTLE_S", 0.0)
    monkeypatch.setenv("SLIDEX_CDP_REBUILD_FROZEN", "1")
    calls = _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    _install_websockets(monkeypatch, _timeout_handler)

    assert await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222") == 6  # 3 解冻 + 3 重建
    reopened = [u for _, u in calls if "/json/new?" in u]
    assert all(m == "PUT" for m, u in calls if "/json/new?" in u)
    assert "http://127.0.0.1:9222/json/new?https%3A%2F%2Fc.example%2F%23x" in reopened
    closed = sorted(u.rsplit("/", 1)[-1] for _, u in calls if "/json/close/" in u)
    assert closed == ["alive", "frozen-b", "frozen-c"]


@pytest.mark.asyncio
async def test_self_heal_skips_rebuild_when_second_probe_comes_back_alive(monkeypatch):
    """解冻是异步生效的:复探已活就不许再动用户的标签。"""
    solver = _make_solver()
    monkeypatch.setattr(SliderSolver, "_REBUILD_SETTLE_S", 0.0)
    monkeypatch.setenv("SLIDEX_CDP_REBUILD_FROZEN", "1")
    calls = _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    rounds = {"n": 0}

    def handler(url, kw):
        if url == _WS_A:
            return _FakeWS(reply=_alive_reply())
        rounds["n"] += 1
        if rounds["n"] <= 2:  # 首轮:B、C 都冻
            return _FakeWS(exc=asyncio.TimeoutError())
        return _FakeWS(reply=_alive_reply())  # 复探:已解冻

    _install_websockets(monkeypatch, handler)
    assert await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222") == 2
    assert not [u for _, u in calls if "/json/close/" in u or "/json/new?" in u]


@pytest.mark.asyncio
async def test_self_heal_rebuild_failure_is_tolerated(monkeypatch):
    """重建失败(旧标签关不掉/新标签开不出)不影响返回值语义,也不抛异常。"""
    solver = _make_solver()
    monkeypatch.setattr(SliderSolver, "_REBUILD_SETTLE_S", 0.0)
    monkeypatch.setenv("SLIDEX_CDP_REBUILD_FROZEN", "1")
    _install_cdp_http(monkeypatch, _PAGE_TARGETS)
    _install_websockets(monkeypatch, _timeout_handler)
    monkeypatch.setattr(SliderSolver, "_rebuild_cdp_target", staticmethod(lambda ep, t: False))
    assert await solver._self_heal_frozen_cdp_tabs("http://127.0.0.1:9222") == 3


@pytest.mark.asyncio
async def test_connect_existing_browser_self_heals_before_connect_when_enabled(monkeypatch):
    """SLIDEX_CDP_PROBE_FROZEN=1 时走探针路径,且不再叠加"全量切前台"的 pre-activate。"""
    solver = _make_solver()
    context = _FakeContext(_FakePage())
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePWFactory(_FakeBrowser(context)))
    monkeypatch.setenv("SLIDEX_CDP_PROBE_FROZEN", "1")
    monkeypatch.setenv("SLIDEX_CDP_PRE_ACTIVATE", "1")

    order = []

    async def _heal(endpoint):
        order.append(("heal", endpoint))
        return 2

    monkeypatch.setattr(solver, "_self_heal_frozen_cdp_tabs", _heal)
    monkeypatch.setattr(
        SliderSolver, "_wake_cdp_targets",
        staticmethod(lambda ep: order.append(("wake", ep)) or 3),
    )

    await solver._connect_existing_browser("http://localhost:9222", "")
    assert order == [("heal", "http://localhost:9222")]
    assert solver.page in context.created_pages


@pytest.mark.asyncio
async def test_connect_existing_browser_survives_self_heal_failure(monkeypatch):
    """自愈只是优化:探针/解冻抛错也必须照常完成连接。"""
    solver = _make_solver()
    context = _FakeContext(_FakePage())
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePWFactory(_FakeBrowser(context)))
    monkeypatch.setenv("SLIDEX_CDP_PROBE_FROZEN", "1")

    async def _heal(endpoint):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(solver, "_self_heal_frozen_cdp_tabs", _heal)
    await solver._connect_existing_browser("http://localhost:9222", "")
    assert solver.page in context.created_pages

