"""PX 求解器离线仿真 — 真 Chromium + 本地 HTTP 页，绝不碰真实站点。

页面只做一件事：用 JS 写 ``_px3`` 等 cookie。这样验证的是**生产同一条
链路**——建 context（UA/时区/locale/proxy 选项）、导航、从 cookie jar 抽
四件套——而不是假 context 对象。通过级与拒绝级各跑一遍，确保 ``:1000:``
判据真的从 cookie 里读出来。

本地浏览器/端口不可用时跳过；纯逻辑与假 context 的测试仍然兜底。
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from slidex.px.models import PxPressRequest
from slidex.px.solver import PxPressSolver

_PASS_PAGE = b"""<!doctype html><html><body>
<script>
document.cookie = "_px3=1:a:1000:abcdef::; path=/";
document.cookie = "_pxde={}; path=/";
document.cookie = "_pxvid=vid-1; path=/";
document.cookie = "_pxcts=cts-1; path=/";
</script>ok</body></html>"""

_LOW_PAGE = b"""<!doctype html><html><body>
<script>
document.cookie = "_px3=1:a:200:low::; path=/";
document.cookie = "_pxde=%7B%7D; path=/";
</script>low</body></html>"""


class _Server:
    def __init__(self, body: bytes) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib 约定
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 静默
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def _has_chromium() -> bool:
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except Exception:
        return False
    return True


@pytest.fixture
def pass_site():
    server = _Server(_PASS_PAGE)
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def low_site():
    server = _Server(_LOW_PAGE)
    try:
        yield server
    finally:
        server.close()


def _request(url: str, **overrides):
    payload = {
        "appId": "PXzC5j78di",
        "websiteURL": url,
        "country": "US",
        "pollInterval": 0.25,
        "timeout": 30,
    }
    payload.update(overrides)
    return PxPressRequest.from_payload(payload)


@pytest.mark.asyncio
async def test_real_browser_harvests_pass_quadruple(pass_site):
    """通过级 cookie → success，四件套齐全且 px3 含 :1000:。"""
    if not _has_chromium():  # pragma: no cover
        pytest.skip("playwright not installed")

    solver = PxPressSolver(headless=True, poll_interval_s=0.25, queue_timeout_s=10)
    try:
        try:
            result = await solver.solve(_request(pass_site.url))
        except Exception as exc:  # pragma: no cover - 浏览器缺失
            pytest.skip(f"local chromium unavailable: {exc}")
    finally:
        await solver.close()

    assert result.success is True, result.error
    assert ":1000:" in result.tokens["px3"]
    assert result.tokens["pxde"] == "{}"
    assert result.tokens["pxvid"] == "vid-1"
    assert result.tokens["pxcts"] == "cts-1"
    assert result.missing == []
    assert result.mode == "silent"


@pytest.mark.asyncio
async def test_real_browser_rejects_low_score_cookie(low_site, monkeypatch):
    """只有 :200: 级 → 宽限后判 blocked，不会把无段 token 当成功。"""
    if not _has_chromium():  # pragma: no cover
        pytest.skip("playwright not installed")
    monkeypatch.setattr("slidex.px.solver._BLOCKED_GRACE_S", 0.4)

    solver = PxPressSolver(headless=True, poll_interval_s=0.25, queue_timeout_s=10)
    try:
        result = await solver.solve(_request(low_site.url))
    finally:
        await solver.close()

    assert result.success is False
    assert result.error_kind == "px_token_blocked"
    assert result.retryable_error is True


@pytest.mark.asyncio
async def test_real_browser_is_reused_across_tasks(pass_site):
    """同一 solver 连跑两次：共享一个 Chromium，两个 context 都拿到 token。"""
    if not _has_chromium():  # pragma: no cover
        pytest.skip("playwright not installed")

    solver = PxPressSolver(headless=True, poll_interval_s=0.25, queue_timeout_s=10)
    try:
        first = await solver.solve(_request(pass_site.url))
        second = await solver.solve(_request(pass_site.url))
    finally:
        await solver.close()

    assert first.success is True and second.success is True
    assert first.tokens["px3"] == second.tokens["px3"]
