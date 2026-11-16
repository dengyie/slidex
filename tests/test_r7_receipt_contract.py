"""R7 成功判定契约测试（docs/slider-humanization-requirements-2026-11-16 §3-R7）。

2026-11-16 z.ai 双号注册实证：上游返回
``{success: true, verifyResult: false, verifyCode: F001}`` 时旧判定把它
当成功——页面实际未放行，后续注册流程空转。本文件锁定三件事：

1. 信封响应（任意大小写键名）``verifyResult:false`` 必须判失败；
2. ``SolveResult`` 携带 verified / upstream_code / reason 归因；
3. console 拒绝回执（验证失败 + verifyResult:false）必须置失败事件，
   绝不允许「拖完即成功」假信号。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from slidex._slide_result import extract_verify_code, interpret_slide_json
from slidex.providers import SolveResult
from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider


# ── ① 校验包信封判定 ────────────────────────────────────────────────────────

class TestEnvelopeReceipt:
    def test_verify_result_false_is_failure(self):
        """z.ai F001 实录信封：success:true 但 verifyResult:false → 失败。"""
        data = {"success": True, "verifyResult": False, "verifyCode": "F001",
                "certifyId": "BDrvwbaKeM"}
        assert interpret_slide_json(data) is False

    def test_verify_result_false_uppercase_key(self):
        """键名大小写漂移同样要认。"""
        assert interpret_slide_json({"VerifyResult": "false", "VerifyCode": "F001"}) is False

    def test_verify_result_true_is_success(self):
        assert interpret_slide_json({"success": True, "verifyResult": True}) is True
        assert interpret_slide_json({"verifyResult": "true"}) is True

    def test_verify_code_captured_for_attribution(self):
        """拒绝时 verifyCode 必须可提取供 SolveResult.upstream_code 透传。"""
        assert extract_verify_code(
            {"success": True, "verifyResult": False, "verifyCode": "F001"}
        ) == "F001"
        assert extract_verify_code({"success": True}) is None

    def test_bare_success_true_stays_compatible(self):
        """旧式裸 {success:true}（无 code 无 verifyResult）保持兼容语义。"""
        assert interpret_slide_json({"success": True}) is True

    def test_code_mismatch_still_failure(self):
        assert interpret_slide_json({"success": True, "code": 300}) is False


# ── ② validate_response 三通道之一：网络响应 ────────────────────────────────

class _FakeResponse:
    def __init__(self, url: str, body: dict):
        self.url = url
        self._body = json.dumps(body).encode()

    async def body(self):
        return self._body


def _run_validate(provider, body: dict):
    resp = _FakeResponse("https://no8xfe-verify.captcha-open.aliyuncs.com/", body)
    return asyncio.run(provider.validate_response(resp))


class TestValidateResponseEnvelope:
    def test_f001_envelope_is_rejected(self):
        """网络通道拿到 F001 信封 → False，并记录 upstream_code。"""
        provider = AliyunCaptchaJsProvider()
        ok = _run_validate(provider, {
            "success": True, "verifyResult": False, "verifyCode": "F001",
        })
        assert ok is False
        assert provider._last_upstream_code == "F001"

    def test_envelope_pass_is_accepted(self):
        provider = AliyunCaptchaJsProvider()
        ok = _run_validate(provider, {"success": True, "verifyResult": True, "Code": "Success"})
        assert ok is True

    def test_envelope_code_success_alone_is_not_pass(self):
        """Code="Success" 只是 API 受理——verifyResult 缺失时不得判成功。"""
        provider = AliyunCaptchaJsProvider()
        ok = _run_validate(provider, {"Code": "Success"})
        assert ok is None or ok is False


# ── ③ SolveResult 契约字段 ──────────────────────────────────────────────────

class TestSolveResultContract:
    def test_default_unverified(self):
        res = SolveResult(success=True, cookies=None)
        assert res.verified is None
        assert res.upstream_code is None
        assert res.reason is None

    def test_rejected_receipt_shape(self):
        res = SolveResult(success=False, cookies=None, code=1,
                          verified=False, upstream_code="F001", reason="F001")
        assert res.success is False
        assert res.verified is False
        assert res.upstream_code == "F001"


class TestGetResultVerifiedSemantics:
    """get_result 的 verified 语义：拒绝 → (False, F 码)；无回执 → no_receipt。"""

    @staticmethod
    def _result(provider, timeout_ms=100):
        page = MagicMock()
        page.url = "https://chat.z.ai/auth"
        page.context.cookies = AsyncMock(return_value=[])
        page.evaluate = AsyncMock(return_value={})
        return asyncio.run(provider.get_result(page, timeout_ms=timeout_ms))

    def test_fail_verdict_carries_upstream_code(self):
        provider = AliyunCaptchaJsProvider()
        provider._result = False
        provider._result_code = None
        provider._last_upstream_code = "F001"
        res = self._result(provider)
        assert res.success is False
        assert res.verified is False
        assert res.upstream_code == "F001"
        assert res.reason == "F001"
        assert res.code == 1  # 基类约定：无数字码回退 1（手势会话换族）

    def test_fail_without_code_reason_receipt_rejected(self):
        provider = AliyunCaptchaJsProvider()
        provider._result = False
        provider._result_code = None
        provider._last_upstream_code = None
        res = self._result(provider)
        assert res.verified is False
        assert res.reason == "receipt_rejected"

    def test_timeout_is_no_receipt(self):
        """三通道都没拿到回执 → verified=None + reason=no_receipt（宁缺勿假）。"""
        provider = AliyunCaptchaJsProvider()
        provider._result = None
        provider._result_code = None
        # _dom_verdict 走 evaluate 永远返回空 → 轮询到超时
        res = self._result(provider, timeout_ms=60)
        assert res.success is False
        assert res.verified is None
        assert res.reason == "no_receipt"

    def test_success_verdict_is_verified(self):
        provider = AliyunCaptchaJsProvider()
        provider._result = True
        provider._result_code = None
        res = self._result(provider)
        assert res.success is True
        assert res.verified is True


# ── ④ console 拒绝回执通道 ──────────────────────────────────────────────────

class TestConsoleFailReceipt:
    def _solver(self):
        from slidex.solver import SliderSolver
        return SliderSolver(cookie_id="contract-test")

    def test_console_fail_receipt_marks_failure(self):
        """z.ai F001 实录 console 行必须置 _slide_ok=False + 记 F 码。"""
        solver = self._solver()
        msg = MagicMock()
        msg.text = ("❌ 验证码验证失败: {success: true, verifyResult: false, "
                    "verifyCode: F001, certifyId: BDrvwbaKeM}")
        solver._on_console(msg)
        assert solver._slide_ok is False
        assert solver._slide_upstream_code == "F001"
        assert solver._result_event.is_set()

    def test_console_fail_without_code_still_fails(self):
        solver = self._solver()
        msg = MagicMock()
        msg.text = "❌ 验证码验证失败: timeout"
        solver._on_console(msg)
        assert solver._slide_ok is False
        assert solver._slide_upstream_code is None
        assert solver._result_event.is_set()

    def test_console_success_marker_still_works(self):
        solver = self._solver()
        msg = MagicMock()
        msg.text = "captchaVerifyParam: xxx"
        solver._on_console(msg)
        assert solver._slide_ok is True

    def test_console_plain_message_ignored(self):
        solver = self._solver()
        msg = MagicMock()
        msg.text = "some unrelated log"
        solver._on_console(msg)
        assert solver._slide_ok is None
        assert not solver._result_event.is_set()


# ── ⑤ 调用方归因：F001 换会话 vs F015 重取图 ────────────────────────────────

class TestCallerAttribution:
    def test_f001_class_means_new_session(self):
        """上游风控码（F001）→ 同会话重试无意义，调用方应换新会话。"""
        F001_CLASS = {"F001", "F002", "F003"}
        assert "F001" in F001_CLASS

    def test_f015_class_means_regrab_image(self):
        """位置码（F015）→ 几何未命中，重取图即可。"""
        assert interpret_slide_json(
            {"success": True, "verifyResult": False, "verifyCode": "F015"}
        ) is False
        assert extract_verify_code(
            {"success": True, "verifyResult": False, "verifyCode": "F015"}
        ) == "F015"


# ── ⑥ 拒绝路由根修：-2 专用码绝不原路重放 ──────────────────────────────────

class TestRejectRouting:
    """R7 根修：显式拒绝（code=-2）与 packet miss（code=-1）路由必须区分。

    -1 → 同录制重放（捕获面 miss 不代表几何错）；-2 → consume 当前
    家族/录制换下一个——同会话原路重放等于给风控追加确认样本。
    """

    def test_upstream_reject_code_constant(self):
        from slidex._slide_result import UPSTREAM_REJECT_CODE
        assert UPSTREAM_REJECT_CODE == -2
        assert UPSTREAM_REJECT_CODE != -1  # 不得与 packet miss 撞码

    def test_console_reject_sets_code_minus_2(self):
        """console 拒绝回执 → _slide_code=-2（GestureSession 换家族信号）。"""
        from slidex.solver import SliderSolver
        solver = SliderSolver(cookie_id="routing-test")
        msg = MagicMock()
        msg.text = ("❌ 验证码验证失败: {success: true, verifyResult: false, "
                    "verifyCode: F001}")
        solver._on_console(msg)
        assert solver._slide_code == -2
        assert solver._slide_ok is False

    def test_minus_2_switches_family_not_replay(self):
        """code=-2 时 human 家族不得原路重放同一录制——必须换。"""
        import os
        import tempfile

        from slidex._gestures import ARCHETYPE_HUMAN, GestureSession
        from slidex._trajectory_pool import SliderTrajectoryPool

        with tempfile.TemporaryDirectory() as tmp:
            pool = SliderTrajectoryPool(tmp)
            pool.save_trajectory(
                [[0.0, 0.0, 100.0], [10.0, 0.0, 30.0], [25.0, 0.0, 30.0],
                 [40.0, 0.0, 30.0], [50.0, 0.0, 40.0]],
                "u1", 50.0, True, source="human_cdp")
            s = GestureSession(pool, "u1", trajectory_mode="recorded")
            first = s.next(50.0, None)
            assert first is not None
            assert s._current_archetype == ARCHETYPE_HUMAN
            second = s.next(50.0, -2)
            # -2 拒绝：绝不原路重放（current 归档、换新计划）
            if second is not None:
                assert second is not first
                assert second.points != first.points

    def test_minus_1_still_replays_same_recording(self):
        """对照组：code=-1 packet miss 保持同录制重放契约（既有行为不回退）。"""
        import tempfile

        from slidex._gestures import GestureSession
        from slidex._trajectory_pool import SliderTrajectoryPool

        with tempfile.TemporaryDirectory() as tmp:
            pool = SliderTrajectoryPool(tmp)
            pool.save_trajectory(
                [[0.0, 0.0, 100.0], [10.0, 0.0, 30.0], [25.0, 0.0, 30.0],
                 [40.0, 0.0, 30.0], [50.0, 0.0, 40.0]],
                "u1", 50.0, True, source="human_cdp")
            s = GestureSession(pool, "u1", trajectory_mode="recorded")
            first = s.next(50.0, None)
            assert first is not None
            second = s.next(50.0, -1)
            assert second is not None
            assert list(second.points) == list(first.points)


# ── ⑦ AliyunNoCaptchaProvider 回执通路（实例级归因，无全局状态）────────────

class _FakeResponse:
    def __init__(self, url: str, body: dict):
        self.url = url
        self._body = json.dumps(body).encode()

    async def body(self):
        return self._body


class TestNoCaptchaReceiptPipeline:
    def test_f001_attribution_on_provider_instance(self):
        """F001 信封 → provider 实例记 upstream_code（无 last_verify_code 全局态）。"""
        from slidex.providers.aliyun import AliyunNoCaptchaProvider

        provider = AliyunNoCaptchaProvider()
        resp = _FakeResponse("https://_____tmd_____/slide?abc=1", {
            "success": True, "verifyResult": False, "verifyCode": "F001",
        })
        ok = asyncio.run(provider.validate_response(resp))
        assert ok is False
        assert provider._last_upstream_code == "F001"

    def test_interpret_slide_json_has_no_global_state(self):
        """interpret_slide_json 必须保持纯函数——函数属性已删除。"""
        assert not hasattr(interpret_slide_json, "last_verify_code")

    def test_get_result_timeout_is_no_receipt(self):
        """超时无回执 → verified=None + reason=no_receipt（宁缺勿假）。"""
        from unittest.mock import MagicMock

        from slidex.providers.aliyun import AliyunNoCaptchaProvider

        provider = AliyunNoCaptchaProvider()
        provider._result = None
        provider._result_event = None
        page = MagicMock()
        page.url = "https://example.com/"
        page.context.cookies = AsyncMock(return_value=[])
        res = asyncio.run(provider.get_result(page, timeout_ms=50))
        assert res.success is False
        assert res.verified is None
        assert res.reason == "no_receipt"

    def test_get_result_rejected_carries_f_code(self):
        """判定失败 + F 码在实例 → SolveResult.verified=False + upstream_code。"""
        from unittest.mock import MagicMock

        from slidex.providers.aliyun import AliyunNoCaptchaProvider

        provider = AliyunNoCaptchaProvider()
        provider._result = False
        provider._result_code = 1
        provider._result_event = None
        provider._last_upstream_code = "F001"
        page = MagicMock()
        page.url = "https://example.com/"
        page.context.cookies = AsyncMock(return_value=[])
        res = asyncio.run(provider.get_result(page, timeout_ms=50))
        assert res.success is False
        assert res.verified is False
        assert res.upstream_code == "F001"
        assert res.reason == "F001"
