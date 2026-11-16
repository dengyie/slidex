"""Aliyun 风格滑块 JSON：success 与 code 必须同时成立（0.6.12 语义修正）。

生产实证（punish newslidevalidate）：`{"code":300,"dt":"success","ec":200,
"result":{"code":300,"sig":"from bx"},"success":true}` 中 success:true 只是
baxia 网关"已受理"；scratch-captcha 前端判的是 `c.code===e.success`（枚举
success=0，300=other-punish → verifyFail + 3s 后 verifyRefresh 重试）。只有
code=0 才走 verifySuccess → checkCookie → bx-x5sec 头 → x5sec 落地。
"""

from __future__ import annotations

from typing import Any, Optional


_SUCCESS_TRUE = {True, 1, "1", "true", "success", "ok"}
_SUCCESS_FALSE = {False, 0, "0", "false", "fail"}

# baxia 惩罚码：300 = other-punish（设备/会话被判罚，非轨迹质量问题）。
# 换手势重拖不改变判决，只会给风控追加确认样本——必须停止自动拖动转被动恢复。
PUNISH_SLIDE_CODES = frozenset({300})

# R7 上游拒绝回执专用码：verifyCode（F001 行为风控 / F015 位置错）显式拒绝。
# 与 -1（packet miss，同录制重放）区分——风控拒绝后同会话重拖无意义，
# GestureSession 据此换家族/换录制而不是原路重放。
UPSTREAM_REJECT_CODE = -2


def extract_verify_code(data: Any) -> Optional[str]:
    """从校验包信封取 verifyCode（任意大小写键名），无则 None。"""
    if not isinstance(data, dict):
        return None
    for key in ("verifyCode", "VerifyCode"):
        if data.get(key) is not None:
            return str(data[key])
    return None


def is_punish_slide_code(code: Any) -> bool:
    """判决 code 是否为风控惩罚（other-punish）。"""
    try:
        return int(code) in PUNISH_SLIDE_CODES
    except (TypeError, ValueError):
        return False


def interpret_slide_json(data: Any, success_code: int = 0) -> Optional[bool]:
    """把滑块校验 JSON 判成 True/False；非 dict 返回 None（不是结果包）。

    判定：``success`` 为真 **且** ``code == success_code``（默认 0）才算成功。
    任一不成立即失败。``success:true code:300`` 是网关受理码，前端按
    other-punish 处理（0.6.11 前被误判为成功，导致 27 个周期假通过）。

    R7（2026-11-16 z.ai 实证）：`{success:true, verifyResult:false,
    verifyCode:"F001"}` 是「API 受理成功、行为验证被拒」的信封——页面未
    放行。verifyResult/verifyCode（任意大小写）存在时优先于裸 success
    判定；裸 success:true（无 code 无 verifyResult）保持旧兼容语义。
    verifyCode 归因用 ``extract_verify_code``（调用方在自己实例上记），
    本函数保持纯函数语义（并发求解不得共享全局状态）。
    """
    if not isinstance(data, dict):
        return None
    # R7：信封内的行为验证结果优先（键名大小写漂移都认）
    verify_result = None
    for key in ("verifyResult", "VerifyResult"):
        if key in data:
            vr: Any = data[key]
            if isinstance(vr, str):
                vr = vr.strip().lower()
            verify_result = vr in _SUCCESS_TRUE
            break
    if verify_result is False:
        # 上游明确拒绝：行为风控（F001 类）或位置错（F015 类）
        return False
    if "success" in data:
        flag: Any = data.get("success")
        if isinstance(flag, str):
            flag = flag.strip().lower()
        if flag in _SUCCESS_FALSE:
            return False
        if flag not in _SUCCESS_TRUE:
            return False
    code = data.get("code")
    if code is None:
        if verify_result is True:
            return True
        # 无 code 的裸 success:true —— 兼容旧式纯 success 布尔响应
        return "success" in data
    return code == success_code
