"""Regression tests for kill_chromium_process_tree (orphan Chromium cleanup)."""

from unittest.mock import MagicMock, patch

import psutil
import pytest

from slidex import _chromium_lifecycle


def _make_proc(pid, name="chrome.exe", children=None):
    proc = MagicMock()
    proc.pid = pid
    proc.name.return_value = name
    proc.children.return_value = children or []
    proc.kill.return_value = None
    return proc


class TestKillChromiumProcessTree:
    def test_kills_process_and_children(self):
        child = _make_proc(200)
        parent = _make_proc(100, children=[child])

        with patch.object(psutil, "Process", return_value=parent):
            killed = _chromium_lifecycle.kill_chromium_process_tree(100)

        assert killed == 2
        child.kill.assert_called_once()
        parent.kill.assert_called_once()

    def test_missing_process_returns_zero(self):
        with patch.object(psutil, "Process", side_effect=psutil.NoSuchProcess(999)):
            assert _chromium_lifecycle.kill_chromium_process_tree(999) == 0

    def test_children_enumeration_failure_still_kills_parent(self):
        parent = _make_proc(100)
        parent.children.side_effect = psutil.AccessDenied(100)

        with patch.object(psutil, "Process", return_value=parent):
            killed = _chromium_lifecycle.kill_chromium_process_tree(100)

        assert killed == 1
        parent.kill.assert_called_once()

    def test_child_kill_failure_does_not_block_parent_kill(self):
        bad_child = _make_proc(200)
        bad_child.kill.side_effect = psutil.AccessDenied(200)
        parent = _make_proc(100, children=[bad_child])

        with patch.object(psutil, "Process", return_value=parent):
            killed = _chromium_lifecycle.kill_chromium_process_tree(100)

        assert killed == 1
        parent.kill.assert_called_once()

    def test_zombie_parent_returns_zero(self):
        with patch.object(psutil, "Process", side_effect=psutil.ZombieProcess(100)):
            assert _chromium_lifecycle.kill_chromium_process_tree(100) == 0

    def test_non_chromium_root_skipped_pid_reuse_guard(self):
        """quit 后 PID 被回收复用为无关进程时不得误杀。"""
        proc = _make_proc(100, name="python3")

        with patch.object(psutil, "Process", return_value=proc):
            assert _chromium_lifecycle.kill_chromium_process_tree(100) == 0

        proc.kill.assert_not_called()

    def test_exported_in_module_all(self):
        assert "kill_chromium_process_tree" in _chromium_lifecycle.__all__
