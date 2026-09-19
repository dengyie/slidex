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


@pytest.fixture(scope="module")
def solver():
    return XianyuSliderStealth(user_id="ut-headful-consistency")


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
