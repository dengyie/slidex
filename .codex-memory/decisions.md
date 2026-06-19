# Decisions

## 2026-06-19 - Initialize Repo-Native Memory
- Decision: Use `.codex-memory/` in the Slidex repository as the continuity surface for future Codex sessions.
- Rationale: The project did not contain memory files after clone, and AGENTS instructions require project work to start through `best-project-memory`.
- Alternatives considered: Keep context only in chat; rejected because it would not survive cleanly across sessions.
- Impact: Future work should read `.codex-memory/project-state.md`, `todo.md`, `decisions.md`, and `session-log.md` before changing the project.
- Rollback trigger: User explicitly chooses a different project-memory surface.
- Related files: `.codex-memory/project-state.md`, `.codex-memory/todo.md`, `.codex-memory/session-log.md`, `.codex-memory/decisions.md`

## 2026-06-19 - Require X5 Validation Cookies For Xianyu Punish Success
- Decision: For Xianyu/Goofish `punish` captcha URLs, `SliderSolver` remote fallback must not report success unless the cookie snapshot includes `x5sec` or `x5secdata`.
- Rationale: Real logs showed remote fallback completed immediately when slider DOM was absent, but the next token refresh still returned `FAIL_SYS_USER_VALIDATE` because validation cookies were missing.
- Alternatives considered: Trust remote DOM disappearance; rejected because it caused false success. Add a full new provider; deferred until live evidence shows rendering/interaction still needs it.
- Impact: Callers can distinguish unresolved official validation from a real accepted verification. Missing validation tickets now return failure with the current cookie snapshot for diagnostics.
- Rollback trigger: Live evidence shows this Xianyu flow uses a different reliable success signal instead of `x5sec`/`x5secdata`.
- Related files: `slidex/solver.py`, `tests/test_slider_solver.py`, `docs/xianyu-punish-validation.md`
