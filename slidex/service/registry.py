"""打码类型注册表。

一个「打码类型」= 一个 handler：接收已解析的 JSON payload，返回要放进
``data`` 的 dict；失败抛 ``HandlerFailure``。类型名大小写/连字符不敏感，
并且允许别名（``press`` / ``PxCaptcha2`` 都指 px_press）——调用方的
拼写差异不该变成 400。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from . import codes


def normalize_type(name: Any) -> str:
    """统一类型名：小写、下划线分隔（``PxCaptcha2`` → ``pxcaptcha2``）。"""
    text = str(name or "").strip().lower()
    return text.replace("-", "_").replace(" ", "_")


@dataclass(slots=True)
class HandlerFailure(Exception):
    """handler 的确定性/临时失败，由路由层翻成统一错误响应。"""

    kind: str
    message: str
    retryable: bool = False
    status_code: int = 200
    code: int = codes.SOLVE_FAILED
    data: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - 仅日志
        return f"{self.kind}: {self.message}"

    @classmethod
    def unsupported(cls, name: str) -> "HandlerFailure":
        return cls(
            kind="unsupported_type",
            message=f"unsupported captcha type: {name!r}",
            status_code=400,
            code=codes.UNSUPPORTED_TYPE,
        )

    @classmethod
    def bad_request(cls, message: str) -> "HandlerFailure":
        return cls(
            kind="invalid_request",
            message=message,
            status_code=422,
            code=codes.INVALID_REQUEST,
        )


@runtime_checkable
class SolveHandler(Protocol):
    type_names: Sequence[str]

    async def solve(self, payload: Mapping[str, Any]) -> Dict[str, Any]: ...


class HandlerRegistry:
    def __init__(self, handlers: Iterable[SolveHandler] = ()) -> None:
        self._handlers: Dict[str, SolveHandler] = {}
        for handler in handlers:
            self.register(handler)

    def register(self, handler: SolveHandler) -> None:
        for name in handler.type_names:
            key = normalize_type(name)
            if not key:
                continue
            if key in self._handlers:
                raise ValueError(f"duplicate captcha type: {name}")
            self._handlers[key] = handler

    def get(self, name: Any) -> SolveHandler:
        key = normalize_type(name)
        handler = self._handlers.get(key)
        if handler is None:
            raise HandlerFailure.unsupported(str(name or ""))
        return handler

    def types(self) -> Sequence[str]:
        return sorted(self._handlers)

    def canonical_types(self) -> Sequence[str]:
        """每个 handler 一个名字（type_names 首个）：/health 的可读视图。

        全别名列表（press/px/gt3/…）是路由层的宽容，运维看板只需要知道
        「有几种求解器」。
        """
        seen = set()
        out = []
        for handler in self._handlers.values():
            marker = id(handler)
            if marker in seen:
                continue
            seen.add(marker)
            for name in handler.type_names:
                key = normalize_type(name)
                if key in self._handlers and self._handlers[key] is handler:
                    out.append(key)
                    break
        return sorted(out)
