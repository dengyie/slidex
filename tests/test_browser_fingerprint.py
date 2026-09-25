"""0.6.22: 容器浏览器指纹加固 — channel 升级（真 Chrome 优先）+ 环境一致性旗标 + 指纹自审计。

背景：容器路径滑块被拒定位为环境指纹问题（真人拖也 code=300）。本组测试钉住：
- channel 解析约定（XY_SLIDER_BROWSER_CHANNEL env 与 stealth.py 同名同义，auto 探测 google-chrome-stable）
- patchright 下只补环境一致性旗标（GL/语言），反检测面交给 patchright 本体
- 指纹自审计：默认开、env 可关、失败不阻断
"""
import pytest

from slidex.solver import SliderSolver, _resolve_browser_channel
from slidex import solver as solver_module
from slidex._stealth_patch import STEALTH_LAUNCH_ARGS, ENV_CONSISTENCY_LAUNCH_ARGS


# ---------- channel 解析 ----------

def test_channel_env_explicit(monkeypatch):
    monkeypatch.setenv("XY_SLIDER_BROWSER_CHANNEL", "msedge")
    assert _resolve_browser_channel() == "msedge"


def test_channel_env_force_chromium(monkeypatch):
    for v in ("chromium", "none", "off"):
        monkeypatch.setenv("XY_SLIDER_BROWSER_CHANNEL", v)
        assert _resolve_browser_channel() is None, v


def test_channel_autodetect_chrome(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(solver_module.shutil, "which", lambda name: "/usr/bin/google-chrome-stable" if name.startswith("google-chrome") else None)
    assert _resolve_browser_channel() == "chrome"


def test_channel_autodetect_none(monkeypatch):
    monkeypatch.delenv("XY_SLIDER_BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(solver_module.shutil, "which", lambda name: None)
    assert _resolve_browser_channel() is None


# ---------- _init_browser 组装 ----------

class _FakePage:
    def __init__(self):
        self.init_scripts = []
        self.handlers = {}
        self.evaluated = []

    async def add_init_script(self, script):
        self.init_scripts.append(script)

    def on(self, event, handler):
        self.handlers[event] = handler

    async def evaluate(self, js, *a, **kw):
        self.evaluated.append(js)
        return {"ua": "test", "glRenderer": "Mesa LLVMpipe"}


class _FakeContext:
    def __init__(self):
        self.page = _FakePage()

    async def new_page(self):
        return self.page

    async def new_cdp_session(self, page):
        raise RuntimeError("no cdp session in fake")


class _FakeChromium:
    def __init__(self, captured):
        self._captured = captured

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self._captured.update(kwargs)
        self._captured["user_data_dir"] = user_data_dir
        return _FakeContext()


class _FakePW:
    def __init__(self, captured):
        self.chromium = _FakeChromium(captured)

    async def start(self):
        return self


class _Recorder:
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, msg, *a, **kw):
        self.infos.append(str(msg))

    def warning(self, msg, *a, **kw):
        self.warnings.append(str(msg))

    def __getattr__(self, name):
        return lambda *a, **kw: None


def _make_solver(tmp_path):
    s = SliderSolver(cookie_id="fp_test", cookies_str="")
    s.profile_dir = tmp_path / "prof"
    return s


@pytest.mark.asyncio
async def test_init_browser_uses_chrome_channel_and_consistency_args(monkeypatch, tmp_path):
    monkeypatch.delenv("XY_SLIDER_AUTOMATION_BACKEND", raising=False)
    monkeypatch.delenv("XY_SLIDER_BROWSER_CHANNEL", raising=False)
    monkeypatch.setattr(solver_module.shutil, "which", lambda name: "/usr/bin/google-chrome-stable" if name.startswith("google-chrome") else None)
    captured = {}
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePW(captured))
    rec = _Recorder()
    monkeypatch.setattr(solver_module, "logger", rec)

    s = _make_solver(tmp_path)
    await s._init_browser()

    assert captured.get("channel") == "chrome"
    assert "--use-angle=gl" in captured["args"]
    assert "--accept-lang=zh-CN,zh;q=0.9" in captured["args"]
    assert s.browser_channel == "chrome"
    # 审计默认开启：evaluate 被调用且落了一行指纹日志
    assert s.page.evaluated
    assert any("browser fingerprint" in m and "Mesa LLVMpipe" in m for m in rec.infos)


@pytest.mark.asyncio
async def test_init_browser_patchright_only_consistency_args(monkeypatch, tmp_path):
    monkeypatch.setenv("XY_SLIDER_AUTOMATION_BACKEND", "patchright")
    captured = {}
    monkeypatch.setattr(solver_module, "patchright_async_playwright", lambda: _FakePW(captured))
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePW(captured))
    monkeypatch.setattr(solver_module, "logger", _Recorder())

    s = _make_solver(tmp_path)
    await s._init_browser()

    # patchright 下不携带 STEALTH_LAUNCH_ARGS 反检测堆（交给本体），只补环境一致性旗标
    for a in ENV_CONSISTENCY_LAUNCH_ARGS:
        assert a in captured["args"]
    for a in STEALTH_LAUNCH_ARGS:
        assert a not in captured["args"]
    assert "channel" not in captured  # 探测不到真 Chrome 时用自带 Chromium


@pytest.mark.asyncio
async def test_init_browser_env_forces_chromium(monkeypatch, tmp_path):
    monkeypatch.delenv("XY_SLIDER_AUTOMATION_BACKEND", raising=False)
    monkeypatch.setenv("XY_SLIDER_BROWSER_CHANNEL", "chromium")
    monkeypatch.setattr(solver_module.shutil, "which", lambda name: "/usr/bin/google-chrome-stable")
    captured = {}
    monkeypatch.setattr(solver_module, "async_playwright", lambda: _FakePW(captured))
    monkeypatch.setattr(solver_module, "logger", _Recorder())

    s = _make_solver(tmp_path)
    await s._init_browser()

    assert "channel" not in captured
    assert s.browser_channel is None


# ---------- 指纹自审计 ----------

@pytest.mark.asyncio
async def test_audit_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XY_SLIDER_FINGERPRINT_AUDIT", "0")
    s = _make_solver(tmp_path)
    s.page = _FakePage()
    rec = _Recorder()
    monkeypatch.setattr(solver_module, "logger", rec)

    await s._audit_browser_fingerprint()

    assert not s.page.evaluated
    assert not rec.infos and not rec.warnings


@pytest.mark.asyncio
async def test_audit_evaluate_failure_non_blocking(monkeypatch, tmp_path):
    monkeypatch.delenv("XY_SLIDER_FINGERPRINT_AUDIT", raising=False)
    s = _make_solver(tmp_path)

    class _BoomPage:
        async def evaluate(self, js, *a, **kw):
            raise RuntimeError("evaluate dead")

    s.page = _BoomPage()
    rec = _Recorder()
    monkeypatch.setattr(solver_module, "logger", rec)

    await s._audit_browser_fingerprint()

    assert not rec.infos
    assert any("fingerprint audit failed" in m for m in rec.warnings)


@pytest.mark.asyncio
async def test_audit_skips_error_fields_into_line(monkeypatch, tmp_path):
    monkeypatch.delenv("XY_SLIDER_FINGERPRINT_AUDIT", raising=False)
    s = _make_solver(tmp_path)

    class _Page:
        async def evaluate(self, js, *a, **kw):
            return {"ua": "u", "glRenderer": "SwiftShader", "uadError": "no uad"}

    s.page = _Page()
    rec = _Recorder()
    monkeypatch.setattr(solver_module, "logger", rec)

    await s._audit_browser_fingerprint()

    line = rec.infos[0]
    assert "browser fingerprint" in line
    assert "SwiftShader" in line
    assert "uadError=no uad" in line
