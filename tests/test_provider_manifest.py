from slidex import ProviderRegistry
from slidex.vision import ChallengeType, VisionContext


def test_builtin_provider_manifests_declare_slider_capability():
    geetest = ProviderRegistry.get("geetest")
    aliyun = ProviderRegistry.get("aliyun-nocaptcha")

    assert geetest.manifest.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.CDP)
    assert aliyun.manifest.supports(ChallengeType.SLIDER_CAPTCHA, VisionContext.PLAYWRIGHT_PAGE)


def test_registry_filters_by_challenge_type_and_context():
    providers = ProviderRegistry.find_providers(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
    )

    assert "geetest" in providers
    assert "aliyun-nocaptcha" in providers


def test_registry_records_provider_decision():
    providers = ProviderRegistry.find_providers(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
    )
    decision = ProviderRegistry.build_decision(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.CDP,
        requested_provider="auto",
        selected_provider=providers[0],
        candidates=providers,
        reason="unit_test",
    )

    assert decision.selected_provider == providers[0]
    assert decision.reason == "unit_test"
    assert decision.candidates == providers
