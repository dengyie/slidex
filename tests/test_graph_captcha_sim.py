"""字符图形验证码离线仿真 — 本地 Chromium + 自研 graph_chars_sim.html，绝不触碰线上真实风控。

用户指令：验证只在本地模拟风控上进行（0.6.25 起）。本测试用生产同一条链路
（VisualChallengeSolver → plan_graph_chars → GraphCaptchaExecutor），驱动真实
本地浏览器里的仿真字符验证码（严格比对答案），端到端验证截图 → 识别 → 填入
→ 提交 → 判决能判过。

**识别引擎注入假实现**：返回仿真页里已知的答案，因此本测试不依赖任何视觉
API，也不测试任何特定 OCR 模型——验证的是流水线本身。两段式解耦的收益正在
于此：换引擎不改这条链路。

本地浏览器不可用时跳过（单测层的纯函数/执行器测试仍然兜底）。
"""

from __future__ import annotations

import pathlib

import pytest

from slidex.ocr.models import OcrResult
from slidex.vision.models import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision.solver import VisualChallengeSolver

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "slidex" / "fixtures" / "graph_chars_sim.html"
)

# 仿真页默认答案（见 graph_chars_sim.html 的 ?answer= 默认值）。
_SIM_TRUTH = "AEQQ"


class _FakeCharEngine:
    """假识别引擎：直接给出仿真页的答案。"""

    provider = "fake-sim"

    def __init__(self, text: str = _SIM_TRUTH):
        self.text = text

    def read_chars(self, *, image_bytes=None, image_path=None):
        return OcrResult(
            text=self.text, confidence=0.95, provider=self.provider, metadata={}
        )


async def _open_sim(pw, answer: str = _SIM_TRUTH):
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport={"width": 800, "height": 600})
    await page.route(
        "**/_____tmd_____/graphChars**",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"code":0}'
        ),
    )
    await page.goto(f"{FIXTURE.as_uri()}?answer={answer}")
    board = page.locator("#board")
    await board.wait_for(state="visible")
    return browser, page, board


def _request(board, answer_box, submit, **kwargs):
    base = dict(
        challenge_type=ChallengeType.GRAPH_CHARS,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=None,
        target_element=board,
        input_element=answer_box,
        submit_element=submit,
        expected_char_count=4,
    )
    base.update(kwargs)
    return VisualChallengeRequest(**base)


@pytest.mark.asyncio
async def test_graph_chars_pipeline_passes_local_sim():
    """正确答案 → 仿真器判过，且答案确实经由 fill + 提交。"""
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

        answer_box = page.locator("#answer")
        submit = page.locator("#submit")

        async def verdict_reader():
            return {"code": 0 if await page.evaluate("() => window.__sim.pass") else 300}

        solver = VisualChallengeSolver(vision_extractor=_FakeCharEngine())
        result = await solver.solve(
            _request(board, answer_box, submit, metadata={"verdict_reader": verdict_reader})
        )

        # 仿真页判过 → verdict_reader 回报 code=0。
        assert result.metadata.get("submit_attempted") is True, result.to_dict()
        assert result.metadata["verdict"]["code"] == 0, result.metadata["verdict"]

        sim = await page.evaluate("() => window.__sim")
        assert sim["pass"] is True, sim
        assert sim["attempts"] == 1, sim
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
async def test_wrong_answer_is_rejected_by_sim():
    """错误答案 → 仿真器判败（code=300 → graph_chars_punished）。

    同时证明仿真器不是恒过，否则上面那个测试无意义。
    """
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

        answer_box = page.locator("#answer")
        submit = page.locator("#submit")

        async def verdict_reader():
            return {"code": 0 if await page.evaluate("() => window.__sim.pass") else 300}

        # 长度合法但内容错：让规划层放行，由仿真器判负——验证惩罚码链路。
        solver = VisualChallengeSolver(vision_extractor=_FakeCharEngine("ZZZZ"))
        result = await solver.solve(
            _request(board, answer_box, submit, metadata={"verdict_reader": verdict_reader})
        )

        assert result.error_code == "graph_chars_punished", result.to_dict()
        assert result.retryable is False

        sim = await page.evaluate("() => window.__sim")
        assert sim["pass"] is False, sim
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
async def test_verdict_pending_without_reader():
    """不注入判决读取器时不擅自判成功——停在 verdict_pending 交调用方判读。"""
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

        solver = VisualChallengeSolver(vision_extractor=_FakeCharEngine())
        result = await solver.solve(
            _request(board, page.locator("#answer"), page.locator("#submit"))
        )

        assert result.success is False
        assert result.error_code == "graph_chars_verdict_pending"
        # 但提交确实发生了，答案确实填进去了。
        assert result.metadata["chars"] == _SIM_TRUTH
        assert result.metadata["submit_attempted"] is True
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