"""hCaptcha 机制离线仿真 — 本地 Chromium + 路由伪造的 hCaptcha iframe。

绝不触碰线上真实站点：页面用 `page.set_content` 构造，两个 iframe 的
`https://newassets.hcaptcha.com/captcha/v1/...frame=checkbox|challenge` 由
`page.route` 用本地 HTML 应答。这样走的是**生产同一条链路**——
按 URL 前缀找 frame、跨 frame 取 bounding_box、`page.mouse` 真实点击、
从宿主页 `textarea[name=h-captcha-response]` 读 token——而不是假对象。

识别引擎注入确定性假实现（直接返回 9 位真值），因此本测试不依赖任何视觉
API：验证的是「checkbox → 挑战出现 → 点中正确格子 → 提交 → 拿到 token」
这条流水线本身。本地浏览器不可用时跳过，纯逻辑层的三种测试仍然兜底。
"""
import pytest

from slidex.vision.hcaptcha import HcaptchaExecutor

_CHECKBOX_URL = "https://newassets.hcaptcha.com/captcha/v1/test/frame=checkbox"
_CHALLENGE_URL = "https://newassets.hcaptcha.com/captcha/v1/test/frame=challenge"

_MAIN_HTML = """
<!doctype html>
<html><body style="margin:0">
<div id="host"></div>
<textarea name="h-captcha-response" style="display:none"></textarea>
<script>
window.__clicks = [];
window.__submits = 0;
window.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'checkbox_clicked') {
    if (!document.getElementById('challenge-frame')) {
      const frame = document.createElement('iframe');
      frame.id = 'challenge-frame';
      frame.src = '%(challenge)s';
      frame.style.cssText = 'width:380px;height:480px;border:0';
      document.getElementById('host').appendChild(frame);
    }
  } else if (data.type === 'tile') {
    window.__clicks.push(data.index);
  } else if (data.type === 'submit') {
    window.__submits += 1;
  } else if (data.type === 'verdict' && data.pass) {
    document.querySelector("textarea[name='h-captcha-response']").value = 'PASS-TOKEN';
  }
});
const checkbox = document.createElement('iframe');
checkbox.id = 'checkbox-frame';
checkbox.src = '%(checkbox)s';
checkbox.style.cssText = 'width:300px;height:80px;border:0';
document.getElementById('host').appendChild(checkbox);
</script>
</body></html>
"""

_CHECKBOX_HTML = """
<!doctype html>
<html><body style="margin:0">
<div id="checkbox" style="width:30px;height:30px;border:1px solid #333;cursor:pointer"></div>
<script>
document.getElementById('checkbox').addEventListener('click', () => {
  window.parent.postMessage({type: 'checkbox_clicked'}, '*');
});
</script>
</body></html>
"""

_CHALLENGE_HTML = """
<!doctype html>
<html><head><style>
  body { margin: 0; font: 14px sans-serif; }
  .challenge-header { margin: 8px 10px; }
  .challenge-view { position: relative; width: 300px; height: 300px; margin: 8px 10px; }
  .task { position: absolute; width: 100px; height: 100px; box-sizing: border-box;
          border: 1px solid #ccd; background: #eef; cursor: pointer; }
  .button-submit { margin: 10px; width: 120px; height: 36px; background: #2b6;
                   color: #fff; display: flex; align-items: center;
                   justify-content: center; cursor: pointer; }
</style></head>
<body>
<div class="challenge-header">Please click all traffic lights</div>
<div class="challenge-view">
  <div class="task" style="left:0;top:0"></div>
  <div class="task" style="left:100px;top:0"></div>
  <div class="task" style="left:200px;top:0"></div>
  <div class="task" style="left:0;top:100px"></div>
  <div class="task" style="left:100px;top:100px"></div>
  <div class="task" style="left:200px;top:100px"></div>
  <div class="task" style="left:0;top:200px"></div>
  <div class="task" style="left:100px;top:200px"></div>
  <div class="task" style="left:200px;top:200px"></div>
</div>
<div class="button-submit button">Verify</div>
<script>
// 仿真器只认「正确的 3 格全部选中且没有误选」，与真实判题同形。
const CORRECT = [1, 4, 9];
const selected = new Set();
document.querySelectorAll('.task').forEach((node, i) => {
  node.addEventListener('click', () => {
    const index = i + 1;
    selected.add(index);
    node.style.outline = '3px solid #2b6';
    window.parent.postMessage({type: 'tile', index}, '*');
  });
});
document.querySelector('.button-submit').addEventListener('click', () => {
  window.parent.postMessage({type: 'submit'}, '*');
  const ok = selected.size === CORRECT.length && CORRECT.every((i) => selected.has(i));
  window.parent.postMessage({type: 'verdict', pass: ok}, '*');
});
</script>
</body></html>
"""


class _SimEngine:
    """确定性假引擎：直接给出 9 位真值（第 1/4/9 格）。"""

    provider = "fake-sim"

    def __init__(self, flags):
        self._flags = flags
        self.calls = 0

    def select_tiles(self, image_bytes, question, *, prompt=None):
        from slidex.vision.hcaptcha import TileSelection

        self.calls += 1
        return TileSelection(flags=list(self._flags), provider=self.provider, confidence=0.95)


async def _route_handler(route):
    url = route.request.url
    if "frame=checkbox" in url:
        body = _CHECKBOX_HTML
    elif "frame=challenge" in url:
        body = _CHALLENGE_HTML
    else:  # pragma: no cover - 仿真页不该请求其它地址
        return await route.abort()
    await route.fulfill(status=200, content_type="text/html", body=body)


@pytest.mark.asyncio
async def test_hcaptcha_binary_pipeline_passes_local_sim():
    """正确作答 → 提交一次 → 宿主页拿到 token，且只点了正确的 3 格。"""
    try:
        from playwright.async_api import async_playwright
    except Exception:  # pragma: no cover
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = None
    try:
        try:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 900, "height": 700})
            await page.route("https://newassets.hcaptcha.com/**", _route_handler)
            await page.set_content(_MAIN_HTML % {"challenge": _CHALLENGE_URL, "checkbox": _CHECKBOX_URL})
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"local chromium unavailable: {exc}")

        engine = _SimEngine([True, False, False, True, False, False, False, False, True])
        executor = HcaptchaExecutor(
            vision_engine=engine,
            inter_click_delay=(0.0, 0.0),
            settle_s=0.05,
            wait_challenge_s=8.0,
        )

        outcome = await executor.solve(page)

        assert outcome["success"] is True, outcome
        assert outcome["mode"] == "binary"
        assert outcome["question"] == "Please click all traffic lights"
        assert engine.calls == 1
        assert await page.evaluate("window.__clicks") == [1, 4, 9]
        assert await page.evaluate("window.__submits") == 1
        assert outcome["pass_token"] is True
        token = await page.input_value("textarea[name='h-captcha-response']")
        assert token == "PASS-TOKEN"
    finally:
        if browser is not None:
            await browser.close()
        await pw.stop()


@pytest.mark.asyncio
async def test_hcaptcha_wrong_answer_stops_without_resubmitting():
    """误选 → 仿真器不判过；执行器必须停手，绝不点第二次提交。"""
    try:
        from playwright.async_api import async_playwright
    except Exception:  # pragma: no cover
        pytest.skip("playwright not installed")

    pw = await async_playwright().start()
    browser = None
    try:
        try:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 900, "height": 700})
            await page.route("https://newassets.hcaptcha.com/**", _route_handler)
            await page.set_content(_MAIN_HTML % {"challenge": _CHALLENGE_URL, "checkbox": _CHECKBOX_URL})
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"local chromium unavailable: {exc}")

        engine = _SimEngine([False, True] + [False] * 7)  # 只点第 2 格 → 判错
        executor = HcaptchaExecutor(
            vision_engine=engine,
            inter_click_delay=(0.0, 0.0),
            settle_s=0.05,
            wait_challenge_s=8.0,
        )

        outcome = await executor.solve(page)

        assert outcome["success"] is False
        # 误选后仿真器只高亮、不换题：执行器应识别出「画面没变」并停手。
        assert outcome["error_code"] == "hcaptcha_verdict_pending"
        assert await page.evaluate("window.__submits") == 1
        token = await page.input_value("textarea[name='h-captcha-response']")
        assert token == ""
    finally:
        if browser is not None:
            await browser.close()
        await pw.stop()
