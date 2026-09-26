"""拖动机制离线仿真 — 本地 Chromium + 自研 slider_sim.html，绝不触碰线上真实风控。

用户指令：拖动行为的验证只能在本地模拟风控上进行。本测试用与生产完全相同的
_drag 流水线（build_drag_events + dispatch_drag_timeline）驱动一个真实本地
浏览器里的仿真滑块（只接受 isTrusted 输入、校验 buttons 拖拽态、事件数与
终点位置），端到端验证 choreography 能把按钮拖到终点并判过。本地浏览器
不可用时跳过（test_drag_pipeline 的单元流水线测试仍然兜底）。
"""
import pathlib

import pytest

from slidex._drag import build_drag_events, dispatch_drag_timeline

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "slidex" / "fixtures" / "slider_sim.html"


@pytest.mark.asyncio
async def test_drag_pipeline_passes_local_slider_sim():
    try:
        from playwright.async_api import async_playwright
    except Exception:
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = None
    try:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"local chromium unavailable: {e}")

        page = await browser.new_page()
        await page.route(
            "**/_____tmd_____/slide**",
            lambda route: route.fulfill(status=200, content_type="application/json", body='{"code":0}'),
        )
        await page.goto(FIXTURE.as_uri())

        geo = await page.evaluate(
            """() => {
                const t = document.getElementById('track').getBoundingClientRect();
                const b = document.getElementById('btn').getBoundingClientRect();
                return { sx: b.x + b.width / 2, sy: b.y + b.height / 2, travel: t.width - b.width };
            }"""
        )
        start_x, start_y, travel = geo["sx"], geo["sy"], geo["travel"]

        session = await page.context.new_cdp_session(page)
        # 与生产同构的时间线：press-hold 500ms + 12 位移点 + 过冲/回拖 + 末端握持
        pts = [(start_x + travel * (i + 1) / 12.0, start_y, 30.0) for i in range(12)]
        timeline = build_drag_events(
            start_x, start_y, [(start_x, start_y, 500.0)] + pts, extra_overshoot=True
        )
        await dispatch_drag_timeline(session, timeline)

        sim = await page.evaluate("() => window.__sim")
        assert sim["pass"] is True, f"sim did not pass: {sim}"
        assert sim["moves"] >= 8, sim
        assert sim["pressedMs"] >= 400, sim  # press-hold 真实生效
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            await pw.stop()
        except Exception:
            pass
