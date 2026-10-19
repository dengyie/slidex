#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境变量容错解析。

与 ``config.py`` 的 ``_safe_int`` / ``_safe_float`` 同一约定：空值、非数字、
非有限值、越界一律回落默认值并告警，**绝不因为一个坏 env 抛异常**。

这条约定对本模块的两个用途尤其关键：

* ``solver.py`` 的 ``SOLVE_WATCHDOG_TIMEOUT_S`` / ``MANUAL_VOUCHER_WAIT_S``
  在**类体（import 期）**求值 —— 裸 float() 遇到 ``SLIDEX_SOLVE_WATCHDOG=abc``
  会让 ``import slidex.solver`` 直接失败，整个库不可用；
* 运行期的解析点若抛异常，会把一次本可继续的求解变成硬失败。

int 走 ``int(float(raw))``：容忍 ``"5000.0"`` 这类浮点写法的 env（容器编排
里很常见），并显式接住 ``OverflowError``（``"inf"`` 会触发）。
"""

from __future__ import annotations

import math
import os
from typing import Optional

from loguru import logger

__all__ = ["env_float", "env_int"]


def _raw(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _reject(name: str, raw: str, default, why: str):
    logger.warning(f"[env] {name}={raw!r} {why}，回落默认值 {default}")
    return default


def env_float(name: str, default: float, *, minimum: Optional[float] = None,
              maximum: Optional[float] = None) -> float:
    """读浮点 env；非法/非有限/越界回落 default。minimum/maximum 均为闭区间。"""
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _reject(name, raw, default, "不是合法数字")
    if not math.isfinite(value):
        return _reject(name, raw, default, "不是有限值")
    if minimum is not None and value < minimum:
        return _reject(name, raw, default, f"低于下限 {minimum}")
    if maximum is not None and value > maximum:
        return _reject(name, raw, default, f"高于上限 {maximum}")
    return value


def env_int(name: str, default: int, *, minimum: Optional[int] = None,
            maximum: Optional[int] = None) -> int:
    """读整型 env；容忍 ``"5000.0"``；非法/非有限/越界回落 default。"""
    raw = _raw(name)
    if raw is None:
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return _reject(name, raw, default, "不是合法整数")
    if minimum is not None and value < minimum:
        return _reject(name, raw, default, f"低于下限 {minimum}")
    if maximum is not None and value > maximum:
        return _reject(name, raw, default, f"高于上限 {maximum}")
    return value
