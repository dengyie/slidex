# CLIProxyAPI Customization Log

Last updated: 2026-06-09 19:19:44 CST

## Source Checkout

The upstream CLIProxyAPI source was cloned into this workspace:

```text
/Users/mango/project/codex/cpa-account-pool/CLIProxyAPI
```

Repository:

```text
https://github.com/router-for-me/CLIProxyAPI.git
```

Local customization branch:

```text
mango-custom
```

Baseline upstream revision:

```text
2aeb41ce / v7.1.58
```

## Customization 001: Baseline Custom Image Pipeline

Date: 2026-06-09

Purpose:

- Establish a repeatable local-modification-to-VPS deployment path.
- Build a custom Docker image locally.
- Transfer the image to the VPS.
- Replace the running VPS image without changing `/opt/cliproxyapi/config.yaml` or `/opt/cliproxyapi/auth`.

Code change:

- `CLIProxyAPI/Dockerfile`
- Added build argument `GOPROXY`.
- Added `ENV GOPROXY=${GOPROXY}` before `go mod download`.

Reason:

- Local Docker builds repeatedly failed while downloading Go modules from `proxy.golang.org`.
- The patch allows builds to pass a mirror such as `https://goproxy.cn,direct` without hardcoding that mirror for everyone.

Commit:

```text
e5b0cb8b build: allow custom Go module proxy
```

Build command pattern:

```bash
cd /Users/mango/project/codex/cpa-account-pool/CLIProxyAPI

PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH" docker buildx build \
  --platform linux/amd64 \
  --load \
  --build-arg GOPROXY=https://goproxy.cn,direct \
  --build-arg VERSION=v7.1.58-mango-baseline \
  --build-arg COMMIT="$(git rev-parse --short HEAD)" \
  --build-arg BUILD_DATE="$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
  -t cliproxyapi-mango:baseline-20260609-1640 \
  .
```

Image:

```text
cliproxyapi-mango:baseline-20260609-1640
```

Image details:

```text
sha256:e757d4360481e447108fe2b12248d688276aedbad4b35137015bf645ccef61bd
linux/amd64
67.6 MB
```

Local image archive:

```text
/Users/mango/.cliproxyapi-migration/images/cliproxyapi-mango-baseline-20260609-1640.tar.gz
```

VPS image archive:

```text
/opt/cliproxyapi/backups/images/cliproxyapi-mango-baseline-20260609-1640.tar.gz
```

VPS deployment:

- Updated `/opt/cliproxyapi/docker-compose.yml`.
- Set image to `cliproxyapi-mango:baseline-20260609-1640`.
- Recreated the `cliproxyapi` container.
- Preserved `/opt/cliproxyapi/config.yaml`.
- Preserved `/opt/cliproxyapi/auth`.

VPS verification:

```text
Container image: cliproxyapi-mango:baseline-20260609-1640
Version log: CLIProxyAPI Version: v7.1.58-mango-baseline
Client load log: full client load complete - 799 clients
Public root endpoint: http://vps.mangoq.ccwu.cc:8317/
Management panel: http://vps.mangoq.ccwu.cc:8317/management.html#/ai-providers
```

Runtime status after replacement:

```text
Port: 0.0.0.0:8317->8317/tcp
Memory: about 72 MiB
CPU: idle
```

## Notes For Future Customizations

- Make code changes on branch `mango-custom`.
- Commit each distinct customization.
- Build as `linux/amd64` for the current VPS.
- Export with `docker save | gzip`.
- Upload to `/opt/cliproxyapi/backups/images/`.
- Load on VPS with `gunzip -c IMAGE.tar.gz | docker load`.
- Update `/opt/cliproxyapi/docker-compose.yml` image only unless the change specifically requires config or volume changes.
- Restart with `docker compose up -d --force-recreate`.
- Verify root endpoint, management panel, and client load log.

## Customization 002: Product-Scoped Quota Batch Test And Cleanup

Date: 2026-06-09

Repository:

```text
/Users/mango/project/codex/cpa-account-pool/Cli-Proxy-API-Management-Center
```

Branch:

```text
feature/quota-batch-test-cleanup
```

Purpose:

- Add a product-scoped "test all resource quotas" action to each quota section.
- Keep success, failure, and skipped result groups in frontend memory after each batch run.
- Allow direct cleanup of failed accounts with select-delete and delete-all actions.

Implementation:

- Added a concurrency-limited batch quota runner in `src/components/quota/useQuotaLoader.ts`.
- Added per-product batch UI, progress summary, grouped result view, copy actions, and failed-account deletion actions in `src/components/quota/QuotaSection.tsx`.
- Reused existing `authFilesApi.deleteFiles()` so no backend change is required for this first version.
- Added quota batch styles in `src/pages/QuotaPage.module.scss`.
- Added Simplified Chinese, Traditional Chinese, English, and Russian i18n strings.

Behavior:

- The new button appears inside each product quota section as `测试全部资源额度`.
- It tests only resources in the current product section.
- It skips disabled resources, runtime-only resources, unnamed resources, and resources missing `auth_index`.
- It limits each product batch run to 5 concurrent quota checks.
- It continues when individual resources fail.
- It stores success, failure, and skipped groups in frontend memory until refresh/navigation resets state.
- The failure group supports selecting failed accounts, deleting selected accounts, and deleting all failed accounts.
- After deletion, the panel triggers the normal auth-file refresh path and removes successfully deleted accounts from the failure group.
- Review hardening:
  - Batch quota loader now returns an explicit `busy` status instead of an empty result when another quota refresh is active.
  - Batch UI no longer clears previous results if a run cannot start because the section is busy.
  - Failed-account cleanup now awaits the auth-file refresh and warns separately if refresh fails after deletion.
  - Enabled locale files `zh-TW` and `ru` now include the new quota batch strings.

Local verification:

```text
bun run type-check: passed
bun run lint: passed
bun run build: passed
Browser smoke check: local dev app loads, protected quota route redirects to login without credentials, no console warnings/errors.
```

Notes:

- Backend checkout was left untouched because it contains unrelated in-progress work on another branch.
- VPS deployment has not been performed for this customization yet.

GitHub asset and merge record:

```text
Repository: https://github.com/dengyie/cpa-management-center
Visibility: public
Default branch: main
Feature branch: feature/quota-batch-test-cleanup
Feature commit: 5b175aa feat: add product quota batch cleanup
Main merge commit: e5b5fa0 merge: quota batch cleanup feature
Pushed branch: main -> origin/main
```

Remote configuration:

```text
origin   https://github.com/dengyie/cpa-management-center.git
upstream https://github.com/router-for-me/Cli-Proxy-API-Management-Center.git
upstream push URL: DISABLED
```

README asset positioning:

- Updated `README.md` and `README_CN.md` to describe this repository as an independently maintained secondary-development open-source asset.
- Removed the direct upstream project/fork-style positioning from the README header.

## 2026-06-10 Local Service And Management Panel Refresh

Purpose:

- Fix the local management panel so the already-implemented batch quota test and failed-account cleanup UI is available from `http://localhost:8317/management.html`.
- Keep the backend service on the latest local `main` build.

What changed:

- Built the management center from `Cli-Proxy-API-Management-Center` main commit `e5b5fa0`.
- Published the generated single-file panel as GitHub release `management-panel-20260610-161925` in `https://github.com/dengyie/cpa-management-center`.
- Updated `/opt/homebrew/etc/cliproxyapi.conf` so `remote-management.panel-github-repository` points to `https://github.com/dengyie/cpa-management-center`.
- Replaced `/opt/homebrew/etc/static/management.html` with the latest built panel.
- Rebuilt the local CLIProxyAPI binary from backend main commit `566d4c59` and replaced the Homebrew service binary.

Verification:

```text
Frontend type-check: passed
Frontend lint: passed
Frontend build: passed
Backend go test ./...: passed
Local service: running
Binary: v7.1.58-mango-local / 566d4c59
Panel SHA256: 9c9d3d865f2937f7226aa4080b1e2a96f537950b1e402760ffe32082fbac9fc3
Panel text check: 测试全部资源额度, 删除失败账号, 仅显示有问题凭证, 删除问题凭证
```

Usage note:

- Active batch validation and failed-account cleanup is on `/management.html#/quota` as `测试全部资源额度`.
- `/management.html#/auth-files` supports filtering existing problem credentials with `仅显示有问题凭证`, then deleting them with `删除问题凭证`.
