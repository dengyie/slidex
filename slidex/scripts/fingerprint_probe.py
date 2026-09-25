#!/usr/bin/env python3
"""容器浏览器指纹探针：按 solver 同款配置启动一次性浏览器（或连接外部真机），
把 UA/brands/WebGL 渲染串/字体/时区等硬信号以 JSON 输出。

用法：
    python slidex/scripts/fingerprint_probe.py                    # 容器模式（solver 同款 channel/旗标/后端）
    python slidex/scripts/fingerprint_probe.py --headless         # 强制无头（默认有头，需 DISPLAY）
    python slidex/scripts/fingerprint_probe.py --connect-cdp http://127.0.0.1:9222
                                                                  # 对照组：只读审计外部真机浏览器

用途背景：容器路径滑块被拒定位为环境指纹问题（真人拖也 code=300）。本探针
用于量化"容器浏览器 vs 真机 Chrome"的指纹差异，指导加固迭代；不触碰任何
业务页面，只对 about:blank 做一次只读 evaluate。
"""
import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from slidex.solver import SliderSolver, _resolve_automation_backend, _resolve_browser_channel  # noqa: E402
from slidex._stealth_patch import STEALTH_LAUNCH_ARGS, ENV_CONSISTENCY_LAUNCH_ARGS  # noqa: E402


async def _probe_local(headless: bool) -> dict:
    backend = _resolve_automation_backend()
    channel = _resolve_browser_channel()
    if backend == "patchright":
        from patchright.async_api import async_playwright
    else:
        from playwright.async_api import async_playwright

    args = (list(ENV_CONSISTENCY_LAUNCH_ARGS) if backend == "patchright"
            else list(STEALTH_LAUNCH_ARGS) + list(ENV_CONSISTENCY_LAUNCH_ARGS))
    kwargs = {"headless": headless, "args": args}
    if channel:
        kwargs["channel"] = channel
    profile_dir = tempfile.mkdtemp(prefix="slidex-fp-probe-")
    print(f"# backend={backend} channel={channel or 'bundled-chromium'} headless={headless}", file=sys.stderr)
    pw = await async_playwright().start()
    try:
        ctx = await pw.chromium.launch_persistent_context(user_data_dir=profile_dir, **kwargs)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return await page.evaluate(SliderSolver._FINGERPRINT_AUDIT_JS)
    finally:
        try:
            await pw.stop()
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


async def _probe_cdp(endpoint: str) -> dict:
    from playwright.async_api import async_playwright

    print(f"# connect-cdp={endpoint}", file=sys.stderr)
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(endpoint, timeout=20000)
        if not browser.contexts:
            raise RuntimeError(f"no contexts on {endpoint}")
        page = await browser.contexts[0].new_page()
        try:
            return await page.evaluate(SliderSolver._FINGERPRINT_AUDIT_JS)
        finally:
            await page.close()
    finally:
        try:
            await pw.stop()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="slidex browser fingerprint probe")
    ap.add_argument("--headless", action="store_true", help="force headless launch")
    ap.add_argument("--connect-cdp", dest="connect_cdp", default="",
                    help="audit an external browser via CDP endpoint instead of launching one")
    ap.add_argument("--json-out", dest="json_out", default="", help="also write JSON to this path")
    ns = ap.parse_args()

    info = asyncio.run(_probe_cdp(ns.connect_cdp) if ns.connect_cdp else _probe_local(not ns.headless))
    text = json.dumps(info, ensure_ascii=False, indent=2)
    print(text)
    if ns.json_out:
        Path(ns.json_out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
