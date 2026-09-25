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
