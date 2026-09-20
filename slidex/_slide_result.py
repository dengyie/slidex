"""Aliyun 风格滑块 JSON：success 标志优先于 code。"""

from __future__ import annotations

from typing import Any, Optional


_SUCCESS_TRUE = {True, 1, "1", "true", "success", "ok"}
_SUCCESS_FALSE = {False, 0, "0", "false", "fail"}


def interpret_slide_json(data: Any, success_code: int = 0) -> Optional[bool]:
    """把滑块校验 JSON 判成 True/False；非 dict 返回 None（不是结果包）。

    ``success: false`` 即使 ``code == 0`` 也是失败。没有 success 字段时才回退
    到 ``code == success_code``（Aliyun 默认 0）。
    """
    if not isinstance(data, dict):
        return None
    if "success" in data:
        flag: Any = data.get("success")
        if isinstance(flag, str):
            flag = flag.strip().lower()
        if flag in _SUCCESS_FALSE:
            return False
        if flag in _SUCCESS_TRUE:
            return True
    code = data.get("code")
    if code is None:
        return False
    return code == success_code
