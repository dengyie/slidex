#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单次手动 Cookie 滑块调试入口"""

import argparse
import os
import sys
import time
from pathlib import Path


def _read_cookie(args: argparse.Namespace) -> str:
    if args.cookie:
        return args.cookie.strip().lstrip("﻿")
    if args.cookie_file:
        return Path(args.cookie_file).read_text(encoding="utf-8-sig").strip().lstrip("﻿")
    raise ValueError("必须提供 --cookie 或 --cookie-file")


def main() -> int:
    parser = argparse.ArgumentParser(description="单次手动 Cookie 滑块调试入口")
    parser.add_argument("--url", help="当前触发滑块的 verification_url；不传则先用 Cookie 预检拿最新的")
    parser.add_argument("--cookie-id", default="manual_cookie_debug", help="日志里的账号标识")
    parser.add_argument("--cookie", help="完整 Cookie 字符串")
    parser.add_argument("--cookie-file", help="包含完整 Cookie 字符串的文本文件")
    parser.add_argument("--headless", action="store_true", help="强制无头")
    parser.add_argument("--browser-channel", help="例如 msedge / chrome")
    parser.add_argument("--browser-path", help="指定本机浏览器 executable_path")
    parser.add_argument("--max-retries", type=int, default=4, help="最大滑块尝试次数")
    parser.add_argument("--open-only", action="store_true", help="只打开页面并保存快照")
    parser.add_argument("--open-wait", type=float, default=6.0, help="open-only 模式下页面停留秒数")
    parser.add_argument("--proxy-type", default="none", help="none/http/https/socks5")
    parser.add_argument("--proxy-host", default="", help="代理地址")
    parser.add_argument("--proxy-port", type=int, default=0, help="代理端口")
    parser.add_argument("--proxy-user", default="", help="代理用户名")
    parser.add_argument("--proxy-pass", default="", help="代理密码")
    args = parser.parse_args()

    cookie = _read_cookie(args)
    proxy = {
        "proxy_type": args.proxy_type,
        "proxy_host": args.proxy_host,
        "proxy_port": args.proxy_port,
        "proxy_user": args.proxy_user,
        "proxy_pass": args.proxy_pass,
    }

    from slidex.stealth import XianyuSliderStealth, resolve_verification_url_from_cookie

    target_url = args.url
    if not target_url:
        target_url = resolve_verification_url_from_cookie(cookie, proxy)
    print(f"verification_url={target_url}")
    print(f"cookie_id={args.cookie_id}")
    print(f"headless={args.headless}")
    print(f"browser_channel={args.browser_channel or ''}")
    print(f"browser_path={args.browser_path or ''}")
    print(f"max_retries={args.max_retries}")
    print(f"open_only={args.open_only}")

    slider = XianyuSliderStealth(
        user_id=args.cookie_id,
        enable_learning=True,
        headless=args.headless,
        initial_cookies=cookie,
        proxy=proxy,
        browser_channel=args.browser_channel or None,
        executable_path=args.browser_path or None,
        slider_max_retries=args.max_retries,
    )

    if args.open_only:
        print("phase=init_browser")
        slider.init_browser()
        print("phase=warmup")
        slider._warmup_slider_context(target_url)
        print("phase=goto")
        slider.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        print("phase=save_snapshot")
        slider._save_debug_snapshot("open_only", slider.page)
        print(f"phase=wait seconds={args.open_wait}")
        time.sleep(max(0.0, args.open_wait))
        print("phase=close")
        slider.close_browser()
        print("success=False")
        return 1

    print("phase=run")
    success, cookies = slider.run(target_url)
    print("phase=run_done")
    print(f"success={success}")
    if cookies:
        print(f"cookie_count={len(cookies)}")
    return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"debug_manual_cookie_slider failed: {exc}", file=sys.stderr)
        raise
