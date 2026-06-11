from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class SlidexConfig:
    # ── slider concurrency ──
    max_concurrent: int = 3
    wait_timeout: int = 60

    # ── trajectory pool ──
    trajectory_pool_enabled: bool = True
    trajectory_pool_max_per_cookie: int = 50
    trajectory_pool_min_pool_size: int = 5
    trajectory_pool_rotation_strategy: str = "lru"
    trajectory_pool_recorded_only_mode: bool = False
    trajectory_pool_base_dir: Optional[str] = None

    # ── remote captcha ──
    remote_captcha_enabled: bool = True
    remote_captcha_timeout: int = 180
    remote_captcha_poll_interval: int = 2

    # ── paths ──
    browser_data_dir: Optional[str] = None
    debug_screenshot_dir: Optional[str] = None
    calibration_dir: Optional[str] = None
    project_root: Optional[str] = None

    # ── callback slots ──
    on_risk_log: Optional[Callable[..., Optional[int]]] = None
    on_risk_log_update: Optional[Callable[..., None]] = None
    on_notification: Optional[Callable[..., None]] = None

    def _default_dir(self, subdir: str) -> str:
        base = self.project_root or os.path.join(os.path.expanduser("~"), ".slidex")
        return os.path.join(base, subdir)

    def get_trajectory_dir(self) -> str:
        return self.trajectory_pool_base_dir or self._default_dir("trajectories")

    def get_browser_data_dir(self) -> str:
        return self.browser_data_dir or self._default_dir("browser_data")

    def get_debug_screenshot_dir(self) -> str:
        return self.debug_screenshot_dir or self._default_dir("debug_screenshots")

    def get_calibration_dir(self) -> str:
        return self.calibration_dir or self._default_dir("calibration")

    @classmethod
    def from_env(cls) -> "SlidexConfig":
        return cls(
            max_concurrent=int(os.environ.get("SLIDEX_MAX_CONCURRENT", "3")),
            wait_timeout=int(os.environ.get("SLIDEX_WAIT_TIMEOUT", "60")),
            trajectory_pool_enabled=os.environ.get("SLIDEX_TRAJ_POOL_ENABLED", "1") == "1",
            trajectory_pool_max_per_cookie=int(os.environ.get("SLIDEX_TRAJ_POOL_MAX", "50")),
            trajectory_pool_min_pool_size=int(os.environ.get("SLIDEX_TRAJ_POOL_MIN", "5")),
            trajectory_pool_rotation_strategy=os.environ.get("SLIDEX_TRAJ_POOL_STRATEGY", "lru"),
            trajectory_pool_base_dir=os.environ.get("SLIDEX_TRAJ_POOL_DIR") or None,
            remote_captcha_enabled=os.environ.get("SLIDEX_REMOTE_ENABLED", "1") == "1",
            remote_captcha_timeout=int(os.environ.get("SLIDEX_REMOTE_TIMEOUT", "180")),
            remote_captcha_poll_interval=int(os.environ.get("SLIDEX_REMOTE_POLL", "2")),
            browser_data_dir=os.environ.get("SLIDEX_BROWSER_DATA_DIR") or None,
            debug_screenshot_dir=os.environ.get("SLIDEX_DEBUG_SCREENSHOT_DIR") or None,
            calibration_dir=os.environ.get("SLIDEX_CALIBRATION_DIR") or None,
        )
