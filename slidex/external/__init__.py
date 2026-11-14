"""外部服务客户端（非浏览器交互的纯 HTTP 通道）。

与 ``slidex.providers`` 的页面内交互型 provider 平行：外部打码平台
（pigeonw / Turnstile-Solver 兼容协议）等 token 型服务在这里落地，
核心依赖保持轻（标准库 urllib）。
"""

from slidex.external.pigeonw import (
    BalanceInfo,
    CfClearanceSolution,
    PigeonwClient,
    PigeonwError,
    PigeonwSolveError,
    PigeonwTimeoutError,
    TurnstileSolution,
    solve_turnstile,
)

__all__ = [
    "PigeonwClient",
    "PigeonwError",
    "PigeonwSolveError",
    "PigeonwTimeoutError",
    "TurnstileSolution",
    "CfClearanceSolution",
    "BalanceInfo",
    "solve_turnstile",
]