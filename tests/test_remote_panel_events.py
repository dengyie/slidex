"""人工面板事件语义（0.6.35 扩展）：点选 click 事件 + 字符图形验证码文本输入。

面板原本只有滑块语义（down/move/up）。文字点选需要「点某位置」、字符图形
验证码需要「填几个字」——都不是拖动。这些用例锁住新增事件的委托语义，
确保它们真的落到页面动作上，而不是静默吞掉。
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

from slidex.remote import CaptchaRemoteController


class _FakeMouse:
    def __init__(self, log: List[Tuple[str, object, object]]):
        self.log = log

    async def move(self, x, y):
        self.log.append(("move", x, y))

    async def down(self):
        self.log.append(("down", None, None))

    async def up(self):
        self.log.append(("up", None, None))


class _FakeKeyboard:
    def __init__(self, log: List[Tuple[str, object, object]]):
        self.log = log

    async def type(self, ch, delay=0):
        self.log.append(("type", ch, delay))


class _FakePage:
    def __init__(self):
        self.log: List[Tuple[str, object, object]] = []
        self.mouse = _FakeMouse(self.log)
        self.keyboard = _FakeKeyboard(self.log)


def _controller() -> CaptchaRemoteController:
    controller = CaptchaRemoteController()
    controller.active_sessions["s1"] = {
        "page": _FakePage(),
        "input_delay_ms": (10, 20),
    }
    return controller


@pytest.mark.asyncio
async def test_click_event_moves_and_presses_once():
    """click 合成一次 move+down+up，不要求控制页拆成三段拖拽。"""
    controller = _controller()
    ok = await controller.handle_mouse_event("s1", "click", 120, 45)
    assert ok is True
    log = controller.active_sessions["s1"]["page"].log
    assert log == [("move", 120, 45), ("down", None, None), ("up", None, None)]


@pytest.mark.asyncio
async def test_drag_events_still_work_unchanged():
    """滑块路径不受影响——本版是纯增量。"""
    controller = _controller()
    page = controller.active_sessions["s1"]["page"]
    assert await controller.handle_mouse_event("s1", "down", 10, 10) is True
    assert await controller.handle_mouse_event("s1", "move", 30, 10) is True
    assert await controller.handle_mouse_event("s1", "up", 30, 10) is True
    kinds = [item[0] for item in page.log]
    assert kinds == ["move", "down", "move", "up"]


@pytest.mark.asyncio
async def test_unknown_event_type_still_rejected():
    controller = _controller()
    assert await controller.handle_mouse_event("s1", "hover", 10, 10) is False


@pytest.mark.asyncio
async def test_event_on_missing_session_returns_false():
    controller = CaptchaRemoteController()
    assert await controller.handle_mouse_event("nope", "click", 1, 1) is False
    assert await controller.handle_text_input("nope", "AB") is False


@pytest.mark.asyncio
async def test_text_input_types_each_character():
    """逐字键入，字符顺序与内容都保真。"""
    controller = _controller()
    page = controller.active_sessions["s1"]["page"]
    assert await controller.handle_text_input("s1", "AEZQ") is True
    typed = [item[1] for item in page.log if item[0] == "type"]
    assert typed == ["A", "E", "Z", "Q"]


@pytest.mark.asyncio
async def test_text_input_uses_configured_delay_range():
    """打字间隔走会话配置，落在这个区间内。"""
    controller = _controller()
    page = controller.active_sessions["s1"]["page"]
    await controller.handle_text_input("s1", "ABC")
    delays = [item[2] for item in page.log if item[0] == "type"]
    assert len(delays) == 3
    assert all(10 <= d <= 20 for d in delays)


@pytest.mark.asyncio
async def test_empty_text_is_noop():
    controller = _controller()
    page = controller.active_sessions["s1"]["page"]
    assert await controller.handle_text_input("s1", "") is False
    assert page.log == []


@pytest.mark.asyncio
async def test_malformed_delay_falls_back_to_default():
    """配置写坏时回退默认区间，不因解包失败而整条路径挂掉。"""
    controller = CaptchaRemoteController()
    page = _FakePage()
    controller.active_sessions["s1"] = {"page": page, "input_delay_ms": "bad"}
    assert await controller.handle_text_input("s1", "AB") is True
    assert [item[0] for item in page.log] == ["type", "type"]