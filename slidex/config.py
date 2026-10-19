from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

from slidex._env import parse_float, parse_int


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

    # ── point select vision (0.6.34) ──
    # 多模态视觉通道。缺endpoint/model 时 point_select 直接返回
    # vision_not_configured（不可重试）——通道没配是部署问题，重试只空耗预算。
    vision_endpoint: Optional[str] = None
    vision_api_key: Optional[str] = None
    vision_model: Optional[str] = None
    vision_timeout_s: float = 180.0
    #: 低于该置信度视为「没认清」→ 换通道重截图，不硬点（0.6.28 重试纪律）。
    vision_min_confidence: float = 0.35
    #: 输出 token 上限。给得宽是因为截断的代价是「答案一个字没吐」——宁可多留
    #: 预算也不能让正文被 reasoning_tokens 挤掉（实测 64 时 reasoning 吃掉 59）。
    #: 正常识别的实际用量只有百余 token，这个上限跑不到。
    vision_max_tokens: int = 100_000
    #: 网关层超时（504/524）的重发次数。524 是网关等不到上游，重发有效。
    vision_gateway_retries: int = 3
    #: 点击半径（页面像素）与点击间隔区间（毫秒），用于人工化点击节奏。
    point_click_radius_px: float = 6.0
    point_inter_click_delay_ms: Tuple[int, int] = (350, 900)

    # ── paths ──
    browser_data_dir: Optional[str] = None
    debug_screenshot_dir: Optional[str] = None
    calibration_dir: Optional[str] = None
    trajectory_history_dir: Optional[str] = None
    project_root: Optional[str] = None

    # ── callback slots ──
    on_risk_log: Optional[Callable[..., Optional[int]]] = None
    on_risk_log_update: Optional[Callable[..., None]] = None
    on_notification: Optional[Callable[..., None]] = None
    telemetry_enabled: bool = True
    telemetry_dir: Optional[str] = None

    def _default_dir(self, subdir: str) -> str:
        base = self.project_root or os.path.join(os.path.expanduser("~"), ".slidex")
        return os.path.join(base, subdir)

    def get_trajectory_dir(self) -> str:
        return self.trajectory_pool_base_dir or self._default_dir("trajectories")

    def get_trajectory_history_dir(self) -> str:
        return self.trajectory_history_dir or self._default_dir("trajectory_history")

    def get_browser_data_dir(self) -> str:
        return self.browser_data_dir or self._default_dir("browser_data")

    def get_debug_screenshot_dir(self) -> str:
        return self.debug_screenshot_dir or self._default_dir("debug_screenshots")

    def get_calibration_dir(self) -> str:
        return self.calibration_dir or self._default_dir("calibration")

    def get_telemetry_dir(self) -> str:
        return self.telemetry_dir or self._default_dir("telemetry")

    @classmethod
    def _safe_int(cls, value: str, default: int) -> int:
        # 委托 _env.parse_int（0.6.42 收口为全库唯一容错契约）：非法/非有限回落。
        return parse_int(value, default)

    @classmethod
    def _safe_float(cls, value: str, default: float) -> float:
        # 委托 _env.parse_float 并保留原「非正数回落」规则（开下限 0）；
        # "inf" 由 _env 拒绝回落 —— 旧实现会让 float("inf") 通过并造成挂死风险。
        return parse_float(value, default, exclusive_minimum=0.0)

    @classmethod
    def _safe_delay_pair(cls, raw: str, default: Tuple[int, int]) -> Tuple[int, int]:
        """解析 "350,900" 形式的点击间隔区间；非法或非正序回落默认。"""
        try:
            lo_text, _, hi_text = str(raw).partition(",")
            lo, hi = int(lo_text), int(hi_text)
        except (ValueError, TypeError):
            return default
        if lo <= 0 or hi < lo:
            return default
        return (lo, hi)

    @classmethod
    def from_env(cls) -> "SlidexConfig":
        return cls(
            max_concurrent=cls._safe_int(os.environ.get("SLIDEX_MAX_CONCURRENT", "3"), 3),
            wait_timeout=cls._safe_int(os.environ.get("SLIDEX_WAIT_TIMEOUT", "60"), 60),
            trajectory_pool_enabled=os.environ.get("SLIDEX_TRAJ_POOL_ENABLED", "1") == "1",
            trajectory_pool_max_per_cookie=cls._safe_int(os.environ.get("SLIDEX_TRAJ_POOL_MAX", "50"), 50),
            trajectory_pool_min_pool_size=cls._safe_int(os.environ.get("SLIDEX_TRAJ_POOL_MIN", "5"), 5),
            trajectory_pool_rotation_strategy=os.environ.get("SLIDEX_TRAJ_POOL_STRATEGY", "lru"),
            trajectory_pool_base_dir=os.environ.get("SLIDEX_TRAJ_POOL_DIR") or None,
            remote_captcha_enabled=os.environ.get("SLIDEX_REMOTE_ENABLED", "1") == "1",
            remote_captcha_timeout=cls._safe_int(os.environ.get("SLIDEX_REMOTE_TIMEOUT", "180"), 180),
            remote_captcha_poll_interval=cls._safe_int(os.environ.get("SLIDEX_REMOTE_POLL", "2"), 2),
            vision_endpoint=os.environ.get("SLIDEX_VISION_ENDPOINT") or None,
            vision_api_key=os.environ.get("SLIDEX_VISION_API_KEY") or None,
            vision_model=os.environ.get("SLIDEX_VISION_MODEL") or None,
            vision_timeout_s=cls._safe_float(os.environ.get("SLIDEX_VISION_TIMEOUT", ""), 180.0),
            vision_min_confidence=cls._safe_float(
                os.environ.get("SLIDEX_VISION_MIN_CONFIDENCE", ""), 0.35
            ),
            vision_max_tokens=cls._safe_int(
                os.environ.get("SLIDEX_VISION_MAX_TOKENS", ""), 100_000
            ),
            vision_gateway_retries=cls._safe_int(
                os.environ.get("SLIDEX_VISION_GATEWAY_RETRIES", ""), 3
            ),
            point_click_radius_px=cls._safe_float(
                os.environ.get("SLIDEX_POINT_CLICK_RADIUS", ""), 6.0
            ),
            point_inter_click_delay_ms=cls._safe_delay_pair(
                os.environ.get("SLIDEX_POINT_INTER_DELAY_MS", ""), (350, 900)
            ),
            browser_data_dir=os.environ.get("SLIDEX_BROWSER_DATA_DIR") or None,
            debug_screenshot_dir=os.environ.get("SLIDEX_DEBUG_SCREENSHOT_DIR") or None,
            calibration_dir=os.environ.get("SLIDEX_CALIBRATION_DIR") or None,
            trajectory_history_dir=os.environ.get("SLIDEX_TRAJ_HISTORY_DIR") or None,
            telemetry_enabled=os.environ.get("SLIDEX_TELEMETRY_ENABLED", "1") == "1",
            telemetry_dir=os.environ.get("SLIDEX_TELEMETRY_DIR") or None,
        )
