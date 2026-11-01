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
        """瞬态文案必须用 MutationObserver 捕获（成功后弹窗即关）。"""
        from slidex.providers.aliyun_captcha_js import _OBSERVER_JS

        for token in ("MutationObserver", "__slidexCaptchaVerdict", "aliyunCaptcha-sliding-text-box"):
            assert token in _OBSERVER_JS

    def test_synthetic_drag_js_rules(self):
        """合成手势红线：可信形态字段、相对位移换算、终点 dwell、落点回读。"""
        from slidex.providers.aliyun_captcha_js import _SYNTHETIC_DRAG_JS

        for token in ("pointerdown", "mousedown", "pointermove", "mousemove", "pointerup", "mouseup",
                      "targetLeft", "getComputedStyle", "isPrimary"):
            assert token in _SYNTHETIC_DRAG_JS
