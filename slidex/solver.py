import asyncio, json, os, re, socket, threading, time, random, shutil, psutil, uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List, Callable
from urllib.parse import urlparse, parse_qs
from loguru import logger
from playwright.async_api import async_playwright

try:
    from patchright.async_api import async_playwright as patchright_async_playwright
except Exception:
    patchright_async_playwright = None

def _resolve_automation_backend() -> str:
    """与 stealth.py 相同的 XY_SLIDER_AUTOMATION_BACKEND 约定，供 solver 复用。"""
    backend_env = os.environ.get("XY_SLIDER_AUTOMATION_BACKEND", "").strip().lower()
    if backend_env == "patchright" and patchright_async_playwright is not None:
        return "patchright"
    return "playwright"

from slidex._stealth_patch import STEALTH_LAUNCH_ARGS, STEALTH_INIT_SCRIPT
from slidex._trajectory import generate_trajectory, trajectory_to_points
from slidex._image_match import SliderImageMatcher
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex._sanitize import sanitize_pure_user_id
from slidex.config import SlidexConfig
from slidex._provider_mixin import ProviderSolverMixin
from slidex._chromium_lifecycle import (
    ensure_profile_chromium_closed,
    record_chromium_pid,
    find_chromium_pid_by_user_data_dir,
    find_chromium_pids_by_user_data_dir,
    kill_chromium_process_tree,
)
from slidex._async_budget import await_with_budget
from slidex._cookies import cookie_domain_for_url, parse_cookie_header, select_cookies_for_url
from slidex._frames import iter_search_targets, query_in_targets, wait_in_targets
from slidex._slide_geometry import clamp_travel, points_from_recorded
from slidex._slide_result import interpret_slide_json


# ════════════════════════════════════════════════════════════
#  默认选择器配置（Aliyun NoCaptcha）
# ════════════════════════════════════════════════════════════
DEFAULT_SELECTORS = {
    "slider_btn": "#nc_1_n1z",
    "slider_track": "#nc_1_n1t",
    "bg_img": "#nc_1_n1t img, .nc_scale img, img[id*=bg]",
    "piece_img": ".nc_iconfont, #nc_1_n1z img, img[id*=slide]",
    "track_width": ".nc_scale, [class*=track]",
    "slider_alt": (".nc_iconfont", ".btn_slide", ".sm-btn", "#nc_1_n1z"),
    "result_url_pattern": ("/slide?", "/_____tmd_____/slide"),
    "success_code": 0,
}



class SliderSolver(ProviderSolverMixin):
    """滑块求解器

    支持三种运行模式:
      - solve(): 启动自己的浏览器
      - solve_on_existing_page(): 连接已有浏览器（CDP 模式）
      - provider mode: 通过 provider="auto" 或 provider="geetest" 使用 Provider 适配器

    向后兼容:
      - selectors={...}: legacy 模式，使用硬编码选择器求解
    """

    MAX_RETRIES = 3
    OFFSET_CORRECTION_DEFAULT = -35
    OFFSET_CORRECTION_LIMITS = (-100, 100)
    CLOSE_TIMEOUT_S = 30.0
    # solve 全程硬看门狗：须大于内部各段预算之和（profile 锁等待 60 + 页面加载
    # ~60 + provider/legacy 重试 ~120 + remote 人工会话 180）。生产（1GB VPS）
    # 曾在死驱动连接上挂死 16h+，局部预算防不住挂在不同协议调用上的死等。
    SOLVE_WATCHDOG_TIMEOUT_S = float(os.environ.get("SLIDEX_SOLVE_WATCHDOG", "600"))
    SCREENSHOT_BUDGET_S = 10.0
    # CDP 模式人工等待期：自动拖动全部失败后，页面留在用户真实浏览器里，
    # 保持监听等人工拖过（真手通过率远高于合成轨迹）。须小于 solve 看门狗。
    MANUAL_VOUCHER_WAIT_S = float(os.environ.get("SLIDEX_MANUAL_VOUCHER_WAIT", "300"))

    # 同 profile（同账号浏览器目录）互斥：进程内串行化，防止并发求解互踩
    _profile_locks: Dict[str, asyncio.Lock] = {}
    _profile_locks_guard = threading.Lock()

    def __init__(self, cookie_id="default", cookies_str="", headless=True, proxy=None,
                 trajectory_mode: str = "auto",
                 config: Optional[SlidexConfig] = None,
                 notification_callback: Optional[Callable] = None,
                 selectors: Optional[Dict] = None,
                 provider: Optional[str] = None):
        # Initialize provider mixin first (handles provider logic)
        super().__init__(provider=provider)

        self.cookie_id = cookie_id
        raw_id = cookie_id.split("_")[0] if "_" in cookie_id else cookie_id
        self.pure_user_id = sanitize_pure_user_id(raw_id)
        self.cookies_str = str(cookies_str or "").strip()
        self.headless = headless
        self.proxy = dict(proxy or {})
        self.trajectory_mode = trajectory_mode
        self.last_fallback_used = None
        self._current_recorded_trajectory = None
        self._config = config or SlidexConfig()
        self._notification_callback = notification_callback
        self._is_cdp_mode = False
        self._scale_slider = False
        self.selectors = {**DEFAULT_SELECTORS, **(selectors or {})}

        traj_dir = self._config.get_trajectory_dir()
        self._trajectory_pool = SliderTrajectoryPool(base_dir=traj_dir)

        profile_root = Path(self._config.get_browser_data_dir())
        self.profile_dir = profile_root / f"slider_{self.pure_user_id}"
        # profile_dir creation deferred to _init_browser (not needed in CDP mode)
        self._playwright = None
        self.context = None
        self.page = None
        self._cdp = None
        self._browser_pid = None
        self._profile_lock_held = False
        self._verify_url = ""
        self._slider_scope = None
        self._result_event = asyncio.Event()
        self._slide_code = None
        self._slide_ok: Optional[bool] = None
        # punish 票据旁路：x5sec 随校验 XHR 的 bx-x5sec / bx-x5sec-root 响应头下发，
        # 页面 checkCookie 回调才写进 document.cookie——环境不稳时回调不跑，票据
        # 就只能从这里自取（0.6.10）。
        self._bx_voucher: Optional[str] = None
        self._calibration = self._load_calibration()
        self._telemetry_run_id = uuid.uuid4().hex
        self._telemetry_events: List[Dict] = []
        self._telemetry_summary: Dict[str, object] = {
            "run_id": self._telemetry_run_id,
            "cookie_id": self.cookie_id,
            "pure_user_id": self.pure_user_id,
            "provider_mode": self._provider_name or "legacy",
            "trajectory_mode": self.trajectory_mode,
            "status": "running",
            "success": None,
            "fallback_used": None,
            "failure_reason": None,
            "distance": None,
            "distance_source": None,
            "provider_name": None,
            "slide_code": None,
            "cookie_count": 0,
            "remote_session_id": None,
            "started_at": time.time(),
            "elapsed_ms": None,
            "risk_log_id": None,
        }

    def _emit_step(self, phase: str, step: str, status: str = "started", **metadata):
        safe_metadata = self._redact_step_metadata(metadata)
        entry = self._emit_telemetry_event(
            "solver_step",
            phase=phase,
            step=step,
            status=status,
            **safe_metadata,
        )
        if entry:
            self._write_telemetry_record(entry)
            log_method = logger.info
            if status == "failed":
                log_method = logger.warning
            elif status == "skipped":
                log_method = logger.debug
            details = " ".join(f"{key}={value}" for key, value in safe_metadata.items() if value is not None)
            suffix = f" {details}" if details else ""
            log_method(f"[{self.pure_user_id}] step {phase}.{step} {status}{suffix}")
        return entry

    @classmethod
    def _redact_step_metadata(cls, value: Any):
        if isinstance(value, dict):
            safe = {}
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if key_text.endswith("_url") or lowered in {"url", "verify_url", "page_url", "response_url"}:
                    safe[key] = cls._sanitize_url(item)
                elif lowered in {"cookie_names", "cookie_count"}:
                    safe[key] = cls._redact_step_metadata(item)
                elif cls._is_sensitive_key(key_text):
                    safe[key] = "[redacted]"
                else:
                    safe[key] = cls._redact_step_metadata(item)
            return safe
        if isinstance(value, (list, tuple, set)):
            return [cls._redact_step_metadata(item) for item in value]
        if isinstance(value, str):
            return cls._redact_sensitive_string(value)
        return value

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        lowered = key.lower()
        return any(
            token in lowered
            for token in (
                "cookie",
                "token",
                "secret",
                "password",
                "authorization",
                "x5sec",
                "x5secdata",
            )
        )

    @staticmethod
    def _sanitize_url(url_value: Any):
        if not isinstance(url_value, str):
            return url_value
        try:
            parsed = urlparse(url_value)
            query = parse_qs(parsed.query or "", keep_blank_values=True)
            return {
                "scheme": parsed.scheme,
                "host": parsed.netloc,
                "path": parsed.path,
                "query_keys": sorted(query.keys()),
            }
        except Exception:
            return "[redacted-url]"

    @staticmethod
    def _redact_sensitive_string(value: str) -> str:
        if not value:
            return value
        sensitive_names = (
            "x5secdata",
            "x5sec",
            "token",
            "access_token",
            "refresh_token",
            "authorization",
            "password",
            "secret",
            "cookie",
        )
        redacted = value
        for name in sensitive_names:
            redacted = re.sub(
                rf"(?i)({re.escape(name)}\s*[:=]\s*)([^&\s,;]+)",
                r"\1[redacted]",
                redacted,
            )
        return redacted

    def _emit_telemetry_event(self, event: str, **payload):
        if not self._config.telemetry_enabled:
            return None
        payload = self._redact_step_metadata(payload)

        entry = {
            "event": event,
            "run_id": self._telemetry_run_id,
            "cookie_id": self.cookie_id,
            "pure_user_id": self.pure_user_id,
            "timestamp": time.time(),
            **payload,
        }
        self._telemetry_events.append(entry)

        if event == "distance_detected":
            self._telemetry_summary["distance"] = payload.get("distance")
            self._telemetry_summary["distance_source"] = payload.get("source")
        elif event == "provider_selected":
            self._telemetry_summary["provider_name"] = payload.get("provider_name")
        elif event == "fallback_started":
            self._telemetry_summary["fallback_used"] = payload.get("fallback")
            self._telemetry_summary["remote_session_id"] = payload.get("session_id")
        elif event == "slide_result":
            self._telemetry_summary["slide_code"] = payload.get("slide_code")

        callback = self._config.on_risk_log_update
        if callback:
            try:
                callback(entry)
            except Exception as e:
                logger.debug(f"[{self.pure_user_id}] telemetry callback error: {e}")
        return entry

    def _write_telemetry_record(self, payload: Dict[str, object]):
        if not self._config.telemetry_enabled:
            return
        try:
            telemetry_dir = Path(self._config.get_telemetry_dir())
            telemetry_dir.mkdir(parents=True, exist_ok=True)
            events_path = telemetry_dir / "events.jsonl"
            with events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] telemetry persist failed: {e}")

    def _write_telemetry_summary_file(self, payload: Dict[str, object]):
        """per-run 摘要落盘，使 VisionArtifact(artifacts=telemetry/{run_id}.json) 契约成立"""
        try:
            telemetry_dir = Path(self._config.get_telemetry_dir())
            telemetry_dir.mkdir(parents=True, exist_ok=True)
            path = telemetry_dir / f"{self._telemetry_run_id}.json"
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] telemetry summary persist failed: {e}")

    def _finalize_telemetry(
        self,
        *,
        success: bool,
        status: str,
        cookies: Optional[dict] = None,
        extra: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        self._telemetry_summary["success"] = success
        self._telemetry_summary["status"] = status
        self._telemetry_summary["elapsed_ms"] = round(
            max(0.0, (time.time() - float(self._telemetry_summary["started_at"])) * 1000), 1
        )
        self._telemetry_summary["cookie_count"] = len(cookies or {})
        if extra:
            self._telemetry_summary.update(extra)

        payload = {
            "event": "solve_summary",
            **self._telemetry_summary,
        }

        callback = self._config.on_risk_log
        if callback:
            try:
                risk_log_id = callback(**payload)
                if risk_log_id is not None:
                    self._telemetry_summary["risk_log_id"] = risk_log_id
                    payload["risk_log_id"] = risk_log_id
            except Exception as e:
                logger.debug(f"[{self.pure_user_id}] summary callback error: {e}")

        self._write_telemetry_record(payload)
        self._write_telemetry_summary_file(payload)
        return dict(self._telemetry_summary)

    def get_telemetry_summary(self) -> Dict[str, object]:
        return dict(self._telemetry_summary)

    def get_telemetry_dir(self) -> str:
        """telemetry 摘要的实际落盘目录（与 _write_telemetry_summary_file 一致）。

        声明给外层的 per-run 摘要 artifact 路径必须与这里对齐，否则报告里的
        telemetry/{run_id}.json 是 CWD 相对的幽灵路径、指向不存在的文件。
        """
        return self._config.get_telemetry_dir()

    # ════════════════════════════════════════════════════════════
    #  同 profile 并发治理
    # ════════════════════════════════════════════════════════════
    @classmethod
    def _get_profile_lock(cls, profile_dir: str) -> asyncio.Lock:
        with cls._profile_locks_guard:
            lock = cls._profile_locks.get(profile_dir)
            if lock is None:
                lock = asyncio.Lock()
                cls._profile_locks[profile_dir] = lock
            return lock

    async def _acquire_profile_lock(self, profile_dir: str) -> bool:
        """有界等待同 profile 互斥锁；超时返回 False（与 concurrency_manager.wait_for_slot 同语义）。

        不用 ``wait_for(lock.acquire())``：3.10 超时可能丢掉已完成的 acquire，锁被占死。
        也不用 ``wait_for(shield(acquire))``：3.11+ 超时会取消 shield 并一直等到它结束，
        而 shield 不会把取消传给 acquire，等于卡死。改成 ``asyncio.wait`` 与 sleep 竞速；
        所有权以 acquire Future 的 done 回调为准，超时/取消时若已拿到则立即释放。
        """
        lock = self._get_profile_lock(profile_dir)
        timeout = max(1.0, float(self._config.wait_timeout))
        acquired = False

        def _mark_acquired(fut: asyncio.Future) -> None:
            nonlocal acquired
            if fut.cancelled():
                return
            try:
                if fut.exception() is None:
                    acquired = True
            except (asyncio.CancelledError, Exception):
                return

        task = asyncio.ensure_future(lock.acquire())
        task.add_done_callback(_mark_acquired)
        timeout_waiter = asyncio.ensure_future(asyncio.sleep(timeout))
        try:
            await asyncio.wait(
                {task, timeout_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            await self._await_cancelled(timeout_waiter)
            await self._abandon_lock_acquire(lock, task, acquired)
            raise

        await self._await_cancelled(timeout_waiter)
        got_lock = False
        if task.done() and not task.cancelled():
            try:
                got_lock = task.exception() is None
            except (asyncio.CancelledError, Exception):
                got_lock = False
        if got_lock:
            self._profile_lock_held = True
            return True
        await self._abandon_lock_acquire(lock, task, acquired)
        return False

    @staticmethod
    async def _await_cancelled(fut: asyncio.Future) -> None:
        if not fut.done():
            fut.cancel()
        try:
            await fut
        except (asyncio.CancelledError, Exception):
            pass

    async def _abandon_lock_acquire(
        self,
        lock: asyncio.Lock,
        task: asyncio.Future,
        acquired: bool,
    ) -> None:
        """超时/取消后：若 acquire 仍在等则取消；若已经拿到锁则立刻释放。"""
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if not acquired and task.done() and not task.cancelled():
            try:
                acquired = task.exception() is None
            except (asyncio.CancelledError, Exception):
                acquired = False
        if acquired:
            try:
                lock.release()
            except RuntimeError:
                pass
        self._profile_lock_held = False

    def _release_profile_lock(self, profile_dir: str) -> None:
        if not getattr(self, "_profile_lock_held", False):
            return
        self._profile_lock_held = False
        lock = self._get_profile_lock(profile_dir)
        if lock.locked():
            try:
                lock.release()
            except RuntimeError:
                pass

    # ════════════════════════════════════════════════════════════
    #  主求解入口
    # ════════════════════════════════════════════════════════════
    async def solve(self, verify_url):
        """启动自己的浏览器求解

        全程硬看门狗：超预算即 cancel 求解任务（其 finally 里预算化清理与
        profile 锁释放通常仍会执行；若任务被遗弃则此处补发），并 OS 级强杀
        浏览器进程树后返回失败——调用方（编排器）随即降级 remote/DrissionPage，
        token 刷新不会因单个挂死的 solve 永久卡住。
        """
        result = await await_with_budget(
            self._solve_impl(verify_url), self.SOLVE_WATCHDOG_TIMEOUT_S
        )
        if result is None:
            logger.error(
                f"[{self.pure_user_id}] solve watchdog fired after "
                f"{self.SOLVE_WATCHDOG_TIMEOUT_S:.0f}s, hard-killing browser"
            )
            self._emit_step("solve", "solve_watchdog", "failed", timeout_s=self.SOLVE_WATCHDOG_TIMEOUT_S)
            self._emit_telemetry_event("solve_watchdog_fired", timeout_s=self.SOLVE_WATCHDOG_TIMEOUT_S)
            self._finalize_telemetry(
                success=False,
                status="watchdog_timeout",
                cookies=None,
                extra={"failure_reason": "solve_watchdog_timeout"},
            )
            await self._hard_kill_browser()
            self._release_profile_lock(str(self.profile_dir))
            return False, None
        return result

    async def _solve_impl(self, verify_url):
        self.last_fallback_used = None
        self._is_cdp_mode = False
        self._bx_voucher = None
        self._emit_telemetry_event("solve_started", mode="browser", verify_url=verify_url)
        self._emit_step("solve", "solve_started", "started", mode="browser", verify_url=verify_url)
        logger.info(f"[{self.pure_user_id}] solving (mode={self.trajectory_mode})...")
        self._verify_url = verify_url or ""
        if not await self._acquire_profile_lock(str(self.profile_dir)):
            logger.warning(
                f"[{self.pure_user_id}] same-profile solve still running, "
                f"gave up after {self._config.wait_timeout}s"
            )
            self._emit_step("solve", "profile_lock", "failed", reason="same_profile_busy")
            self._finalize_telemetry(
                success=False,
                status="profile_lock_timeout",
                cookies=None,
                extra={"failure_reason": "profile_lock_timeout"},
            )
            return False, None
        try:
            await self._init_browser()
            await self._load_page(verify_url)
            success, cookies = await self._run_solve_loop(verify_url)
            self._finalize_telemetry(
                success=success,
                status="success" if success else "failed",
                cookies=cookies,
                extra={"failure_reason": None if success else "solve_failed"},
            )
            return success, cookies
        except Exception as e:
            logger.error(f"[{self.pure_user_id}] error: {e}")
            self._emit_step("solve", "solve_exception", "failed", reason=str(e))
            success, cookies = await self._fallback_or_fail(verify_url)
            self._finalize_telemetry(
                success=success,
                status="exception" if not success else "success",
                cookies=cookies,
                extra={"failure_reason": str(e) if not success else None},
            )
            return success, cookies
        finally:
            await self._close()
            self._release_profile_lock(str(self.profile_dir))

    async def solve_on_existing_page(
        self,
        cdp_endpoint: str,
        page_url: str = "",
    ) -> Tuple[bool, Optional[dict]]:
        """连接已有浏览器求解（CDP 模式）

        同样受 solve 看门狗约束；外部浏览器不属于本实例，超时只做预算化断开
        （_close_cdp_only），绝不杀浏览器进程。

        Args:
            cdp_endpoint: CDP WebSocket 地址，如 ws://localhost:9222/devtools/browser/xxx
            page_url: 如果提供，先导航到此 URL

        Returns:
            (success, cookies)
        """
        result = await await_with_budget(
            self._solve_on_existing_impl(cdp_endpoint, page_url),
            self.SOLVE_WATCHDOG_TIMEOUT_S,
        )
        if result is None:
            logger.error(
                f"[{self.pure_user_id}] CDP solve watchdog fired after "
                f"{self.SOLVE_WATCHDOG_TIMEOUT_S:.0f}s"
            )
            self._emit_step("solve", "solve_watchdog", "failed", timeout_s=self.SOLVE_WATCHDOG_TIMEOUT_S, mode="cdp")
            self._emit_telemetry_event("solve_watchdog_fired", timeout_s=self.SOLVE_WATCHDOG_TIMEOUT_S, mode="cdp")
            try:
                await self._close_cdp_only()
            except Exception:
                pass
            self._finalize_telemetry(
                success=False,
                status="watchdog_timeout",
                cookies=None,
                extra={"failure_reason": "solve_watchdog_timeout"},
            )
            return False, None
        return result

    async def _solve_on_existing_impl(
        self,
        cdp_endpoint: str,
        page_url: str = "",
    ) -> Tuple[bool, Optional[dict]]:
        self.last_fallback_used = None
        self._is_cdp_mode = True
        self._bx_voucher = None
        self._verify_url = page_url or ""
        self._emit_telemetry_event("solve_started", mode="cdp", page_url=page_url)
        self._emit_step("solve", "solve_started", "started", mode="cdp", page_url=page_url)
        logger.info(f"[{self.pure_user_id}] solving on existing page "
                    f"(cdp={cdp_endpoint[:60]}, mode={self.trajectory_mode})...")
        try:
            await self._connect_existing_browser(cdp_endpoint, page_url)
            success, cookies = await self._run_solve_loop(page_url)
            self._finalize_telemetry(
                success=success,
                status="success" if success else "failed",
                cookies=cookies,
                extra={"failure_reason": None if success else "solve_failed"},
            )
            return success, cookies
        except Exception as e:
            logger.error(f"[{self.pure_user_id}] CDP solve error: {e}")
            self._emit_step("solve", "solve_exception", "failed", reason=str(e))
            self._finalize_telemetry(
                success=False,
                status="exception",
                cookies=None,
                extra={"failure_reason": str(e)},
            )
            return False, None
        finally:
            await self._close_cdp_only()

    async def solve_on_page(self, page, page_url: str = "") -> Tuple[bool, Optional[dict]]:
        """在调用方持有的 Playwright Page 上求解，不接管浏览器生命周期。"""
        self.last_fallback_used = None
        self._is_cdp_mode = True
        self._bx_voucher = None
        self.page = page
        self.context = page.context
        self._verify_url = page_url or getattr(page, "url", "") or ""
        self._emit_telemetry_event("solve_started", mode="playwright_page", page_url=page_url)
        self._emit_step("solve", "solve_started", "started", mode="playwright_page", page_url=page_url)
        response_handler = self._on_response
        listener_registered = False
        try:
            self.page.on("response", response_handler)
            self.page.on("console", self._on_console)
            listener_registered = True
            try:
                self._cdp = await self.context.new_cdp_session(self.page)
                logger.debug(f"[{self.pure_user_id}] CDP session ready (provided page)")
            except Exception:
                self._cdp = None
                logger.warning(f"[{self.pure_user_id}] CDP session failed (provided page)")

            if page_url:
                self._emit_step("page", "page_load", "started", page_url=page_url)
                await self.page.goto(page_url, wait_until="networkidle", timeout=45000)
                await asyncio.sleep(3)
                self._emit_step("page", "page_load", "ok", page_url=page_url)

            success, cookies = await self._run_solve_loop(page_url)
            self._finalize_telemetry(
                success=success,
                status="success" if success else "failed",
                cookies=cookies,
                extra={"failure_reason": None if success else "solve_failed"},
            )
            return success, cookies
        except Exception as e:
            logger.error(f"[{self.pure_user_id}] Playwright page solve error: {e}")
            self._emit_step("solve", "solve_exception", "failed", reason=str(e))
            self._finalize_telemetry(
                success=False,
                status="exception",
                cookies=None,
                extra={"failure_reason": str(e)},
            )
            return False, None
        finally:
            if listener_registered:
                remove_listener = getattr(self.page, "remove_listener", None)
                if remove_listener:
                    try:
                        remove_listener("response", response_handler)
                    except Exception:
                        pass
                    try:
                        remove_listener("console", self._on_console)
                    except Exception:
                        pass
            await self._close_cdp_only()

    async def _run_solve_loop(self, verify_url: str):
        """核心求解循环 — Provider 模式 或 Legacy 模式"""

        # ── Provider 模式：尝试自动检测并使用 provider ──
        self._emit_step("solve", "solve_loop", "started", verify_url=verify_url)
        if self._use_provider_mode:
            if await self._detect_and_init_provider(self.page):
                logger.info(f"[{self.pure_user_id}] using provider mode: {self._provider.name}")
                self._emit_telemetry_event("provider_selected", provider_name=self._provider.name, selected_by="detect")
                success, cookies = await self._solve_with_provider(self.page)
                if success:
                    return True, cookies
                logger.warning(f"[{self.pure_user_id}] provider mode failed, falling back to legacy")
            else:
                logger.warning(f"[{self.pure_user_id}] provider detection failed, using legacy mode")

        # ── Legacy 模式：硬编码选择器 + 录制轨迹 ──
        return await self._run_legacy_solve_loop(verify_url)

    async def _run_legacy_solve_loop(self, verify_url: str):
        """Legacy 求解循环 — 录制回放 + 数学生成 + 重试"""
        self._emit_step("legacy", "slider_wait", "started", timeout_s=15.0)
        if not await self._wait_slider():
            logger.warning(f"[{self.pure_user_id}] slider not found on page")
            self._emit_step("legacy", "slider_wait", "failed", reason="slider_not_found")
            await self._save_debug_screenshot("slider_not_found")
            return await self._fallback_or_fail(verify_url)
        await self._install_net_tap(self.page)
        self._emit_step("legacy", "slider_wait", "ok")

        self._emit_step("legacy", "distance_detection", "started")
        distance = await self._calc_distance_multi_source()
        if distance is None or distance <= 0:
            logger.warning(f"[{self.pure_user_id}] cannot determine distance")
            self._emit_step("legacy", "distance_detection", "failed", distance=distance)
            return await self._fallback_or_fail(verify_url)
        self._emit_step("legacy", "distance_detection", "ok", distance=distance)

        success_code = self.selectors["success_code"]

        # ── Phase A: 录制轨迹模式 ──
        if self.trajectory_mode in ("auto", "recorded"):
            recorded = self._trajectory_pool.load_best_trajectory(
                self.pure_user_id, distance)
            if recorded and recorded.get("points"):
                logger.info(f"[{self.pure_user_id}] using recorded trajectory "
                            f"(dist={recorded['distance']:.0f}px, {len(recorded['points'])} points)")
                for attempt in range(1, self.MAX_RETRIES + 1):
                    self._emit_step("legacy", "slide_attempt", "started", mode="recorded", attempt=attempt, distance=distance)
                    await self._do_slide(distance, attempt, recorded_trajectory=recorded)
                    ok, code = await self._wait_slide_outcome(6.0, success_code)
                    logger.info(f"[{self.pure_user_id}] recorded replay attempt {attempt}: ok={ok} code={code}")
                    self._emit_step(
                        "legacy",
                        "slide_attempt",
                        "ok" if ok else "failed",
                        mode="recorded",
                        attempt=attempt,
                        slide_code=code,
                    )
                    if ok:
                        cookies = await self._get_cookies()
                        logger.success(f"[{self.pure_user_id}] pass! (recorded, attempt={attempt})")
                        cookies = await self._settle_x5sec(self.page, cookies)
                        return True, cookies
                    if attempt < self.MAX_RETRIES:
                        await asyncio.sleep(2 + random.uniform(1, 2))
                        if not await self._wait_slider(10.0):
                            break
                        distance = await self._calc_distance_multi_source() or distance
                logger.warning(f"[{self.pure_user_id}] recorded replays exhausted")
                if self.trajectory_mode == "recorded":
                    return await self._fallback_or_fail(verify_url)

        # ── Phase B: 数学模型生成的轨迹 ──
        logger.info(f"[{self.pure_user_id}] trying generated trajectories...")
        for attempt in range(1, self.MAX_RETRIES + 1):
            self._emit_step("legacy", "slide_attempt", "started", mode="generated", attempt=attempt, distance=distance)
            await self._do_slide(distance, attempt)
            ok, code = await self._wait_slide_outcome(6.0, success_code)
            logger.info(f"[{self.pure_user_id}] generated attempt {attempt}: ok={ok} code={code}")
            self._emit_step(
                "legacy",
                "slide_attempt",
                "ok" if ok else "failed",
                mode="generated",
                attempt=attempt,
                slide_code=code,
            )
            if ok:
                cookies = await self._get_cookies()
                logger.success(f"[{self.pure_user_id}] pass! (generated, attempt={attempt})")
                cookies = await self._settle_x5sec(self.page, cookies)
                return True, cookies
            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(2 + random.uniform(1, 2))
                if not await self._wait_slider(10.0):
                    break
                distance = await self._calc_distance_multi_source() or distance

        logger.warning(f"[{self.pure_user_id}] all auto retries exhausted")
        return await self._fallback_or_fail(verify_url)

    async def _fallback_or_fail(self, verify_url):
        # 手动通过收割：checkCookie 链已在页面层走通并发出 bx-x5sec 票据
        # （典型场景：CDP 模式下真人在外部浏览器拖过了滑块），但 provider 的
        # 结果等待器只认自己流水线的完成信号，legacy 循环又只见"滑块已消失"，
        # 全部重试耗尽走到这里 —— 页面层验证其实是成功的。此时绝不能把已捕获
        # 的票据当失败丢弃：结算成 x5sec 返回，bot 侧才能合并并重试 token API。
        if self._bx_voucher:
            logger.info(f"[{self.pure_user_id}] bx voucher captured earlier — harvesting page-level pass")
            self._emit_telemetry_event("manual_voucher_harvested", source="fallback_or_fail")
            self._emit_step("solve", "voucher_harvest", "started", reason="voucher_captured_before_fail")
            try:
                cookies = await self._get_cookies()
                cookies = await self._settle_x5sec(self.page, cookies)
            except Exception as e:
                logger.warning(f"[{self.pure_user_id}] voucher harvest settle failed: {e}")
                self._emit_step("solve", "voucher_harvest", "failed", reason=str(e))
                cookies = None
            if self._has_validation_cookie(cookies):
                logger.success(f"[{self.pure_user_id}] pass! (manual voucher harvest)")
                self._emit_step("solve", "voucher_harvest", "ok")
                return True, cookies
            logger.warning(f"[{self.pure_user_id}] voucher present but x5sec did not settle")
            self._emit_step("solve", "voucher_harvest", "failed", reason="x5sec_absent")
        if self._is_cdp_mode and self.page is not None:
            # CDP 模式最后一张牌：自动拖动耗尽后不立即放弃——页面开在用户
            # 真实浏览器里，人工随时可能拖过。保持响应监听轮询等待：
            # 票据头出现（checkCookie 链走通）或 x5sec 直接落 jar 即收割。
            # 人工拖动后若仍无果才返回失败。须留足 solve 看门狗预算。
            wait_s = max(0.0, self.MANUAL_VOUCHER_WAIT_S)
            logger.info(f"[{self.pure_user_id}] CDP mode: waiting up to {wait_s:.0f}s for manual pass on the visible page")
            self._emit_step("solve", "manual_wait", "started", timeout_s=wait_s)
            loop = asyncio.get_event_loop()
            deadline = loop.time() + wait_s
            while loop.time() < deadline:
                if self.page is not None and callable(getattr(self.page, "is_closed", None)) and self.page.is_closed():
                    logger.warning(f"[{self.pure_user_id}] page closed during manual wait")
                    self._emit_step("solve", "manual_wait", "failed", reason="page_closed")
                    break
                if self._bx_voucher:
                    try:
                        cookies = await self._get_cookies()
                        cookies = await self._settle_x5sec(self.page, cookies)
                    except Exception as e:
                        logger.warning(f"[{self.pure_user_id}] manual wait settle failed: {e}")
                        cookies = None
                    if self._has_validation_cookie(cookies):
                        logger.success(f"[{self.pure_user_id}] pass! (manual pass during wait)")
                        self._emit_step("solve", "manual_wait", "ok")
                        return True, cookies
                try:
                    jar = await self._get_cookies()
                except Exception:
                    jar = {}
                if jar.get("x5sec"):
                    logger.success(f"[{self.pure_user_id}] pass! (x5sec landed in jar during manual wait)")
                    self._emit_step("solve", "manual_wait", "ok")
                    return True, jar
                await asyncio.sleep(1.5)
            logger.warning(f"[{self.pure_user_id}] manual wait expired without a pass")
            self._emit_step("solve", "manual_wait", "failed", reason="no_manual_pass")
            self._emit_telemetry_event("fallback_skipped", reason="cdp_mode")
            self._emit_step("remote", "remote_fallback", "skipped", reason="cdp_mode")
            return False, None
        if self.trajectory_mode in ("auto", "recorded"):
            try:
                result = await self._fallback_to_remote(verify_url)
                if result[0]:
                    return result
                if result[1] is not None:
                    return result
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] remote fallback failed: {e}")
                self._emit_step("remote", "remote_fallback", "failed", reason=str(e))
        return False, None

    # ════════════════════════════════════════════════════════════
    #  远程人工兜底
    # ════════════════════════════════════════════════════════════
    async def _fallback_to_remote(self, verify_url) -> Tuple[bool, Optional[dict]]:
        try:
            from slidex.remote import captcha_controller
        except ImportError:
            logger.warning(f"[{self.pure_user_id}] captcha_remote_control not available")
            self._emit_step("remote", "remote_fallback", "skipped", reason="remote_controller_unavailable")
            return False, None

        session_id = f"slider_fallback_{self.pure_user_id}_{int(time.time())}"
        logger.info(f"[{self.pure_user_id}] starting remote fallback session: {session_id}")
        self._emit_telemetry_event("fallback_started", fallback="remote", session_id=session_id)
        self._emit_step("remote", "remote_fallback", "started", session_id=session_id, verify_url=verify_url)

        if not self.page:
            if self._is_cdp_mode:
                logger.warning(f"[{self.pure_user_id}] CDP mode: cannot fallback without a page")
                self._emit_step("remote", "remote_fallback", "skipped", reason="cdp_without_page")
                return False, None
            try:
                await self._init_browser()
                await self._load_page(verify_url)
                await self._wait_slider()
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] failed to init browser for remote: {e}")
                self._emit_step("remote", "remote_fallback", "failed", reason=str(e))
                return False, None

        session_created = False
        try:
            try:
                await captcha_controller.create_session(
                    session_id, self.page, cookie_id=self.pure_user_id
                )
                session_created = True
                self._emit_step("remote", "session_created", "ok", session_id=session_id)
            except Exception as e:
                logger.error(f"[{self.pure_user_id}] create remote session failed: {e}")
                self._emit_step("remote", "session_created", "failed", session_id=session_id, reason=str(e))
                return False, None

            # 发送通知（通过注入的回调）
            if self._notification_callback:
                try:
                    # 控制 URL 携带一次性 ticket 而非长期 token：token 不落入访问日志/
                    # Referer/浏览器历史，页面 GET 时由服务端换 ticket 入页面内存。
                    control_ticket = captcha_controller.issue_control_ticket(session_id)
                    control_path = f"/api/captcha/control/{session_id}?ticket={control_ticket}"
                    asyncio.ensure_future(
                        self._notification_callback(
                            self.cookie_id,
                            f"【滑块验证需要人工介入】\nCookie: {self.cookie_id}\nSession: {session_id}\nURL: {control_path}\n"
                            f"请访问滑块控制页面完成验证",
                            "滑块验证 - 人工介入"
                        )
                    )
                except Exception as e:
                    logger.warning(f"[{self.pure_user_id}] notification failed: {e}")

            self.last_fallback_used = "remote"
            timeout = self._config.remote_captcha_timeout
            poll_interval = self._config.remote_captcha_poll_interval
            deadline = time.time() + timeout

            while time.time() < deadline:
                try:
                    completed = await captcha_controller.check_completion(session_id)
                    if completed:
                        logger.success(f"[{self.pure_user_id}] remote solve completed!")
                        self._emit_step("remote", "remote_completion", "ok", session_id=session_id)
                        cookies = await self._get_cookies()
                        if self._requires_validation_cookie(verify_url) and not self._has_validation_cookie(cookies):
                            logger.warning(
                                f"[{self.pure_user_id}] remote completion missing validation cookie; "
                                "treating as unresolved"
                            )
                            self._emit_telemetry_event(
                                "fallback_validation_cookie_missing",
                                fallback="remote",
                                session_id=session_id,
                                cookie_names=sorted((cookies or {}).keys()),
                            )
                            self._emit_step(
                                "remote",
                                "validation_cookie",
                                "failed",
                                session_id=session_id,
                                cookie_names=sorted((cookies or {}).keys()),
                                verify_url=verify_url,
                            )
                            self._telemetry_summary["failure_reason"] = "x5_validation_cookie_missing"
                            return False, cookies
                        self._emit_telemetry_event("fallback_completed", fallback="remote", session_id=session_id)
                        self._emit_step(
                            "remote",
                            "validation_cookie",
                            "ok",
                            session_id=session_id,
                            cookie_names=sorted((cookies or {}).keys()),
                        )
                        try:
                            recording = captcha_controller.finish_recording(session_id)
                            if recording and recording.get("points"):
                                self._trajectory_pool.save_trajectory(
                                    recording["points"], self.pure_user_id,
                                    recording.get("distance", 0), True, verify_url,
                                    recording.get("duration_ms", 0))
                                logger.info(f"[{self.pure_user_id}] trajectory recorded from remote solve")
                        except Exception as e:
                            logger.warning(f"[{self.pure_user_id}] trajectory record failed: {e}")
                        return True, cookies
                    await asyncio.sleep(poll_interval)
                except Exception as e:
                    logger.warning(f"[{self.pure_user_id}] poll error: {e}")
                    self._emit_step("remote", "poll", "failed", session_id=session_id, reason=str(e))
                    await asyncio.sleep(poll_interval)

            logger.warning(f"[{self.pure_user_id}] remote fallback timed out after {timeout}s")
            self._emit_telemetry_event("fallback_timeout", fallback="remote", session_id=session_id, timeout_s=timeout)
            self._emit_step("remote", "remote_fallback", "failed", session_id=session_id, reason="timeout", timeout_s=timeout)
            return False, None
        finally:
            if session_created:
                try:
                    await captcha_controller.close_session(session_id)
                except Exception:
                    pass

    # ════════════════════════════════════════════════════════════
    #  多源距离计算（链式 fallback + 自适应校准）
    # ════════════════════════════════════════════════════════════
    async def _calc_distance_multi_source(self) -> Optional[float]:
        """缺口行程来自图像匹配；JS 轨道宽-按钮宽只是可滑动上限，不当缺口。
        scale 型（nc.js"拖到最右边"，无缺口）例外：满行程即目标，
        图像匹配出的"缺口"是背景纹理伪匹配（生产实测恒 87px/conf 0.31）。"""
        self._scale_slider = False
        js_dist = await self._calc_distance_js()
        logger.debug(f"[{self.pure_user_id}] JS max travel: {js_dist}")

        if self._scale_slider and js_dist and js_dist > 0:
            logger.info(
                f"[{self.pure_user_id}] scale slider detected: travel=full {js_dist:.0f}px "
                "(no gap to match — slide to end)"
            )
            self._emit_telemetry_event(
                "distance_detected", distance=float(js_dist), source="scale_full_travel"
            )
            return float(js_dist)

        img_dist = await self._calc_distance()
        if img_dist and img_dist > 0:
            travel = clamp_travel(img_dist, js_dist)
            if js_dist and js_dist > 0 and img_dist > js_dist:
                logger.warning(
                    f"[{self.pure_user_id}] image gap {img_dist:.0f}px exceeds JS max travel "
                    f"{js_dist:.0f}px, clamping"
                )
            self._emit_telemetry_event(
                "distance_detected",
                distance=travel,
                source="image_match",
                image_distance=img_dist,
                max_travel=js_dist,
            )
            return travel

        try:
            scope = self._challenge_scope()
            track_w = await scope.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.offsetWidth : 0;
                }""", self.selectors["track_width"])
            if track_w and track_w > 0:
                estimated = clamp_travel(track_w * 0.85, js_dist)
                logger.warning(f"[{self.pure_user_id}] estimated distance from track: {estimated:.0f}px")
                self._emit_telemetry_event("distance_detected", distance=estimated, source="track_estimate")
                return estimated
        except Exception:
            pass

        return None

    async def _calc_distance(self):
        logger.debug(f"[{self.pure_user_id}] calculating distance via image match...")
        await asyncio.sleep(0.5)
        try:
            bg = await self._query_in_challenge_scope(self.selectors["bg_img"])
            piece = await self._query_in_challenge_scope(self.selectors["piece_img"])
            if not bg or not piece:
                logger.debug(f"[{self.pure_user_id}] image selectors not found bg={bool(bg)} piece={bool(piece)}")
                return None
            bb = await bg.screenshot(type="png")
            pb = await piece.screenshot(type="png")
            if bb and pb:
                offset = self._calibration.get("offset_correction", -35)
                d = SliderImageMatcher.find_gap_from_bytes(bb, pb, offset)
                if d and d > 0:
                    logger.info(f"[{self.pure_user_id}] image match distance: {d} (offset={offset})")
                    self._emit_telemetry_event("distance_detected", distance=float(d), source="image_match")
                    return float(d)
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] image match error: {e}")
        return None

    async def _calc_distance_js(self):
        try:
            scope = self._challenge_scope()
            d = await scope.evaluate("""(selectors) => {
                const b = document.querySelector(selectors.slider_btn);
                const t = document.querySelector(selectors.slider_track);
                if (!b || !t) return {js_dist: 0};
                const bw = b.getBoundingClientRect();
                const tw = t.getBoundingClientRect();
                const st = document.querySelector('#nc_1__scale_text, .nc_scale_text, .nc-lang-cnt, [id*=scale_text]');
                return {
                    js_dist: tw.width - bw.width,
                    track_width: tw.width,
                    btn_width: bw.width,
                    scale_slider: !!st || !document.querySelector(selectors.piece_img),
                };
            }""", {
                "slider_btn": self.selectors["slider_btn"],
                "slider_track": self.selectors["slider_track"],
                "piece_img": self.selectors["piece_img"],
            })
            if isinstance(d, dict):
                dist = float(d.get("js_dist", 0))
                if dist > 0:
                    logger.info(f"[{self.pure_user_id}] JS dist={dist:.0f}px "
                                f"(track={d.get('track_width',0):.0f}, btn={d.get('btn_width',0):.0f}, "
                                f"scale={bool(d.get('scale_slider'))})")
                    self._scale_slider = bool(d.get("scale_slider"))
                    return dist
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] JS calc error: {e}")
        return None

    # ════════════════════════════════════════════════════════════
    #  校准管理（只读已有 calibration.json；JS 已改为上限夹紧，不再学习 offset）
    # ════════════════════════════════════════════════════════════
    def _calibration_path(self):
        return Path(self._config.get_calibration_dir()) / self.pure_user_id / "calibration.json"

    def _load_calibration(self) -> dict:
        p = self._calibration_path()
        if p.exists():
            try:
                with open(p, "r") as f:
                    data = json.load(f)
                raw = int(data.get("offset_correction", self.OFFSET_CORRECTION_DEFAULT))
                low, high = self.OFFSET_CORRECTION_LIMITS
                if not (low <= raw <= high):
                    logger.warning(
                        f"[{self.pure_user_id}] calibration offset {raw} out of "
                        f"{self.OFFSET_CORRECTION_LIMITS}, resetting to default"
                    )
                    data["offset_correction"] = self.OFFSET_CORRECTION_DEFAULT
                return data
            except Exception:
                pass
        return {"offset_correction": self.OFFSET_CORRECTION_DEFAULT}

    def _save_calibration(self):
        p = self._calibration_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self._calibration, f)

    # ════════════════════════════════════════════════════════════
    #  轨迹回放方法
    # ════════════════════════════════════════════════════════════
    async def _replay_recorded_cdp(self, points, sx, sy):
        cdp = getattr(self, "_cdp", None)
        if not cdp:
            return False
        try:
            ts_info = await self.page.evaluate("""() => ({
                timeOrigin: performance.timeOrigin,
                now: performance.now(),
            })""")
            base_ts = int(ts_info["timeOrigin"] + ts_info["now"])
        except Exception:
            base_ts = int(time.time() * 1000)

        if not points:
            return False

        try:
            # 事件顺序必须与真实拖拽一致：hover → pressed → moved×N → released；
            # 末点录制自 up 事件，是释放位，不当 move 派发。
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": sx, "y": sy,
                "movementX": 0, "movementY": 0,
                "pointerType": "mouse",
                "timestamp": base_ts,
            })
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": sx, "y": sy,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts,
            })

            total_ms = 0
            px, py = sx, sy
            for i, (dx, dy, delay_ms) in enumerate(points):
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)
                total_ms += delay_ms
                if i == len(points) - 1:
                    break
                tx, ty = sx + dx, sy + dy
                if abs(tx - px) < 0.5 and abs(ty - py) < 0.5:
                    continue
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved", "x": tx, "y": ty,
                    "movementX": tx - px, "movementY": ty - py,
                    "pointerType": "mouse",
                    "timestamp": base_ts + int(total_ms),
                })
                px, py = tx, ty

            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": sx + points[-1][0], "y": sy + points[-1][1],
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(total_ms),
            })
            return True
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] CDP replay error: {e}")
            return False

    async def _replay_recorded_playwright(self, points, sx, sy):
        try:
            await self.page.mouse.move(sx + random.uniform(-5, -1), sy + random.uniform(1, 4))
            await asyncio.sleep(random.uniform(0.05, 0.12))
            await self.page.mouse.move(sx, sy)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await self.page.mouse.down()
            # 录制回放同样需要"按下按住再拖"的真人停顿（看雪 284633 量级 1200ms）
            await asyncio.sleep(random.uniform(600, 1200) / 1000.0)

            for dx, dy, delay_ms in points:
                await self.page.mouse.move(sx + dx, sy + dy)
                await asyncio.sleep(delay_ms / 1000.0)

            # 释放前人手抖动：左右 ±2px 再回到释放位（对齐真人收尾）
            end_x = sx + points[-1][0]
            await self.page.mouse.move(end_x - random.uniform(1.5, 2.5), sy + points[-1][1])
            await asyncio.sleep(random.uniform(0.02, 0.05))
            await self.page.mouse.move(end_x + random.uniform(1.0, 2.0), sy + points[-1][1])
            await asyncio.sleep(random.uniform(0.02, 0.05))
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.up()
            return True
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] Playwright replay error: {e}")
            return False

    # ════════════════════════════════════════════════════════════
    #  浏览器初始化与页面加载
    # ════════════════════════════════════════════════════════════
    def _heal_stale_singleton_lock(self):
        """容器 recreate 时若上次验证还在跑，profile 卷里会留下指向旧容器
        hostname 的 SingletonLock——新 Chromium 拒绝启动且无对话框工具可
        提示（process_singleton_posix 'profile in use by another computer'）。
        这里在 launch 前自愈：锁目标 hostname 不是本机、或 pid 已无进程时清锁。"""
        try:
            profile = Path(str(self.profile_dir))
            lock = profile / "SingletonLock"
            if not lock.is_symlink():
                return
            target = os.readlink(str(lock))  # e.g. "oldhost-3486"
            host, _, pid_str = target.rpartition("-")
            stale = False
            if host and host != socket.gethostname():
                stale = True
            else:
                try:
                    stale = not psutil.pid_exists(int(pid_str))
                except ValueError:
                    stale = False
            if not stale:
                return
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                p = profile / name
                try:
                    if p.is_symlink() or p.exists():
                        p.unlink()
                except Exception:
                    pass
            logger.warning(
                f"[{self.pure_user_id}] removed stale chromium SingletonLock "
                f"(target={target!r}, hostname={socket.gethostname()!r})"
            )
        except Exception as e:
            logger.debug(f"[{self.pure_user_id}] singleton lock heal skipped: {e}")

    async def _init_browser(self):
        self.automation_backend = _resolve_automation_backend()
        self._emit_step("browser", "browser_init", "started", headless=self.headless, proxy_enabled=bool(self.proxy), backend=self.automation_backend)
        await ensure_profile_chromium_closed(str(self.profile_dir))
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        pw = await (patchright_async_playwright if self.automation_backend == "patchright" else async_playwright)().start()
        self._playwright = pw
        kwargs = {"headless": self.headless, "args": STEALTH_LAUNCH_ARGS}
        if self.automation_backend == "patchright":
            # patchright 自带反检测注入与 launch 参数处理；多余的 init_script/启动参数反而扩大指纹面
            kwargs.pop("args", None)
        proxy_host = self.proxy.get("proxy_host")
        proxy_port = self.proxy.get("proxy_port")
        if proxy_host and proxy_port:
            pt = str(self.proxy.get("proxy_type", "http")).lower()
            if pt in ("none", ""):
                pt = "http"
            kwargs["proxy"] = {"server": f"{pt}://{proxy_host}:{proxy_port}"}
        self._heal_stale_singleton_lock()
        self.context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            viewport={"width": 1920, "height": 1080},
            **kwargs,
        )
        self.page = await self.context.new_page()
        pid = find_chromium_pid_by_user_data_dir(str(self.profile_dir))
        if pid:
            record_chromium_pid(pid)
            self._browser_pid = pid

        if self.automation_backend != "patchright":
            await self.page.add_init_script(STEALTH_INIT_SCRIPT)
        await self._inject_cookies()
        self.page.on("response", self._on_response)
        self.page.on("console", self._on_console)
        async def _on_nav(frame):
            if frame == self.page.main_frame:
                logger.debug(f"[{self.pure_user_id}] page navigated: {frame.url[:100]}")
        self.page.on("framenavigated", _on_nav)
        self.page.on("close", lambda: logger.warning(f"[{self.pure_user_id}] page closed!"))
        if self.automation_backend == "patchright":
            # patchright 下禁止创建 CDP 会话：Runtime.enable 正是其要规避的检测特征
            self._cdp = None
            self._emit_step("browser", "cdp_session", "skipped", reason="patchright_backend")
        else:
            try:
                self._cdp = await self.page.context.new_cdp_session(self.page)
                logger.debug(f"[{self.pure_user_id}] CDP session ready")
                self._emit_step("browser", "cdp_session", "ok")
            except Exception:
                self._cdp = None
                logger.warning(f"[{self.pure_user_id}] CDP session failed")
                self._emit_step("browser", "cdp_session", "failed")
        self._emit_step("browser", "browser_init", "ok", profile_dir=str(self.profile_dir))

    async def _connect_existing_browser(self, cdp_endpoint: str, page_url: str = ""):
        """连接已有浏览器（CDP 模式）— 不启动新浏览器"""
        pw = await async_playwright().start()
        self._playwright = pw

        browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        if not browser.contexts:
            raise RuntimeError(f"No contexts found on CDP endpoint: {cdp_endpoint}")
        self.context = browser.contexts[0]

        if self.context.pages:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        await self.page.add_init_script(STEALTH_INIT_SCRIPT)
        self.page.on("response", self._on_response)
        self.page.on("close", lambda: logger.warning(f"[{self.pure_user_id}] page closed (external browser)!"))

        try:
            self._cdp = await self.context.new_cdp_session(self.page)
            logger.debug(f"[{self.pure_user_id}] CDP session ready (existing browser)")
        except Exception:
            self._cdp = None
            logger.warning(f"[{self.pure_user_id}] CDP session failed (existing browser)")

        if page_url:
            # CDP 模式也要带账号会话 cookie：punish 的 x5secdata 绑定 bot 侧会话，
            # 借外部真实浏览器（用户 PC Chrome）的是设备指纹，会话身份必须仍是
            # bot 账号——否则票据发到浏览器自己的会话上，bot 合并无效。
            try:
                await self._inject_cookies()
            except Exception as e:
                logger.warning(f"[{self.pure_user_id}] CDP cookie inject failed: {e}")
            await self._goto_page(self.page, page_url)
            await asyncio.sleep(3)

    async def _inject_cookies(self):
        if not self.cookies_str:
            return
        domain = cookie_domain_for_url(self._verify_url)
        cl = parse_cookie_header(self.cookies_str, domain)
        if cl:
            await self.context.add_cookies(cl)

    @staticmethod
    async def _goto_page(page, url: str, step_origin: str = "page"):
        """networkidle 优先；处罚页后台信标可能导致永不 idle，超时降级 domcontentloaded。
        页面是否真的渲染出滑块由后续 page_state 探测判定，导航等待不是成败判据。"""
        try:
            await page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception as nav_exc:
            if "Timeout" not in type(nav_exc).__name__ and "TimeoutError" not in str(type(nav_exc)):
                raise
            logger.warning(f"goto networkidle timeout, falling back to domcontentloaded: {url[:80]}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(8)

    async def _load_page(self, url):
        self._emit_step("page", "page_load", "started", verify_url=url)
        await self._goto_page(self.page, url)
        await asyncio.sleep(3)
        try:
            info = await self.page.evaluate("""(sel) => ({
                title: document.title,
                body_len: document.body ? document.body.innerHTML.length : 0,
                slider_btn_visible: !!document.querySelector(sel),
                all_divs: document.querySelectorAll("div").length,
                all_imgs: document.querySelectorAll("img").length,
                scripts: document.querySelectorAll("script").length,
            })""", self.selectors["slider_btn"])
            try:
                targets = await iter_search_targets(self.page)
                info["search_targets"] = len(targets)
                if not info.get("slider_btn_visible"):
                    handle, used = await query_in_targets(targets, self.selectors["slider_btn"])
                    if handle:
                        info["slider_btn_visible"] = True
                        self._slider_scope = used
            except Exception:
                pass
            logger.info(f"[{self.pure_user_id}] page state: {info}")
            self._emit_step("page", "page_state", "ok", **info)
        except Exception:
            self._emit_step("page", "page_state", "failed")
            pass
        self._emit_step("page", "page_load", "ok", verify_url=url)

    def _challenge_scope(self):
        return self._slider_scope or self.page

    def _slider_wait_selectors(self) -> List[str]:
        ordered = [self.selectors.get("slider_btn"), *(self.selectors.get("slider_alt") or ())]
        unique: List[str] = []
        for sel in ordered:
            if sel and sel not in unique:
                unique.append(sel)
        return unique

    async def _query_in_challenge_scope(self, selector: str):
        scope = self._challenge_scope()
        if scope is not None:
            try:
                handle = await scope.query_selector(selector)
                if handle:
                    return handle
            except Exception:
                pass
        if not self.page:
            return None
        try:
            handle, used = await query_in_targets(await iter_search_targets(self.page), selector)
        except Exception:
            return None
        if handle and used is not None:
            self._slider_scope = used
        return handle

    async def _wait_slider(self, timeout=15.0):
        logger.debug(f"[{self.pure_user_id}] waiting for slider (timeout={timeout}s)")
        if not self.page:
            return False
        selectors = self._slider_wait_selectors()
        if not selectors:
            return False
        try:
            targets = await iter_search_targets(self.page)
        except Exception:
            targets = [self.page]
        budget_ms = max(500.0, float(timeout) * 1000.0)
        per_selector = budget_ms / max(len(selectors), 1)
        for sel in selectors:
            handle, used = await wait_in_targets(targets, sel, timeout=per_selector)
            if handle:
                self._slider_scope = used
                if sel != self.selectors.get("slider_btn"):
                    logger.info(f"[{self.pure_user_id}] found slider via: {sel}")
                return True
        return False

    # ════════════════════════════════════════════════════════════
    #  滑动执行（统一入口）
    # ════════════════════════════════════════════════════════════
    async def _do_slide(self, distance, attempt, recorded_trajectory=None):
        btn = None
        try:
            btn = await self._query_in_challenge_scope(self.selectors["slider_btn"])
        except Exception:
            btn = None
        if not btn:
            logger.warning(f"[{self.pure_user_id}] slider button gone before slide")
            return

        box = await btn.bounding_box()
        if not box:
            return

        sx = box["x"] + box["width"] / 2 + random.uniform(-2.5, 2.5)
        sy = box["y"] + box["height"] / 2 + random.uniform(-2.5, 2.5)

        self._result_event.clear()
        self._slide_code = None
        self._slide_ok = None

        # ── 录制轨迹回放 ──
        if recorded_trajectory and recorded_trajectory.get("points"):
            points = recorded_trajectory["points"]
            logger.info(f"[{self.pure_user_id}] replaying recorded trajectory: "
                        f"dist={distance:.0f}px, {len(points)} points")

            scaled = points_from_recorded(recorded_trajectory, distance)
            if scaled:
                if scaled != points:
                    logger.info(f"[{self.pure_user_id}] scaled recorded trajectory to {distance:.0f}px")
                points = scaled

            cdp = getattr(self, "_cdp", None)
            if cdp:
                ok = await self._replay_recorded_cdp(points, sx, sy)
                if ok:
                    return
            await self._replay_recorded_playwright(points, sx, sy)
            return

        # ── 数学模型生成轨迹 ──
        cdp = getattr(self, "_cdp", None)
        if cdp is None:
            await self._slide_playwright(distance, attempt, btn, sx, sy)
            return

        traj = generate_trajectory(distance, attempt)
        logger.info(f"[{self.pure_user_id}] sliding (generated): dist={distance:.0f}px steps={len(traj)} from=({sx:.0f},{sy:.0f})")

        try:
            ts_info = await self.page.evaluate("""() => ({
                timeOrigin: performance.timeOrigin,
                now: performance.now(),
            })""")
            base_ts = int(ts_info["timeOrigin"] + ts_info["now"])
        except Exception:
            base_ts = int(time.time() * 1000)

        try:
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": sx, "y": sy,
                "movementX": 0, "movementY": 0,
                "pointerType": "mouse", "timestamp": base_ts,
            })
            px, py = sx, sy

            pct_traj = [t for t in traj if t[2] > 0]
            init_wait = pct_traj[0][2] / 1000.0 if pct_traj else 0.1
            await asyncio.sleep(init_wait)

            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": sx, "y": sy,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(init_wait * 1000),
            })

            total_ms = 0
            for i, (dx, dy, delay_ms) in enumerate(traj):
                if i == 0 and abs(dx) < 0.1 and abs(dy) < 0.1:
                    total_ms += delay_ms
                    continue
                tx = sx + dx
                ty = sy + dy
                mx = tx - px
                my = ty - py
                total_ms += delay_ms
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved", "x": tx, "y": ty,
                    "movementX": mx, "movementY": my,
                    "pointerType": "mouse",
                    "timestamp": base_ts + total_ms,
                })
                px, py = tx, ty
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

            release_wait = traj[-1][2] / 1000.0 if traj[-1][2] > 0 else 0.05
            await asyncio.sleep(release_wait)
            total_ms += traj[-1][2]

            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": px, "y": py,
                "button": "left", "clickCount": 1,
                "pointerType": "mouse",
                "timestamp": base_ts + int(total_ms),
            })
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] CDP failed: {e}, falling back")
            self._cdp = None
            await self._slide_playwright(distance, attempt, btn, sx, sy)

    async def _slide_playwright(self, distance, attempt, btn, sx, sy):
        try:
            sx2 = sx + random.uniform(-2, 2)
            sy2 = sy + random.uniform(-2, 2)
            # 按下后按住一段再拖 + 终点过冲回拖/释放抖动：对齐真人行为特征
            # （看雪 284633 成功案例：down 后 1200ms 才动 + 收尾回拖 ±2px）
            traj = generate_trajectory(
                distance, attempt,
                press_hold_ms=random.uniform(600, 1200),
                overshoot_back=True,
            )
            pts = trajectory_to_points(traj, sx2, sy2)
            await self.page.mouse.move(sx2 + random.uniform(-8, -3), sy2 + random.uniform(2, 6))
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.move(sx2, sy2)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await self.page.mouse.down()
            # pts[0] 是按下后按住不动的停顿（press_hold），从第 1 个位移点开始拖
            hold = pts[0][2] / 1000.0 if pts else 0.8
            await asyncio.sleep(hold)
            for x, y, d in pts[1:]:
                await self.page.mouse.move(x, y)
                await asyncio.sleep(d / 1000.0)
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self.page.mouse.up()
        except Exception as e:
            logger.warning(f"[{self.pure_user_id}] Playwright slide error: {e}")

    # ════════════════════════════════════════════════════════════
    #  结果监听与Cookie获取
    # ════════════════════════════════════════════════════════════
    async def _on_response(self, response):
        url = response.url
        # 旁路抓票据头（0.6.10）：优先于 /slide JSON——checkCookie 不跑时这是唯一来源。
        # 0.6.11：response.headers 是同步子集，bx-* 可能只在 all_headers() 里。
        try:
            if self._bx_voucher is None and ("_____tmd_____" in url or "/slide" in url):
                hdr = response.headers.get("bx-x5sec") or response.headers.get("bx-x5sec-root")
                if not hdr:
                    try:
                        allh = await response.all_headers()
                        hdr = allh.get("bx-x5sec") or allh.get("bx-x5sec-root")
                    except Exception:
                        allh = {}
                if hdr and "x5sec=" in hdr:
                    self._bx_voucher = hdr
                    logger.info(f"[{self.pure_user_id}] bx voucher header captured from {url[:120]}")
                    self._emit_telemetry_event(
                        "bx_voucher_captured",
                        response_url=url[:200],
                        from_root="bx-x5sec-root" in (allh if not hdr else {}) or False,
                    )
        except Exception:
            pass
        # 报文回执旁听（0.6.11）：checkCookie 会向 /report 发 setCookieSuccess/
        # setCookieFail——通过后如果页面 JS 正常工作，这里能听到成败
        try:
            if "_____tmd_____/report" in url and "setCookie" in (url or ""):
                logger.info(f"[{self.pure_user_id}] checkCookie report: {url[:200]}")
                self._emit_telemetry_event("checkcookie_report", response_url=url[:200])
        except Exception:
            pass
        patterns = self.selectors["result_url_pattern"]
        if any(pat in url for pat in patterns):
            try:
                body = await response.body()
                text = body.decode("utf-8", errors="ignore")
                # 前 200 字节落日志：code=-1 时可直接判断捕获的是最终校验包
                # （阿里 100/900 语义）还是中间探测包——决定轨迹层 vs 结果层的排查方向
                logger.info(f"[{self.pure_user_id}] tmd slide body[:200]: {text[:200]!r}")
                data = json.loads(text)
                success_code = self.selectors.get("success_code", 0)
                ok = interpret_slide_json(data, success_code=success_code)
                if ok is None:
                    return
                code = data.get("code") if isinstance(data, dict) else None
                logger.info(f"[{self.pure_user_id}] SLIDE RESPONSE: ok={ok} code={code}")
                self._emit_telemetry_event(
                    "slide_result",
                    slide_ok=ok,
                    slide_code=code,
                    response_url=url[:200],
                )
                self._slide_ok = ok
                self._slide_code = -1 if code is None else code
                self._result_event.set()
            except Exception:
                pass

    def _on_console(self, msg):
        """阿里新前端（CAPTCHA V3）成功标志走 console/前端回调而非 _____tmd_____/slide
        响应（mucsbr/aliyun-captcha-fake 同款捕获面），这里做兜底成功信号。"""
        try:
            text = msg.text or ""
        except Exception:
            return
        if ("验证通过" in text) or ("captchaVerifyParam" in text):
            logger.info(f"[{self.pure_user_id}] console success marker: {text[:200]!r}")
            self._emit_telemetry_event(
                "slide_console_success",
                marker=text[:200],
            )
            self._slide_ok = True
            self._result_event.set()

    async def _wait_result(self, timeout=5.0):
        try:
            await asyncio.wait_for(self._result_event.wait(), timeout=timeout)
            return self._slide_code if self._slide_code is not None else -1
        except asyncio.TimeoutError:
            return -1
        except Exception:
            return -1
    async def _wait_slide_outcome(self, timeout=5.0, success_code=0):
        """等滑块校验包：success 标志优先于 code。超时视为失败。"""
        try:
            await asyncio.wait_for(self._result_event.wait(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            # 结果捕获面 miss（patchright 下 page.on("console") 死、response 只见
            # 主进程 HTTP）：读回页面内 tap 的 fetch/XHR/beacon/WS/console 记录
            await self._dump_net_tap(self.page)
            return False, -1
        if self._slide_ok is not None:
            code = self._slide_code if self._slide_code is not None else -1
            return bool(self._slide_ok), code
        code = self._slide_code if self._slide_code is not None else -1
        return code == success_code, code

    async def _save_debug_screenshot(self, tag):
        try:
            import datetime as dt_mod
            debug_dir = Path(self._config.get_debug_screenshot_dir())
            debug_dir.mkdir(parents=True, exist_ok=True)
            ts = dt_mod.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / "{}_{}_{}.png".format(self.pure_user_id, tag, ts)
            # 死驱动连接上 page.screenshot 可能永不返回（生产挂死点之一），加预算
            shot = await await_with_budget(
                self.page.screenshot(path=str(path), full_page=False),
                self.SCREENSHOT_BUDGET_S,
            )
            if shot is None:
                logger.warning("[{}] screenshot timed out (budget {}s)".format(
                    self.pure_user_id, self.SCREENSHOT_BUDGET_S))
                return
            logger.info("[{}] screenshot saved: {}".format(self.pure_user_id, path))
        except Exception as e:
            logger.warning("[{}] screenshot failed: {}".format(self.pure_user_id, e))

    async def _get_cookies(self):
        self._emit_step("cookies", "cookie_snapshot", "started", context_available=bool(self.context), cdp_available=bool(getattr(self, "_cdp", None)))
        try:
            jar = []
            if self.context:
                jar.extend(await self.context.cookies() or [])
            cdp = getattr(self, "_cdp", None)
            if cdp:
                try:
                    response = await cdp.send("Network.getAllCookies")
                    jar.extend(response.get("cookies", []) if isinstance(response, dict) else [])
                except Exception as e:
                    logger.debug(f"[{self.pure_user_id}] CDP cookie snapshot failed: {e}")
                    self._emit_step("cookies", "cdp_cookie_snapshot", "failed", reason=str(e))
            page_url = self._verify_url or (getattr(self.page, "url", "") if self.page else "")
            cookies = select_cookies_for_url(jar, page_url)
            self._emit_step("cookies", "cookie_snapshot", "ok", cookie_names=sorted(cookies.keys()))
            return cookies
        except Exception as e:
            self._emit_step("cookies", "cookie_snapshot", "failed", reason=str(e))
            return {}

    def _requires_validation_cookie(self, verify_url: str) -> bool:
        try:
            parsed = urlparse(verify_url or "")
            text = f"{parsed.netloc}{parsed.path}".lower()
            query = parse_qs(parsed.query or "", keep_blank_values=True)
        except Exception:
            lowered = (verify_url or "").lower()
            return "punish" in lowered and any(
                token in lowered for token in ("x5secdata", "x5step", "action=captcha", "purecaptcha")
            )

        if "goofish.com" not in text and "taobao" not in text:
            return False
        if "punish" not in text:
            return False
        return any(key.lower() in {"x5secdata", "x5step", "action", "purecaptcha"} for key in query)

    @staticmethod
    def _has_validation_cookie(cookies: Optional[Dict[str, str]]) -> bool:
        return bool((cookies or {}).get("x5sec"))

    # ════════════════════════════════════════════════════════════
    #  清理
    # ════════════════════════════════════════════════════════════
    async def close(self):
        """公共清理入口 — 根据模式选择正确的清理路径"""
        # 调用 provider 清理钩子
        if self._provider:
            try:
                await self._provider.on_cleanup()
            except Exception as e:
                logger.debug(f"[{self.pure_user_id}] provider cleanup error: {e}")

        if self._is_cdp_mode:
            await self._close_cdp_only()
        else:
            await self._close()

    async def _hard_kill_browser(self):
        """OS 级兜底回收：断开驱动 + 按 profile 杀掉全部 chromium 进程。

        供 solve 看门狗超时路径使用：被取消/遗弃的求解任务其 finally 清理可能
        没跑完，协议层 close 不可信时只有进程级回收能保证 chromium 不驻留
        （1GB 内存机器上一个僵尸 chromium 就能拖死整机）。
        """
        try:
            if self._playwright:
                await await_with_budget(self._playwright.stop(), 10.0)
        except Exception:
            pass
        self._playwright = None
        self.context = None
        pids = set(find_chromium_pids_by_user_data_dir(str(self.profile_dir)))
        if self._browser_pid:
            pids.add(self._browser_pid)
        for pid in pids:
            try:
                kill_chromium_process_tree(pid)
            except Exception:
                pass
        self._browser_pid = None

    async def _close(self):
        pid = self._browser_pid or find_chromium_pid_by_user_data_dir(str(self.profile_dir))
        try:
            if self.context:
                await await_with_budget(self.context.close(), self.CLOSE_TIMEOUT_S)
        except Exception:
            pass
        self.context = None
        try:
            if self._playwright:
                await await_with_budget(self._playwright.stop(), self.CLOSE_TIMEOUT_S)
        except Exception:
            pass
        self._playwright = None
        if pid:
            try:
                kill_chromium_process_tree(pid)
            except Exception:
                pass
        try:
            await ensure_profile_chromium_closed(str(self.profile_dir))
        except Exception:
            pass
        self._browser_pid = None
        self._cleanup_profiles()

    async def _close_cdp_only(self):
        """CDP 模式清理 — 不关闭外部浏览器，只断开连接"""
        if self._cdp:
            try:
                await await_with_budget(self._cdp.detach(), self.CLOSE_TIMEOUT_S)
            except Exception:
                pass
            self._cdp = None
        if self._playwright:
            try:
                await await_with_budget(self._playwright.stop(), self.CLOSE_TIMEOUT_S)
            except Exception:
                pass
            self._playwright = None

    def _cleanup_profiles(self):
        try:
            parent = self.profile_dir.parent
            if not parent.exists():
                return
            cutoff = time.time() - 86400
            for d in parent.iterdir():
                if d.is_dir() and d.name.startswith("slider_"):
                    try:
                        if os.path.getmtime(str(d)) < cutoff:
                            shutil.rmtree(str(d), ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass


__all__ = ["SliderSolver", "DEFAULT_SELECTORS"]
