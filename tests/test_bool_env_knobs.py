"""布尔开关收口到 ``_env.env_bool`` 后的行为回归（0.11.5）。

背景：0.11.4 把 ``_env.parse_bool/env_bool`` 立为唯一实现，但只迁移了
``config.py`` 的 4 个开关，库里还散着三种手搓词表：

* ``solver.py``：``XY_SLIDER_FINGERPRINT_AUDIT``（默认**开**）、
  ``SLIDEX_CDP_PRE_ACTIVATE`` / ``SLIDEX_CDP_PROBE_FROZEN`` /
  ``SLIDEX_CDP_REBUILD_FROZEN``（默认**关**）；后两个正是 xianyu compose 用
  ``${VAR:-}`` 传的旋钮，空串会被注入容器。
* ``stealth.py``：``DOCKER_ENV`` / ``XY_KEEP_VERIFICATION_SCREENSHOT``，以及
  ``XY_SLIDER_HEADLESS_WARMUP`` —— 后者写成 ``not in {...}``，空串判 True。
* ``service/app.py``：本地 ``_env_bool`` / ``_env_int``，与 ``_env`` 能力重复。

收口时**最危险的是语义漂移**：字段名是 ``disable_headless_warmup``（否定），
开关名是 ``XY_SLIDER_HEADLESS_WARMUP``（肯定），默认值写错就会静默把
"关掉 warmup" 翻成 "开 warmup"（用户会看到求解前标签被逐个切前台）。本文件
用「未设/空串取什么值」把每个开关的旧行为逐条钉死。
"""
from __future__ import annotations

import uuid

import pytest

from slidex._concurrency import concurrency_manager
from slidex.config import SlidexConfig
from slidex.stealth import XianyuSliderStealth


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


# ── stealth：字段名与开关名的极性相反，默认值必须逐字保持 ──

@pytest.mark.parametrize("raw", [None, "", "   ", "0", "false", "off"])
def test_headless_warmup_stays_disabled_by_default(stealth, monkeypatch, raw):
    """未设/空串/显式假 → disable=True（warmup 不启，0.11.4 之前的行为）。"""
    if raw is None:
        monkeypatch.delenv("XY_SLIDER_HEADLESS_WARMUP", raising=False)
    else:
        monkeypatch.setenv("XY_SLIDER_HEADLESS_WARMUP", raw)
    assert stealth().disable_headless_warmup is True


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
def test_headless_warmup_enabled_by_explicit_truthy(stealth, monkeypatch, raw):
    monkeypatch.setenv("XY_SLIDER_HEADLESS_WARMUP", raw)
    assert stealth().disable_headless_warmup is False


@pytest.mark.parametrize("raw", [None, ""])
def test_keep_verification_screenshot_off_by_default(stealth, monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("XY_KEEP_VERIFICATION_SCREENSHOT", raising=False)
    else:
        monkeypatch.setenv("XY_KEEP_VERIFICATION_SCREENSHOT", raw)
    assert stealth().keep_verification_screenshots is False


def test_keep_verification_screenshot_on(stealth, monkeypatch):
    monkeypatch.setenv("XY_KEEP_VERIFICATION_SCREENSHOT", "1")
    assert stealth().keep_verification_screenshots is True


# ── solver：CDP 三个开关默认关闭，且 compose 会把空串注入进来 ──

_CDP_SWITCHES = (
    "SLIDEX_CDP_PRE_ACTIVATE",
    "SLIDEX_CDP_PROBE_FROZEN",
    "SLIDEX_CDP_REBUILD_FROZEN",
)

# 变量名 → 读取方法名（三个都是 @staticmethod，无需实例化 SliderSolver）
_GETTERS = {
    "SLIDEX_CDP_PRE_ACTIVATE": "_cdp_pre_activate_enabled",
    "SLIDEX_CDP_PROBE_FROZEN": "_cdp_probe_frozen_enabled",
    "SLIDEX_CDP_REBUILD_FROZEN": "_cdp_rebuild_frozen_enabled",
}


@pytest.mark.parametrize("var", _CDP_SWITCHES)
@pytest.mark.parametrize("raw", [None, "", "   ", "0", "no"])
def test_cdp_switches_default_off(monkeypatch, var, raw):
    from slidex.solver import SliderSolver

    if raw is None:
        monkeypatch.delenv(var, raising=False)
    else:
        monkeypatch.setenv(var, raw)
    assert getattr(SliderSolver, _GETTERS[var])() is False


@pytest.mark.parametrize("var", _CDP_SWITCHES)
def test_cdp_switches_on_when_explicit(monkeypatch, var):
    from slidex.solver import SliderSolver

    monkeypatch.setenv(var, "1")
    assert getattr(SliderSolver, _GETTERS[var])() is True


# ── service：删掉本地 _env_bool/_env_int 后，工厂默认值不得变化 ──

def test_service_factory_defaults(monkeypatch):
    from slidex._env import env_bool, env_int

    monkeypatch.delenv("SLIDEX_PX_HEADLESS", raising=False)
    monkeypatch.delenv("SLIDEX_PX_CONCURRENCY", raising=False)
    assert env_bool("SLIDEX_PX_HEADLESS", True) is True
    assert env_int("SLIDEX_PX_CONCURRENCY", 5) == 5

    # 旧 _env_int 用 int(str)：容忍不了 "5.0"；_env.env_int 容忍（容错更宽，不算行为回退）
    monkeypatch.setenv("SLIDEX_PX_CONCURRENCY", "5.0")
    assert env_int("SLIDEX_PX_CONCURRENCY", 5) == 5
    monkeypatch.setenv("SLIDEX_PX_CONCURRENCY", "0")
    assert env_int("SLIDEX_PX_CONCURRENCY", 5, minimum=1) == 5
