"""Built-in providers registration"""

from slidex.providers import ProviderRegistry
from slidex.providers.aliyun import AliyunNoCaptchaProvider
from slidex.providers.aliyun_captcha_js import AliyunCaptchaJsProvider
from slidex.providers.geetest import GeeTestProvider


def register_builtin_providers():
    """注册所有内置 provider"""
    ProviderRegistry.register(
        "aliyun-nocaptcha",
        AliyunNoCaptchaProvider,
        detection_priority=10,
    )
    # 新一代 AliyunCaptcha.js 弹窗拼图：DOM 前缀不同（aliyunCaptcha-*），
    # 与 nc（nc_1_*）互不误检，priority 高低只影响同页共存时的先手。
    ProviderRegistry.register(
        "aliyun-captcha-js",
        AliyunCaptchaJsProvider,
        detection_priority=15,
    )
    ProviderRegistry.register(
        "geetest",
        GeeTestProvider,
        detection_priority=20,
    )


# 自动注册
register_builtin_providers()


__all__ = [
    "AliyunNoCaptchaProvider",
    "AliyunCaptchaJsProvider",
    "GeeTestProvider",
    "register_builtin_providers",
]
