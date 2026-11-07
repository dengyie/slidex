"""PX cookie → 四件套纯逻辑：抽键、:1000: 判据、脱敏、打包串解析。

需求单 §3.3 的成功判据只有一条：px3 含 ``:1000:``。这里把它和几个
易错点（``_`` 前缀、大小写、_pxhd 混入、打包串多形态）全部钉死。
"""

import base64
import json

import pytest

from slidex.px.cookies import (
    PASS_SCORE_MARKER,
    TOKEN_FIELDS,
    extract_px_tokens,
    is_pass_token,
    looks_blocked,
    missing_fields,
    parse_packed_token,
    redact_token,
)

_PASS_PX3 = "1:a:1000:abcdef::"


# ────────────────────── is_pass_token ──────────────────────


def test_pass_token_requires_marker():
    assert is_pass_token(_PASS_PX3) is True
    assert is_pass_token("1:a:200:abcdef::") is False
    assert is_pass_token("") is False
    assert is_pass_token(None) is False  # type: ignore[arg-type]


def test_pass_marker_constant():
    assert PASS_SCORE_MARKER == ":1000:"


# ────────────────────── extract_px_tokens ──────────────────────


def test_extract_from_playwright_cookie_dicts():
    cookies = [
        {"name": "_px3", "value": _PASS_PX3, "domain": ".signup.live.com"},
        {"name": "_pxde", "value": "{}", "domain": ".signup.live.com"},
        {"name": "_pxvid", "value": "vid-1"},
        {"name": "_pxcts", "value": "cts-1"},
    ]
    tokens = extract_px_tokens(cookies)
    assert tokens == {"px3": _PASS_PX3, "pxde": "{}", "pxvid": "vid-1", "pxcts": "cts-1"}


def test_extract_from_plain_mapping_ignores_prefix_case():
    tokens = extract_px_tokens({"PX3": _PASS_PX3, "__PXDE": "{}"})
    assert tokens["px3"] == _PASS_PX3
    assert tokens["pxde"] == "{}"
    assert tokens["pxvid"] == ""
    assert tokens["pxcts"] == ""


def test_extract_ignores_non_token_px_cookies():
    tokens = extract_px_tokens({"_pxhd": "xxx", "_pxcts_h": "y", "_px2": "z"})
    assert tokens == {field: "" for field in TOKEN_FIELDS}


def test_extract_skips_empty_values():
    tokens = extract_px_tokens({"_px3": "", "_pxde": None})
    assert tokens["px3"] == "" and tokens["pxde"] == ""


def test_extract_extra_fills_missing_only():
    tokens = extract_px_tokens({"_px3": "from-cookie"}, extra={"px3": "ignored", "pxvid": "v"})
    assert tokens["px3"] == "from-cookie"
    assert tokens["pxvid"] == "v"


def test_extract_tolerates_pairs_and_junk():
    tokens = extract_px_tokens([("_px3", "p"), object(), ("bad",)])
    assert tokens["px3"] == "p"


def test_extract_always_returns_all_fields():
    assert set(extract_px_tokens([])) == set(TOKEN_FIELDS)


# ────────────────────── redact_token ──────────────────────


def test_redact_never_leaks_value():
    secret = "1:a:1000:" + "x" * 200
    redacted = redact_token(secret)
    assert "x" * 20 not in redacted
    assert redacted.startswith("1:a:10")
    assert f"{len(secret)}B" in redacted


def test_redact_short_values_collapse_entirely():
    assert redact_token("abc") == "***"
    assert redact_token("") == "***"


# ────────────────────── looks_blocked ──────────────────────


@pytest.mark.parametrize("status", [403, 429])
def test_blocked_by_status(status):
    assert looks_blocked(status=status) is True


@pytest.mark.parametrize(
    "needle",
    [
        "Access to this page has been denied",
        "please verify you are a human",
        "PX-BLOCK",
        "sorry, you have been blocked",
    ],
)
def test_blocked_by_body_phrase(needle):
    assert looks_blocked(status=200, body=needle) is True


def test_not_blocked_on_plain_page():
    assert looks_blocked(status=200, body="<html>ok</html>", title="Sign in") is False


# ────────────────────── parse_packed_token ──────────────────────


def test_parse_json_object():
    text = json.dumps({"px3": _PASS_PX3, "pxde": "{}", "pxvid": "v", "pxcts": "c"})
    assert parse_packed_token(text)["px3"] == _PASS_PX3


def test_parse_nested_json():
    text = json.dumps({"solution": {"cookies": {"_px3": _PASS_PX3}}})
    assert parse_packed_token(text)["px3"] == _PASS_PX3


def test_parse_base64_json():
    raw = json.dumps({"px3": _PASS_PX3, "pxde": "d"})
    text = base64.b64encode(raw.encode()).decode()
    tokens = parse_packed_token(text)
    assert tokens["px3"] == _PASS_PX3
    assert tokens["pxde"] == "d"


def test_parse_base64_nested_list():
    raw = json.dumps({"cookies": [{"name": "_pxvid", "value": "vv"}]})
    text = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    assert parse_packed_token(text)["pxvid"] == "vv"


def test_parse_querystring():
    text = "_px3=1%3Aa%3A1000%3Aabc%3A%3A&_pxvid=vv"
    tokens = parse_packed_token(text)
    assert tokens["px3"] == "1:a:1000:abc::"
    assert tokens["pxvid"] == "vv"


def test_parse_tilde_split_fallback():
    tokens = parse_packed_token("px3v~~~pxdev~~~pxvidv~~~pxctsv")
    assert tokens == {"px3": "px3v", "pxde": "pxdev", "pxvid": "pxvidv", "pxcts": "pxctsv"}


def test_parse_quoted_json():
    text = '"' + json.dumps({"px3": _PASS_PX3}) + '"'
    # 外层引号被剥掉后仍应解析出 px3
    assert parse_packed_token(text).get("px3") == _PASS_PX3


@pytest.mark.parametrize("text", ["", "   ", "not-a-token", "{}", "[1,2,3]", None])
def test_parse_never_raises_on_garbage(text):
    assert isinstance(parse_packed_token(text), dict)  # type: ignore[arg-type]


# ────────────────────── missing_fields ──────────────────────


def test_missing_fields_names_each_gap():
    assert missing_fields({"px3": "x", "pxde": "", "pxvid": "v"}) == ["pxde", "pxcts"]


def test_missing_fields_merges_sources():
    assert missing_fields([{"px3": "x"}, {"pxde": "d"}]) == ["pxvid", "pxcts"]
