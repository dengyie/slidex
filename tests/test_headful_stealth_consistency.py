"""有头/轻量隐身脚本的指纹一致性契约（2026-09-19 滑块 400+ 次全败回归）。

风控 JS 只读 navigator.userAgent / platform / userAgentData.brands 三个
只读属性做一致性校验。历史实现有头只注入 4 行脚本：UA 改成池内 Windows
Chrome/119，platform 保持真实 Linux x86_64、brands 保持真实内核版本、
plugins 被写成数字数组——四重自相矛盾，轨迹再精确也被硬拒
(阿里 punish 页 error:hwR4mj)。本文件钉死三件事：

1. 有头脚本必须覆盖 platform + userAgentData，且 brands 与 UA 池版本一致；
2. 任何隐身脚本不得再出现数字数组 plugins 冒充；
3. 有头/轻量脚本都不得覆盖 document.fonts / EventTarget / Performance /
   Date（有头登录页白屏的根源，见 login_with_password_playwright 注释）。
"""
import json
import re
from pathlib import Path

import pytest

from slidex._concurrency import concurrency_manager
import slidex.stealth as stealth_module
from slidex.stealth import XianyuSliderStealth

FEATURES = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    "platform": "Win32",
    "vendor": "Google Inc.",
    "locale": "zh-CN",
    "is_mobile": False,
    "viewport_width": 1920,
    "viewport_height": 1200,
}

# 有头白屏的根源：这些 API 一旦被 init script 覆盖，登录页整页无法渲染
BLANK_SCREEN_SOURCES = ("document.fonts", "EventTarget", "Performance.now", "Date.now")

# __init__ 会占用并发槽位（max=3、等待 60s）：每个实例用完必须立刻注销，
# 否则会把同进程内后续也要占槽位的测试套件（如 test_stealth_dirs）拖到
# 槽位等待超时——这曾经让组合跑凭空多出 11 个失败和 8 分钟耗时。


def _release(solver):
    try:
        concurrency_manager.unregister_instance(solver.user_id, solver)
    except Exception:
        pass


@pytest.fixture
def solver():
    s = XianyuSliderStealth(user_id="ut-headful-consistency")
    yield s
    _release(s)


@pytest.fixture
def make_solver():
    built = []

    def _build(**kwargs):
        s = XianyuSliderStealth(**kwargs)
        built.append(s)
        return s

    yield _build

    for s in built:
        _release(s)


def test_headful_script_overrides_platform_and_user_agent_data(solver):
    script = solver._get_headful_stealth_script(FEATURES)

    assert "Navigator.prototype, 'platform'" in script
    assert "Navigator.prototype, 'userAgent'" in script
    assert "Navigator.prototype, 'userAgentData'" in script
    assert "getHighEntropyValues" in script
    # webdriver 由 Playwright 注入为 true，必须压回 undefined
    assert "Navigator.prototype, 'webdriver'" in script


def test_headful_brands_match_client_hint_profile_not_real_kernel(solver):
    script = solver._get_headful_stealth_script(FEATURES)

    hints = solver._build_client_hint_profile(FEATURES)
    assert json.dumps(hints["brands"]) in script
    assert json.dumps(hints["fullVersionList"]) in script
    # UA 池是 119：brands 里必须是 119，绝不能漏出真实内核版本（如 147）
    assert '"version": "119"' in script


def test_headful_uses_present_false_webdriver_not_undefined(solver):
    """真实有头 Chrome 的 navigator.webdriver 存在且为 false；undefined
    （缺失语义）本身可探测——full 脚本同样为此用 false。"""
    script = solver._get_headful_stealth_script(FEATURES)

    assert "Navigator.prototype, 'webdriver', () => false" in script
    assert "webdriver', () => undefined" not in script


def test_client_hint_profile_separates_platform_from_platform_name(solver):
    hints = solver._build_client_hint_profile(FEATURES)

    # navigator.platform 是 Win32；userAgentData.platform / sec-ch-ua-platform
    # 必须是高层平台名 Windows（真实 Chrome 从不发 "Win32"）
    assert hints["platform"] == "Win32"
    assert hints["platformName"] == "Windows"
    assert hints["secChUaPlatform"] == '"Windows"'


def test_ua_data_override_uses_platform_name(solver):
    script = solver._get_user_agent_data_override_script(FEATURES)

    assert 'platform: "Windows"' in script
    assert 'platform: "Win32"' not in script


class _FakeCdpSession:
    def __init__(self):
        self.commands = []

    def send(self, method, params=None):
        self.commands.append((method, params))


class _FakeContext:
    def __init__(self):
        self.session = _FakeCdpSession()

    def new_cdp_session(self, page):
        return self.session


def _override_params(session):
    overrides = [p for m, p in session.commands if m == "Network.setUserAgentOverride"]
    assert len(overrides) == 1, f"expected exactly one setUserAgentOverride: {session.commands}"
    return overrides[0]


def test_network_fingerprint_also_applies_in_headful(make_solver):
    """CDP UA/UA-CH 覆盖不再按 headless 门禁：有头页面 Sec-CH-UA 头此前
    仍是真实内核派生值（头层 UA vs UA-CH 自相矛盾）。"""
    solver = make_solver(user_id="ut-network-headful", headless=False)
    fake_context = _FakeContext()
    solver.context = fake_context

    solver._apply_network_fingerprint(object(), FEATURES)

    params = _override_params(fake_context.session)
    assert params["userAgent"] == FEATURES["user_agent"]
    # 顶层 platform 覆盖 navigator.platform（Win32），
    # metadata.platform 是 UA-CH 高层平台名（Windows）
    assert params["platform"] == "Win32"
    assert params["userAgentMetadata"]["platform"] == "Windows"


def test_network_fingerprint_headful_metadata_matches_profile(make_solver):
    solver = make_solver(user_id="ut-network-meta", headless=False)
    fake_context = _FakeContext()
    solver.context = fake_context

    solver._apply_network_fingerprint(object(), FEATURES)

    params = _override_params(fake_context.session)
    metadata = params["userAgentMetadata"]
    hints = solver._build_client_hint_profile(FEATURES)
    assert metadata["brands"] == hints["brands"]
    assert metadata["platformVersion"] == hints["platformVersion"]


def test_headful_script_avoids_blank_screen_sources_and_fake_plugins(solver):
    script = solver._get_headful_stealth_script(FEATURES)

    for forbidden in BLANK_SCREEN_SOURCES:
        assert forbidden not in script
    assert "1, 2, 3, 4, 5" not in script


def test_light_script_also_consistent(solver):
    """无头 lite 同样要带 userAgentData（此前只有 UA/platform 一致）。"""
    script = solver._get_light_stealth_script(FEATURES)

    assert "Navigator.prototype, 'platform'" in script
    assert "Navigator.prototype, 'userAgentData'" in script
    for forbidden in BLANK_SCREEN_SOURCES:
        assert forbidden not in script


def test_broken_numeric_plugins_override_gone_from_entire_module():
    """数字数组 plugins 曾在两处注入（有头 4 行脚本 + 滑块运行时加固），
    且会覆盖 full 脚本注入的正确 PluginArray shim——全模块禁止再现。"""
    source = Path(stealth_module.__file__).read_text(encoding="utf-8")

    assert "1, 2, 3, 4, 5" not in source


def test_headful_branch_uses_consistency_script():
    """密码登录有头分支必须走 _get_headful_stealth_script，不得回退内联脚本。"""
    source = Path(stealth_module.__file__).read_text(encoding="utf-8")

    assert "_get_headful_stealth_script(browser_features)" in source
