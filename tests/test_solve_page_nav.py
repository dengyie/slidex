"""solve 页面导航必须容忍 networkidle 假超时（不得中止整个 solve）。

处罚页后台信标会让 networkidle 永不达成。`_load_page` 早已走 `_goto_page`
（超时降级 domcontentloaded 后继续，页面是否真的渲染出滑块交给后续 page_state
探测判定），但 `solve_on_page` 的 page_url 分支仍是裸
`page.goto(..., wait_until="networkidle", timeout=45000)` —— 该 Timeout 会被
`solve_on_page` 的兜底 except 吞成 "Playwright page solve error"，一次本可继续的
求解直接返回失败。回归点：page_url 分支走 `self._goto_page`，且不再有裸 goto。
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

import slidex.solver as solver_mod
from slidex.solver import SliderSolver

URL = "https://h5api.m.goofish.com/h5/api/_____tmd_____/punish?x5step=2&action=captcha"


class _FakeTimeout(Exception):
    """类型名含 Timeout，匹配降级判据。"""


class _RecordingPage:
    """按调用顺序回放 goto 结果；None 表示成功，异常表示抛错。"""

    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls.append((url, wait_until, timeout))
        if self._results:
            exc = self._results.pop(0)
            if exc is not None:
                raise exc
        return None

    @property
    def waits(self):
        return [call[1] for call in self.calls]


class _AsyncioShim:
    """只替换 solver 模块里的 asyncio.sleep，不动真 asyncio 模块（免扰事件循环）。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def sleep(self, *_args, **_kwargs):
        return None


@pytest.fixture
def fast_sleep(monkeypatch):
    monkeypatch.setattr(solver_mod, "asyncio", _AsyncioShim(asyncio))


class _FakeCdp:
    def __init__(self):
        self.detach = mock.AsyncMock()


class _FakeContext:
    def __init__(self):
        self.cdp = _FakeCdp()

    async def new_cdp_session(self, page):
        return self.cdp


class _SolvePage:
    """solve_on_page 需要的最小 Page 面（on/remove_listener/context/url）。"""

    def __init__(self, url=""):
        self.context = _FakeContext()
        self.url = url
        self.handlers = []
        self.goto = mock.AsyncMock()

    def on(self, event_name, handler):
        assert event_name in ("response", "console")
        self.handlers.append(handler)

    def remove_listener(self, event_name, handler):
        assert event_name in ("response", "console")
        self.handlers.remove(handler)


@pytest.mark.asyncio
async def test_goto_page_success_uses_networkidle(fast_sleep):
    page = _RecordingPage([None])
    await SliderSolver._goto_page(page, "https://example.test/slider")
    assert page.waits == ["networkidle"]


@pytest.mark.asyncio
async def test_goto_page_timeout_degrades_to_domcontentloaded(fast_sleep):
    page = _RecordingPage([_FakeTimeout("Page.goto: Timeout 45000ms exceeded."), None])
    await SliderSolver._goto_page(page, URL)
    assert page.waits == ["networkidle", "domcontentloaded"]


@pytest.mark.asyncio
async def test_goto_page_non_timeout_error_propagates(fast_sleep):
    page = _RecordingPage([RuntimeError("net::ERR_ABORTED")])
    with pytest.raises(RuntimeError, match="ERR_ABORTED"):
        await SliderSolver._goto_page(page, URL)


@pytest.mark.asyncio
async def test_solve_on_page_routes_navigation_through_goto_page(fast_sleep):
    page = _SolvePage(url=URL)
    solver = SliderSolver(cookie_id="envtest")
    solver._run_solve_loop = mock.AsyncMock(return_value=(True, {"session": "abc"}))
    solver._maybe_record_success = mock.AsyncMock()
    solver._finalize_telemetry = mock.Mock()
    solver._goto_page = mock.AsyncMock()

    success, cookies = await solver.solve_on_page(page, page_url=URL)

    solver._goto_page.assert_awaited_once_with(page, URL)
    page.goto.assert_not_awaited()
    assert success is True
    assert cookies == {"session": "abc"}


@pytest.mark.asyncio
async def test_solve_on_page_continues_after_fake_networkidle_timeout(fast_sleep):
    """真 _goto_page + 会假超时的 page：降级后 solve 必须继续走到 _run_solve_loop。"""
    timeout_page = _RecordingPage([_FakeTimeout("Page.goto: Timeout 45000ms exceeded."), None])
    page = _SolvePage(url=URL)
    page.goto = timeout_page.goto  # 让真实 _goto_page 撞上假超时
    solver = SliderSolver(cookie_id="envtest")
    solver._run_solve_loop = mock.AsyncMock(return_value=(True, {"session": "abc"}))
    solver._maybe_record_success = mock.AsyncMock()
    solver._finalize_telemetry = mock.Mock()

    success, _cookies = await solver.solve_on_page(page, page_url=URL)

    solver._run_solve_loop.assert_awaited_once_with(URL)  # 没有被 Timeout 打断
    assert timeout_page.waits == ["networkidle", "domcontentloaded"]
    assert success is True
