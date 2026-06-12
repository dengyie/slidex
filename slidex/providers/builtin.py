"""Built-in providers registration"""

from slidex.providers import ProviderRegistry
from slidex.providers.aliyun import AliyunNoCaptchaProvider
from slidex.providers.geetest import GeeTestProvider


def register_builtin_providers():
    """注册所有内置 provider"""
    ProviderRegistry.register(
        "aliyun-nocaptcha",
        AliyunNoCaptchaProvider,
        detection_priority=10,
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
    "GeeTestProvider",
    "register_builtin_providers",
]
