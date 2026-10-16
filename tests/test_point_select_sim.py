"""点选机制离线仿真 — 本地 Chromium + 自研 point_select_sim.html，绝不触碰线上真实风控。

用户指令：验证只在本地模拟风控上进行（0.6.25 起）。本测试用生产同一条
规划器（plan_click_points）+ 执行器（PointSelectExecutor）流水线，驱动真实
本地浏览器里的仿真点选（只接受 isTrusted、校验点击顺序），端到端验证坐标
换算与依序点击能判过。

**识别引擎注入假实现**：返回仿真页布局里已知的字符坐标，因此本测试不依赖
任何视觉 API，也不测试任何特定 OCR 模型——验证的是流水线本身（截图 →规划
→ 依序点击 → 判决）。两段式解耦的收益正在于此：换引擎不改这条链路。

本地浏览器不可用时跳过（单测层的纯函数/执行器测试仍然兜底）。
"""
import pathlib

import pytest

from slidex.ocr.instruction import parse_instruction
from slidex.ocr.models import OcrBox, OcrResult
from slidex.vision.point_select import PointSelectExecutor, plan_click_points

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "slidex" / "fixtures" / "point_select_sim.html"
)

# 与 point_select_sim.html 的 #board 布局契约对应：360px 宽、5 个字
# space-around 均分、y 居中 → 归一化中心点 x≈0.1/0.3/0.5/0.7/0.9, y=0.5。
_SIM_CHARS = ["的", "了", "我", "你", "好"]
_SIM_XS = [0.1, 0.3, 0.5, 0.7, 0.9]


class _FakeVisionExtractor:
    """假识别引擎：直接给出仿真页里 5 个字的归一化字框。"""

    provider = "fake-sim"

    def locate_targets(self, *, image_bytes=None, image_path=None, instruction=None):
        boxes = [
            OcrBox(text=ch, x=x - 0.08, y=0.4, width=0.16, height=0.2, confidence=0.95)
            for ch, x in zip(_SIM_CHARS, _SIM_XS)
        ]
        return OcrResult(
            text="".join(_SIM_CHARS), confidence=0.95, boxes=boxes, provider=self.provider
        )


async def _open_sim(pw):
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 800, "height": 600})
    await page.route(
        "**/_____tmd_____/pointSelect**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"code":0}'
        ),
    )
    await page.goto(FIXTURE.as_uri())
    # 承载挑战的元素：截图与坐标换算都以它为基准坐标系。
    board = page.locator("#board")
    await board.wait_for(state="visible")
    return browser, page, board


@pytest.mark.asyncio
async def test_point_select_pipeline_passes_local_sim():
    """正确顺序 → 仿真器判过，且点击序列与指令一致。"""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = None
    try:
        try:
            browser, page, board = await _open_sim(pw)
        except Exception as e:
            pytest.skip(f"local chromium unavailable: {e}")

        instruction = parse_instruction("请依次点击【的】【了】【我】")
        assert instruction is not None
        assert instruction.targets == ("的", "了", "我")

        detected = _FakeVisionExtractor().locate_targets(
            image_bytes=None, instruction=instruction
        )
        plan = plan_click_points(detected, instruction)
        assert plan.ok, plan.error_code

        outcome = await PointSelectExecutor().execute(
            page=page, target_element=board, plan=plan
        )
        assert outcome.get("error_code") is None, outcome
        assert [c["char"] for c in outcome["clicked"]] == ["的", "了", "我"]

        sim = await page.evaluate("() => window.__sim")
        assert sim["pass"] is True, sim
        assert sim["picked"] == ["的", "了", "我"], sim
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


@pytest.mark.asyncio
async def test_wrong_order_is_detected_by_sim():
    """故意点错顺序 → 仿真器判败。证明仿真器不是恒过（否则上面那个测试无意义）。"""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = None
    try:
        try:
            browser, page, board = await _open_sim(pw)
        except Exception as e:
            pytest.skip(f"local chromium unavailable: {e}")

        # 构造一个顺序颠倒的计划：先点「了」再点「的」。
        # 坐标由 board 实际 bounding_box 推导，不硬编码——避免布局微调
        # 就让本测试失效（这正是执行器要解决的换算问题）。
        box = await board.bounding_box()
        wrong = ["了", "的"]
        for ch in wrong:
            idx = _SIM_CHARS.index(ch)
            x = box["x"] + _SIM_XS[idx] * box["width"]
            y = box["y"] + 0.5 * box["height"]
            await page.mouse.click(x, y)

        sim = await page.evaluate("() => window.__sim")
        assert sim["pass"] is False, sim
        assert sim["reason"] in {"wrong_order", "too_many_clicks"}, sim
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