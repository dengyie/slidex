"""Tests for slidex.solver — SliderSolver class and module-level helpers."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from slidex.config import SlidexConfig
from slidex.solver import (
    SliderSolver,
    _get_pid_lock,
    _kill_chromium_by_pid,
    _find_chromium_pid_by_user_data_dir,
    _ensure_previous_chromium_closed,
    _record_chromium_pid,
)


# ════════════════════════════════════════════════════════════
# Constructor & config
# ════════════════════════════════════════════════════════════

class TestSliderSolverInit:
    def test_default_config(self):
        s = SliderSolver()
        assert isinstance(s._config, SlidexConfig)
        assert s._config.max_concurrent == 3

    def test_injected_config(self):
        cfg = SlidexConfig(max_concurrent=5, wait_timeout=30)
        s = SliderSolver(config=cfg)
        assert s._config is cfg
        assert s._config.max_concurrent == 5

    def test_pure_user_id_sanitizes_path_traversal(self):
        s = SliderSolver(cookie_id="../../etc/passwd")
        assert ".." not in s.pure_user_id
        assert "/" not in s.pure_user_id

    def test_pure_user_id_keeps_valid_chars(self):
        s = SliderSolver(cookie_id="user_123_test")
        assert s.pure_user_id == "user"

    def test_pure_user_id_no_underscore(self):
        s = SliderSolver(cookie_id="simple_user_id")
        assert s.pure_user_id == "simple"

    def test_pure_user_id_only_special_chars(self):
        s = SliderSolver(cookie_id="../../../")
        assert s.pure_user_id == "default"

    def test_notification_callback_stored(self):
        called_with = []
        def cb(cookie_id, msg, title):
            called_with.append((cookie_id, msg, title))
        s = SliderSolver(cookie_id="u1", notification_callback=cb)
        assert s._notification_callback is cb


# ════════════════════════════════════════════════════════════
# Calibration paths
# ════════════════════════════════════════════════════════════

class TestCalibration:
    def test_calibration_path_uses_config_dir(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(calibration_dir=td)
            s = SliderSolver(cookie_id="testuser", config=cfg)
            expected = Path(td) / "testuser" / "calibration.json"
            assert s._calibration_path() == expected

    def test_calibration_path_default_dir(self):
        s = SliderSolver(cookie_id="testuser")
        p = s._calibration_path()
        assert ".slidex" in str(p) or "calibration" in str(p)
        assert p.name == "calibration.json"

    def test_load_calibration_returns_default_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(calibration_dir=td)
            s = SliderSolver(cookie_id="nonexistent", config=cfg)
            cal = s._load_calibration()
            assert cal == {"offset_correction": -35}

    def test_save_and_load_calibration_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(calibration_dir=td)
            s = SliderSolver(cookie_id="roundtrip_user", config=cfg)
            s._calibration = {"offset_correction": -42, "custom": True}
            s._save_calibration()

            s2 = SliderSolver(cookie_id="roundtrip_user", config=cfg)
            loaded = s2._load_calibration()
            assert loaded["offset_correction"] == -42
            assert loaded["custom"] is True


# ════════════════════════════════════════════════════════════
# PID tracking (module-level helpers)
# ════════════════════════════════════════════════════════════

class TestPIDTracking:
    def test_get_pid_lock_returns_same_instance(self):
        lock1 = _get_pid_lock()
        lock2 = _get_pid_lock()
        assert lock1 is lock2

    @mock.patch("slidex.solver.psutil")
    def test_kill_chromium_by_pid_not_running(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = False
        mock_psutil.Process.return_value = mock_proc

        result = _kill_chromium_by_pid(12345)
        assert result is False
        mock_proc.terminate.assert_not_called()

    @mock.patch("slidex.solver.psutil")
    def test_kill_chromium_by_pid_wrong_process_name(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.name.return_value = "python"
        mock_psutil.Process.return_value = mock_proc

        result = _kill_chromium_by_pid(12345)
        assert result is False

    @mock.patch("slidex.solver.psutil")
    def test_kill_chromium_by_pid_terminates_and_kills(self, mock_psutil):
        _Exc = type("_Exc", (Exception,), {})
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.name.return_value = "chromium"
        mock_proc.wait.side_effect = [_Exc("timeout"), None]
        mock_psutil.Process.return_value = mock_proc
        mock_psutil.TimeoutExpired = _Exc
        mock_psutil.NoSuchProcess = _Exc
        mock_psutil.AccessDenied = _Exc

        result = _kill_chromium_by_pid(12345)
        assert result is True
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    @mock.patch("slidex.solver.psutil")
    def test_kill_chromium_no_such_process(self, mock_psutil):
        mock_psutil.Process.side_effect = mock_psutil.NoSuchProcess("gone")
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        result = _kill_chromium_by_pid(12345)
        assert result is False

    @mock.patch("slidex.solver.psutil")
    def test_find_chromium_by_user_data_dir(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.info = {
            "pid": 9999,
            "name": "chromium",
            "cmdline": ["/usr/bin/chromium", "--user-data-dir=/tmp/profile_abc"],
        }
        mock_psutil.process_iter.return_value = [mock_proc]

        pid = _find_chromium_pid_by_user_data_dir("/tmp/profile_abc")
        assert pid == 9999

    @mock.patch("slidex.solver.psutil")
    def test_find_chromium_not_found(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.info = {
            "pid": 9999,
            "name": "chromium",
            "cmdline": ["/usr/bin/chromium", "--other-arg"],
        }
        mock_psutil.process_iter.return_value = [mock_proc]

        pid = _find_chromium_pid_by_user_data_dir("/tmp/nonexistent")
        assert pid is None

    @mock.patch("slidex.solver._kill_chromium_by_pid")
    def test_ensure_previous_chromium_closed_calls_kill(self, mock_kill):
        _record_chromium_pid(42)
        asyncio.run(_ensure_previous_chromium_closed())
        mock_kill.assert_called_once_with(42)

    @mock.patch("slidex.solver._kill_chromium_by_pid")
    def test_ensure_previous_chromium_closed_no_pid(self, mock_kill):
        asyncio.run(_ensure_previous_chromium_closed())
        mock_kill.assert_not_called()


# ════════════════════════════════════════════════════════════
# Trajectory pool integration
# ════════════════════════════════════════════════════════════

class TestTrajectoryPoolIntegration:
    def test_trajectory_pool_uses_config_dir(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(trajectory_pool_base_dir=td)
            s = SliderSolver(cookie_id="pool_user", config=cfg)
            assert s._trajectory_pool.base_dir == Path(td)

    def test_trajectory_pool_default_dir(self):
        s = SliderSolver(cookie_id="pool_user")
        assert ".slidex" in str(s._trajectory_pool.base_dir)


# ════════════════════════════════════════════════════════════
# Browser data dir
# ════════════════════════════════════════════════════════════

class TestBrowserDataDir:
    def test_profile_dir_uses_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(browser_data_dir=td)
            s = SliderSolver(cookie_id="bduser", config=cfg)
            assert str(s.profile_dir).startswith(td)
            assert "slider_bduser" in str(s.profile_dir)

    def test_profile_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = SlidexConfig(browser_data_dir=td)
            s = SliderSolver(cookie_id="bduser", config=cfg)
            assert s.profile_dir.exists()
