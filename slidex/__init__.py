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
]
