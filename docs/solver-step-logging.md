# Solver Step Logging

## Problem

Real slider failures are hard to diagnose from a final success/failure flag. The Xianyu token-refresh flow showed why: the solver entered remote fallback, the official page stayed blank, no validation cookie appeared, and the caller needed a precise timeline to know which part failed.

Existing telemetry captures some events, but they are not consistently shaped as operation steps and most events are only persisted when the run finalizes. If the process crashes or the caller needs live progress, the useful context can be missing.

## Design

Add a structured `solver_step` event emitted through the existing telemetry surface.

Each step event has:

- `event`: always `solver_step`
- `phase`: broad area such as `solve`, `browser`, `page`, `legacy`, `remote`, `cookies`, or `cleanup`
- `step`: concrete operation name such as `solve_started`, `browser_init`, `page_load`, `slider_wait`, `remote_fallback`, `cookie_snapshot`
- `status`: `started`, `ok`, `failed`, or `skipped`
- standard identifiers: `run_id`, `cookie_id`, `pure_user_id`, `timestamp`
- optional safe metadata such as `mode`, `attempt`, `cookie_names`, `duration_ms`, `reason`

Step events are broadcast through `SlidexConfig.on_risk_log_update`, appended to the in-memory telemetry event list, written immediately to `events.jsonl`, and logged with `loguru`. The final summary remains unchanged and still uses `on_risk_log`.

## Redaction Policy

All step metadata must be sanitized before callbacks, JSONL persistence, and logger output.

Redact values for keys containing:

- `cookie`
- `token`
- `secret`
- `password`
- `authorization`
- `x5sec`
- `x5secdata`

URL metadata is normalized to scheme, host, path, and non-sensitive query key names. Query values are never logged. For example, a punish URL can expose `query_keys=["action", "pureCaptcha", "x5secdata", "x5step"]` without exposing the `x5secdata` ticket.

Cookie snapshots should log `cookie_names` only, not cookie values.

## Critical Steps

The first implementation covers the most useful failure path:

- solve entry for `solve()`, `solve_on_existing_page()`, and `solve_on_page()`
- browser initialization
- page loading and page state capture
- provider/legacy solve loop entry
- slider wait result
- distance detection failure path
- generated/recorded slide attempts
- remote fallback start, session creation, completion, validation-cookie missing, timeout, and failure
- cookie snapshot start/result/failure

## Non-Goals

- Do not add a new external logging service.
- Do not log raw cookies, tickets, authorization headers, or full sensitive URLs.
- Do not solve the blank Xianyu punish rendering issue in this change.

## Acceptance

- Callers receive ordered `solver_step` events through `on_risk_log_update`.
- `events.jsonl` includes step events immediately, not only final summaries.
- Sensitive URL query values and cookie values are redacted.
- Missing Xianyu validation cookies are reported as an explicit failed remote step.
