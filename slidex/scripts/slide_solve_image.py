#!/usr/bin/env python3
"""纯图片滑块缺口求解（无浏览器）

用法:
    python -m slidex.scripts.slide_solve_image \
        --background bg.png \
        [--piece piece.png] \
        [--distance-scale 1.0]

输入背景图与可选拼图块图，输出统一视觉结果 JSON（无 cookie / telemetry 字段）。
适合 TypeScript/Node 子进程集成：截图或抓包图片先落盘，再调用本命令拿缺口坐标。
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


def main():
    parser = argparse.ArgumentParser(
        description="从背景图（与可选拼图块图）定位滑块缺口，输出 JSON 结果",
    )
    parser.add_argument(
        "--background", required=True,
        help="背景图路径（png/jpg）",
    )
    parser.add_argument(
        "--piece", default=None,
        help="拼图块图路径（可选；缺失时走无块图缺口检测）",
    )
    parser.add_argument(
        "--distance-scale", type=float, default=None,
        help="图像像素到实际滑动行程的换算比例（可选）",
    )
    parser.add_argument(
        "--roi", default=None,
        help='检测区域 "x,y,width,height"（原图像素，可选；适合整页截图）',
    )
    parser.add_argument(
        "--min-confidence", type=float, default=None,
        help="候选置信度下限（可选，默认由 solver 内部阈值决定）",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="启用详细日志",
    )
    args = parser.parse_args()

    if not args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="ERROR")

    result = _run(
        background=args.background,
        piece=args.piece,
        distance_scale=args.distance_scale,
        min_confidence=args.min_confidence,
        roi=_parse_roi(args.roi),
    )
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result["success"] else 1)


def _parse_roi(roi_str: Optional[str]) -> Optional[Dict[str, float]]:
    """解析 "x,y,width,height" → roi 字典；非法输入直接报错退出。"""
    if roi_str is None:
        return None
    try:
        parts = [float(part.strip()) for part in roi_str.split(",")]
        if len(parts) != 4:
            raise ValueError
        x, y, width, height = parts
    except ValueError:
        raise SystemExit(f"invalid --roi value: {roi_str!r} (expected x,y,width,height)")
    return {"x": x, "y": y, "width": width, "height": height}


def _run(
    background: str,
    piece: Optional[str],
    distance_scale: Optional[float],
    min_confidence: Optional[float],
    roi: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    from slidex.vision import SliderImageSolver

    started = time.time()
    bg_path = Path(background)
    if not bg_path.is_file():
        return {
            "success": False,
            "challenge_type": "slider_captcha",
            "provider": "slidex-image",
            "confidence": 0.0,
            "duration_ms": 0.0,
            "error_code": "background_image_not_found",
            "retryable": False,
            "artifacts": [],
            "metadata": {},
            "error": "background_image_not_found",
        }
    piece_path = Path(piece) if piece else None
    if piece_path is not None and not piece_path.is_file():
        return {
            "success": False,
            "challenge_type": "slider_captcha",
            "provider": "slidex-image",
            "confidence": 0.0,
            "duration_ms": 0.0,
            "error_code": "piece_image_not_found",
            "retryable": False,
            "artifacts": [],
            "metadata": {},
            "error": "piece_image_not_found",
        }

    solver = SliderImageSolver()
    if min_confidence is not None:
        solver.min_confidence = float(min_confidence)

    image_result = solver.solve(
        bg_path,
        piece_path,
        distance_scale=distance_scale,
        roi=roi,
    )
    duration_ms = round((time.time() - started) * 1000, 1)
    payload = {
        "success": image_result.success,
        "challenge_type": "slider_captcha",
        "provider": "slidex-image",
        "confidence": image_result.confidence,
        "duration_ms": duration_ms,
        "error_code": image_result.error_code,
        "retryable": not image_result.success,
        "artifacts": [],
        "metadata": {
            "background": str(bg_path),
            "piece": str(piece_path) if piece_path else None,
            "gap_x": image_result.gap_x,
            "distance_px": image_result.distance_px,
            "method": image_result.method,
            "gap_box": image_result.gap_box,
            "candidates": image_result.candidates,
            **image_result.metadata,
        },
        # 兼容 slide_solve_cdp 输出的平铺字段
        "elapsed_ms": duration_ms,
        "error": image_result.error_code,
    }
    return payload


if __name__ == "__main__":
    main()
