"""Bounded await that never waits past a timeout.

``asyncio.wait_for`` on 3.11+ waits until the cancelled inner awaitable
actually finishes. Playwright ``close`` / ``detach`` / ``stop`` and
``asyncio.shield`` can ignore cancel, so a timeout becomes an unbounded
stall. Race the work against ``sleep`` instead; if the budget expires,
cancel once and drop the task.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable


def _retrieve(fut: asyncio.Future) -> None:
    if not fut.done():
        return
    try:
        fut.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _cancel_waiter(waiter: asyncio.Future) -> None:
    if not waiter.done():
        waiter.cancel()
    try:
        await waiter
    except (asyncio.CancelledError, Exception):
        pass


async def await_with_budget(awaitable: Awaitable[Any], timeout: float) -> Any:
    """Return the awaitable's result if it finishes within *timeout* seconds.

    Raises the awaitable's exception if it fails in time. Returns ``None``
    if the budget expires; the work is cancelled once and then abandoned,
    even if it ignores cancel.
    """
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.ensure_future(asyncio.sleep(max(0.0, float(timeout))))
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        await _cancel_waiter(waiter)
        if not task.done():
            task.cancel()
            task.add_done_callback(_retrieve)
        raise
    await _cancel_waiter(waiter)
    if task.done() and not task.cancelled():
        return task.result()
    if not task.done():
        task.cancel()
        task.add_done_callback(_retrieve)
        return None
    _retrieve(task)
    return None


async def wait_abandoned(task: asyncio.Future, timeout: float) -> None:
    """Give *task* up to *timeout* seconds to finish, then drop it.

    Does not cancel *task* — caller already did, so ``finally`` can run.
    Never waits past *timeout* even if the task ignores cancel.
    """
    if task.done():
        _retrieve(task)
        return
    waiter = asyncio.ensure_future(asyncio.sleep(max(0.0, float(timeout))))
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        await _cancel_waiter(waiter)
        if not task.done():
            task.add_done_callback(_retrieve)
        raise
    await _cancel_waiter(waiter)
    if task.done():
        _retrieve(task)
    else:
        task.add_done_callback(_retrieve)
