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


def interpret_slide_json(data: Any, success_code: int = 0) -> Optional[bool]:
    """把滑块校验 JSON 判成 True/False；非 dict 返回 None（不是结果包）。

    判定：``success`` 为真 **且** ``code == success_code``（默认 0）才算成功。
    任一不成立即失败。``success:true code:300`` 是网关受理码，前端按
    other-punish 处理（0.6.11 前被误判为成功，导致 27 个周期假通过）。
    """
    if not isinstance(data, dict):
        return None
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
        # 无 code 的裸 success:true —— 兼容旧式纯 success 布尔响应
        return "success" in data
    return code == success_code
