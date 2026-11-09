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


def test_outbound_ip_probe_url_disable_switch(monkeypatch):
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


# ── env 容错（_parse_int_env：非法值回落默认并告警，门控不失效） ──────────


def test_preflight_malformed_window_env_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XY_SLIDER_RISK_WINDOW", "abc")
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    # 10 分钟前的硬拒绝：默认 1800s 窗口内 → 仍延迟（门控未因 env 非法而失效）
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time() - 600,
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    allowed, _ = s._evaluate_slider_preflight_risk()
    assert not allowed


def test_preflight_malformed_threshold_env_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XY_SLIDER_RISK_FAILURE_THRESHOLD", "oops")
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    s.success_history_file = str(tmp_path / "s_missing.json")
    now = time.time()
    _write_failure_history(
        tmp_path / "f.json",
        [{"timestamp": now, "trigger_scene": "token_refresh", "verification_feedback": {}} for _ in range(6)],
    )
    allowed, reason = s._evaluate_slider_preflight_risk()
    assert not allowed
    assert "无成功记录" in reason


def test_preflight_float_format_env_tolerated(tmp_path, monkeypatch):
    """与 _env 宽容规则对齐："1800.0"/"300.0" 浮点写法按值生效，不回落默认。"""
    monkeypatch.setenv("XY_SLIDER_RISK_WINDOW", "300.0")
    s = _build()
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = str(tmp_path / "f.json")
    _write_failure_history(
        tmp_path / "f.json",
        [{
            "timestamp": time.time() - 600,
            "trigger_scene": "token_refresh",
            "verification_feedback": {"fail_code": "nc_block"},
        }],
    )
    # 10 分钟前的硬拒绝：300s 窗口外 → 放行（若回落默认 1800 则会延迟）
    assert s._evaluate_slider_preflight_risk() == (True, "")

    monkeypatch.setenv("XY_SLIDER_RISK_WINDOW", "1800.0")
    allowed, _ = s._evaluate_slider_preflight_risk()
    assert not allowed


# ── 人工接管分支（run() manual_headful 路径） ─────────────────────────────


class _FakeManualMouse:
    def move(self, *args, **kwargs):
        pass


class _FakeManualPage:
    """run() 人工分支所需的最小页面桩。"""

    def __init__(self):
        self.url = "https://passport.goofish.com/punish?action=captcha"
        self.mouse = _FakeManualMouse()

    def goto(self, url, **kwargs):
        return None

    def title(self):
        return "验证码拦截"

    def content(self):
        return "亲，请按住滑块，拖动到最右边"

    def wait_for_load_state(self, state=None, timeout=None):
        return None

    def is_closed(self):
        return False


def _setup_manual_run(s, page):
    """把实例摆到 run() 能走进 manual_headful 分支的状态。"""
    s.auto_slider_enabled = False
    s.headless = False
    s.risk_trigger_scene = "token_refresh"
    s.failure_history_file = "/nonexistent/failure-history.json"
    s.page = page
    s.context = mock.Mock()
    s.context.pages = []


def test_run_manual_branch_success_snapshots_cookies(tmp_path):
    s = _build()
    page = _FakeManualPage()
    _setup_manual_run(s, page)
    button = object()
    snapshot = {"_m_h5_tk": "abc", "unb": "123"}
    cookies_after = {"_m_h5_tk": "abc", "unb": "123", "cookie2": "x"}

    with mock.patch.object(s, "_check_date_validity", return_value=True), \
         mock.patch.object(s, "init_browser", lambda: None), \
         mock.patch.object(s, "_warmup_slider_context", lambda url: None), \
         mock.patch.object(s, "_is_hard_block_page", return_value=False), \
         mock.patch.object(s, "_simulate_human_page_behavior", lambda: None), \
         mock.patch.object(s, "find_slider_elements", return_value=(None, button, None)), \
         mock.patch.object(s, "check_verification_success_fast", return_value=True), \
         mock.patch.object(s, "_snapshot_context_cookies", return_value=snapshot), \
         mock.patch.object(s, "_select_monitor_page", return_value=page), \
         mock.patch.object(s, "_detect_qr_code_verification", return_value=(False, None)), \
         mock.patch.object(s, "_get_cookies_after_success", return_value=cookies_after) as get_cookies:
        ok, cookies = s.run("https://example.com/slider")

    assert ok is True
    assert cookies == cookies_after
    # 成功瞬间已写过快照；正常收口走 _get_cookies_after_success，快照未被消费
    assert s._manual_success_cookies == snapshot
    get_cookies.assert_called_once()


def test_run_manual_branch_timeout_clears_snapshot(tmp_path):
    s = _build()
    page = _FakeManualPage()
    _setup_manual_run(s, page)
    s.manual_slider_timeout = 1.0  # 缩短窗口，测试 ~1s
    button = object()

    with mock.patch.object(s, "_check_date_validity", return_value=True), \
         mock.patch.object(s, "init_browser", lambda: None), \
         mock.patch.object(s, "_warmup_slider_context", lambda url: None), \
         mock.patch.object(s, "_is_hard_block_page", return_value=False), \
         mock.patch.object(s, "_simulate_human_page_behavior", lambda: None), \
         mock.patch.object(s, "find_slider_elements", return_value=(None, button, None)), \
         mock.patch.object(s, "check_verification_success_fast", return_value=False), \
         mock.patch.object(s, "_select_monitor_page", return_value=page), \
         mock.patch.object(s, "_detect_qr_code_verification", return_value=(False, None)), \
         mock.patch.object(s, "_save_debug_snapshot", return_value=None):
        ok, cookies = s.run("https://example.com/slider")

    assert ok is False
    assert cookies is None
    assert s._manual_success_cookies is None


def test_run_manual_branch_no_slider_returns_fast_without_waiting(tmp_path):
    """无滑块页面不得进入 180s 无意义等待（上游 #111 的反挂死属性）。"""
    s = _build()
    page = _FakeManualPage()
    _setup_manual_run(s, page)
    s.manual_slider_timeout = 1.0

    import time as _time
    started = _time.time()
    with mock.patch.object(s, "_check_date_validity", return_value=True), \
         mock.patch.object(s, "init_browser", lambda: None), \
         mock.patch.object(s, "_warmup_slider_context", lambda url: None), \
         mock.patch.object(s, "_is_hard_block_page", return_value=False), \
         mock.patch.object(s, "_simulate_human_page_behavior", lambda: None), \
         mock.patch.object(s, "find_slider_elements", return_value=(None, None, None)), \
         mock.patch.object(s, "_select_monitor_page", return_value=page), \
         mock.patch.object(s, "_detect_qr_code_verification", return_value=(False, None)):
        ok, cookies = s.run("https://example.com/slider")
    elapsed = _time.time() - started

    assert ok is False
    assert cookies is None
    assert elapsed < 5, f"无滑块页应立即返回，实际耗时 {elapsed:.1f}s"


def test_run_manual_branch_no_slider_qr_redirect_returns_verification_result(tmp_path):
    s = _build()
    page = _FakeManualPage()
    _setup_manual_run(s, page)
    qr_result = {"cookie2": "from-qr-flow"}

    with mock.patch.object(s, "_check_date_validity", return_value=True), \
         mock.patch.object(s, "init_browser", lambda: None), \
         mock.patch.object(s, "_warmup_slider_context", lambda url: None), \
         mock.patch.object(s, "_is_hard_block_page", return_value=False), \
         mock.patch.object(s, "_simulate_human_page_behavior", lambda: None), \
         mock.patch.object(s, "find_slider_elements", return_value=(None, None, None)), \
         mock.patch.object(s, "_select_monitor_page", return_value=page), \
         mock.patch.object(s, "_detect_qr_code_verification", return_value=(True, object())), \
         mock.patch.object(
             s, "_process_verification_requirement", return_value=qr_result
         ) as process_qr:
        ok, cookies = s.run("https://example.com/slider")

    assert ok is True
    assert cookies == qr_result
    process_qr.assert_called_once()


# ── _get_cookies_after_success 人工快照兜底（上游 #111 关页场景） ─────────


def test_get_cookies_after_success_falls_back_to_manual_snapshot():
    s = _build()

    class _ClosedPage:
        @property
        def url(self):
            raise RuntimeError("Target page, context or browser has been closed")

    s.page = _ClosedPage()
    s._manual_success_cookies = {"_m_h5_tk": "snap", "unb": "42"}

    first = s._get_cookies_after_success()
    assert first == {"_m_h5_tk": "snap", "unb": "42"}
    # 一次性消费：第二次不得再拿到旧快照
    assert s._get_cookies_after_success() is None
    assert s._manual_success_cookies is None
