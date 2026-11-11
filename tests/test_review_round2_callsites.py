"""Call sites must not report a success that the gate refused."""

import types

import pytest
from loguru import logger

from slidex.solver import SliderSolver


class _FakePage:
    def __init__(self, raw):
        self._raw = raw

    async def evaluate(self, _script):
        return self._raw


def _solver_with(page, pool):
    """Minimal stand-in exposing exactly what the export method touches."""
    solver = types.SimpleNamespace(page=page, pure_user_id="user", _trajectory_pool=pool)
    solver._export_human_drag_recording = types.MethodType(
        SliderSolver._export_human_drag_recording, solver
    )
    return solver


@pytest.mark.asyncio
async def test_refused_recording_logs_no_success(tmp_path):
    from slidex._trajectory_pool import SliderTrajectoryPool

    pool = SliderTrajectoryPool(base_dir=str(tmp_path))
    page = _FakePage({
        "done": True,
        "events": [
            {"dt": 0, "x": 100, "y": 100},
            {"dt": 20, "x": 120, "y": 100},
            {"dt": 40, "x": 150, "y": 100},
            {"dt": 60, "x": 200, "y": 100},
            {"dt": 80, "x": 260, "y": 100},
        ],
    })
    solver = _solver_with(page, pool)

    messages = []
    sink_id = logger.add(lambda m: messages.append(str(m)), level="INFO")
    try:
        await solver._export_human_drag_recording("https://example.com")
    finally:
        logger.remove(sink_id)

    assert not any("trajectory saved to pool" in m for m in messages)
    assert any("NOT pooled" in m for m in messages)
    assert not list((tmp_path / "user").glob("trajectory_*.json"))


# ── HTTP boundary ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejection_detail_carries_reason_code(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from slidex import api
    from slidex.api import TrajectorySubmitRequest

    monkeypatch.setattr(api.trajectory_pool, "base_dir", tmp_path)
    api.captcha_controller.active_sessions.clear()
    api.captcha_controller.active_sessions["s3"] = {"token": "secret"}

    request = TrajectorySubmitRequest(
        session_id="s3",
        cookie_id="owner",
        points=[[0.0, 0, 10], [150.0, 0, 20], [20.0, 0, 50]],
        distance=20,
    )
    with pytest.raises(HTTPException) as exc:
        await api.submit_trajectory(request, x_captcha_token="secret")
    assert exc.value.status_code == 400
    assert "overshoot" in exc.value.detail or "non_monotonic" in exc.value.detail


@pytest.mark.asyncio
async def test_500_detail_does_not_leak_exception_text(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from slidex import api
    from slidex.api import TrajectorySubmitRequest

    api.captcha_controller.active_sessions.clear()
    api.captcha_controller.active_sessions["s4"] = {"token": "secret"}

    def _boom(*_a, **_k):
        raise OSError("/etc/slidex-captcha.env: permission denied")

    monkeypatch.setattr(api.trajectory_pool, "save_trajectory", _boom)
    request = TrajectorySubmitRequest(
        session_id="s4", cookie_id="owner",
        points=[[0.0, 0, 10], [1.0, 0, 10], [2.0, 0, 10]], distance=2,
    )
    with pytest.raises(HTTPException) as exc:
        await api.submit_trajectory(request, x_captcha_token="secret")
    assert exc.value.status_code == 500
    assert "captcha.env" not in str(exc.value.detail)
