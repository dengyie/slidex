"""slidex.external.pigeonw 单元测试：全部 mock urllib 层，零网络。

协议对齐白鸽打码平台（cloudflare.pigeonw.com，Turnstile-Solver 兼容）：
建任务 /result 轮询 /balance；错误码表与 processing→ready 状态机按平台
文档固化，防上游改版静默漂移。
"""

import json
import urllib.error
import urllib.request
from unittest import mock

import pytest

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


def _client(**kwargs) -> PigeonwClient:
    defaults = dict(api_key="sk-test", poll_interval_s=0.01, request_timeout_s=2.0)
    defaults.update(kwargs)
    return PigeonwClient(**defaults)


def _urlopen_body(body: dict):
    """构造一个带 .read() 的假 urlopen 上下文。"""
    payload = json.dumps(body).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return payload

    return _Resp()


# ── create_task ──


def test_create_task_returns_task_id():
    client = _client()
    with mock.patch.object(
        urllib.request, "urlopen", return_value=_urlopen_body({"errorId": 0, "taskId": "t-1"})
    ):
        assert client.create_task("turnstile", "https://example.com", "0x4AAA") == "t-1"


def test_create_task_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        _client().create_task("geetest", "https://example.com", "0x4AAA")


def test_create_task_requires_url_and_sitekey():
    with pytest.raises(ValueError, match="url is required"):
        _client().create_task("turnstile", "", "0x4AAA")
    with pytest.raises(ValueError, match="sitekey is required"):
        _client().create_task("turnstile", "https://example.com", "")
    # cf_clearance 无 sitekey 也能建任务
    with mock.patch.object(
        urllib.request, "urlopen", return_value=_urlopen_body({"errorId": 0, "taskId": "t-cf"})
    ):
        assert _client().create_task("cf_clearance", "https://example.com", "") == "t-cf"


def test_create_task_error_payload_raises_solve_error():
    client = _client()
    with mock.patch.object(
        urllib.request,
        "urlopen",
        return_value=_urlopen_body(
            {"errorId": 1, "errorCode": "ERROR_ZERO_BALANCE", "errorDescription": "no quota"}
        ),
    ):
        with pytest.raises(PigeonwSolveError) as excinfo:
            client.create_task("turnstile", "https://example.com", "0x4AAA")
    assert excinfo.value.error_code == "ERROR_ZERO_BALANCE"


def test_create_task_builds_query_with_urlencode():
    """action/cdata 可选参数进 query；空值不发送。"""
    client = _client()
    captured = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return _urlopen_body({"errorId": 0, "taskId": "t-2"})

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        client.create_task(
            "turnstile", "https://example.com", "0x4AAA", action="login", cdata=""
        )
    assert "/turnstile?" in captured["url"]
    assert "sitekey=0x4AAA" in captured["url"]
    assert "action=login" in captured["url"]
    assert "cdata" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    # 浏览器 UA（平台明确警告 urllib 默认 UA 被 Cloudflare 拦）
    assert "Mozilla/5.0" in captured["headers"]["User-agent"]


# ── solve 状态机 ──


def test_solve_polls_until_ready():
    client = _client()
    bodies = [
        {"errorId": 0, "taskId": "t-3"},
        {"status": "processing"},
        {"status": "processing"},
        {
            "errorId": 0,
            "status": "ready",
            "solution": {"token": "0.token-abc"},
            "elapsed_time": 7.12,
        },
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        sol = client.solve("https://example.com", "0x4AAA")
    assert isinstance(sol, TurnstileSolution)
    assert sol.token == "0.token-abc"
    assert sol.task_id == "t-3"
    assert sol.elapsed_time == pytest.approx(7.12)


def test_solve_returns_cf_clearance_solution():
    client = _client()
    bodies = [
        {"errorId": 0, "taskId": "t-4"},
        {
            "errorId": 0,
            "status": "ready",
            "solution": {
                "cf_clearance": {"name": "cf_clearance", "value": "u1Lq...", "domain": ".example.com"},
                "user_agent": "Mozilla/5.0 UA",
                "proxy": "socks5h://u:p@h:1080",
            },
            "elapsed_time": 12.5,
        },
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        sol = client.solve("https://example.com", "", kind="cf_clearance")
    assert isinstance(sol, CfClearanceSolution)
    assert sol.cookie_value == "u1Lq..."
    assert sol.proxy == "socks5h://u:p@h:1080"
    assert sol.user_agent == "Mozilla/5.0 UA"


def test_solve_failure_raises_solve_error():
    client = _client()
    bodies = [
        {"errorId": 0, "taskId": "t-5"},
        {
            "errorId": 1,
            "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
            "errorDescription": "Workers could not solve the Captcha",
        },
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        with pytest.raises(PigeonwSolveError) as excinfo:
            client.solve("https://example.com", "0x4AAA")
    assert excinfo.value.error_code == "ERROR_CAPTCHA_UNSOLVABLE"


def test_solve_times_out_when_stuck_processing():
    client = _client(timeout_s=0.5)
    bodies = [
        {"errorId": 0, "taskId": "t-6"},
        {"status": "processing"},
        {"status": "processing"},
        {"status": "processing"},
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        with pytest.raises(PigeonwTimeoutError):
            client.solve("https://example.com", "0x4AAA", timeout_s=0.3)


def test_solve_recaptcha_requires_action_param_passthrough():
    client = _client()
    captured = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _urlopen_body({"errorId": 1, "errorCode": "X", "errorDescription": "stop"})

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        with pytest.raises(PigeonwSolveError):
            client.solve("https://example.com", "6Labc", kind="recaptcha_v3", action="login")
    assert "/recaptcha_v3?" in captured["url"]
    assert "action=login" in captured["url"]


def test_solve_recaptcha_without_action_fails_fast_locally():
    """recaptcha 族缺 action 直接本地 ValueError，不发建任务请求。"""
    client = _client()
    with mock.patch.object(urllib.request, "urlopen") as fake:
        with pytest.raises(ValueError, match="action is required"):
            client.solve("https://example.com", "6Labc", kind="recaptcha_v3")
        with pytest.raises(ValueError, match="action is required"):
            client.solve("https://example.com", "6Labc", kind="recaptcha_enterprise")
    fake.assert_not_called()


def test_solve_tolerates_transient_poll_errors_until_deadline():
    """轮询途中瞬态错误（5xx/URLError/读超时）容忍到 deadline，任务可能仍在平台侧跑。

    review P2 实锤：此前单次瞬态网络错误直接杀死整个 solve，白烧一次建任务
    （且平台侧任务成功仍会计费）。
    """
    client = _client(timeout_s=2.0, poll_interval_s=0.01)
    bodies = [
        {"errorId": 0, "taskId": "t-transient"},
        urllib.error.HTTPError("u", 503, "no node", {}, None),  # 瞬态
        urllib.error.URLError("conn reset"),  # 瞬态
        TimeoutError("read timed out"),  # 瞬态
        {
            "errorId": 0,
            "status": "ready",
            "solution": {"token": "0.ok"},
            "elapsed_time": 3.1,
        },
    ]

    def _fake_urlopen(request, timeout):
        item = bodies.pop(0)
        if isinstance(item, Exception):
            raise item
        return _urlopen_body(item)

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        sol = client.solve("https://example.com", "0x4AAA")
    assert sol.token == "0.ok"


def test_solve_transient_errors_raise_timeout_with_last_error():
    """瞬态错误持续到 deadline → PigeonwTimeoutError，detail 带最后一次错误。

    建任务成功后轮询持续失败：建任务阶段的错误不在容忍范围（任务没建成，
    没什么可等），只有轮询阶段才容忍瞬态。
    """
    client = _client(timeout_s=0.3, poll_interval_s=0.01)
    bodies = [
        {"errorId": 0, "taskId": "t-stuck"},
    ]

    def _fake_urlopen(request, timeout):
        if bodies:
            return _urlopen_body(bodies.pop(0))
        raise urllib.error.URLError("down")

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        with pytest.raises(PigeonwTimeoutError) as excinfo:
            client.solve("https://example.com", "0x4AAA")
    assert excinfo.value.detail.startswith("task_id=t-stuck")
    assert "last_error=NETWORK:" in excinfo.value.detail
    assert "network error calling /result" in excinfo.value.detail


def test_solve_fails_fast_on_deterministic_http_errors():
    """401（key 无效）等确定性错误不容忍，立即上抛。"""
    client = _client(poll_interval_s=0.01)
    with mock.patch.object(
        urllib.request,
        "urlopen",
        side_effect=urllib.error.HTTPError("u", 401, "Unauthorized", {}, None),
    ):
        with pytest.raises(PigeonwError) as excinfo:
            client.solve("https://example.com", "0x4AAA")
    assert excinfo.value.error_code == "HTTP_401"


def test_solve_rejects_non_positive_timeout_s():
    """per-call timeout_s<=0 显式拒绝（构造器有 max(1.0) 夹取，per-call 一致防呆）。"""
    client = _client()
    with pytest.raises(ValueError, match="timeout_s must be positive"):
        client.solve("https://example.com", "0x4AAA", timeout_s=0)


def test_solve_poll_error_does_not_swallow_solve_error():
    """errorId=1 业务失败在轮询中也要立即上抛，不做瞬态容忍。"""
    client = _client(timeout_s=5.0, poll_interval_s=0.01)
    bodies = [
        {"errorId": 0, "taskId": "t-fail"},
        {"errorId": 1, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE", "errorDescription": "nope"},
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        with pytest.raises(PigeonwSolveError):
            client.solve("https://example.com", "0x4AAA")


def test_solve_ready_without_token_raises_not_fake_success():
    """ready 但 solution 缺 token → PigeonwError，绝不返回 token='' 的假成功。

    review P1 实锤：此前该响应会被 from_solution 吞成空 token 成功返回，
    调用方拿空 token 过站必败且难排查。
    """
    client = _client()
    bodies = [
        {"errorId": 0, "taskId": "t-empty"},
        {"errorId": 0, "status": "ready"},  # 无 solution
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        with pytest.raises(PigeonwError) as excinfo:
            client.solve("https://example.com", "0x4AAA")
    assert excinfo.value.error_code == "EMPTY_SOLUTION"


def test_solve_cf_clearance_ready_without_cookie_raises():
    """cf_clearance ready 但缺 cookie → PigeonwError（EMPTY_SOLUTION）。"""
    client = _client()
    bodies = [
        {"errorId": 0, "taskId": "t-empty-cf"},
        {"errorId": 0, "status": "ready", "solution": {"user_agent": "UA"}},
    ]
    with mock.patch.object(urllib.request, "urlopen", side_effect=[_urlopen_body(b) for b in bodies]):
        with pytest.raises(PigeonwError) as excinfo:
            client.solve("https://example.com", "", kind="cf_clearance")
    assert excinfo.value.error_code == "EMPTY_SOLUTION"


def test_read_timeout_wrapped_as_pigeonw_error():
    """读超时（socket.timeout=TimeoutError）必须包成 PigeonwError。

    review P1 实锤：TimeoutError 是 OSError 子类但不是 URLError，此前会
    裸穿 _get_json，调用方 except PigeonwError 兜不住。
    """
    client = _client()
    with mock.patch.object(urllib.request, "urlopen", side_effect=TimeoutError("read timed out")):
        with pytest.raises(PigeonwError) as excinfo:
            client.balance()
    assert excinfo.value.error_code == "HTTP_TIMEOUT"


# ── HTTP 层错误 ──


def test_http_error_wrapped_as_pigeonw_error():
    client = _client()
    exc = urllib.error.HTTPError(
        "https://cloudflare.pigeonw.com/balance", 401, "Unauthorized", {}, None
    )
    with mock.patch.object(urllib.request, "urlopen", side_effect=exc):
        with pytest.raises(PigeonwError) as excinfo:
            client.balance()
    assert excinfo.value.error_code == "HTTP_401"


def test_network_error_wrapped():
    client = _client()
    with mock.patch.object(
        urllib.request, "urlopen", side_effect=urllib.error.URLError("conn refused")
    ):
        with pytest.raises(PigeonwError, match="network error"):
            client.balance()


def test_invalid_json_wrapped():
    client = _client()

    class _Bad:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"<html>blocked</html>"

    with mock.patch.object(urllib.request, "urlopen", return_value=_Bad()):
        with pytest.raises(PigeonwError, match="invalid JSON"):
            client.balance()


# ── balance ──


def test_balance_parses_full_body():
    client = _client()
    body = {
        "errorId": 0,
        "balance": 886,
        "quota_total": 886,
        "quota_used": 0,
        "daily_limit": 0,
        "daily_used": 0,
        "rpm_limit": 30,
        "concurrency_limit": 5,
        "in_flight": 0,
        "expires_at": None,
    }
    with mock.patch.object(urllib.request, "urlopen", return_value=_urlopen_body(body)):
        info = client.balance()
    assert isinstance(info, BalanceInfo)
    assert info.balance == 886
    assert info.rpm_limit == 30
    assert info.concurrency_limit == 5
    assert info.expires_at is None


# ── 模块级便捷入口 ──


def test_solve_turnstile_reads_env_key(monkeypatch):
    monkeypatch.setenv("SLIDEX_PIGEONW_KEY", "sk-env")
    captured = {}

    def _fake_urlopen(request, timeout):
        captured["auth"] = request.headers["Authorization"]
        return _urlopen_body({"errorId": 1, "errorCode": "X", "errorDescription": "stop"})

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        with pytest.raises(PigeonwSolveError):
            solve_turnstile("https://example.com", "0x4AAA", poll_interval_s=0.01)
    assert captured["auth"] == "Bearer sk-env"


def test_solve_turnstile_explicit_empty_key_raises_without_env(monkeypatch):
    """api_key="" 传空且 env 也没有 key 时，报 NO_API_KEY 而非静默走空串。"""
    monkeypatch.delenv("SLIDEX_PIGEONW_KEY", raising=False)
    with pytest.raises(PigeonwError, match="no api key"):
        solve_turnstile("https://example.com", "0x4AAA", api_key="", poll_interval_s=0.01)


def test_solve_turnstile_without_key_raises():
    import os

    saved = os.environ.pop("SLIDEX_PIGEONW_KEY", None)
    try:
        with pytest.raises(PigeonwError) as excinfo:
            solve_turnstile("https://example.com", "0x4AAA")
    finally:
        if saved is not None:
            os.environ["SLIDEX_PIGEONW_KEY"] = saved
    assert excinfo.value.error_code == "NO_API_KEY"


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("SLIDEX_PIGEONW_BASE", "http://127.0.0.1:9999")
    monkeypatch.setenv("SLIDEX_PIGEONW_KEY", "sk-env")
    captured = {}

    def _fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _urlopen_body({"errorId": 1, "errorCode": "X", "errorDescription": "stop"})

    with mock.patch.object(urllib.request, "urlopen", side_effect=_fake_urlopen):
        with pytest.raises(PigeonwSolveError):
            solve_turnstile("https://example.com", "0x4AAA", poll_interval_s=0.01)
    assert captured["url"].startswith("http://127.0.0.1:9999/turnstile")


def test_client_requires_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        PigeonwClient(api_key="  ")


def test_public_exports():
    """顶层 slidex 导出防漂移。"""
    import slidex

    for name in ("PigeonwClient", "solve_turnstile", "TurnstileSolution", "BalanceInfo"):
        assert getattr(slidex, name, None) is not None, name
