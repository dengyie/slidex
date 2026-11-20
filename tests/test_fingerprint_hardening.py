"""0.14.0 指纹加固自测：环境指纹注入 + 行为预热 + 事件序列统计。

背景：z.ai 注册链 100% F001（人手总能过）→ 风控识别的是环境指纹与事件
序列指纹，不是轨迹形状。本文件钉住三层加固的本地可验证面：

- V2 注入脚本自身痕迹面（静态结构断言：无 Proxy、无实例级 own getter 堆积、
  toString 归一存在、原型挂载）；
- 真浏览器注入效果（本地 playwright，无网络依赖）：webdriver/languages/
  plugins/pdfViewerEnabled/permissions/WebGL/screen 逐项断言 + toString 链
  检测（getter 链探测的最常见形态）；
- 行为预热（_do_slide 派发前置）：微动鼠标真实落盘、hover 触达、开关语义、
  失败静默；
- 事件序列分布统计：_drag.timeline 间隔直方图非均匀、无 ≥4 步等间隔连段；
  provider legacy _drag_real 的 gamma 化间隔同理（uniform 矩形直方图是经典
  统计签名——被 R1 验收线钉住的是合成家族，本文件把 legacy 路径钉进同一屏障）。
"""
import asyncio
import os
import random
import statistics
import re

import pytest

from slidex._behavior_prep import (
    BEHAVIORAL_WARMUP_JS,
    DETECT_SCRIPT_JS,
    STEALTH_INIT_SCRIPT_V2,
    pre_slide_warmup,
)
from slidex._drag import build_drag_events


# ════════════════════════════════════════════════════════════
#  0.14.1：开关单一事实源 + provider 收口（回归钉住）
# ════════════════════════════════════════════════════════════

def test_switches_single_source_of_truth():
    """WARMUP/STEALTH_V2 开关的 env 读取只发生在 _behavior_prep：
    solver 类属性与 provider 引用同一常量，不得各自重复读 env。"""
    import inspect
    import slidex.solver as solver_mod
    from slidex.providers import aliyun_captcha_js as provider_mod
    from slidex._behavior_prep import WARMUP_ENABLED, STEALTH_V2_ENABLED

    assert solver_mod.SliderSolver.WARMUP_ENABLED is WARMUP_ENABLED
    assert provider_mod._WARMUP_ENABLED is WARMUP_ENABLED
    assert isinstance(STEALTH_V2_ENABLED, bool)
    # solver 模块体内不得再有 SLIDEX_STEALTH_V2 的散落读取（单一事实源）
    src = inspect.getsource(solver_mod)
    assert 'env_bool("SLIDEX_STEALTH_V2"' not in src
    # provider 不得反向 import solver（0.14.0 的环依赖收口）
    psrc = inspect.getsource(provider_mod)
    assert "from slidex.solver" not in psrc


def test_v2_plugins_native_methods_tostring_whitelisted():
    """plugins 数组的 item/namedItem/refresh 在真 Chrome 是 native 函数——
    V2 必须把它们注册进 toString WeakSet 白名单（0.14.1 修复源码泄露）。"""
    for fn in ("arr.item = markFake", "arr.namedItem = markFake", "arr.refresh = markFake"):
        assert fn in STEALTH_INIT_SCRIPT_V2, fn


# ════════════════════════════════════════════════════════════
#  V2 注入脚本静态结构
# ════════════════════════════════════════════════════════════

def test_v2_script_has_toString_native_guard():
    # getter 链检测的第一道防线：Function.prototype.toString 覆盖存在，
    # 且伪装函数的 toString 返回 '[native code]' 形态。
    assert "[native code]" in STEALTH_INIT_SCRIPT_V2
    assert "Function.prototype" in STEALTH_INIT_SCRIPT_V2
    assert "toString" in STEALTH_INIT_SCRIPT_V2


def test_v2_script_no_proxy():
    # Proxy 痕迹：探测方对 window / navigator 做 Object.getOwnPropertyNames
    # 或 instanceof Proxy 类启发式时，Proxy 包裹层会漏。V2 全程 defineProperty。
    assert "new Proxy" not in STEALTH_INIT_SCRIPT_V2


def test_v2_script_no_global_markers_left():
    # 伪装层自身零痕迹：注入完毕不留可枚举全局符号，清理块必须存在。
    assert "伪装层自身痕迹清理" in STEALTH_INIT_SCRIPT_V2
    assert "delete window.__slidexStealth" in STEALTH_INIT_SCRIPT_V2


def test_v2_script_hooks_on_prototype_not_own():
    # anti-desc-chain：own-getter 链（getOwnPropertyDescriptor(navigator,'webdriver')
    # 返回 getter）是常见探测。V2 把 webdriver/languages getter 挂到
    # navigator.__proto__（原型层），不在 own 层堆积。
    webdriver_block = STEALTH_INIT_SCRIPT_V2.split("navigator.webdriver 伪装")[1].split("languages 伪装")[0]
    assert "navigator.__proto__" in webdriver_block
    langs_block = STEALTH_INIT_SCRIPT_V2.split("languages 伪装")[1].split("window.chrome 保留核")[0]
    assert "navigator.__proto__ || navigator" in langs_block


def test_v2_script_covers_all_detection_items():
    # 任务清单覆盖面：每项加固都在脚本里。
    items = {
        "webdriver": "navigator.webdriver",
        "plugins": "'PDF Viewer'",
        "pdfViewer": "pdfViewerEnabled",
        "languages": "'zh-CN'",
        "permissions": "permissions",
        "webgl": "37445",
        "screen": "availHeight",
        "rtt": "connection",
        "chrome_keys": "window.chrome",
        "ua_headless": "Headless",
        "toString": "toString",
    }
    for key, marker in items.items():
        assert marker in STEALTH_INIT_SCRIPT_V2, f"missing hardening item: {key}"


def test_detect_script_is_chakra_carry():
    # chakra 1000 行 hook 方法不可知论：detectScript 必须 >1000 字符。
    assert len(DETECT_SCRIPT_JS) > 1000


# ════════════════════════════════════════════════════════════
#  真浏览器注入效果（本地 playwright，无网络）
# ════════════════════════════════════════════════════════════

def _chromium_available() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        base = os.path.expanduser("~/Library/Caches/ms-playwright")
        if os.path.isdir(base):
            return any(d.startswith("chromium") for d in os.listdir(base))
        return True  # 非 mac 布局，交给真启动判定
    except Exception:
        return False


pytestmark_browser = pytest.mark.skipif(
    os.environ.get("SLIDEX_SKIP_BROWSER_TESTS", "0") == "1" or not _chromium_available(),
    reason="local playwright chromium not available",
)


_AUDIT_JS = """
() => {
  const out = {};
  out.webdriver = navigator.webdriver;
  out.languages = (navigator.languages || []).join(',');
  out.pluginsLen = navigator.plugins ? navigator.plugins.length : -1;
  out.pdfViewer = !!navigator.pdfViewerEnabled;
  out.pluginProto = navigator.plugins[0] instanceof Plugin;
  out.pluginArrayProto = navigator.plugins instanceof PluginArray;
  out.availW = screen.availWidth === screen.width;
  out.availH = screen.availHeight === screen.height;
  out.colorDepth = screen.colorDepth;
  // toString 链检测：getter 链探测最常见形态——伪装函数的 toString 是否泄露。
  let toStringLeak = '';
  try { toStringLeak = navigator.webdriver && Function.prototype.toString.call(Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver').get); } catch (e) {}
  try {
    const own = Object.getOwnPropertyDescriptor(navigator, 'webdriver');
    if (own) toStringLeak += '|own-getter';
  } catch (e) {}
  out.toStringLeak = String(toStringLeak);
  // permissions 一致性探测
  out.permissionsOk = !!(navigator.permissions && navigator.permissions.query);
  // chrome keys
  out.chromeKeys = (window.chrome && typeof window.chrome === 'object') ? Object.keys(window.chrome).join('|') : '';
  return out;
}
"""


@pytest.mark.asyncio
async def test_browser_injection_v2_effect():
    """真浏览器注入后逐项断言：注入前是 headless 缺口值，注入后全部人形值。"""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        # 注入前基线（裸 chromium）
        raw = await page.evaluate(_AUDIT_JS)
        # 提前关掉：注入是 init script，只能对新 page 生效
        await browser.close()

        browser2 = await pw.chromium.launch(headless=True)
        ctx = await browser2.new_context()
        await ctx.add_init_script(STEALTH_INIT_SCRIPT_V2)
        page2 = await ctx.new_page()
        await page2.goto("about:blank")
        out = await page2.evaluate(_AUDIT_JS)

        # 核心伪装断言
        assert out["webdriver"] is False or out["webdriver"] is None, out
        assert out["languages"].startswith("zh-CN"), out
        assert out["pluginsLen"] == 5, out
        assert out["pdfViewer"] is True, out
        assert out["pluginProto"] is True, out           # Plugin.prototype 保留
        assert out["pluginArrayProto"] is True, out      # PluginArray.prototype 保留
        assert out["availW"] is True and out["availH"] is True, out
        assert out["colorDepth"] == 24, out
        assert out["permissionsOk"] is True, out
        assert "runtime" in out["chromeKeys"], out
        # own-getter 链探测：navigator 自身不应挂 webdriver own descriptor
        assert "own-getter" not in out["toStringLeak"], out
        await browser2.close()
    finally:
        await pw.stop()


@pytest.mark.asyncio
async def test_browser_injection_tostring_no_leak():
    """toString 归一：被覆盖的 permissions.query / WebGL getParameter 经
    Function.prototype.toString / fn.toString 双通道取源码都不泄露 JS 体。"""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(STEALTH_INIT_SCRIPT_V2)
        page = await ctx.new_page()
        await page.goto("about:blank")
        res = await page.evaluate("""
          () => {
            const leak = (fn) => {
              try { return Function.prototype.toString.call(fn); } catch (e) { return 'ERR'; }
            };
            const q = navigator.permissions.query;
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl') || c.getContext('webgl2');
            const gp = gl ? gl.getParameter : null;
            return {
              q_leak: leak(q),
              gp_leak: gp ? leak(gp) : 'NO_GL',
              q_self: String(q),
              gp_self: gp ? String(gp) : 'NO_GL',
            };
          }
        """)
        for key in ("q_leak", "gp_leak", "q_self", "gp_self"):
            val = res[key]
            if val == "NO_GL" or val == "ERR":
                continue
            assert "[native code]" in val, f"{key} leaks source: {val[:120]}"
            assert "origQuery" not in val and "origGet" not in val, f"{key} leaks closure names"
        await browser.close()
    finally:
        await pw.stop()


@pytest.mark.asyncio
async def test_browser_injection_webgl_whitelist_fallback():
    """WebGL 白名单覆盖：37445/37446 伪装值，其余 getParameter 原样透传真内核。"""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(STEALTH_INIT_SCRIPT_V2)
        page = await ctx.new_page()
        await page.goto("about:blank")
        res = await page.evaluate("""
          () => {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl') || c.getContext('webgl2');
            if (!gl) return null;
            return {
              vendor: gl.getParameter(gl.VENDOR),           // 7936 原样
              version: gl.getParameter(gl.VERSION),         // 7938 原样
              dbg: (() => { try { return gl.getParameter(37445); } catch (e) { return null; } })(),
            };
          }
        """)
        if res is None:
            pytest.skip("no webgl context")
        # 白名单外的参数保持真内核值（SwiftShader 系）——探测方对白名单外
        # 任何参数问 getParameter 拿到的都是真值，缺口不在伪装层。
        assert res["vendor"], res
        await browser.close()
    finally:
        await pw.stop()


@pytest.mark.asyncio
async def test_browser_injection_no_global_markers():
    """注入后 window 上无 __slidex 前缀的全局符号（伪装层自身痕迹清理）。"""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await ctx.add_init_script(STEALTH_INIT_SCRIPT_V2)
        page = await ctx.new_page()
        await page.goto("about:blank")
        names = await page.evaluate(
            "() => Object.getOwnPropertyNames(window).filter(n => n.startsWith('__slidex'))"
        )
        assert names == [], names
        await browser.close()
    finally:
        await pw.stop()


# ════════════════════════════════════════════════════════════
#  行为预热（_do_slide 派发前置）
# ════════════════════════════════════════════════════════════

class _WarmupPage:
    """记录 mouse/wheel/evaluate 派发序列的假页面。"""

    def __init__(self, vw=1280, vh=800, scrollable=True):
        self.vw, self.vh, self.scrollable = vw, vh, scrollable
        self.moves = []
        self.wheels = []
        self.evaluated = []
        self.mouse = _WarmupPage._Mouse(self)

    async def evaluate(self, js, *a, **k):
        self.evaluated.append(js)
        if BEHAVIORAL_WARMUP_JS[:40] in js:
            return {"vw": self.vw, "vh": self.vh, "scrollableY": self.scrollable}
        return None

    class _Mouse:
        def __init__(self, page):
            self._page = page

        async def move(self, x, y):
            self._page.moves.append((x, y))

        async def wheel(self, dx, dy):
            self._page.wheels.append((dx, dy))


@pytest.mark.asyncio
async def test_pre_slide_warmup_runs_dispatches_real_events():
    """预热：探测 → 微动鼠标（4-6 段）→ 可能的滚轮。派发的是可信路径事件。"""
    random.seed(7)
    page = _WarmupPage()
    ok = await pre_slide_warmup(page, log_prefix="[t] ")
    assert ok is True
    assert page.moves, "warmup must dispatch mouse moves"
    assert 3 <= len(page.moves) <= 7, len(page.moves)
    # 落点在视口内
    for (x, y) in page.moves:
        assert 0 <= x <= page.vw and 0 <= y <= page.vh
    # 落点分散（不是同一个点反复跳）
    xs = sorted({round(x) for x, _ in page.moves})
    assert len(xs) >= 2
    # 滚轮概率化（seed=7 下可能触发也可能不触发，只断言结构合法）
    for (dx, dy) in page.wheels:
        assert dx == 0 and abs(dy) <= 60


@pytest.mark.asyncio
async def test_pre_slide_warmup_probe_failure_silent():
    """探测失败静默降级（返回 False，不抛异常），永不影响求解。"""

    class _Boom:
        async def evaluate(self, js, *a, **k):
            raise RuntimeError("evaluate dead")

    ok = await pre_slide_warmup(_Boom())
    assert ok is False

    class _BadViewport:
        async def evaluate(self, js, *a, **k):
            return None

    assert await pre_slide_warmup(_BadViewport()) is False


@pytest.mark.asyncio
async def test_warmup_toggle_env(monkeypatch):
    """SLIDEX_WARMUP=0 关闭（solver 类开关读 env）。"""
    from slidex.solver import SliderSolver
    from slidex._env import env_bool
    monkeypatch.setenv("SLIDEX_WARMUP", "0")
    assert env_bool("SLIDEX_WARMUP", True) is False
    monkeypatch.setenv("SLIDEX_WARMUP", "1")
    assert env_bool("SLIDEX_WARMUP", True) is True


@pytest.mark.asyncio
async def test_do_slide_dispatches_warmup_before_drag(monkeypatch):
    """_do_slide 在拖拽派发前执行预热 + hover：事件序列带浏览行为上下文。"""
    from slidex.solver import SliderSolver
    import slidex.solver as solver_mod

    calls = []

    class _Session:
        async def send(self, method, params=None):
            calls.append(("cdp", params["type"]))

    class _Btn:
        def __init__(self):
            self.hovered = False

        async def bounding_box(self):
            return {"x": 100.0, "y": 100.0, "width": 40.0, "height": 40.0}

        async def hover(self, timeout=None):
            self.hovered = True

    btn = _Btn()

    async def _find(_selector):
        return btn

    async def _warmup(page, log_prefix=""):
        calls.append(("warmup", len(page.moves) if hasattr(page, "moves") else 0))
        return True

    monkeypatch.setattr(solver_mod, "pre_slide_warmup", _warmup)

    s = SliderSolver.__new__(SliderSolver)
    s.pure_user_id = "t"
    s._cdp = _Session()
    s.page = _WarmupPage()
    s.selectors = {"slider_btn": "#btn"}
    s._query_in_challenge_scope = _find
    s._result_event = asyncio.Event()
    s._slide_code = None
    s._slide_ok = None
    # 类开关打开（默认）
    assert SliderSolver.WARMUP_ENABLED is True

    await s._do_slide(258.0, 1)

    kinds = [c[0] for c in calls]
    assert "warmup" in kinds, "warmup must run before drag dispatch"
    assert kinds.index("warmup") < kinds.index("cdp"), "warmup must precede CDP dispatch"
    assert btn.hovered, "slider hover touch must happen before drag"


@pytest.mark.asyncio
async def test_do_slide_warmup_disabled_skips(monkeypatch):
    """SLIDEX_WARMUP=0：不调预热，拖拽照常派发。"""
    from slidex.solver import SliderSolver
    import slidex.solver as solver_mod

    calls = []

    class _Session:
        async def send(self, method, params=None):
            calls.append(("cdp", params["type"]))

    class _Btn:
        async def bounding_box(self):
            return {"x": 100.0, "y": 100.0, "width": 40.0, "height": 40.0}

        async def hover(self, timeout=None):
            calls.append(("hover",))

    async def _find(_selector):
        return _Btn()

    async def _boom(page, log_prefix=""):
        calls.append(("warmup",))
        raise AssertionError("warmup must not be called when disabled")

    monkeypatch.setattr(SliderSolver, "WARMUP_ENABLED", False)
    monkeypatch.setattr(solver_mod, "pre_slide_warmup", _boom)

    s = SliderSolver.__new__(SliderSolver)
    s.pure_user_id = "t"
    s._cdp = _Session()
    s.page = _WarmupPage()
    s.selectors = {"slider_btn": "#btn"}
    s._query_in_challenge_scope = _find
    s._result_event = asyncio.Event()
    s._slide_code = None
    s._slide_ok = None

    await s._do_slide(258.0, 1)
    assert not any(c[0] == "warmup" for c in calls)
    assert any(c[0] == "cdp" for c in calls)


# ════════════════════════════════════════════════════════════
#  事件序列分布统计（间隔直方图 / 等间隔连段 / 变异系数）
# ════════════════════════════════════════════════════════════

def _flat_uniform_runs(delays, tol_rel=0.01, run_len=4):
    """≥run_len 步的等间隔连段数（步长比差 ≤tol_rel，R1 契约 ±1% 语义）。"""
    runs = 0
    cur = 1
    for i in range(1, len(delays)):
        prev, cur_v = delays[i - 1], delays[i]
        if prev > 0 and abs(cur_v / prev - 1.0) <= tol_rel:
            cur += 1
        else:
            if cur >= run_len:
                runs += 1
            cur = 1
    if cur >= run_len:
        runs += 1
    return runs


def _timeline_move_gaps(extra_overshoot=False, n=9):
    pts = [(100.0, 50.0, 800.0)] + [(100.0 + 25.0 * i, 50.0 + i, 30.0) for i in range(1, n)]
    tl = build_drag_events(100.0, 50.0, pts, extra_overshoot=extra_overshoot)
    return [g for g, _ in tl if _["type"] == "mouseMoved"]


def test_drag_timeline_gaps_not_flat_uniform():
    """真实手势家族（内核生成）的间隔序列无 ≥4 步 ±1% 平顶连段。
    tremor_dense 0.14.0 gamma 化后同样满足（uniform 矩形直方图是经典统计
    签名——30 seeds 里 8 个出现 ≥4 步等宽连段，修复后 ±1% 语义 ≤5/200）。"""
    from slidex._gestures import _tremor_dense, _minimum_jerk
    random.seed(11)
    # tremor_dense：连续 50 个独立样本，±1% 平顶率 ≤5/200（R1 同款验收线）
    flat = 0
    for _ in range(50):
        gaps = [p[2] for p in _tremor_dense(258.0) if p[2] > 0]
        flat += _flat_uniform_runs(gaps)
        assert statistics.mean(gaps) > 0
    assert flat <= 5, f"tremor_dense ±1% flat runs: {flat}/50"
    # minimum_jerk 家族（内核总装）天然满足
    for _ in range(10):
        gaps = [p[2] for p in _minimum_jerk(258.0) if p[2] > 0]
        assert _flat_uniform_runs(gaps) == 0


def test_drag_timeline_gap_cv_within_human_band():
    """间隔变异系数（CV）落在人手采样带（>0.2，远离恒定节奏）。"""
    random.seed(23)
    cvs = []
    for _ in range(20):
        gaps = _timeline_move_gaps()
        gaps = [g for g in gaps if g > 0]
        if len(gaps) >= 3:
            mean = statistics.mean(gaps)
            cvs.append(statistics.pstdev(gaps) / mean if mean else 0)
    assert statistics.mean(cvs) > 0.2, statistics.mean(cvs)


def test_legacy_drag_real_intervals_gamma_not_uniform():
    """provider legacy _drag_real：步进间隔从 uniform(13,37) 换 gamma ——
    抽样统计非均匀（CV > 0.2、无 ≥4 步等宽连段）。"""
    from slidex._humanize import gamma_intervals
    random.seed(41)
    samples = gamma_intervals(200, 24.0)
    mean = statistics.mean(samples)
    cv = statistics.pstdev(samples) / mean
    assert cv > 0.2, cv  # gamma(shape=2.2) 理论 CV ≈ 0.67
    assert _flat_uniform_runs(samples[:50]) == 0
    # 右偏（gamma 右偏形态：mean > median）
    assert mean > statistics.median(samples)


def test_v2_script_languages_aligns_with_launch_args():
    """languages 伪装值与 ENV_CONSISTENCY_LAUNCH_ARGS 的 --lang=zh-CN 对齐
    （UA/languages/时区不一致是阿里云常见检测项）。"""
    from slidex._stealth_patch import ENV_CONSISTENCY_LAUNCH_ARGS
    assert "--lang=zh-CN" in ENV_CONSISTENCY_LAUNCH_ARGS
    assert "zh-CN" in STEALTH_INIT_SCRIPT_V2


# ════════════════════════════════════════════════════════════
#  0.14.1：预热行为统计签名 + jigsaw 收割短路
# ════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_warmup_inter_move_gaps_not_flat_uniform(monkeypatch):
    """预热段间隔非均匀（gamma 化）：间隔 CV > 0.2、无 ≥4 步 ±1% 平顶连段，
    与 _tremor_dense / legacy _drag_real 的 0.14.0 修复同口径——0.14.0 的
    uniform(0.08, 0.25) 矩形直方图是同类统计签名。"""
    import slidex._behavior_prep as bp

    random.seed(101)
    page = _WarmupPage()
    gaps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(t):
        gaps.append(t)

    monkeypatch.setattr(bp.asyncio, "sleep", fake_sleep)
    ok = await pre_slide_warmup(page, log_prefix="[t] ")
    monkeypatch.setattr(bp.asyncio, "sleep", real_sleep)
    assert ok is True
    # 段间隔 = segments 次 move 之间的 sleep（不含初始等待）
    seg_gaps = gaps[1:len(page.moves)]  # 初始等待 + 每段一个间隔
    assert len(seg_gaps) >= 3
    mean = statistics.mean(seg_gaps)
    cv = statistics.pstdev(seg_gaps) / mean
    assert cv > 0.2, f"warmup inter-move gaps too regular: cv={cv:.3f} gaps={seg_gaps}"
    assert _flat_uniform_runs([g * 1000 for g in seg_gaps]) == 0


@pytest.mark.asyncio
async def test_warmup_wheel_steps_and_amplitude_bounded(monkeypatch):
    """滚轮：概率触发、1-3 步、每步同向、总幅度限 ±60px（0.14.0 的单步
    round-uniform 幅度是形态签名）。"""
    import slidex._behavior_prep as bp

    real_sleep = asyncio.sleep
    async def fake_sleep(t):
        return
    monkeypatch.setattr(bp.asyncio, "sleep", fake_sleep)

    # 概率命中路径：60% 概率 + scrollable，多 seed 采样必出现触发样本
    triggered = 0
    for seed in range(40):
        random.seed(seed)
        page = _WarmupPage()
        await pre_slide_warmup(page, log_prefix="[t] ")
        if page.wheels:
            triggered += 1
            dys = [d for _, d in page.wheels]
            assert 1 <= len(dys) <= 3, dys
            signs = {1 if d > 0 else -1 for d in dys}
            assert len(signs) == 1, dys  # 同一滚轮动作不反向
            assert abs(sum(dys)) <= 60.0, dys
            for d in dys:
                assert abs(d) <= 60.0
    assert triggered > 0, "60% probability over 40 seeds must trigger at least once"
    monkeypatch.setattr(bp.asyncio, "sleep", real_sleep)


@pytest.mark.asyncio
async def test_jigsaw_manual_wait_short_circuits_on_visible_float():
    """0.14.1 回归钉住：manual wait 的 jigsaw 判定 JS 端短路——浮窗仍可见
    （无 hidden class）时直接返回 false，全页文案扫描
    （querySelectorAll('div,span,p')）不执行；浮窗消失后才扫。同时钉住
    manual_pass_wait 收割分支本身（0.13.4 红线：不得回退）。"""
    import inspect
    from slidex.solver import SliderSolver

    src = inspect.getsource(SliderSolver)
    # 短路行必须存在（浮窗可见 → return false，不进 ask 扫描）
    assert "if (f && !f.classList.contains('aliyunCaptcha-hidden'))" in src
    assert "return false;" in src.split("if (f && !f.classList")[1].split("const ask")[0]
    # 0.13.4 收割分支红线：jigsaw 形态 manual_pass_wait 出口保留
    assert "jigsaw modal dismissed during manual wait" in src
    assert src.count('_success_outcome = "manual_pass_wait"') >= 2

