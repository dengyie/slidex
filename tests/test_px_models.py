"""PX 请求模型 — payload 归一化、国家/时区对齐、proxy 规范化。

这一层决定「调用方传什么形状都能被正确理解」：camelCase/snake_case 混用、
cookies 两种形态、proxy 对象/空值、timeout 夹取。纯数据，零 IO。
"""

import pytest

from slidex.px.models import (
    DEFAULT_PX_USER_AGENT,
    PxPressRequest,
    PxPressResult,
    PxRequestError,
    country_default_locale,
    country_default_timezone,
)


# ────────────────────── 必填与 mode 推导 ──────────────────────


def test_app_id_is_required():
    with pytest.raises(PxRequestError, match="appId is required"):
        PxPressRequest.from_payload({"websiteURL": "https://signup.live.com/"})


def test_press_mode_requires_challenge_url():
    with pytest.raises(PxRequestError, match="challengeUrl is required"):
        PxPressRequest.from_payload({"appId": "PXzC5j78di", "mode": "press"})


def test_silent_mode_requires_website_url():
    with pytest.raises(PxRequestError, match="websiteURL is required for silent"):
        PxPressRequest.from_payload({"appId": "PXzC5j78di", "mode": "silent"})


def test_mode_inferred_from_payload_shape():
    press = PxPressRequest.from_payload(
        {"appId": "PXzC5j78di", "challengeUrl": "https://iframe.hsprotect.net/"}
    )
    assert press.mode == "press"
    silent = PxPressRequest.from_payload(
        {"appId": "PXzC5j78di", "websiteURL": "https://signup.live.com/"}
    )
    assert silent.mode == "silent"


def test_neither_url_rejected():
    # 无 URL 时 mode 推导落到 silent，由 silent 分支报错（确定性 4xx）
    with pytest.raises(PxRequestError, match="websiteURL is required"):
        PxPressRequest.from_payload({"appId": "PXzC5j78di"})


def test_non_mapping_payload_rejected():
    with pytest.raises(PxRequestError, match="JSON object"):
        PxPressRequest.from_payload(["nope"])  # type: ignore[arg-type]


# ────────────────────── 字段别名与派生属性 ──────────────────────


def test_snake_case_aliases_accepted():
    request = PxPressRequest.from_payload(
        {
            "app_id": "PXzC5j78di",
            "challenge_url": "https://iframe.hsprotect.net/x",
            "website_url": "https://signup.live.com/",
            "user_agent": "UA/1.0",
            "country": "jp",
            "uaid": "u1",
            "uuid": "uu1",
            "vid": "v1",
        }
    )
    assert request.app_id == "PXzC5j78di"
    assert request.user_agent == "UA/1.0"
    assert request.country == "JP"
    assert (request.uaid, request.uuid, request.vid) == ("u1", "uu1", "v1")
    assert request.target_url == "https://iframe.hsprotect.net/x"


def test_country_drives_timezone_and_locale_defaults():
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/", "country": "JP"}
    )
    assert request.timezone == "Asia/Tokyo"
    assert request.locale == "ja-JP"


def test_unknown_country_falls_back_to_us_east():
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/", "country": "ZZ"}
    )
    assert request.timezone == "America/New_York"
    assert request.locale == "en-US"


def test_explicit_timezone_and_locale_win():
    request = PxPressRequest.from_payload(
        {
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "country": "DE",
            "timeZone": "Europe/Berlin",
            "language": "de-AT",
        }
    )
    assert request.timezone == "Europe/Berlin"
    assert request.locale == "de-AT"


def test_default_user_agent_used_when_absent():
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/"}
    )
    assert request.user_agent == DEFAULT_PX_USER_AGENT


def test_referer_only_when_site_differs_from_target():
    press = PxPressRequest.from_payload(
        {
            "appId": "a",
            "challengeUrl": "https://iframe.hsprotect.net/c",
            "websiteURL": "https://signup.live.com/",
        }
    )
    assert press.referer == "https://signup.live.com/"
    assert press.target_url == "https://iframe.hsprotect.net/c"

    silent = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/"}
    )
    assert silent.referer == ""
    assert silent.target_url == "https://signup.live.com/"


def test_country_helpers_are_case_insensitive():
    assert country_default_timezone("us") == "America/New_York"
    assert country_default_locale("GB") == "en-GB"
    assert country_default_timezone("") == "America/New_York"


# ────────────────────── timeout / poll 夹取 ──────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        (1, 10.0),          # 太短 → 下限
        (999, 150.0),       # 太长 → 需求单 §6 上限
        (60, 60.0),
        ("bad", 120.0),     # 非数字 → 默认
    ],
)
def test_solve_timeout_clamped(raw, expected):
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/", "timeout": raw}
    )
    assert request.solve_timeout_s == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(0.01, 0.25), (99, 10.0), (1.5, 1.5), ("x", 2.0)],
)
def test_poll_interval_clamped(raw, expected):
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/", "pollInterval": raw}
    )
    assert request.poll_interval_s == expected


# ────────────────────── cookies ──────────────────────


def test_cookies_map_accepted():
    request = PxPressRequest.from_payload(
        {
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "cookies": {"_px3": "v:1000:abc", "blank": "  "},
        }
    )
    assert request.cookies == {"_px3": "v:1000:abc"}


def test_cookies_list_of_objects_accepted():
    request = PxPressRequest.from_payload(
        {
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "cookies": [{"name": "_pxde", "value": "{}"}, {"name": "x", "value": ""}],
        }
    )
    assert request.cookies == {"_pxde": "{}"}


def test_cookies_bad_entries_rejected():
    with pytest.raises(PxRequestError, match="cookies entries"):
        PxPressRequest.from_payload(
            {
                "appId": "a",
                "websiteURL": "https://signup.live.com/",
                "cookies": ["_px3=abc"],
            }
        )


# ────────────────────── proxy ──────────────────────


def test_empty_proxy_means_direct():
    request = PxPressRequest.from_payload(
        {"appId": "a", "websiteURL": "https://signup.live.com/", "proxy": {}}
    )
    assert request.uses_proxy is False
    assert request.playwright_proxy() is None


def test_proxy_normalized_and_playwright_shape():
    request = PxPressRequest.from_payload(
        {
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "proxy": {"host": "1.2.3.4", "port": "8000", "username": "u", "pass": "p"},
        }
    )
    assert request.uses_proxy is True
    assert request.proxy == {"host": "1.2.3.4", "port": 8000, "login": "u", "password": "p"}
    assert request.playwright_proxy() == {
        "server": "http://1.2.3.4:8000",
        "username": "u",
        "password": "p",
    }


def test_proxy_without_auth_omits_credentials():
    request = PxPressRequest.from_payload(
        {
            "appId": "a",
            "websiteURL": "https://signup.live.com/",
            "proxy": {"host": "1.2.3.4", "port": 8000},
        }
    )
    assert request.playwright_proxy() == {"server": "http://1.2.3.4:8000"}


def test_proxy_missing_host_rejected():
    with pytest.raises(PxRequestError, match="proxy.host is required"):
        PxPressRequest.from_payload(
            {
                "appId": "a",
                "websiteURL": "https://signup.live.com/",
                "proxy": {"port": 8000},
            }
        )


def test_proxy_bad_port_rejected():
    with pytest.raises(PxRequestError, match="proxy.port must be an integer"):
        PxPressRequest.from_payload(
            {
                "appId": "a",
                "websiteURL": "https://signup.live.com/",
                "proxy": {"host": "h", "port": "nope"},
            }
        )


def test_proxy_port_out_of_range_rejected():
    with pytest.raises(PxRequestError, match="out of range"):
        PxPressRequest.from_payload(
            {
                "appId": "a",
                "websiteURL": "https://signup.live.com/",
                "proxy": {"host": "h", "port": 70000},
            }
        )


def test_proxy_non_mapping_rejected():
    with pytest.raises(PxRequestError, match="proxy must be an object"):
        PxPressRequest.from_payload(
            {
                "appId": "a",
                "websiteURL": "https://signup.live.com/",
                "proxy": "1.2.3.4:8000",
            }
        )


# ────────────────────── result ──────────────────────


def test_result_retryable_error_derives_from_kind():
    assert PxPressResult(False, error_kind="px_token_timeout").retryable_error is True
    assert PxPressResult(False, error_kind="px_token_blocked").retryable_error is False
    assert PxPressResult(False, error_kind="solver_busy").retryable_error is True


def test_result_success_defaults_to_not_retryable():
    result = PxPressResult(True, tokens={"px3": "x:1000:y"})
    assert result.retryable_error is False


def test_result_error_payload_shape():
    payload = PxPressResult(False, error_kind="", error="boom").error_payload()
    assert payload == {"kind": "unknown", "message": "boom", "retryable": False}
