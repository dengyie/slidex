"""XY_VERIFICATION_WAIT_TIMEOUT 的 clamp 语义回归（0.6.42 收口到 _env 时不得漂移）。

原实现：``max(5, int(text)) if text else 450`` —— 设了值就钳到 ≥5（``"3"`` 也给
5s），只有未设/非法才回落 450。收口时若误用 ``env_int(minimum=5)``，``"3"`` 会
被回落成 450 而不是钳到 5 —— 语义被静默改变。本文件把这个差别锁死。
"""
from __future__ import annotations

import uuid

import pytest

from slidex._concurrency import concurrency_manager
from slidex.config import SlidexConfig
from slidex.stealth import XianyuSliderStealth

ENV_KEY = "XY_VERIFICATION_WAIT_TIMEOUT"


@pytest.fixture
def stealth():
    built = []

    def _build():
        s = XianyuSliderStealth(
            user_id=f"u{uuid.uuid4().hex[:8]}",
            slidex_config=SlidexConfig(),
        )
        built.append(s)
        return s

    yield _build

    for s in built:
        try:
            concurrency_manager.unregister_instance(s.user_id, s)
        except Exception:
            pass


def test_unset_falls_back_to_450(stealth, monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    assert stealth().verification_wait_timeout == 450


def test_empty_falls_back_to_450(stealth, monkeypatch):
    monkeypatch.setenv(ENV_KEY, "")
    assert stealth().verification_wait_timeout == 450


def test_bad_value_falls_back_to_450(stealth, monkeypatch):
    monkeypatch.setenv(ENV_KEY, "abc")
    assert stealth().verification_wait_timeout == 450


def test_valid_value_used(stealth, monkeypatch):
    monkeypatch.setenv(ENV_KEY, "120")
    assert stealth().verification_wait_timeout == 120


def test_below_five_clamped_to_five_not_fallback(stealth, monkeypatch):
    # 关键回归：clamp 语义。"3" 必须钳到 5，而不是回落 450。
    monkeypatch.setenv(ENV_KEY, "3")
    assert stealth().verification_wait_timeout == 5


def test_negative_clamped_to_five_not_fallback(stealth, monkeypatch):
    # 原实现 int("-10")=-10 → max(5,-10)=5；收口后必须保持。
    monkeypatch.setenv(ENV_KEY, "-10")
    assert stealth().verification_wait_timeout == 5