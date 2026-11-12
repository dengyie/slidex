"""AliyunCaptchaJsProvider 与其几何/检测器单元测试。

素材语义来自 2026-11-02 zchat.z.ai 登录滑块实测：
- 二次几何 g(knob)=A·knob²+B·knob 两次独立标定一致（g(235)≈214.142）；
- 拼图块整条 PNG 52×200 带 alpha，形状起点 bx0 恒为 2；
- 背景 296×200 原图被拉伸到 300 CSS 宽渲染（k≈1.0135）。
"""

import math

import cv2
import numpy as np
import pytest

from slidex._image_match import AliyunJigsawMatcher
from slidex._slide_geometry import (
    ALIYUN_JIGSAW_PIECE_A,
    ALIYUN_JIGSAW_PIECE_B,
    aliyun_jigsaw_knob_for_piece,
    aliyun_jigsaw_piece_left,
)


# ── 几何定律 ────────────────────────────────────────────────────────────────

class TestAliyunJigsawGeometry:
    def test_zero_is_zero(self):
        assert aliyun_jigsaw_piece_left(0.0) == 0.0
        assert aliyun_jigsaw_knob_for_piece(0.0) == 0.0
        assert aliyun_jigsaw_knob_for_piece(-5.0) == 0.0

    def test_calibrated_points(self):
        # 两次独立标定：g(200)=157.396；成功验证：g(235)≈214.142
        assert aliyun_jigsaw_piece_left(200.0) == pytest.approx(157.396, abs=0.01)
        assert aliyun_jigsaw_piece_left(235.0) == pytest.approx(214.142, abs=0.05)

    def test_inverse_roundtrip(self):
        for piece in (5.0, 60.0, 157.396, 214.142, 259.0):
            knob = aliyun_jigsaw_knob_for_piece(piece)
            assert aliyun_jigsaw_piece_left(knob) == pytest.approx(piece, abs=1e-6)

    def test_monotonic(self):
        vals = [aliyun_jigsaw_piece_left(k) for k in range(0, 261, 10)]
        assert all(b > a for a, b in zip(vals, vals[1:]))

    def test_inverse_positive_root_only(self):
        a, b = ALIYUN_JIGSAW_PIECE_A, ALIYUN_JIGSAW_PIECE_B
        l_val = 100.0
        expected = (-b + math.sqrt(b * b + 4 * a * l_val)) / (2 * a)
        assert aliyun_jigsaw_knob_for_piece(l_val) == pytest.approx(expected, abs=1e-9)


# ── 双检测器 ────────────────────────────────────────────────────────────────

def _piece_rgba(width=52, height=200, bx0=2, bw=44, y0=40, bh=48):
    """构造带 alpha 的拼图条：矩形-圆形并集形状，左缘严格在 bx0（确定性 RNG）。"""
    rng = np.random.default_rng(7)
    img = np.zeros((height, width, 4), np.uint8)
    alpha = np.zeros((height, width), np.uint8)
    alpha[y0:y0 + bh, bx0:bx0 + bw] = 255
    cv2.circle(alpha, (bx0 + bw // 2, y0 + bh // 2), max(bw, bh) // 2, 255, -1)
    alpha[:, :bx0] = 0  # 圆可能越界，夹回左缘
    img[:, :, 3] = alpha
    # 形状内部给随机纹理，避免全零
    m = alpha > 0
    n_px = int(m.sum())
    img[m, 0] = rng.integers(50, 200, n_px)
    img[m, 1] = rng.integers(50, 200, n_px)
    img[m, 2] = rng.integers(50, 200, n_px)
    return img


def _bg_with_imprint(width=296, height=200, imprint_x=150, piece=None):
    """背景 = 平滑渐变 + 噪声；缺口处嵌入拼图块自身纹理（+40 亮度）。

    真实生成方式是"从背景缺口处抽取拼图块"，所以压痕内容 ≈ 拼图块像素，
    内容信号（掩码 SQDIFF）在真缺口处才有极小值。
    """
    xx = np.tile(np.linspace(30, 220, width, dtype=np.uint8), (height, 1))
    bg = cv2.cvtColor(xx, cv2.COLOR_GRAY2BGR)
    rng = np.random.default_rng(11)
    noise = rng.integers(0, 12, (height, width, 1), dtype=np.uint8)
    bg = np.clip(bg.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    mask = (piece[:, :, 3] > 200).astype(np.uint8)
    h, w = mask.shape
    region = bg[0:h, imprint_x:imprint_x + w]
    mh = min(h, region.shape[0])
    mw = min(w, region.shape[1])
    sub = mask[:mh, :mw].astype(bool)
    piece_rgb = piece[:mh, :mw, :3]
    region[sub] = np.clip(piece_rgb[sub].astype(np.int16) + 40, 0, 255).astype(np.uint8)
    return bg


class TestAliyunJigsawMatcher:
    def test_finds_exact_imprint(self):
        piece = _piece_rgba()
        bg = _bg_with_imprint(imprint_x=150, piece=piece)
        gap, conf, debug = AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        assert gap is not None
        # 压痕左缘 = imprint_x + bx0
        assert gap == pytest.approx(150 + 2, abs=2)
        assert conf > 0.3
        assert debug["bx0"] == 2

    def test_imprint_near_right_edge(self):
        piece = _piece_rgba()
        bg = _bg_with_imprint(imprint_x=220, piece=piece)
        gap, conf, _ = AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        assert gap is not None
        assert gap == pytest.approx(222, abs=2)

    def test_no_imprint_low_confidence(self):
        piece = _piece_rgba()
        # 无压痕背景：直接用渐变+噪声
        xx = np.tile(np.linspace(30, 220, 296, dtype=np.uint8), (200, 1))
        bg = cv2.cvtColor(xx, cv2.COLOR_GRAY2BGR)
        bg = np.clip(bg.astype(np.int16) + np.random.randint(0, 12, (200, 296, 1), dtype=np.uint8), 0, 255).astype(np.uint8)
        gap, conf, _ = AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        # 无压痕时不应给出高置信结果
        assert gap is None or conf < 0.9

    def test_bytes_entry_rejects_flat_piece(self):
        flat = np.zeros((200, 52, 3), np.uint8)
        ok, enc = cv2.imencode(".png", flat)
        assert ok
        gap, conf, debug = AliyunJigsawMatcher.find_gap_from_bytes(
            cv2.imencode(".png", np.zeros((200, 296, 3), np.uint8))[1].tobytes(),
            enc.tobytes(),
        )
        assert gap is None

    def test_bytes_entry_with_alpha(self):
        piece = _piece_rgba()
        bg = _bg_with_imprint(imprint_x=120, piece=piece)
        ok1, bg_png = cv2.imencode(".png", bg)
        ok2, piece_png = cv2.imencode(".png", piece)
        assert ok1 and ok2
        gap, conf, debug = AliyunJigsawMatcher.find_gap_from_bytes(
            bg_png.tobytes(), piece_png.tobytes()
        )
        assert gap is not None
        assert gap == pytest.approx(122, abs=2)
        assert debug["bx0"] == 2


# ── Provider 契约 ───────────────────────────────────────────────────────────

class TestAliyunCaptchaJsProvider:
    def test_registered(self):
        from slidex.providers import ProviderRegistry
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        instance = ProviderRegistry.get("aliyun-captcha-js")
        assert isinstance(instance, AliyunCaptchaJsProvider)
        assert instance.name == "aliyun-captcha-js"

    def test_image_selectors_cover_both_dom_shapes(self):
        """图片镜像元素有两种 DOM 形态，选择器必须同时命中。

        2026-11-04 chat.z.ai 实测：id 从包裹 div 挪到了 <img> 自身
        （`img#aliyunCaptcha-img` / `img#aliyunCaptcha-puzzle`），旧选择器
        `#aliyunCaptcha-img img` 命不中 → extract_images 拿 None →
        "bg/piece image extraction failed"，滑块永远过不去。
        弹窗 id 同时存在 `-window-popup`（旧）与 `-window-float`（新）。
        """
        from slidex.providers import aliyun_captcha_js as mod

        def parts(selector: str) -> set[str]:
            return {s.strip() for s in selector.split(",")}

        assert parts(mod._BG_IMG) >= {"#aliyunCaptcha-img img", "img#aliyunCaptcha-img"}
        assert parts(mod._PIECE_IMG) >= {"#aliyunCaptcha-puzzle img",
                                         "img#aliyunCaptcha-puzzle"}
        assert parts(mod._POPUP) >= {"#aliyunCaptcha-window-popup",
                                     "#aliyunCaptcha-window-float"}

    def test_knob_target_conversion_chain(self):
        """缺口 bg px → CSS → 二次反解：z.ai 实测链路（k=300/296, bx0=2）。"""
        from unittest.mock import MagicMock
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        provider = AliyunCaptchaJsProvider()
        provider._last_bx0 = 2
        elements = MagicMock()
        elements.metadata = {
            "bg_nat_w": 296, "bg_box_w": 300.0,
            "piece_nat_w": 52, "piece_box_w": 52.0,
        }
        elements.track_width_px = 260
        # 实测成功样本：压痕左缘 bg px 213 → knob 235（piece CSS 214.14）
        gap_x = 211 + 2  # find_gap 返回 dx+bx0
        target = provider._knob_target(gap_x, elements)
        assert target == pytest.approx(235.0, abs=1.5)
        assert 0 <= target <= 260

    def test_knob_target_float_precision(self):
        """find_gap 的 float 精度要保留（mixin 会截 int，±1px 敏感）。"""
        from unittest.mock import MagicMock
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        provider = AliyunCaptchaJsProvider()
        provider._last_bx0 = 2
        elements = MagicMock()
        elements.metadata = {"bg_nat_w": 296, "bg_box_w": 300.0, "piece_nat_w": 52, "piece_box_w": 52.0}
        elements.track_width_px = 260
        t_int = provider._knob_target(213, elements)
        t_float = provider._knob_target(213.5, elements)
        assert t_float > t_int  # 更右的缺口 → 更大的行程
        # ΔT = ΔL·k·(dg/dL)|₂₁₄ ≈ 0.5 × 1.0135 × 0.573 ≈ 0.29
        assert t_float - t_int == pytest.approx(0.29, abs=0.05)

    def test_knob_target_clamped_to_track(self):
        """缺口越出轨道右缘时行程必须夹紧（260 = track 300 − knob 40）。

        注意 g(260)=260（标定常数两端点固定），所以要用 L>260 才会触夹紧。"""
        from unittest.mock import MagicMock
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        provider = AliyunCaptchaJsProvider()
        provider._last_bx0 = 0
        elements = MagicMock()
        elements.metadata = {"bg_nat_w": 296, "bg_box_w": 296.0, "piece_nat_w": 52, "piece_box_w": 52.0}
        elements.track_width_px = 260
        assert provider._knob_target(280, elements) == 260.0

    def test_rescale_targets_exact(self):
        """review 关键回归：缺口 213 → 行程 235 相对差 9.4% < 10%，
        带死区的缩放器会静默跳过 → 终点差 39px 必挂 F015。必须显式重定标。"""
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider
        from slidex._slide_geometry import scale_recorded_points

        pts = [(0, 0, 800.0), (50, -1, 30), (100, -2, 30), (213, 0, 60)]
        # 复现陷阱：默认 tolerance 下不会被缩放
        leaked = scale_recorded_points(pts, 213.0, 235.0)
        assert leaked[-1][0] == pytest.approx(213.0)  # 死区吞掉了重定标
        fixed = AliyunCaptchaJsProvider._rescale_points(pts, 213.0, 235.0)
        assert fixed[-1][0] == pytest.approx(235.0)  # 显式重定标落点正确
        assert fixed[0][0] == 0 and fixed[0][2] == 800.0  # hold 首点形状保留
        assert fixed[1][2] == 30.0  # delay 保留

    def test_arm_js_tokens(self):
        """武装脚本必须含：清理旧 DOM、initAliyunCaptcha、param 注入点、show 兜底。"""
        from slidex.providers.aliyun_captcha_js import _ARM_JS

        for token in ("initAliyunCaptcha", "__slidexCaptchaParam", "__slidexCaptchaFail",
                      "aliyunCaptcha-window-popup", ".show()", "slidex-cap-btn"):
            assert token in _ARM_JS

    def test_observer_js_tokens(self):
        """瞬态文案必须用 MutationObserver 捕获（成功后弹窗即关）；
        生命周期绑定被观察节点（SDK 重建 DOM 后自愈重挂）。"""
        from slidex.providers.aliyun_captcha_js import _OBSERVER_JS

        for token in ("MutationObserver", "__slidexCaptchaVerdict", "aliyunCaptcha-sliding-text-box",
                      "__slidexCaptchaObsFor", "__slidexCaptchaObsInst", "disconnect"):
            assert token in _OBSERVER_JS

    def test_synthetic_drag_js_rules(self):
        """合成手势红线：可信形态字段、相对位移换算、终点 dwell、落点回读、点表回放。"""
        from slidex.providers.aliyun_captcha_js import _SYNTHETIC_DRAG_JS

        for token in ("pointerdown", "mousedown", "pointermove", "mousemove", "pointerup", "mouseup",
                      "targetLeft", "getComputedStyle", "isPrimary", "points"):
            assert token in _SYNTHETIC_DRAG_JS


# ── acquire 决策循环（FakePage 驱动）────────────────────────────────────────

class _FakePage:
    """acquire 决策循环的驱动 mock：evaluate 按 JS 内容分发脚本化响应。

    分发键：ARM（slidex-cap-btn）/ 合成拖拽（knobSel）/ param 轮询
    （__slidexCaptchaParam）/ 其余（observer 等）→ {}。
    """

    def __init__(self, geo=None, drag_results=None, poll_results=None,
                 arm_error=None, drag_error=None):
        self.geo = geo
        self.drag_results = list(drag_results or [])
        self.poll_results = list(poll_results or [])
        self.arm_error = arm_error
        self.drag_error = drag_error
        self.kinds = []

    async def evaluate(self, js, arg=None):
        if "slidex-cap-btn" in js:
            self.kinds.append("arm")
            if self.arm_error:
                raise self.arm_error
            return self.geo
        if "knobSel" in js:
            self.kinds.append("drag")
            if self.drag_error:
                err, self.drag_error = self.drag_error, None  # 一次性异常
                raise err
            return self.drag_results.pop(0) if self.drag_results else {"ok": True}
        if "__slidexCaptchaParam" in js:
            self.kinds.append("poll")
            if len(self.poll_results) > 1:
                return self.poll_results.pop(0)
            return self.poll_results[0] if self.poll_results else {"p": None, "f": None, "v": None}
        self.kinds.append("other")
        return {}

    async def wait_for_timeout(self, ms):
        return None

    class mouse:  # real 路径不会被 synthetic 测试触达；占位防误用
        @staticmethod
        async def move(*a, **k):
            raise AssertionError("real mouse path not expected in synthetic tests")

        @staticmethod
        async def down():
            raise AssertionError("real mouse path not expected in synthetic tests")

        up = down


def _arm_geo():
    """构造 ARM 成功响应：真实 fixture 图（matcher 能在其中找到缺口）。"""
    import base64
    piece = _piece_rgba()
    bg = _bg_with_imprint(imprint_x=150, piece=piece)
    _, bg_png = cv2.imencode(".png", bg)
    _, piece_png = cv2.imencode(".png", piece)
    b64 = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()
    return {
        "ok": True, "cert": "CERT",
        "bgData": b64(bg_png.tobytes()), "bgNat": [296, 200], "bgBox": [0, 0, 300.0, 200.0],
        "pieceData": b64(piece_png.tobytes()), "pieceNat": [52, 200], "pieceBox": [0, 0, 52.0, 200.0],
        "knobBox": [490, 424, 40, 40], "trackBox": [490, 424, 300, 40],
    }


class TestAcquireDecisionLoop:
    """acquire_verify_param 的重试/失败分类/预算控制（不依赖真浏览器）。"""

    def _run(self, page, **kw):
        import asyncio
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        provider = AliyunCaptchaJsProvider()
        return asyncio.run(
            provider.acquire_verify_param(page, "1nl1cfkp", drag="synthetic", **kw)
        )

    def test_success_first_attempt(self):
        page = _FakePage(
            geo=_arm_geo(),
            drag_results=[{"ok": True, "sl": "212px", "pz": "175px"}],
            poll_results=[{"p": "Q" * 100, "f": None, "v": None}],
        )
        res = self._run(page, max_attempts=2, verdict_timeout_ms=200)
        assert res["param"] == "Q" * 100 and res["verdict"] == "success"
        assert res["attempts"] == 1 and res["fail_code"] is None
        assert "arm" in page.kinds and "drag" in page.kinds
        assert res["target"] and 0 < res["target"] <= 260

    def test_arm_shape_failure_consumes_attempts(self):
        page = _FakePage(geo={"ok": True, "cert": "X"})  # 缺 bgData/pieceData
        res = self._run(page, max_attempts=2, verdict_timeout_ms=200)
        assert res["attempts"] == 2 and res["fail_code"] == "ARM_SHAPE"
        assert res["param"] is None
        assert page.kinds.count("arm") == 2  # 每轮都重新武装

    def test_arm_error_absorbed(self):
        page = _FakePage(geo=_arm_geo(), arm_error=RuntimeError("page closed"))
        res = self._run(page, max_attempts=2, verdict_timeout_ms=200)
        assert res["attempts"] == 2 and res["fail_code"] == "ARM"
        assert res["param"] is None

    def test_drag_exception_continues_to_next_attempt(self):
        page = _FakePage(
            geo=_arm_geo(),
            drag_results=[{"ok": True}],
            poll_results=[{"p": "P" * 280, "f": None, "v": None}],
            drag_error=RuntimeError("mouse gone"),
        )
        res = self._run(page, max_attempts=2, verdict_timeout_ms=200)
        assert res["param"] == "P" * 280 and res["attempts"] == 2
        assert res["fail_code"] == "EXC"  # 第 1 轮的失败码保留
        assert res["verdict"] == "success"

    def test_deadline_blocks_before_first_attempt(self):
        page = _FakePage(geo=_arm_geo())
        res = self._run(page, max_attempts=3, verdict_timeout_ms=50, deadline_s=0.0)
        assert res["attempts"] == 0 and res["fail_code"] == "DEADLINE"
        assert "arm" not in page.kinds  # 预算耗尽，一次都没开拖

    def test_deadline_stops_between_attempts(self):
        # 第 1 轮的 verdict 轮询耗 250ms（无 fail 回调 → 轮询到超时），
        # 200ms 预算在第 2 轮入口被拦下
        page = _FakePage(
            geo=_arm_geo(),
            drag_results=[{"ok": True}],
            poll_results=[{"p": None, "f": None, "v": None}],
        )
        res = self._run(page, max_attempts=3, verdict_timeout_ms=250, deadline_s=0.2)
        assert res["attempts"] == 1 and res["fail_code"] == "DEADLINE"
        assert res["param"] is None

    def test_upstream_fail_code_surfaced(self):
        page = _FakePage(
            geo=_arm_geo(),
            drag_results=[{"ok": True}, {"ok": True}],
            poll_results=[
                {"p": None, "f": {"code": "F015"}, "v": None},
                {"p": None, "f": {"code": "F001"}, "v": None},
            ],
        )
        res = self._run(page, max_attempts=2, verdict_timeout_ms=100)
        assert res["attempts"] == 2 and res["fail_code"] == "F001"
        assert res["param"] is None and res["verdict"] == "fail"


class TestOpaquePieceGuard:
    """全不透明 alpha（截图兜底）必须判失败，而不是给出高置信错误答案。"""

    def test_opaque_piece_rejected(self):
        piece = np.zeros((200, 52, 4), np.uint8)
        piece[:, :, :3] = 120
        piece[:, :, 3] = 255  # 全不透明
        bg = _bg_with_imprint(imprint_x=150, piece=piece)
        gap, conf, debug = AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        assert gap is None and debug.get("reason") == "opaque_piece"

    def test_opaque_piece_bytes_rejected(self):
        piece = np.zeros((200, 52, 4), np.uint8)
        piece[:, :, :3] = 120
        piece[:, :, 3] = 255
        bg = _bg_with_imprint(imprint_x=150, piece=piece)
        _, bg_png = cv2.imencode(".png", bg)
        _, piece_png = cv2.imencode(".png", piece)
        gap, conf, debug = AliyunJigsawMatcher.find_gap_from_bytes(
            bg_png.tobytes(), piece_png.tobytes()
        )
        assert gap is None and debug.get("reason") == "opaque_piece"

    def test_provider_routes_opaque_to_classic_matcher(self, monkeypatch):
        """无形状信息 → provider 回落经典模板匹配（含中心→左缘语义修正）。"""
        import asyncio
        from unittest.mock import MagicMock
        from slidex.providers import aliyun_captcha_js as m

        captured = {}

        def fake_matcher(bg_bytes, piece_bytes, offset_correction=0):
            captured["offset"] = offset_correction
            return 137, 0.42

        monkeypatch.setattr(
            m.SliderImageMatcher, "find_gap_with_confidence", staticmethod(fake_matcher)
        )
        provider = m.AliyunCaptchaJsProvider()
        piece = np.zeros((200, 52, 4), np.uint8)
        piece[:, :, :3] = 120
        piece[:, :, 3] = 255
        bg = _bg_with_imprint(imprint_x=150, piece=piece)
        _, bg_png = cv2.imencode(".png", bg)
        _, piece_png = cv2.imencode(".png", piece)
        gap, conf = asyncio.run(provider.find_gap(bg_png.tobytes(), piece_png.tobytes()))
        assert (gap, conf) == (137, 0.42)
        assert captured["offset"] == -26  # 52//2：模板中心 → 条左缘

    def test_alpha_carrier_still_detected(self):
        """守卫不误伤正常带 alpha 拼图（覆盖率 ~20% 远低于 0.9 阈值）。"""
        piece = _piece_rgba()
        bg = _bg_with_imprint(imprint_x=120, piece=piece)
        gap, conf, debug = AliyunJigsawMatcher.find_gap_contour_contrast(bg, piece)
        assert gap is not None
        assert gap == pytest.approx(122, abs=2)
        assert "reason" not in debug

    def test_piece_taller_than_bg_no_crash(self):
        """异常素材（拼图条高于背景）不再 IndexError：y 越界点一次性剔除。

        向量化前的逐 dx 实现在 fy >= h 时会直接索引越界崩溃（0.6.45 顺带
        修复）；行为约定为"剔除越界点后正常检测或低置信判失败"。"""
        tall_piece = np.zeros((260, 52, 4), np.uint8)
        tall_piece[40:88, 2:46, :3] = 90
        tall_piece[40:88, 2:46, 3] = 255
        bg = _bg_with_imprint(imprint_x=120, piece=_piece_rgba())
        gap, conf, debug = AliyunJigsawMatcher.find_gap_contour_contrast(bg, tall_piece)
        # 不崩溃即可：有效点足够则给定位，否则低置信判失败
        assert gap is None or gap >= 0


class TestGetResultFailureShape:
    """get_result 失败分支必须构造合法 SolveResult（cookies 是必填位置参数）。

    2026-11-13 chat.z.ai 实测回归：判定失败但 `_result_code is None` 时，
    旧代码 `SolveResult(success=False, code=…)` 漏传 cookies → TypeError
    ('SolveResult' object … missing 'cookies')，滑块明明已滑过却整次求解报错。
    """

    @staticmethod
    def _result_with_verdict(verdict: bool):
        """observer 已给出判定（True=success / False=fail），但无数字码。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider

        provider = AliyunCaptchaJsProvider()
        provider._result = verdict  # `_result is not None` 即短路 DOM 轮询
        provider._result_code = None  # 有判定但无数字码 → 约定回退 code=1

        page = MagicMock()
        page.url = "https://chat.z.ai/auth"
        page.context.cookies = AsyncMock(return_value=[])
        # _dom_verdict 走 evaluate：返回无文案无包的空状态，不干扰已给的判定
        page.evaluate = AsyncMock(return_value={})
        return asyncio.run(provider.get_result(page, timeout_ms=100))

    def test_fail_verdict_returns_solve_result_not_typeerror(self):
        res = self._result_with_verdict(verdict=False)
        assert res.success is False
        assert res.cookies is None
        assert res.code == 1  # 无数字码 → 按基类约定回退 1，让手势会话换族重采样
        assert res.error is None

    def test_success_verdict_still_returns_result(self):
        res = self._result_with_verdict(verdict=True)
        assert res.success is True
        assert res.code == 0
