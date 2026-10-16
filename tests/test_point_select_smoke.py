"""点选全链路烟测 — 走完整 VisualChallengeSolver 通路，真实浏览器 + 仿真器。

与 test_point_select_sim.py 的区别：那个直接调执行器（验证流水线两段中的
第二段）；本测试从 ``VisualChallengeSolver.solve()`` 入口进，**把截图 → 解析
指令 → 识别 → 规划 → 点击 → 判决 全部串起来**，验证接缝真的接上了——这类
接缝最容易在"各段单测都绿、串起来不通"时出问题。

识别注入假引擎（不依赖任何视觉 API）。本地浏览器不可用时跳过。
"""
import pathlib

import pytest

from slidex.ocr.models import OcrBox, OcrResult
from slidex.vision.models import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision.solver import VisualChallengeSolver

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "slidex" / "fixtures" / "point_select_sim.html"
)

_SIM_CHARS = ["的", "了", "我", "你", "好"]
_SIM_XS = [0.1, 0.3, 0.5, 0.7, 0.9]


class _SimVision:
    """仿真页布局已知的假识别引擎：第 1 次返回不可信，其后返回正确字框。"""

    provider = "smoke-fake"

    def __init__(self):
        self.calls = 0

    def locate_targets(self, *, image_bytes=None, instruction=None):
        self.calls += 1
        assert image_bytes, "solver must pass a real screenshot to the engine"
        if self.calls == 1:
            # 第一次故意失败：验证编排层会重截图而不是连点
            return OcrResult(
                text="", confidence=0.0, boxes=[], provider=self.provider,
                metadata={"error_code": "vision_unparseable"},
            )
        boxes = [
            OcrBox(text=ch, x=x - 0.08, y=0.4, width=0.16, height=0.2, confidence=0.95)
            for ch, x in zip(_SIM_CHARS, _SIM_XS)
        ]
        return OcrResult(
            text="".join(_SIM_CHARS), confidence=0.95, boxes=boxes, provider=self.provider
        )


@pytest.mark.asyncio
async def test_solver_end_to_end_smoke():
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

        page = await browser.new_page(viewport={"width": 800, "height": 600})
        await page.route(
            "**/_____tmd_____/pointSelect**",
            lambda r: r.fulfill(status=200, content_type="application/json",
                                body='{"code":0}'),
        )
        await page.goto(FIXTURE.as_uri())
        board = page.locator("#board")
        await board.wait_for(state="visible")

        extractor = _SimVision()
        solver = VisualChallengeSolver(vision_extractor=extractor)

        request = VisualChallengeRequest(
            challenge_type=ChallengeType.POINT_SELECT,
            context=VisionContext.PLAYWRIGHT_PAGE,
            page=page,
            target_element=board,
            instruction_text="请依次点击【的】【了】【我】",
            timeout_ms=30000,
        )
        result = await solver.solve(request)

        sim = await page.evaluate("() => window.__sim")

        # 编排层确实重试过一次（第一次识别失败）
        assert extractor.calls == 2, f"expected 1 retry, got {extractor.calls}"
        # 仿真器判过
        assert sim["pass"] is True, sim
        assert sim["picked"] == ["的", "了", "我"], sim
        # 结果形状正确：点击已发生、判决待定（不误报成功）
        assert result.success is False
        assert result.error_code == "point_select_verdict_pending"
        assert result.metadata["click_attempted"] is True
        assert result.metadata["attempt"] == 2
        assert [c["char"] for c in result.metadata["clicks"]] == ["的", "了", "我"]
        assert result.metadata["targets"] == ["的", "了", "我"]
        assert result.metadata["engine_provider"] == "smoke-fake"
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
async def test_smoke_success_record_is_written(tmp_path):
    """成功记录能落盘，且包含调识别所需的关键字段。"""
    import json

    from slidex._success_record import (
        build_point_select_record,
        persist_success_record,
    )
    from slidex.vision.models import VisualChallengeResult

    result = VisualChallengeResult(
        success=False,
        challenge_type=ChallengeType.POINT_SELECT,
        provider="smoke-fake",
        confidence=0.95,
        duration_ms=2500.0,
        error_code="point_select_verdict_pending",
        metadata={
            "targets": ["的", "了", "我"],
            "engine_provider": "smoke-fake",
            "clicks": [{"char": "的", "x": 10.0, "y": 50.0, "confidence": 0.95}],
            "verdict": {"code": 0},
            "notes": [],
            "context": "playwright_page",
        },
    )
    record = build_point_select_record(result, egress_ip="203.0.113.9")
    path = persist_success_record(tmp_path, record)
    assert path and pathlib.Path(path).exists()

    lines = pathlib.Path(path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["point_select"]["click_count"] == 1
    assert loaded["point_select"]["targets"] == ["的", "了", "我"]
    assert loaded["environment"]["egress_ip"] == "203.0.113.9"