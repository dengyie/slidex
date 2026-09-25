"""0.6.18: 末端握持 — 拖到终点变绿后不能立刻松键（用户 2026-09-25 实测）。

验证在松键时刻评估，到达即松被拒（此前全部 code=300 的行为特征之一）。
所有拖动路径（generated/recorded × CDP/Playwright + aliyun provider）在
mouseup 前必须握住 slide_end_hold_range() 的随机时长。
"""
import asyncio

import pytest

from slidex import solver as solver_module
from slidex._trajectory import slide_end_hold_range
from slidex.solver import SliderSolver


def test_end_hold_range_defaults():
    assert slide_end_hold_range() == (0.45, 1.10)


def test_end_hold_range_env_override_and_clamp(monkeypatch):
    monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "0.2")
    monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "0.5")
    assert slide_end_hold_range() == (0.2, 0.5)

    # min > max 时钳制
    monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MIN", "0.9")
    monkeypatch.setenv("SLIDEX_SLIDE_END_HOLD_MAX", "0.5")
    assert slide_end_hold_range() == (0.9, 0.9)


class _FakeMouse:
    def __init__(self):
        self.events = []

    async def move(self, x, y):
        self.events.append(("move", asyncio.get_event_loop().time()))

    async def down(self):
        self.events.append(("down", asyncio.get_event_loop().time()))

    async def up(self):
        self.events.append(("up", asyncio.get_event_loop().time()))


class _FakePage:
    def __init__(self):
        self.mouse = _FakeMouse()


def _make_solver(page):
    solver = SliderSolver.__new__(SliderSolver)
    solver.pure_user_id = "t"
    solver.page = page
    solver.selectors = {}
    return solver


@pytest.mark.asyncio
async def test_slide_playwright_holds_before_mouseup(monkeypatch):
    # 缩短按住停顿，让测试只度量末端握持
    monkeypatch.setattr(solver_module, "slide_end_hold_range", lambda: (0.30, 0.35))

    page = _FakePage()
    solver = _make_solver(page)

    await solver._slide_playwright(120.0, 1, None, 40.0, 20.0)

    times = dict(page.mouse.events)
    last_move = max(t for name, t in page.mouse.events if name == "move")
    up_time = times["up"]
    assert up_time - last_move >= 0.29
    # 顺序必须是 move → up，且 up 存在
    assert page.mouse.events[-1][0] == "up"
