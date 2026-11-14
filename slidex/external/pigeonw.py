"""外部打码平台客户端（token 型挑战，纯 HTTP，无浏览器交互）。

设计取舍（0.12.0）：与 ``slidex.providers`` 的滑块 provider 体系刻意分开——
provider 抽象（``locate_elements``/``perform_slide``/响应监听）承载的是**页面
内交互型**挑战；白鸽（cloudflare.pigeonw.com）这类外部打码平台是纯 HTTP
token 服务：提交 url+sitekey 建任务、轮询拿 token，全程不需要活页面。硬套
provider 抽象会迫使 detect/locate 返回假元素，违反接口语义。

传输层用标准库 urllib：slidex 核心依赖刻意保持轻（opencv/numpy/playwright/
psutil），不为单次 HTTP 调用引入 httpx/aiohttp（与 ``slidex.ocr.llm`` 同一
契约）。同步实现是刻意的——调用方（签到 runner / 注册流程）多在脚本或线程
池上下文使用。

协议：接口与 Turnstile-Solver 完全兼容——

* ``GET /turnstile?url=&sitekey=[&action=][&cdata=]`` → ``{"errorId":0,
  "taskId":"..."}``
* ``GET /result?id=<taskId>`` → processing / ``{"errorId":0,"status":"ready",
  "solution":{"token":...}}`` / ``{"errorId":1,"errorCode":...}``
* ``GET /recaptcha_v3`` / ``GET /recaptcha_enterprise``（必传 action）
* ``GET /cf_clearance?url=[&proxy=]``（结果绑定出口 IP 与 User-Agent）
* ``GET /balance``

鉴权三选一：``Authorization: Bearer <key>`` / ``X-API-Key`` / ``?api_key=``。
成功才计费，失败（``ERROR_CAPTCHA_UNSOLVABLE``）不扣额度可直接重试。

注意：站点前端用 Python 内置 urllib 直连时其默认 UA 会被 Cloudflare 拦截，
本模块自带浏览器 UA（与 ``LlmVisionConfig.user_agent`` 同款）。
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

__all__ = [
    "PigeonwError",
    "PigeonwSolveError",
    "PigeonwTimeoutError",
    "TurnstileSolution",
    "CfClearanceSolution",
    "BalanceInfo",
    "PigeonwClient",
    "solve_turnstile",
]

# 白鸽打码平台（Cloudflare solver）。生产 base，允许 env 覆盖以便指向
# 同协议的其它部署。
DEFAULT_BASE_URL = "https://cloudflare.pigeonw.com"

# 保守浏览器 UA。部分网关按 UA 拦非浏览器客户端（平台文档明确警告 Python
# 内置 urllib 默认 UA 会被 Cloudflare 拦）。
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 支持的任务类型 → 建任务端点路径。recaptcha 族必传 action（平台校验
# 1-100 位字母/数字/下划线/斜杠）。
KIND_ENDPOINTS: Dict[str, str] = {
    "turnstile": "/turnstile",
    "recaptcha_v3": "/recaptcha_v3",
    "recaptcha_enterprise": "/recaptcha_enterprise",
    "cf_clearance": "/cf_clearance",
}

# 轮询节奏：平台文档建议每 1 秒一次。间隔给可覆盖旋钮（测试与限流场景用）。
DEFAULT_POLL_INTERVAL_S = 1.0
# 结果保留默认 10 分钟；轮询超过 solve 超时即放弃（任务可能仍在跑，费用
# 只在成功时扣，放弃不产生额外额度损耗）。
DEFAULT_TIMEOUT_S = 210.0


class PigeonwError(RuntimeError):
    """白鸽打码平台调用失败基类。"""

    def __init__(self, message: str, *, error_code: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code
        self.detail = detail


class PigeonwSolveError(PigeonwError):
    """打码失败（errorId=1）。平台不扣额度，可直接重试。"""


class PigeonwTimeoutError(PigeonwError):
    """轮询超时未出结果。"""


@dataclass
class TurnstileSolution:
    """token 型任务结果。recaptcha 族与 turnstile 同构（多 userAgent/method）。"""

    token: str
    user_agent: str = ""
    method: str = ""
    elapsed_time: float = 0.0
    task_id: str = ""

    @classmethod
    def from_solution(
        cls, solution: Dict[str, Any], *, task_id: str = "", elapsed_time: float = 0.0
    ) -> "TurnstileSolution":
        # elapsed_time 在响应顶层（solution 外），cf_clearance 与 recaptcha 族同构。
        return cls(
            token=str(solution.get("token") or ""),
            user_agent=str(solution.get("userAgent") or ""),
            method=str(solution.get("method") or ""),
            elapsed_time=elapsed_time if elapsed_time else _as_float(solution.get("elapsed_time")),
            task_id=task_id,
        )


@dataclass
class CfClearanceSolution:
    """cf_clearance 结果。绑定解题时的出口 IP 与 User-Agent——请求必须走同一
    出口（proxy 原样带上）并使用返回的 user_agent，否则仍 403。"""

    cookie_name: str = "cf_clearance"
    cookie_value: str = ""
    user_agent: str = ""
    proxy: str = ""
    elapsed_time: float = 0.0
    task_id: str = ""

    @classmethod
    def from_solution(
        cls, solution: Dict[str, Any], *, task_id: str = "", elapsed_time: float = 0.0
    ) -> "CfClearanceSolution":
        cookie = solution.get("cf_clearance") or {}
        return cls(
            cookie_name=str(cookie.get("name") or "cf_clearance"),
            cookie_value=str(cookie.get("value") or ""),
            user_agent=str(solution.get("user_agent") or ""),
            proxy=str(solution.get("proxy") or ""),
            elapsed_time=elapsed_time if elapsed_time else _as_float(solution.get("elapsed_time")),
            task_id=task_id,
        )


@dataclass
class BalanceInfo:
    """``GET /balance`` 返回的额度与限额快照。"""

    balance: int = 0
    quota_total: int = 0
    quota_used: int = 0
    daily_limit: int = 0
    daily_used: int = 0
    rpm_limit: int = 0
    concurrency_limit: int = 0
    in_flight: int = 0
    expires_at: Optional[str] = None

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "BalanceInfo":
        return cls(
            balance=_as_int(body.get("balance")),
            quota_total=_as_int(body.get("quota_total")),
            quota_used=_as_int(body.get("quota_used")),
            daily_limit=_as_int(body.get("daily_limit")),
            daily_used=_as_int(body.get("daily_used")),
            rpm_limit=_as_int(body.get("rpm_limit")),
            concurrency_limit=_as_int(body.get("concurrency_limit")),
            in_flight=_as_int(body.get("in_flight")),
            expires_at=body.get("expires_at"),
        )


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class PigeonwClient:
    """白鸽打码平台客户端（Turnstile-Solver 兼容协议）。

    用法::

        client = PigeonwClient(api_key="sk-...", base_url="https://cloudflare.pigeonw.com")
        sol = client.solve(url="https://example.com", sitekey="0x4AAA...")
        # sol.token 提交到目标站点服务端校验
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        request_timeout_s: float = 15.0,
        user_agent: str = _BROWSER_UA,
    ):
        api_key = str(api_key or "").strip()
        if not api_key:
            raise ValueError("api_key is required (PigeonwClient)")
        self.api_key = api_key
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        # 单任务轮询总预算。turnstile 通常 5-15s；recaptcha 族给默认 210s
        # （对齐平台示例代码的超时带）。
        self.timeout_s = max(1.0, float(timeout_s))
        self.poll_interval_s = max(0.2, float(poll_interval_s))
        # 单次 HTTP 请求超时（建任务/轮询各请求独立计时）。
        self.request_timeout_s = max(1.0, float(request_timeout_s))
        self.user_agent = user_agent

    # ── HTTP 层 ──

    def _headers(self) -> Dict[str, str]:
        # Bearer 头为主；UA 必须是浏览器形态（平台文档明确警告 urllib 默认
        # UA 会被 Cloudflare 拦）。
        return {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": self.user_agent,
        }

    def _get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")}
            )
        url = f"{self.base_url}{path}{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout_s) as response:
                body = response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            # 平台错误码表：401 key 无效 / 402 额度不足 / 403 禁用 / 429 限流 /
            # 503 无节点。HTTP 层错误统一上抛 PigeonwError，errorCode 尽力取。
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="ignore")[:500]
            except Exception:
                pass
            raise PigeonwError(
                f"HTTP {exc.code} from {path}", error_code=f"HTTP_{exc.code}", detail=detail
            ) from exc
        except urllib.error.URLError as exc:
            raise PigeonwError(f"network error calling {path}", detail=str(exc.reason)) from exc
        except (TimeoutError, socket.timeout) as exc:
            # socket.timeout 是 OSError 子类但不是 URLError——轮询读超时若裸穿，
            # 调用方的 except PigeonwError 修不好（真机 review P1 实锤：Python 3.10+
            # socket.timeout=TimeoutError，urlopen 的 timeout 参数同时管建连与读）。
            raise PigeonwError(
                f"request timed out calling {path}", error_code="HTTP_TIMEOUT", detail=str(exc)
            ) from exc
        try:
            data = json.loads(body)
        except ValueError as exc:
            raise PigeonwError(f"invalid JSON from {path}", detail=body[:200]) from exc
        if not isinstance(data, dict):
            raise PigeonwError(f"unexpected response shape from {path}", detail=body[:200])
        return data

    @staticmethod
    def _raise_if_error(data: Dict[str, Any]) -> None:
        if data.get("errorId"):
            raise PigeonwSolveError(
                str(data.get("errorDescription") or "solver returned error"),
                error_code=str(data.get("errorCode") or ""),
                detail=json.dumps(data, ensure_ascii=False)[:500],
            )

    # ── 业务端点 ──

    def create_task(
        self,
        kind: str,
        url: str,
        sitekey: str,
        *,
        action: str = "",
        cdata: str = "",
        proxy: str = "",
        timeout: int = 0,
    ) -> str:
        """提交打码任务，返回 taskId（不等待结果）。"""
        endpoint = KIND_ENDPOINTS.get(kind)
        if endpoint is None:
            raise ValueError(
                f"unknown kind {kind!r}; expected one of {sorted(KIND_ENDPOINTS)}"
            )
        if not url:
            raise ValueError("url is required")
        # cf_clearance 无 sitekey；turnstile/recaptcha 族必填。
        if kind != "cf_clearance" and not sitekey:
            raise ValueError("sitekey is required for token kinds")
        params: Dict[str, str] = {"url": url}
        if sitekey:
            params["sitekey"] = sitekey
        if action:
            params["action"] = action
        if cdata:
            params["cdata"] = cdata
        if proxy:
            params["proxy"] = proxy
        if timeout:
            params["timeout"] = str(int(timeout))
        data = self._get_json(endpoint, params)
        self._raise_if_error(data)
        task_id = str(data.get("taskId") or "")
        if not task_id:
            raise PigeonwError(
                "create task returned no taskId", detail=json.dumps(data, ensure_ascii=False)[:200]
            )
        return task_id

    def get_result(self, task_id: str) -> Dict[str, Any]:
        """查询一次任务结果。返回原始 body：processing / ready / error。"""
        data = self._get_json("/result", {"id": task_id})
        self._raise_if_error(data)
        return data

    def solve(
        self,
        url: str,
        sitekey: str,
        *,
        kind: str = "turnstile",
        action: str = "",
        cdata: str = "",
        proxy: str = "",
        task_timeout: int = 0,
        timeout_s: Optional[float] = None,
    ) -> Any:
        """建任务 + 轮询到出结果。

        turnstile/recaptcha 族返回 :class:`TurnstileSolution`；
        ``kind="cf_clearance"`` 返回 :class:`CfClearanceSolution`。

        ``timeout_s`` 覆盖实例默认的轮询总预算。
        """
        task_id = self.create_task(
            kind, url, sitekey,
            action=action, cdata=cdata, proxy=proxy, timeout=task_timeout,
        )
        budget = self.timeout_s if timeout_s is None else float(timeout_s)
        deadline = time.monotonic() + budget
        while True:
            time.sleep(self.poll_interval_s)
            data = self.get_result(task_id)
            if str(data.get("status") or "") == "processing":
                if time.monotonic() >= deadline:
                    raise PigeonwTimeoutError(
                        f"not solved in {budget:.0f}s", detail=f"task_id={task_id}"
                    )
                continue
            # ready 但 solution 缺字段是平台侧异常（真机 review P1：此前会
            # 返回 token='' 的假成功，调用方拿空 token 去过校验必然失败且难排查）。
            solution = data.get("solution") or {}
            elapsed = _as_float(data.get("elapsed_time"))
            if kind == "cf_clearance":
                result = CfClearanceSolution.from_solution(solution, task_id=task_id, elapsed_time=elapsed)
                if not result.cookie_value:
                    raise PigeonwError(
                        "solver returned ready without cf_clearance cookie",
                        error_code="EMPTY_SOLUTION",
                        detail=json.dumps(data, ensure_ascii=False)[:200],
                    )
                return result
            result = TurnstileSolution.from_solution(solution, task_id=task_id, elapsed_time=elapsed)
            if not result.token:
                raise PigeonwError(
                    "solver returned ready without token",
                    error_code="EMPTY_SOLUTION",
                    detail=json.dumps(data, ensure_ascii=False)[:200],
                )
            return result

    def balance(self) -> BalanceInfo:
        """查询账户余额与限额。"""
        return BalanceInfo.from_body(self._get_json("/balance"))


def solve_turnstile(
    url: str,
    sitekey: str,
    *,
    action: str = "",
    cdata: str = "",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> TurnstileSolution:
    """模块级便捷入口：从 env 读 key（``SLIDEX_PIGEONW_KEY``）一键打码。

    对齐 ``grok-register px_client.py`` 的调用方模式——脚本里一行搞定::

        token = solve_turnstile("https://example.com", "0x4AAA...").token
    """
    key = str(api_key or os.environ.get("SLIDEX_PIGEONW_KEY") or "").strip()
    if not key:
        raise PigeonwError(
            "no api key: pass api_key or set SLIDEX_PIGEONW_KEY",
            error_code="NO_API_KEY",
        )
    client = PigeonwClient(
        key,
        base_url=base_url or os.environ.get("SLIDEX_PIGEONW_BASE") or DEFAULT_BASE_URL,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )
    logger.info(f"[pigeonw] solving turnstile for {url}")
    return client.solve(url, sitekey, action=action, cdata=cdata)
