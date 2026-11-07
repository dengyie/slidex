"""PX press 请求/结果模型（纯数据，无页面与网络依赖）。

字段命名对齐需求单 docs/captcha-platform-requirements.md §3.1：
``appId/uaid/uuid/vid/userAgent/country/timezone/websiteURL/challengeUrl/
cookies/proxy``；同时兼容 snake_case，便于站内调用方直接透传 dict。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

# 与 Outlook 注册侧默认 UA 同族（Windows + Chromium），country/timezone 对齐后才可信
DEFAULT_PX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

# 需求单 §3.1 的国家→时区映射；未知国家回退美东（与注册侧一致）
_COUNTRY_TIMEZONE: Dict[str, str] = {
    "US": "America/New_York",
    "CA": "America/Toronto",
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "NL": "Europe/Amsterdam",
    "SE": "Europe/Stockholm",
    "CH": "Europe/Zurich",
    "IT": "Europe/Rome",
    "ES": "Europe/Madrid",
    "PL": "Europe/Warsaw",
    "TR": "Europe/Istanbul",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "SG": "Asia/Singapore",
    "HK": "Asia/Hong_Kong",
    "CN": "Asia/Shanghai",
    "TW": "Asia/Taipei",
    "MY": "Asia/Kuala_Lumpur",
    "TH": "Asia/Bangkok",
    "VN": "Asia/Ho_Chi_Minh",
    "ID": "Asia/Jakarta",
    "PH": "Asia/Manila",
    "IN": "Asia/Kolkata",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "IL": "Asia/Jerusalem",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
    "BR": "America/Sao_Paulo",
    "MX": "America/Mexico_City",
    "ZA": "Africa/Johannesburg",
}

_COUNTRY_LOCALE: Dict[str, str] = {
    "US": "en-US",
    "CA": "en-CA",
    "GB": "en-GB",
    "IE": "en-IE",
    "AU": "en-AU",
    "NZ": "en-NZ",
    "IN": "en-IN",
    "SG": "en-SG",
    "MY": "en-MY",
    "PH": "en-PH",
    "ZA": "en-ZA",
    "DE": "de-DE",
    "CH": "de-CH",
    "FR": "fr-FR",
    "NL": "nl-NL",
    "SE": "sv-SE",
    "IT": "it-IT",
    "ES": "es-ES",
    "MX": "es-MX",
    "BR": "pt-BR",
    "PL": "pl-PL",
    "TR": "tr-TR",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "HK": "zh-HK",
    "TW": "zh-TW",
    "CN": "zh-CN",
    "TH": "th-TH",
    "VN": "vi-VN",
    "ID": "id-ID",
    "AE": "ar-AE",
    "SA": "ar-SA",
    "IL": "he-IL",
}

# 失败类别：确定性 vs 可重试（需求单 §6）。服务层用 kind 生成 error 字段。
ERR_INVALID_REQUEST = "invalid_request"
ERR_UNAUTHORIZED = "unauthorized"
ERR_UNSUPPORTED_TYPE = "unsupported_type"
ERR_SOLVER_BUSY = "solver_busy"
ERR_BROWSER_LAUNCH = "browser_launch_failed"
ERR_CHALLENGE_HTTP = "challenge_http_error"
ERR_CHALLENGE_TIMEOUT = "challenge_load_timeout"
ERR_TOKEN_TIMEOUT = "px_token_timeout"
ERR_TOKEN_BLOCKED = "px_token_blocked"
ERR_COOKIE_PARSE = "cookie_parse_failed"
ERR_INTERNAL = "solver_internal_error"

RETRYABLE_KINDS = frozenset(
    {
        ERR_SOLVER_BUSY,
        ERR_BROWSER_LAUNCH,
        ERR_CHALLENGE_TIMEOUT,
        ERR_TOKEN_TIMEOUT,
        ERR_INTERNAL,
    }
)


class PxRequestError(ValueError):
    """参数校验失败：确定性错误，调用方重试同一 payload 不会变好。"""

    kind = ERR_INVALID_REQUEST
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def country_default_timezone(country: str) -> str:
    return _COUNTRY_TIMEZONE.get((country or "").upper(), "America/New_York")


def country_default_locale(country: str) -> str:
    return _COUNTRY_LOCALE.get((country or "").upper(), "en-US")


def _pick(payload: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_cookie_map(value: Any) -> Dict[str, str]:
    """接受 {name: value} 或 [{"name","value"}]，其余形态直接拒绝。"""
    out: Dict[str, str] = {}
    if value is None:
        return out
    if isinstance(value, Mapping):
        for name, raw in value.items():
            text = _as_text(raw)
            if text and name:
                out[str(name)] = text
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            if not isinstance(item, Mapping):
                raise PxRequestError("cookies entries must be objects with name/value")
            name = _as_text(item.get("name"))
            text = _as_text(item.get("value"))
            if name and text:
                out[name] = text
        return out
    raise PxRequestError("cookies must be an object or a list of {name,value}")


def _as_proxy(value: Any) -> Dict[str, Any]:
    """规范化 proxy 对象；空值表示直连（仅本地测试允许）。"""
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise PxRequestError("proxy must be an object with host/port")
    host = _as_text(_pick(value, "host", "server", "addr", default=""))
    port_raw = _pick(value, "port", default=0)
    if not host and not port_raw:
        return {}
    if not host:
        raise PxRequestError("proxy.host is required when proxy is provided")
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        raise PxRequestError("proxy.port must be an integer") from None
    if not (0 < port < 65536):
        raise PxRequestError("proxy.port out of range")
    login = _as_text(_pick(value, "login", "username", "user", default=""))
    password = _as_text(_pick(value, "password", "pass", default=""))
    return {"host": host, "port": port, "login": login, "password": password}


@dataclass(slots=True)
class PxPressRequest:
    """一道 press/silent 挑战的完整上下文。

    ``mode`` 为 ``press`` 时必须有 challengeUrl；``silent`` 只用 websiteURL。
    """

    app_id: str
    challenge_url: str = ""
    website_url: str = ""
    uaid: str = ""
    uuid: str = ""
    vid: str = ""
    user_agent: str = DEFAULT_PX_USER_AGENT
    country: str = "US"
    timezone: str = ""
    locale: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    proxy: Dict[str, Any] = field(default_factory=dict)
    solve_timeout_s: float = 120.0
    poll_interval_s: float = 2.0
    mode: str = "press"

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        default_timeout_s: float = 120.0,
    ) -> "PxPressRequest":
        if not isinstance(payload, Mapping):
            raise PxRequestError("payload must be a JSON object")

        app_id = _as_text(_pick(payload, "appId", "app_id", "appid"))
        if not app_id:
            raise PxRequestError("appId is required")

        challenge_url = _as_text(
            _pick(payload, "challengeUrl", "challenge_url", "url")
        )
        website_url = _as_text(_pick(payload, "websiteURL", "website_url", "siteUrl"))

        mode = _as_text(_pick(payload, "mode", default="")).lower()
        if mode not in {"press", "silent"}:
            mode = "press" if challenge_url else "silent"
        if mode == "press" and not challenge_url:
            raise PxRequestError("challengeUrl is required for press tasks")
        if mode == "silent" and not website_url:
            raise PxRequestError("websiteURL is required for silent tasks")
        if not challenge_url and not website_url:
            raise PxRequestError("challengeUrl or websiteURL is required")

        country = _as_text(_pick(payload, "country", default="US")).upper() or "US"
        timezone = _as_text(_pick(payload, "timezone", "timeZone")) or country_default_timezone(country)
        locale = _as_text(_pick(payload, "locale", "language")) or country_default_locale(country)
        user_agent = _as_text(_pick(payload, "userAgent", "user_agent")) or DEFAULT_PX_USER_AGENT

        try:
            timeout_s = float(_pick(payload, "timeout", "solveTimeout", default=default_timeout_s))
        except (TypeError, ValueError):
            timeout_s = default_timeout_s
        # 需求单 §3.2/§6：服务内部求解预算 ≤150s，太小则必然失败
        timeout_s = min(150.0, max(10.0, timeout_s))

        try:
            poll_interval_s = float(_pick(payload, "pollInterval", default=2.0))
        except (TypeError, ValueError):
            poll_interval_s = 2.0
        poll_interval_s = min(10.0, max(0.25, poll_interval_s))

        return cls(
            app_id=app_id,
            challenge_url=challenge_url,
            website_url=website_url,
            uaid=_as_text(_pick(payload, "uaid")),
            uuid=_as_text(_pick(payload, "uuid")),
            vid=_as_text(_pick(payload, "vid")),
            user_agent=user_agent,
            country=country,
            timezone=timezone,
            locale=locale,
            cookies=_as_cookie_map(_pick(payload, "cookies", default=None)),
            proxy=_as_proxy(_pick(payload, "proxy", default=None)),
            solve_timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            mode=mode,
        )

    # ---- 派生 -------------------------------------------------------------
    @property
    def target_url(self) -> str:
        """真正要被打开的那一页：press 走 hsprotect iframe，silent 走站点首页。"""
        return self.challenge_url or self.website_url

    @property
    def referer(self) -> str:
        """challenge 页带上站点 Referer，贴近真实 iframe 内嵌上下文。"""
        return self.website_url if self.website_url != self.target_url else ""

    @property
    def uses_proxy(self) -> bool:
        return bool(self.proxy.get("host") and self.proxy.get("port"))

    def playwright_proxy(self) -> Optional[Dict[str, str]]:
        """Playwright context-level proxy（HTTP CONNECT）。"""
        if not self.uses_proxy:
            return None
        settings = {"server": f"http://{self.proxy['host']}:{self.proxy['port']}"}
        login = _as_text(self.proxy.get("login"))
        password = _as_text(self.proxy.get("password"))
        if login:
            settings["username"] = login
        if password:
            settings["password"] = password
        return settings


@dataclass(slots=True)
class PxPressResult:
    """一次求解的终态。``success`` 只由 px3 是否含 ``:1000:`` 决定。"""

    success: bool
    tokens: Dict[str, str] = field(default_factory=dict)
    mode: str = "press"
    error_kind: str = ""
    error: str = ""
    retryable: bool = False
    duration_ms: int = 0
    missing: List[str] = field(default_factory=list)

    @property
    def retryable_error(self) -> bool:
        if not self.success and self.error_kind in RETRYABLE_KINDS:
            return True
        return bool(self.retryable)

    def error_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.error_kind or "unknown",
            "message": self.error or "solve failed",
            "retryable": self.retryable_error,
        }
