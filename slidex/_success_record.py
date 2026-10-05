"""成功链路记录（schema v1）：每次通过风控后的完整环境快照。

需求（2026-10-05）：每次成功过风控都要完整记录"指纹链路"，后续进数据库。
哪些信息值得留：什么样的浏览器指纹（UA/brands/WebGL 渲染串/字体/时区/并发数）
× 什么执行环境（CDP 真机 / 容器、backend、channel、出口代理）× 什么轨迹来源
能通过当时的风控——成功样本是后续失败对照与风控策略变化检测的基线数据。

成功出口（solver 五处 return True 前标记 ``_success_outcome``）：
provider_pass / legacy_pass / voucher_harvest / manual_pass_wait / jar_landed。
求解入口在成功返回前统一组装 success_record：

- 指纹在**成功时**于活页面补采一次（只读 evaluate，页面已通过、无检测风险；
  CDP 模式也采——真机指纹正是最宝贵的对照组）。采集失败回退 browser_init
  时的审计快照（容器模式）。
- 滑块/票据信息取自遥测摘要与事件（distance/provider/slide_code/x5sec 结算来源）。

持久化三通道（宿主读 1，2/3 为 slidex 独立运行时的落盘与导出口）：
1. ``solver.success_record`` 属性 —— 宿主同步读取，随 Cookie 合并结果写库
2. telemetry 摘要 ``{run_id}.json`` 的 ``success`` 块
3. ``successes.jsonl``（telemetry 目录，append-only，一行一次成功）

schema 字段即未来主仓 ``slider_success_records`` 表的 ingest 契约，改动需升版本。
"""

from __future__ import annotations

import asyncio
import json
import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1

# 指纹审计 JS（0.6.22 引入；此处为唯一事实源，solver 引用同一常量）
FINGERPRINT_AUDIT_JS = r"""
(async () => {
  const out = {};
  out.ua = navigator.userAgent;
  out.webdriver = navigator.webdriver;
  out.platform = navigator.platform;
  out.languages = (navigator.languages || []).join(',');
  out.hwConcurrency = navigator.hardwareConcurrency;
  out.deviceMemory = navigator.deviceMemory;
  out.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  out.tzOffset = new Date().getTimezoneOffset();
  out.screen = screen.width + 'x' + screen.height + '@' + screen.colorDepth + ' dpr=' + devicePixelRatio;
  out.viewport = innerWidth + 'x' + innerHeight;
  out.plugins = navigator.plugins ? navigator.plugins.length : -1;
  out.pdfViewer = !!navigator.pdfViewerEnabled;
  out.chromeKeys = (window.chrome && typeof window.chrome === 'object') ? Object.keys(window.chrome).join('|') : '';
  try {
    const uad = navigator.userAgentData;
    if (uad) {
      out.brands = (uad.brands || []).map(b => b.brand + ' ' + b.version).join(' / ');
      out.uadPlatform = uad.platform;
      const he = await uad.getHighEntropyValues(['platformVersion', 'architecture', 'bitness', 'model', 'uaFullVersion']);
      out.platformVersion = he.platformVersion;
      out.arch = he.architecture;
      out.bitness = he.bitness;
      out.uaFullVersion = he.uaFullVersion;
    }
  } catch (e) { out.uadError = String(e).slice(0, 80); }
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl2') || c.getContext('webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      out.glVendor = dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
      out.glRenderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    } else { out.glRenderer = '(no webgl)'; }
  } catch (e) { out.glRenderer = 'err:' + String(e).slice(0, 60); }
  try {
    const probe = ['Segoe UI', 'Microsoft YaHei', 'SimSun', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'Arial', 'Times New Roman', 'Helvetica', 'Roboto', 'Ubuntu', 'DejaVu Sans', 'Liberation Sans'];
    const s = document.createElement('span');
    s.style.cssText = 'position:absolute;visibility:hidden;font-size:48px';
    s.textContent = 'mmmmmmmmmmlli';
    document.body.appendChild(s);
    const base = {};
    for (const b of ['monospace', 'serif', 'sans-serif']) { s.style.fontFamily = b; base[b] = s.offsetWidth; }
    const found = [];
    for (const f of probe) {
      for (const b of ['monospace', 'serif', 'sans-serif']) {
        s.style.fontFamily = f + ',' + b;
        if (s.offsetWidth !== base[b]) { found.push(f); break; }
      }
    }
    s.remove();
    out.fonts = found.join('|') || '(none)';
  } catch (e) { out.fonts = 'err:' + String(e).slice(0, 60); }
  return out;
})()
"""


async def capture_fingerprint(page: Any, timeout_s: float = 5.0) -> Optional[Dict[str, Any]]:
    """在活页面上只读采集指纹快照；失败返回 None（绝不影响求解结果）。"""
    if page is None:
        return None
    try:
        info = await asyncio.wait_for(page.evaluate(FINGERPRINT_AUDIT_JS), timeout=timeout_s)
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def _slidex_version() -> str:
    try:
        from importlib.metadata import version
        return version("slidex")
    except Exception:
        return "unknown"


def build_success_record(
    solver: Any,
    outcome: str,
    cookies: Optional[Dict[str, Any]],
    fingerprint: Optional[Dict[str, Any]],
    duration_s: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装 schema v1 成功记录。solver 需暴露遥测/环境属性（均为 getattr 容错）。"""
    summary = dict(getattr(solver, "_telemetry_summary", {}) or {})
    slide_keys = ("distance", "distance_source", "provider_name", "slide_code",
                  "fallback_used", "remote_session_id")
    settle_source = None
    for ev in getattr(solver, "_telemetry_events", []) or []:
        if isinstance(ev, dict) and ev.get("event") == "x5sec_settled":
            settle_source = ev.get("source")
    x5sec_len = 0
    if isinstance(cookies, dict):
        x5sec_len = len(str(cookies.get("x5sec") or ""))
    is_cdp = bool(getattr(solver, "_is_cdp_mode", False))
    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": getattr(solver, "_telemetry_run_id", None),
        "cookie_id": getattr(solver, "cookie_id", None),
        "pure_user_id": getattr(solver, "pure_user_id", None),
        "ts": time.time(),
        "duration_s": round(duration_s, 2) if isinstance(duration_s, (int, float)) else None,
        "outcome": outcome,
        "slide": {k: summary.get(k) for k in slide_keys if summary.get(k) is not None},
        "voucher": {
            "bx_voucher_present": bool(getattr(solver, "_bx_voucher", None)),
            "x5sec_len": x5sec_len,
            "settle_source": settle_source,
        },
        "fingerprint": fingerprint or {},
        "environment": {
            "backend": getattr(solver, "automation_backend", None) or ("playwright" if is_cdp else None),
            "channel": getattr(solver, "browser_channel", None) or ("bundled-chromium" if not is_cdp else None),
            "mode": getattr(solver, "_solve_mode", None) or ("cdp" if is_cdp else "container"),
            "headless": bool(getattr(solver, "headless", None)),
            "proxy_enabled": bool(getattr(solver, "proxy", None)),
            "trajectory_mode": getattr(solver, "trajectory_mode", None),
            "slidex_version": _slidex_version(),
            "python": platform.python_version(),
        },
        "extra": dict(extra or {}),
    }
    return record


def persist_success_record(telemetry_dir: Any, record: Dict[str, Any]) -> Optional[str]:
    """append-only 落盘 successes.jsonl；失败静默（记录永不影响求解）。"""
    try:
        base = Path(telemetry_dir)
        base.mkdir(parents=True, exist_ok=True)
        path = base / "successes.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception:
        return None
