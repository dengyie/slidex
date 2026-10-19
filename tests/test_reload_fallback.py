"""滑块重置回退分支的页面刷新：networkidle 假超时必须降级 domcontentloaded。

`click_to_reset_slider` 失败后会 `page.reload(wait_until='networkidle', timeout=15000)`。
处罚页后台信标让 networkidle 永不静默，这个刷新在**滑块最常出现的页面上天天假失败**：
导航其实已发起，却被判失败，于是丢掉那次 reset 收尾的 1s 稳定等待并刷一条告警。
修复后仅 Timeout 降级，其他错误照抛（外层的 except 仍兜底）。
"""
from __future__ import annotations

import pytest

from slidex.stealth import XianyuSliderStealth


class _FakeTimeout(Exception):
    """类型名含 Timeout，匹配降级判据。"""


class _FakePage:
    """按调用顺序回放 reload 结果；None 表示成功，异常表示抛错。"""

    def __init__(self, results):
        self.calls = []
        self._results = list(results)

    def reload(self, wait_until=None, timeout=None):
        self.calls.append((wait_until, timeout))
        if self._results:
            exc = self._results.pop(0)
            if exc is not None:
                raise exc
        return None

    @property
    def waits(self):
        return [call[0] for call in self.calls]


def test_reload_success_uses_networkidle():
    page = _FakePage([None])
    assert XianyuSliderStealth._reload_with_fallback(page, "t") == "networkidle"
    assert page.waits == ["networkidle"]


def test_reload_timeout_falls_back_to_domcontentloaded():
    page = _FakePage([_FakeTimeout("Page.reload: Timeout 15000ms exceeded."), None])
    assert XianyuSliderStealth._reload_with_fallback(page, "t") == "domcontentloaded"
    assert page.waits == ["networkidle", "domcontentloaded"]


def test_reload_fallback_failure_still_returns_soft_ok():
    # 降级刷新也失败时不再上抛——导航可能已部分生效，交给后续重新探测滑块。
    page = _FakePage([_FakeTimeout("timeout"), RuntimeError("fallback boom")])
    assert XianyuSliderStealth._reload_with_fallback(page, "t") == "domcontentloaded"
    assert page.waits == ["networkidle", "domcontentloaded"]


def test_reload_non_timeout_error_propagates():
    # 非超时错误照抛，保持调用点原有 except 兜底语义。
    page = _FakePage([RuntimeError("Target page, context or browser has been closed")])
    with pytest.raises(RuntimeError, match="has been closed"):
        XianyuSliderStealth._reload_with_fallback(page, "t")
    assert page.waits == ["networkidle"]


def test_reload_helper_is_staticmethod_usable_without_instance():
    # 调用点在实例方法里用 self._reload_with_fallback(...)，静态方法须可直接调用。
    page = _FakePage([None])
    assert XianyuSliderStealth._reload_with_fallback(page) == "networkidle"
