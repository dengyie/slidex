"""自建打码站服务。

- ``create_app()`` / ``app``：FastAPI 应用（``POST /v1/solve``、
  captcha.run 兼容的 ``/v2/tasks``、``GET /health``）。
- ``PxPressHandler`` / ``GeeTestV3Handler`` / ``HandlerRegistry``：类型注册。

FastAPI 属可选依赖（``slidex[server]``），核心库 import slidex 不会拉起
本包——需要时显式 ``from slidex.service.app import app``。
"""

from .app import TaskRecord, TaskStore, create_app
from .handlers import GeeTestV3Handler, PxPressHandler
from .registry import HandlerFailure, HandlerRegistry, normalize_type

__all__ = [
    "create_app",
    "HandlerFailure",
    "HandlerRegistry",
    "PxPressHandler",
    "GeeTestV3Handler",
    "TaskRecord",
    "TaskStore",
    "normalize_type",
]
