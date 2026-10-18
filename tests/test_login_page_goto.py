"""密码登录页导航：networkidle 永不静默时必须降级 domcontentloaded。

处罚/风控页持续跑后台信标，networkidle 无法达成，`page.goto(..., timeout=60000)`
会抛 `Page.goto: Timeout 60000ms exceeded`。历史实现把这个假超时直接上抛，
整条密码登录链路被打断（生产曾因此触发 init-auth 失败循环）。修复后：仅当
等待条件未静默（Timeout）时降级 domcontentloaded 继续，登录表单是否存在交给
后续 `_find_login_form_with_retry` 判定；连接类错误仍按原逻辑重试；其余错误照抛。
"""
from __future__ import annotations

import pytest

import slidex.stealth as stealth_mod
from slidex.stealth import XianyuSliderStealth

LOGIN_URL = "https://www.goofish.com/im"


class _FakeTimeout(Exception):
    """类型名含 Timeout，匹配降级判据。"""


class _FakePage:
    """按调用顺序回放 goto 结果；None 表示成功，异常表示抛错。"""

    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append((url, wait_until, timeout))
        if self._results:
            exc = self._results.pop(0)
            if exc is not None:
                raise exc
        return None

    @property
    def waits(self):
        return [call[1] for call in self.calls]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(stealth_mod.time, "sleep", lambda *_a, **_k: None)


def test_networkidle_success_used_directly():
    page = _FakePage([None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "networkidle"
    assert page.waits == ["networkidle"]


def test_networkidle_timeout_falls_back_to_domcontentloaded():
    page = _FakePage([_FakeTimeout("Page.goto: Timeout 60000ms exceeded."), None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "domcontentloaded"
    assert page.waits == ["networkidle", "domcontentloaded"]


def test_fallback_failure_still_returns_soft_ok():
    # 降级导航也失败时不再上抛——页面可能已部分可用，交由后续表单探测判定。
    page = _FakePage([_FakeTimeout("timeout"), RuntimeError("fallback boom")])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "domcontentloaded"
    assert page.waits == ["networkidle", "domcontentloaded"]


def test_connection_error_retried_then_succeeds():
    page = _FakePage([RuntimeError("net::ERR_CONNECTION_CLOSED"), None])
    assert XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t") == "networkidle"
    assert page.waits == ["networkidle", "networkidle"]


def test_non_retryable_error_raises():
    page = _FakePage([RuntimeError("unexpected navigation failure")])
    with pytest.raises(RuntimeError, match="unexpected navigation failure"):
        XianyuSliderStealth._goto_login_page(page, LOGIN_URL, "t")
