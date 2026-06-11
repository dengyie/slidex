from slidex.config import SlidexConfig
from slidex.solver import SliderSolver
from slidex._stealth_patch import STEALTH_LAUNCH_ARGS, STEALTH_INIT_SCRIPT
from slidex._trajectory import generate_trajectory, trajectory_to_points
from slidex._trajectory_pool import SliderTrajectoryPool
from slidex._image_match import SliderImageMatcher, find_gap, find_gap_from_bytes
from slidex.remote import CaptchaRemoteController

__all__ = [
    "SlidexConfig",
    "SliderSolver",
    "STEALTH_LAUNCH_ARGS",
    "STEALTH_INIT_SCRIPT",
    "generate_trajectory",
    "trajectory_to_points",
    "SliderTrajectoryPool",
    "SliderImageMatcher",
    "find_gap",
    "find_gap_from_bytes",
    "CaptchaRemoteController",
]
