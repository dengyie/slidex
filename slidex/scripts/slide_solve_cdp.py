#!/usr/bin/env python3
"""通过 CDP 连接已有浏览器求解滑块验证码

用法:
    python -m slidex.scripts.slide_solve_cdp \
        --cdp-endpoint ws://localhost:9222/devtools/browser/xxx \
        --page-url "https://..." \
        --selectors '{"slider_btn": ".geetest_slider_button", ...}'
"""

import argparse
import asyncio
import json
import sys
import time
from typing import Dict, Any, Optional

from loguru import logger


def main():
    parser = argparse.ArgumentParser(
        description="连接已有浏览器（CDP）求解滑块验证码",
    )
    parser.add_argument(
        "--cdp-endpoint", required=True,
        help="CDP WebSocket 地址，如 ws://localhost:9222/devtools/browser/xxx",
    )
    parser.add_argument(
        "--page-url", default="",
        help="验证码页面 URL（如果需要先导航）",
    )
    parser.add_argument(
        "--selectors", default=None,
        help="JSON 格式的选择器配置，覆盖默认 Aliyun 选择器",
    )
    parser.add_argument(
        "--trajectory-mode", default="auto",
        choices=["auto", "recorded", "generated"],
        help="轨迹模式 (默认: auto)",
    )
    parser.add_argument(
        "--cookie-id", default="default",
        help="用户标识符 (默认: default)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="启用详细日志",
    )
    args = parser.parse_args()

    if not args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    selectors: Optional[Dict[str, Any]] = None
    if args.selectors:
        try:
            selectors = json.loads(args.selectors)
        except json.JSONDecodeError as e:
            print(json.dumps({"success": False, "error": f"Invalid selectors JSON: {e}"}))
            sys.exit(1)

    result = asyncio.run(_run(
        cdp_endpoint=args.cdp_endpoint,
        page_url=args.page_url,
        selectors=selectors,
        trajectory_mode=args.trajectory_mode,
        cookie_id=args.cookie_id,
    ))

    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["success"] else 1)


async def _run(
    cdp_endpoint: str,
    page_url: str,
    selectors: Optional[Dict[str, Any]],
    trajectory_mode: str,
    cookie_id: str,
) -> Dict[str, Any]:
    from slidex.solver import SliderSolver

    start = time.time()
    solver = SliderSolver(
        cookie_id=cookie_id,
        headless=True,
        trajectory_mode=trajectory_mode,
        selectors=selectors,
    )

    try:
        success, cookies = await solver.solve_on_existing_page(
            cdp_endpoint=cdp_endpoint,
            page_url=page_url,
        )
        elapsed_ms = (time.time() - start) * 1000
        return {
            "success": success,
            "cookies": cookies,
            "elapsed_ms": round(elapsed_ms, 1),
            "error": None if success else "solve_failed",
        }
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        return {
            "success": False,
            "cookies": None,
            "elapsed_ms": round(elapsed_ms, 1),
            "error": str(e),
        }
    finally:
        await solver.close()


if __name__ == "__main__":
    main()
