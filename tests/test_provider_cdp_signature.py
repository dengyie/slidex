"""0.10.2: provider 签名门不得静默把拖动降级成"逐点等一次往返"。

缺陷（代码实证）：`_call_perform_slide` 只在 `inspect.signature` 里有
`cdp_session` 时才注入。GeeTest 的 `perform_slide` 曾缺这个形参，于是 CDP
真机模式下它是唯一还在 `await page.mouse.move()` 逐点等 ack 的 provider——
网络 RTT 直接进事件间隔（实测 40ms 单向延迟下单点间隔从 25ms 变成 108ms）。
本文件钉住：所有 provider 都接受 cdp_session；有 CDP 会话时派发走流水线。
"""
import inspect
import re

import pytest

from slidex.providers.aliyun import AliyunNoCaptchaProvider
from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider
from slidex.providers.geetest import GeeTestProvider


@pytest.mark.parametrize("cls", [AliyunNoCaptchaProvider, AliyunCaptchaJsProvider, GeeTestProvider])
def test_every_provider_accepts_cdp_session(cls):
    """签名门是靠形参名工作的：漏一个 provider 就静默降级，且没有任何日志。"""
    params = inspect.signature(cls.perform_slide).parameters
    assert "cdp_session" in params, f"{cls.__name__} 无法接收 cdp_session → 拖动会逐点走网络"


@pytest.mark.asyncio
async def test_geetest_pipelines_when_cdp_available():
    """GeeTest 有 CDP 会话时不再逐点 await page.mouse。"""
    from slidex.providers import ProviderElements

    sent = []

    class _Session:
        async def send(self, method, params=None):
            sent.append(dict(params))

    class _Box:
        async def bounding_box(self):
            return {"x": 100.0, "y": 60.0, "width": 40.0, "height": 40.0}

    class _NoMouse:
        def __getattr__(self, name):
            raise AssertionError(f"page.{name} touched under pipeline mode")

    class _Page:
        mouse = _NoMouse()

        def __getattr__(self, name):
            # bind_response_listener 之外的页面调用不该发生
            raise AssertionError(f"page.{name} touched under pipeline mode")

    provider = GeeTestProvider.__new__(GeeTestProvider)
    provider.bind_response_listener = lambda page: None

    elements = ProviderElements(
        slider_btn=_Box(),
        slider_track=_Box(),
        bg_img=None,
        piece_img=None,
        track_width_px=300,
        metadata={},
    )
    trajectory = [(0.0, 0.0, 700.0)] + [(float(i * 20), 0.0, 25.0) for i in range(1, 12)]

    await provider.perform_slide(
        _Page(), elements, 258, trajectory, cdp_session=_Session(),
        extra_overshoot=False, end_hold_scale=1.0,
    )

    types = [e["type"] for e in sent]
    assert types[0] == "mouseMoved"
    assert "mousePressed" in types and types[-1] == "mouseReleased"
    assert len([t for t in types if t == "mouseMoved"]) >= len(trajectory) + 2
    # 事件按设计间隔派发（末次是 25ms 量级，不是 700ms 的 press-hold 重复）
    gaps = [p["type"] for p in sent]
    assert gaps.count("mouseReleased") == 1


@pytest.mark.asyncio
async def test_geetest_none_timestamp_advances_like_playwright_path(monkeypatch):
    """P3-1: 轨迹里 ts_ms=None 的点，两条派发路径必须同义。

    Playwright 步径按 15ms 前进；CDP 步径曾 float(ts_ms or 0.0) 当成 0。
    生成器只在"需要改节奏"处填 ts_ms，其余全是 None——按 0 播等于一连串
    事件挤进同一帧，判题侧读到的就是瞬移/零速度。

    直接截获编译后的 timeline：总时长里混着随机过冲/手抖，差值断言会抖。
    """
    import slidex._drag as drag_mod
    from slidex.providers import ProviderElements

    captured = {}

    async def _noop(session, timeline):
        captured["timeline"] = [(gap, dict(p)) for gap, p in timeline]

    monkeypatch.setattr(drag_mod, "dispatch_drag_timeline", _noop)

    class _Box:
        async def bounding_box(self):
            return {"x": 100.0, "y": 60.0, "width": 40.0, "height": 40.0}

    class _Page:
        def __getattr__(self, name):
            raise AssertionError(f"page.{name} touched under pipeline mode")

    provider = GeeTestProvider.__new__(GeeTestProvider)
    provider.bind_response_listener = lambda page: None

    elements = ProviderElements(
        slider_btn=_Box(), slider_track=_Box(), bg_img=None,
        piece_img=None, track_width_px=300, metadata={},
    )
    slide = [(float(i * 20), 0.0, None) for i in range(1, 13)]

    await provider.perform_slide(
        _Page(), elements, 240, [(0.0, 0.0, 300.0)] + slide,
        cdp_session=object(), extra_overshoot=False, end_hold_scale=1.0,
    )

    timeline = captured["timeline"]
    pressed = next(i for i, (_, p) in enumerate(timeline) if p["type"] == "mousePressed")
    moved = [(g, p) for g, p in timeline[pressed + 1:] if p["type"] == "mouseMoved"]
    # 首条是 press-hold 的空转移动（吃掉 hold_ms），其后每个位移点一条
    assert len(moved) == len(slide) + 1
    assert moved[0][0] == pytest.approx(300.0, abs=1e-6)
    # 每个 None 点都推进 15ms（旧实现全是 0.0，整串事件压在同一帧）
    for gap, _ in moved[1:]:
        assert gap == pytest.approx(15.0, abs=1e-6)
