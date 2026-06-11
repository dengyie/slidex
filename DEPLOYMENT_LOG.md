# CPA Account Pool Deployment Log

Last updated: 2026-06-09 17:18:35 CST

## Context

This workspace is used to track the deployment and migration work for the local `cliproxyapi` / CLIProxyAPI account pool setup.

The local URL originally investigated was:

```text
http://localhost:8317/management.html#/ai-providers
```

That local service was identified as a Homebrew-managed `cliproxyapi` process, not a project running from this workspace.

## Local Service Findings

- Process: `/opt/homebrew/opt/cliproxyapi/bin/cliproxyapi`
- Installed version at investigation time: `/opt/homebrew/Cellar/cliproxyapi/7.1.40`
- Local config: `/opt/homebrew/etc/cliproxyapi.conf`
- Local auth/account directory: `/Users/mango/.cli-proxy-api`
- Homebrew service plist: `/Users/mango/Library/LaunchAgents/homebrew.mxcl.cliproxyapi.plist`
- Upstream project: `https://github.com/router-for-me/CLIProxyAPI`
- Management panel source configured by CLIProxyAPI: `https://github.com/router-for-me/Cli-Proxy-API-Management-Center`

## VPS Target

- SSH host: `google-vps-next`
- VPS IP: `35.212.179.13`
- SSH user: `mango`
- Hostname: `googlevps`
- OS: Ubuntu 22.04.5 LTS
- Docker: installed
- Docker Compose: installed
- Sudo: passwordless sudo available for `mango`

## Deployment Strategy

User preferences confirmed:

- Use public access for now and mark it as temporary.
- Migrate the full local `.cli-proxy-api` directory, including logs and account JSON files.
- Preserve the existing management password hash from the local config.
- Use Docker if the official/community image is usable.
- Open/listen on port `8317`; cloud firewall/security group is handled separately by the user.

Docker image used:

```text
cliproxyapi-mango:baseline-20260609-1640
```

This replaced the original image `eceasy/cli-proxy-api:latest` after the baseline custom-image pipeline was established.

The image exposes `8317/tcp`, uses working directory `/CLIProxyAPI`, and runs `./CLIProxyAPI`.

Local source checkout for custom work:

```text
/Users/mango/project/codex/cpa-account-pool/CLIProxyAPI
```

Local customization branch:

```text
mango-custom
```

Customization details are tracked in:

```text
CUSTOMIZATION_LOG.md
```

## VPS Deployment Layout

```text
/opt/cliproxyapi/
  auth/
  backups/
  config.yaml
  docker-compose.yml
```

The Docker Compose file in this workspace is:

```text
deploy/cliproxyapi/docker-compose.yml
```

It maps:

- `./config.yaml` to `/CLIProxyAPI/config.yaml`
- `./auth` to `/root/.cli-proxy-api`
- `0.0.0.0:8317` to container port `8317`

## Important Config Adjustment

The local config contained two template API key placeholders:

```text
your-api-key-2
your-api-key-3
```

CLIProxyAPI refuses to start the normal API server when these template values remain configured. On the VPS copy, these two placeholder keys were removed after each sync. The real API key and management password hash were preserved.

No plaintext management password or secret key is recorded in this log.

## First Deployment

Initial deployment completed on 2026-06-09.

Initial local auth count:

```text
203 auth JSON files
```

Initial VPS verification:

```text
CLIProxyAPI loaded 203 clients
GET http://35.212.179.13:8317/ returned CLI Proxy API Server
GET http://35.212.179.13:8317/management.html returned CLI Proxy API Management Center
```

Initial local backup:

```text
/Users/mango/.cliproxyapi-migration/20260609-161856/cliproxyapi-local-full.tar.gz
```

## Resync After Adding Accounts

Resync completed on 2026-06-09.

Local auth count before resync:

```text
789 auth JSON files
799 total files under .cli-proxy-api
```

VPS auth count before resync:

```text
203 auth JSON files
```

VPS auth count after resync:

```text
789 auth JSON files
799 total files under auth
```

CLIProxyAPI final load log confirmed:

```text
full client load complete - 789 clients
server clients and configuration updated: 789 clients
```

Public verification after resync:

```text
GET http://35.212.179.13:8317/ returned CLI Proxy API Server
GET http://35.212.179.13:8317/management.html returned CLI Proxy API Management Center
```

Resync local backup:

```text
/Users/mango/.cliproxyapi-migration/20260609-163019/cliproxyapi-local-full.tar.gz
```

VPS pre-resync backup:

```text
/opt/cliproxyapi/backups/pre-resync-20260609-083055.tar.gz
```

## Current Public Access

Temporary public HTTP endpoint:

```text
http://35.212.179.13:8317/management.html#/ai-providers
```

The VPS domain is:

```text
http://vps.mangoq.ccwu.cc/
```

As of 2026-06-09 16:39 CST, the domain without a port is not reverse-proxied to CLIProxyAPI. Requests to the API should include port `8317`.

Current API base URL:

```text
http://vps.mangoq.ccwu.cc:8317/v1
```

Equivalent IP-based API base URL:

```text
http://35.212.179.13:8317/v1
```

Current management panel URL using the domain:

```text
http://vps.mangoq.ccwu.cc:8317/management.html#/ai-providers
```

If a reverse proxy is later configured on port `80` or `443`, the base URL may become:

```text
http://vps.mangoq.ccwu.cc/v1
```

or:

```text
https://vps.mangoq.ccwu.cc/v1
```

Security note: this is intentionally public and HTTP-only for now per user instruction. Management login traffic is not protected by HTTPS until the user adds a domain/reverse proxy/TLS layer.

## Current Runtime State

As of 2026-06-09 17:18 CST:

```text
Container: cliproxyapi
Image: cliproxyapi-mango:baseline-20260609-1640
Version log: CLIProxyAPI Version: v7.1.58-mango-baseline
Loaded clients: 799
Port mapping: 0.0.0.0:8317->8317/tcp
```

## Local Service Update

Date: 2026-06-10

The local Homebrew-managed `cliproxyapi` service was updated from the installed Homebrew binary to the current custom `main` build.

Verification before replacement:

```text
Branch: main
Commit: dfbb821c
Tests: GOPROXY=https://goproxy.cn,direct go test ./...
Result: passed
```

Local build:

```text
Binary: /Users/mango/project/codex/cpa-account-pool/CLIProxyAPI/dist/local/cliproxyapi
Version: v7.1.58-mango-local
Commit: dfbb821c
Default config path: /opt/homebrew/etc/cliproxyapi.conf
```

Local config cleanup:

```text
Removed template API key placeholders:
- your-api-key-2
- your-api-key-3
```

Local backups:

```text
/Users/mango/.cliproxyapi-migration/local-service-20260610-155506/cliproxyapi.conf.before-template-key-cleanup
/Users/mango/.cliproxyapi-migration/local-service-20260610-155506/cliproxyapi.homebrew-original-20260610-155548
```

Replacement target:

```text
/opt/homebrew/Cellar/cliproxyapi/7.1.40/bin/cliproxyapi
```

Post-update verification:

```text
brew service: cliproxyapi started
listen: *:8317
root endpoint: OK
management panel: OK
loaded auth files: 890
test-import route: POST /v0/management/auth-files/test-import returns 401 missing management key, confirming route is registered
version headers: v7.1.58-mango-local / dfbb821c
```

## GitHub Asset

As of 2026-06-09, the custom source asset is hosted as a normal public GitHub repository owned by the user account:

```text
https://github.com/dengyie/cpa-account-pool
```

Repository properties:

```text
Owner: dengyie
Visibility: public
Default branch: mango-custom
GitHub fork flag: false
```

Local source remotes:

```text
origin: https://github.com/dengyie/cpa-account-pool.git
upstream fetch: https://github.com/router-for-me/CLIProxyAPI.git
upstream push: DISABLED
```

Public repository sanitization:

```text
2026-06-09: The public GitHub repository was deleted and recreated after operational details were replaced with placeholders. The remote branch now contains a clean sanitized history rooted at commit ab12b2ee.
```

## Useful Commands

SSH into VPS:

```bash
ssh google-vps-next
```

Manage service:

```bash
cd /opt/cliproxyapi
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
docker compose up -d
```

Check account file count:

```bash
find /opt/cliproxyapi/auth -maxdepth 1 -type f -name '*.json' | wc -l
```

Verify public service:

```bash
curl http://35.212.179.13:8317/
curl http://35.212.179.13:8317/management.html
```

## Future Discussion Notes

- If local accounts change again, repeat the full config/auth sync from local to VPS.
- After each sync, remove template API key placeholders from the VPS config before restart.
- Domain binding and HTTPS are intentionally left for the user to arrange.
- The current workspace is not a Git repository at the time of this log.
- CLIProxyAPI source is cloned under `CLIProxyAPI/` and custom changes are made on branch `mango-custom`.
- VPS currently runs custom image `cliproxyapi-mango:baseline-20260609-1640`.

## 2026-06-10 Local Latest Service And Panel Refresh

Purpose:

- Use the latest local backend source and the latest customized management panel.
- Make the batch quota test and failed-account cleanup UI available from the local service.

Backend source:

```text
Repository: /Users/mango/project/codex/cpa-account-pool/CLIProxyAPI
Branch: main
Commit: 566d4c59
```

Frontend management panel source:

```text
Repository: /Users/mango/project/codex/cpa-account-pool/Cli-Proxy-API-Management-Center
Branch: main
Commit: e5b5fa0
```

Validation before replacement:

```text
Frontend: bun run type-check passed
Frontend: bun run lint passed
Frontend: bun run build passed
Backend: GOPROXY=https://goproxy.cn,direct go test ./... passed
```

Panel release:

```text
Repository: https://github.com/dengyie/cpa-management-center
Release tag: management-panel-20260610-161925
Asset: management.html
SHA256: 9c9d3d865f2937f7226aa4080b1e2a96f537950b1e402760ffe32082fbac9fc3
```

Local service changes:

```text
Updated /opt/homebrew/etc/cliproxyapi.conf remote-management.panel-github-repository
from https://github.com/router-for-me/Cli-Proxy-API-Management-Center
to   https://github.com/dengyie/cpa-management-center

Replaced /opt/homebrew/etc/static/management.html with the latest frontend build.
Rebuilt and replaced /opt/homebrew/Cellar/cliproxyapi/7.1.40/bin/cliproxyapi.
```

Backups:

```text
/Users/mango/.cliproxyapi-migration/panel-20260610-161925/cliproxyapi.conf.before-panel-update
/Users/mango/.cliproxyapi-migration/panel-20260610-161925/management.html.before-panel-update
/Users/mango/.cliproxyapi-migration/local-service-20260610-162433/cliproxyapi.before-566d4c59
```

Post-update verification:

```text
brew service: cliproxyapi running
PID: 13842
Binary version: v7.1.58-mango-local
Binary commit: 566d4c59
Root endpoint: OK
Served management.html SHA256: 9c9d3d865f2937f7226aa4080b1e2a96f537950b1e402760ffe32082fbac9fc3
Served panel contains: 测试全部资源额度, 删除失败账号, 仅显示有问题凭证, 删除问题凭证
Browser smoke check: /management.html#/quota loads and redirects to login without a stored management key.
```
