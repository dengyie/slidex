"""Tests for slidex.solver — SliderSolver class and module-level helpers."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from slidex.config import SlidexConfig
from slidex.solver import SliderSolver
from slidex._chromium_lifecycle import (
    get_pid_lock,
    kill_chromium_by_pid,
    find_chromium_pid_by_user_data_dir,
    ensure_previous_chromium_closed,
    record_chromium_pid,
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
    def testget_pid_lock_returns_same_instance(self):
        lock1 = get_pid_lock()
        lock2 = get_pid_lock()
        assert lock1 is lock2

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def testkill_chromium_by_pid_not_running(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = False
        mock_psutil.Process.return_value = mock_proc

        result = kill_chromium_by_pid(12345)
        assert result is False
        mock_proc.terminate.assert_not_called()

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def testkill_chromium_by_pid_wrong_process_name(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.name.return_value = "python"
        mock_psutil.Process.return_value = mock_proc

        result = kill_chromium_by_pid(12345)
        assert result is False

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def testkill_chromium_by_pid_terminates_and_kills(self, mock_psutil):
        _Exc = type("_Exc", (Exception,), {})
        mock_proc = mock.MagicMock()
        mock_proc.is_running.return_value = True
        mock_proc.name.return_value = "chromium"
        mock_proc.wait.side_effect = [_Exc("timeout"), None]
        mock_psutil.Process.return_value = mock_proc
        mock_psutil.TimeoutExpired = _Exc
        mock_psutil.NoSuchProcess = _Exc
        mock_psutil.AccessDenied = _Exc

        result = kill_chromium_by_pid(12345)
        assert result is True
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def test_kill_chromium_no_such_process(self, mock_psutil):
        mock_psutil.Process.side_effect = mock_psutil.NoSuchProcess("gone")
        mock_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        mock_psutil.AccessDenied = type("AccessDenied", (Exception,), {})

        result = kill_chromium_by_pid(12345)
        assert result is False

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def test_find_chromium_by_user_data_dir(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.info = {
            "pid": 9999,
            "name": "chromium",
            "cmdline": ["/usr/bin/chromium", "--user-data-dir=/tmp/profile_abc"],
        }
        mock_psutil.process_iter.return_value = [mock_proc]

        pid = find_chromium_pid_by_user_data_dir("/tmp/profile_abc")
        assert pid == 9999

    @mock.patch("slidex._chromium_lifecycle.psutil")
    def test_find_chromium_not_found(self, mock_psutil):
        mock_proc = mock.MagicMock()
        mock_proc.info = {
            "pid": 9999,
            "name": "chromium",
            "cmdline": ["/usr/bin/chromium", "--other-arg"],
        }
        mock_psutil.process_iter.return_value = [mock_proc]

        pid = find_chromium_pid_by_user_data_dir("/tmp/nonexistent")
        assert pid is None

    @mock.patch("slidex._chromium_lifecycle.kill_chromium_by_pid")
    def testensure_previous_chromium_closed_calls_kill(self, mock_kill):
        record_chromium_pid(42)
        asyncio.run(ensure_previous_chromium_closed())
        mock_kill.assert_called_once_with(42)

    @mock.patch("slidex._chromium_lifecycle.kill_chromium_by_pid")
    def testensure_previous_chromium_closed_no_pid(self, mock_kill):
        asyncio.run(ensure_previous_chromium_closed())
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
            # profile_dir is not created in __init__ anymore (deferred to _init_browser)
            assert not s.profile_dir.exists()
            # but the path is set correctly
            assert "slider_bduser" in str(s.profile_dir)


# ════════════════════════════════════════════════════════════
# Selectors configuration
# ════════════════════════════════════════════════════════════

from slidex.solver import DEFAULT_SELECTORS


class TestSelectors:
    def test_default_selectors_has_all_keys(self):
        required = {
            "slider_btn", "slider_track", "bg_img", "piece_img",
            "track_width", "slider_alt", "result_url_pattern", "success_code",
        }
        assert required == set(DEFAULT_SELECTORS.keys())

    def test_slider_alt_is_tuple(self):
        assert isinstance(DEFAULT_SELECTORS["slider_alt"], tuple)

    def test_result_url_pattern_is_tuple(self):
        assert isinstance(DEFAULT_SELECTORS["result_url_pattern"], tuple)

    def test_constructor_merges_with_defaults(self):
        s = SliderSolver(selectors={"slider_btn": ".my-btn", "success_code": 1})
        assert s.selectors["slider_btn"] == ".my-btn"
        assert s.selectors["success_code"] == 1
        assert s.selectors["slider_track"] == DEFAULT_SELECTORS["slider_track"]

    def test_none_selectors_uses_defaults(self):
        s = SliderSolver(selectors=None)
        assert s.selectors == DEFAULT_SELECTORS

    def test_empty_selectors_uses_defaults(self):
        s = SliderSolver(selectors={})
        assert s.selectors == DEFAULT_SELECTORS


# ════════════════════════════════════════════════════════════
# CDP mode
# ════════════════════════════════════════════════════════════

class TestCDPMode:
    def test_is_cdp_mode_defaults_false(self):
        s = SliderSolver()
        assert s._is_cdp_mode is False

    def test_solve_on_existing_page_method_exists(self):
        import inspect
        assert hasattr(SliderSolver, "solve_on_existing_page")
        sig = inspect.signature(SliderSolver.solve_on_existing_page)
        assert "cdp_endpoint" in sig.parameters
        assert "page_url" in sig.parameters

    def test_close_method_exists(self):
        import inspect
        assert hasattr(SliderSolver, "close")
        method = getattr(SliderSolver, "close")
        assert inspect.iscoroutinefunction(method)

    def test_fallback_or_fail_skips_remote_in_cdp_mode(self):
        s = SliderSolver()
        s._is_cdp_mode = True
        result = asyncio.run(s._fallback_or_fail("https://example.com"))
        assert result == (False, None)


# ════════════════════════════════════════════════════════════
# CLI entry point
# ════════════════════════════════════════════════════════════

class TestCLI:
    def test_import_cli(self):
        from slidex.scripts.slide_solve_cdp import main
        assert callable(main)
