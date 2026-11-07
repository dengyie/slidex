"""自建打码站 HTTP 服务（FastAPI）。

三个入口，覆盖两种调用方：

- ``POST /v1/solve`` —— 站内/自研调用方的同步接口。请求 ``{"type": ...,
  ...参数}``，成功返回 ``{"code":0,"msg":"ok","data":{...}}``；``px_press``
  的 ``data`` 就是 ``{"px3","pxde","pxvid","pxcts"}`` 四件套。
- ``POST /v2/tasks/`` + ``GET /v2/tasks/{taskId}`` —— captcha.run 兼容的
  异步任务协议（需求单 §3.1/§3.2），调用方把 ``CAPTCHA_RUN_BASE`` 指向
  本服务即可无缝切换。
- ``GET /health`` —— ``{ok, active_tasks, done_24h, fail_24h}``。

鉴权：``Authorization: Bearer <key>``（也接受 ``X-Api-Key``）。key 从
``SLIDEX_CAPTCHA_KEYS``（逗号分隔多把）或 ``CAPTCHA_RUN_KEY`` 读；两者
都为空时服务以开放模式启动并打印警告（仅本机/内网调试）。

不落盘：任务只活在内存里，完成即弃；日志只出 token 前缀与长度。
"""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Sequence, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from slidex.px import PxPressSolver

from . import codes
from .handlers import GeeTestV3Handler, PxPressHandler
from .registry import HandlerFailure, HandlerRegistry, normalize_type

# 需求单 §3.2/§6：服务内部求解预算 ≤150s；留一点序列化/收尾余量
_HARD_SOLVE_BUDGET_S = 170.0
_TASK_TTL_S = 30 * 60
_COUNTER_WINDOW_S = 24 * 3600


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _configured_keys() -> Tuple[str, ...]:
    raw = os.environ.get("SLIDEX_CAPTCHA_KEYS") or os.environ.get("CAPTCHA_RUN_KEY") or ""
    keys = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(keys)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header:
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    return (request.headers.get("x-api-key") or "").strip()


@dataclass
class TaskRecord:
    task_id: str
    captcha_type: str
    status: str = "processing"
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    diagnostics: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None

    def public(self) -> Dict[str, Any]:
        if self.status == "success":
            # response 里可能已带 diagnostics（handler 透传），不再重复
            return {"status": "success", "response": dict(self.response or {})}
        if self.status in {"fail", "failed", "error"}:
            body: Dict[str, Any] = {"status": "fail", "error": self.error or "solve failed"}
            if self.diagnostics:
                # 需求单 v2 §3.3：fail 响应附脱敏诊断，老客户端忽略未知键
                body["diagnostics"] = dict(self.diagnostics)
            return body
        return {"status": "processing"}


class TaskStore:
    """内存任务表 + 24h 成败计数；不持久化、不落盘（需求单 §6）。"""

    def __init__(self, *, ttl_s: int = _TASK_TTL_S) -> None:
        self._tasks: Dict[str, TaskRecord] = {}
        self._running: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._ttl_s = ttl_s
        self._done: Deque[float] = deque()
        self._fail: Deque[float] = deque()

    async def create(
        self,
        task_id: str,
        captcha_type: str,
        runner: Any,
        payload: Dict[str, Any],
    ) -> TaskRecord:
        async with self._lock:
            self._purge_locked()
            existing = self._tasks.get(task_id)
            if existing is not None:
                # taskId 是幂等键：重复创建返回原任务，绝不重复消耗求解资源
                return existing
            record = TaskRecord(task_id=task_id, captcha_type=captcha_type)
            self._tasks[task_id] = record
            self._running[task_id] = asyncio.create_task(
                self._run(record, runner, payload)
            )
            return record

    async def get(self, task_id: str) -> Optional[TaskRecord]:
        async with self._lock:
            self._purge_locked()
            return self._tasks.get(task_id)

    async def _run(
        self, record: TaskRecord, runner: Any, payload: Dict[str, Any]
    ) -> None:
        started = time.monotonic()
        ok = False
        try:
            data = await asyncio.wait_for(runner(payload), timeout=_HARD_SOLVE_BUDGET_S)
            record.response = data
            record.status = "success"
            ok = True
        except asyncio.TimeoutError:
            record.status = "fail"
            record.error = "solve_timeout: exceeded service budget"
        except HandlerFailure as exc:
            record.status = "fail"
            record.error = f"{exc.kind}: {exc.message}"
            record.diagnostics = (exc.data or {}).get("diagnostics") or None
        except Exception as exc:  # pragma: no cover - 兜底
            logger.warning(f"service: task {record.task_id} crashed: {exc}")
            record.status = "fail"
            record.error = f"solver_internal_error: {exc}"
        finally:
            record.finished_at = time.monotonic()
            self._running.pop(record.task_id, None)
            self._record_outcome(ok)
            logger.info(
                "service: task {} type={} status={} in {:.2f}s",
                record.task_id,
                record.captcha_type,
                record.status,
                record.finished_at - started,
            )

    def _record_outcome(self, ok: bool) -> None:
        now = time.monotonic()
        (self._done if ok else self._fail).append(now)
        self._trim_locked(now)

    def record_outcome(self, ok: bool) -> None:
        """同步接口（/v1/solve）的成败同样计入 24h 看板。"""
        self._record_outcome(ok)

    def _trim_locked(self, now: float) -> None:
        cutoff = now - _COUNTER_WINDOW_S
        for bucket in (self._done, self._fail):
            while bucket and bucket[0] < cutoff:
                bucket.popleft()

    def _purge_locked(self) -> None:
        now = time.monotonic()
        expired = [
            task_id
            for task_id, record in self._tasks.items()
            if record.finished_at is not None
            and now - record.finished_at > self._ttl_s
        ]
        for task_id in expired:
            self._tasks.pop(task_id, None)

    def counters(self) -> Dict[str, int]:
        now = time.monotonic()
        self._trim_locked(now)
        return {"done_24h": len(self._done), "fail_24h": len(self._fail)}

    async def cancel_all(self) -> None:
        running = list(self._running.values())
        for fut in running:
            fut.cancel()
        for fut in running:
            try:
                await fut
            except (asyncio.CancelledError, Exception):
                pass
        self._running.clear()


def _response(
    status_code: int,
    *,
    code: int,
    msg: str,
    data: Any,
    error: Optional[str] = None,
) -> JSONResponse:
    content: Dict[str, Any] = {"code": code, "msg": msg, "data": data}
    if error is not None:
        content["error"] = error
    return JSONResponse(status_code=status_code, content=content)


def _failure_response(failure: HandlerFailure) -> JSONResponse:
    data: Dict[str, Any] = {
        "kind": failure.kind,
        "retryable": bool(failure.retryable),
        **(failure.data or {}),
    }
    return _response(
        failure.status_code,
        code=failure.code,
        msg=failure.message,
        data=data,
        # 需求单 §3.主体：创建失败是 4xx/5xx + {"error": "..."}。额外带上
        # error 键对 v1 调用方是纯增量，v2 侧则和 captcha.run 形状对齐。
        error=failure.message,
    )


class _Auth:
    def __init__(self, keys: Sequence[str]) -> None:
        self._keys = tuple(k for k in keys if k)
        if not self._keys:
            logger.warning(
                "service: no API key configured (SLIDEX_CAPTCHA_KEYS/CAPTCHA_RUN_KEY empty); "
                "running in open mode"
            )

    def check(self, request: Request) -> None:
        if not self._keys:
            return
        token = _bearer_token(request)
        if not token or not any(
            hmac.compare_digest(token, key) for key in self._keys
        ):
            raise HandlerFailure(
                kind="unauthorized",
                message="invalid or missing API key",
                status_code=401,
                code=codes.UNAUTHORIZED,
            )


async def _read_json(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HandlerFailure.bad_request("request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise HandlerFailure.bad_request("request body must be a JSON object")
    return body


def create_app(
    *,
    keys: Optional[Sequence[str]] = None,
    registry: Optional[HandlerRegistry] = None,
    solver: Optional[Any] = None,
    geetest_solver_factory: Optional[Any] = None,
    task_ttl_s: int = _TASK_TTL_S,
) -> FastAPI:
    """构造服务。所有依赖可注入，测试零网络/零浏览器。"""
    px_solver = solver or PxPressSolver(
        headless=_env_bool("SLIDEX_PX_HEADLESS", True),
        concurrency=_env_int("SLIDEX_PX_CONCURRENCY", 5),
    )
    handlers = [
        PxPressHandler(px_solver),
        GeeTestV3Handler(geetest_solver_factory),
    ]
    handler_registry = registry or HandlerRegistry(handlers)
    auth = _Auth(tuple(keys) if keys is not None else _configured_keys())
    store = TaskStore(ttl_s=task_ttl_s)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 关停时先取消在跑任务，再回收共享 Chromium：否则 systemd restart 后
        # 旧进程的浏览器/pw driver 会苟着，和下一个实例抢内存（1.9G 小机器）。
        yield
        await store.cancel_all()
        close = getattr(px_solver, "close", None)
        if callable(close):
            try:
                await close()
            except Exception as exc:  # pragma: no cover - 关停竞态
                logger.warning(f"service: solver close failed: {exc}")

    app = FastAPI(
        title="slidex captcha service",
        version="1.0.0",
        description="Self-hosted CAPTCHA solving service: PX press-token (px_press) + GeeTest v3.",
        lifespan=lifespan,
    )
    app.state.registry = handler_registry
    app.state.solver = px_solver
    app.state.store = store
    app.state.auth = auth

    @app.exception_handler(HandlerFailure)
    async def _on_handler_failure(_: Request, exc: HandlerFailure) -> JSONResponse:
        return _failure_response(exc)

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        counters = store.counters()
        return {
            "ok": True,
            "active_tasks": int(getattr(px_solver, "active_tasks", 0) or 0),
            "done_24h": counters["done_24h"],
            "fail_24h": counters["fail_24h"],
            "types": list(handler_registry.types()),
        }

    @app.post("/v1/solve")
    async def solve(request: Request) -> JSONResponse:
        auth.check(request)
        payload = await _read_json(request)
        type_name = normalize_type(payload.get("type") or payload.get("captchaType"))
        if not type_name:
            raise HandlerFailure.bad_request("type is required")
        handler = handler_registry.get(type_name)
        body = dict(payload)
        body["type"] = type_name
        try:
            data = await asyncio.wait_for(
                handler.solve(body), timeout=_HARD_SOLVE_BUDGET_S
            )
        except asyncio.TimeoutError:
            store.record_outcome(False)
            raise HandlerFailure(
                kind="solve_timeout",
                message="solve exceeded service budget",
                retryable=True,
                code=codes.SOLVE_TIMEOUT,
            ) from None
        except HandlerFailure:
            # 同步接口的成败也要进 24h 计数，否则 /health 的 fail_24h 永远
            # 只算 v2 任务，运维看板会把同步链路的失败全吞掉。
            store.record_outcome(False)
            raise
        except Exception as exc:
            # handler 把 HanderFailure 之外的异常吞成 500 信封，而不是让
            # traceback 泄漏到响应里；solver 内部异常一律按可重试处理
            logger.warning(f"service: /v1/solve internal error: {exc}")
            store.record_outcome(False)
            raise HandlerFailure(
                kind="solver_internal_error",
                message="unexpected solver error",
                retryable=True,
                code=codes.INTERNAL,
                status_code=500,
            ) from None
        store.record_outcome(True)
        return _response(200, code=codes.OK, msg="ok", data=data)

    @app.post("/v2/tasks/")
    async def create_task(request: Request) -> JSONResponse:
        auth.check(request)
        payload = await _read_json(request)
        captcha_type = str(payload.get("captchaType") or payload.get("type") or "").strip()
        if not captcha_type:
            raise HandlerFailure.bad_request("captchaType is required")
        handler = handler_registry.get(captcha_type)
        task_id = str(payload.get("taskId") or "").strip()
        if not task_id:
            task_id = f"t_{int(time.time() * 1000):x}_{os.urandom(4).hex()}"

        body = dict(payload)
        body["type"] = normalize_type(captcha_type)
        validate = getattr(handler, "validate", None)
        if callable(validate):
            # 参数错误在创建时就暴露为 4xx，而不是让调用方轮询到 fail
            validate(body)

        await store.create(task_id, captcha_type, handler.solve, body)
        return JSONResponse(status_code=200, content={"taskId": task_id})

    @app.get("/v2/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> JSONResponse:
        # captcha.run 的轮询同样带 Bearer key；失败时保持 v2 的 status/error 形状
        try:
            auth.check(request)
        except HandlerFailure as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"status": "fail", "error": exc.message},
            )
        record = await store.get(task_id)
        if record is None:
            return JSONResponse(
                status_code=404,
                content={"status": "fail", "error": "task not found"},
            )
        return JSONResponse(status_code=200, content=record.public())

    return app


app = create_app()
