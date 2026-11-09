"""run() 验证前风险门控（上游 b838cd1 迁移自 xianyu 5-mixin 版本）单测。

覆盖三组能力：
- _evaluate_slider_preflight_risk：失败史冷却门控（硬拒绝一票延迟 / 阈值+无成功延迟）
- _check_browser_outbound_ip_consistency：Token 链路与浏览器出口 IP 一致性门控
- _is_runtime_detached_error：detached/disconnected 判定收敛到模块级纯函数
- __init__ 人工接管属性（XY_ENABLE_AUTO_SLIDER / XY_MANUAL_SLIDER_TIMEOUT）与 run() 接线顺序
"""

import json
import time
import uuid
from unittest import mock

import pytest

from slidex.config import SlidexConfig
from slidex.stealth import XianyuSliderStealth, _is_runtime_detached_error
from slidex._concurrency import concurrency_manager

_BUILT = []


def _build(user_id=None, **kwargs):
    s = XianyuSliderStealth(
        user_id=user_id or f"g{uuid.uuid4().hex[:8]}",
        slidex_config=SlidexConfig(),
        **kwargs,
    )
    _BUILT.append(s)
    return s


@pytest.fixture(autouse=True)
def _release_concurrency_slots():
    """__init__ 会占用并发管理器槽位；不释放会让后续实例排队 60s。"""
    _BUILT.clear()
    yield
    for s in _BUILT:
        try:
            concurrency_manager.unregister_instance(s.user_id, s)
        except Exception:
            pass
    _BUILT.clear()


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch):
    for key in (
        "XY_SLIDER_RISK_WINDOW",
        "XY_SLIDER_RISK_FAILURE_THRESHOLD",
        "XY_OUTBOUND_IP_PROBE_URL",
        "XY_ENABLE_AUTO_SLIDER",
        "XY_MANUAL_SLIDER_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)


# ── _is_runtime_detached_error ────────────────────────────────────────────


def test_runtime_detached_error_matches_detached_and_disconnected():
    assert _is_runtime_detached_error(RuntimeError("Page.frameDetached: Frame was detached"))
    assert _is_runtime_detached_error(RuntimeError("Browser has been disconnected"))
    assert not _is_runtime_detached_error(RuntimeError("net::ERR_CONNECTION_REFUSED"))
    assert not _is_runtime_detached_error(ValueError("plain error"))


# ── _evaluate_slider_preflight_risk ───────────────────────────────────────


def _write_failure_history(path, records):
    path.write_text(json.dumps(records), encoding="utf-8")


def test_preflight_allows_when_scene_not_token_refresh(tmp_path):
    s = _build()
    s.risk_trigger_scene = "password_login"
    s.failure_history_file = str(tmp_path / "f.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{"timestamp": time.time(), "verification_feedback": {"fail_code": "300"}}],
    )
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_allows_when_no_history_file(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "missing.json")
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_defers_on_recent_hard_reject(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time(),
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    allowed, reason = s._evaluate_slider_preflight_risk()
    assert not allowed
    assert "硬拒绝" in reason


def test_preflight_ignores_stale_records(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time() - 7200,  # 2h 前，超出默认 1800s 窗口
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_defers_on_threshold_without_recent_success(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    s.success_history_file = str(tmp_path / "s.json")
    now = time.time()
    _write_failure_history(
        tmp_path / "f.json",
        [{"timestamp": now, "trigger_scene": "token_refresh", "verification_feedback": {}} for _ in range(6)],
    )
    allowed, reason = s._evaluate_slider_preflight_risk()
    assert not allowed
    assert "无成功记录" in reason


def test_preflight_allows_on_threshold_with_recent_success(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    s.success_history_file = str(tmp_path / "s.json")
    now = time.time()
    _write_failure_history(
        tmp_path / "f.json",
        [{"timestamp": now, "trigger_scene": "token_refresh", "verification_feedback": {}} for _ in range(6)],
    )
    _write_failure_history(
        tmp_path / "s.json",
        [{"timestamp": now, "success": True}],
    )
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_allows_below_threshold(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    s.success_history_file = str(tmp_path / "s_missing.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{"timestamp": time.time(), "trigger_scene": "token_refresh", "verification_feedback": {}}],
    )
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_tolerates_corrupt_history(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    (tmp_path / "f.json").write_text("not-json", encoding="utf-8")
    assert s._evaluate_slider_preflight_risk() == (True, "")


def test_preflight_window_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XY_SLIDER_RISK_WINDOW", "300")
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    # 10 分钟前的硬拒绝：默认 1800s 窗口会延迟，300s 窗口放行
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time() - 600,
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    assert s._evaluate_slider_preflight_risk() == (True, "")


# ── _check_browser_outbound_ip_consistency ────────────────────────────────


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


class _FakeRequestContext:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self._ok = ok

    def get(self, url, timeout=None):
        return _FakeResponse(self._payload, ok=self._ok)


class _FakeContext:
    def __init__(self, payload, ok=True):
        self.request = _FakeRequestContext(payload, ok=ok)


def test_outbound_ip_allows_when_expected_not_set():
    s = _build()
    s.expected_outbound_ip = None
    s.context = _FakeContext({"ip": "1.2.3.4"})
    assert s._check_browser_outbound_ip_consistency() == (True, "")


def test_outbound_ip_defers_on_mismatch():
    s = _build()
    s.expected_outbound_ip = "5.6.7.8"
    s.context = _FakeContext({"ip": "1.2.3.4"})
    allowed, reason = s._check_browser_outbound_ip_consistency()
    assert not allowed
    assert "出口 IP 不一致" in reason


def test_outbound_ip_allows_on_match():
    s = _build()
    s.expected_outbound_ip = "1.2.3.4"
    s.context = _FakeContext({"ip": "1.2.3.4"})
    assert s._check_browser_outbound_ip_consistency() == (True, "")


def test_outbound_ip_allows_when_probe_fails():
    s = _build()
    s.expected_outbound_ip = "5.6.7.8"
    s.context = _FakeContext({"ip": "1.2.3.4"}, ok=False)
    assert s._check_browser_outbound_ip_consistency() == (True, "")


def test_outbound_ip_allows_when_context_missing():
    s = _build()
    s.expected_outbound_ip = "5.6.7.8"
    s.context = None
    assert s._check_browser_outbound_ip_consistency() == (True, "")


def test_outbound_ip_probe_url_disable_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("XY_OUTBOUND_IP_PROBE_URL", "")
    s = _build()
    s.expected_outbound_ip = "5.6.7.8"
    s.context = _FakeContext({"ip": "1.2.3.4"})
    assert s._check_browser_outbound_ip_consistency() == (True, "")


# ── __init__ 人工接管属性 ─────────────────────────────────────────────────


def test_auto_slider_defaults_on_preserves_headless():
    s = _build(headless=True)
    assert s.auto_slider_enabled is True
    assert s.headless is True
    assert s.manual_slider_timeout == 180
    assert s._manual_success_cookies is None
    assert s.expected_outbound_ip is None


def test_auto_slider_disabled_forces_headful(monkeypatch):
    monkeypatch.setenv("XY_ENABLE_AUTO_SLIDER", "0")
    s = _build(headless=True)
    assert s.auto_slider_enabled is False
    assert s.headless is False


def test_manual_slider_timeout_clamped(monkeypatch):
    monkeypatch.setenv("XY_MANUAL_SLIDER_TIMEOUT", "5")
    s = _build()
    assert s.manual_slider_timeout == 30
    monkeypatch.setenv("XY_MANUAL_SLIDER_TIMEOUT", "not-a-number")
    s2 = _build()
    assert s2.manual_slider_timeout == 180


# ── run() 接线顺序 ────────────────────────────────────────────────────────


def test_run_defers_before_browser_init_on_preflight_gate(tmp_path):
    """preflight 延迟时 run() 必须在 init_browser 之前收口，且写反馈。"""
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time(),
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    with mock.patch.object(s, "init_browser", side_effect=AssertionError("must not init browser")):
        ok, cookies = s.run("https://example.com/slider")
    assert ok is False
    assert cookies is None
    feedback = s.last_verification_feedback
    assert feedback["status"] == "preflight_deferred"
    assert feedback["source"] == "recent_risk_pressure"


def test_run_defers_on_outbound_ip_mismatch_after_browser_init(tmp_path):
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f_missing.json")
    s.expected_outbound_ip = "5.6.7.8"
    with mock.patch.object(s, "init_browser", lambda: setattr(s, "context", _FakeContext({"ip": "1.2.3.4"}))):
        with mock.patch.object(s, "_warmup_slider_context", lambda url: None):
            ok, cookies = s.run("https://example.com/slider")
    assert ok is False
    assert cookies is None
    feedback = s.last_verification_feedback
    assert feedback["status"] == "preflight_deferred"
    assert feedback["source"] == "outbound_ip_mismatch"
