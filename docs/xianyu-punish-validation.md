# Xianyu Punish Validation Gate

## Problem

Real token refresh traffic can return a Xianyu/Goofish verification URL like:

```text
https://h5api.m.goofish.com/.../_____tmd_____/punish?x5step=2&action=captcha&pureCaptcha=
```

For this page family, the current remote fallback can mark verification complete when visible slider DOM disappears. In the observed logs the page had a large scripted body, no `div`/`img` elements, and no visible slider button. The remote controller immediately treated that as completed, but the returned cookie snapshot did not include `x5sec` or `x5secdata`, so the next token refresh still returned `FAIL_SYS_USER_VALIDATE`.

## Root Cause

The completion signal is too weak for `_____tmd_____/punish` captcha pages:

- `CaptchaRemoteController.check_completion()` means "no known slider elements are visible".
- `SliderSolver._fallback_to_remote()` converts that directly into `(True, cookies)`.
- `SliderSolver._get_cookies()` flattens browser context cookies and does not require a verification ticket.

For Xianyu token-refresh punish flows, absence of slider DOM is not equivalent to official gateway acceptance.

## Design

Add a validation-cookie gate for URLs that look like a Xianyu/Goofish punish captcha:

- Treat URLs containing `punish` plus `action=captcha`, `pureCaptcha`, `x5step`, or `x5secdata` as requiring validation cookies.
- After remote/manual completion, collect a fresh cookie snapshot.
- Return success only if the snapshot contains `x5sec` or `x5secdata`.
- If the ticket is missing, return `(False, cookies)` and emit telemetry with `failure_reason=x5_validation_cookie_missing`.

Also improve cookie collection by merging Playwright `context.cookies()` with CDP `Network.getAllCookies` when a CDP session exists, so domain-scoped gateway cookies are less likely to be missed.

## Non-Goals

- Do not implement a new CAPTCHA provider.
- Do not claim live-site success without testing against the original non-redacted URL/session.
- Do not change generic non-punish slider success semantics in this patch.

## Acceptance

- A remote fallback completion for a punish captcha URL without `x5sec`/`x5secdata` returns failure.
- The same path with `x5sec` or `x5secdata` returns success.
- Cookie collection can include cookies exposed only through CDP `Network.getAllCookies`.
