"""Playwright 主文档 + iframe 作用域解析。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, List, Optional, Sequence
from loguru import logger


async def iter_search_targets(page) -> List[Any]:
    """主 frame 优先，再各 iframe 的 content_frame（拿不到则跳过）。"""
    targets = [page]
    try:
        handles = await page.query_selector_all("iframe")
    except Exception:
        return targets
    for handle in handles:
        frame = None
        try:
            content_frame = getattr(handle, "content_frame", None)
            if content_frame:
                frame = await content_frame()
        except Exception:
            frame = None
        if frame is not None:
            targets.append(frame)
    return targets


async def first_matching_target(
    page,
    predicate: Callable[[Any], Awaitable[bool]],
) -> Optional[Any]:
    for target in await iter_search_targets(page):
        try:
            if await predicate(target):
                return target
        except Exception as exc:
            logger.debug(f"frame predicate failed: {exc}")
    return None


async def query_in_targets(targets: Sequence[Any], selector: str):
    for target in targets:
        try:
            handle = await target.query_selector(selector)
        except Exception:
            continue
        if handle:
            return handle, target
    return None, None


async def wait_in_targets(targets: Sequence[Any], selector: str, timeout: float = 10000):
    per_target = max(500.0, float(timeout) / max(len(targets), 1))
    for target in targets:
        try:
            handle = await target.wait_for_selector(selector, timeout=per_target)
        except Exception as exc:
            logger.debug(f"wait_for_selector {selector!r} failed: {exc}")
            continue
        if handle:
            return handle, target
    return None, None
