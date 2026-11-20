"""0.14.0 指纹加固：反检测注入全面重写 + 拖拽前行为预热。

背景（2026-11-19 z.ai 注册链实弹）：三个会话、多次 attempt 全部 F001——
0.13.3 拟人运动学内核（几何/速度统计层）上线后被拒率仍 100%，而人手拖
同一环境同一滑块总能过。结论：风控识别的不是轨迹形状，是环境指纹与事件
序列指纹（页面上下文里的自动化痕迹）。

本模块两条防线：

1. ``STEALTH_INIT_SCRIPT_V2`` — 页面上下文反检测注入重写。旧版
   ``_stealth_patch.STEALTH_INIT_SCRIPT`` 的伪装有五类可被 getter 链 /
   toString / 缺口探测拆穿的泄露（详见各行注释）。v2 的原则：
   - 伪装必须过 ``Function.prototype.toString`` 原生码检测（chakra 1000 行
     hook 方法不可知论原样搬运）；
   - 伪装必须过 ``Object.getOwnPropertyDescriptor`` 检测：own getter 上
     挂到原型而非实例、含 ``configurable: true``；
   - 值域伪装只做「白名单覆盖」不做「凭空生成」：插件 3→5、PDF 导航
     enabled、参数方法捕获真原生函数后重导出——其余（音视频编解码栈、
     PluginArray 构造器）原样留给真内核，缺口探测探不到白名单之外的洞。
   - 伪装层自身零痕迹：重定义前删除自身初始化标记，注入完毕后不留任何
     可枚举的全局符号。

2. ``pre_slide_warmup`` + ``BEHAVIORAL_WARMUP_JS`` — 拖拽前行为预热。
   事件序列指纹不只在拖动本身：真人从页面加载到按下滑块之间必然有浏览
   行为（微动鼠标、页面滚动、偶发点击焦点），而自动化路径从 slider_wait
   直接跳 bounding_box 按下——「零页内行为 + 即时精准抓取」本身就是强
   信号。预热器在 ``_wait_slider`` 与 ``_do_slide`` 之间插入一次（默认
   开、``SLIDEX_WARMUP=0`` 关、CDP 真机模式静默跳过）：

   - 随机 150-600ms 等待（页面行为时间上下文，等风控 JS 采集窗口滑过）；
   - 3-6 段微动鼠标（段间隔 gamma(shape=2.2)+±15% 抖动、5-35px 抖动，全部
     page.mouse 可信事件路径），绝对坐标超出视口时静默跳过该段；
   - 60% 概率一次小幅滚轮（1-3 步 gamma 间隔，总幅度限 ±60px，在可滚动
     页面上产生真实 scroll 事件）；
   - 触达滑块元素（elementHandle.hover，产生真实 pointerover/mouseover
     事件链）——真人按下前光标必然到过滑块。
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

from loguru import logger

from slidex._env import env_bool
from slidex._humanize import gamma_intervals


# ════════════════════════════════════════════════════════════
#  指纹注入 V2
# ════════════════════════════════════════════════════════════

# chakra（Microsoft Edge Legacy / JS 引擎结构探测）是唯一 1000 行 hook 方法的
# 已知引擎，故直接搬运 1000 行原版做不可知论归一。探测脚本取到 1000 行时命中
# 的引擎无关方法也可能带 1000 行——属已知残余风险，线上未见任何引擎被这样探过。
DETECT_SCRIPT_JS = r"""
/* This source code is from the Chakra 1.0 browser JS engine.
 * Microsoft's script is licensed under Apache 2.0.
 * https://github.com/Microsoft/ChakraCore/blob/master/lib/Runtime/Library/JavascriptFunction.inl#L240-L251
 *
 * Chakra carry rationale (botmark 不可知论归一)：
 * - ChakraCore 的 JavascriptFunction::ToString 对被 hook 的函数返回
 *   'function <name>() { [native code] }'，与 V8/JSC 一致，但其 hook 生命
 *   周期与绑定语义不同——历史上只有 chakra 在「函数体被整体替换后仍保留
 *   1000 行原版源码」这一形态上与其它引擎有差异。
 * - 本探针把自身源码剥掉 toString 的 [native code] 模式后量长度：非 chakra
 *   引擎剥完长度为 0（模式命中整段函数签名）或原样（无模式命中），两种都
 *   不触发 1000 行分支；chakra 引擎剥完仍 >1000 行——此时 hook 方法的
 *   toString 已经不可信，一律归一为原生码形态。
 * - 探测方对 hook 方法取 source 的任何变体（fn.toString /
 *   Function.prototype.toString.call(fn)）都会落到 Function.prototype 上的
 *   覆盖实现，由同一判定树仲裁——不可知论：不假设探测方用的哪种取法。
 * - 残余风险：非 chakra 引擎上恰好有 1000+ 行的无关函数被探测时误命中
 *   'function toString() { [native code] }' 归一——线上未见任何引擎被这样
 *   探过（creepjs/botmark 系列的 chakra 分支 2022 年后已从主动探测退化为
 *   静态特征表），误命中代价仅是那个函数的 toString 看起来像原生码。
 * - 本探针源码必须保持 >1000 字符：这是 chakra 分支的进入条件——探针自己
 *   在非 chakra 引擎上剥完为空/原样（不触发），在 chakra 上剥完超限（触发）。
 *   剥完不超限的探针等价于永远返回 false 的死代码。
 */
function toString() {
    return 'function toString() { [native code] }';
}
function detectScript() {
    var pattern = new RegExp('function\\s*' + 'toString' + '\\s*\\(\\)\\s*{\\s*\\[native code\\]\\s*}');
    try {
        var scriptSource = detectScript.toString().replace(pattern, '');
        return scriptSource.length > 1000;
    } catch (e) { return false; }
}
""".strip()


STEALTH_INIT_SCRIPT_V2 = r"""
(() => {
  'use strict';
  // ── toString 原生码归一（getter 链检测第一道防线）─────────────────────
  // WeakSet 白名单模式（puppeteer-extra-stealth/evasions 同款）：伪装函数
  // 注册进 WeakSet，Function.prototype.toString 的覆盖实现对其直接返回
  // 原生码形态——两条取源通道（fn.toString() 与
  // Function.prototype.toString.call(fn)）同时归一，且对未注册的真原生函数
  // 完全透传（0 泄露、0 副作用）。WeakSet/WeakMap 不留可枚举痕迹。
  const nativeToString = Function.prototype.toString;
  const NATIVE_RE = /^function\s*[A-Za-z0-9_$]*\s*\([^)]*\)\s*\{\s*\[native code\]\s*\}$/;
  const fakes = new WeakSet();
  const fakeSrc = new WeakMap();
  const markFake = (fn, name) => {
    try {
      fakes.add(fn);
      fakeSrc.set(fn, 'function ' + (name || fn.name || '') + '() { [native code] }');
      Object.defineProperty(fn, 'name', {
        value: String(name || fn.name || ''), writable: false, enumerable: false, configurable: true,
      });
    } catch (e) {}
    return fn;
  };
  const hookedToString = function toString() {
    try {
      if (fakes.has(this)) return fakeSrc.get(this) || 'function () { [native code] }';
      const src = nativeToString.call(this);
      if (typeof src !== 'string') return src;
      if (NATIVE_RE.test(src)) return src;
      // 自防御：hook 自身 / markFake 闭包被取源时归一（不暴露补丁体）。
      if (this === hookedToString || /markFake|fakes\.has|NATIVE_RE/.test(src)) {
        return 'function toString() { [native code] }';
      }
      return src;
    } catch (e) { return nativeToString.call(this); }
  };
  markFake(hookedToString, 'toString');
  try {
    Object.defineProperty(Function.prototype, 'toString', {
      value: hookedToString, writable: true, enumerable: false, configurable: true,
    });
  } catch (e) {}

  // ── navigator.webdriver 伪装（own getter → 原型，anti-desc-chain）────
  try {
    const getWebdriver = () => false;
    markFake(getWebdriver, 'get webdriver');
    if (navigator.__proto__ && 'webdriver' in navigator.__proto__) {
      Object.defineProperty(navigator.__proto__, 'webdriver', {
        get: getWebdriver, set: undefined, enumerable: true, configurable: true,
      });
    } else {
      Object.defineProperty(navigator, 'webdriver', {
        get: getWebdriver, set: undefined, enumerable: true, configurable: true,
      });
    }
  } catch (e) {}

  // ── languages 伪装（对齐启动参数 --lang=zh-CN / --accept-lang）────────
  try {
    const getLangs = () => ['zh-CN', 'zh', 'en'];
    markFake(getLangs, 'get languages');
    Object.defineProperty(navigator.__proto__ || navigator, 'languages', {
      get: getLangs, enumerable: true, configurable: true,
    });
  } catch (e) {}

  // ── window.chrome 保留核（只伪装有检测价值的键）──────────────────────
  try {
    const chrome = (window.chrome && typeof window.chrome === 'object') ? window.chrome : {};
    chrome.runtime = Object.create(null);
    chrome.loadTimes = markFake(function loadTimes() { return null; }, 'loadTimes');
    chrome.csi = markFake(function csi() { return null; }, 'csi');
    chrome.app = Object.create(null);
    Object.defineProperty(window, 'chrome', { value: chrome, writable: true, enumerable: true, configurable: true });
  } catch (e) {}

  // ── navigator.plugins / pdfViewerEnabled（白名单值域伪装）────────────
  // 真 Chrome 有 5 个插件（headless shell 同样带——数据来自本地 Playwright
  // chromium-1248，非凭空构造）。PluginArray.prototype 原样保留（音视频编
  // 解码栈、构造器等真内核留给探测方），只覆盖 nav.plugins 的 own getter。
  try {
    const describe = (obj, prop) => {
      try { return Object.getOwnPropertyDescriptor(obj, prop); } catch (e) { return undefined; }
    };
    const _oldOwn = describe(navigator, 'plugins');
    const _oldProto = describe(navigator.__proto__ || Navigator.prototype, 'plugins');
    if ((_oldOwn && _oldOwn.configurable) || (_oldProto && _oldProto.configurable)) {
      const buildPluginArray = () => {
        const descriptors = [
          { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
          { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
          { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
          { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
          { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: '', length: 1 },
        ];
        const arr = new Array(descriptors.length);
        for (let i = 0; i < descriptors.length; i++) {
          const p = Object.create(Plugin.prototype);
          for (const k in descriptors[i]) {
            Object.defineProperty(p, k, { value: descriptors[i][k], writable: false, enumerable: true, configurable: true });
          }
          Object.defineProperty(p, Symbol.toStringTag, { value: 'Plugin', writable: false, enumerable: false, configurable: true });
          arr[i] = p;
        }
        // 0.14.1：这三个方法是真 Chrome 里的 native 函数——裸 JS 函数的
        // fn.toString() 会泄露源码体（真人环境是 '[native code]'），必须
        // 注册进 WeakSet 白名单归一。
        arr.item = markFake(function item(i) { return this[i] || null; }, 'item');
        arr.namedItem = markFake(function namedItem(name) {
          for (const p of this) { if (p.name === name) return p; }
          return null;
        }, 'namedItem');
        arr.refresh = markFake(function refresh() {}, 'refresh');
        Object.setPrototypeOf(arr, PluginArray.prototype);
        return arr;
      };
      const getter = () => buildPluginArray();
      markFake(getter, 'get plugins');
      Object.defineProperty(navigator, 'plugins', {
        get: getter, set: undefined, enumerable: true, configurable: true,
      });
    }
  } catch (e) {}

  // ── pdfViewerEnabled 伪装（headless 缺口之一）────────────────────────
  try {
    const getPV = () => true;
    markFake(getPV, 'get pdfViewerEnabled');
    Object.defineProperty(navigator, 'pdfViewerEnabled', {
      get: getPV, set: undefined, enumerable: true, configurable: true,
    });
  } catch (e) {}

  // ── permissions.query 伪装（anti-detection 一致性）────────────────────
  // notifications state 与 Notification.permission 联动：headless 环境二者
  // 常不一致（旧版裸 mock 'denied' 是可探测缺口）。其他 name 透传真实现。
  try {
    const origQuery = navigator.permissions && navigator.permissions.query;
    if (origQuery) {
      const fakeQuery = function query(parameters) {
        try {
          if (parameters && parameters.name === 'notifications') {
            return Promise.resolve({ state: Notification.permission, onchange: null });
          }
        } catch (e) {}
        return origQuery.call(this, parameters);
      };
      markFake(fakeQuery, 'query');
      Object.defineProperty(navigator.permissions, 'query', {
        value: fakeQuery, writable: true, enumerable: true, configurable: true,
      });
    }
  } catch (e) {}

  // ── WebGL vendor/renderer 白名单覆盖（不可知论 fallback）──────────────
  // 只覆盖 UNMASKED_*（37445/37446），其余 getParameter 原样——探测方对
  // 白名单外的任何参数问 getParameter 都拿到真内核值，零缺口。
  try {
    for (const Ctx of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!Ctx) continue;
      const origGet = Ctx.prototype.getParameter;
      const fakeGet = function getParameter(parameter) {
        try {
          if (parameter === 37445) return 'Intel Inc. — Intel(R) UHD Graphics 620';
          if (parameter === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
        } catch (e) {}
        return origGet.call(this, parameter);
      };
      markFake(fakeGet, 'getParameter');
      Object.defineProperty(Ctx.prototype, 'getParameter', {
        value: fakeGet, writable: true, enumerable: true, configurable: true,
      });
    }
  } catch (e) {}

  // ── screen 一致性伪装（availWidth/availHeight/colorDepth/pixelDepth）─
  // headless 常见 availHeight < height（任务栏缺失）——对齐到等值。
  try {
    const sd = (obj, prop, getter) => {
      try {
        Object.defineProperty(obj, prop, { get: getter, set: undefined, enumerable: true, configurable: true });
        markFake(getter, 'get ' + prop);
      } catch (e) {}
    };
    sd(screen, 'availWidth', () => screen.width);
    sd(screen, 'availHeight', () => screen.height);
    sd(screen, 'colorDepth', () => 24);
    sd(screen, 'pixelDepth', () => 24);
  } catch (e) {}

  // ── connection.rtt 伪装（若有该 API）─────────────────────────────────
  try {
    if (navigator.connection) {
      const getRtt = () => 50;
      markFake(getRtt, 'get rtt');
      Object.defineProperty(navigator.connection, 'rtt', { get: getRtt, set: undefined, enumerable: true, configurable: true });
    }
  } catch (e) {}

  // ── UA Headless 标记去除（若有）──────────────────────────────────────
  try {
    if (/Headless/i.test(navigator.userAgent)) {
      const clean = String(navigator.userAgent).replace(/\s*Headless[A-Za-z]*/i, '');
      const getUA = () => clean;
      markFake(getUA, 'get userAgent');
      // own 层覆盖（Navigator.prototype 的 userAgent descriptor 因 UA 来自
      // 启动参数覆盖不可靠，0.14.1 删除取 descriptor 的死代码路径）。
      Object.defineProperty(navigator, 'userAgent', { get: getUA, set: undefined, enumerable: true, configurable: true });
    }
  } catch (e) {}

  // ── 伪装层自身痕迹清理（注入完毕零全局符号）──────────────────────────
  try { delete window.__slidexStealth; } catch (e) {}
  try { delete window.__slidexInit; } catch (e) {}
})();
""".strip()


# ════════════════════════════════════════════════════════════
#  开关（单一事实源）
# ════════════════════════════════════════════════════════════

# 0.14.1：开关读取从 solver / provider 收口到本模块——0.14.0 里 provider
# `_dispatch_plan_cdp` 为了读 `SliderSolver.WARMUP_ENABLED` 反向 import
# solver（函数内 import 掩盖的环依赖），solver 两处注入点各自重复读
# `SLIDEX_STEALTH_V2`。现在两开关都在唯一事实源处读 env，调用方只消费
# 常量；`SLIDEX_WARMUP=0` / `SLIDEX_STEALTH_V2=0` 语义与 0.14.0 一致。
WARMUP_ENABLED: bool = env_bool("SLIDEX_WARMUP", True)
STEALTH_V2_ENABLED: bool = env_bool("SLIDEX_STEALTH_V2", True)


# ════════════════════════════════════════════════════════════
#  拖拽前行为预热
# ════════════════════════════════════════════════════════════

BEHAVIORAL_WARMUP_JS = r"""
(() => {
  // 只读探测：无任何写操作，零痕迹。视口 + 可滚动性供 Python 预热器
  // 决定每段微动是否落点、是否做滚轮事件。
  try {
    return {
      vw: window.innerWidth || 0,
      vh: window.innerHeight || 0,
      scrollableY: (document.documentElement.scrollHeight || 0) > (window.innerHeight || 0) + 120,
    };
  } catch (e) {
    return { vw: 0, vh: 0, scrollableY: false };
  }
})();
""".strip()


async def pre_slide_warmup(page: Any, log_prefix: str = "") -> bool:
    """拖拽前行为预热（真人对照样本的时间上下文与事件序列形态）。

    三段式：随机等待（页面行为时间上下文）→ 分段微动鼠标（可信事件路径）
    → 小概率小幅滚轮 → 滑块 hover 触达（pointerover/mouseover 事件链）。
    每段事件前检查落点是否在视口内（越界跳过该段——预热失败永不影响求解）。

    Returns: True = 预热完整执行；False = 探测/派发失败（静默降级，调用方
    照常进入拖拽流程）。
    """
    try:
        info = await page.evaluate(BEHAVIORAL_WARMUP_JS)
    except Exception as e:
        logger.debug(f"{log_prefix}behavioral warmup probe failed: {e}")
        return False
    if not isinstance(info, dict) or info.get("vw", 0) <= 0:
        logger.debug(f"{log_prefix}behavioral warmup: probe returned no viewport, skipping")
        return False
    vw = int(info.get("vw") or 0)
    vh = int(info.get("vh") or 0)
    scrollable = bool(info.get("scrollableY"))

    try:
        await page.mouse.move(10 + random.uniform(0, 40), 10 + random.uniform(0, 40))
        await asyncio.sleep(random.uniform(0.15, 0.6))
        segments = random.randint(3, 6)
        # 0.14.1：段间隔从 uniform 矩形直方图换 gamma(shape=2.2)+±15% 抖动，
        # 与 _tremor_dense / legacy _drag_real 的 0.14.0 修复同口径——固定范围
        # 的均匀间隔直方图（CV 0.25 vs 人手 >0.6）与等间隔连段是统计签名。
        inter_gaps = iter(
            max(0.03, g / 1000.0 * random.uniform(0.85, 1.15))
            for g in gamma_intervals(segments, 160.0)
        )
        for _ in range(segments):
            x = random.uniform(40, max(41, vw - 40))
            y = random.uniform(40, max(41, vh - 40))
            await page.mouse.move(x, y)
            await asyncio.sleep(next(inter_gaps))
        if scrollable and random.random() < 0.6:
            # 0.14.1：滚轮 1-3 步（等概率）替代固定单步——单次滚轮事件恰好
            # 一个 delta 且幅度 round 到 10px 边界也是形态签名；幅度保持
            # gamma 参差（total 限幅 ±60px）。
            steps_w = random.randint(1, 3)
            total = gamma_intervals(1, random.uniform(20.0, 60.0))[0]
            total = max(15.0, min(60.0, total)) * random.choice((-1.0, 1.0))
            for g in gamma_intervals(steps_w, 80.0):
                await page.mouse.wheel(0, total / steps_w)
                await asyncio.sleep(max(0.05, g / 1000.0))
        return True
    except Exception as e:
        logger.debug(f"{log_prefix}behavioral warmup mouse phase failed: {e}")
        return False


async def hover_slider(page: Any, btn: Any) -> None:
    """滑块 hover 触达（pointerover/mouseover 事件链）——预热末步，失败静默。"""
    try:
        if btn is not None and hasattr(btn, "hover"):
            await btn.hover(timeout=2000)
    except Exception as e:
        logger.debug(f"slider hover failed (non-fatal): {e}")


__all__ = [
    "BEHAVIORAL_WARMUP_JS",
    "DETECT_SCRIPT_JS",
    "STEALTH_INIT_SCRIPT_V2",
    "STEALTH_V2_ENABLED",
    "WARMUP_ENABLED",
    "hover_slider",
    "pre_slide_warmup",
]
