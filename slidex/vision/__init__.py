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

__all__ = [
    "ChallengeType",
    "ProviderDecision",
    "ProviderManifest",
    "VisionArtifact",
    "VisionContext",
    "VisualChallengeRequest",
    "VisualChallengeResult",
    "VisualChallengeSolver",
    "redact_sensitive",
]


def __getattr__(name):
    if name == "VisualChallengeSolver":
        from slidex.vision.solver import VisualChallengeSolver

        return VisualChallengeSolver
    raise AttributeError(name)
