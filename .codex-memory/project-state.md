# Project State

## Objective
- Fix the Xianyu token-refresh punish flow where remote fallback reported slider success without `x5sec`/`x5secdata`.

## Current Phase
- Xianyu punish false-success fix implemented; live validation still manual-required.

## Current Branch
- main tracking origin/main at d4d372b (`docs: 固化 automation-kit 复核基线表述`).

## Last Verified
- 2026-06-19: `python -m pytest tests/test_slider_solver.py tests/test_api_security.py tests/test_vision_solver.py tests/test_cli.py -q` passed 73 tests after review fixes.
- 2026-06-19: `python -m pytest tests/test_slider_solver.py tests/test_api_security.py tests/test_vision_solver.py tests/test_cli.py -q` passed 72 tests.
- 2026-06-19: full `python -m pytest -q` produced 247 passed, 1 skipped, 1 unrelated Windows path-separator failure in `tests/test_automation_kit_integration.py::test_to_artifacts_without_native_adapter_is_json_serializable`.

## Active Risks
- Console output on this Windows shell renders UTF-8 Chinese/emoji as mojibake unless `PYTHONIOENCODING=utf-8` or another UTF-8-safe path is used.
- GitHub CLI token for account `dengyie` is invalid; public Git operations work, authenticated `gh` actions require re-login.
- Live Xianyu validation still requires the original non-redacted `x5secdata` URL/account session; local tests only prove false-success gating and cookie snapshot behavior.

## Active Blockers
- Manual-required: validate against a live Xianyu token-refresh punish URL/session.

## Current Focus
- `SliderSolver` now rejects Xianyu/Goofish punish remote completion unless `x5sec` or `x5secdata` is present, preserves diagnostic cookies through `_fallback_or_fail()`, recognizes blank `pureCaptcha=`, and `_get_cookies()` merges CDP `Network.getAllCookies`.

## Next Milestone
- Run a live verification with the original URL/session, then decide whether page-level provider/rendering work is needed.

## Key Artifacts
- `README.md`, `README_EN.md`
- `pyproject.toml`
- `docs/ARCHITECTURE.md`
- `docs/automation-kit-vision-platform.md`
- `slidex/solver.py`
- `slidex/providers/__init__.py`
- `slidex/vision/`
- `slidex/api.py`, `slidex/remote.py`
- `tests/`
- `docs/xianyu-punish-validation.md`
