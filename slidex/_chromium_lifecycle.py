"""Chromium process singleton tracking for clean restarts"""

import os
import threading
import psutil
from loguru import logger


CHROMIUM_NAMES = {"chromium", "chrome", "chromium-browser", "google-chrome"}


def _is_chromium_name(name):
    # Windows 上进程名带 .exe 后缀（如 chrome.exe），归一后再匹配
    return (name or "").lower().removesuffix(".exe") in CHROMIUM_NAMES

_last_chromium_pid = None
_pid_lock = threading.Lock()


def get_pid_lock():
    """Get the process ID lock for thread-safe operations"""
    return _pid_lock


def kill_chromium_by_pid(pid):
    """
    Kill a Chromium process by PID.

    Args:
        pid: Process ID to kill

    Returns:
        True if process was killed, False otherwise
    """
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False

        # 严格匹配 Chromium 进程名
        if not _is_chromium_name(proc.name()):
            logger.debug(f"[slider] PID={pid} name={proc.name()} is not a Chromium process")
            return False

        logger.info(f"[slider] Killing previous Chromium PID={pid}")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        logger.info(f"[slider] Previous Chromium PID={pid} killed")
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    except Exception as e:
        logger.warning(f"[slider] Kill Chromium PID={pid} failed: {e}")
        return False


def record_chromium_pid(pid):
    """
    Record the current Chromium PID for later cleanup.

    Args:
        pid: Process ID to record
    """
    global _last_chromium_pid
    with get_pid_lock():
        _last_chromium_pid = pid
    logger.info(f"[slider] Recorded Chromium PID={pid}")


async def ensure_previous_chromium_closed():
    """
    Ensure any previously recorded Chromium process is closed.

    Legacy global-registry cleanup: kills the last recorded PID regardless of
    which solver profile it belongs to. Kept for backward compatibility;
    SliderSolver now uses ensure_profile_chromium_closed() instead so that
    concurrent solvers never kill each other's browser.
    """
    global _last_chromium_pid
    with get_pid_lock():
        pid = _last_chromium_pid

    if pid is not None:
        success = kill_chromium_by_pid(pid)
        if success:
            with get_pid_lock():
                # 只在是同一个 PID 时清空（防止被其他线程覆盖）
                if _last_chromium_pid == pid:
                    _last_chromium_pid = None


def _iter_chromium_pids_for_user_data_dir(normalized_target):
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if not _is_chromium_name(proc.info.get("name")):
                continue

            cmdline = proc.info.get("cmdline") or []
            for arg in cmdline:
                if arg and "--user-data-dir=" in arg:
                    arg_path = arg.split("--user-data-dir=", 1)[1]
                    if os.path.normpath(arg_path) == normalized_target:
                        yield proc.info["pid"]
                        break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def find_chromium_pids_by_user_data_dir(user_data_dir):
    """
    Find all Chromium processes using a specific user data directory.

    Args:
        user_data_dir: Path to the user data directory

    Returns:
        List of matching PIDs
    """
    normalized_target = os.path.normpath(str(user_data_dir))
    try:
        return list(_iter_chromium_pids_for_user_data_dir(normalized_target))
    except Exception:
        return []


def find_chromium_pid_by_user_data_dir(user_data_dir):
    """
    Find a Chromium process using a specific user data directory.

    Args:
        user_data_dir: Path to the user data directory

    Returns:
        Process ID if found, None otherwise
    """
    try:
        for pid in _iter_chromium_pids_for_user_data_dir(
            os.path.normpath(str(user_data_dir))
        ):
            return pid
    except Exception:
        pass
    return None


async def ensure_profile_chromium_closed(user_data_dir):
    """
    Ensure Chromium processes bound to the given user data dir are closed.

    Called before launching a new browser for the same profile (e.g. leftover
    from a crashed run). Scoped to the profile directory, so concurrent
    solvers with different profiles never kill each other's browser.
    """
    pids = find_chromium_pids_by_user_data_dir(user_data_dir)
    killed = 0
    for pid in pids:
        if kill_chromium_by_pid(pid):
            killed += 1
    return killed


__all__ = [
    "get_pid_lock",
    "kill_chromium_by_pid",
    "record_chromium_pid",
    "ensure_previous_chromium_closed",
    "ensure_profile_chromium_closed",
    "find_chromium_pid_by_user_data_dir",
    "find_chromium_pids_by_user_data_dir",
]
