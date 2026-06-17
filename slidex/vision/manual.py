from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from slidex.vision.models import ChallengeType, VisualChallengeResult


@dataclass(frozen=True)
class ManualFallbackSession:
    session_id: str
    challenge_type: ChallengeType
    token: str
    timeout_s: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "session_id": self.session_id,
            "challenge_type": self.challenge_type.value,
            "token": "[redacted]",
            "timeout_s": self.timeout_s,
        }

    def complete_text(self, text: str) -> VisualChallengeResult:
        return self.complete_metadata({"text": text})

    def complete_metadata(self, metadata: Dict[str, object]) -> VisualChallengeResult:
        return VisualChallengeResult(
            success=True,
            challenge_type=self.challenge_type,
            provider="manual",
            confidence=1.0,
            retryable=False,
            metadata={"session_id": self.session_id, **metadata},
        )
