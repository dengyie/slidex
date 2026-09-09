"""WebSocket 首条消息鉴权的行为测试。

覆盖 P2 修复：首条消息非 JSON / 二进制 / 超时 / 非 auth 形状 / 错误 token
都必须走 1008 协议关闭，而不是在公开端点上制造未处理异常；正确 token 才注册
连接并下发 session_info。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import slidex.api as api
from slidex.api import router, captcha_controller

app = FastAPI()
app.include_router(router)


@pytest.fixture(autouse=True)
def _clean_controller_state():
    captcha_controller.active_sessions.clear()
    captcha_controller.control_tickets.clear()
    captcha_controller.websocket_connections.clear()
    yield
    captcha_controller.active_sessions.clear()
    captcha_controller.control_tickets.clear()
    captcha_controller.websocket_connections.clear()


@pytest.fixture
def ws_client():
    with TestClient(app) as client:
        yield client


def _expect_policy_close(ws_client, url, send):
    with pytest.raises(WebSocketDisconnect) as exc, ws_client.websocket_connect(url) as ws:
        send(ws)
        # 服务端可能先回一条 error 消息再关闭；读到断连为止
        while True:
            ws.receive_json()
    assert exc.value.code == 1008


def test_ws_rejects_non_json_first_message(ws_client):
    def send(ws):
        ws.send_text("this is not json")

    _expect_policy_close(ws_client, "/api/captcha/ws/s1", send)


def test_ws_rejects_binary_first_message(ws_client):
    def send(ws):
        ws.send_bytes(b"\x00\x01 not-json")

    _expect_policy_close(ws_client, "/api/captcha/ws/s1", send)


def test_ws_rejects_non_auth_json(ws_client):
    def send(ws):
        ws.send_json({"type": "hello"})

    _expect_policy_close(ws_client, "/api/captcha/ws/s1", send)


def test_ws_rejects_wrong_token(ws_client):
    captcha_controller.active_sessions["s1"] = {"token": "right"}

    def send(ws):
        ws.send_json({"type": "auth", "token": "wrong"})

    _expect_policy_close(ws_client, "/api/captcha/ws/s1", send)


def test_ws_auth_timeout_closes(ws_client, monkeypatch):
    monkeypatch.setattr(api, "_WS_AUTH_TIMEOUT", 0.2)

    def send(ws):
        pass  # 什么都不发，等待超时

    _expect_policy_close(ws_client, "/api/captcha/ws/s1", send)


def test_ws_accepts_valid_token(ws_client):
    captcha_controller.active_sessions["s1"] = {
        "token": "right",
        "screenshot": "img",
        "captcha_info": {"kind": "slider"},
        "viewport": {"w": 1, "h": 1},
        "challenge_type": "slider_captcha",
        "audit": [],
    }

    with ws_client.websocket_connect("/api/captcha/ws/s1") as ws:
        ws.send_json({"type": "auth", "token": "right"})
        msg = ws.receive_json()

    assert msg["type"] == "session_info"
    assert captcha_controller.websocket_connections.get("s1") is not None
