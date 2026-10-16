"""0.6.15: CDP 模式连接外部真实浏览器时的会话身份保障。

punish 的 x5secdata 绑定 bot 侧会话：借外部浏览器（用户 PC Chrome 经反向
隧道）的是设备指纹，连接后必须注入 bot 的账号会话 cookie，否则票据发到
浏览器自己的会话上，bot 合并无效。
"""
import asyncio
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


def test_wake_cdp_targets_skips_active_and_restores_focus(monkeypatch):
    """已激活标签不再被切走;结束后把焦点还给用户原标签(减少可见抖动)。"""
    import io
    import json
    from urllib.request import Request

    called_urls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if isinstance(req, Request) else str(req)
        called_urls.append(url)
        if url.endswith("/json"):
            targets = [
                {"id": "user-tab", "type": "page", "active": True},
                {"id": "frozen-a", "type": "page"},
                {"id": "frozen-b", "type": "page", "active": False},
            ]
            return io.BytesIO(json.dumps(targets).encode("utf-8"))
        return io.BytesIO(b"Target activated")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    woken = SliderSolver._wake_cdp_targets("http://127.0.0.1:9222", restore_focus=True)
    assert woken == 2  # 只唤醒两个非激活标签
    assert "http://127.0.0.1:9222/json/activate/frozen-a" in called_urls
    assert "http://127.0.0.1:9222/json/activate/frozen-b" in called_urls
    # 焦点还原:被唤醒的冻结标签先切前,最后必须切回用户原标签
    assert called_urls[-1] == "http://127.0.0.1:9222/json/activate/user-tab"


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

    async def connect_over_cdp(self, endpoint, timeout=None):
        self.calls += 1
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
    import urllib.request

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
        staticmethod(lambda ep, restore_focus=True: wake_calls.append(ep) or 1),
    )

    await solver._connect_existing_browser("http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x")
    assert chromium.calls == 2
    assert wake_calls  # 重试前唤醒过一次
    assert solver.page in context.created_pages


@pytest.mark.asyncio
async def test_connect_existing_browser_raises_after_second_failure(monkeypatch):
    """两次都失败则上抛首个异常,并停掉 playwright,不白等第三个超时。"""
    import urllib.request

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
        staticmethod(lambda ep, restore_focus=True: 0),
    )

    with pytest.raises(RuntimeError):
        await solver._connect_existing_browser(
            "http://localhost:9222", "https://h5api.m.goofish.com/punish?x5secdata=x"
        )
    assert chromium.calls == 2  # 只重试一次
    assert solver._playwright is None
    assert started and started[0].stopped is True

