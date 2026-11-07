"""hCaptcha 执行器 — 假页面上的交互纪律锁定（零浏览器、零网络）。

执行器只依赖 Playwright 的一小撮鸭子接口（``page.frames`` / ``page.mouse`` /
``page.on`` / ``page.query_selector`` / ``frame.locator``），因此可以用纯
Python 假对象精确观测它「点了哪里、点了几次、提交了几次」。这些断言正是
风控相关回归最容易悄悄破坏的部分：

- 九宫格：必须点在被命中的格子中心，且**不能**碰到模型标 False 的格子；
- 区域点选：归一化坐标 × challenge-view 盒子 = 页面坐标；
- 拖放回退：引擎判「不是拖放」时必须改走点选，而不是盲拖；
- 提交纪律：每 crumb 只点一次 submit；提交后没判决就停手，不再补点
  （重复提交 = 追加被判样本）。
"""
import asyncio

import pytest

from slidex.ocr.models import OcrResult
from slidex.vision.hcaptcha import (
    DragGesture,
    HcaptchaExecutor,
    LlmHcaptchaEngine,
    plan_drag_gesture,
)
from slidex.vision.point_select import ClickPlan, ClickPoint

_FRAME_PREFIX = "https://newassets.hcaptcha.com/captcha/v1/"
_TOKEN_SELECTORS = {
    "textarea[name='h-captcha-response']",
    "input[name='h-captcha-response']",
    "textarea[name='g-recaptcha-response']",
    "input[name='g-recaptcha-response']",
}


# ────────────────────── 假 DOM ──────────────────────


class _Item:
    def __init__(self, box=None, *, visible=True, text="", image=b"PNG"):
        self.box = box
        self.visible = visible
        self.text = text
        self.image = image


class _Locator:
    def __init__(self, items):
        self._items = list(items)

    @property
    def first(self):
        return _Locator(self._items[:1])

    def nth(self, index):
        return _Locator([self._items[index]])

    async def count(self):
        return len(self._items)

    async def is_visible(self):
        return bool(self._items) and self._items[0].visible

    async def inner_text(self):
        return self._items[0].text if self._items else ""

    async def bounding_box(self):
        return self._items[0].box if self._items else None

    async def screenshot(self, type="png"):
        if not self._items:
            return b""
        image = self._items[0].image
        return image() if callable(image) else image


class _Frame:
    def __init__(self, url, selectors):
        self.url = url
        self._selectors = selectors

    def locator(self, selector):
        return _Locator(self._selectors.get(selector, []))


class _Mouse:
    def __init__(self, page):
        self._page = page

    async def move(self, x, y, steps=1):
        self._page.moves.append((round(x, 2), round(y, 2)))

    async def down(self):
        x, y = self._page.moves[-1]
        self._page.downs.append((x, y))
        self._page.on_down(x, y)

    async def up(self):
        pass


class _TokenElement:
    def __init__(self, page):
        self._page = page

    async def input_value(self):
        return self._page.token

    async def get_attribute(self, name):
        return self._page.token if name == "value" else None


class _Page:
    def __init__(self, frames, *, token="", submit_box=None):
        self.frames = frames
        self.moves = []
        self.downs = []
        self.handlers = []
        self.token = token
        self.submit_box = submit_box
        self.mouse = _Mouse(self)

    def on(self, event, handler):
        self.handlers.append((event, handler))

    def remove_listener(self, event, handler):
        if (event, handler) in self.handlers:
            self.handlers.remove((event, handler))

    async def query_selector(self, selector):
        if selector in _TOKEN_SELECTORS and self.token:
            return _TokenElement(self)
        return None

    def on_down(self, x, y):
        # 模拟「点提交按钮即拿到 h-captcha-response token」。
        box = self.submit_box
        if not box:
            return
        if box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"]:
            self.token = "PASS-TOKEN"


def _task_item(index, origin=(0.0, 0.0), size=(300.0, 300.0)):
    """3x3 均分：返回第 index 格的 page 坐标盒子。"""
    left, top = origin
    width, height = size
    col, row = index % 3, index // 3
    return _Item(
        {
            "x": left + col * width / 3,
            "y": top + row * height / 3,
            "width": width / 3,
            "height": height / 3,
        }
    )


def _build_grid_page(selectors=None):
    view = {"x": 100.0, "y": 50.0, "width": 300.0, "height": 300.0}
    tasks = [_task_item(i, origin=(view["x"], view["y"]), size=(300.0, 300.0)) for i in range(9)]
    submit = {
        "x": 100.0,
        "y": 400.0,
        "width": 80.0,
        "height": 30.0,
    }
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        selectors
        if selectors is not None
        else {
            ".challenge-view": [_Item(view)],
            ".task": tasks,
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="Please click all traffic lights")],
        },
    )
    page = _Page([frame], submit_box=submit)
    return page, frame, view, tasks, submit


def _engine_with(extractor):
    return LlmHcaptchaEngine(extractor)


class _GridExtractor:
    provider = "fake-grid"

    def __init__(self, selected="[1,9]"):
        self._text = '{"selected":%s}' % selected

    def ask(self, *, image_bytes=None, image_path=None, prompt):
        return OcrResult(text=self._text, confidence=0.9, provider=self.provider)


class _ExpectSelected:
    """锁定期望的题面是否注入，以及回答文本。"""

    provider = "fake"

    def __init__(self, text):
        self._text = text

    def ask(self, *, image_bytes=None, image_path=None, prompt):
        return OcrResult(text=self._text, confidence=0.8, provider=self.provider)


class _DragThenPointEngine:
    """先报「不是拖放」，再给点选坐标——用于验证回退链。"""

    def __init__(self, points):
        self._points = points
        self.drag_calls = 0
        self.point_calls = 0
        self.provider = "fake-fallback"

    def locate_drag(self, image_bytes, question, *, prompt=None):
        self.drag_calls += 1
        return DragGesture(
            start=(0.0, 0.0), end=(0.0, 0.0), error_code="not_a_drag_challenge"
        )

    def locate_points(self, image_bytes, question, *, prompt=None):
        self.point_calls += 1
        return ClickPlan(points=self._points, confidence=0.9)


class _DragEngine:
    def __init__(self, start, end):
        self._gesture = plan_drag_gesture(start, end, jitter=0.0)
        self.provider = "fake-drag"

    def locate_drag(self, image_bytes, question, *, prompt=None):
        return self._gesture

    def locate_points(self, image_bytes, question, *, prompt=None):  # pragma: no cover
        raise AssertionError("drag succeeded; point fallback must not run")


# ────────────────────── 测试 ──────────────────────


@pytest.mark.asyncio
async def test_binary_clicks_only_selected_tiles():
    page, frame, view, tasks, submit = _build_grid_page()
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1,9]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    outcome = await executor.solve(page)

    assert outcome["success"] is True, outcome
    assert outcome["mode"] == "binary"
    # 题面来自 DOM
    assert outcome["question"] == "Please click all traffic lights"

    clicked = [c for c in outcome["clicked"] if "tile" in c]
    assert sorted(c["tile"] for c in clicked) == [1, 9]

    # 命中的格子：点击坐标必须落在该格中心（允许 jitter，但不得跨格）。
    def _inside(point, item):
        b = item.box
        return b["x"] <= point[0] <= b["x"] + b["width"] and b["y"] <= point[1] <= b["y"] + b["height"]

    tile_down = [p for p in page.downs if not _inside(p, _Item(submit))]
    assert len(tile_down) == 2
    assert _inside(tile_down[0], tasks[0])
    assert _inside(tile_down[1], tasks[8])


@pytest.mark.asyncio
async def test_binary_all_false_falls_back_to_first_tile():
    page, *_ = _build_grid_page()
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )
    outcome = await executor.solve(page)
    assert outcome["success"] is True
    tiles = [c for c in outcome["clicked"] if "tile" in c]
    assert [t["tile"] for t in tiles] == [1]


@pytest.mark.asyncio
async def test_checkbox_clicked_at_its_center():
    view = {"x": 100.0, "y": 50.0, "width": 300.0, "height": 300.0}
    tasks = [_task_item(i, origin=(100.0, 50.0)) for i in range(9)]
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}
    checkbox_box = {"x": 500.0, "y": 300.0, "width": 30.0, "height": 30.0}
    checkbox_frame = _Frame(
        _FRAME_PREFIX + "en/frame=checkbox&...",
        {"#checkbox": [_Item(checkbox_box)]},
    )
    challenge_frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": tasks,
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="题面")],
        },
    )
    page = _Page([checkbox_frame, challenge_frame], submit_box=submit)
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        click_radius_px=0.0,
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    await executor.solve(page)

    first = page.downs[0]
    assert first[0] == pytest.approx(515.0, abs=0.01)
    assert first[1] == pytest.approx(315.0, abs=0.01)


@pytest.mark.asyncio
async def test_point_mode_uses_challenge_view_coordinates():
    view = {"x": 100.0, "y": 50.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": [],
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="请点击消防栓")],
        },
    )
    page = _Page([frame], submit_box=submit)
    engine = _DragThenPointEngine(
        [ClickPoint(char="point", x=0.5, y=0.25, confidence=0.9)]
    )
    executor = HcaptchaExecutor(
        vision_engine=engine,
        click_radius_px=0.0,
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    outcome = await executor.solve(page)

    assert outcome["success"] is True
    assert outcome["mode"] == "point"
    assert engine.drag_calls == 1
    assert engine.point_calls == 1
    # 归一化 (0.5, 0.25) → 页面 (100+150, 50+75) = (250, 125)
    assert page.downs[0] == pytest.approx((250.0, 125.0), abs=0.01)


@pytest.mark.asyncio
async def test_drag_mode_emits_press_move_release():
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": [],
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="拖动拼图")],
        },
    )
    page = _Page([frame], submit_box=submit)
    executor = HcaptchaExecutor(
        vision_engine=_DragEngine((0.1, 0.2), (0.9, 0.8)),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    outcome = await executor.solve(page)

    assert outcome["success"] is True
    assert outcome["mode"] == "drag"
    # 拖拽结束后才点 submit；过程中不能有多余的 down。
    tile_like = [p for p in page.downs if p[1] < 400]
    assert len(tile_like) == 1  # 只有起始的那次按下
    assert page.downs[0] == pytest.approx((30.0, 60.0), abs=0.01)


@pytest.mark.asyncio
async def test_submit_exactly_once_then_stops_without_verdict():
    """提交后无判决：必须立刻返回，**不得**再点一次 submit。"""
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": [_task_item(i, origin=(0.0, 0.0)) for i in range(9)],
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="题面")],
        },
    )
    # submit_box=None → 点提交不会产生 token → 无判决。
    page = _Page([frame], submit_box=None)
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
        wait_challenge_s=1.0,
    )

    outcome = await executor.solve(page)

    submit_clicks = [
        p
        for p in page.downs
        if submit["x"] <= p[0] <= submit["x"] + submit["width"]
        and submit["y"] <= p[1] <= submit["y"] + submit["height"]
    ]
    assert len(submit_clicks) == 1
    # 无判决 + 题面仍可见会继续下一页，最终以 pending 收尾，但绝不重复点 submit
    # （max_steps 由 max_rounds=3 × crumbs=1 × 2 = 6 轮上限兜底）。
    assert outcome["success"] is False
    assert outcome["error_code"] in {"hcaptcha_verdict_pending", "submit_not_found"}


@pytest.mark.asyncio
async def test_missing_submit_reports_submit_not_found():
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": [_task_item(i, origin=(0.0, 0.0)) for i in range(9)],
            ".challenge-header": [_Item(text="题面")],
        },
    )
    page = _Page([frame])
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    outcome = await executor.solve(page)
    assert outcome["success"] is False
    assert outcome["error_code"] == "submit_not_found"


@pytest.mark.asyncio
async def test_invisible_token_short_circuits():
    """没有 checkbox 挑战但 token 已存在 → invisible 直通，不发任何点击。"""
    page = _Page([], token="ALREADY-PASSED")
    executor = HcaptchaExecutor(vision_engine=_engine_with(_GridExtractor()), settle_s=0.0)
    outcome = await executor.solve(page)
    assert outcome["success"] is True
    assert outcome["mode"] == "invisible"
    assert page.downs == []


@pytest.mark.asyncio
async def test_challenge_timeout_reports_error():
    page = _Page([])
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor()),
        wait_challenge_s=1.0,
        settle_s=0.0,
    )
    outcome = await executor.solve(page)
    assert outcome["success"] is False
    assert outcome["error_code"] == "hcaptcha_challenge_timeout"


@pytest.mark.asyncio
async def test_engine_error_is_returned_without_submit():
    """识别失败时不能在页面上乱点或提交。"""
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}
    frame = _Frame(
        _FRAME_PREFIX + "en/frame=challenge&...",
        {
            ".challenge-view": [_Item(view)],
            ".task": [_task_item(i, origin=(0.0, 0.0)) for i in range(9)],
            "//div[@class='button-submit button']": [_Item(submit)],
            ".button-submit": [_Item(submit)],
            ".challenge-header": [_Item(text="题面")],
        },
    )
    page = _Page([frame], submit_box=submit)
    engine = _engine_with(_ExpectSelected("不是 JSON"))
    executor = HcaptchaExecutor(
        vision_engine=engine, inter_click_delay=(0.0, 0.0), settle_s=0.0
    )
    outcome = await executor.solve(page)
    assert outcome["success"] is False
    assert outcome["error_code"] == "vision_unparseable"
    assert page.downs == []


@pytest.mark.asyncio
async def test_question_prefers_dom_over_payload():
    """题面优先用 DOM 文本（language 无关），payload 只作兜底。"""
    page, frame, *_ = _build_grid_page()
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )
    executor._payload = {"requester_question": {"zh": "payload 题面"}}

    outcome = await executor.solve(page)

    assert outcome["question"] == "Please click all traffic lights"


@pytest.mark.asyncio
async def test_payload_question_language_selection():
    """payload 兜底题面：auto 中文优先，显式语种优先取该键，缺失再回退。"""
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor()), settle_s=0.0
    )
    executor._payload = {
        "requester_question": {"en": "click all buses", "zh": "点击所有巴士"}
    }

    assert executor._question_from_payload("auto") == "点击所有巴士"
    assert executor._question_from_payload("en") == "click all buses"
    assert executor._question_from_payload("zh") == "点击所有巴士"
    # 请求的语种缺失时回退到其它已知键，而不是交白卷。
    assert executor._question_from_payload("fr") == "点击所有巴士"
    executor._payload = {}
    assert executor._question_from_payload("auto") == ""


@pytest.mark.asyncio
async def test_late_failed_verdict_is_not_reported_as_success():
    """判决在轮询缝隙里才到达且 pass=false：绝不能因「收到判决」就报成功。"""
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}

    class _RefreshOnSubmit(_Page):
        def __init__(self):
            self.challenge_image = b"CRUMB-1"
            frame = _Frame(
                _FRAME_PREFIX + "en/frame=challenge&...",
                {
                    ".challenge-view": [_Item(view, image=lambda: self.challenge_image)],
                    ".task": [_task_item(i, origin=(0.0, 0.0)) for i in range(9)],
                    "//div[@class='button-submit button']": [_Item(submit)],
                    ".button-submit": [_Item(submit)],
                    ".challenge-header": [_Item(text="题面")],
                },
            )
            super().__init__([frame])

        def on_down(self, x, y):
            inside = (
                submit["x"] <= x <= submit["x"] + submit["width"]
                and submit["y"] <= y <= submit["y"] + submit["height"]
            )
            if inside:
                self.challenge_image = b"CRUMB-2"

    page = _RefreshOnSubmit()
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    async def _late_failed_verdict(_page, _frame, timeout_s):
        executor._verdict = {"pass": False, "error": "", "has_pass_uuid": False}
        return None

    executor._await_verdict = _late_failed_verdict

    outcome = await executor.solve(page)

    assert outcome["success"] is False
    assert outcome["error_code"] == "hcaptcha_failed"


@pytest.mark.asyncio
async def test_multi_crumb_continues_only_when_challenge_refreshes():
    """题面刷新 → 继续下一页；题面原地不动 → 停手，不重复提交。"""
    view = {"x": 0.0, "y": 0.0, "width": 300.0, "height": 300.0}
    submit = {"x": 100.0, "y": 400.0, "width": 80.0, "height": 30.0}

    class _MultiCrumbPage(_Page):
        def __init__(self):
            self.challenge_image = b"CRUMB-1"
            self.challenge_item = _Item(view, image=lambda: self.challenge_image)
            frame = _Frame(
                _FRAME_PREFIX + "en/frame=challenge&...",
                {
                    ".challenge-view": [self.challenge_item],
                    ".task": [_task_item(i, origin=(0.0, 0.0)) for i in range(9)],
                    "//div[@class='button-submit button']": [_Item(submit)],
                    ".button-submit": [_Item(submit)],
                    ".challenge-header": [_Item(text="题面")],
                },
            )
            super().__init__([frame], submit_box=submit)

        def on_down(self, x, y):
            inside = (
                submit["x"] <= x <= submit["x"] + submit["width"]
                and submit["y"] <= y <= submit["y"] + submit["height"]
            )
            if not inside:
                return
            # 第一次提交：换下一 crumb，无判决；第二次提交：拿到 token。
            if self.challenge_image == b"CRUMB-1":
                self.challenge_image = b"CRUMB-2"
            else:
                self.token = "PASS-TOKEN"

    page = _MultiCrumbPage()
    executor = HcaptchaExecutor(
        vision_engine=_engine_with(_GridExtractor("[1]")),
        inter_click_delay=(0.0, 0.0),
        settle_s=0.0,
    )

    outcome = await executor.solve(page, max_rounds=3)

    assert outcome["success"] is True, outcome
    assert outcome["rounds"] == 2
    submit_clicks = [
        p
        for p in page.downs
        if p[0] >= submit["x"] and p[1] >= submit["y"]
    ]
    assert len(submit_clicks) == 2
