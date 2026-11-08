"""打码站 HTTP 接口 — 路由/鉴权/错误语义/两套协议。

全部依赖注入：假 solver 同步返回，不碰浏览器、不发网络。断言重点是对外
契约——``{code,msg,data}`` 信封、四件套形状、:1000: 判据对应的成功/失败码、
captcha.run 兼容的 ``/v2/tasks`` 三段状态、以及 401/422/429 的 HTTP 语义。
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from slidex.px.models import PxPressResult
from slidex.service.app import create_app

_PASS_TOKENS = {
    "px3": "1:a:1000:abcdef::",
    "pxde": "{}",
    "pxvid": "vid-1",
    "pxcts": "cts-1",
}


class _FakePxSolver:
    def __init__(self, result=None, *, exc=None):
        self.result = result or PxPressResult(True, tokens=dict(_PASS_TOKENS))
        self.exc = exc
        self.requests = []

    async def solve(self, request):
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return self.result


class _FakeGeeTestSolver:
    def __init__(self, *, success=True, cookies=None, exc=None):
        self.success = success
        self.cookies = cookies if cookies is not None else {"x5sec": "ok"}
        self.exc = exc

    async def solve(self, url):
        if self.exc is not None:
            raise self.exc
        return self.success, self.cookies


def _client(solver=None, *, keys=("secret",), geetest_factory=None):
    app = create_app(
        keys=list(keys) if keys is not None else None,
        solver=solver or _FakePxSolver(),
        geetest_solver_factory=geetest_factory,
    )
    return TestClient(app)


_PX_BODY = {
    "type": "px_press",
    "appId": "PXzC5j78di",
    "challengeUrl": "https://iframe.hsprotect.net/challenge",
    "websiteURL": "https://signup.live.com/",
    "country": "US",
}


# ────────────────────── 鉴权 ──────────────────────


def test_missing_key_is_401():
    client = _client()
    response = client.post("/v1/solve", json=_PX_BODY)
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 401
    assert body["data"]["kind"] == "unauthorized"


def test_wrong_key_is_401():
    client = _client()
    response = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_bearer_and_api_key_both_work():
    client = _client()
    r1 = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    )
    r2 = client.post("/v1/solve", json=_PX_BODY, headers={"X-Api-Key": "secret"})
    assert r1.status_code == 200 and r1.json()["code"] == 0
    assert r2.status_code == 200 and r2.json()["code"] == 0


def test_open_mode_when_no_keys(monkeypatch):
    monkeypatch.delenv("SLIDEX_CAPTCHA_KEYS", raising=False)
    monkeypatch.delenv("CAPTCHA_RUN_KEY", raising=False)
    client = _client(keys=None)
    response = client.post("/v1/solve", json=_PX_BODY)
    assert response.status_code == 200


# ────────────────────── /v1/solve ──────────────────────


def test_px_press_success_returns_quadruple():
    client = _client()
    response = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0 and body["msg"] == "ok"
    assert body["data"] == _PASS_TOKENS


def test_request_fields_reach_solver():
    solver = _FakePxSolver()
    client = _client(solver)
    client.post(
        "/v1/solve",
        json={
            **_PX_BODY,
            "uaid": "u1",
            "uuid": "uu1",
            "vid": "v1",
            "proxy": {"host": "1.2.3.4", "port": 8000, "login": "a", "password": "b"},
        },
        headers={"Authorization": "Bearer secret"},
    )
    request = solver.requests[0]
    assert request.app_id == "PXzC5j78di"
    assert request.mode == "press"
    assert (request.uaid, request.uuid, request.vid) == ("u1", "uu1", "v1")
    assert request.proxy == {"host": "1.2.3.4", "port": 8000, "login": "a", "password": "b"}


def test_type_aliases_resolve_to_same_handler():
    solver = _FakePxSolver()
    client = _client(solver)
    for type_name in ("PxCaptcha2", "px-press", "press"):
        response = client.post(
            "/v1/solve",
            json={"type": type_name, "appId": "a", "challengeUrl": "https://iframe.hsprotect.net/c"},
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200, (type_name, response.text)


def test_pxcaptcha2_with_only_website_url_infers_silent():
    """调用方 silent 回退的形状：captchaType=PxCaptcha2 + 只有 websiteURL。

    线上实测过这个 422：按类型名硬判 press 会把 silent 回退直接打死。
    """
    solver = _FakePxSolver()
    client = _client(solver)
    response = client.post(
        "/v1/solve",
        json={"type": "PxCaptcha2", "appId": "a", "websiteURL": "https://signup.live.com/"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200, response.text
    assert solver.requests[0].mode == "silent"


def test_pxcaptcha2_with_both_urls_stays_press():
    solver = _FakePxSolver()
    client = _client(solver)
    client.post(
        "/v1/solve",
        json={
            "type": "PxCaptcha2",
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "challengeUrl": "https://iframe.hsprotect.net/c",
        },
        headers={"Authorization": "Bearer secret"},
    )
    assert solver.requests[0].mode == "press"


def test_pxcaptcha2_without_any_url_is_422():
    client = _client()
    response = client.post(
        "/v1/solve",
        json={"type": "PxCaptcha2", "appId": "a"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    assert response.json()["data"]["kind"] == "invalid_request"


def test_v2_silent_variant_accepts_website_url_only():
    """v2 协议同样要容忍 PxCaptcha2 + 仅 websiteURL（press 失败后的回退）。"""
    solver = _FakePxSolver()
    client = _client(solver)
    response = client.post(
        "/v2/tasks/",
        json={"captchaType": "PxCaptcha2", "appId": "a", "websiteURL": "https://signup.live.com/"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["taskId"]
    assert solver.requests[0].mode == "silent"


def test_missing_type_is_422():
    client = _client()
    response = client.post(
        "/v1/solve", json={"appId": "a"}, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == 422


def test_missing_app_id_is_422():
    client = _client()
    response = client.post(
        "/v1/solve",
        json={"type": "px_press", "challengeUrl": "https://iframe.hsprotect.net/c"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    assert response.json()["data"]["kind"] == "invalid_request"


def test_failure_envelope_carries_error_key():
    """需求单 §3.1：创建失败是 4xx/5xx + {"error": "..."}。"""
    client = _client()
    response = client.post(
        "/v2/tasks/",
        json={"captchaType": "PxCaptcha2", "websiteURL": "https://signup.live.com/"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "appId is required"
    assert body["msg"] == body["error"]
    assert body["code"] == 422


def test_unsupported_type_is_400():
    client = _client()
    response = client.post(
        "/v1/solve",
        json={"type": "recaptcha_v9"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == 400
    assert response.json()["data"]["kind"] == "unsupported_type"


def test_non_json_body_is_422():
    client = _client()
    response = client.post(
        "/v1/solve",
        content=b"not json",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_solve_failure_is_http_200_with_business_code():
    blocked = PxPressResult(
        False,
        error_kind="px_token_blocked",
        error="px3 lacks :1000:",
        retryable=False,
    )
    client = _client(_FakePxSolver(blocked))
    response = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 1003
    assert body["data"]["kind"] == "px_token_blocked"
    assert body["data"]["retryable"] is False


def test_solve_timeout_maps_to_1002():
    timed_out = PxPressResult(
        False, error_kind="px_token_timeout", error="no pass token", retryable=True
    )
    client = _client(_FakePxSolver(timed_out))
    body = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    ).json()
    assert body["code"] == 1002
    assert body["data"]["retryable"] is True


def test_load_failure_maps_to_1005_not_1002():
    """连不上挑战页 → 1005，与「页面加载了但没出 token」的 1002 分开。

    1002 告诉调用方换 IP / 降风控；1005 告诉它先查代理链路，两者处置不同。
    """
    for kind in ("challenge_load_failed", "challenge_load_timeout"):
        failed = PxPressResult(
            False, error_kind=kind, error="challenge page unreachable", retryable=True
        )
        client = _client(_FakePxSolver(failed))
        body = client.post(
            "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
        ).json()
        assert body["code"] == 1005, kind
        assert body["data"]["kind"] == kind
        assert body["data"]["retryable"] is True


def test_proxy_auth_407_maps_to_1006_not_1001():
    """代理要认证（407）→ 1006，与页面层 403/5xx 的 1001 分开。

    407 是代理配置坏了（改代理密码），403/404 是被拦或配置错，5xx 可重试——
    处置方向不同，数字码层面就要能区分。
    """
    failed = PxPressResult(
        False, error_kind="challenge_proxy_auth",
        error="proxy requires authentication (407)", retryable=False,
    )
    client = _client(_FakePxSolver(failed))
    body = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    ).json()
    assert body["code"] == 1006
    assert body["data"]["kind"] == "challenge_proxy_auth"
    assert body["data"]["retryable"] is False


def test_solver_busy_is_http_429():
    busy = PxPressResult(
        False, error_kind="solver_busy", error="slots exhausted", retryable=True
    )
    client = _client(_FakePxSolver(busy))
    response = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 429
    assert response.json()["code"] == 1004


def test_solver_exception_is_internal_envelope():
    client = _client(_FakePxSolver(exc=RuntimeError("kaboom")))
    response = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == 500
    assert body["data"]["kind"] == "solver_internal_error"
    assert body["data"]["retryable"] is True


# ────────────────────── /health ──────────────────────


def test_health_shape_and_types():
    client = _client()
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["active_tasks"] == 0
    assert body["done_24h"] == 0
    assert body["fail_24h"] == 0
    assert "px_press" in body["types"]
    assert "geetest_v3" in body["types"]


# ────────────────────── /v2/tasks（captcha.run 兼容） ──────────────────────


def _wait_status(client, task_id, headers, *, want=("success", "fail"), timeout=3.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/v2/tasks/{task_id}", headers=headers).json()
        if last.get("status") in want:
            return last
        time.sleep(0.02)
    return last


def test_v2_create_then_success():
    client = _client()
    headers = {"Authorization": "Bearer secret"}
    created = client.post(
        "/v2/tasks/",
        json={
            "captchaType": "press",
            "appId": "PXzC5j78di",
            "challengeUrl": "https://iframe.hsprotect.net/challenge",
            "websiteURL": "https://signup.live.com/",
        },
        headers=headers,
    )
    assert created.status_code == 200
    task_id = created.json()["taskId"]
    assert task_id

    final = _wait_status(client, task_id, headers)
    assert final["status"] == "success"
    assert final["response"] == _PASS_TOKENS


def test_v2_failure_reports_error():
    blocked = PxPressResult(
        False, error_kind="px_token_blocked", error="no :1000:", retryable=False
    )
    client = _client(_FakePxSolver(blocked))
    headers = {"Authorization": "Bearer secret"}
    task_id = client.post(
        "/v2/tasks/",
        json={
            "captchaType": "press",
            "appId": "a",
            "challengeUrl": "https://iframe.hsprotect.net/c",
        },
        headers=headers,
    ).json()["taskId"]

    final = _wait_status(client, task_id, headers)
    assert final["status"] == "fail"
    assert "px_token_blocked" in final["error"]


def test_v2_task_id_is_idempotent():
    solver = _FakePxSolver()
    client = _client(solver)
    headers = {"Authorization": "Bearer secret"}
    payload = {
        "captchaType": "press",
        "taskId": "my-task-1",
        "appId": "a",
        "challengeUrl": "https://iframe.hsprotect.net/c",
    }
    first = client.post("/v2/tasks/", json=payload, headers=headers)
    second = client.post("/v2/tasks/", json=payload, headers=headers)
    assert first.json()["taskId"] == "my-task-1"
    assert second.json()["taskId"] == "my-task-1"
    assert len(solver.requests) == 1


def test_v2_invalid_params_rejected_at_creation():
    client = _client()
    response = client.post(
        "/v2/tasks/",
        json={"captchaType": "press"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422


def test_v2_unsupported_type_is_400():
    client = _client()
    response = client.post(
        "/v2/tasks/",
        json={"captchaType": "unknown_v1"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 400


def test_v2_poll_requires_auth():
    client = _client()
    response = client.get("/v2/tasks/whatever")
    assert response.status_code == 401
    assert response.json()["status"] == "fail"


def test_v2_unknown_task_is_404():
    client = _client()
    response = client.get(
        "/v2/tasks/nope", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 404
    assert response.json()["status"] == "fail"


def test_v2_missing_captcha_type_is_422():
    client = _client()
    response = client.post(
        "/v2/tasks/", json={}, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 422


def test_health_counts_finished_tasks():
    client = _client()
    headers = {"Authorization": "Bearer secret"}
    task_id = client.post(
        "/v2/tasks/",
        json={
            "captchaType": "press",
            "appId": "a",
            "challengeUrl": "https://iframe.hsprotect.net/c",
        },
        headers=headers,
    ).json()["taskId"]
    _wait_status(client, task_id, headers)
    body = client.get("/health").json()
    assert body["done_24h"] == 1
    assert body["fail_24h"] == 0


def test_health_counts_v1_solve_outcomes():
    """线上发现的洞：/v1/solve 成败不进计数 → fail_24h 恒 0，看板骗人。"""
    client = _client()
    headers = {"Authorization": "Bearer secret"}
    client.post("/v1/solve", json=_PX_BODY, headers=headers)
    ok = client.get("/health").json()
    assert ok["done_24h"] == 1

    solver = _FakePxSolver(result=PxPressResult(False, error_kind="px_token_timeout"))
    failing = _client(solver)
    failing.post("/v1/solve", json=_PX_BODY, headers=headers)
    bad = failing.get("/health").json()
    assert bad["fail_24h"] == 1


# ────────────────────── geetest_v3 兼容 ──────────────────────


def test_geetest_v3_returns_cookies():
    factory = lambda proxy_url: _FakeGeeTestSolver(cookies={"x5sec": "token"})
    client = _client(geetest_factory=factory)
    response = client.post(
        "/v1/solve",
        json={"type": "geetest_v3", "websiteURL": "https://example.com/login"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"result": "success", "cookies": {"x5sec": "token"}}


def test_geetest_v3_proxy_url_built_and_forwarded():
    seen = {}

    def factory(proxy_url):
        seen["proxy"] = proxy_url
        return _FakeGeeTestSolver()

    client = _client(geetest_factory=factory)
    client.post(
        "/v1/solve",
        json={
            "type": "geetest_v3",
            "websiteURL": "https://example.com/login",
            "proxy": {"host": "1.2.3.4", "port": 8000, "login": "a", "password": "b"},
        },
        headers={"Authorization": "Bearer secret"},
    )
    assert seen["proxy"] == "http://a:b@1.2.3.4:8000"


def test_geetest_v3_requires_website_url():
    client = _client(geetest_factory=lambda proxy: _FakeGeeTestSolver())
    response = client.post(
        "/v1/solve", json={"type": "geetest_v3"}, headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 422


def test_geetest_v3_failure_is_retryable_failure():
    client = _client(geetest_factory=lambda proxy: _FakeGeeTestSolver(success=False))
    body = client.post(
        "/v1/solve",
        json={"type": "geetest_v3", "websiteURL": "https://example.com/login"},
        headers={"Authorization": "Bearer secret"},
    ).json()
    assert body["code"] == 1001
    assert body["data"]["retryable"] is True


def test_geetest_v3_solver_error_is_retryable_failure():
    client = _client(
        geetest_factory=lambda proxy: _FakeGeeTestSolver(exc=RuntimeError("boom"))
    )
    body = client.post(
        "/v1/solve",
        json={"type": "geetest_v3", "websiteURL": "https://example.com/login"},
        headers={"Authorization": "Bearer secret"},
    ).json()
    assert body["code"] == 1001
    assert body["data"]["kind"] == "geetest_solver_error"


# ────────────────────── 线上 review：确定性输入必须 4xx，不能变可重试 ──────────────────────


def test_bare_hostname_website_url_is_422():
    """裸域名在 Chromium 里必失败；必须创建时 422，别让调用方重试到超时。"""
    client = _client()
    response = client.post(
        "/v1/solve",
        json={"type": "px_press", "appId": "PXzC5j78di", "websiteURL": "signup.live.com"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["data"]["retryable"] is False
    assert "websiteURL" in body["error"]


def test_non_http_challenge_url_is_422():
    client = _client()
    response = client.post(
        "/v1/solve",
        json={"type": "px_press", "appId": "PXzC5j78di", "challengeUrl": "ftp://x/y"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    assert "challengeUrl" in response.json()["error"]


def test_invalid_timezone_is_422():
    client = _client()
    response = client.post(
        "/v1/solve",
        json={**_PX_BODY, "timezone": "Not/AZone"},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code == 422
    assert "timezone" in response.json()["error"]


def test_geetest_v3_solver_is_closed_after_task():
    closed = []

    class _Closable(_FakeGeeTestSolver):
        async def close(self):
            closed.append(True)

    client = _client(geetest_factory=lambda proxy: _Closable())
    client.post(
        "/v1/solve",
        json={"type": "geetest_v3", "websiteURL": "https://example.com/login"},
        headers={"Authorization": "Bearer secret"},
    )
    assert closed == [True]


def test_geetest_v3_solver_closed_even_on_failure():
    closed = []

    class _Closable(_FakeGeeTestSolver):
        async def close(self):
            closed.append(True)

    client = _client(geetest_factory=lambda proxy: _Closable(exc=RuntimeError("boom")))
    client.post(
        "/v1/solve",
        json={"type": "geetest_v3", "websiteURL": "https://example.com/login"},
        headers={"Authorization": "Bearer secret"},
    )
    assert closed == [True]


def test_lifespan_shutdown_closes_solver_and_cancels_tasks():
    events = []

    class _Solver(_FakePxSolver):
        async def close(self):
            events.append("solver_closed")

    app = create_app(keys=["secret"], solver=_Solver())
    with TestClient(app) as client:
        assert client.get("/health", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert events == ["solver_closed"]


def test_diagnostics_passthrough_v1_and_v2():
    """需求单 v2 §4.2：成败响应都要带 diagnostics——/v1/solve 与 v2 双路验证。"""
    diag = {"iframe_loaded": True, "captcha_js_loaded": False}

    # v1 失败路径
    failed = PxPressResult(
        False, error_kind="px_token_timeout", error="no pass token",
        retryable=True, diagnostics=diag,
    )
    client = _client(_FakePxSolver(failed))
    body = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    ).json()
    assert body["data"]["diagnostics"] == diag

    # v1 成功路径
    ok = PxPressResult(True, tokens=dict(_PASS_TOKENS), diagnostics=diag)
    client = _client(_FakePxSolver(ok))
    body = client.post(
        "/v1/solve", json=_PX_BODY, headers={"Authorization": "Bearer secret"}
    ).json()
    assert body["code"] == 0
    assert body["data"]["diagnostics"] == diag


def test_v2_fail_response_carries_diagnostics():
    """v2 轮询 fail 信封附 diagnostics（老客户端忽略未知键）。"""
    diag = {"iframe_loaded": False, "captcha_js_loaded": False, "collector_seen": False}
    failed = PxPressResult(
        False, error_kind="px_token_timeout", error="no pass token",
        retryable=True, diagnostics=diag,
    )
    with _client(_FakePxSolver(failed)) as client:
        resp = client.post(
            "/v2/tasks/",
            json=dict(_PX_BODY, taskId="diag-task-1"),
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200
        deadline = time.time() + 10
        body = {}
        while time.time() < deadline:
            poll = client.get(
                "/v2/tasks/diag-task-1",
                headers={"Authorization": "Bearer secret"},
            ).json()
            if poll.get("status") != "processing":
                body = poll
                break
            time.sleep(0.05)
    assert body.get("status") == "fail"
    assert body.get("diagnostics") == diag


def test_load_env_file_parses_and_never_overrides(tmp_path, monkeypatch):
    """supervisor 宿主（无 systemd EnvironmentFile=）的服务端 env 文件加载。

    规则：注释跳过、export 前缀、引号剥离、真实环境变量优先（不覆盖）。
    """
    from slidex.service import app as app_mod

    env_file = tmp_path / "svc.env"
    env_file.write_text(
        "# comment line\n"
        "SLIDEX_TEST_A=plain-value\n"
        "export SLIDEX_TEST_B=\"quoted value\"\n"
        "SLIDEX_TEST_C='single'\n"
        "SLIDEX_TEST_D=  spaced  \n"
        "NOT_A_PAIR_NO_EQUALS\n"
        "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SLIDEX_TEST_A", raising=False)
    monkeypatch.delenv("SLIDEX_TEST_B", raising=False)
    monkeypatch.delenv("SLIDEX_TEST_C", raising=False)
    monkeypatch.delenv("SLIDEX_TEST_D", raising=False)
    monkeypatch.setenv("SLIDEX_TEST_E", "from-real-env")
    env_file.write_text(
        env_file.read_text(encoding="utf-8") + "SLIDEX_TEST_E=from-file\n",
        encoding="utf-8",
    )

    app_mod._load_env_file(str(env_file))

    assert os.environ["SLIDEX_TEST_A"] == "plain-value"
    assert os.environ["SLIDEX_TEST_B"] == "quoted value"
    assert os.environ["SLIDEX_TEST_C"] == "single"
    assert os.environ["SLIDEX_TEST_D"] == "spaced"
    assert os.environ["SLIDEX_TEST_E"] == "from-real-env"  # 不覆盖


def test_load_env_file_missing_is_silent(tmp_path):
    from slidex.service import app as app_mod

    # 不存在 → 静默返回，不抛错（尽力而为）
    app_mod._load_env_file(str(tmp_path / "nope.env"))


async def test_cancel_all_is_atomic_against_concurrent_create():
    """cancel 与清表必须原子（0.9.5 F3）：窗口内 create 的新任务不能被抹掉。

    变异验证：把 cancel_all 里的「list→cancel→clear」改回无锁三步后本测试
    不稳定失败——新任务仍在跑但 GET 变 404（孤儿任务）。
    """
    import asyncio

    from slidex.service.app import TaskStore

    store = TaskStore()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_runner(payload):
        started.set()
        await release.wait()
        return {"ok": True}

    # 第一个任务进 _running
    await store.create("t1", "px_press", slow_runner, {})
    await asyncio.wait_for(started.wait(), timeout=5)

    # cancel_all 进行中，并发塞入第二个任务：cancel+clear 原子化后，
    # 它只可能 (a) 在 cancel 前入表→被正常取消，或 (b) 在 clear 后入表→
    # 完整跑完且 GET 仍可见。绝不允许「在跑却 404」。
    cancel_task = asyncio.ensure_future(store.cancel_all())

    # 让出事件循环，使 cancel_all 先拿到锁并进入等待区
    await asyncio.sleep(0)
    second = asyncio.ensure_future(
        store.create("t2", "px_press", slow_runner, {})
    )
    await asyncio.wait_for(second, timeout=5)

    release.set()
    await asyncio.wait_for(cancel_task, timeout=5)

    # t2 无论处于哪个时序分支，都不该「表里查无但任务还在跑」
    record = await store.get_owned("t2", None)
    running_now = store._running.get("t2")
    assert not (record is None and running_now is not None), (
        "orphan task: running but invisible via GET"
    )
    if record is not None:
        assert store._running.get("t2") is None or record.status in {
            "processing", "success",
        }


def test_v2_task_owner_isolation_between_keys():
    """多 key 部署点：A 创建的任务，B 的 key 轮询必须 404（0.9.5 F7）。

    变异验证：把 get_task 里的 get_owned 换回无条件 get 后本测试失败
    （B 拿到 A 的任务结果）。开放模式（无 key 配置）不受影响。
    """
    import hashlib

    class _BlockingSolver:
        def __init__(self):
            self.release = None

        async def solve(self, request):
            import asyncio
            await asyncio.Event().wait()  # 一直挂着，任务停 processing

    solver = _BlockingSolver()
    client = _client(solver, keys=("key-a", "key-b"))

    created = client.post(
        "/v2/tasks/",
        headers={"Authorization": "Bearer key-a"},
        json={"type": "px_press", "appId": "PXzC5j78di", "websiteURL": "https://x.example/"},
    )
    assert created.status_code == 200
    task_id = created.json()["taskId"]

    # 创建者自己能查到
    mine = client.get(
        f"/v2/tasks/{task_id}", headers={"Authorization": "Bearer key-a"}
    )
    assert mine.status_code == 200
    assert mine.json()["status"] == "processing"

    # 别的 key 查同一个 id → 404，形状保持 v2 fail
    theirs = client.get(
        f"/v2/tasks/{task_id}", headers={"Authorization": "Bearer key-b"}
    )
    assert theirs.status_code == 404
    assert theirs.json() == {"status": "fail", "error": "task not found"}

    # 无 key → 401（不是 404，鉴权语义不混淆）
    noauth = client.get(f"/v2/tasks/{task_id}")
    assert noauth.status_code == 401


def test_v2_task_open_mode_has_no_owner():
    """开放模式（无 key）下创建的任务任何请求都可查——过渡期不误伤。"""
    from fastapi.testclient import TestClient as _TC

    class _FastSolver:
        async def solve(self, request):
            return {"px3": "1:a:1000:x::"}

    app = create_app(solver=_FastSolver(), keys=[])
    client = _TC(app)
    created = client.post(
        "/v2/tasks/", json={"type": "px_press", "appId": "PX", "websiteURL": "https://x.example/"}
    )
    task_id = created.json()["taskId"]
    got = client.get(f"/v2/tasks/{task_id}")  # 无 header
    assert got.status_code == 200
