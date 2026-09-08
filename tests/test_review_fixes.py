"""Regression tests for the 2026-09-06 production review fixes.

- CDP recorded-trajectory replay must dispatch press before drag moves
- Chromium cleanup must be scoped to the solver's own user_data_dir
- Trajectory pool load must degrade gracefully when last_used write fails
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from slidex._chromium_lifecycle import (
    ensure_profile_chromium_closed,
    find_chromium_pids_by_user_data_dir,
)
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex.config import SlidexConfig
from slidex.solver import SliderSolver


class _FakeCdp:
    def __init__(self):
        self.sent = []

    async def send(self, method, params):
        self.sent.append(params)


class _FakePage:
    async def evaluate(self, script):
        return {"timeOrigin": 1000, "now": 500.0}


def _make_solver(tmp_path: Path) -> SliderSolver:
    cfg = SlidexConfig(
        trajectory_pool_base_dir=str(tmp_path / "traj"),
        browser_data_dir=str(tmp_path / "browser_data"),
        calibration_dir=str(tmp_path / "calibration"),
        telemetry_enabled=False,
    )
    return SliderSolver(cookie_id="replay_user", config=cfg)


class TestCdpReplayOrder:
    def test_press_before_drag_moves_and_release_at_recorded_end(self, tmp_path):
        s = _make_solver(tmp_path)
        s.page = _FakePage()
        s._cdp = _FakeCdp()
        # 首点 = 起始驻留，中段 = 拖拽移动，末点 = 录制自 up 事件的释放位
        points = [[0, 0, 20], [10, 1, 10], [60, 2, 10], [120, 0, 10]]

        ok = asyncio.run(s._replay_recorded_cdp(points, 500.0, 300.0))

        assert ok is True
        types = [p["type"] for p in s._cdp.sent]
        assert types == [
            "mouseMoved",   # hover 到起点
            "mousePressed",
            "mouseMoved",
            "mouseMoved",
            "mouseReleased",
        ]
        # 拖拽 move 的 movement 基于按下位置
        first_drag_move = s._cdp.sent[2]
        assert first_drag_move["movementX"] == pytest.approx(10)
        # 释放位置 = 起点 + 末点位移（录制的 up 位置）
        release = s._cdp.sent[-1]
        assert release["x"] == pytest.approx(620.0)
        assert release["y"] == pytest.approx(300.0)
        # released 晚于 pressed
        assert release["timestamp"] > s._cdp.sent[1]["timestamp"]

    def test_empty_points_returns_false_without_dispatch(self, tmp_path):
        s = _make_solver(tmp_path)
        s.page = _FakePage()
        s._cdp = _FakeCdp()

        ok = asyncio.run(s._replay_recorded_cdp([], 500.0, 300.0))

        assert ok is False
        assert s._cdp.sent == []

    def test_no_cdp_returns_false(self, tmp_path):
        s = _make_solver(tmp_path)
        s.page = _FakePage()
        s._cdp = None

        ok = asyncio.run(s._replay_recorded_cdp([[0, 0, 20]], 500.0, 300.0))

        assert ok is False


def _fake_proc(pid, name, cmdline):
    p = mock.MagicMock()
    p.info = {"pid": pid, "name": name, "cmdline": cmdline}
    return p


class TestProfileScopedChromiumCleanup:
    @mock.patch("slidex._chromium_lifecycle.kill_chromium_by_pid")
    @mock.patch("slidex._chromium_lifecycle.psutil.process_iter")
    def test_only_kills_same_profile_processes(self, mock_iter, mock_kill, tmp_path):
        target = str(tmp_path / "slider_a")
        other = str(tmp_path / "slider_b")
        mock_iter.return_value = [
            _fake_proc(101, "chrome.exe", ["chrome", f"--user-data-dir={target}"]),
            _fake_proc(102, "chrome.exe", ["chrome", f"--user-data-dir={other}"]),
            _fake_proc(103, "chrome.exe", ["chrome", "--headless"]),
        ]

        killed = asyncio.run(ensure_profile_chromium_closed(target))

        assert killed == 1
        mock_kill.assert_called_once_with(101)

    @mock.patch("slidex._chromium_lifecycle.psutil.process_iter")
    def test_find_pids_returns_all_matches(self, mock_iter, tmp_path):
        target = str(tmp_path / "slider_a")
        mock_iter.return_value = [
            _fake_proc(201, "chrome", ["chrome", f"--user-data-dir={target}", "--headless"]),
            _fake_proc(202, "chrome", ["chrome", f"--user-data-dir={target}"]),
        ]

        assert find_chromium_pids_by_user_data_dir(target) == [201, 202]

    @mock.patch("slidex._chromium_lifecycle.psutil.process_iter")
    def test_no_match_returns_empty(self, mock_iter, tmp_path):
        mock_iter.return_value = [
            _fake_proc(301, "chrome", ["chrome", "--other-arg"]),
        ]

        assert find_chromium_pids_by_user_data_dir(str(tmp_path / "none")) == []

    def test_solver_module_wires_profile_scoped_cleanup(self):
        import slidex.solver as solver_mod

        assert solver_mod.ensure_profile_chromium_closed is ensure_profile_chromium_closed


class TestTrajectoryPoolDegradation:
    def test_load_best_trajectory_survives_last_used_write_failure(self, tmp_path, monkeypatch):
        pool = SliderTrajectoryPool(base_dir=str(tmp_path))
        pool.save_trajectory(
            [[0, 0, 120], [120, 0, 80]], "u1", 120.0, True, "https://example.test"
        )
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        monkeypatch.setattr(
            SliderTrajectoryPool, "_last_used_path", lambda self, cid: blocked
        )

        best = pool.load_best_trajectory("u1", 120.0)

        assert best is not None
        assert best["distance"] == pytest.approx(120.0)


class TestCalibrationGuard:
    def _solver(self, tmp_path: Path) -> SliderSolver:
        cfg = SlidexConfig(
            trajectory_pool_base_dir=str(tmp_path / "traj"),
            browser_data_dir=str(tmp_path / "browser_data"),
            calibration_dir=str(tmp_path / "calibration"),
            telemetry_enabled=False,
        )
        return SliderSolver(cookie_id="cal_user", config=cfg)

    def test_load_resets_poisoned_offset_to_default(self, tmp_path):
        cfg = SlidexConfig(
            trajectory_pool_base_dir=str(tmp_path / "traj"),
            browser_data_dir=str(tmp_path / "browser_data"),
            calibration_dir=str(tmp_path / "calibration"),
            telemetry_enabled=False,
        )
        cal_dir = tmp_path / "calibration" / "cal_user"
        cal_dir.mkdir(parents=True)
        (cal_dir / "calibration.json").write_text(
            json.dumps({"offset_correction": 250}), encoding="utf-8"
        )

        s = SliderSolver(cookie_id="cal_user", config=cfg)

        assert s._calibration["offset_correction"] == SliderSolver.OFFSET_CORRECTION_DEFAULT

    def test_single_mismatch_does_not_persist(self, tmp_path):
        s = self._solver(tmp_path)
        default_offset = s._calibration["offset_correction"]

        s._register_offset_mismatch(400.0, 200.0, 2.0)  # 候选 +200，越界且未确认

        assert s._calibration["offset_correction"] == default_offset
        assert not s._calibration_path().exists()

    def test_two_consecutive_agreements_persist_within_band(self, tmp_path):
        s = self._solver(tmp_path)

        s._register_offset_mismatch(280.0, 200.0, 1.4)  # 候选 +80
        assert not s._calibration_path().exists()
        s._register_offset_mismatch(283.0, 200.0, 1.415)  # 候选 +83，与前次差 3 ≤ 5

        assert s._calibration["offset_correction"] == 83
        assert json.loads(s._calibration_path().read_text())["offset_correction"] == 83

    def test_diverging_candidates_never_persist(self, tmp_path):
        s = self._solver(tmp_path)

        s._register_offset_mismatch(280.0, 200.0, 1.4)   # 候选 +80
        s._register_offset_mismatch(240.0, 200.0, 1.2)   # 候选 +40，与前次差 40
        s._register_offset_mismatch(360.0, 200.0, 1.8)   # 候选 +160 → clamp 100，与前次差 60

        assert not s._calibration_path().exists()
        assert s._calibration["offset_correction"] == SliderSolver.OFFSET_CORRECTION_DEFAULT


class TestTelemetrySummaryArtifact:
    def test_finalize_writes_per_run_summary_file(self, tmp_path):
        cfg = SlidexConfig(
            trajectory_pool_base_dir=str(tmp_path / "traj"),
            browser_data_dir=str(tmp_path / "browser_data"),
            calibration_dir=str(tmp_path / "calibration"),
            telemetry_enabled=True,
            telemetry_dir=str(tmp_path / "telemetry"),
        )
        s = SliderSolver(cookie_id="tel_user", config=cfg)

        s._finalize_telemetry(success=True, status="success", cookies={"a": "1"})

        summary_path = tmp_path / "telemetry" / f"{s._telemetry_run_id}.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        assert data["run_id"] == s._telemetry_run_id
        assert data["success"] is True
        assert data["cookie_count"] == 1


class TestApiTrajectoryPoolWiring:
    def test_api_pool_follows_env_config(self, monkeypatch, tmp_path):
        import importlib

        import slidex.api as api_mod

        monkeypatch.setenv("SLIDEX_TRAJ_POOL_DIR", str(tmp_path))
        try:
            importlib.reload(api_mod)
            assert Path(api_mod.trajectory_pool.base_dir).resolve() == tmp_path.resolve()
        finally:
            monkeypatch.delenv("SLIDEX_TRAJ_POOL_DIR", raising=False)
            importlib.reload(api_mod)


class TestProfileLock:
    async def test_second_solve_on_same_profile_times_out_without_browser(
        self, tmp_path, monkeypatch
    ):
        s1 = _make_solver(tmp_path)
        s2 = _make_solver(tmp_path)  # 同 cookie_id + 同 browser_data_dir → 同 profile
        s2._config.wait_timeout = 1

        lock = SliderSolver._get_profile_lock(str(s1.profile_dir))
        await lock.acquire()  # 模拟 s1 的求解占住 profile

        async def boom():
            raise AssertionError("second solve must not launch a browser")

        monkeypatch.setattr(s2, "_init_browser", boom)

        try:
            ok, cookies = await s2.solve("https://example.test/verify")
            assert ok is False and cookies is None
            assert s2.get_telemetry_summary()["status"] == "profile_lock_timeout"
        finally:
            lock.release()

    async def test_same_profile_solves_serialize(self, tmp_path, monkeypatch):
        s1 = _make_solver(tmp_path)
        s2 = _make_solver(tmp_path)
        order = []
        active = {"n": 0}

        def _wire(s, tag, hold):
            async def init():
                active["n"] += 1
                assert active["n"] == 1, "two solves overlapped on the same profile"
                order.append(f"{tag}:init")
                await asyncio.sleep(hold)
                active["n"] -= 1

            async def noop(*args, **kwargs):
                pass

            async def loop(url):
                order.append(f"{tag}:loop")
                return True, {"k": "v"}

            monkeypatch.setattr(s, "_init_browser", init)
            monkeypatch.setattr(s, "_load_page", noop)
            monkeypatch.setattr(s, "_run_solve_loop", loop)
            monkeypatch.setattr(s, "_close", noop)

        _wire(s1, "s1", 0.2)
        _wire(s2, "s2", 0.0)
        s2._config.wait_timeout = 5

        (r1, r2) = await asyncio.gather(s1.solve("u"), s2.solve("u"))

        assert r1[0] is True and r2[0] is True
        assert order.index("s1:init") < order.index("s2:init")
