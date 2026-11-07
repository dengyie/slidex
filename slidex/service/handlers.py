"""内置打码类型 handler。

``px_press`` 是本次主线（需求单 §3.1/§3.3）；``geetest_v3`` 只为兼容
打码站既有调用方保留——它转调 slidex 自己的 ``SliderSolver``（GeeTest
provider），返回 cookies。两者都不在这里做 HTTP，纯 handler。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from loguru import logger

from slidex.px.models import PxPressResult, PxRequestError
from slidex.px.solver import PxPressSolver

from . import codes
from .registry import HandlerFailure

# 需求单 §3.2：press/silent 的 type 别名（大小写不敏感，见 normalize_type）
_PX_TYPE_ALIASES = ("px_press", "press", "pxcaptcha2", "px_captcha2", "px", "silent", "px_silent")
_GEETEST_TYPE_ALIASES = ("geetest_v3", "geetest", "geetestv3", "gt3")


class PxPressHandler:
    """把 payload 翻成 ``PxPressRequest`` 并交给 ``PxPressSolver``。"""

    type_names: Sequence[str] = _PX_TYPE_ALIASES

    def __init__(self, solver: Optional[Any] = None) -> None:
        self._solver = solver

    @property
    def solver(self) -> Any:
        if self._solver is None:
            self._solver = PxPressSolver()
        return self._solver

    async def solve(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        request = self.validate(payload)
        result = await self.solver.solve(request)
        return self._to_data(result)

    def validate(self, payload: Mapping[str, Any]) -> Any:
        """创建任务时就校验参数，别让错误拖到轮询才暴露。"""
        from slidex.px.models import PxPressRequest

        from .registry import normalize_type

        body = dict(payload)
        if "mode" not in body:
            type_name = normalize_type(body.get("type") or body.get("captchaType"))
            if type_name in {"silent", "px_silent"}:
                body["mode"] = "silent"
            elif type_name in {"press", "px_press", "pxcaptcha2", "px_captcha2"}:
                body["mode"] = "press"
        try:
            return PxPressRequest.from_payload(body)
        except PxRequestError as exc:
            raise HandlerFailure.bad_request(str(exc)) from None

    @staticmethod
    def _to_data(result: PxPressResult) -> Dict[str, Any]:
        if result.success:
            return dict(result.tokens)
        kind = result.error_kind or "px_solve_failed"
        raise HandlerFailure(
            kind=kind,
            message=result.error or "px solve failed",
            retryable=result.retryable_error,
            status_code=429 if kind == "solver_busy" else 200,
            code=codes.for_kind(kind),
        )


def _proxy_url(proxy: Any) -> Optional[str]:
    """服务端收到的 proxy 是对象；SliderSolver 要 URL 串。"""
    if not proxy:
        return None
    if not isinstance(proxy, Mapping):
        return str(proxy)
    host = str(proxy.get("host") or "").strip()
    port = proxy.get("port")
    if not host:
        return None
    try:
        port_text = str(int(port))
    except (TypeError, ValueError):
        return None
    login = str(proxy.get("login") or proxy.get("username") or "").strip()
    password = str(proxy.get("password") or proxy.get("pass") or "").strip()
    auth = f"{login}:{password}@" if login else ""
    return f"http://{auth}{host}:{port_text}"


def _default_geetest_solver_factory(proxy_url: Optional[str]) -> Any:
    from slidex.solver import SliderSolver

    return SliderSolver(provider="geetest", proxy=proxy_url, headless=True)


class GeeTestV3Handler:
    """兼容既有 ``geetest_v3`` 调用方：在 websiteURL 上跑 slidex 滑块链。"""

    type_names: Sequence[str] = _GEETEST_TYPE_ALIASES

    def __init__(
        self,
        solver_factory: Optional[Callable[[Optional[str]], Any]] = None,
    ) -> None:
        self._factory = solver_factory or _default_geetest_solver_factory

    async def solve(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        website = str(
            payload.get("websiteURL") or payload.get("website_url") or payload.get("url") or ""
        ).strip()
        if not website:
            raise HandlerFailure.bad_request("websiteURL is required for geetest_v3")

        proxy_url = _proxy_url(payload.get("proxy"))
        try:
            solver = self._factory(proxy_url)
            success, cookies = await solver.solve(website)
        except Exception as exc:
            logger.warning(f"service: geetest_v3 solve error: {exc}")
            raise HandlerFailure(
                kind="geetest_solver_error",
                message=str(exc),
                retryable=True,
                code=codes.SOLVE_FAILED,
            ) from None

        if not success:
            raise HandlerFailure(
                kind="geetest_failed",
                message="geetest_v3 slider not solved",
                retryable=True,
                code=codes.SOLVE_FAILED,
            )
        return {"result": "success", "cookies": cookies or {}}
