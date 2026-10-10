"""0.6.33: 300 other-punish 判定与单发停止。

风控惩罚码(300)与轨迹质量无关: 换手势重拖不改变判决, 只会追加风控确认样本
并加深惩罚。solver 三个 solve 入口都必须"一次判决即停", 绝不再等
verifyRefresh 重建的挑战、也不再让 legacy 循环接手。
"""
from __future__ import annotations

import asyncio

import pytest

import slidex.solver as solver_mod
from slidex._slide_result import PUNISH_SLIDE_CODES, is_punish_slide_code
from slidex.solver import SliderSolver


class TestIsPunishSlideCode:
    def test_300_is_punish(self):
        assert is_punish_slide_code(300) is True
        assert is_punish_slide_code("300") is True
        assert is_punish_slide_code(300.0) is True
        assert 300 in PUNISH_SLIDE_CODES

    @pytest.mark.parametrize("code", [0, 1, -1, None, "0", "abc", [], {}, 301, -300])
    def test_non_punish_codes(self, code):
        assert is_punish_slide_code(code) is False


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(*_a, **_k):
        return None

    # 非惩罚码路径有 2~4s 退避; 测试里跳过, 只验证控制流。
    monkeypatch.setattr(solver_mod.asyncio, "sleep", _instant)


def _legacy_solver(monkeypatch, codes):
    """构造只跑 legacy 循环的 solver; codes 是 _wait_slide_outcome 依次返回的判决码。"""
    s = SliderSolver(cookie_id="punish_test")
    s.page = object()
    s._use_provider_mode = False
    s._bx_voucher = None
    s.trajectory_mode = "generated"
    seq = list(codes)
    seen = {"drags": [], "fallback": [], "telemetry": []}

    async def _wait_slider(timeout=15.0):
        return True

    async def _install_net_tap(page):
        return None

    async def _calc_distance_multi_source():
        return 258.0

    async def _do_slide(distance, attempt):
        seen["drags"].append(attempt)

    async def _wait_slide_outcome(timeout, success_code):
        code = seq.pop(0)
        return (code == 0), code

    async def _fallback_or_fail(verify_url):
        seen["fallback"].append(verify_url)
        return False, None

    monkeypatch.setattr(s, "_wait_slider", _wait_slider)
    monkeypatch.setattr(s, "_install_net_tap", _install_net_tap)
    monkeypatch.setattr(s, "_calc_distance_multi_source", _calc_distance_multi_source)
    monkeypatch.setattr(s, "_do_slide", _do_slide)
    monkeypatch.setattr(s, "_wait_slide_outcome", _wait_slide_outcome)
    monkeypatch.setattr(s, "_fallback_or_fail", _fallback_or_fail)
    monkeypatch.setattr(s, "_emit_step", lambda *a, **k: None)
    monkeypatch.setattr(
        s, "_emit_telemetry_event", lambda name, **k: seen["telemetry"].append((name, k))
    )
    return s, seen


def test_legacy_300_stops_after_single_drag(monkeypatch):
    s, seen = _legacy_solver(monkeypatch, [300, 300, 300])
    ok, cookies = asyncio.run(s._run_legacy_solve_loop("http://verify"))
    assert ok is False and cookies is None
    assert seen["drags"] == [1], "300 后只应拖一次, 不再重试"
    assert s.last_slide_code == 300
    assert len(seen["fallback"]) == 1
    assert any(name == "slide_punish_stop" for name, _ in seen["telemetry"])


def test_legacy_non_punish_keeps_retrying(monkeypatch):
    s, seen = _legacy_solver(monkeypatch, [1, 1, 1])
    asyncio.run(s._run_legacy_solve_loop("http://verify"))
    assert seen["drags"] == [1, 2, 3], "非惩罚码应走满重试"
    assert not any(name == "slide_punish_stop" for name, _ in seen["telemetry"])


def test_provider_punish_skips_legacy_loop(monkeypatch):
    s = SliderSolver(cookie_id="punish_test")
    s.page = object()
    s._use_provider_mode = True
    s._bx_voucher = None
    s._provider = type("P", (), {"name": "aliyun-nocaptcha"})()
    called = {"legacy": 0, "fallback": 0}

    async def _detect(page):
        return True

    async def _solve_with_provider(page):
        s.last_slide_code = 300
        return False, None

    async def _legacy(url):
        called["legacy"] += 1
        return False, None

    async def _fallback(url):
        called["fallback"] += 1
        return False, None

    monkeypatch.setattr(s, "_detect_and_init_provider", _detect)
    monkeypatch.setattr(s, "_solve_with_provider", _solve_with_provider)
    monkeypatch.setattr(s, "_run_legacy_solve_loop", _legacy)
    monkeypatch.setattr(s, "_fallback_or_fail", _fallback)
    monkeypatch.setattr(s, "_emit_step", lambda *a, **k: None)
    monkeypatch.setattr(s, "_emit_telemetry_event", lambda *a, **k: None)

    asyncio.run(s._run_solve_loop("http://verify"))
    assert called["legacy"] == 0, "provider 判罚后不得再进 legacy 循环"
    assert called["fallback"] == 1


class _FakeBtn:
    async def bounding_box(self):
        return {"width": 40.0}


class _FakeTrack:
    async def bounding_box(self):
        return {"width": 298.0}


class _FakeElements:
    metadata = {"slider_type": "scale"}
    track_width_px = 298.0
    slider_btn = _FakeBtn()
    slider_track = _FakeTrack()


class _FakeResult:
    code = 300
    success = False
    cookies = {}
    error = "other-punish"


class _FakeProvider:
    name = "aliyun-nocaptcha"

    async def locate_elements(self, page):
        return _FakeElements()

    async def get_result(self, page, timeout_ms=5000):
        return _FakeResult()

    async def cleanup_after_result(self, page):
        return None


class _FakeAudit:
    def uninstall(self):
        return None


def test_provider_loop_300_stops_after_single_drag(monkeypatch):
    s = SliderSolver(cookie_id="punish_test")
    s._provider = _FakeProvider()
    s.trajectory_mode = "generated"
    s.page = object()
    seen = {"drags": 0, "telemetry": []}

    async def _noop(*_a, **_k):
        return None

    async def _perform(page, elements, travel, points, plan):
        seen["drags"] += 1

    monkeypatch.setattr(s, "_install_url_audit", lambda page: _FakeAudit())
    monkeypatch.setattr(s, "_dump_net_tap", _noop)
    monkeypatch.setattr(s, "_install_net_tap", _noop)
    monkeypatch.setattr(s, "_call_perform_slide", _perform)
    monkeypatch.setattr(
        s, "_emit_telemetry_event", lambda name, **k: seen["telemetry"].append((name, k))
    )

    ok, cookies = asyncio.run(s._solve_with_provider(s.page))
    assert ok is False and cookies == {}
    assert seen["drags"] == 1, "provider 300 后不得重定位重拖"
    assert s.last_slide_code == 300
    assert any(name == "slide_punish_stop" for name, _ in seen["telemetry"])


def test_provider_non_punish_falls_back_to_legacy(monkeypatch):
    s = SliderSolver(cookie_id="punish_test")
    s.page = object()
    s._use_provider_mode = True
    s._bx_voucher = None
    s._provider = type("P", (), {"name": "aliyun-nocaptcha"})()
    called = {"legacy": 0}

    async def _detect(page):
        return True

    async def _solve_with_provider(page):
        s.last_slide_code = 1
        return False, None

    async def _legacy(url):
        called["legacy"] += 1
        return False, None

    monkeypatch.setattr(s, "_detect_and_init_provider", _detect)
    monkeypatch.setattr(s, "_solve_with_provider", _solve_with_provider)
    monkeypatch.setattr(s, "_run_legacy_solve_loop", _legacy)
    monkeypatch.setattr(s, "_emit_step", lambda *a, **k: None)
    monkeypatch.setattr(s, "_emit_telemetry_event", lambda *a, **k: None)

    asyncio.run(s._run_solve_loop("http://verify"))
    assert called["legacy"] == 1, "非惩罚失败仍应回退 legacy"
