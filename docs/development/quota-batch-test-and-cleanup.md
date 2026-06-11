# Quota Batch Test And Cleanup Development Spec

Last updated: 2026-06-09

Implementation status:

```text
Frontend first pass implemented on branch feature/quota-batch-test-cleanup.
Review hardening completed: busy batch state is explicit, delete refresh is awaited, and enabled locales are covered.
Merged to frontend main and pushed to https://github.com/dengyie/cpa-management-center.
Backend changes are not required for v1 because the existing auth-file batch delete API is reused.
VPS deployment is still pending.
```

## 1. Background

The CPA account pool currently uses CLIProxyAPI as the backend service and CLI Proxy API Management Center as the web management panel.

The quota management page already supports checking quota for individual credentials across product sections such as Claude, Antigravity, Codex, Grok, Gemini CLI, and Kimi. The requested feature adds a product-scoped batch quota test workflow and integrates cleanup for failed credentials.

This document captures the implementation decisions from the planning/grilling phase. It is the source of truth for the first implementation pass.

## 2. Assets And Repositories

Backend asset:

```text
https://github.com/dengyie/cpa-account-pool
```

Local backend checkout:

```text
/Users/mango/project/codex/cpa-account-pool/CLIProxyAPI
```

Current backend customization branch normally used:

```text
mango-custom
```

Target backend feature branch:

```text
feature/test-all-product-resource-quotas
```

Frontend management center asset to create:

```text
https://github.com/dengyie/cpa-management-center
```

Local frontend checkout:

```text
/Users/mango/project/codex/cpa-account-pool/Cli-Proxy-API-Management-Center
```

Implementation note: before coding, confirm the active backend and frontend branches. At the time this document was written, the backend checkout was not on `feature/test-all-product-resource-quotas`.

## 3. Product Scope

In scope:

- Add a "test all resource quotas" action inside each product quota section.
- Batch test only the current product section.
- Test only resources that are not disabled and are usable for quota checks.
- Limit each product batch test to 5 concurrent resource checks.
- Show batch progress and summary.
- Keep in-memory result groups for success, failure, and skipped resources.
- Allow cleanup actions for the failure group.
- Deploy the customized management center to the VPS.

Out of scope for the first version:

- A page-level "test all products" action.
- Canceling an active batch test.
- Persisting batch result history on the backend.
- Deleting skipped resources.
- Historical cleanup reports.
- Moving credentials to quarantine instead of deleting.

## 4. Current Code Evidence

Backend:

- Quota toggles are in `internal/api/handlers/management/quota.go`.
- Auth file deletion is already exposed through `DELETE /v0/management/auth-files`.
- Existing tests show batch deletion by repeated `name` query parameters, for example `?name=a&name=b`.

Frontend:

- Quota page is `src/pages/QuotaPage.tsx`.
- Product sections use `src/components/quota/QuotaSection.tsx`.
- Generic quota loading is in `src/components/quota/useQuotaLoader.ts`.
- Product quota configuration is in `src/components/quota/quotaConfigs.ts`.
- Existing loader uses `Promise.all`, so the new batch workflow must add concurrency limiting.

## 5. User Experience Requirements

Each product quota section should include a batch action:

```text
测试全部资源额度
```

The button should:

- Run only for the current product section.
- Be disabled while a batch test for that section is active.
- Ignore disabled and non-testable resources.
- Use max concurrency of 5.
- Continue testing remaining resources when one resource fails.

Each product section should show a lightweight summary:

```text
已完成 X/Y，成功 A，失败 B，跳过 C
```

Skipped summary should include a small explanation:

```text
已跳过禁用或不可用于额度查询的资源
```

After a batch test finishes, the product section should expose a grouped result view:

- Success group.
- Failure group.
- Skipped group.

The grouped result view is held in frontend memory and disappears on page refresh.

## 6. Resource Eligibility

Only test resources that satisfy all of the following:

- Belong to the current product section.
- Are not disabled.
- Have the fields required by that product's quota fetcher.
- Are not placeholders.
- Are not runtime-only resources that cannot be tested by the current quota flow.

Skipped resources should be grouped under "skipped" with enough local metadata to show the account name and a broad reason.

First-version skipped reasons can stay coarse:

- Disabled.
- Missing required quota fields.
- Not supported by this product quota test.

## 7. Concurrency Model

Default concurrency:

```text
5
```

The batch runner should:

- Process a queue of eligible resources.
- Start at most 5 active checks at once.
- Update per-resource quota card state as each check starts and completes.
- Update batch summary as each result completes.
- Prevent duplicate batch starts while a section-level batch is running.

No cancellation is required in the first version.

## 8. Result Groups

For each product section, keep an in-memory result object:

```ts
type QuotaBatchResultGroup = {
  success: QuotaBatchResourceResult[];
  failure: QuotaBatchResourceResult[];
  skipped: QuotaBatchResourceResult[];
};
```

Each result item should include:

- Auth file name.
- Display name or email when available.
- Product type.
- Error message for failed resources.
- Skip reason for skipped resources.
- Deletion state for failed resources when cleanup is attempted.

The exact TypeScript shape can be adjusted to match existing frontend types.

## 9. Cleanup Requirements

Failure group actions:

- Select individual failed accounts.
- Delete selected failed accounts.
- Delete all failed accounts.

Skipped group actions:

- View list.
- Copy list.
- No deletion in first version.

Success group actions:

- View list.
- Copy list.
- No deletion in first version.

Deletion behavior:

- Use the existing auth file deletion API if possible.
- After deletion, refetch the auth file list.
- Remove successfully deleted resources from the current product failure group.
- Keep failed deletion items in the failure group and show the deletion error.
- Show a summary notification:

```text
删除成功 A，失败 B
```

No forced `DELETE <count>` input confirmation is required. Use normal confirmation UI consistent with the existing management panel.

## 10. Backend API Strategy

First preference:

- Reuse existing `DELETE /v0/management/auth-files?name=a&name=b`.

Add backend code only if one of these is true:

- Existing batch delete response does not expose enough per-file success/failure details.
- Existing delete handler cannot safely support the front-end cleanup flow.
- A dedicated batch quota endpoint is needed after frontend implementation proves browser-side quota checks are insufficient.

Backend branch target:

```text
feature/test-all-product-resource-quotas
```

Backend tests should be added only if backend behavior changes.

## 11. Frontend Implementation Strategy

Primary changes should be in the management center frontend:

- Add a frontend branch for this feature.
- Create GitHub asset `dengyie/cpa-management-center`.
- Replace upstream remote with user-owned `origin`.
- Keep upstream fetch remote if useful, disable upstream push.
- Implement product-scoped batch controls in quota components.
- Add i18n strings for English and Chinese at minimum.
- Add result group UI and cleanup controls.
- Build single-file management panel output.
- Publish release/asset in the format expected by CLIProxyAPI panel downloader.

Likely files:

- `src/pages/QuotaPage.tsx`
- `src/components/quota/QuotaSection.tsx`
- `src/components/quota/useQuotaLoader.ts`
- `src/components/quota/quotaConfigs.ts`
- `src/stores/useQuotaStore.ts`
- `src/i18n/locales/en.json`
- `src/i18n/locales/zh-CN.json`
- Possibly `src/i18n/locales/zh-TW.json` and other locale files

## 12. Deployment Strategy

Backend:

- Preserve the existing VPS layout:

```text
/opt/cliproxyapi/config.yaml
/opt/cliproxyapi/auth
```

- Build custom backend image only if backend changes are needed.
- Keep existing custom image workflow documented in `CUSTOMIZATION_LOG.md`.

Frontend:

- Build and publish the customized management center.
- Update VPS config:

```yaml
remote-management:
  panel-github-repository: "https://github.com/dengyie/cpa-management-center"
```

- Restart or trigger panel refresh as required by CLIProxyAPI.
- Verify `GET /management.html` returns the customized panel.

## 13. Validation Plan

Local frontend validation:

- Install dependencies.
- Run typecheck/lint if configured.
- Build management panel.
- Manually test quota page with mock or live VPS connection if practical.

VPS validation:

- Confirm management panel loads from the custom asset.
- Run a product-scoped batch test.
- Confirm progress and summary update.
- Confirm success/failure/skipped groups are visible.
- Delete selected failed resources.
- Delete all failed resources.
- Confirm auth file list refreshes.
- Confirm deleted files no longer appear.
- Confirm skipped resources are not deletable.

## 14. Acceptance Criteria

The feature is complete when:

- Each product quota section has a batch quota test button.
- Batch tests only eligible resources in that product section.
- Concurrency is limited to 5.
- Progress summary is visible during and after testing.
- Success/failure/skipped groups can be inspected after completion.
- Failure group supports selecting and deleting failed accounts.
- Delete-all failure action is available.
- Partial delete failures are retained and labeled.
- Auth file list refreshes after deletion.
- The customized management panel is published to `dengyie/cpa-management-center`.
- VPS uses the customized management panel.
- Development and deployment records are updated.

## 15. Risks

- Large account pools can still take time even with concurrency limited to 5.
- Quota check failure does not always mean an account should be permanently deleted.
- Direct deletion is irreversible unless separate backups exist.
- The management panel release format must match CLIProxyAPI's downloader expectations.
- Browser-side quota checks may be limited by CORS or upstream behavior for some providers.

## 16. Open Implementation Checks

Before implementation:

- Confirm exact active branches in both repositories.
- Confirm CLIProxyAPI panel updater release asset expectations.
- Confirm current `DELETE /auth-files` response shape for batch deletion.
- Confirm existing quota card UI layout has room for batch summary and group controls.
- Confirm locale coverage requirements beyond English and Simplified Chinese.
