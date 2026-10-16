from slidex.vision.models import (
    ChallengeType,
    ProviderDecision,
    ProviderManifest,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
    redact_sensitive,
)
from slidex.vision.artifacts import build_artifact_path, safe_artifact_metadata
from slidex.vision.manual import ManualFallbackSession
from slidex.vision.graph_captcha import (
    GraphCaptchaChars,
    GraphCaptchaExecutor,
    GraphCaptchaPlan,
    is_punish_graph_code,
    normalize_chars,
    plan_graph_chars,
)
from slidex.vision.point_select import (
    ClickPlan,
    ClickPoint,
    PointSelectExecutor,
    is_punish_point_code,
    plan_click_points,
)
from slidex.vision.slider_image import SliderImageResult, SliderImageSolver

__all__ = [
    "ChallengeType",
    "ProviderDecision",
    "ProviderManifest",
    "VisionArtifact",
    "VisionContext",
    "VisualChallengeRequest",
    "VisualChallengeResult",
    "VisualChallengeSolver",
    "SliderImageSolver",
    "SliderImageResult",
    "ManualFallbackSession",
    "ClickPlan",
    "ClickPoint",
    "PointSelectExecutor",
    "is_punish_point_code",
    "plan_click_points",
    "GraphCaptchaChars",
    "GraphCaptchaExecutor",
    "GraphCaptchaPlan",
    "is_punish_graph_code",
    "normalize_chars",
    "plan_graph_chars",
    "build_artifact_path",
    "redact_sensitive",
    "safe_artifact_metadata",
]


def __getattr__(name):
    if name == "VisualChallengeSolver":
        from slidex.vision.solver import VisualChallengeSolver

        return VisualChallengeSolver
    raise AttributeError(name)
