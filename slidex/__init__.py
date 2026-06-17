from slidex.config import SlidexConfig
from slidex.solver import SliderSolver, DEFAULT_SELECTORS
from slidex._stealth_patch import STEALTH_LAUNCH_ARGS, STEALTH_INIT_SCRIPT
from slidex._trajectory import generate_trajectory, trajectory_to_points
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex._image_match import SliderImageMatcher, find_gap, find_gap_from_bytes
from slidex.remote import CaptchaRemoteController, captcha_controller
from slidex._concurrency import SliderConcurrencyManager, concurrency_manager
from slidex.providers import CaptchaProvider, ProviderRegistry, ProviderElements, SolveResult
from slidex.providers.builtin import AliyunNoCaptchaProvider, GeeTestProvider
from slidex.ocr import FakeOcrExtractor, OcrBox, OcrInput, OcrResult, OcrTextExtractor
from slidex.vision import (
    ChallengeType,
    ManualFallbackSession,
    ProviderDecision,
    ProviderManifest,
    VisionArtifact,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeResult,
    VisualChallengeSolver,
)
from slidex._chromium_lifecycle import (
    get_pid_lock,
    kill_chromium_by_pid,
    record_chromium_pid,
    ensure_previous_chromium_closed,
    find_chromium_pid_by_user_data_dir,
)

__all__ = [
    "SlidexConfig",
    "SliderSolver",
    "DEFAULT_SELECTORS",
    "STEALTH_LAUNCH_ARGS",
    "STEALTH_INIT_SCRIPT",
    "generate_trajectory",
    "trajectory_to_points",
    "SliderTrajectoryPool",
    "SliderImageMatcher",
    "find_gap",
    "find_gap_from_bytes",
    "CaptchaRemoteController",
    "captcha_controller",
    "SliderConcurrencyManager",
    "concurrency_manager",
    "CaptchaProvider",
    "ProviderRegistry",
    "ProviderElements",
    "SolveResult",
    "AliyunNoCaptchaProvider",
    "GeeTestProvider",
    "FakeOcrExtractor",
    "OcrBox",
    "OcrInput",
    "OcrResult",
    "OcrTextExtractor",
    "ChallengeType",
    "ManualFallbackSession",
    "ProviderDecision",
    "ProviderManifest",
    "VisionArtifact",
    "VisionContext",
    "VisualChallengeRequest",
    "VisualChallengeResult",
    "VisualChallengeSolver",
    # Chromium lifecycle (for tests)
    "get_pid_lock",
    "kill_chromium_by_pid",
    "record_chromium_pid",
    "ensure_previous_chromium_closed",
    "find_chromium_pid_by_user_data_dir",
]
