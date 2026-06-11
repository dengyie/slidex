#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slidex 验证 Demo
用法: python -m demo.verify --cookie-str "..." [--rounds 5] [--headless]
"""

import asyncio
import argparse
import os
import time
from datetime import datetime
from pathlib import Path

from slidex import SlidexConfig, SliderSolver


def get_verification_url(cookie_text: str) -> str:
    """从 Cookie 预检验证 URL（需要 aiohttp）"""
    try:
        from slidex.stealth import resolve_verification_url_from_cookie
    except ImportError:
        raise RuntimeError("resolve_verification_url_from_cookie requires aiohttp. Install with: pip install aiohttp aiohttp-socks")

    proxy_type = os.environ.get("PROXY_TYPE", "none")
    proxy_host = os.environ.get("PROXY_HOST", "")
    proxy_port = int(os.environ.get("PROXY_PORT", "0") or "0")

    proxy = {}
    if proxy_type not in ("none", "") and proxy_host and proxy_port:
        proxy = {"proxy_type": proxy_type, "proxy_host": proxy_host, "proxy_port": proxy_port}

    print(f"  [pre] probing verification URL...")
    url = resolve_verification_url_from_cookie(cookie_text, proxy=proxy)
    print(f"  [pre] verification_url = {url}")
    return url


async def run_single_round(cookie_id: str, cookies_str: str, round_num: int,
                           headless: bool, config: SlidexConfig) -> dict:
    proxy_type = os.environ.get("PROXY_TYPE", "none")
    proxy_host = os.environ.get("PROXY_HOST", "")
    proxy_port = int(os.environ.get("PROXY_PORT", "0") or "0")

    proxy = {}
    if proxy_type not in ("none", "") and proxy_host and proxy_port:
        proxy = {"proxy_type": proxy_type, "proxy_host": proxy_host, "proxy_port": proxy_port}

    try:
        verify_url = get_verification_url(cookies_str)
    except RuntimeError as e:
        msg = str(e)
        if "cookie_valid" in msg.lower():
            return {"round": round_num, "status": "cookie_valid", "error": msg[:100]}
        raise

    solver = SliderSolver(
        cookie_id=cookie_id,
        cookies_str=cookies_str,
        headless=headless,
        proxy=proxy,
        config=config,
    )

    print(f"  [{round_num}] solving slider...")
    start = time.time()
    success, cookies = await solver.solve(verify_url)
    elapsed = time.time() - start

    return {
        "round": round_num,
        "status": "pass" if success else "fail",
        "elapsed": round(elapsed, 1),
        "cookies_count": len(cookies) if cookies else 0,
        "verify_url": verify_url,
    }


async def main():
    parser = argparse.ArgumentParser(description="Slidex Slider Captcha Demo")
    parser.add_argument("--cookie-str", default=os.environ.get("SLIDEX_COOKIE_STR", ""),
                        help="Cookie string (or set SLIDEX_COOKIE_STR env)")
    parser.add_argument("--cookie-file", help="Read cookie string from file")
    parser.add_argument("--cookie-id", default="demo_user", help="Cookie/user identifier")
    parser.add_argument("--rounds", type=int, default=1, help="Test rounds (1-30)")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument("--verify-url", help="Skip probing, use this URL directly")
    args = parser.parse_args()

    # Resolve cookie string
    cookies_str = args.cookie_str
    if args.cookie_file:
        cookies_str = Path(args.cookie_file).read_text(encoding="utf-8-sig").strip()
    if not cookies_str:
        parser.error("Must provide --cookie-str, --cookie-file, or SLIDEX_COOKIE_STR env var")

    rounds = max(1, min(args.rounds, 30))
    config = SlidexConfig.from_env()

    print(f"=== Slidex Slider Captcha Demo ===")
    print(f"  cookie_id: {args.cookie_id}")
    print(f"  rounds: {rounds}")
    print(f"  headless: {args.headless}")
    print(f"  cookie_len: {len(cookies_str)} chars")

    # Resolve verification URL
    if args.verify_url:
        verify_url = args.verify_url
        print(f"  verify_url: {verify_url}")
    else:
        try:
            verify_url = get_verification_url(cookies_str)
        except RuntimeError:
            print("  Cannot probe URL (aiohttp not available). Use --verify-url instead.")
            return 1

    results = []
    for r in range(1, rounds + 1):
        print(f"--- Round {r}/{rounds} ---")
        try:
            solver = SliderSolver(
                cookie_id=args.cookie_id,
                cookies_str=cookies_str,
                headless=args.headless,
                config=config,
            )
            start = time.time()
            success, cookies = await solver.solve(verify_url)
            elapsed = time.time() - start
            result = {
                "round": r,
                "status": "pass" if success else "fail",
                "elapsed": round(elapsed, 1),
                "cookies_count": len(cookies) if cookies else 0,
            }
            results.append(result)
        except Exception as e:
            print(f"  [{r}] ERROR: {e}")
            results.append({"round": r, "status": "error", "error": str(e)[:200]})

        status = results[-1]["status"]
        elapsed = results[-1].get("elapsed", 0)
        print(f"  [{r}] result: {status} ({elapsed}s)")

        if r < rounds:
            wait = 3
            print(f"  cooling {wait}s...")
            await asyncio.sleep(wait)

    # Summary
    total = len(results)
    passes = sum(1 for r in results if r["status"] == "pass")
    fails = sum(1 for r in results if r["status"] == "fail")
    errors = sum(1 for r in results if r["status"] == "error")

    print("=" * 50)
    if total > 0:
        print(f"SUMMARY: {passes}/{total} passed ({passes/total*100:.1f}%)")
    print(f"  pass: {passes}, fail: {fails}, error: {errors}")
    if passes + fails > 0:
        print(f"  effective pass rate: {passes}/{passes+fails} = {passes/(passes+fails)*100:.1f}%")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
