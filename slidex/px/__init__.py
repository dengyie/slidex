"""PX (PerimeterX) press-token 求解 —— 打码站核心。

两层结构沿用库内惯例（参考 slidex/vision/{point_select,hcaptcha}.py）：
纯逻辑（本包）不碰页面，页面采集在 ``PxPressSolver`` 内做薄封装，
HTTP 服务在 ``slidex/service`` 里消费本包。对外只暴露构造器、请求/结果
模型和错误种类常量，方便测试注入 fake 浏览器工厂。

铁律（docs/captcha-platform-requirements.md §4）：solver 的一切对外请求
必须走任务 payload 里的同一个 sticky proxy，出口不一致 = token 全作废。
"""

from .models import (
    DEFAULT_PX_USER_AGENT,
    PxPressRequest,
    PxPressResult,
    PxRequestError,
    country_default_timezone,
    country_default_locale,
)
from .cookies import (
    extract_px_tokens,
    is_pass_token,
    looks_blocked,
    parse_packed_token,
    redact_token,
)
from .solver import PxPressSolver, PxPressSlots

__all__ = [
    "DEFAULT_PX_USER_AGENT",
    "PxPressRequest",
    "PxPressResult",
    "PxRequestError",
    "PxPressSolver",
    "PxPressSlots",
    "country_default_timezone",
    "country_default_locale",
    "extract_px_tokens",
    "is_pass_token",
    "looks_blocked",
    "parse_packed_token",
    "redact_token",
]