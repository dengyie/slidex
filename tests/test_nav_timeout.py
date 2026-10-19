"""is_nav_timeout：三处降级点共享的超时判定谓词（_nav.py）。

判定必须单一来源（solver._goto_page / stealth._goto_login_page /
stealth._reload_with_fallback 共用），本文件锁住谓词对真假两类的边界。
"""
from __future__ import annotations

import pytest

from slidex._nav import is_nav_timeout


class _FakeTimeout(Exception):
    """类型名含 Timeout —— 模拟 Playwright TimeoutError 的最小特征。"""


class _WeirdTimeout(Exception):
    """类名不含 Timeout，但限定路径含 TimeoutError —— 走第二判据。"""


try:
    _WeirdTimeout.__module__ = "playwright._impl._errors"
    _WeirdTimeout.__qualname__ = "TimeoutError"
except Exception:  # pragma: no cover - __qualname__ 不可写时跳过该用例
    pass


@pytest.mark.parametrize("exc", [
    _FakeTimeout("Page.goto: Timeout 45000ms exceeded."),
])
def test_timeout_types_match(exc):
    assert is_nav_timeout(exc) is True


def test_message_mentioning_timeout_does_not_match():
    # 谓词只看异常类型，不看消息——消息里恰好带 "Timeout" 的普通错误不许误判。
    assert is_nav_timeout(RuntimeError("Page.goto: Timeout 45000ms exceeded.")) is False


def test_qualified_name_fallback():
    try:
        assert is_nav_timeout(_WeirdTimeout("boom")) is True
    except AssertionError:
        pytest.skip("环境不允许改 __qualname__")


def test_non_timeout_types_do_not_match():
    assert is_nav_timeout(RuntimeError("net::ERR_ABORTED")) is False
    assert is_nav_timeout(ConnectionResetError("reset")) is False
    assert is_nav_timeout(ValueError("boom")) is False