"""服务层业务码。

与 HTTP 状态码分工明确：
- **HTTP 状态码**表达传输/鉴权/参数层结果（400/401/422/429/500）。
- **业务码 ``code``** 表达求解结果本身：0 = 成功；1xxx = 求解失败。
  求解失败（含 IP 被拦、超时）仍是 HTTP 200——客户端要读 body 判断，
  这跟 captcha.run 的 ``status: fail`` 语义一致，只是数字码更省解析。
"""

from __future__ import annotations

# 成功
OK = 0

# 鉴权 / 参数 / 限流（同时作为 HTTP 状态码使用）
UNAUTHORIZED = 401
BAD_REQUEST = 400
INVALID_REQUEST = 422
UNSUPPORTED_TYPE = 400
BUSY = 429

# 求解失败（HTTP 200 + code）
SOLVE_FAILED = 1001
SOLVE_TIMEOUT = 1002
SOLVE_BLOCKED = 1003
SOLVE_BUSY = 1004

# 服务内部错误（HTTP 500 + code）
INTERNAL = 500

# kind → 业务码，避免每个 handler 各写一套映射
_KIND_TO_CODE = {
    "solver_busy": SOLVE_BUSY,
    "px_token_timeout": SOLVE_TIMEOUT,
    "challenge_load_timeout": SOLVE_TIMEOUT,
    "px_token_blocked": SOLVE_BLOCKED,
    "px_blocked": SOLVE_BLOCKED,
    "solver_internal_error": INTERNAL,
    "internal_error": INTERNAL,
}


def for_kind(kind: str, *, default: int = SOLVE_FAILED) -> int:
    return _KIND_TO_CODE.get((kind or "").lower(), default)
