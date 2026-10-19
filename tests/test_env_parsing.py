"""环境变量容错解析（slidex._env）与「坏 env 不得让 import 崩」回归。

``config.py`` 早就有 ``_safe_int``/``_safe_float`` 的容错约定，但 ``solver.py`` /
``_trajectory.py`` / ``stealth.py`` 里仍有裸 ``int()``/``float()`` 读 env —— 其中
``SliderSolver.SOLVE_WATCHDOG_TIMEOUT_S`` 与 ``MANUAL_VOUCHER_WAIT_S`` 在**类体**
（import 期）求值，一个 ``SLIDEX_SOLVE_WATCHDOG=abc`` 就能让 ``import slidex.solver``
直接失败、整个库不可用。这里同时锁住两个契约：

* 单元层：坏值/空值/非有限/越界一律回落默认，绝不上抛；
* 集成层：子进程里带坏 env 导入 slidex 主要模块必须成功且取到默认值。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from slidex._env import env_float, env_int, parse_float, parse_int

FLOAT_KEY = "SLIDEX_TEST_ENV_FLOAT"
INT_KEY = "SLIDEX_TEST_ENV_INT"


class TestEnvFloat:
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(FLOAT_KEY, raising=False)
        assert env_float(FLOAT_KEY, 1.5) == 1.5

    def test_valid_value_parsed(self, monkeypatch):
        monkeypatch.setenv(FLOAT_KEY, "7.25")
        assert env_float(FLOAT_KEY, 1.5) == 7.25

    def test_integer_like_value_parsed(self, monkeypatch):
        monkeypatch.setenv(FLOAT_KEY, "600")
        assert env_float(FLOAT_KEY, 1.5) == 600.0

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "1.2.3", "nan", "inf", "-inf"])
    def test_bad_values_fall_back(self, monkeypatch, raw):
        monkeypatch.setenv(FLOAT_KEY, raw)
        assert env_float(FLOAT_KEY, 2.5) == 2.5

    def test_minimum_is_inclusive_and_keeps_zero(self, monkeypatch):
        monkeypatch.setenv(FLOAT_KEY, "0")
        assert env_float(FLOAT_KEY, 2.5, minimum=0.0) == 0.0

    def test_below_minimum_falls_back(self, monkeypatch):
        monkeypatch.setenv(FLOAT_KEY, "-1")
        assert env_float(FLOAT_KEY, 2.5, minimum=0.0) == 2.5

    def test_above_maximum_falls_back(self, monkeypatch):
        monkeypatch.setenv(FLOAT_KEY, "99")
        assert env_float(FLOAT_KEY, 2.5, maximum=10.0) == 2.5


class TestEnvInt:
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(INT_KEY, raising=False)
        assert env_int(INT_KEY, 11) == 11

    def test_valid_value_parsed(self, monkeypatch):
        monkeypatch.setenv(INT_KEY, "5000")
        assert env_int(INT_KEY, 11) == 5000

    def test_float_formatted_value_tolerated(self, monkeypatch):
        # 容器编排里 "5000.0" 很常见，int("5000.0") 会炸，这里必须容忍。
        monkeypatch.setenv(INT_KEY, "5000.0")
        assert env_int(INT_KEY, 11) == 5000

    @pytest.mark.parametrize("raw", ["", "abc", "12abc", "inf", "-inf", "nan"])
    def test_bad_values_fall_back(self, monkeypatch, raw):
        # "inf" 走 float 解析成功但 int() 抛 OverflowError —— 也必须回落。
        monkeypatch.setenv(INT_KEY, raw)
        assert env_int(INT_KEY, 7) == 7

    def test_below_minimum_falls_back(self, monkeypatch):
        monkeypatch.setenv(INT_KEY, "-5")
        assert env_int(INT_KEY, 30, minimum=0) == 30

    def test_above_maximum_falls_back(self, monkeypatch):
        monkeypatch.setenv(INT_KEY, "99")
        assert env_int(INT_KEY, 7, maximum=10) == 7


class TestParsePrimitives:
    """parse_float / parse_int：按原始字符串解析的原语（config._safe_* 的底层）。"""

    def test_empty_is_silent_default(self):
        # 空串视同未设置：静默回落，不打告警（config.from_env 未设 env 时传 ""）。
        assert parse_float("", 1.5) == 1.5
        assert parse_float("   ", 1.5) == 1.5
        assert parse_int("", 11) == 11

    def test_bad_values_fall_back(self):
        assert parse_float("abc", 2.5) == 2.5
        assert parse_float("inf", 2.5) == 2.5
        assert parse_int("5000.0", 7) == 5000
        assert parse_int("12abc", 7) == 7

    def test_exclusive_minimum_rejects_zero_keeps_positive(self):
        assert parse_float("0", 45.0, exclusive_minimum=0.0) == 45.0
        assert parse_float("-3", 45.0, exclusive_minimum=0.0) == 45.0
        assert parse_float("7.5", 45.0, exclusive_minimum=0.0) == 7.5


class TestConfigSafeDelegation:
    """config._safe_int/_safe_float 委托 _env 后，旧语义保持 + 统一容错。"""

    def test_safe_int_keeps_plain_semantics(self):
        from slidex.config import SlidexConfig

        assert SlidexConfig._safe_int("3", 3) == 3
        assert SlidexConfig._safe_int("", 3) == 3
        assert SlidexConfig._safe_int("abc", 3) == 3

    def test_safe_int_tolerates_float_formatted(self):
        # 统一后的行为：config 与 _env 同契约，"5000.0" 不再判坏值。
        from slidex.config import SlidexConfig

        assert SlidexConfig._safe_int("5000.0", 3) == 5000

    def test_safe_float_keeps_positive_rule(self):
        from slidex.config import SlidexConfig

        assert SlidexConfig._safe_float("6.5", 6.0) == 6.5
        assert SlidexConfig._safe_float("0", 6.0) == 6.0  # 非正数回落（非钳制）
        assert SlidexConfig._safe_float("-1", 6.0) == 6.0
        assert SlidexConfig._safe_float("", 180.0) == 180.0

    def test_safe_float_rejects_inf(self):
        # 根因修复：旧实现 float("inf") > 0 会放行 inf 造成挂死风险，现在回落。
        from slidex.config import SlidexConfig

        assert SlidexConfig._safe_float("inf", 180.0) == 180.0


class TestSlideEndHoldRangeToleratesBadEnv:
    def test_bad_values_fall_back_to_defaults(self, monkeypatch):
        from slidex._trajectory import slide_end_hold_range

        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "abc")
        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "not-a-number")
        assert slide_end_hold_range() == (0.45, 1.10)

    def test_keeps_lo_le_hi_invariant(self, monkeypatch):
        from slidex._trajectory import slide_end_hold_range

        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "2.0")
        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "0.5")
        lo, hi = slide_end_hold_range()
        assert lo == 2.0
        assert hi >= lo

    def test_zero_minimum_still_honoured(self, monkeypatch):
        from slidex._trajectory import slide_end_hold_range

        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "0")
        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "0.5")
        assert slide_end_hold_range() == (0.0, 0.5)

    def test_negative_minimum_clamped_to_zero(self, monkeypatch):
        # 原实现的 `max(0.0, lo)` 语义保持不变：负值视同「不握」。
        from slidex._trajectory import slide_end_hold_range

        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "-3")
        monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "1.10")
        assert slide_end_hold_range() == (0.0, 1.10)


_IMPORT_PROBE = (
    "import slidex.solver as s;"
    "assert s.SliderSolver.SOLVE_WATCHDOG_TIMEOUT_S == 600.0, s.SliderSolver.SOLVE_WATCHDOG_TIMEOUT_S;"
    "assert s.SliderSolver.MANUAL_VOUCHER_WAIT_S == 300.0, s.SliderSolver.MANUAL_VOUCHER_WAIT_S;"
    "import slidex._trajectory as t;"
    "assert t.slide_end_hold_range() == (0.45, 1.10), t.slide_end_hold_range();"
    "import slidex.stealth;"
    "print('IMPORT_OK')"
)

_BAD_ENV_CASES = [
    {"SLIDEX_SOLVE_WATCHDOG": "abc", "SLIDEX_MANUAL_VOUCHER_WAIT": "inf"},
    {"SLIDEX_SOLVE_WATCHDOG": "", "SLIDEX_MANUAL_VOUCHER_WAIT": "-1"},
    {"SLIDEX_SOLVE_WATCHDOG": "nan", "SLIDEX_MANUAL_VOUCHER_WAIT": "1e9999"},
    {"SLIDEX_SOLVE_WATCHDOG": "1e9"},  # 高于上限 3600 → 回落 600（防事实关闭看门狗）
    {"SLIDEX_SLIDE_END_HOLD_MIN": "abc", "SLIDEX_SLIDE_END_HOLD_MAX": "1.2.3"},
    {"XY_BROWSER_COOKIE_WARMUP_TIMEOUT_MS": "abc"},
    {"XY_VERIFICATION_NOTIFY_DEDUP_SECONDS": "oops"},
]


@pytest.mark.parametrize("bad_env", _BAD_ENV_CASES)
def test_bad_env_never_breaks_import(bad_env):
    """坏 env 下 import 主要模块必须成功，且常量取默认值（子进程隔离，避免污染本进程）。"""
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        env={**os.environ, **bad_env},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"env={bad_env}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "IMPORT_OK" in proc.stdout
