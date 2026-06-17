import pytest
from fastapi import HTTPException
from pathlib import Path

from slidex.remote import captcha_controller
from slidex import api
from slidex.api import TrajectorySubmitRequest, _verify_session_or_404


def test_verify_session_requires_token():
    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions["s1"] = {"token": "secret"}

    with pytest.raises(HTTPException) as exc_info:
        _verify_session_or_404("s1", None)

    assert exc_info.value.status_code == 403


def test_verify_session_rejects_wrong_token():
    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions["s1"] = {"token": "secret"}

    with pytest.raises(HTTPException) as exc_info:
        _verify_session_or_404("s1", "wrong")

    assert exc_info.value.status_code == 403


def test_verify_session_accepts_correct_token():
    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions["s1"] = {"token": "secret"}

    _verify_session_or_404("s1", "secret")


@pytest.mark.asyncio
async def test_session_info_exposes_visual_challenge_audit_metadata():
    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions["s1"] = {
        "token": "secret",
        "screenshot": "image",
        "captcha_info": {"selector": "#captcha"},
        "viewport": {"width": 100, "height": 100},
        "challenge_type": "ocr_text",
        "audit": [{"event": "session_created", "metadata": {"challenge_type": "ocr_text"}}],
        "completed": False,
    }

    payload = await api.get_session_info("s1", x_captcha_token="secret")

    assert payload["challenge_type"] == "ocr_text"
    assert payload["audit"][0]["event"] == "session_created"
    assert "token" not in payload


def test_control_page_does_not_log_raw_token():
    html = Path("slidex/_html/captcha_control.html").read_text(encoding="utf-8")

    assert "token=<redacted>" in html
    assert "log(`WebSocket URL: ${wsUrl}`" not in html


@pytest.mark.asyncio
async def test_control_page_escapes_initial_session_script_values():
    session_id = "s1</script><script>alert(1)</script>"
    token = "tok</script>"
    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions[session_id] = {"token": token}

    response = await api.captcha_control_page_with_session(session_id, token=token)
    body = response.body.decode("utf-8")

    assert session_id not in body
    assert token not in body
    assert "s1<\\/script><script>alert(1)<\\/script>" in body
    assert "tok<\\/script>" in body


@pytest.mark.asyncio
async def test_trajectory_uses_session_cookie_id_not_request_cookie(monkeypatch, tmp_path):
    pool = api.trajectory_pool
    monkeypatch.setattr(pool, "base_dir", tmp_path)

    captcha_controller.active_sessions.clear()
    captcha_controller.active_sessions["s1"] = {
        "token": "secret",
        "cookie_id": "owner-user",
    }

    request = TrajectorySubmitRequest(
        session_id="s1",
        cookie_id="../../evil",
        points=[[0, 0, 100], [10, 0, 20], [20, 0, 50]],
        distance=20,
    )

    result = await api.submit_trajectory(request, x_captcha_token="secret")

    assert result["cookie_id"] == "owner-user"
    assert (tmp_path / "owner-user").is_dir()
    assert not (tmp_path.parent / "evil").exists()
