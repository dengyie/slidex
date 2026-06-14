"""Chromium process singleton tracking for clean restarts"""

import threading
import psutil
from loguru import logger


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
        name = (proc.name() or "").lower()
        CHROMIUM_NAMES = {"chromium", "chrome", "chromium-browser", "google-chrome"}
        if name not in CHROMIUM_NAMES:
            logger.debug(f"[slider] PID={pid} name={name} is not a Chromium process")
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

    This is called before launching a new browser to prevent
    multiple Chromium instances from accumulating.
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


def find_chromium_pid_by_user_data_dir(user_data_dir):
    """
    Find a Chromium process using a specific user data directory.

    Args:
        user_data_dir: Path to the user data directory

    Returns:
        Process ID if found, None otherwise
    """
    import os

    try:
        normalized_target = os.path.normpath(str(user_data_dir))

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pname = (proc.info.get("name") or "").lower()
                CHROMIUM_NAMES = {"chromium", "chrome", "chromium-browser", "google-chrome"}
                if pname not in CHROMIUM_NAMES:
                    continue

                cmdline = proc.info.get("cmdline") or []
                for arg in cmdline:
                    if arg and "--user-data-dir=" in arg:
                        # 提取路径并规范化
                        arg_path = arg.split("--user-data-dir=", 1)[1]
                        if os.path.normpath(arg_path) == normalized_target:
                            return proc.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return None


__all__ = [
    "get_pid_lock",
    "kill_chromium_by_pid",
    "record_chromium_pid",
    "ensure_previous_chromium_closed",
    "find_chromium_pid_by_user_data_dir",
]
