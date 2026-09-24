# Changelog

## [0.6.14] - 2026-09-25

0.6.13 生产 10 小时观察：重试环代码正确但**从未到达**——浏览器路径在拖动之前就死了。周期画像：`page_load` networkidle 45s 超时降级 domcontentloaded → `page.page_state failed`（渲染进程在内存饥饿下崩溃）→ `No provider detected` → legacy 15s 找不到滑块 → **solve 挂死**（不同周期挂在不同的协议调用上：`_save_debug_screenshot` 的 `page.screenshot`、死驱动连接上的等待均可能永不返回），最长挂死 16h+，token 刷新任务随之整体卡死、僵尸 chromium 驻留。局部预算（`CLOSE_TIMEOUT_S` 等）只护清理段，防不住挂在主流程上的死等。

### Added
- **solve 全程硬看门狗**（`SOLVE_WATCHDOG_TIMEOUT_S=600s`，env `SLIDEX_SOLVE_WATCHDOG` 可调）：`solve()` / `solve_on_existing_page()` 包 `await_with_budget`。超预算 → cancel 求解任务（其 finally 的预算化清理与 profile 锁释放通常仍执行；若任务被遗弃则由看门狗补发）→ OS 级强杀浏览器进程树（`_hard_kill_browser`：预算化 `playwright.stop()` + 按 user-data-dir 枚举并 `kill_chromium_process_tree`）→ 返回 `(False, None)`，编排器随即降级 remote/DrissionPage。CDP 模式只断开不杀外部浏览器。telemetry `solve_watchdog_fired`。
- `_save_debug_screenshot` 加 10s 预算（生产挂死点之一）。

### Notes
- 测试 422 绿（新增 `tests/test_solve_watchdog.py` 5 例）。1GB VPS 的根因是内存饥饿导致渲染崩溃 + 死等无界；看门狗保证"挂死必回收、失败必降级"，但页面能否渲染出滑块仍取决于当刻内存。
## [0.6.13] - 2026-09-24

0.6.12 生产首个周期验证了判定修正（`SLIDE RESPONSE: ok=False code=300`，如实失败不再假通过），但暴露了下一个问题：provider 模式单次失败后直接进 remote fallback，**没有重试拖**——而 scratch.js 前端自己都会 verifyFail 后 3 秒 verifyRefresh 重试。服务端依旧回 300（other-punish，非 302 dragFast/303 deny，是"综合判定不干净"）。

### Changed
- `_solve_with_provider` 重构为重试循环（`PROVIDER_SLIDE_RETRIES=3`）：失败后等前端 reset（2.8-3.6s）→ slider 仍在则重定位元素、重新加载/生成轨迹（attempt 递增换采样）→ 重拖。等价 scratch.js verifyRefresh 循环，给 baxia 多次采样机会。
- 成功分支收口到循环后统一 `_settle_x5sec`；telemetry `provider_result` 加 `attempt`。

### Notes
- 测试不变（417 绿）。版本 0.6.13。若 0.6.13 后仍恒 300：方向为轨迹拟人度（attempt 采样已换）或环境指纹/会话侧。


## [0.6.12] - 2026-09-24

**终局根因（决定性）**：scratch-captcha 0.0.50 反读出 verify 判定枚举：`success=0, other=300, deny=303, dragFast=302, secdataTimeout=305`。前端判 `c.code===e.success`（即 **0**）才 `verifySuccess → checkCookie → bx-x5sec 头 → x5sec`；**code=300 走 verifyFail + 3s 后 verifyRefresh 重试**。生产 27 周期的 `{"code":300,"success":true,"sig":"from bx"}` 里 `success:true` 只是 baxia 网关"已受理"——slidex 被它带偏，把 300 当成功，**轨迹从未真正过验**，票据自然从未下发（bx-x5sec 头、setCookie 回执、context cookie 三处全空，与 0.6.11 诊断完全自洽）。滑块初始页能出（action=captcha）但每次提交都被判 other——下一步若 0.6.12 后仍 300，则方向是轨迹拟人度/环境指纹。

### Changed
- `interpret_slide_json` 语义修正：`success` 为真 **且** `code == success_code`（默认 0）才算成功；`success:true code:300` 判失败（触发正常重试/换轨迹）。

### Notes
- 测试：新增 `test_interpret_slide_json_gateway_accept_is_not_success`（含生产确切响应），修正 1 处旧断言，全套 417 绿。版本 0.6.12。


## [0.6.11] - 2026-09-24

0.6.10 生产首个周期：滑动照旧通过，但**无 `bx voucher captured`**——旁路没听到票据头。补证据链：scratch-captcha 0.0.50 反读确认 verify XHR 的 `success(c,t,n)` 第三参就是 XHR 对象，`verifySuccess(n)` → `checkCookie(n)` → `n.getResponseHeader("bx-x5sec")`，链路成立；那么头没被听到只剩两种可能：sync `response.headers` 子集没含 bx-*（改用 `all_headers()`），或服务端确实没在 validate 响应里下发（IP/会话仍被拉黑）。

### Changed
- `_on_response` 票据旁路：headers 为空时回退 `await response.all_headers()`（bx-* 属 late header 时的 sync 子集遗漏）。
- 新增 `/report?...type=setCookie*` 回执旁听（telemetry `checkcookie_report`）：下一周期形成三态诊断——①voucher 捕获→直接修好；②report setCookieSuccess/Fail 出现→checkCookie 在跑、票据在 jar、轮询该收到（矛盾则查 select_cookies_for_url）；③两者皆无→服务端未下发，问题在风控侧不在代码侧。

### Notes
- 测试不变（17 通过）。版本 0.6.11。


## [0.6.10] - 2026-09-24

0.6.9 的 settle 轮询/reload 在生产全程跑通但依旧无 x5sec。深挖 punishpage.min.js 后拿到决定性证据：**x5sec 从不走 Set-Cookie**——校验 XHR 的响应携带 `bx-x5sec` / `bx-x5sec-root` 头（形如 `x5sec=xxx; Path=/; ...`），页面 `checkCookie` 回调对比 jar 中现有双份 x5sec 后用 `document.cookie = getResponseHeader("bx-x5sec")` 手工写入。patchright 环境下回调链不稳定/浏览器存活窗口太短，票据只到响应头就断了。另证实 0.6.9 的 reload 兜底无意义：通过后重访 punish URL 落在 "Captcha Interception" 中间页，`getSubmitPathPrefix(t)` 返回 undefined，页面跳 `_____tmd_____/undefined`。

### Added
- **票据旁路**（`solver._on_response`）：任何 `_____tmd_____`/`/slide` 响应若带 `bx-x5sec` 或 `bx-x5sec-root` 头且含 `x5sec=` 对，记入 `_bx_voucher` 并发 `bx_voucher_captured` telemetry。
- **voucher 优先解析**（`_settle_x5sec`）：有 voucher 直接正则取 x5sec 值，注入 context（.goofish.com）并合入返回 cookies（telemetry `x5sec_settled source=bx_header`），跳过轮询；无 voucher 才轮询 8s + 短轮询兜底（移除 reload）。

### Notes
- 测试：`tests/test_provider_humanize.py` 新增 2（voucher 头优先合并+注入、_on_response 旁路捕获），miss 测试改为断言**不再 reload**，全套 416 绿。版本 0.6.10。


## [0.6.9] - 2026-09-24

0.6.8 后 27 个生产周期完全一致：拖动全过（`ok=True code=300`，sig from bx），但 x5sec 始终缺席 → orchestrator 严格判定失败 → 无限退避。定位：**阿里 punish 流程的放行票据不在 `/slide` 响应里**，而在通过后 nc.js 的页面续行（回跳/重载原 mtop punish 链路）的 Set-Cookie 里下发；slidex 成功后 ~0.8s 即关浏览器，页面没活到跳转链完成。

### Added
- **x5sec settle**（`_settle_x5sec`，provider 与 legacy 成功分支共用）：只对 punish 类 URL 启用。成功后轮询 `context.cookies()`（0.5s × 8s）；无果则用原 verify_url `reload` 一次（等价 nc.js 回跳）再轮询 3s；拿到即合并进返回 cookies，拿不到原样返回（bot 严格判定兜底，失败语义不变）。

### Notes
- 测试：`tests/test_provider_humanize.py` 新增 3（轮询命中合并、已含/非 punish 短路、reload 后仍 miss 报 telemetry），全套 414 绿。版本 0.6.9。


## [0.6.8] - 2026-09-23

0.6.7 net tap 生产数据的结论：滑动窗口内 JS/网络活动**完全为零**（tap 0 事件 + resource timing 40 条里无 /slide），加上图像匹配距离恒 87px/conf 0.31、dist 恒 258px——合并起来指向一个此前误判的事实：**生产渲染的是 AWSC nc 1.97.2 经典 NoCaptcha 缩条滑块（"按住滑块拖到最右边"），不是拼图**。85px 的"缺口"是背景纹理对按钮图标的伪匹配，拖 87px 只走了 1/3 行程就回弹，前端从未提交 verify，`code=-1` 与 Captcha Interception 轮换全是它的下游症状。remote 截图 1940x54 的细条形态、`initialize.jsonp?scene=register` 亦佐证。

### Added
- **scale 滑块识别**（`providers/aliyun.py locate_elements`）：探测 `#nc_1__scale_text/.nc_scale_text/.nc-lang-cnt/[id*=scale_text]`（或无缺口图），metadata 带 `slider_type="scale"`。
- **满行程语义**（`_solve_with_provider` scale 分支）：`travel = track.width - btn.width`（生产实测 300-42=258px），跳过图像匹配，`_jigsaw_travel_and_points` 只服务真拼图。
- **legacy 路径同等识别**（`_calc_distance_js` 页内探测 scale → `_calc_distance_multi_source` 直接返回 js_dist 满行程，不跑 `_calc_distance`）。

### Notes
- 测试：`tests/test_provider_humanize.py` 新增 2（provider scale 满行程 + find_gap 断言不跑、legacy scale 直返），全套 411 绿。版本 0.6.8。


## [0.6.7] - 2026-09-23

0.6.6 生产验证的结论：URL 审计在每个 provider 周期捕获 **0** 响应（12 秒滑动窗口内 Playwright 完全看不到 HTTP 往返），且 `_on_console` 在 patchright 下也是死路。容器内双探针定位：`page.on("response")` 本身工作正常（`example.com` 导航 1 事件命中，有无 CDP 皆然），但 `page.on("console")` **永远不触发**——它依赖 `Runtime.enable`，正是 patchright 刻意屏蔽的检测特征。所以"0 审计"是真实数据：要么校验请求根本没发出（拖动未被前端接受），要么走了 `page.on` 看不见的通道（sendBeacon / WebSocket / worker）。

### Added
- **页面内网络打点（net tap）**（`_install_net_tap`/`_dump_net_tap`，provider 滑动前 `page.evaluate` 装入、legacy `_wait_slide_outcome` 超时后 dump）：页面内包装 `fetch`/`XMLHttpRequest`/`sendBeacon`/`WebSocket` + `console.log/info/warn/error`，记录进 `window.__slidexNet`。dump 读回后落 `provider_net_tap` telemetry；0 事件时明确告警"校验请求很可能根本没发出"；命中「验证通过」/`captchaVerifyParam` 时作为 **console 兜底成功信号**（patchright 下这是唯一能捕获该标志的通道）。dump 后重置缓冲，重试从零计数。
- **Resource Timing 兜底清点**：dump 时同步读 `performance.getEntriesByType("resource")` 尾部 15 条（含 worker 发起的请求，tap 记不到的），落 `resource |` 日志。

### Fixed
- legacy 求解循环在 `_wait_slider` 成功后装入 net tap，超时路径 `_wait_slide_outcome` 调用 dump——legacy 重试的每次 `code=-1` 不再零诊断。

### Notes
- 容器内探针（`scripts/audit_probe*.py`）：patchright 1.63.0 下 response 事件正常、console 事件恒零；CDP `Network.enable` 可用但生产后端为规避指纹不建 CDP 会话。
- 测试：`tests/test_provider_humanize.py` 新增 4（roundtrip 含缓冲重置与成功标志、零事件、读失败静默、超时路径 dump），全套 409 绿。版本 0.6.7。

## [0.6.6] - 2026-09-23

0.6.5 生产烟测暴露三个缺口：provider 模式（生产 `provider="auto"` 每周期先走的那条路）完全没吃到人形化；结果捕获面全 miss 时无从知道滑动期间真实经过了哪些校验端点；容器 recreate 时陈旧 SingletonLock 让所有后续浏览器启动全挂。

### Fixed
- **provider 路径人形化**（`providers/aliyun.py perform_slide`）：press-hold（录制轨迹 `(0,0,hold)` 首点透传，否则随机 600-1200ms）+ 终点过冲 3-6px 回拖 + 释放前 ±1px 手抖 + 接近段两步 move。**删掉每步 `min(delay, 50)` 截断**——它会把 press-hold 剪成 50ms，真人 300-400ms 的步间停顿也被压扁。
- **陈旧 SingletonLock 自愈**（`_heal_stale_singleton_lock`，launch 前调用）：锁目标 hostname 非本机（容器 recreate 场景）或 pid 已死时，清 `SingletonLock/Cookie/Socket` 三件套。根因：07:44 force-recreate 时有头 Chromium 正在验证中被杀，锁留在 profile 卷里，新容器 Chromium 全部拒绝启动（`profile in use by another computer`，无对话框工具静默死）。

### Added
- **滑动窗口 URL 审计**（`_install_url_audit`，provider 求解全程挂/finally 卸）：捕获面 miss（`code=-1` 且无 tmd slide 包）时，teardown 落出滑动期间全部响应（method/status/url，最多 40 条）+ `provider_url_audit` telemetry——下一步钉真实 verify pattern 的数据源。

### Notes
- 测试：`tests/test_provider_humanize.py` 新增 9（6 过 + 3 symlink 特权 skip on Windows，Linux CI 全跑），全套 405 绿。版本 0.6.6。

## [0.6.5] - 2026-09-23

滑块滑到位后校验包 `code=-1` 仍判失败：轨迹行为特征不够"人"（down 后 10-30ms 就拖、释放干脆利落），且阿里新前端成功标志走 console/前端回调而非 `_____tmd_____/slide` 响应，捕获面漏了。

### Added
- `generate_trajectory`：`press_hold_ms`（按下后按住不动，600-1200ms，看雪 284633 量级）与 `overshoot_back`（终点过冲 3-6px 回拖 + 释放前 ±1px 手抖）。`_do_slide` 两处调用点启用。
- 录制回放 `_replay_slide`：同样的按下按住 + 释放前抖动。
- `_on_console` 兜底成功信号：console 出现「验证通过」/`captchaVerifyParam` 时置位成功事件（mucsbr/aliyun-captcha-fake 同款捕获面），`solve_on_page` 生命周期同步挂/卸。
- `_____tmd_____/slide` 响应体前 200 字节落 INFO 日志，`code=-1` 时可直接分辨捕获的是最终校验包还是中间探测包。

### Notes
- 测试：`tests/test_slider_solver.py` FakePage 放宽为接受 `response`/`console` 两种事件挂卸（399 绿）。版本 0.6.5。

## [0.6.4] - 2026-09-21

Token-refresh / 人工面板走的是 headless `SliderSolver`，不是密码登录 stealth。0.6.3 的 x5sec 票据门卫挡得住假通过，但下午这条路径根本没滑到：iframe 壳页上 `_wait_slider` 只看主文档，`check_completion` 把「从来没出现过」当成「已经消失」。

### Fixed
- Legacy `_wait_slider` / 距离计算 / `_do_slide` 通过 `iter_search_targets` 搜索主文档 + iframe，并钉住 `_slider_scope`，避免只等到 iframe、实际还在主文档上拖。
- `CaptchaRemoteController.check_completion`：从未观察到滑块且没有 `x5sec` 时不再判定完成。`#nc_1_n1z` / `.nc-container` 纳入容器与完成检测。
- Token-refresh 路径的通过 cookie 只认 `x5sec`，`x5secdata` 仍是挑战参数。

### Notes
- 测试：`tests/test_ha_fixes.py` 覆盖 iframe wait 钉作用域、从未出现过的远程完成、出现后消失、仅有 x5sec。`tests/test_slider_solver.py` 收紧 validation cookie。版本 0.6.4。

## [0.6.3] - 2026-09-21

0.6.2 把「已下发 x5sec 仍被 DOM 判失败」修掉了，但通过票过宽：`x5secdata` 是挑战参数，空基线时任何非空 cookie 都会假通过。

### Fixed
- 处罚页通过票只认相对基线新下发的 `x5sec`，且必须已经滑过。`x5secdata` 不再当通过票。基线快照为空、尚未滑动时，已有挑战 cookie 不能 `x5sec_on_punish`。
- vision CPU 超时取消不了工作线程。挂死次数达到 worker 上限时换池，避免槽位被占死后连续 `executor_busy`。

### Notes
- 测试：`tests/test_slider_verification_guards.py` 覆盖空基线 / 仅 `x5secdata` / 滑后值未变仍失败；`tests/test_ha_fixes.py` 覆盖挂死 OCR 换池后下一次能成功。版本 0.6.3。

## [0.6.2] - 2026-09-21

密码登录 stealth 把「处罚页滑块已下发 x5sec、容器消失、父页仍停在 punish URL」误判成失败。
顺带收口 0.6.1 复审遗留：profile 锁超时竞态、OCR `timeout_ms`、legacy 结果判定、vision 超时边界。

### Fixed
- 密码登录 `solve_slider`：处罚页 nocaptcha 拖过后容器消失、URL 仍是 `/punish?x5secdata=` 时，若相对基线新下发了 `x5sec`/`x5secdata`，按通过收口，不再 `hard_block` 后交给二维码。无票据的真实拦截仍拒绝。
- Profile 锁：Python 3.10 `asyncio.wait_for(lock.acquire())` 超时可能丢掉已完成的 acquire；`wait_for(shield(acquire))` 在 3.11+ 超时会卡死。改为 `asyncio.wait` 与 sleep 竞速，done 回调记录所有权，超时/取消后若已拿到则立即释放；释放只发生在本实例真正持有锁时。
- `VisualChallengeRequest.timeout_ms` 覆盖 OCR / IMAGE_TEXT / 图片滑块。CPU 路径走有界 `slidex-vision` 线程池，排队也占槽；满员立即 `error_code=executor_busy`。超时后槽位等到工作线程真正结束才释放。
- 有界等待不再用 `asyncio.wait_for`：3.11+ 会等到被取消的内层协程真正结束。Playwright `close`/`detach`/`stop` 和 vision `close()` 改成 `asyncio.wait` 与 sleep 竞速，预算到点就放弃。浏览器滑块超时后 `close()` 最多等 2s。
- Legacy `_on_response` 与 Aliyun provider 共用 `interpret_slide_json`：`success: false` 优先于 `code==0`，避免 provider 失败回落 legacy 后把失败响应当通过。

### Removed
- `_register_offset_mismatch` 学习链。JS 距离已是上限夹紧，这条路径没有调用方；仍读取已有 `calibration.json`。

### Notes
- 测试：`tests/test_slider_verification_guards.py` 覆盖处罚页 x5sec 通过门；`tests/test_ha_fixes.py` 覆盖锁所有权 / OCR timeout / executor_busy / 忽略 cancel 的 close 预算 / legacy success:false。版本 0.6.2。

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
