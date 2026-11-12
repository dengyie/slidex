"""0.11.9 回归：业务预热探测构造 URL 依赖 urlencode，缺导入即 NameError。

0.11.8 的 stealth.py 只从 urllib.parse 导入 parse_qs/urlparse，而
_build_browser_mtop_probe_requests 用 urlencode 拼 mtop 探测 URL。该探测在
「标准稳定化后仍缺少关键 Cookie」时触发——恰是缺 havana_lgc2_77 的账号
最需要的恢复路径，一旦走到必然 NameError，把整个账密登录拖崩。
"""

from urllib.parse import parse_qs, urlparse

from slidex.stealth import XianyuSliderStealth


def _stealth_stub():
    # 探测构造是 cookies_dict 的纯函数，不触碰 __init__ 里的浏览器资源
    return object.__new__(XianyuSliderStealth)


def test_probe_requests_build_without_name_error():
    probes = _stealth_stub()._build_browser_mtop_probe_requests(
        {"_m_h5_tk": "abc123_1700000000000", "unb": "2219383264998"}
    )

    assert [p["name"] for p in probes] == ["login_token_fetch", "login_user_fetch"]
    for probe in probes:
        query = parse_qs(urlparse(probe["url"]).query)
        assert query["api"], probe
        assert query["sign"], probe
        assert query["t"], probe


def test_probe_requests_skipped_without_token_or_unb():
    assert _stealth_stub()._build_browser_mtop_probe_requests({}) == []
    assert _stealth_stub()._build_browser_mtop_probe_requests({"unb": "123"}) == []
    assert _stealth_stub()._build_browser_mtop_probe_requests({"_m_h5_tk": "abc_123"}) == []
