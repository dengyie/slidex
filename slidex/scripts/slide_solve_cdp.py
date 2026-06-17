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
from pathlib import Path
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
    from slidex.vision import ChallengeType, VisionArtifact, VisualChallengeResult

    async def _resolve_telemetry() -> Dict[str, Any]:
        telemetry = solver.get_telemetry_summary()
        if asyncio.iscoroutine(telemetry):
            telemetry = await telemetry
        return telemetry if isinstance(telemetry, dict) else {}

    def _serialize_result(
        *,
        success: bool,
        cookies: Optional[Dict[str, str]],
        elapsed_ms: float,
        error_code: Optional[str],
        telemetry: Dict[str, Any],
    ) -> Dict[str, Any]:
        run_id = str(telemetry.get("run_id") or "unknown")
        result = VisualChallengeResult(
            success=success,
            challenge_type=ChallengeType.SLIDER_CAPTCHA,
            provider=str(telemetry.get("provider_name") or "auto"),
            confidence=float(telemetry.get("confidence") or 0.0),
            duration_ms=round(elapsed_ms, 1),
            error_code=error_code,
            retryable=not success,
            cookies=cookies,
            artifacts=[
                VisionArtifact(
                    artifact_type="telemetry",
                    path=Path("telemetry") / f"{run_id}.json",
                    metadata={"run_id": run_id},
                )
            ],
            metadata={"telemetry": telemetry},
        )
        payload = result.to_dict()
        # Backward-compatible runtime output: callers still need solved cookies.
        payload["cookies"] = cookies
        payload["elapsed_ms"] = payload["duration_ms"]
        payload["error"] = error_code
        payload["telemetry"] = telemetry
        return payload

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
        telemetry = await _resolve_telemetry()
        return _serialize_result(
            success=success,
            cookies=cookies,
            elapsed_ms=elapsed_ms,
            error_code=None if success else "solve_failed",
            telemetry=telemetry,
        )
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        telemetry = await _resolve_telemetry()
        return _serialize_result(
            success=False,
            cookies=None,
            elapsed_ms=elapsed_ms,
            error_code=str(e),
            telemetry=telemetry,
        )
    finally:
        await solver.close()


if __name__ == "__main__":
    main()
