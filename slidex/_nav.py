#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导航等待超时判定 —— 全库唯一实现。

networkidle 在处罚/风控页因后台信标永不静默，「Timeout」类异常属于**假超时**：
导航其实已发起/页面其实已可用，允许降级 domcontentloaded 继续，页面可用性交给
后续探测判定。其余导航错误必须照抛，由调用点各自的兜底处理。

三个使用点（solver._goto_page、stealth._goto_login_page、
stealth._reload_with_fallback）共享本谓词，防止判定标准漂移。
"""

from __future__ import annotations

__all__ = ["is_nav_timeout"]


def is_nav_timeout(exc: BaseException) -> bool:
    """是否为导航/刷新等待超时（Playwright TimeoutError 及同类命名变体）。

    双判据：类名含 "Timeout" 覆盖 Playwright 的 ``TimeoutError``；
    限定类型名含 ``TimeoutError`` 兜住类名不含 "Timeout" 但限定路径含
    ``TimeoutError`` 的变体。
    """
    return "Timeout" in type(exc).__name__ or "TimeoutError" in str(type(exc))
