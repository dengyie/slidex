"""Declared telemetry artifact paths must match where the summary is actually written.

Regression: VisualChallengeSolver and the CDP CLI declared
``Path("telemetry") / "{run_id}.json"`` — a CWD-relative path that drifts with
the process working directory and never pointed at the file written by
``SliderSolver._write_telemetry_summary_file`` (``get_telemetry_dir()``). The
reported artifact was a ghost path. Both declarations must resolve through the
solver's own telemetry dir.
"""

import json
from pathlib import Path

from slidex.config import SlidexConfig
from slidex.solver import SliderSolver
from slidex.vision.models import ChallengeType, VisionContext, VisualChallengeRequest
from slidex.vision.solver import VisualChallengeSolver


class _FakeSlider:
    """Minimal slider double exposing the telemetry contract surface."""

    def __init__(self, telemetry_dir: str, run_id: str = "r1"):
        self._telemetry_dir = telemetry_dir
        self._run_id = run_id

    async def solve_on_page(self, page, page_url=""):
        return True, {"k": "v"}

    async def solve_on_existing_page(self, cdp_endpoint="", page_url=""):
        return True, {"k": "v"}

    def get_telemetry_summary(self):
        return {"run_id": self._run_id, "provider_name": "fake"}

    def get_telemetry_dir(self) -> str:
        return self._telemetry_dir

    def close(self):
        return None

    def write_summary(self) -> Path:
        """Mirror SliderSolver._write_telemetry_summary_file."""
        path = Path(self._telemetry_dir) / f"{self._run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.get_telemetry_summary()), encoding="utf-8")
        return path


def test_solver_get_telemetry_dir_delegates_to_config(tmp_path):
    cfg = SlidexConfig(telemetry_dir=str(tmp_path))
    solver = SliderSolver(cookie_id="t", config=cfg)
    assert solver.get_telemetry_dir() == str(tmp_path)


async def test_visual_solver_artifact_path_matches_real_telemetry_file(tmp_path):
    fake = _FakeSlider(str(tmp_path), run_id="r1")
    real_path = fake.write_summary()

    solver = VisualChallengeSolver(
        slider_solver_factory=lambda cookie_id, provider: fake,
    )
    request = VisualChallengeRequest(
        challenge_type=ChallengeType.SLIDER_CAPTCHA,
        context=VisionContext.PLAYWRIGHT_PAGE,
        page=object(),
    )
    result = await solver.solve(request)

    assert len(result.artifacts) == 1
    declared = result.artifacts[0].path
    assert declared == real_path
    assert declared.is_absolute()
    assert declared.exists()


async def test_cdp_cli_artifact_path_matches_real_telemetry_file(tmp_path, monkeypatch):
    import slidex.scripts.slide_solve_cdp as cdp_script

    class _FakeCdpSlider:
        def __init__(self, **kwargs):
            self._telemetry_dir = str(tmp_path)

        async def solve_on_existing_page(self, cdp_endpoint="", page_url=""):
            return True, {"k": "v"}

        def get_telemetry_summary(self):
            return {"run_id": "cdp-r1", "provider_name": "auto"}

        def get_telemetry_dir(self) -> str:
            return self._telemetry_dir

        async def close(self):
            return None

    real_path = tmp_path / "cdp-r1.json"
    real_path.write_text(json.dumps({"run_id": "cdp-r1"}), encoding="utf-8")

    monkeypatch.setattr("slidex.solver.SliderSolver", _FakeCdpSlider)
    payload = await cdp_script._run(
        cdp_endpoint="ws://localhost:9222/devtools/browser/x",
        page_url="https://example.test/",
        selectors=None,
        trajectory_mode="auto",
        provider="auto",
        cookie_id="cdp",
    )

    artifacts = payload["artifacts"]
    assert len(artifacts) == 1
    declared = Path(artifacts[0]["path"])
    assert declared == real_path
    assert declared.is_absolute()
    assert declared.exists()
