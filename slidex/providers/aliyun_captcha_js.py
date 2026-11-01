"""AliyunCaptcha.js 2.0 智能验证拼图 Provider（z.ai 系站点实测，全链路内化）

与经典 AWSC nc 滑块（`aliyun.py`，`nc_1_*` DOM / scale 条 / /slide 校验包）是
**两个不同产品**：本 provider 面向新一代 `AliyunCaptcha.js` SDK 的弹窗拼图——
DOM 前缀 `aliyunCaptcha-*`（window-popup / sliding-slider / img / puzzle），
成功产物是回调里的 `captchaVerifyParam`（而非 cookie），典型部署为
z.ai / zchat.z.ai 登录（场景 1nl1cfkp）。

内化 2026-11-02 z.ai 实测（真渲染浏览器端到端拿 param，两次独立标定）：

1. **二次几何**：拼图块 CSS left = A·knob² + B·knob（`_slide_geometry`）。
   缺口→行程换算必须走反函数；1:1 拖必差 60px+。
2. **双检测器**：带 alpha 的整条拼图素材上模板匹配会出伪峰
   （`AliyunJigsawMatcher`：轮廓-边缘 × 填充-对比度）。
3. **换算链**：背景 296px 原图被拉伸到 300 CSS 宽（k≈1.0135）而拼图条 52px
   1:1；`L = imprint_x·k − bx0·piece_k` 先换 CSS 再二次反解，行程按
   `track − knob`（实测 260）夹紧。
4. **轨迹重定标必须显式**：缺口 bg px 与行程的相对差常落在 10% 以内
   （z.ai 典型 213→235 = 9.4%），任何带死区的缩放器都会静默跳过——
   本 provider 用 tolerance=0 显式重定标（review 发现的致命坑）。
5. **运行时红线**：控件会话分钟级过期（陈旧弹窗拖准也 F014/Timed out，
   fresh 15-30s 正常——尽快拖）；失败（F015 位置 / F001 风控）换 cert 并
   重置/关闭弹窗——重试前必须重新武装、重新定位；拖动中途不可刷新/中断；
   判定发生在 mouseup，释放前终点 dwell；成功文案是瞬态的，必须用
   MutationObserver 在拖拽前挂好（否则弹窗关闭后无从判定）。

两条使用路径：
- `SliderSolver` 通用链路（mixin 驱动）：detect→…→perform_slide→get_result，
  适用宿主页面自己弹出控件、成功后落 cookie 的普通 web 登录。
- `acquire_verify_param()` 一站式 API：本 provider 自行武装全新控件（注入
  holder/button + initAliyunCaptcha + show）、检测、拖拽、捕获 param，失败
  自动重试（重武装）。适用 API 登录类集成（拿到 param 后由调用方 POST 业务端点，
  如 z.ai 的 /auths/signin）。
"""

import asyncio
import base64
import json
import random
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger
from playwright.async_api import Page, Response

from slidex.providers import CaptchaProvider, ProviderElements, SolveResult
from slidex.vision.models import ChallengeType, ProviderManifest, VisionContext
from slidex._frames import iter_search_targets, wait_in_targets, query_in_targets
from slidex._image_match import AliyunJigsawMatcher, SliderImageMatcher
from slidex._slide_geometry import aliyun_jigsaw_knob_for_piece, scale_recorded_points

_KNOB = "#aliyunCaptcha-sliding-slider"
_TRACK = "#aliyunCaptcha-sliding-body"
_BG_IMG = "#aliyunCaptcha-img img"
_PIECE_IMG = "#aliyunCaptcha-puzzle img"
_POPUP = "#aliyunCaptcha-window-popup"
_TEXT_BOX = "#aliyunCaptcha-sliding-text-box"

_SUCCESS_PAT = ("验证通过", "验证成功", "verification success", "verified")
_FAIL_PAT = ("验证失败", "失败", "verification failed", "failed", "timed out", "再试")

# 滑块判定的 aliyuncs 端点（VerifyIntelligentCaptcha）。字段名随版本漂移，
# 多字段尽力判定；无法解析时返回 None，不阻塞 DOM/observer 兜底。
_VERIFY_URL_PAT = ("VerifyIntelligentCaptcha", "captcha-open.aliyuncs.com", "captcha.aliyuncs.com")

# 成功/失败判定注入点：宿主把 success 回调参数挂到这个 window 变量
PARAM_VAR = "__slidexCaptchaParam"
FAIL_VAR = "__slidexCaptchaFail"
VERDICT_VAR = "__slidexCaptchaVerdict"

# ── 页面内 JS（一次性武装全新控件；时序来自实测成功流）──────────────────────
_ARM_JS = """
async ({ sceneId, mode, region, prefix }) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const remove = id => { const e = document.getElementById(id); if (e) e.remove(); };
  // 旧 SDK DOM 全清（半状态弹窗会让新实例出图失败）
  ['aliyunCaptcha-mask', 'aliyunCaptcha-window-popup', 'aliyunCaptcha-window-embed',
   'slidex-cap-holder', 'slidex-cap-btn'].forEach(remove);
  const holder = document.createElement('div'); holder.id = 'slidex-cap-holder';
  const btn = document.createElement('button'); btn.id = 'slidex-cap-btn';
  btn.style.cssText = 'position:fixed;left:4px;top:4px;z-index:2147483647;width:40px;height:20px;opacity:0.01;';
  document.body.appendChild(holder); document.body.appendChild(btn);
  window.__slidexCaptchaParam = null; window.__slidexCaptchaFail = null;
  window.__slidexCaptchaVerdict = null;
  window.initAliyunCaptcha({
    SceneId: sceneId, mode: mode, element: '#slidex-cap-holder', button: '#slidex-cap-btn',
    getInstance: i => { window.__slidexCaptchaInst = i; },
    success: p => { window.__slidexCaptchaParam = (typeof p === 'string') ? p : ((p && p.captchaVerifyParam) || null);
                    window.__slidexCaptchaParamAt = Date.now(); },
    fail: e => { window.__slidexCaptchaFail = { code: (e && e.verifyCode) || null,
                                                msg: (e && e.verifyMsg) || null, at: Date.now() }; },
    onError: () => {}, onClose: () => {},
  });
  btn.click();
  await sleep(2600);
  try { window.__slidexCaptchaInst && window.__slidexCaptchaInst.show(); } catch (e) {}
  let knob = null;
  for (let i = 0; i < 14; i++) {
    await sleep(400);
    knob = document.getElementById('aliyunCaptcha-sliding-slider');
    if (knob && document.contains(knob) && knob.getBoundingClientRect().width > 0) break;
    if (i === 8) { try { window.__slidexCaptchaInst && window.__slidexCaptchaInst.show(); } catch (e) {} }
    knob = null;
  }
  if (!knob) return { ok: false, err: 'popup not visible' };
  await sleep(600);
  const bgImg = [...document.querySelectorAll('img')].find(i => /^data:image\\/png/.test(i.src) && (i.naturalWidth || 0) >= 200);
  const pzImg = [...document.querySelectorAll('img')].find(i => { const w = i.naturalWidth || 0; return /^data:image\\/png/.test(i.src) && w >= 40 && w <= 80; });
  if (!bgImg || !pzImg) return { ok: false, err: 'images not ready' };
  const box = el => { const b = el.getBoundingClientRect();
    return [Math.round(b.left * 100) / 100, Math.round(b.top * 100) / 100, Math.round(b.width * 100) / 100, Math.round(b.height * 100) / 100]; };
  const track = document.getElementById('aliyunCaptcha-sliding-body');
  return { ok: true,
    cert: (document.getElementById('aliyunCaptcha-certifyId') || {}).textContent || null,
    bgBox: box(bgImg), bgNat: [bgImg.naturalWidth, bgImg.naturalHeight],
    bgData: bgImg.src, pieceBox: box(pzImg), pieceNat: [pzImg.naturalWidth, pzImg.naturalHeight],
    pieceData: pzImg.src,
    knobBox: box(knob), trackBox: track ? box(track) : null };
}
"""

# 瞬态成功/失败文案的 MutationObserver：必须在拖拽前挂好（成功后弹窗随即
# 关闭，事后轮询什么都读不到——实测教训）。
_OBSERVER_JS = """
() => {
  if (window.__slidexCaptchaObs) return true;
  const classify = t => {
    const s = (t || '').trim().toLowerCase(); if (!s) return null;
    const SUCC = ['验证通过', '验证成功', 'verification success', 'verified'];
    const FAIL = ['验证失败', '失败', 'verification failed', 'failed', 'timed out', '再试'];
    if (SUCC.some(p => s.includes(p))) return 'success';
    if (FAIL.some(p => s.includes(p))) return 'fail';
    return null;
  };
  const box = document.getElementById('aliyunCaptcha-sliding-text-box');
  if (box) {
    const obs = new MutationObserver(() => {
      const v = classify(box.textContent);
      if (v && !(window.__slidexCaptchaVerdict && window.__slidexCaptchaVerdict.verdict === 'success')) {
        window.__slidexCaptchaVerdict = { verdict: v, text: String(box.textContent).slice(0, 80), at: Date.now() };
      }
    });
    obs.observe(box, { childList: true, characterData: true, subtree: true });
  }
  window.__slidexCaptchaObs = true;
  return true;
}
"""

# 合成手势（无 CDP 真鼠标的嵌入式 webview 宿主用；仅真渲染浏览器可行）。
# 关键点全部来自实测：pointerdown 打在滑块中心、move 派发到 document（气泡可
# 达）、目标是"滑块 computed left"故位移 = 目标 − 当前值、拟人慢-快-慢、释放前
# dwell、每次 move 重查元素（SDK 可能重建 DOM）、结束回读落点。
# 过冲默认关闭：唯一一次端到端成功（param 280 字符）的手势是纯前进无回拉，
# 带过冲的多次尝试均 F001——不排除风控对回拉敏感，故按成功样本对齐。
_SYNTHETIC_DRAG_JS = """
async ({ knobSel, targetLeft, stepPx, stepMs, overshoot }) => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const q = s => document.querySelector(s);
  const knob = q(knobSel);
  if (!knob || !document.contains(knob)) return { err: 'knob missing' };
  const kb = knob.getBoundingClientRect();
  const cur = parseFloat(getComputedStyle(knob).left) || 0;
  const disp = Math.max(0, Math.round(targetLeft - cur));
  const X0 = kb.left + kb.width / 2, Y0 = kb.top + kb.height / 2;
  const fire = (t, type, x, y, extra) => {
    const o = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y,
                screenX: x, screenY: y, button: 0, buttons: 1, view: window, detail: 1, ...extra };
    const ev = type.startsWith('pointer')
      ? new PointerEvent(type, { ...o, pointerId: 1, pointerType: 'mouse', isPrimary: true,
                                 width: 1, height: 1, pressure: type === 'pointerup' ? 0 : 0.5 })
      : new MouseEvent(type, o);
    t.dispatchEvent(ev);
  };
  fire(knob, 'pointerdown', X0, Y0);
  fire(knob, 'mousedown', X0, Y0);
  await sleep(100 + Math.random() * 60);
  const T = cur + disp;
  let d = cur; const pts = [];
  const phases = [[0.35, stepPx * 0.75], [0.75, stepPx * 1.25], [1, stepPx]];
  for (const [frac, step] of phases) {
    const upto = cur + Math.round(disp * frac);
    while (d < upto) { d = Math.min(d + step, T); pts.push(d); }
  }
  if (overshoot > 0) pts.push(T + overshoot, T - 1);
  pts.push(T, T, T);  // 终点 dwell（判定发生在 mouseup）
  for (const dd of pts) {
    const live = q(knobSel);
    if (!live || !document.contains(live)) return { err: 'knob gone mid-drag', at: dd };
    const jy = Y0 + (Math.random() - 0.5) * 2.2;
    fire(document, 'pointermove', X0 + (dd - cur), jy);
    fire(document, 'mousemove', X0 + (dd - cur), jy);
    await sleep(stepMs[0] + Math.random() * (stepMs[1] - stepMs[0]));
  }
  await sleep(110 + Math.random() * 60);
  fire(document, 'pointerup', X0 + disp, Y0, { buttons: 0 });
  const live = q(knobSel);
  if (live) fire(live, 'mouseup', X0 + disp, Y0, { buttons: 0 });
  await sleep(80);
  const pz = q('#aliyunCaptcha-puzzle');
  return { ok: true, target: Math.round(targetLeft),
           sl: live ? getComputedStyle(live).left : null,
           pz: pz ? getComputedStyle(pz).left : null };
}
"""


class AliyunCaptchaJsProvider(CaptchaProvider):
    """AliyunCaptcha.js 2.0 弹窗拼图（z.ai 登录等）"""

    name = "aliyun-captcha-js"
    description = "AliyunCaptcha.js 2.0 jigsaw popup (captchaVerifyParam)"
    manifest = ProviderManifest(
        name=name,
        version="0.2.0",
        challenge_types=[ChallengeType.SLIDER_CAPTCHA],
        contexts=[VisionContext.PLAYWRIGHT_PAGE, VisionContext.CDP],
        requires_network=False,
        produces_artifacts=["screenshot", "crop", "trajectory", "telemetry"],
    )

    def __init__(self):
        super().__init__()
        self._last_bx0: int = 0       # 最近一次 find_gap 的形状起点（几何换算用）
        self._last_gap_x: Optional[float] = None  # float 精度压痕左缘（mixin 会截成 int）
        self._last_gap_debug: Dict = {}

    # ── 检测 / 定位 ────────────────────────────────────────────────────────

    async def detect(self, page: Page) -> bool:
        """主文档或 iframe 里出现 aliyunCaptcha-* DOM 即命中。"""
        self._challenge_scope = None
        try:
            targets = await iter_search_targets(page)
        except Exception as e:
            logger.debug(f"AliyunCaptchaJsProvider.detect() targets error: {e}")
            return False
        for target in targets:
            for sel in (_POPUP, _KNOB):
                try:
                    el = await target.query_selector(sel)
                except Exception:
                    el = None
                if el:
                    self._challenge_scope = target
                    return True
        return False

    def _targets(self, page: Page):
        if self._challenge_scope is not None:
            return [self._challenge_scope]
        return [page]

    async def locate_elements(self, page: Page) -> ProviderElements:
        targets = self._targets(page)
        if self._challenge_scope is None:
            targets = await iter_search_targets(page)

        knob, used = await wait_in_targets(targets, _KNOB, timeout=10000)
        if not knob:
            raise RuntimeError("aliyunCaptcha slider knob not found")
        scope_targets = [used] if used is not None else targets

        track, _ = await query_in_targets(scope_targets, _TRACK)
        if not track:
            track, _ = await query_in_targets(targets, _TRACK)
        bg_img, _ = await query_in_targets(scope_targets, _BG_IMG)
        piece_img, _ = await query_in_targets(scope_targets, _PIECE_IMG)

        track_box = await track.bounding_box() if track else None
        knob_box = await knob.bounding_box()
        track_width_px = int(track_box["width"]) if track_box else 300
        knob_width_px = int(knob_box["width"]) if knob_box else 40
        self._challenge_scope = used or self._challenge_scope

        return ProviderElements(
            slider_btn=knob,
            slider_track=track,
            bg_img=bg_img,
            piece_img=piece_img,
            # 行程上限：滑块 computed left 最大值 = 轨道 − 滑块（实测 260）
            track_width_px=max(1, track_width_px - knob_width_px),
            metadata={
                "in_iframe": used is not None and used is not page,
                "slider_type": "jigsaw-captcha-js",
                "knob_width_px": knob_width_px,
            },
        )

    async def extract_images(
        self, page: Page, elements: ProviderElements
    ) -> Tuple[bytes, bytes]:
        """提取背景/拼图原图，并把几何换算链所需尺寸写进 metadata。

        data:URL 优先（原尺寸、带 alpha——双检测器的前提）；截图兜底会丢
        alpha，find_gap 自行回落经典模板匹配（近似语义）。
        """
        meta = elements.metadata

        async def _grab(el, key: str) -> Optional[bytes]:
            if el is None:
                return None
            src = await el.get_attribute("src")
            data = None
            if src and src.startswith("data:image"):
                try:
                    data = base64.b64decode(src.split(",", 1)[1])
                except Exception:
                    data = None
            if data is None:
                try:
                    data = await el.screenshot()
                except Exception:
                    return None
            try:
                box = await el.bounding_box()
                natural = await page.evaluate("el => [el.naturalWidth, el.naturalHeight]", el)
                if box and natural and natural[0]:
                    meta[f"{key}_box_w"] = float(box["width"])
                    meta[f"{key}_box_h"] = float(box["height"])
                    meta[f"{key}_nat_w"] = int(natural[0])
                    meta[f"{key}_nat_h"] = int(natural[1])
            except Exception:
                pass
            return data

        bg_bytes = await _grab(elements.bg_img, "bg")
        if bg_bytes is None and elements.slider_track is not None:
            bg_bytes = await elements.slider_track.screenshot()
        piece_bytes = await _grab(elements.piece_img, "piece")
        if bg_bytes is None or piece_bytes is None:
            raise RuntimeError("aliyunCaptcha bg/piece image extraction failed")
        return bg_bytes, piece_bytes

    # ── 缺口定位 ───────────────────────────────────────────────────────────

    async def find_gap(
        self,
        bg_bytes: bytes,
        piece_bytes: bytes,
    ) -> Tuple[Optional[int], float]:
        """双检测器定位。返回压痕左缘（背景图像素 x）与置信度。

        float 精度同步存 `self._last_gap_x`（mixin 会把 travel 截成 int，
        二次反解对 ±1px 敏感，终点换算必须用未截断值）。
        """
        gap_x, confidence, debug = AliyunJigsawMatcher.find_gap_from_bytes(
            bg_bytes, piece_bytes
        )
        self._last_gap_debug = debug
        self._last_bx0 = int(debug.get("bx0") or 0)
        if gap_x is None:
            # 无 alpha（截图兜底）或峰不孤立：回落经典 Canny+模板匹配。
            # 该匹配返回的是"模板中心"，补 −piece_w//2 对齐到"压痕左缘"语义。
            try:
                piece = cv2.imdecode(np.frombuffer(piece_bytes, np.uint8), cv2.IMREAD_COLOR)
                correction = -(piece.shape[1] // 2) if piece is not None else 0
            except Exception:
                correction = 0
            gap, conf = SliderImageMatcher.find_gap_with_confidence(
                bg_bytes, piece_bytes, offset_correction=correction
            )
            if gap is not None:
                self._last_gap_x = float(gap)
            return gap, conf
        self._last_gap_x = float(gap_x)
        return gap_x, confidence

    # ── 几何换算 ───────────────────────────────────────────────────────────

    def _knob_target(self, gap_x: float, elements: Optional[ProviderElements] = None) -> float:
        """压痕左缘（背景图像素 x，float）→ 滑块行程（CSS px，二次反解 + 夹紧）。"""
        meta = (elements.metadata or {}) if elements is not None else {}
        bg_nat = float(meta.get("bg_nat_w") or 0)
        bg_box = float(meta.get("bg_box_w") or 0)
        piece_nat = float(meta.get("piece_nat_w") or 0)
        piece_box = float(meta.get("piece_box_w") or 0)
        k = (bg_box / bg_nat) if bg_nat and bg_box else 1.0
        piece_k = (piece_box / piece_nat) if piece_nat and piece_box else 1.0
        bx0 = float(self._last_bx0)
        left_css = float(gap_x) * k - bx0 * piece_k
        target = aliyun_jigsaw_knob_for_piece(left_css)
        if elements is not None:
            target = min(target, float(elements.track_width_px))
        return max(0.0, target)

    @staticmethod
    def _rescale_points(
        points: List[Tuple[int, int, int]], gap_x: float, target: float
    ) -> List[Tuple[float, float, float]]:
        """把为 gap_x 生成的轨迹显式重定标到 target。

        必须 tolerance=0：缺口 bg px 与行程的相对差常在 10% 以内
        （z.ai 实测 213→235 = 9.4%），带死区的缩放器会静默跳过 →
        终点差 39px 必挂 F015（review 发现的致命坑）。
        """
        scaled = scale_recorded_points(points, float(gap_x or 1.0), float(target), tolerance=0.0)
        return [(x, y, delay) for (x, y, delay) in scaled]

    # ── 拖动 ───────────────────────────────────────────────────────────────

    async def _ensure_observer(self, page: Page) -> None:
        """挂瞬态文案 observer（成功后弹窗即关，事后轮询读不到）。"""
        try:
            await self._targets(page)[0].evaluate(_OBSERVER_JS)
        except Exception as e:
            logger.debug(f"aliyun-captcha-js observer install failed: {e}")

    async def perform_slide(
        self,
        page: Page,
        elements: ProviderElements,
        gap_x: int,
        trajectory: List[Tuple[int, int, int]],
        cdp_session: Optional[Any] = None,
        extra_overshoot: bool = True,
        end_hold_scale: float = 1.0,
    ) -> None:
        """拖滑块到二次反解出的行程（轨迹显式重定标 gap_x→T）。

        gap_x 语义来自 find_gap（背景图像素坐标）；trajectory 是为 gap_x
        生成的相对位移轨迹——形状（hold/抖动/收尾）保留，终点重定标到 T。
        """
        self.bind_response_listener(page)
        await self._ensure_observer(page)

        gap_used = float(gap_x)
        if self._last_gap_x is not None and abs(self._last_gap_x - gap_used) <= 1.0:
            gap_used = self._last_gap_x  # mixin 截断前的 float 精度
        target = self._knob_target(gap_used, elements)
        points = self._rescale_points(trajectory, gap_used, target)
        logger.info(
            f"[aliyun-captcha-js] gap_x={gap_used:.1f}px → knob target={target:.1f}px "
            f"(二次反解, points={len(points)})"
        )

        knob_box = await elements.slider_btn.bounding_box()
        if not knob_box:
            raise RuntimeError("Cannot get aliyunCaptcha knob bounding box")
        start_x = knob_box["x"] + knob_box["width"] / 2
        start_y = knob_box["y"] + knob_box["height"] / 2

        if cdp_session is not None:
            from slidex._drag import apply_end_hold_scale, build_drag_events, dispatch_drag_timeline

            pts_abs = [
                (start_x + float(x), start_y + float(y), float(delay or 0.0))
                for (x, y, delay) in points
            ]
            timeline = build_drag_events(
                start_x, start_y, pts_abs, extra_overshoot=bool(extra_overshoot)
            )
            apply_end_hold_scale(timeline, end_hold_scale)
            await dispatch_drag_timeline(cdp_session, timeline)
            return

        await page.mouse.move(
            start_x + random.uniform(-8, -3), start_y + random.uniform(2, 6)
        )
        await page.wait_for_timeout(random.randint(30, 80))
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(20, 60))
        await page.mouse.down()

        hold_ms = 0.0
        if points and points[0][0] == 0 and points[0][1] == 0 and points[0][2] > 0:
            hold_ms = float(points[0][2])
            slide_points = points[1:]
        else:
            hold_ms = random.uniform(600, 1200)
            slide_points = points
        await page.wait_for_timeout(int(hold_ms))

        for x, y, ts_ms in slide_points:
            await page.mouse.move(start_x + x, start_y + y)
            delay = 10 if ts_ms is None else max(0.0, float(ts_ms))
            if delay:
                await page.wait_for_timeout(int(delay))

        end_x = start_x + (slide_points[-1][0] if slide_points else 0.0)
        end_y = start_y + (slide_points[-1][1] if slide_points else 0.0)
        if extra_overshoot:
            overshoot = random.uniform(3.0, 6.0)
            back = random.uniform(2.0, 3.5)
            await page.mouse.move(end_x + overshoot, end_y + random.uniform(-1.5, 1.5))
            await page.wait_for_timeout(random.randint(60, 110))
            await page.mouse.move(end_x + overshoot - back, end_y + random.uniform(-1.0, 1.0))
            await page.wait_for_timeout(random.randint(50, 90))
            await page.mouse.move(end_x + random.uniform(-1.0, 1.0), end_y)
        from slidex._trajectory import slide_end_hold_range

        lo, hi = slide_end_hold_range()
        scale = max(0.0, float(end_hold_scale or 1.0))
        await page.wait_for_timeout(int(random.uniform(lo, hi) * 1000 * scale))
        await page.mouse.up()

    async def perform_slide_synthetic(
        self,
        page: Page,
        elements: ProviderElements,
        knob_target: float,
        *,
        step_px: float = 5.0,
        step_ms: Tuple[float, float] = (16.0, 30.0),
        overshoot: float = 0.0,
    ) -> Dict:
        """页面内合成 PointerEvent/MouseEvent 手势（无 CDP 真鼠标时的兜底）。

        仅适用于**真渲染浏览器**（嵌入式 webview 等只放行 evaluate 的宿主）；
        纯 DOM 伪造环境会被风控判 bot（happy-dom 路线 2026-09 起全拒）。
        过冲默认关闭——端到端唯一成功样本是纯前进手势（见 _SYNTHETIC_DRAG_JS）。
        返回 {ok, target, sl, pz} 供调用方核对落点。
        """
        await self._ensure_observer(page)
        return await page.evaluate(
            _SYNTHETIC_DRAG_JS,
            {
                "knobSel": _KNOB,
                "targetLeft": float(knob_target),
                "stepPx": float(step_px),
                "stepMs": [float(step_ms[0]), float(step_ms[1])],
                "overshoot": float(overshoot),
            },
        )

    # ── 一站式 API：武装 → 求解 → 捕获 param ──────────────────────────────

    async def acquire_verify_param(
        self,
        page: Page,
        scene_id: str,
        *,
        region: str = "cn",
        prefix: str = "no8xfe",
        mode: str = "popup",
        drag: str = "real",
        max_attempts: int = 2,
        verdict_timeout_ms: int = 10000,
    ) -> Dict:
        """全链路：武装全新控件 → 提图 → 检测 → 二次反解 → 拖拽 → 捕获 param。

        适用 API 登录类集成（z.ai /auths/signin 等）：param 由本 provider
        注入的 success 回调写到 `window.__slidexCaptchaParam`。失败（F015
        位置 / F001 风控）会换 cert 并重置弹窗——每轮重试都重新武装、重新
        定位（红线：陈旧控件拖准也挂）。

        Args:
            drag: "real" 走 page.mouse（CDP 可信事件）；"synthetic" 走页面内
                合成手势（嵌入式 webview 宿主）。

        Returns:
            {"param", "verdict", "fail_code", "attempts", "target"} — param
            为 captchaVerifyParam；verdict ∈ success/fail/None。
        """
        result: Dict[str, Any] = {
            "param": None, "verdict": None, "fail_code": None, "attempts": 0, "target": None,
        }
        for attempt in range(1, max(1, max_attempts) + 1):
            result["attempts"] = attempt
            geo = await page.evaluate(
                _ARM_JS, {"sceneId": scene_id, "mode": mode, "region": region, "prefix": prefix}
            )
            if not geo.get("ok"):
                logger.warning(f"[aliyun-captcha-js] arm attempt {attempt} failed: {geo.get('err')}")
                result["fail_code"] = result["fail_code"] or "ARM"
                continue
            bg_bytes = base64.b64decode(geo["bgData"].split(",", 1)[1])
            piece_bytes = base64.b64decode(geo["pieceData"].split(",", 1)[1])

            gap_x, confidence, debug = AliyunJigsawMatcher.find_gap_from_bytes(bg_bytes, piece_bytes)
            self._last_gap_debug = debug
            self._last_bx0 = int(debug.get("bx0") or 0)
            if gap_x is None:
                logger.warning(
                    f"[aliyun-captcha-js] attempt {attempt}: gap not found (conf={confidence:.2f})"
                )
                result["fail_code"] = "NO_GAP"
                continue
            self._last_gap_x = float(gap_x)

            track_w = float(geo["trackBox"][2]) if geo.get("trackBox") else 300.0
            knob_w = float(geo["knobBox"][2]) if geo.get("knobBox") else 40.0
            elements = ProviderElements(
                slider_btn=None, slider_track=None, bg_img=None, piece_img=None,
                track_width_px=max(1, int(track_w - knob_w)),
                metadata={
                    "bg_box_w": geo["bgBox"][2], "bg_nat_w": geo["bgNat"][0],
                    "piece_box_w": geo["pieceBox"][2], "piece_nat_w": geo["pieceNat"][0],
                },
            )
            target = self._knob_target(float(gap_x), elements)
            result["target"] = round(target, 1)
            logger.info(
                f"[aliyun-captcha-js] attempt {attempt}: gap={gap_x}px (conf={confidence:.2f}, "
                f"bx0={self._last_bx0}) → knob target={target:.1f}px, drag={drag}"
            )

            if drag == "synthetic":
                drag_res = await self.perform_slide_synthetic(page, elements, target)
                if drag_res.get("err"):
                    logger.warning(f"[aliyun-captcha-js] synthetic drag error: {drag_res}")
                    result["fail_code"] = "DRAG"
                    continue
            else:
                await self._drag_real(page, geo, target)

            verdict = await self._wait_param_or_fail(page, verdict_timeout_ms)
            result["verdict"] = verdict.get("verdict")
            if verdict.get("param"):
                result["param"] = verdict["param"]
                result["verdict"] = "success"
                logger.success(
                    f"[aliyun-captcha-js] verify param captured "
                    f"(attempt={attempt}, len={len(verdict['param'])})"
                )
                return result
            result["fail_code"] = verdict.get("fail_code") or result["fail_code"]
            logger.warning(
                f"[aliyun-captcha-js] attempt {attempt} failed: "
                f"verdict={verdict.get('verdict')}, code={verdict.get('fail_code')}"
            )
        return result

    async def _drag_real(self, page: Page, geo: Dict, target: float) -> None:
        """真鼠标拖拽（CDP 可信事件）：按下→拟人轨迹→过冲回拉→dwell→释放。"""
        kb = geo["knobBox"]
        start_x = kb[0] + kb[2] / 2
        start_y = kb[1] + kb[3] / 2
        await page.mouse.move(start_x + random.uniform(-8, -3), start_y + random.uniform(2, 6))
        await page.wait_for_timeout(random.randint(30, 80))
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(20, 60))
        await page.mouse.down()
        await page.wait_for_timeout(int(random.uniform(600, 1200)))
        # 慢-快-慢 + 过冲回拉，总时长 ~2s（过快会 F001）
        phases = [(0.35, target * 0.75, 3.0), (0.75, target, 5.0), (1.0, target, 4.0)]
        done = 0.0
        for frac_lo, upto, step in phases:
            while done < upto - 0.5:
                done = min(done + step, target)
                await page.mouse.move(
                    start_x + done, start_y + random.uniform(-1.1, 1.1)
                )
                await page.wait_for_timeout(int(random.uniform(13, 37)))
        await page.mouse.move(start_x + target + 2.0, start_y + random.uniform(-1.0, 1.0))
        await page.wait_for_timeout(random.randint(60, 110))
        await page.mouse.move(start_x + target - 1.0, start_y)
        await page.wait_for_timeout(random.randint(50, 90))
        await page.mouse.move(start_x + target, start_y)
        await page.wait_for_timeout(int(random.uniform(500, 800)))
        await page.mouse.up()

    async def _wait_param_or_fail(self, page: Page, timeout_ms: int) -> Dict:
        """轮询 param / fail / observer 判定，先到先得。"""
        deadline = asyncio.get_event_loop().time() + max(0.05, timeout_ms / 1000.0)
        while asyncio.get_event_loop().time() < deadline:
            try:
                st = await page.evaluate(
                    f"""() => ({{
                      p: window[{PARAM_VAR!r}] || null,
                      f: window[{FAIL_VAR!r}] || null,
                      v: window[{VERDICT_VAR!r}] || null,
                    }})"""
                )
            except Exception:
                st = {}
            if st.get("p"):
                return {"param": str(st["p"]), "verdict": "success", "fail_code": None}
            fail = st.get("f")
            if fail:
                return {"param": None, "verdict": "fail", "fail_code": fail.get("code")}
            v = st.get("v")
            if v and v.get("verdict") == "success":
                return {"param": None, "verdict": "success", "fail_code": None}
            if v and v.get("verdict") == "fail":
                return {"param": None, "verdict": "fail", "fail_code": "DOM"}
            await asyncio.sleep(0.15)
        return {"param": None, "verdict": None, "fail_code": None}

    async def wait_for_verify_param(
        self, page: Page, timeout_ms: int = 8000
    ) -> Optional[str]:
        """读取 `window.__slidexCaptchaParam`（本 provider 或调用方注入的
        success 回调写入）。供自行管理控件的集成单独使用。"""
        deadline = asyncio.get_event_loop().time() + max(0.05, timeout_ms / 1000.0)
        while asyncio.get_event_loop().time() < deadline:
            try:
                val = await page.evaluate(f"() => window[{PARAM_VAR!r}] || null")
            except Exception:
                val = None
            if val:
                return str(val)
            await asyncio.sleep(0.15)
        return None

    # ── 结果判定（mixin 通用链路）─────────────────────────────────────────

    async def validate_response(self, response: Response) -> Optional[bool]:
        url = response.url
        if not any(pat in url for pat in _VERIFY_URL_PAT):
            return None
        try:
            body = await response.body()
            data = json.loads(body.decode("utf-8", errors="ignore"))
            if not isinstance(data, dict):
                return None
            if "code" in data:
                try:
                    self._result_code = int(data["code"])
                except (TypeError, ValueError):
                    pass
            result = data.get("Result", data.get("VerifyResult"))
            if result is not None:
                text = str(result).strip().lower()
                if text in ("pass", "success", "true"):
                    return True
                if text in ("fail", "failed", "false"):
                    return False
            code = data.get("Code", data.get("code"))
            if code is not None and str(code) in ("Success", "1000"):
                return True
            return None
        except Exception as e:
            logger.debug(f"aliyun-captcha-js validate_response error: {e}")
            return None

    async def _dom_verdict(self, page: Page) -> Optional[bool]:
        """observer 判定优先（瞬态文案），否则直读文案条。iframe 感知。"""
        scope = self._targets(page)[0]
        try:
            st = await scope.evaluate(
                f"""() => ({{
                  v: window[{VERDICT_VAR!r}] || null,
                  t: (document.getElementById('{_TEXT_BOX.lstrip('#')}') || {{}}).textContent || null,
                }})"""
            )
        except Exception:
            st = {}
        v = (st or {}).get("v")
        if v and v.get("verdict") == "success":
            return True
        if v and v.get("verdict") == "fail":
            return False
        low = str((st or {}).get("t") or "").strip().lower()
        if not low:
            return None
        if any(p in low for p in _SUCCESS_PAT):
            return True
        if any(p in low for p in _FAIL_PAT):
            return False
        return None

    async def get_result(self, page: Page, timeout_ms: int = 5000) -> SolveResult:
        """observer / 校验包响应 / 文案条 三通道判定，先到先得。"""
        from slidex._cookies import select_cookies_for_url

        deadline = asyncio.get_event_loop().time() + max(0.05, timeout_ms / 1000.0)
        verdict: Optional[bool] = None
        while asyncio.get_event_loop().time() < deadline:
            if self._result is not None:
                verdict = bool(self._result)
                break
            verdict = await self._dom_verdict(page)
            if verdict is not None:
                break
            await asyncio.sleep(0.2)
        if verdict is None:
            return SolveResult(
                success=False,
                cookies=None,
                error=f"{self.name}: timeout waiting for result",
                code=-1,
            )
        cookies = await page.context.cookies()
        page_url = getattr(page, "url", "")
        if not isinstance(page_url, str):
            page_url = ""
        if verdict:
            return SolveResult(success=True, code=0, cookies=select_cookies_for_url(cookies, page_url))
        # 有判定但无数字码：按基类约定给 1，让手势会话换家族重采样
        return SolveResult(success=False, code=self._result_code if self._result_code is not None else 1)


__all__ = ["AliyunCaptchaJsProvider"]
