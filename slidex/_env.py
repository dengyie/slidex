#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境变量/原始字符串的容错数值解析 —— 本库的唯一实现。

约定：空值、非数字、非有限值、越界一律回落默认值并告警，**绝不因为一个坏
值抛异常**。

这条约定对本库的两类用途尤其关键：

* ``solver.py`` 的 ``SOLVE_WATCHDOG_TIMEOUT_S`` / ``MANUAL_VOUCHER_WAIT_S``
  在**类体（import 期）**求值 —— 裸 float() 遇到 ``SLIDEX_SOLVE_WATCHDOG=abc``
  会让 ``import slidex.solver`` 直接失败，整个库不可用；
* 运行期的解析点若抛异常，会把一次本可继续的求解变成硬失败。

``env_float`` / ``env_int`` 按**变量名**读 ``os.environ``；
``parse_float`` / ``parse_int`` 按**原始字符串**解析（调用方自行取值，例如
``config.py`` 的 ``_safe_int`` / ``_safe_float`` 就委托到这里）。两族函数
共享同一套校验与回落逻辑，保证全库只有一个容错契约。

int 走 ``int(float(raw))``：容忍 ``"5000.0"`` 这类浮点写法的 env（容器编排
里很常见），并显式接住 ``OverflowError``（``"inf"`` 会触发）。

区间语义（对两族函数一致）：

* ``minimum`` / ``maximum`` 为**闭区间**，越界回落 default；
* ``exclusive_minimum`` 为**开下限**（值必须严格大于），``0`` 无意义而
  ``0.5`` 合法这类旋钮（如 CDP 超时）用它表达「非正数回落默认」。
"""

from __future__ import annotations

import math
import os
from typing import Optional

from loguru import logger

__all__ = ["env_bool", "env_float", "env_int", "parse_bool", "parse_float", "parse_int"]

# 布尔开关的真/假字面量。刻意只认这些常见写法：未知值回落默认并告警，
# 而不是把任意非空串当 True（那样 "disabled" 会被当成开启）。
_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "y", "t", "enable", "enabled"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "n", "f", "disable", "disabled"})


def _raw(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _reject(source: str, raw: str, default, why: str):
    logger.warning(f"[env] {source}={raw!r} {why}，回落默认值 {default}")
    return default


def _bounds_ok(value: float, source: str, raw: str, default, *,
               minimum: Optional[float], maximum: Optional[float],
               exclusive_minimum: Optional[float]) -> bool:
    if not math.isfinite(value):
        _reject(source, raw, default, "不是有限值")
        return False
    if minimum is not None and value < minimum:
        _reject(source, raw, default, f"低于下限 {minimum}")
        return False
    if maximum is not None and value > maximum:
        _reject(source, raw, default, f"高于上限 {maximum}")
        return False
    if exclusive_minimum is not None and value <= exclusive_minimum:
        _reject(source, raw, default, f"不大于 {exclusive_minimum}")
        return False
    return True


def parse_float(raw: str, default: float, *, minimum: Optional[float] = None,
                maximum: Optional[float] = None,
                exclusive_minimum: Optional[float] = None) -> float:
    """解析浮点字符串；空串视同未设置（静默回落）；非法/非有限/越界回落并告警。"""
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _reject(str(raw), raw, default, "不是合法数字")
    if _bounds_ok(value, str(raw), raw, default, minimum=minimum, maximum=maximum,
                  exclusive_minimum=exclusive_minimum):
        return value
    return default


def parse_int(raw: str, default: int, *, minimum: Optional[int] = None,
              maximum: Optional[int] = None) -> int:
    """解析整型字符串；空串视同未设置（静默回落）；容忍 ``"5000.0"``。"""
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return _reject(str(raw), raw, default, "不是合法整数")
    if minimum is not None and value < minimum:
        _reject(source=str(raw), raw=raw, default=default, why=f"低于下限 {minimum}")
        return default
    if maximum is not None and value > maximum:
        _reject(source=str(raw), raw=raw, default=default, why=f"高于上限 {maximum}")
        return default
    return value


def parse_bool(raw: str, default: bool) -> bool:
    """解析布尔字符串；空串视同未设置（静默回落）；未知字面量回落并告警。"""
    if raw is None or str(raw).strip() == "":
        return default
    word = str(raw).strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return _reject(str(raw), raw, default, "不是合法布尔值（用 1/0、true/false、on/off）")


def env_bool(name: str, default: bool) -> bool:
    """读布尔 env；空值与未知字面量回落 default（空值静默）。

    空串视同未设置这条契约必须覆盖布尔开关：容器编排里
    ``SLIDEX_TRAJ_POOL_ENABLED=${SLIDEX_TRAJ_POOL_ENABLED:-}`` 在 .env 未设置时
    会注入**空字符串**，而 ``os.environ.get(name, "1") == "1"`` 会把空串判成
    关闭——0.11.3 之前 ``SLIDEX_TRAJ_POOL_ENABLED`` 就这样被静默关掉整个轨迹池。
    """
    raw = _raw(name)
    if raw is None:
        return default
    return parse_bool(raw, default)


def env_float(name: str, default: float, *, minimum: Optional[float] = None,
              maximum: Optional[float] = None,
              exclusive_minimum: Optional[float] = None) -> float:
    """读浮点 env；非法/非有限/越界回落 default。"""
    raw = _raw(name)
    if raw is None:
        return default
    return parse_float(raw, default, minimum=minimum, maximum=maximum,
                       exclusive_minimum=exclusive_minimum)


def env_int(name: str, default: int, *, minimum: Optional[int] = None,
            maximum: Optional[int] = None) -> int:
    """读整型 env；容忍 ``"5000.0"``；非法/非有限/越界回落 default。"""
    raw = _raw(name)
    if raw is None:
        return default
    return parse_int(raw, default, minimum=minimum, maximum=maximum)
