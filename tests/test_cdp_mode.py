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

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    def on(self, event, handler):
        self.handlers[event] = handler

    async def goto(self, url, **kwargs):
        self.gotos.append((url, kwargs))


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.cookies_added = []

    async def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    async def new_cdp_session(self, page):
        raise RuntimeError("no cdp session in fake")


class _FakeBrowser:
    def __init__(self, context):
        self.contexts = [context]


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    async def connect_over_cdp(self, endpoint):
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
    assert solver.context is context and solver.page is page
    assert page.gotos and page.gotos[0][0] == url


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
