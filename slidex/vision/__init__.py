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

__all__ = [
    "ChallengeType",
    "ProviderDecision",
    "ProviderManifest",
    "VisionArtifact",
    "VisionContext",
    "VisualChallengeRequest",
    "VisualChallengeResult",
    "VisualChallengeSolver",
    "ManualFallbackSession",
    "build_artifact_path",
    "redact_sensitive",
    "safe_artifact_metadata",
]


def __getattr__(name):
    if name == "VisualChallengeSolver":
        from slidex.vision.solver import VisualChallengeSolver

        return VisualChallengeSolver
    raise AttributeError(name)
