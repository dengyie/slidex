# Changelog

## [0.6.2] - 2026-09-21

密码登录 stealth 把「处罚页滑块已下发 x5sec、容器消失、父页仍停在 punish URL」误判成失败。

### Fixed
- 密码登录 `solve_slider`：处罚页 nocaptcha 拖过后容器消失、URL 仍是 `/punish?x5secdata=` 时，若相对基线新下发了 `x5sec`/`x5secdata`，按通过收口，不再 `hard_block` 后交给二维码。无票据的真实拦截仍拒绝。

### Notes
- 测试：`tests/test_slider_verification_guards.py` 覆盖处罚页 x5sec 通过门。版本 0.6.2。

## [0.6.1] - 2026-09-20

浏览器路径高可用根因修复：iframe 作用域、profile 锁、关闭超时、cookie 选域、距离语义、timeout_ms。

### Fixed
- Aliyun / GeeTest `detect`/`locate` 在主文档 + iframe 中搜索；iframe `src` 命中但 `content_frame()` 不可用时不再谎称已适配。
- Provider 响应监听改为 Event + 任务集合；`cleanup_after_result` 取消未完成的 body 读取。Aliyun `success:false` 优先于 `code==0`。
- Profile 锁改为 `asyncio.wait_for(lock.acquire())`，去掉 poll-then-unbounded-acquire 的 TOCTOU。
- `_close` 对 context/playwright 加 30s 超时，随后按 PID / `user_data_dir` 杀 Chromium 进程树，避免隐身浏览器残留。
- Cookie 注入域从 `verify_url` 推导，不再写死 `.goofish.com`；快照按目标 host 选更具体的同名 cookie。
- 距离语义统一为相对行程：图像匹配是缺口，JS `track-btn` 只做上限夹紧。录制轨迹按 cookie + 目标距离缩放。`_image_match` 默认 offset 0，Aliyun 自己带 `-35`。
- `VisualChallengeRequest.timeout_ms` 真正约束滑块求解，超时返回 `error_code=timeout`。
- 远程人工兜底用 `try/finally` 关闭 session；轨迹提交的 400 不再被吞成 500；控制页 `Cache-Control: no-store`。
- Windows 保留名清洗覆盖 `CON.txt` / `COM1.log`（比第一段 stem）。

### Notes
- 测试：新增 `tests/test_ha_fixes.py`。全量 379 passed / 9 skipped。版本 0.6.1。

## [0.6.0] - 2026-09-19

新增纯图片滑块缺口识别能力：无需浏览器/网络，只给图片就能定位缺口。
面向截图、抓包图像、裁剪产物等只拿得到图片的场景。

### Added
- `slidex.vision.SliderImageSolver` — 同步图片滑块求解器，多策略检测管线：
  1. `template_edge` — 有拼图块图时：Canny 边缘 + `matchTemplate`，与浏览器内
     provider 同源（`_image_match`），直接返回缺口左上角 box（不做供应商 offset 校正，
     校正是调用方职责）。
  2. `yolo` — 可选深度学习后端：安装 `slidex[vision]`（社区项目
     chenwei-zhao/captcha-recognizer，YOLO/ONNX，支持无块图与多缺口）后启用；
     未安装时静默跳过。
  3. `contour` — 无块图零依赖几何检测：Canny → 轮廓筛选（尺寸/长宽比/矩形贴合度）
     → 评分；启发式置信度上限 0.8。
  4. `column_profile` — 轮廓无结果时的列边缘能量剖面兜底。
- `SliderImageResult` 结构化结果：`gap_x` / `distance_px` / `confidence` / `method`
  / `gap_box` / `candidates` 与 `to_dict()`。两者从顶层 `slidex` 与 `slidex.vision` 导出。
- `VisualChallengeSolver` 现在把 `SLIDER_CAPTCHA` + `IMAGE_BYTES`/`IMAGE_PATH`/
  `ANDROID_SCREENSHOT_BYTES` 上下文路由到图片求解器（`asyncio.to_thread` 隔离
  CPU-bound），provider 名 `slidex-image`。`ANDROID_SCREENSHOT_BYTES` 从此有了
  内置求解路径——0.5.8 时安卓截图返回 `unsupported_slider_context`（dianping
  REQ-004 阻塞），现走无块图缺口检测。
  `VisualChallengeRequest` 新增 `piece_image_bytes` / `piece_image_path`；`roi`
  复用为检测区域裁剪（坐标自动平移回原图），`metadata.distance_scale` 做行程换算。
- `SlidexVisualCapability`（automation-kit 适配）透传 `piece_image_bytes` /
  `piece_image_path` 参数。
- CLI `python -m slidex.scripts.slide_solve_image`：`--background` / `--piece` /
  `--roi x,y,w,h` / `--distance-scale` / `--min-confidence`，输出与
  `slide_solve_cdp` 兼容的 JSON（无 cookie / telemetry）。
- 可选 extra `slidex[vision]` 引入 `captcha-recognizer` YOLO 后端。

### Notes
- `SliderImageSolver` 只定位缺口几何，不启动浏览器、不产生 cookie、不做滑动。
  实际滑动行程需调用方按初始偏移与 `distance_scale` 换算。
- 测试：新增 `tests/test_slider_image.py` 与 `slide_solve_image` CLI / vision 路由
  用例；合成图保证确定性，不依赖网络或模型权重。346 passed / 9 skipped。

## [0.5.9] - 2026-09-19

Fingerprint self-consistency for headful/light stealth — the slider was being
auto-detected by three read-only navigator attributes.

### Fixed
- Headful password login injected only a 4-line stealth script (avoiding the
  full script's document.fonts/EventTarget/Performance.now/Date overrides that
  blank the login page). It left `navigator.platform` (real Linux x86_64),
  `navigator.userAgentData.brands` (real kernel version) and a numeric-array
  `plugins` fake exposed while the UA claimed Windows Chrome — three direct
  contradictions any risk JS can check for free. New
  `_get_headful_stealth_script` composes the light script (platform/vendor/
  userAgent now consistent with the UA pool) with a `webdriver => false`
  override; plugins stay real.
- `_get_light_stealth_script` now appends a `userAgentData` override
  (`_get_user_agent_data_override_script`) whose brands and
  `getHighEntropyValues` are built from `_build_client_hint_profile`, so the
  headless lite path no longer leaks the real kernel brand either.
- `_harden_password_slider_runtime` no longer overwrites plugins with a
  numeric array — it was clobbering the proper PluginArray shim installed by
  the full script.
- `_build_client_hint_profile` separates `platform` (navigator.platform,
  "Win32") from `platformName` ("Windows"): `userAgentData.platform`, the
  `sec-ch-ua-platform` header and the CDP `userAgentMetadata.platform` now
  emit the high-level name real Chrome sends, not "Win32".
- `_apply_headless_network_fingerprint` → `_apply_network_fingerprint`, no
  longer gated on headless: headful pages get the CDP UA/UA-CH override too,
  so the `Sec-CH-UA` header family matches the overridden UA instead of the
  real kernel.

Evidence: xianyu-auto-bot production, 400+ slider failures over 20 days
(`error:hwR4mj`), solved from a runtime debug snapshot of the failed punish
page. Added `tests/test_headful_stealth_consistency.py` (6 cases).

## [0.5.8] - 2026-09-10

Dependency governance: bound the floating majors that broke CI, and restore
green CI after the starlette 1.x TestClient change.

### Fixed
- CI was red since 0.5.4: starlette 1.x's `TestClient` requires the `httpx2`
  package, so `tests/test_ws_auth.py` failed at collection time. `httpx2` is
  now part of the `dev` extra.
- `test_ws_accepts_valid_token` asserted the websocket registration *after*
  the `with` block; starlette 1.6 delivers the disconnect before the block
  exits, so the endpoint's `finally` (the intended cleanup) had already
  removed it. The assertion now runs while the connection is alive.

### Changed
- The `remote` extra bounds the majors an unpinned install was free to cross
  (`fastapi<2.0.0`, explicit `starlette<2.0.0`, `uvicorn[standard]<1.0.0`,
  `pydantic<3.0.0`); the `dev` extra bounds the pytest family and
  `httpx2<3.0.0`. Starlette is declared explicitly because fastapi's own
  range allows starlette majors, and a starlette major is what broke CI.

## [0.5.7] - 2026-09-10

Review follow-up: explicit browser-data configuration takes precedence over
legacy CWD profile inference.

### Fixed
- `XianyuSliderStealth._resolve_account_profile_dir()` honored a legacy
  CWD `browser_data/` directory even when the operator explicitly set
  `SLIDEX_BROWSER_DATA_DIR` (or passed `browser_data_dir`) — the explicit
  intent was silently ignored and the account stayed on the old location,
  splitting profiles across two places. Precedence is now
  `account_persistent_profile_dir` > explicit `browser_data_dir` > legacy
  CWD dir (only when nothing is configured) > default stable
  `~/.slidex/browser_data`. The legacy branch log message was corrected to
  match (it is now only reachable in the unconfigured case).
- Tests: `tests/test_stealth_dirs.py` gains `test_explicit_config_beats_legacy_dir`
  and `test_default_no_legacy_uses_stable_home_dir`; the legacy-honoring
  test now uses an unconfigured config. 306 passed / 9 skipped.

## [0.5.6] - 2026-09-10

Final CWD-drift cleanup across the stealth stack.

### Fixed
- The persistent account browser profile directory defaulted to
  CWD-relative `browser_data/user_{id}` (two sites: persistent-profile
  launch and the password-login reuse path). Service deployments that
  change their start directory would silently keep writing profiles to
  the new CWD. Profiles now resolve to the stable
  `SlidexConfig.get_browser_data_dir()` (`~/.slidex/browser_data`,
  `SLIDEX_BROWSER_DATA_DIR` overrides). To avoid silent logouts on
  upgrade, if the account already has a profile in the legacy CWD
  `browser_data/`, that directory is still used (with a log notice) —
  new accounts go to the stable location.
- The failure debug snapshot wrote to CWD-relative `logs/slider_debug`.
  It now writes to `SlidexConfig.get_debug_screenshot_dir()`
  (`~/.slidex/debug_screenshots`, `SLIDEX_DEBUG_SCREENSHOT_DIR`
  overrides), matching the existing solver debug-screenshot location.
- Full sweep found no remaining CWD-relative directory writes in the
  library.
- Tests: new `tests/test_stealth_dirs.py` (profile/snapshot dir
  resolution incl. legacy-honoring and no-config fallback); three
  `test_slider_verification_guards` cases that had pinned the old
  CWD-relative behavior now assert the stable tmp-backed directory.
  304 passed / 9 skipped.

## [0.5.5] - 2026-09-10

Follow-up hardening from the 0.5.4 re-review.

### Fixed
- The user/cookie account id sanitizer existed as three near-identical copies
  (`_concurrency` slot identity, `solver` profile dir, trajectory pool cookie
  subdir) that could drift apart. Consolidated into a single
  `slidex/_sanitize.sanitize_pure_user_id()` helper; all three call sites now
  delegate to it.
- Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`,
  `LPT1-9`, case-insensitive) passed the sanitizer verbatim and failed as a
  trajectory cookie subdirectory on NTFS (OSError, silently swallowed). They
  are now escaped with a trailing underscore (`CON` → `CON_`), verified on
  Windows with a live save/load round-trip.

## [0.5.4] - 2026-09-10

Production review of the 0.5.2/0.5.3 follow-up round: all remaining findings
fixed at root cause.

### Fixed
- The control-page WebSocket closed with an unhandled exception when the first
  message was not valid JSON text (`JSONDecodeError`) or a binary frame
  (`KeyError`) — any unauthenticated client could raise an exception on a
  public endpoint. Non-JSON, binary, non-`auth` and wrong-token first messages
  now all close with policy code `1008`; the auth wait timeout is exposed as
  `api._WS_AUTH_TIMEOUT`.
- `GeeTestProvider` no longer accepts `/ajax.php` or `/api/v4/slider` on any
  host: the non-geetest-host fallback path whitelist was the same over-broad
  class as the original `/verify` finding. Responses are recognized only on
  geetest-marker hosts (still configurable for private deployments via the new
  `host_markers=` argument) or on the official domains
  `geetest.com` / `geetest.cn` / `geevisit.com`.
- Account ids flowing into `trajectory_history/{id}_*.json` filenames are now
  sanitized through `sanitize_pure_user_id()` at the single extraction point
  (`SliderConcurrencyManager._extract_pure_user_id`): `/`, `\` and `..`
  sequences are stripped so a caller-supplied id can no longer escape the
  history directory (previously possible with both the old CWD-relative and the
  new anchored path).
- Unredeemed control tickets are now bounded (`MAX_CONTROL_TICKETS`, FIFO
  eviction of the oldest) instead of accumulating without limit.

### Tests
- `tests/test_ws_auth.py`: first-message auth behavior — non-JSON text, binary,
  non-auth JSON, wrong token, timeout and valid-token paths.
- `tests/test_review_fixes.py`: geetest official-domain / unrelated-`/ajax.php`
  / host-marker-extension cases; `sanitize_pure_user_id` traversal cases;
  bounded-ticket FIFO case.

## [0.5.3] - 2026-09-10

### Fixed
- The telemetry artifact declared by `VisualChallengeSolver` and the CDP CLI
  (`Path("telemetry") / "{run_id}.json"`) was a CWD-relative path that never
  pointed at the real file: `SliderSolver._write_telemetry_summary_file` writes
  to `SlidexConfig.get_telemetry_dir()` (`~/.slidex/telemetry/` by default), so
  reports referenced a ghost path. Both declarations now resolve through a new
  `SliderSolver.get_telemetry_dir()` accessor and point at the file that is
  actually written, independent of the process working directory.

## [0.5.2] - 2026-09-10

Production code review follow-up (2026-09-09/10): the remaining P3 findings
are fixed at root-cause level.

### Fixed
- `stealth.py` strategy-statistics and learning-history files no longer use
  CWD-relative `trajectory_history/` paths. They now resolve through
  `SlidexConfig.get_trajectory_history_dir()` (default
  `~/.slidex/trajectory_history`, override via `SLIDEX_TRAJ_HISTORY_DIR` or
  `project_root`), so the location no longer drifts with the process working
  directory.
- The remote-control URL now carries a one-time ticket instead of the session
  token: `issue_control_ticket()` / `redeem_control_ticket()` are single-use
  and session-bound, and `captcha_control_page_with_session` exchanges the
  ticket server-side before injecting the token into the page. The session
  token no longer appears in the URL (access log / Referer / browser history).
- The control-page WebSocket authenticates via the first message
  (`{"type":"auth","token":...}`) instead of a `?token=` query parameter.
- `GeeTestProvider.validate_response` now requires the URL to look like a
  GeeTest endpoint (geetest host marker + known path, or the official
  `/ajax.php` / `/api/v4/slider` paths) before reading a result. Unrelated
  `/verify` URLs no longer leak other endpoints' status into the solver.
- `_chromium_lifecycle._iter_chromium_pids_for_user_data_dir` cleanly
  re-raises `GeneratorExit` so early generator closure never touches mocked
  (non-exception) psutil names.

## [0.5.1] - 2026-09-06

Production code review fixes (full-review pass, 2026-09-06).

### Fixed
- `_replay_recorded_cdp` dispatched `mouseMoved` events before `mousePressed`,
  so recorded-trajectory replay over CDP never produced a drag. Replay now
  dispatches hover → press → moves → release at the recorded final position.
- Chromium cleanup before browser launch is scoped to the solver's own
  `user_data_dir` via the new `ensure_profile_chromium_closed()`. Concurrent
  solvers no longer kill each other's browser through the global last-PID
  registry (`ensure_previous_chromium_closed()` kept for backward compat).
- Chromium process-name matching now normalizes the Windows `.exe` suffix
  (`chrome.exe` previously never matched `CHROMIUM_NAMES`).
- `SliderTrajectoryPool._touch_last_used` no longer raises on unwritable pool
  directories, restoring the documented degrade-to-generated fallback.
- Distance calibration (`offset_correction`) can no longer be poisoned by a
  single image/JS mismatch: updates persist only after two consecutive
  agreeing candidates and are clamped to ±100px; out-of-band values loaded
  from disk reset to the default.
- `slidex.api` trajectory pool now follows `SlidexConfig.from_env()`
  (incl. `SLIDEX_TRAJ_POOL_DIR`) instead of always using the default path.
- `SliderSolver.solve()` serializes same-profile launches with a bounded
  in-process lock: a second concurrent solve for the same account fails fast
  with `profile_lock_timeout` (after `wait_timeout`) instead of fighting over
  the same browser profile / killing the running one.

### Changed
- automation-kit extra tracks `automation-kit>=0.4.0,<0.5.0` (introduced in
  `83c9d5d`, previously reflected only in pyproject).
- `SolverSolver._finalize_telemetry` writes a per-run
  `telemetry/{run_id}.json` summary, making the telemetry artifact path
  declared by `VisualChallengeSolver` and the CDP script real.
- Removed the shadowed duplicate `_get_stealth_script` definition (~330 dead
  lines) and the unused `_run_in_thread` closure in `async_run`.

## [0.5.0] - 2026-07-19

### Changed
- `SlidexVisualCapability` now implements Provider V2:
  `execution_profile(request)` and async `execute(request, context)`.
- automation-kit optional dependency now requires
  `automation-kit>=0.3.0,<0.4.0`.
- Runtime owns timeout/retry/lifecycle events; the adapter no longer emits
  workflow lifecycle events or owns retry policy.

### Removed
- V1 `aexecute(request)` dual-entry adapter path.

## [0.4.0] - 2026-07-18

### Added
- `SlidexVisualCapability`, the single automation-kit integration surface for
  `visual.challenge` requests.
- Strict validation for capability names, operations, contexts, required input,
  provider names, metadata, ROI, page URLs, and positive `timeout_ms` values.
- Cancellable timeout handling and a dedicated `capability.end` event.
- Cross-repository contract CI against automation-kit `0.2.x` on Python 3.10
  and 3.12, including dependencies required by the default remote API tests.

### Changed
- The automation-kit optional dependency now requires
  `automation-kit>=0.2.0,<0.3.0`.
- Sensitive metadata redaction now includes `x5sec` and `x5secdata` keys.
- Provider architecture and authoring rules are now maintained in the
  automation-kit ecosystem development baseline; standalone duplicate guides
  are retired.

### Removed
- Legacy action, artifact, and task-event conversion helpers. Consumers now use
  `CapabilityResult` directly through `SlidexVisualCapability`.

## [0.3.0] - 2026-06-13

### Added - Provider 系统重构 🎯

**核心架构**
- `CaptchaProvider` 抽象基类 — 统一接口适配不同验证码供应商
- `ProviderRegistry` 注册表 — 管理 provider 生命周期和自动检测
- `ProviderElements` / `SolveResult` 数据类 — 标准化接口
- `ProviderSolverMixin` — 无侵入式集成到现有 SliderSolver

**内置 Provider**
- `AliyunNoCaptchaProvider` — 阿里云 NoCaptcha 适配器（从 legacy 迁移）
- `GeeTestProvider` — 极验 GeeTest v3/v4 适配器（canvas 提取 + 版本检测）

**新 API**
- `SliderSolver(provider="auto")` — 自动检测验证码供应商
- `SliderSolver(provider="geetest")` — 手动指定供应商
- `SliderSolver.register_provider()` — 注册自定义 provider
- `SliderSolver.list_providers()` — 列出已注册 provider

**扩展性**
- 插件式架构：10 分钟实现新 provider，无需修改核心代码
- 检测优先级机制：`detection_priority` 参数控制自动检测顺序
- Provider 元数据支持：`metadata` 字段存储供应商特定信息

### Changed
- SliderSolver 继承 `ProviderSolverMixin`，支持 `provider=` 参数
- `_run_solve_loop()` 拆分为 provider 模式 + legacy 模式双路径
- Legacy 模式（`selectors=`）保持完全向后兼容

### Documentation
- 新增 Provider 架构与开发指南（现已并入 automation-kit 生态开发总纲）
- README 更新：Provider 快速开始、供应商支持表、自定义 Provider 示例
- 新增 `tests/test_providers.py` — Provider 系统测试（10 个测试用例）

### Migration Guide
向后兼容，无需修改现有代码。推荐迁移路径：

```python
# 旧写法（仍然支持）
solver = SliderSolver(selectors={"slider_btn": ".btn"})

# 新写法（推荐）
solver = SliderSolver(provider="auto")  # 自动检测
solver = SliderSolver(provider="geetest")  # 手动指定
```

## [0.2.0] - 2026-06-12

### Added
- **CDP 模式**: `solve_on_existing_page(cdp_endpoint)` 连接已有浏览器，不启动新实例
- **可配置选择器**: `DEFAULT_SELECTORS` dict，通过 `selectors={}` 参数覆盖，适配 GeeTest/Shumei 等
- **CLI 入口**: `python -m slidex.scripts.slide_solve_cdp` 接收 CDP endpoint 和选择器配置
- **公共 `close()` 方法**: 根据运行模式自动选择清理路径
- **`find_gap_with_confidence`**: 图像匹配返回置信度
- 中英双语 README（README.md + README_EN.md）

### Changed
- `find_gap_position` 返回类型从 `Optional[int]` 改为 `Tuple[Optional[int], float]`
- `find_gap` 便捷函数保持向后兼容，只返回 `gap_x`
- `profile_dir` 创建延迟到 `_init_browser`，CDP 模式不创建
- `DEFAULT_SELECTORS` 中 list 改为 tuple 防止意外修改

### Fixed
- CDP 模式 fallback 不再尝试启动新浏览器
- CDP 模式失败时快速返回，不再等待 180 秒远程兜底
- `_connect_existing_browser` 添加 page close 监听

## [0.1.0] - 2026-06-07

### Added
- 初始版本：从 xianyubot 提取为独立 slidex 包
- 双引擎求解器：`SliderSolver`（异步 CDP）+ `XianyuSliderStealth`（同步）
- 多源距离检测：OpenCV 图像匹配 → JS DOM → CSS 宽度估算
- 轨迹系统：4 阶段物理模型 + 真人轨迹录制回放池
- 反检测：Chromium 启动参数 + JS 注入
- 远程人工兜底：WebSocket 实时截图 + 人工操作
- 并发管理：`SliderConcurrencyManager`
- `SlidexConfig` 配置管理（环境变量 + 回调接口）
