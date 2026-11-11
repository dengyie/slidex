# Changelog

## [0.11.8] - 2026-11-12

评审清理批：删死代码、last_used 对账与原子写、策略校验（评审 P3 全项 + Delete List）。

### Removed（Delete List 全项）
- **删除四个无生产调用方的加载方法**：`load_best_trajectory`（tolerance 逐级放宽 +
  failed 回退——0.11.7 起生产选轨唯一入口是 `load_unused_human`）、
  `load_random_trajectory` / `get_random_trajectory`（注释声称「provider 模式」，
  全库零调用）、`rotate_trajectory`（LRU 能力已被 `load_unused_human` 的 lru 分支
  原生实现，且评分公式与实际选轨不一致，留着误导）。`import random` 一并清除。
  随删 8 个测试用例 + manual 脚本引用；`_drag.py` 注释同步。

### Fixed
- **`_evict_overflow` 淘汰后对账 `last_used`**：死键不再无界累积（0.11.7 起 LRU
  每次选轨都读它），并自愈清理历史死键。不变式 = `lu keys ⊆ live`
  （淘汰在新录制落盘前执行，新文件没有 last_used 键是正确的——它只记被消费过的）。
- **`last_used.json` 原子落盘**：`_write_last_used` 走同目录 tmp + `os.replace`，
  消除 O_TRUNC 原地写的半截 JSON 窗口（坏 JSON 会被 `_read_last_used` 当
  `{}`，等于丢整本账、LRU 从零重排）。
- **策略值校验**：`SLIDEX_TRAJ_POOL_STRATEGY` 拼错值（如 `lur`）从「静默走 legacy
  分支」改为 WARNING（带变量名）+ 回落 `lru`，与本库「未知值告警回落」契约对齐。
  合法值：`lru`（默认）/ `index`（旧编号序）。大小写归一。
- **`GestureSession.next()` 重试路径去掉重复预探测**：耗尽判定与 `_first_available`
  的探测输入完全相同，每次重试少扫一遍全池；耗尽时两入口殊途同归地跳过 human。

### Tests
- 全量 1190 passed / 18 skipped（0.11.7 为 1195 / 18：-9 死方法用例 +5 新用例）。
  新增：策略校验（未知值告警回落、大小写归一）、淘汰清账（死键 + 历史键自愈）、
  原子写无 tmp 残留；「last_used 写失败不打断选轨」降级契约从 `load_best_trajectory`
  迁移到 `load_unused_human`。修正 `tests/manual` 里自 0.11.1 起就被几何闸门拒收
  却因 `norecursedirs` 不进收集而无人看见的 fixture（步长 80% > 35% 上限）。

## [0.11.7] - 2026-11-12

`SLIDEX_TRAJ_POOL_STRATEGY=lru` 接线：选轨跨会话按 last_used 轮换（兑现旋钮承诺）。

### Fixed
- **`load_unused_human` 在 `rotation_strategy == "lru"`（默认）下改为
  「同距离候选里最久未用优先」**（未用过最先，同为未用按编号）。其余策略维持
  「同距离取最旧编号」的旧行为。
  2026-11-12 生产实证（接线前，strategy 一直是 lru 却无人消费）：dist=258 的
  候选里 `trajectory_959` 两天被选 **18 次**（每个会话都从它开始），同距离家族的
  951/953/955/956 各只放过 2 次——同一条人类轨迹反复重放正是池要避免的指纹。
  距离差仍是第一排序键：LRU 只在距离匹配的候选内部轮换，不会为了轮换选远距离。
- 前置依赖：`last_used` 记账在 0.11.6（peek 不写盘）后才真正准确——探测不再
  打乱轮换节奏。

### Tests
- 新增 `tests/test_trajectory_pool_lru.py`（6 例）：跨会话轮换不回头、未用优先于
  刚用、距离差压过 LRU、非 lru 策略保持旧行为、last_used 缺失退化编号序、
  默认策略即 lru。fixture 注意：同 distance+同 dy 的 payload 会被 0.11.3 幂等
  去重并成同一文件。全量 1195 passed / 18 skipped（0.11.6 为 1189 / 18）。

### Note
- 0.11.6 Note 里记录的「STRATEGY 无消费者」就此关闭：旋钮现在真实驱动选轨。
  显式设过 `SLIDEX_TRAJ_POOL_STRATEGY` 为非 lru 值的部署行为不变。

## [0.11.6] - 2026-11-12

轨迹池 peek 语义：只读探测不再按消费记账。

### Fixed
- **`load_unused_human` / `load_best_trajectory` 新增 `peek=True`**：探测路径降为
  DEBUG 日志且**不写 `last_used.json`**。
  2026-11-11 23:09 生产日志实证：一次求解打两条一模一样的
  `[TrajectoryPool] unused human ... loaded trajectory_959.json`——
  `GestureSession._first_available` 的可用性探测与 `_build` 的真正消费各调一次，
  旧实现两次都按消费记账。后果：日志无法区分探测与消费；只读探测推进 LRU
  时钟（`last_used` 是 `rotate_trajectory` 的打分输入，线上只读审计 22:41 实际
  改写了两个文件的记账）。
- `GestureSession._load_human` 改传 `peek=not consume`。选轨本身按
  (距离差, 文件序号) 排序、与 `last_used` 无关，故探测跳过 touch **不改变选到
  哪条**——测试已钉住。

### Tests
- 新增 `tests/test_trajectory_pool_peek.py`（6 例）：peek 不写 last_used /
  不打 INFO、consume 恰好一次 touch 与一条 INFO、`load_best_trajectory` 的
  peek 语义、选轨与 peek 无关、整条求解链恰好一次落盘。mutation 验证：
  把 `peek=not consume` 改回 `peek=False`，求解链用例立即 FAIL。

### Note
- 已知未接线的旋钮：`SLIDEX_TRAJ_POOL_STRATEGY`（`trajectory_pool_rotation_strategy`）
  一路传到 `SliderTrajectoryPool.rotation_strategy` 后**无任何消费者**——当前选轨
  固定为「同距离取最旧编号」（`load_unused_human` 的排序键），`rotate_trajectory`
  （LRU）在求解链路上没有调用方。接线或删除需产品决策，0.11.6 不动行为。

## [0.11.5] - 2026-11-11

布尔解析全库收口：`_env` 成为唯一词表，且告警终于能指出是哪个旋钮写错了。

### Changed
- **11 个布尔开关全部改走 `_env.env_bool`**（0.11.4 只做了 `config.py` 的 4 个，
  库里还散着三种手搓词表）：
  - `solver.py`：`XY_SLIDER_FINGERPRINT_AUDIT`、`SLIDEX_CDP_PRE_ACTIVATE`、
    `SLIDEX_CDP_PROBE_FROZEN`、`SLIDEX_CDP_REBUILD_FROZEN`。后两个正是 xianyu
    compose 用 `${VAR:-}` 传的旋钮，容器里就是空串。
  - `stealth.py`：`DOCKER_ENV`、`XY_KEEP_VERIFICATION_SCREENSHOT`、
    `XY_SLIDER_HEADLESS_WARMUP`。最后一个原先写成 `not in {...}`——**空串判 True**，
    与「空值视同未设置」正好反向；收口后写成
    `not env_bool(..., False)`，未设/空串/显式假都是 `disable=True`，显式真才是
    `False`，与 0.11.4 之前的行为逐字一致（不是行为变更，是补契约）。
  - `service/app.py`：删除本地 `_env_bool` / `_env_int`，改 import `_env`。
    副产品：`_env_int` 走 `int(float(x))`，`SLIDEX_PX_CONCURRENCY=5.0` 这类浮点
    写法不再被静默丢成默认值。
- `env_bool` 从「零生产调用」变成唯一入口（0.11.4 新增它却没人用，属死代码）。

### Fixed
- **`_reject` 的告警缺变量名**：`parse_*` 把 `str(raw)` 当 `source`，于是日志是
  `[env] maybe='maybe' 不是合法布尔值…` ——14 个旋钮一起注入时根本看不出是哪个键
  写错。`parse_float` / `parse_int` / `parse_bool` 新增可选 `source=` 关键参，
  三个 `env_*` 统一传变量名，告警变成
  `[env] SLIDEX_TRAJ_POOL_ENABLED='maybe' …`。直接调用 `parse_*` 的旧行为不变。

### Tests
- 新增 `tests/test_bool_env_knobs.py`（32 例）：`disable_headless_warmup` 的极性
  （字段名否定、开关名肯定）逐条钉死、三个 CDP 开关的空串/未设默认关闭、
  `service` 工厂默认值。
- `tests/test_env_parsing.py` 补 `parse_bool` 直调、未知字面量必须留 WARNING、
  以及 `TestNoParallelBoolParser`——扫源码禁止 `strip().lower() in {...}` 与
  `== "1"` 形式回流。全量 1183 passed / 18 skipped（0.11.4 为 1147 / 18）。

### Note
- **升级提醒**：0.11.3 及更早把 `true/yes/on` 等字面量一律判 False（静默失效），
  0.11.4 起这些写法才生效。升级前请确认部署配置里这类键的取值是**本意**——
  尤其是 `SLIDEX_TRAJ_POOL_RECORDED_ONLY=true` 这种"当初写了但没生效"的开关。

## [0.11.4] - 2026-11-11

布尔开关的空串契约：`SLIDEX_TRAJ_POOL_ENABLED=` 不再被当成"关闭"。

### Fixed
- **布尔开关不吃空串**（`config.py` 的 4 个开关 + `_env.parse_bool/env_bool`）：
  `trajectory_pool_enabled` / `trajectory_pool_recorded_only_mode` /
  `remote_captcha_enabled` / `telemetry_enabled` 原先写成
  `os.environ.get("SLIDEX_TRAJ_POOL_ENABLED", "1") == "1"`——第二个参数是
  **默认值而非兜底**，env 存在但为空串时直接判 False。容器编排几乎都这么写：
  `SLIDEX_TRAJ_POOL_ENABLED=${SLIDEX_TRAJ_POOL_ENABLED:-}`（xianyu 的 compose
  里 14 个 SLIDEX 旋钮就是这样），`.env` 未设置即注入**空字符串**，结果是
  **整个轨迹池被静默关闭**，日志里连一条告警都没有（`serve_pooled()` 只返回
  False，回放直接退回合成轨迹）。这与 `_env` 开头「空值视同未设置」的既有
  契约直接矛盾，属于同库两套语义。
  - 新增 `parse_bool` / `env_bool`：空串/空白/未设置 → 默认值（静默）；
    `1/true/yes/on/y/t/enable/enabled` → True；
    `0/false/no/off/n/f/disable/disabled` → False；其余字面量回落默认并
    WARNING（`"maybe"` 不再被当成 True）。
  - `trajectory_pool_rotation_strategy` 同步改成 `... or "lru"`，去掉
    `strategy=""` 这种"看似设了其实没设"的中间态。
- 单测（`tests/test_env_parsing.py::TestEnvBool`）：空串/空白/未设置一律取
  默认值、真假字面量双向、未知值回落并告警，以及端到端 `SlidexConfig.from_env()`
  在 compose 式空串注入下 `trajectory_pool_enabled is True`、
  `rotation_strategy == "lru"`，且显式 `=0` 仍然能真正关掉。

## [0.11.3] - 2026-11-11

轨迹池幂等去重：同一份录制重复落盘不再新增文件。

### Fixed
- **同 payload 双写挤占池位**（`_trajectory_pool.save_trajectory`）：写入前按
  「落盘后的 `points`（两位小数、一位时间）+ `distance`」比对池内已有录制，
  命中即复用该文件名返回，不再新建文件。线上实证（`1926782908`，2026-10-12
  ~10-14）：50 个文件里 20 对**完全同 payload**（`recorded_at` 同秒、
  `duration_ms` 同值），有效几何只有 30 种；重复条目既占池位（上限 50）又让
  该几何被抽中的概率翻倍，等于把"轨迹多样性"砍了一半。
  - 只在存量文件**本身健康**（`recorded_trace_verdict` 无原因码）时复用：
    否则新录制会被"去重"成一条永远加载不出来的废条目。
  - 去重后线上池：42 条可回放 / 25 种唯一几何 → 25 条 / 25 种唯一几何，
    重放平滑度不变（motion gap median 17.2ms、max 120.0ms = 闸门上限）。
- 单测覆盖：同 payload 二次保存复用首条且文件数不变、不同距离仍写新文件、
  存量脏文件不顶替新录制（`tests/test_trajectory_pool.py`）；原用同一 fixture
  连存 5 次来测轮转/编号/统计的用例改为逐条不同几何（测的是簿记，不是去重）。

## [0.11.2] - 2026-11-11

生产复核修补：0.11.1 的几何闸门本身有三处会把"防守"变成"故障"的缺口，
以及日志/接口契约问题。全部按实测复现后修，线上池判定结果不变（40 保留 /
10 拒收）。

### Fixed
- **一个坏文件拖垮整个 cookie 的池**（`_trajectory_pool._load_all`）：
  `_dirty_geometry` 原先在逐文件 try 之外调用，坐标里出现 `null`
  （JS `JSON.stringify(NaN)` 的产物）时 `float()` 抛 TypeError，整个
  `_load_all` 中断，连 49 条好录制一起不可读；`_load_human` 只在 DEBUG
  记录后回退合成轨迹——用户侧表现为"真人回放又没了"。现在逐文件隔离，
  判定抛错就跳过该条并 WARNING，其余录制照常参与回放。
- **NaN 穿透闸门**（`recorded_geometry_verdict`）：NaN 参与的大小比较恒为
  False，`span<=1e-6`、`retro>limit`、`biggest>limit` 全部放行；整条 NaN
  轨迹会被判为健康、落盘、并在回放时以 `nan` 坐标注入。同理 NaN/Inf 的
  `distance` 会关闭全部距离判据。新增原因码 `invalid_points`：坐标或
  时间戳不是有限数值、或 `distance` 非有限，一律拒收。写池侧同样拦下
  （`save_trajectory` 对非数值坐标返回 None 而不是抛异常）。
- **调用方假报成功**：`solver.py` 两处写池（`human_cdp` / 远端录制）和
  xianyu 侧 `/trajectory` 原先无视 `save_trajectory` 的返回值，闸门拒收
  时仍打 "saved / recorded" 日志；靠日志判断"录制已入池"会得出相反结论。
  现在按返回值区分 `saved` / `NOT pooled`。
- **HTTP 边界契约**（`api.submit_trajectory`）：400 的 `detail` 带上原因码
  （前端此前只读 `message`，一切拒收都显示 "unknown"，现改读
  `detail || message`）；500 不再回显 `str(e)`（可能含内部路径）；点数
  下限与闸门统一为 2。

### Changed
- **闸门与解码器语义对齐**：`upgrade_points_to_cumulative` 与
  `_x_monotonic_forward` 移入 `_slide_geometry`（`_gestures` 继续再导出，
  公开 API 不变），新增 `recorded_trace_verdict`：加载侧先解码再判几何。
  0.11.1 直接用原始点列判定，会把历史增量编码的健康录制判成
  `distance_mismatch`，使解码器的 legacy 分支永远不可达；现在两者一致，
  且绝对坐标垃圾解码是恒等变换，仍被拒收（线上 50 条判定零变化）。
- `RECORDED_RETRO_RATIO` 明确为 `RECORDED_JUMP_FRACTION` 的别名（实测两者
  共用同一个 `limit`，此前文档暗示存在独立阈值）。
- 脏录制告警改为每个文件名只打一次（进程内），后续降为 DEBUG：实测每次
  拖动至少触发一次 `_load_all`，10 条脏文件约占线上日志 9%。

### Tests
- 新增 `tests/test_review_round2.py`（21 条）：NaN/Inf/非数值坐标与
  distance、坏文件不遮蔽同池录制、写池坏坐标返回 None、手写 NaN 文件加载
  侧过滤、脏文件告警只出一次、legacy 增量轨迹解码后放行而绝对坐标仍拒收。
- 新增 `tests/test_review_round2_callsites.py`（3 条）：闸门拒收时无
  "saved to pool" 日志且有 "NOT pooled"、HTTP 400 带原因码、HTTP 500 不回显
  内部异常文本。
- 测试与注释里指向"几何闸门引入版本"的 0.11.0 统一改为 0.11.1。
- 全量 1124 passed / 18 skipped。

## [0.11.1] - 2026-11-11

录制几何校验：坐标轴坏掉的录制不再进池、不再参与回放（承接 0.11.0 的
采样密度与池旋钮修复）。

0.11.0 修好了时间轴，但线上池里仍有 10/50 条录制是**几何**坏的：
`trajectory_1000` 146 点，0→258 的行程里冲到 608 再弹回；`1001` 冲到
545；`997/998` 单步跳变 220px。`normalize_recorded_delays` 只动时间轴
不判断拓扑，这些录制照样被抽中回放——观感就是"拖到一半滑块飞出去再跳
回来"。此前 `upgrade_points_to_cumulative` 也无法识别：两条实测
`sum(x)` 是 46892 / 73636，离 distance 很远，legacy 分类器靠
`last_x == distance` 直接原样放行。坏的不是编码方式，是几何本身。

### Added
- **`recorded_geometry_verdict(points, distance) -> str | None`**
  （`_slide_geometry`）：单一人体可拖动性判据，只看 x 轴（y 抖动是正常
  手抖）。返回 `no_points` / `no_motion` / `distance_mismatch` /
  `overshoot` / `non_monotonic` / `jump` / `invalid_clock`，健康返回 None。

### Fixed
- **写池拒收**（`_trajectory_pool.save_trajectory`）：几何不合格的录制不
  分配编号、不落盘（返回 None，打 `reject recording ...: <reason>`）。
- **加载侧过滤存量**（`_trajectory_pool._load_all`）：0.11.0 之前写进池的
  脏文件读取时跳过（`skip dirty recording ...: <reason>`），一条脏录制
  不再毒化之后每一次自动拖动。
- **HTTP 上报边界不再说谎**（`api.submit_trajectory`）：被拒的录制回
  HTTP 400，而不是 `{"success": true, "message": "saved"}`——上报方据此
  重录，而不是以为池里已有一条好数据。
- **回放分类器收紧**（`_gestures.upgrade_points_to_cumulative`）：末点
  x 已对上 `distance` 且 x 轴单调（`_x_monotonic_forward`，容忍 ≤1px
  采样抖动）即认定已是累计坐标，不再被 `sum(x)≈distance` 误判为增量编码。

### Changed
- 几何阈值改为**证据推导**，不再是经验值：对线上 50 条录制（用户
  1926782908）做盲测分簇，健康 40 条 / 脏 10 条，取两簇分界并留两侧
  ≥10% 余量——`RECORDED_JUMP_FRACTION=0.35`（max 相邻步 / 行程）、
  `RECORDED_RETRO_RATIO=0.35`（峰值回退 / 行程）、
  `RECORDED_SPAN_RATIO=1.15`（max|x| / distance）。

### Tests
- 新增 `tests/test_recorded_geometry_verdict.py`（30 条）：阈值钉死、健康
  轨迹通过、四类坏轨迹逐个命名、健康侧余量 ≥10%、坏轨迹在 60px/120px
  两种 distance 下都判坏、退化形态（空/单点/tap/负时钟/无 distance）、
  回放分类器（累计原样、真增量仍累加、绝对轨迹不被 lift）、池两侧闸门
  （写不进、存量读不出、全脏池返回 None）。
- `test_trajectory_pool.py` / `test_api_security.py` / `test_gestures.py` /
  `test_review_fixes.py` 的占位 fixture 改为健康多点录制，并新增
  `test_trajectory_with_broken_geometry_is_refused`（HTTP 400 + 不落盘）。
- 全量 1100 passed / 18 skipped。

## [0.11.0] - 2026-11-10

录制轨迹解码与池配置根修（承接 0.10.2 录制时序归一化、0.10.3 数值编号淘汰）。

0.10.2 把慢录制的时间轴压回 650ms 设计带，但只压时间、不减点数：线上
234 点 / 48.4s 的录制压完后是 **360 events/s**（真人约 5Hz，池内其余 49 条
p50 70Hz）——时间轴正确了，采样形态仍是一条"机器轨迹"。同一轮审计还发现
池的左旋配置面（`SLIDEX_TRAJ_POOL_MAX/MIN/STRATEGY/ENABLED/RECORDED_ONLY`）
在 config 里存在、构造点却一律 `SliderTrajectoryPool(base_dir=...)`，改了
env 毫无效果；以及 GeeTest 的 CDP 派发路径把 `ts_ms=None` 当成 0ms。

### Fixed
- **采样密度上限**（`_slide_geometry.normalize_recorded_delays`）：压缩后按
  `RECORDED_GAP_MIN_MS`（8ms，⇒ ≤125Hz）贪心抽稀，保首点（press-hold）与
  末点（释放位），被丢点的间隙累加到下一个保留点。总位移时长、终点几何
  逐字不变；同坐标的合并按死停顿上限收口，二次归一化零改动（线上 50 条
  录制：非幂等 0/50，≤92 events/s，端点漂移 0.000px）。
- **远程面板释放点不再虚构 50ms**（`remote.finish_recording`）：`up` 与上
  一采样点同坐标（松手没再动）时只保留真实间隔，原先那 50ms 缓冲会被缩放
  计入纯位移段，把设计带拉长到 651.6ms。真有位移才留缓冲。
- **池旋钮真正生效**（`_trajectory_pool.from_config/apply_config`，`api`
  与 `solver` 两个构造点统一）：`max_per_cookie` / `min_pool_size` /
  `rotation_strategy` / `recorded_only_mode` / `enabled`，非法值回落类默认。
  `SLIDEX_TRAJ_POOL_RECORDED_ONLY` 补进 `SlidexConfig.from_env`；
  `SLIDEX_TRAJ_POOL_MAX` 的 0 由 `parse_int(minimum=1)` 落到 50（此前会
  让池永远爆满、每条新录制都立刻淘汰自己）。
- **GeeTest CDP 派发与 Playwright 步径同义**（`providers/geetest.py`）：
  `ts_ms=None` 的点前进 15ms（原为 `float(ts_ms or 0.0)` = 0）。生成器只在
  需要改节奏处填 ts_ms，按 0 播等于整串事件挤进浏览器同一帧，判题侧读到的
  是瞬移/零速度。
- **淘汰按 `recorded_at` 优先**（`_eviction_order`）：数值编号只作并列决胜。
  编号会被跨机器/备份恢复复用（线上池 951/1001/1002 号段就是痕迹），按编号
  淘汰会删掉"编号小但刚录"的条目。
- **`save_trajectory` 原子建号**：`open(..., "x")`，并发进程撞号时让位重试
  （3 次），不再覆盖对方刚写好的录制。
- **`duration_kind: "motion"` 落盘约束**：`get_pool_stats` 的均值只统计带这个
  标记的条目（0.10.1 及更早落盘的是录制墙钟 13-48s），跨版本升级后均值不会
  被旧墙钟拉到一个不存在的量级。

### Removed
- **`SliderSolver._replay_recorded_cdp`**：生产不可达（PDB 正确的 provider 路径
  自建 timeline），且带 `or list(points)` 旁路语义，与 provider 路径分叉。
  连带删掉它的 `_FakeCdp`/`_FakePage` 测试替身。

### Changed
- 时序阈值全部可经 env 覆盖（`SLIDEX_RECORDED_MOTION_MS/TOLERANCE/GAP_MAX_MS/
  GAP_MIN_MS/DEAD_HOLD_MS/PRESS_HOLD_MS`、`SLIDEX_SLIDE_END_HOLD_MIN/MAX`）；
  xianyu-auto-bot 的 `docker-compose.yml` 已透传，留空走内置默认。

### Tests
- 新增 `tests/test_remote_recording_normalize.py`（4 条，远程面板入库口）。
- `tests/test_recorded_timing.py` 增加密度抽稀 6 条；`test_review_fixes.py`
  增加池配置面 4 条、原子建号 1 条、按时间淘汰 1 条；
  `test_provider_cdp_signature.py` 增加 GeeTest None 时间戳 1 条。
- 全量 1069 passed / 18 skipped。

## [0.10.3] - 2026-11-10

轨迹池淘汰序根修：池满时删的是**最新**录制。线上实证（1926782908）：池
`max_per_cookie=50`、编号已到 `trajectory_1000`，`save_trajectory` 用
`sorted(glob(...))` 挑"最旧"——字典序里 `trajectory_1000.json` 排在
`trajectory_951.json` **前面**（'1' < '9'），于是 22:27 那条 48.4s 真人
录制被当成最旧删除，23:17 的新录制还复用 1000 这个名字把它覆盖。只要编号
进 4 位，每次保存都会吃掉最新一条。

### Fixed
- **淘汰/加载按数值编号排序**（`_trajectory_pool._sorted_trajectories`）：
  淘汰取最小数值编号；新录制编号取 max+1，不复用；`_load_all` 顺序也随之
  按数值（影响 `load_best_trajectory` 的同距离 tie-break）。
- **同距离轮转 tie-break 按数值编号**：`load_unused_human` 原先按文件名字
  典序取，1000 号永远排最前被反复抽中，951 号轮不到。

### Tests
- 新增 `test_save_trajectory_rotation_at_four_digit_indices`：池内
  998/999/1000 三条、上限 3 时保存，必须淘汰 998、保留 1000、新文件名为
  1001。修复前该用例失败（旧逻辑删 1000 并复用其名）。
- 全量 1057 passed / 18 skipped。

## [0.10.2] - 2026-11-10

拖动卡顿根修：录制轨迹的时间轴不再原样回放。线上实证（1926782908，
2026-11-10 19:55）：CDP 真机模式里人是在远端画面里手动拖的，
`DRAG_RECORDER_JS` 记的是"按下到松开的世界时钟"——轨迹池 50 条 human
全部 13.8-17.3s、单点间隙最大 1.24s。回放侧（`scale_recorded_points` /
`_replay_recorded_cdp`）只缩放几何、原样保留 delay，且
`FIRST_TRY_ORDER` 把 human 放第一位，于是**每次自动拖动**的时间线是
16.7 秒的走走停停（设计带 0.66s、末端握持 0.45-1.10s）。用户侧观感
就是"轨迹很卡"，而它跟网络无关，是录制数据本身的时间轴。

### Fixed
- **录制轨迹时序归一化**（`_slide_geometry.normalize_recorded_delays`）：
  几何（每点 x/y、终点、点序）逐字保留；首点按 press-hold 夹到 1.2s；
  同坐标死停顿夹到 40ms；纯位移段超过设计带（650ms×1.15）时整体压缩；
  单点间隙上限 120ms；幂等。静态录制（无位移）返回空 → 当次弃用。
  三处接入：ingest（`human_events_to_points`，池里存的就是归一化后的点）、
  池加载（`GestureSession._plan_from_human_rec`，**存量脏文件自愈**）、
  派发口（`SliderSolver._replay_recorded_cdp`）。
- **真人录制重复入库**：`_export_human_drag_recording` 读到记录后立即从
  `sessionStorage` 消费。原实现在成功路径与兜底路径各调一次，线上实测两条
  记录相隔 25ms 全部入库。
- **GeeTest 拖动静默降级**：`perform_slide` 补 `cdp_session` 形参 + 流水线
  分支。`_call_perform_slide` 按签名注入，缺形参的 provider 只能逐点
  `await`（每点吃一次 RTT）——这是 CDP 真机模式下唯一还在依赖网络的拖动
  路径。实测证据：40ms 单向延迟 + 抖动时，逐点路径事件间隔 108ms（设计
  25ms）、150ms 延迟下 317ms；流水线路径 34.7ms。
- **降级不再静默**：`_call_perform_slide` 在 `_cdp` 存在但 provider 无
  `cdp_session` 时打 warning + `provider_no_cdp_pipeline` 遥测。
- **派发保真度可观测**：`dispatch_drag_timeline` 每次输出
  `events= design= wall=`（wall-design ≈ 一次 ack 往返），线上无需靠手感
  判断"轨迹是不是又被网络拖慢了"。

### Tests
- 新增 `tests/test_recorded_timing.py`（10 例）钉住：慢录制压缩进设计带、
  几何/终点不变、幂等、快录制不被拉长、死停顿折叠、静态录制弃用、池内存量
  脏文件加载后仍在设计带、派发口兜底。
- 新增 `tests/test_provider_cdp_signature.py`（4 例）钉住：三个 provider 都
  接受 `cdp_session`；GeeTest 有会话时走流水线且不触碰 `page.mouse`。
- 改写 `test_human_events_to_points_conversion`：旧断言（松键前 640ms 死停顿
  原样保留、duration=1500 墙钟）正是本缺陷的规格化，按新语义改写。
- 全量 1056 passed / 18 skipped，零回归。

## [0.10.1] - 2026-11-10

打包根修：恢复 stealth.py 随 wheel 分发。setup.py 的 build_py 排除钩子
（0.9.5 `eb40d34` 引入）基于"服务运行时不 import stealth"的判断，但
xianyu-auto-bot 的 reply_server / xianyu_auth_recovery 运行时就是
`from slidex.stealth import XianyuSliderStealth`——66153dc 时代（xianyu
当前 pin）wheel 一直带着它。若按 0.10.0 升级 pin，xianyu 鉴权链路会
ModuleNotFoundError 崩溃（reply_server 的 fallback 守卫只认整包缺失，
`slidex.stealth` 子模块缺失会原样 re-raise）。移除钩子后行为与 66153dc
连续：xianyu 升级 pin 即平移到整合后的 0.10.1 stealth。

### Fixed
- **wheel 重新包含 slidex/stealth.py**：删除 setup.py 排除钩子（该文件
  仅做这一件事，随之删除），pyproject 注释更正。对站点（px 服务）零影响
  ——站点运行时不 import stealth，wheel 变大 ~400KB 源码而已。

## [0.10.0] - 2026-11-10

深度整合 xianyu 侧 5-mixin 版本（10-30 上游同步世代）相对主仓的三组能力
（上游 b838cd1 风控门控 + #111 人工接管），迁移零删除：手势库/轨迹池/
并发管理器/进程树强杀此前已同源，只补真正缺口。1034 passed（基线 1013 +
新增 21）零回归。

### Added
- **run() 验证前双门控（上游 b838cd1）**：
  - `_evaluate_slider_preflight_risk`：token_refresh 场景读失败史，
    窗口期（`XY_SLIDER_RISK_WINDOW`，默认 1800s）内命中过硬拒绝
    （fail_code 或「验证失败，点击框体重试」+error:）一票延迟；失败达
    阈值（`XY_SLIDER_RISK_FAILURE_THRESHOLD`，默认 6）且窗口内无成功
    记录也延迟——风控高压期先冷却，不硬冲加重。任何读取异常兼容放行。
  - `_check_browser_outbound_ip_consistency`：外层注入
    `expected_outbound_ip`（Token HTTP 链路出口）后，浏览器启动完成再探
    一次（`XY_OUTBOUND_IP_PROBE_URL`，缺省 ipify，置空可关）；不一致延迟
    滑块，避免跨出口复用绑定的 x5sec。未注入/探测失败一律放行。
- **人工滑块接管分支（上游 #111）**：`XY_ENABLE_AUTO_SLIDER=0` 时强制
  有头（人工处理必须显示浏览器），token_refresh 场景的 run() 不再自动
  轨迹硬试——确认页面存在可操作滑块后进入 `XY_MANUAL_SLIDER_TIMEOUT`
  （默认 180s，下限 30）人工等待窗口，成功瞬间快照 goofish 域 Cookie；
  无滑块页面（处罚/二维码）照走既有识别分支，不进无意义等待。
  `_get_cookies_after_success` 异常收口增加人工快照兜底（用户成功后关页
  的场景），消费后即清，禁止跨账号/跨重试复用。
- **`_is_runtime_detached_error` 模块级纯函数**：detached/disconnected
  判定收敛单一来源，run 内局部闭包与验证结果检查的 5 处内联匹配全部
  委托。
- **`_parse_int_env` clamp 语义解析器（review 根因修复）**：风控窗口/
  阈值/人工超时三个 env 用独立容错解析——非法值回落默认并 warning，
  不再让单个坏值经外层 except 静默禁用整条门控；合法值保持上游
  `max(floor, int(v))` 钳制语义（与 `_env.env_int` 的 fallback 语义刻意
  区分）。
- **测试 28 项**：门控 9 场景 + IP 门控 6 场景 + run() 双早退接线 +
  env 容错 2 + **人工接管分支 4 场景**（成功快照/超时清理/无滑块防挂死
  <5s/QR 重定向）+ **快照一次性消费兜底**（关页场景 + 二次调用不得复用）。
  全量 1041 passed / 18 skipped 零回归（基线 1013 + 新增 28）。

## [0.9.8] - 2026-11-09

pxed 第 13 轮 deep_check（16/23）暴露的 0.9.6 自身回归 ×2（都是失败测试
先行锁定；1013 passed 零回归）。B1-B4/D1/D2 的齐挂是 load 3.29 启动风暴
的环境伤害，非代码回归。

### Fixed
- **`_goto` 超时分类回归（C4）**：0.9.6 重写 except 链时只捕
  `asyncio.TimeoutError`，playwright 自家的 `TimeoutError`（不同类）落进
  通用分支被错报成 `challenge_load_failed`（第 13 轮 C4 blackhole 实测
  `failed (challenge_load_failed): TimeoutError: Page.goto: Timeout
  12000ms exceeded`）。恢复 `_is_timeout_exc` 分类（按类名后缀，对两个
  TimeoutError 都成立）。
- **`_rpc._swallow` callback 崩溃**：future 被取消时 `fut.exception()`
  抛 `CancelledError`（3.8+ 是 BaseException），`except Exception` 捕不住，
  日志刷 "Exception in callback _rpc._swallow"（第 13 轮日志实测 ×2）。
  callback 内显式捕 `CancelledError` 返回。

## [0.9.7] - 2026-11-09

pxed 第 12 轮 deep_check（22/23）唯一失败 B5 的根因修复：press 任务的
iframe 注入 evaluate 单次 RPC 超过调用级超时（farm 冷启动 + A/B 组连续
任务挤 2 核的启动风暴尾巴），iframe 没注入 → ENV_PAGE 的 `_pxcts` 没写 →
断言 referrer=''。失败测试先行，1011 passed 零回归。

### Fixed
- **iframe 注入调用级超时当场重试一次**：注入是幂等操作，第 1 次
  evaluate 超时/失败不再把整个 press 任务判成 `challenge_load_failed`
  （B5 断言 referrer='' 的根因），当场重试一次；两次都失败维持既有
  `challenge_load_failed` 语义。farm 级死亡消息仍抛 `_FarmDead` 哨兵
  （交由 0.9.6 的进程内重试统一处理）。

## [0.9.6] - 2026-11-09

pxed 线上第 11 轮 deep_check（20/23）三失败的根因修复批次：一次 driver
死亡事件的级联伤害暴露三个缺口，全部有失败测试先行 + 全量回归零新增失败
（1009 passed）。

### Fixed
- **driver 死亡在 `_goto` 被吞成 `challenge_load_failed`（第 11 轮 C5/D1/D2
  齐挂根因）**：`Page.goto: Connection closed while reading from the driver`
  原先按导航失败返回，异常进不了 `_solve_once` 的 except → farm 不重置，
  同一批并发任务连环撞同一具死 driver。现在 `_goto`（及 `_harvest_vid`）
  对 farm 级死亡消息标记抛 `_FarmDead` 哨兵向上传播，`_solve_once` 统一
  重置农场。
- **无进程内重试**：farm 死亡原先只交还 retryable 给调用方；但重置后
  ~3s farm 即 ready（第 11 轮实测），一次环境死亡不该由调用方承担重试。
  `solve()` 现以 farm epoch（`_reset_farm` 时 +1）判定「farm 真死过一次」
  才重试一次（普通业务失败 epoch 不变，预算不翻倍，变异验证：cookie 读
  失败只触发既有 two-strikes 两次，无 solve 级重试）。
- **僵尸协程异常无人认领**：硬顶放弃的协程死于 driver 关闭时 asyncio 刷
  "Task exception was never retrieved"（第 11 轮日志实测）。新增 `_rpc()`
  统一包装所有调用级超时 RPC（cookies/new_page/close/goto/evaluate）：
  detach 模式到点放弃 + 死因收割进有界观测队列 `_REAPED_RPC_EXCS`；
  `_rpc` 自身被取消（硬顶传入）时连带取消并收割内部协程（实测修复了
  "unhandled exception during asyncio.run() shutdown"）。

### Changed
- 旧测试断言更新（`test_browser_gone_mid_task_resets_farm_and_is_retryable`
  /`test_mid_task_browser_death_resets_farm_and_fails_fast`）：进程内重试
  后 farm 不再必然置空——新契约是「farm 不留在死 browser 引用上 + epoch
  前进 + retryable」。

## [0.9.5] - 2026-11-08

深度 review（结构 + 性能）修复批次：并发槽、事件循环阻塞、多租户隔离、
wheel 分发面。全部变异验证或有明确不变量测试。

### Fixed
- **vid 补种占用主并发槽（F1）**：press 且 vid 缺失时，种子任务原先在主槽
  内跑（最长 25s），concurrency=2 的部署点上两个 vid 缺失的调用方互顶成
  `solver_busy`。现在主槽在补种期间放回，种子走独立限速槽（全局并发 1），
  完成后重新排队进主槽。变异验证：删掉放回 → 新测试失败。
- **`_pin_oom_score_adj` 阻塞事件循环（F4）**：/proc 全量扫描同步执行
  （pxed 实测 ~140ms/轮，每任务两次），会卡住其它请求半个 poll 周期。
  新增 `_pin_oom_score_adj_async()`（`asyncio.to_thread`），launch 后与
  每任务前两个调用点均走线程池。变异验证：回退同步直调 → 测试失败。
- **`TaskStore.cancel_all` 竞态窗口（F3）**：list→cancel→clear 三步之间
  create 插入的任务会被 clear 抹掉（任务在跑但 GET 404 的孤儿）。改为
  `async with self._lock` 内 cancel+clear 原子完成。附时序不变量测试
  （注：单线程 TestClient 下竞态窗口写不出稳定失败，测试断言不变量）。
- **v2 任务无属主隔离（F7）**：多 key 部署点上 A 的 key 能轮询到 B 的
  taskId 结果。`TaskRecord` 记创建者 key 的 sha256 指纹（前 16 hex，不存
  key 本体），`GET /v2/tasks/{id}` 时比对，不匹配一律 404（不泄露存在性）；
  开放模式不受影响。变异验证：禁用比对 → 测试失败。

### Changed
- **wheel 排除 `slidex/stealth.py`（F2）**：12k 行闲鱼专用链路只被
  tests/demo 引用，服务运行时不 import——经 `setup.py` 的 `build_py`
  钩子排除，wheel 355KB→235KB；源码仓保留维持测试。运行时依赖
  `_stealth_patch` 不受影响。
- **`/health` 的 `types` 输出按 handler 去重（F6）**：从 11 个别名收敛为
  每求解器一个规范名（`["geetest_v3", "px_press"]` 形状），registry 新增
  `canonical_types()`。
- **`PxPressResult` 构造改 keyword（F5）**：solver 内 7 处位置参数
  `success` 改为 `success=True/False`，杜绝未来插字段静默错位。
- **context 装配去重（P2-2）**：`_solve_once` 与 `_harvest_vid` 重复的
  30 行装配逻辑（context 创建 + 断连重置 + stealth 注入 + cookies）抽成
  `_new_task_context()`，probe 差异用参数表达。

### Verified
- 全量回归与部署实测见 deploy/README 同版本文档。

## [0.9.4] - 2026-11-08

pxed（4G 无 swap 共享 pod）常态化部署专项：修掉宿主全局 OOM 杀 chromium
的 `Target crashed`，并修一个服务日志里实际出现过的真 bug。

### Fixed
- **`expose_binding` 未 await（真 bug，服务日志实锤）**：
  `_expose_parent_probe` 里 `expose("__slidexParentMessage__", …)` 是协程
  却没有 await——uvicorn 日志出现 `coroutine 'BrowserContext.expose_binding'
  was never awaited`，binding 从未注册，父页 postMessage 观测是死路。
  改为 `await`，回归测试断言 binding 真实注册 + handler 可调用；
  变异验证（去掉 await → 测试失败）。

### Added
- **`SLIDEX_PX_CHROMIUM_ARGS`**：追加 chromium 启动参数的环境变量（空格
  分隔，经 shlex 解析），供低内存部署点注入收窄参数（如
  `--renderer-process-limit=4`），不写死默认值；测试覆盖解析与
  farm→launch 连线。

### Ops（pxed，代码外，脚本入库 `deploy/`）
- `slidex-oom-guard`（supervisor 常驻，`deploy/oom_guard.sh` + conf）：每 1s
  把本机 chrome-headless 进程的 `oom_score_adj` 钉到 **-20**（容器缺
  `CAP_SYS_RESOURCE`，-500 写入 EACCES，-20 是 supervisor 主进程 adj
  允许的下限）。根因：pod 3.9G 无 swap + overcommit=1，宿主全局 OOM
  （dmesg 122 次 oom-killer，`constraint=CONSTRAINT_NONE`）专挑 adj=300
  的 chromium；cgroup 自身 failcnt=0 不是限额内 OOM。solver 内另有
  launch 后/每任务前自钉，guard 兜底空档。验证：修复前重复 context
  循环 1-2 轮即 Target crashed，修复后 20/20 轮全绿。
- `deploy/supervisor-slidex-captcha.conf` + `deploy/README.md` pxed 章节：
  supervisor 形态安装步骤、env 需自加载（服务端已实现
  `_load_env_file()`）、`SLIDEX_PX_CHROMIUM_ARGS=--renderer-process-limit=4`
  为 pxed 必需（缺它 B 组指纹断言整组拿空）。

### Verified
- 全量 999 passed / 18 skipped（+3：expose await 变异验证 1、
  env 文件加载 2）。

## [0.9.3] - 2026-11-08

S3 断层根因修复：0.9.2 的 `sensor_console` 抓到传感器静默退出（19 条
pageerror + 零 collector beacon），二分实验锁定 `_STEALTH_INIT_SCRIPT` 的
`window.chrome` getter 自引用是唯一杀手。

### Fixed
- **window.chrome getter 无限递归（P0，线上 S3 断层根因）**：playwright
  headless Chromium 里 `window.chrome` 是 undefined，旧脚本
  `{get: () => window.chrome || {runtime: {}}}` 的 getter 读
  `window.chrome` 会再次触发自身 → Maximum call stack size exceeded →
  任何碰 `window.chrome` 的页面脚本（含 PX 传感器、微软 oneds analytics）
  静默死亡，collector beacon 一个都发不出。实验证据链：
  1. 裸 playwright（无 stealth）跑 signup.live.com：`_pxvid/_px3/_pxde`
     全部种出——网络/代理/脚本源全排除；
  2. 逐条二分 stealth 脚本 5 行：只有 chrome 行复现失败；
  3. `defineProperty` 自引用直接复现 `Maximum call stack size exceeded`；
  4. 修复后（完整 chrome 形状：app/runtime/csi/loadTimes，无自引用）：
     PX cookie 种出 + 0 pageerror（连带 oneds 的错误也消失）。
- 新增回归测试：getter 体内不得自引用 + 关键子形状存在；变异验证
  （改回自引用 → 测试失败）。

### Verified
- 全量 993 passed / 18 skipped；线上 S3 重跑验收见 0.9.3 部署后报告。

## [0.9.2] - 2026-11-08

冒烟报告（grok-register §4.1 live，2026-11-08 05:22–05:37）后续：双页结构
与诊断字段已生效，但真实挑战仍无 token——诊断把断层收敛到「main.min.js
加载成功但零 collector beacon、零 PX cookie」，即传感器在 headless
Chromium 内静默放弃上报。本版按报告 §3 落地增量诊断，不做 stealth 加固
（等有头对比实验结果再定）。

### Added
- **diagnostics.sensor_console（报告 §3 P0-1）**：`page.on("console")` +
  `page.on("pageerror")` 挂进任务页，PX 静默退出通常在 console 留痕
  （"App communication failure" / api blocked）。脱敏：级别+截断 200 字符、
  单任务上限 20 条（防页面脚本刷屏放大内存）。
- **diagnostics.collector_requested / collector_request_error（报告
  §3 P0-2）**：`page.on("requestfailed")` 区分「collector beacon 没发出」
  （requested=false → 执行层断）vs「发出但失败」（requested=true +
  error 有值 → 网络层断，记 failure.errorText 一行 200 字符）；response
  成功后 error 复位。URL query（会话参数）不进诊断。

### Verified
- 两个诊断路径均变异验证（删监听 → 对应测试失败，恢复后全绿）；
  全量 992 passed / 18 skipped。
- 线上实验（S3 重跑 + 有头对比）结果回填冒烟报告 §5。

## [0.9.1] - 2026-11-08

0.9.0 部署后线上 deep_check 回归抓到的问题（21/23 → 修复目标 23/23）。

### Fixed
- **silent 模式丢失首屏状态判死（P0，deep_check C5 回归）**：0.9.0 重构把
  403/404/407/5xx 判死挪进了 press 分支，silent 分支整个没了——代理 407 被
  当文档渲染后一路轮询到预算耗尽，返回误导性的可重试 `px_token_timeout`
  （1002）。修复：判死提成 `_first_paint_kill` 共享方法，press/silent 两个
  分支都过同一道门。变异验证：删掉 silent 判死 → 两个新测试（silent 407、
  silent 404）都失败；恢复后全绿。本地单测此前没抓到是因为 407/403/404
  用例全是 press 路径，silent 分支零覆盖。
- deep_check B5 跟进 v2 契约（非 solver 缺陷）：v2 下无 vid 的 press 会先
  自动补种子（合成站种不出 `_pxvid` → `caller_vid_required`，pxcts 为空），
  且双页结构会真导航 `websiteURL`——用例改为合成宿主页 + 带 vid + 断言
  challenge iframe 的 `document.referrer` 等于宿主 origin（跨源
  strict-origin-when-cross-origin 只保留 origin）。

### Verified
- 全量 989 passed / 18 skipped（+2 silent 判死测试）。

## [0.9.0] - 2026-11-08

需求单 v2（px_press 真实挑战求解修复）：live 联调证明直接导航 hsprotect
iframe URL 时 postMessage 通道永远沉默，110–120s 预算内连非通过级 `_px3`
都种不出来。PX token 依赖「宿主页 + iframe」双层协作，单层结构无解。

### Added
- **press 双页上下文（§3.1，P0）**：press 求解不再直接导航 `challengeUrl`，
  改为顶层页先落 `websiteURL` 域（fail-fast 判死 403/407/4xx/5xx），再把
  challengeUrl 作为 **iframe 注入宿主页**（`page.evaluate`，320×110 可见
  尺寸），postMessage 由浏览器自然打通。cookie 抽取照旧扫全 jar——
  iframe 会把 `_px*` postMessage 给父页，PX main.min.js 写回宿主页域。
- **vid 自动补种子（§3.2，P0）**：press 且 `vid` 为空时不再沉默裸导航——
  先用裸 silent（独立 `_harvest_vid` 循环，只等 `_pxvid` 出现，预算 25s）
  领初始 vid，种进 cookies + `vid` 字段后继续 press；种子也失败则立刻
  `caller_vid_required`（可重试）失败，不再烧满预算。silent 模式 vid 本就
  可选（v1 契约），不做种子套娃。
- **诊断 diagnostics（§3.3，P1）**：无论成败，`PxPressResult.diagnostics`
  携带传感器执行链观测——`iframe_loaded/iframe_status`、
  `captcha_js_loaded/captcha_js_status`、`main_min_js_loaded/_status`、
  `collector_seen`、`parent_message_seen`（context 级 expose_binding +
  init script 探针，只记「有过」）、`px_cookie_names_seen`（只有名字+域，
  绝无值）。`/v1/solve` 放在 `data.diagnostics`，v2 轮询 fail 信封附
  `diagnostics` 键，success 的 `response` 里透传；老客户端忽略未知键。

### Fixed
- press 模式下 challengeUrl 的 403/407/4xx/5xx 判死提前到 iframe 注入之前——
  否则一个 404 的 challengeUrl 会先白等 iframe 就绪预算再失败。

### Verified
- 变异验证（三处）：删掉宿主页 fail-fast 分支 → `test_host_page_404_fails_fast_before_iframe_injection`
  失败（回到白等 iframe）；删掉 vid 补种 → `test_vid_missing_and_seed_fails_returns_vid_required`
  失败；删掉 v2 诊断透传 → `test_v2_fail_response_carries_diagnostics` 失败。
- 全量 987 passed / 18 skipped（新增 10 条：双页导航、宿主判死、诊断成劣
  双路、诊断采集分类、vid 补种三态、cookie 名脱敏、v2 透传）。
- 真浏览器仿真（test_px_sim）确认 silent 无 vid 任务不受种子逻辑影响。

## [0.8.5] - 2026-11-08

Code review 收尾：代理 407 拿到独立 kind 与业务码，调用方在数字码层面就能区分
「改代理配置」（407）与「换 IP / 查链路」（403/5xx/连接失败）。

### Changed
- **新增 kind `challenge_proxy_auth`**：代理 407 不再与页面层 HTTP 错误共用
  `challenge_http_error`——两者处置方向不同（407 是代理配置坏了，同一 payload
  重试永远一样；403/5xx 是页面/上游层面的事）。
- **新增业务码 `SOLVE_PROXY_AUTH = 1006`**，`challenge_proxy_auth` → 1006。
  `challenge_http_error`（403/429/404/5xx）保持落默认 1001，已在
  `codes.py` 注明「处置看 data.retryable」。
- README 错误码表补齐 1005/1006。

### Removed
- `_SubresourcePage` 测试构造器里从未使用的 `status=200` 关键字参数。

### Verified
- 变异验证：把 `for_kind` 的 1006 映射删掉 → `test_proxy_auth_407_maps_to_1006_not_1001`
  失败（落 1001）；把 solver 的 407 分支改回 `ERR_CHALLENGE_HTTP` →
  `test_proxy_407_fails_fast_not_retryable` 失败。
- 全量 977 passed / 18 skipped。

## [0.8.4] - 2026-11-08

失败类别收口：把「根本没到达挑战页」从「页面加载了但没出 token」里拆出来，
让调用方能按真实原因处置（换 IP 降风控 vs 先查代理链路）。

### Changed
- **新增 kind `challenge_load_failed`**：导航阶段抛异常（代理拒绝、
  DNS、TLS、`ERR_UNSAFE_PORT` 等）不再一律报成 `challenge_load_timeout`。
  `challenge_load_timeout` 现在**只**表示导航真的超时（黑洞代理 / 慢站）。
- **业务码拆分**：新增 `SOLVE_LOAD_FAILED = 1005`。
  - `1005` = 连不上挑战页（`challenge_load_failed` + `challenge_load_timeout`）
  - `1002` = 页面已加载，但预算内没出通过级 token（`px_token_timeout`）
  两者都是 `retryable=true`，区别在处置方向：1005 先查代理链路，1002 换 IP/降风控。
- 失败消息带上底层原因（截断 300 字符），例如
  `challenge page unreachable: Error: net::ERR_PROXY_CONNECTION_FAILED at ...`，
  排障时不用再去翻服务日志。

### Verified
- 变异验证：把超时判定改成恒 `False` → `test_navigate_timeout_is_load_timeout`
  失败；把 codes 映射改回 1002 → `test_load_failure_maps_to_1005_not_1002` 失败。
- 全量 976 passed / 18 skipped（新增 2 条：导航异常 vs 导航超时、业务码拆分）。

## [0.8.3] - 2026-11-08

深度验收新增 C4/D5 项时顺带抓到的第五个失败模式：代理 407 吃满整个预算。

### Fixed
- **代理 407（要认证）不再耗满预算**：Chromium 把代理响应 407 当主帧文档渲染
  （实测 `status=407, resource_type=document`），旧逻辑只把 403/429/404 当
  确定性终止，407 于是进入轮询直到 `px_token_timeout`（可重试）——而代理密码
  配错时，同一个 payload 重试永远不会好。现在 407 按确定性失败早退：
  `challenge_http_error` / `retryable=false` / 错误信息点名
  `proxy requires authentication (407)`。
  502 保持可重试（代理可能瞬时故障）；403 已是确定性失败。

### Verified (live, gz-cvm 10.144.144.8:8077)
- 目标页合法、代理回 407：修复前 12.2s `px_token_timeout`（吃预算 + 误导）；
  修复后 ~0.1s `challenge_http_error` `retryable=false`。
- 代理回 502：0.1s `challenge_http_error` `retryable=true`；回 403：0.1s
  `retryable=false`（回归不变）。
- 新增回归测试 `test_proxy_407_fails_fast_not_retryable`；变异验证：去掉 407
  分支后该测试如预期失败（`px_token_timeout`），证明测试真的把得住。
- 深度验收 22/22 全绿（契约 7 · 卫生 6 · 代理 4 · 负载 5）。

## [0.8.2] - 2026-11-08

线上深度验收（第二轮）抓到的回归：0.8.1 引入的「主帧最后一条状态码」在
**子资源**上也会更新，把两个方向都判错。

### Fixed
- **子资源状态覆盖文档状态**：Playwright 的 `response.frame` 对 `<script>` /
  `<img>` / `fetch` 这些子资源同样指向 main_frame，旧代码只比 `frame == main_frame`
  就记状态，于是：
  - 文档 200 + 一个 404 的**阻塞**脚本 → 最后记录的是 404 → 健康页被判
    `challenge_http_error` / `retryable=false` 直接误杀；
  - 文档 404 + 一个 200 脚本 → 最后记录的是 200 → 本该 fail-fast 的 404 页
    进入轮询，耗满预算变成 `px_token_timeout`（可重试）。
  现在只接受 `request.resource_type == "document"` 的响应（`_is_document_response`），
  取不到类型的老式假对象按文档处理，保持既有测试语义。

### Verified (live, gz-cvm 10.144.144.8:8077)
- 文档 200 + 404 阻塞脚本：`code=0`，1.4s 成功（修复前被判死）。
- 文档 404 + 200 阻塞脚本：`code=1001 challenge_http_error`，
  `retryable=false`，0.1s（修复前拖成超时）。
- 深度验收 16/16：契约边界 7 项、上下文卫生 6 项（时区/UA/locale/隐身/referer/
  cookie 落文档）、代理铁律 3 项（含死代理不回退直连）。
  C1/C2 的验收口径同时修正：对 `http://` 目标 Chromium 走绝对 URI 的 plain GET，
  不是 CONNECT——原断言只看 CONNECT，属于测试自身误判，非服务缺陷。

## [0.8.1] - 2026-11-08

线上深度 review 第二轮：8 个根因修复。全部有可复现证据（本地假页面单测 +
真实 Chromium 行为验证），不靠猜测改代码。

### Fixed
- **URL 不是 http(s) 时拖动整个预算才超时**：`challengeUrl` 写裸域名
  （`signup.live.com`）时 Chromium `goto` 直接抛 `Cannot navigate to invalid URL`，
  `ftp://` 则 `net::ERR_ABORTED`——两者都是确定性错误，但此前都落进
  `solver_internal_error` 并标可重试，调用方会拿同一个坏 payload 重试到超时。
  现在 `from_payload` 校验 URL scheme，创建任务即 422、`retryable=false`。
- **非法 IANA 时区被误标成可重试 internal**：`new_context(timezone_id="Not/AZone")`
  本身**不报错**（实测），错误发生在之后的 `new_page()`：`Invalid timezone ID`，
  被兜底 except 吞成 `solver_internal_error`（可重试）。而这是调用方参数错误，
  重试同一 payload 永远不会好。现在解析期校验 `zoneinfo.ZoneInfo`，
  非法时 422；机器无 tzdata 时放行（少拦不误拦）。
- **重定向后的拦截页被当成正常页轮询**：状态码原按「target URL」取，PX 把
  challenge URL 302 到别的域名后再回 403 时，只看到那条 302，于是一直轮询到
  `px_token_timeout`。改为主帧**最后一条**响应状态（即最终渲染的文档）。
- **404 被当成可重试超时**：旧逻辑 `retryable = status not in (403, 429)` 让 404
  变成「可重试」，调用方会对一个写错的 challengeUrl 反复重试。现在 403/429/404
  一律确定性终止；新增 **>=500 视为上游故障、可重试**（此前 5xx 落到
  `px_token_timeout`，语义错且同样耗满预算）；导航失败统一
  `challenge_load_timeout`。
- **任务中途 Chromium 死亡仍耗尽整个预算**：cookie 读取失败原被吞成空 token，
  循环继续 poll，直到预算耗尽才返回 `px_token_timeout`，期间日志每个周期刷一条
  警告，而且农场里那具死进程不回收（后续任务继续挂）。现在读失败即判定
  `browser/context lost mid-task`（`retryable=true`），确认断连时重置农场让下个
  任务拿新进程；仅在首次失败打一条警告。
- **`pxcts` 被 `uuid` 伪造**：缺 `pxcts` 时曾把调用方的 `uuid`（leadingChallengeId）
  填进 `pxcts`。两者语义不同，伪造值会让调用方的 risk/verify 当成无效证据。
  现在只从 cookie 取真值，缺就报在 `missing` 里；只有 `pxvid` 仍可由 `vid` 补。
- **`geetest_v3` 每个任务泄漏一个浏览器**：handler 从不关闭 `SliderSolver`，
  每次调用留下一个 Chromium + playwright driver，1.9G 小机器上很快 OOM。
  `solve` 改为 try/finally 无条件 `close()`（失败路径也关）。
- **`systemctl restart` 后旧进程的 Chromium/pw driver 苟活**：服务没有关停钩子，
  重启后新旧两套浏览器抢内存。新增 FastAPI `lifespan`：先 `cancel_all()` 取消
  在跑任务，再 `close()` 共享 solver。

### Notes
- 测试 +11（请求模型 3、服务路由 3、求解器 5）：裸域名/ftp URL→422、非法时区→422、
  重定向→403/404 fail-fast、404/5xx 快速终止与 retryable 区分、任务中途断连快速
  失败并重置农场、geetest solver 成功/失败都关闭、lifespan 关停回收。
  全量 **971 passed / 18 skipped**（0.8.0 基线 960 + 11，零回归）。
- `_read_tokens` 现在失败返回 `None`（原返回空四件套），调用方按「context 已死」
  处理——语义更贴合事实，也让「读不到」和「读到了但没 px3」不再混淆。

## [0.7.0] - 2026-11-06

provider 治理波（配合 automation-kit 0.6.0）：`visual.challenge` 迎来第二个真实同类 provider。

### Added
- **`SlidexManualCapability`**（`slidex/integrations/automation_kit.py`）：把公开的
  `ManualFallbackSession` 完成句柄包装为 kit capability provider（provider 名
  `slidex-manual`，manifest `priority=-10` 低于自动求解）。`execute` 打开 manual
  session 并交给注入的异步 completer（人工/Agent 驱动）产出结果；无 completer
  （无人值守）时返回 typed `manual_fallback_pending` 可重试失败，kit 侧走查式
  故障转移与重试策略据此确定性决策。集成测试含双 provider failover 门控证明。

### Changed
- `automation-kit` extra 兼容范围扩展至 `>=0.5.0,<0.7.0`（kit 0.6.0 的多 provider
  registry / manifest priority / 健康账本为本依赖的前置）。

## [0.8.0] - 2026-11-07

新增 PerimeterX（PX）press-token 求解与**可自托管打码站**：headless Chromium
打开 hsprotect iframe（或站点页），等 PX JS 写入 `_px3`，从 cookie jar 抽出
四件套 `{"px3","pxde","pxvid","pxcts"}`；成功判据严格按需求单 §3.3——
**`px3` 必须含 `:1000:`**。同时提供 captcha.run 兼容的异步任务协议与
`geetest_v3` 兼容层，旧调用方无需改动。

### Added
- **`slidex/px/` 新包**：
  - `models.py`：`PxPressRequest` / `PxPressResult` / `PxRequestError`。payload 支持
    camelCase 与 snake_case；`appId` 必填，press 需 `challengeUrl`、silent 需
    `websiteURL`；国家→时区/locale 映射（未知回退美东/en-US）；timeout 夹取
    10–150s、poll 0.25–10s；`proxy` 规范化为 `{host,port,login,password}` 并派生
    Playwright context proxy；错误 kind 区分可重试与确定性失败。
  - `cookies.py`：四件套抽取（`_` 前缀/大小写归一化、忽略 `pxhd/pxcts_h/px2`）、
    `is_pass_token`（`:1000:`）、`redact_token`（只留前缀+长度）、
    `parse_packed_token`（JSON / base64 JSON / querystring / `~~~` 分段，绝不抛）、
    `looks_blocked`（403/429 与 PX 拦截文案）。
  - `solver.py`：`PxPressSolver`（共享 Chromium 农场 + 每任务独立 context，
    context 级 proxy，UA/时区/locale 对齐，反自动化探针注入，cookie 双域注入）；
    通过级 token 立即返回，非通过级给 10s 宽限后判 `px_token_blocked`；
    首屏 403/429/404 直接 fail-fast；并发信号量 + 排队超时返回 `solver_busy`。
- **`slidex/service/` 打码站**（`slidex[server]`）：
  - `POST /v1/solve`：同步接口，`{"type": ...}` → `{"code","msg","data"}`；PX 的
    `data` 即四件套。
  - `POST /v2/tasks/` + `GET /v2/tasks/{id}`：captcha.run 兼容协议，`taskId` 幂等，
    内存 TTL 30 分钟，完成即弃。
  - `GET /health`：`{ok, active_tasks, done_24h, fail_24h, types}`。
  - 鉴权：`Authorization: Bearer` 或 `X-Api-Key`，key 读 `SLIDEX_CAPTCHA_KEYS` /
    `CAPTCHA_RUN_KEY`；两者皆空时开放模式并打警告。
  - handler 注册表支持类型别名（`press` / `PxCaptcha2` / `px_silent` 等），
    `geetest_v3` 转调 `SliderSolver` 并透传 proxy。
- **配置**：`SLIDEX_PX_CONCURRENCY`（默认 5）、`SLIDEX_PX_HEADLESS`（默认 1）。
- **再导出**：`slidex` 顶层新增 `PxPressSolver` / `PxPressRequest` / `PxPressResult`
  / `is_pass_token` / `extract_px_tokens` 等。
- **root 部署适配**：`PxPressSolver` 在 root 下自动补 `--no-sandbox`（Chromium 以
  root 运行否则拒绝启动）；非 root 保持沙箱。

### Fixed
- **`aliyun-captcha-js` 选择器兼容新 DOM**（2026-11-04 chat.z.ai 实测）：图片元素
  id 从包裹 div 挪到 `<img>` 自身（`img#aliyunCaptcha-img` /
  `img#aliyunCaptcha-puzzle`），旧选择器 `#aliyunCaptcha-img img` 命不中导致
  `extract_images` 拿 None；弹窗 id 同时兼容 `-window-popup` 与 `-window-float`。
  选择器改为并集，两种形态都命中。

### Fixed（线上首日 review 实测发现）
- **silent 模式推断被打死**：`captchaType=PxCaptcha2` 在 `validate()` 里被无条件
  判成 press，导致「调用方 press 失败后走 silent 回退」的请求（只有 `websiteURL`，
  没有 `challengeUrl`）直接 422 `challengeUrl is required for press tasks`。改为只
  认 `silent/px_silent` 别名为 silent，其余交给 `from_payload` 按 URL 推断
  （有 `challengeUrl` → press，否则有 `websiteURL` → silent）。
- **`/health` 计数只算 v2 任务**：`/v1/solve` 的成败从不进 24h 计数，线上
  `fail_24h` 恒 0（实测同步链路已连挂 3 次仍报 0），运维看板会漏掉同步调用方的
  全部失败。`TaskStore.record_outcome()` 现在同时被 v1 路由调用。
- **Chromium 崩了不会自愈**：农场缓存 `_browser` 后只判 `is not None`，进程被
  OOM 杀掉（1.9G 小机器上很现实）后每个任务都挂在 `TargetClosed`，只能重启服务。
  现在复用前检查 `is_connected()`，断连即重置重拉；`new_context` 因断连失败时
  重置农场（仅在确认真断连时，避免参数错误连坐杀其它在跑任务的 context）。
- **启动失败泄漏 playwright driver**：`launch()` 抛错时 `_pw` 已赋值但没关，
  每次重试叠一个 node 子进程。现在失败路径统一 `_close_browser()`。
- **失败响应缺 `error` 键**：需求单 §3.1 要求创建失败是 4xx/5xx + `{"error": "..."}`，
  实际只回了 `{code,msg,data}`。信封现在额外带 `error`（= `msg`），v2 侧与
  captcha.run 形状对齐，对 v1 调用方是纯增量、不影响既有解析。

### Notes
- **同 IP 铁律**：payload 带 `proxy` 时，浏览器 context 与全部出站请求共用该
  sticky proxy；出口不一致 token 即作废。空 proxy 仅限本机调试。
- **不落盘**：任务只在内存，日志只打印 token 前缀与长度，不输出完整 cookie/token。
- **成功判据**：只认 `px3` 含 `:1000:`；非通过级 token 不冒充成功。
- **能力边界**：只做协议/浏览器层，不逆向 PX 的 wasm/JS 指纹算法，不做 IP 信誉
  对抗；未在真实 signup.live.com 做回归（仅本地 Chromium 仿真）。
- 测试 +128：请求模型 34、cookie 逻辑 33、求解器（假 context）22、服务路由 36、
  本地 Chromium 仿真 3（本地 HTTP 页写 cookie，验证真实链路；浏览器不可用时跳过）。
  全量 960 passed / 18 skipped（基线 832 + 128，零回归；skipped 为 automation_core
  未装时的 kit 集成用例）。
- **线上部署**（gz-cvm，`10.144.144.8:8077`，仅网格监听，systemd
  `slidex-captcha.service`，`MemoryMax=1200M`，`SLIDEX_PX_CONCURRENCY=20`）：
  17 项线上验收全绿（health/鉴权/两套协议/合成页 `:1000:`/低分拦截/403 fail-fast/
  proxy 真的生效（死代理必失败）/日志脱敏/无落盘/TTL/并发上限与真并行）。
  并发上限按 §6「起步 ≥5，target 20」逐级实测：3→5→8→16→20 并发慢页，
  20 并发时 wall 8.0s（串行会 >120s）、cgroup 峰值内存 490MB，故取 20；
  1.9G 小机器上不再靠 3 槽臆测，`MemoryMax=1200M` 仍是兜底 OOM 边界。

## [0.7.1] - 2026-11-07

新增 hCaptcha 兼容（参考 QIN2DIM/hcaptcha-challenger 的**协议形状**，clean-room
重写）：作为 `ChallengeType.HCAPTCHA` 由 `VisualChallengeSolver` 分派，覆盖
9 宫格二选 / 区域点选 / 拖放三型，识别复用既有 `SLIDEX_VISION_*` 多模态通道。

### Added
- **`slidex/vision/hcaptcha.py` 新模块**（两段式解耦，与 `point_select` 同构）：
  - 纯规划器/解析器：`plan_grid_clicks`（9 真值 → 格子中心，全空兜底点一格）、
    `plan_drag_gesture`（归一化起止点 → 二次贝塞尔点列，起止缓动 + 确定性微抖动）、
    `parse_grid_answer` / `parse_points_answer` / `parse_drag_answer`（容错 JSON /
    markdown fence / 裸文本，1-based 与 0-based 二义按「是否出现 0」判定）。
  - `LlmHcaptchaEngine`：把三型题面翻译成固定英文键名的 JSON 协议，只依赖
    `LlmVisionExtractor.ask`，可整体替换（本地 ONNX / 其它多模态）。
  - `HcaptchaExecutor`：查找 checkbox/challenge iframe → 拟人点击 → 截图 →
    问引擎 → 点格子/点坐标/拖拽 → 提交 → 读 `/checkcaptcha/` 判决或
    `h-captcha-response` token。
- **`ChallengeType.HCAPTCHA`**（`slidex/vision/models.py`），`VisualChallengeRequest`
  新增 `challenge_iframe` / `question_language` / `hcaptcha` 三个可选字段。
- **`LlmVisionExtractor.ask`**（`slidex/ocr/llm.py`）：通用「图 + 提示词 → 原始
  回答」方法，复用同一条传输/截断识别/网关重试管道；不改动既有
  `read_chars` / `locate_targets` 签名。
- **配置项**（`SlidexConfig`，均支持 env）：`hcaptcha_max_rounds`
  （`SLIDEX_HCAPTCHA_MAX_ROUNDS`，默认 3）、`hcaptcha_tile_padding_px`
  （`SLIDEX_HCAPTCHA_TILE_PADDING`，默认 3.0）、`hcaptcha_label_prompt`
  （`SLIDEX_HCAPTCHA_PROMPT`，留空用内置提示词）。
- **再导出**：`slidex.vision` / `slidex` 顶层新增执行器、引擎、规划器与解析器。

### Notes
- **重试纪律**：提交后不重试。`hcaptcha_failed` / `hcaptcha_verdict_pending`
  一律 `retryable=False`（同一题重复提交只会追加判罚样本）；仅
  `hcaptcha_challenge_timeout` / `screenshot_failed`（尚未触碰页面）标为可重试。
  提交后若题面画面原地不动，执行器直接停手；只有画面刷新（进入下一 crumb）才继续。
- **GPL 边界**：`hcaptcha-challenger` 是 GPL-3.0，slidex 是 MIT。本次**未**引入其
  任何代码或提示词，仅参考公开协议（payload 字段、三型交互、判决点）。同理
  **不逆向** `hsw.js`、不解析 msgpack 密文：优先读 `/getcaptcha/` 明文 JSON，
  拿不到时截挑战画面 + 读 DOM 题面。
- **能力边界**：只覆盖视觉/答题层；通过率同时受浏览器指纹与 IP 信誉影响，
  后者属于 stealth 层，不在本能力职责内。未在真实站点做回归。
- 不改变 `ProviderRegistry.auto_detect`：hCaptcha 不是滑块 provider，不走
  `CaptchaProvider` 的 `locate_elements → find_gap → perform_slide` 流水线。
- 测试 +67：规划器/解析器 30、引擎 10、执行器 14、solver 分派 11、本地 Chromium
  仿真 2（`page.route` 伪造 hCaptcha iframe，不碰真实站点；浏览器不可用时跳过）。
  其余用例全部假页面 / 假引擎，零网络。全量 832 passed / 18 skipped。

## [0.6.45] - 2026-11-02

`AliyunJigsawMatcher` 性能向量化与鲁棒性收口，行为逐值等价（6 场景锚点回归）。

### Changed
- **缺口检测向量化（2.4×）**：`find_gap_contour_contrast` 的逐 dx Python 循环（245 次 × 多次花式索引/临时数组）收敛为一次 2D gather（`_shifted_mean`：点集沿 x 平移 → 掩码有效计数 → 一次求和）。实测 18.6 → 7.9 ms/call；以 6 个合成场景（imprint 20/80/150/220/242 + 无压痕）建立优化前输出锚点，优化后 gap 逐个相等、conf 一致到 1e-6、gated/bx0 逐值相同。

### Fixed
- **拼图条高于背景不再 IndexError**：原实现对 `fy >= h` 的点直接索引越界崩溃；`_shifted_mean` 将 y 越界点一次性剔除（对合法素材 ph == h 无行为差异）。

### Notes
- 尝试并回退：内容信号（掩码 SQDIFF，全函数热点 7.4ms/占 40%）改半分辨率（INTER_AREA）可降至 ~2ms，但半分辨率模板混合形状边界、与全分辨率压痕失配，内容信号反投错误峰（imprint_220 偏 43px）——检测正确性优先，已回退并在代码注释留档，防止未来再踩。CCORR 三项分解实测无收益（7.5ms ≈ masked TM）。
- 测试 +1：`test_piece_taller_than_bg_no_crash`（异常素材不崩溃）。全量 758 passed / 9 skipped。

## [0.6.44] - 2026-11-02

0.6.43 code review 的根因修复：两个 P2（observer 生命周期、全不透明 alpha 误判）与全部 P3（手势一致性、acquire 异常防护、双引擎漂移钩子、文档数字）。

### Fixed
- **observer 生命周期绑定"当前 DOM"（P2）**：0.6.43 的 `_OBSERVER_JS` 用粘性 installed 标志，而 SDK 每轮重试都销毁重建弹窗 DOM（verifyRefresh / `_ARM_JS`），observer 会指向脱离文档的旧节点——重试路径的瞬态文案判定静默失效，真实失败被降级为超时（code -1），污染手势会话的换家族重采样语义；`acquire_verify_param` 的 real 路径则从未装过 observer。现以"正在观察的节点"为幂等键（`__slidexCaptchaObsFor`/`__slidexCaptchaObsInst`）：节点没变直接返回，变了断开旧的、重挂新的；`acquire_verify_param` 在每轮 ARM 成功后显式调用 `_ensure_observer`。
- **全不透明 alpha 守卫（P2）**：截图兜底（playwright 元素截图默认不透明）会产出 alpha 全 255 的拼图条，mask 退化为整条矩形，三信号检测器在背景纹理上给出"高置信"的错误峰而非检测失败（比失败更糟：伪装成功）。`AliyunJigsawMatcher` 加守卫 `mask.mean() > 0.9 → (None, 0.0, {reason: opaque_piece})`，provider `find_gap` 如设计意图回落经典模板匹配。
- **`_drag_real` 手势对齐成功样本（P3）**：过冲默认关闭（端到端唯一成功样本是纯前进无回拉；带过冲的多次尝试均 F001，风险未定案前不引入未证变量），增加 `overshoot: bool = False`；mouseup 后回读滑块 computed left 返回 `{target, sl}`——轨道被禁用/元素被重建时的"静默拖空"只有回读能发现。
- **`acquire_verify_param` 异常防护（P3）**：单轮内任何异常（SDK 结构变化 / mouse 不可用）吸收为该轮失败并继续重试，不再穿出循环丢失 `attempts`/`fail_code`（`CancelledError` 照常上抛）；ARM 返回缺键判为 `ARM_SHAPE` 而非 KeyError。
- **双引擎漂移钩子（P3）**：`perform_slide_synthetic` 新增 `points` 点表参数（`[[dx, dy, delay_ms], ...]` 相对按下点）——传入时 JS 退化为按点回放，点表可由 `_trajectory.generate_trajectory` 生成，两条手势路径共享同一套 Python 生成逻辑；缺省内置拟人节奏不变（对齐成功样本）。

### Changed
- **`acquire_verify_param` 新增 `deadline_s` 整体预算**：超时后不再开新尝试（进行中的尝试不中断），`fail_code="DEADLINE"`；docstring 写明仅主文档（arm/拖拽不进 iframe）与 fail_code 枚举。
- CHANGELOG 0.6.43 实测数字订正为最终运行值（745 passed / 9 skipped）。

### Notes
- 测试 +12（29 总）：全不透明守卫（数组/bytes/路由到经典匹配含 −piece_w//2 语义锁死/带 alpha 不误伤）、`_knob_target` 轨道夹紧（g(260)=260 两端点固定，用 L=280 才触夹紧）、acquire 决策循环（FakePage 按 JS 内容分发：首轮成功/ARM_SHAPE 消耗轮次/arm 异常吸收/拖拽异常续跑/预算拦在首轮前/预算拦在轮间/上游 F 码透传）、`_OBSERVER_JS`/`_SYNTHETIC_DRAG_JS` token 锁定扩展（ObsFor/ObsInst/disconnect/points）。全量 757 passed / 9 skipped。

## [0.6.43] - 2026-11-02

新增新一代 AliyunCaptcha.js 2.0 弹窗拼图 provider（z.ai 系登录实测驱动，本地真 Chromium 端到端验证），含四项可复用资产：二次几何定律、三信号共识检测器、一站式取参 API、合成事件拟人手势。

### Added
- **`slidex/providers/aliyun_captcha_js.py` 新 provider `aliyun-captcha-js`**：面向 `AliyunCaptcha.js` SDK 弹窗拼图（DOM 前缀 `aliyunCaptcha-*`，成功产物为回调里的 `captchaVerifyParam`），与经典 AWSC nc 滑块（`nc_1_*` DOM / `/slide` 校验包）是两个不同产品，互不误检。`acquire_verify_param()` 一站式 API：武装全新控件（清旧 DOM → 注入 holder/button → initAliyunCaptcha → show）→ 提图 → 缺口检测 → 二次反解 → 拖拽 → param/fail 捕获 → 失败自动重试（每轮重新武装重新定位）。mixin 通用链路同样接入：`validate_response` 尽力解析 aliyuncs 校验包，`get_result` 三通道判定（observer / 校验包 / 文案条，iframe 感知）；`perform_slide_synthetic` 页面内合成 PointerEvent/MouseEvent 兜底（供只放行 evaluate 的嵌入式 webview 宿主）；`wait_for_verify_param` 供 API 登录类集成读取 `window.__slidexCaptchaParam`。
- **`slidex/_slide_geometry.py` 二次几何定律**：AliyunCaptcha.js 2.0 拼图块 CSS left 不是滑块位移的 1:1 投影，而是 `piece_left = A·knob² + B·knob`（zchat.z.ai 2026-11-02 两次独立标定一致：g(200)=157.396，并经 5 组观测 + 一次成功验证 knob=235→214.142 复核）。新增 `aliyun_jigsaw_piece_left` / `aliyun_jigsaw_knob_for_piece` 正反解——缺口位置换算滑块行程必须走反函数，1:1 拖必差 60px+。
- **`slidex/_image_match.py` 新增 `AliyunJigsawMatcher`**：轮廓-边缘 × 填充-对比度 × 内容一致 三信号共识检测器。带 alpha 的整条拼图素材（52×200）上模板匹配会出扁平/伪峰（实测原始亮度均值被照片亮区污染，候选区面积只有真实缺口的 1/4）。信号 1：拼图块 alpha 轮廓沿 x 平移对 Sobel 幅值积分（形状对齐时孤立尖峰 174 vs 次高 111）；信号 2：缺口内部亮度减外环的局部对比度（对"照片本身亮"鲁棒）；信号 3：掩码 SQDIFF 内容相似度（区分度门控——真实生成是"压痕叠白"而非纹理拷贝，信号常平坦，平坦时归一化即纯噪声，只在存在真实凹点时纳入）。置信度取峰-中位差，<0.2 判失败回落经典模板匹配（补 −piece_w//2 中心→左缘语义修正）。

### Fixed（review 发现）
- **轨迹重定标死区陷阱（致命）**：`scale_recorded_points` 默认 tolerance=0.10，而缺口 bg px 与二次反解行程的相对差常落在 10% 以内（z.ai 典型 213→235 = 9.4%）——带死区的缩放器静默跳过，终点差 39px 必挂 F015。provider 改用 `tolerance=0` 显式重定标（`_rescale_points`），并以 `self._last_gap_x` 保留 find_gap 的 float 精度（mixin 会把 travel 截成 int）。
- **瞬态成功文案丢失**：成功后弹窗随即关闭，事后轮询什么都读不到。新增 `_OBSERVER_JS`（MutationObserver）在拖拽**前**挂好，把文案条变化捕获进 `window.__slidexCaptchaVerdict`。

### Notes
- 运行时红线写入 provider docstring（同一批实测教训）：控件会话分钟级过期（陈旧弹窗拖准也 F014/Timed out；fresh 15-30s 正常——尽快拖）；失败（F015 位置 / F001 风控）会换 cert 并重置/关闭弹窗，重试前必须重新武装、重新定位；拖动中途不可刷新/中断；判定发生在 mouseup，释放前要终点 dwell。
- **F001 是环境/风险判定，不是位置错误**：本地 playwright Chromium 里两次拖拽落点与模型分毫不差（g(212)=175.872、g(234)=212.4）且 edge/contrast 双信号互相印证（174-177、207-212 聚类），仍 F001；同一浏览器里更早的成功尝试证明位置正确时可过。合成手势过冲默认关闭——端到端唯一成功样本是纯前进无回拉手势。
- 几何换算链内化到 provider：背景 296px 原图拉伸到 300 CSS 宽（k≈1.0135）而拼图条 52px 1:1，`imprint_x·k − bx0·piece_k` 先换 CSS 再二次反解，行程上限按 `track − knob`（实测 260）夹紧。
- 新增 `tests/test_aliyun_captcha_js.py` +17（确定性 RNG fixture）：几何定律（标定点/反解往返/单调性/正根唯一）、三信号检测器（合成压痕精确定位 ±2px / 无压痕低置信 / bytes 带 alpha 与无 alpha 拒绝）、**重定标死区回归**（213→235 陷阱复现与修复）、换算链端到端（bg px 213 → knob 235 实测样本回放 + float 精度）、注册表、三段页内 JS 的规则 token 锁定。全量 745 passed / 9 skipped。

## [0.6.42] - 2026-10-19

代码评审收口：容错解析收敛为 `_env` 单一契约，超时降级判定收敛为 `_nav` 单一谓词，并堵上两处评审发现的退化值漏洞。

### Fixed
- **`config._safe_float` 会放行 `inf`**：旧实现 `float("inf") > 0` 为真，`SLIDEX_VISION_TIMEOUT=inf` 会得到一个无限大的超时（挂死风险）。委托 `_env.parse_float(exclusive_minimum=0.0)` 后非有限值一律回落默认。`_safe_int` 委托 `parse_int` 后与 `_env` 同契约（`"5000.0"` 这类浮点写法不再判坏值）。
- **`SLIDEX_SOLVE_WATCHDOG` 无上限**：`1e9` 这类值等于把看门狗事实关闭——而看门狗的存在理由正是生产曾在死驱动连接上挂死 16h+。加 `maximum=3600.0`（内部各段预算之和约 420s，8 倍余量），越界回落 600s。
- **`XY_VERIFICATION_WAIT_TIMEOUT` 的裸 try/except 收口到 `env_int`**：保留原 clamp 语义（`max(5, v)`——`"3"` 钳到 5 而不是回落 450；只有未设/非法才回落 450），`"5.0"` 这类浮点写法顺带获得容忍。

### Changed
- **`slidex/_env.py` 新增 `parse_float` / `parse_int` 原语**（空串视同未设置、静默回落）：`config._safe_int` / `_safe_float` 与 solver 的 `_cdp_connect_timeout_s` / `_cdp_probe_timeout_s`（新增 `exclusive_minimum` 开下限，表达「非正数回落」）全部委托到 `_env` —— 全库容错解析契约单一来源，消除「同形 env 值在不同入口行为不同」。
- **新增 `slidex/_nav.py` 的 `is_nav_timeout` 谓词**：`solver._goto_page`、`stealth._goto_login_page`、`stealth._reload_with_fallback` 三处降级点共享同一判定，消除判定标准漂移隐患（stealth 两处此前缺 `TimeoutError` 限定名兜底判据，实际异常无差别）。

### Notes
- 测试 +22：`TestParsePrimitives` / `TestConfigSafeDelegation`（`_safe_float` 拒 `inf`、`_safe_int` 容忍 `"5000.0"`）、`test_nav_timeout.py`（谓词只看类型不看消息）、`test_verification_wait_timeout.py`（clamp 语义锁死：`"3"`→5、`"-10"`→5、非法→450）、`solve_on_page` 非 Timeout 导航错误判负路径端到端、watchdog `1e9` 上限子进程用例。本机 731 passed / 12 skipped。

## [0.6.41] - 2026-10-19

健壮性收口：坏 env 不再能让 `import slidex` 崩，剩下两处 `networkidle` 假超时也不再吞掉整次求解。

### Fixed
- **类体（import 期）裸 `float()` 读 env 会让整个库不可用**（`SliderSolver.SOLVE_WATCHDOG_TIMEOUT_S` / `MANUAL_VOUCHER_WAIT_S`）：这两个常量在类定义时求值，`SLIDEX_SOLVE_WATCHDOG=abc` 会让 `import slidex.solver` 直接抛 `ValueError`，所有依赖 slidex 的进程在启动阶段就死。改为容错解析（非法/非有限/越界回落默认并告警）。`MANUAL_VOUCHER_WAIT` 仍允许 `0`（该值的语义就是「不等」），`SOLVE_WATCHDOG` 下限 1s。
- **`solve_on_page` 的 page_url 分支被 `networkidle` 假超时打成失败**（`solver.py`）：原实现是裸 `page.goto(..., wait_until="networkidle", timeout=45000)`，Timeout 落到 `solve_on_page` 的兜底 `except` 后直接 `return False, None` —— 处罚页信标让 `networkidle` 永不静默时，一次本可继续的求解整体判负。改为走既有 `self._goto_page(...)`（同文件 `_load_page` 早就在用）：仅 Timeout 降级 `domcontentloaded` 继续，页面是否真的可用交给后续 page_state 探测。
- **滑块重置回退分支的 `page.reload(wait_until='networkidle', 15000)` 假失败**（`stealth.py`）：同样受处罚页信标影响，导航其实已发起却被判失败，白丢一次 reset 收尾的稳定等待，并在滑块最常出现的页面上持续刷假告警。新增 `_reload_with_fallback` 静态方法：仅 Timeout 降级，其余错误照抛（保留调用点原有兜底）。
- **`slide_end_hold_range()` 与两处 `int(os.environ...)` 的裸转换**：坏值（`"abc"`、`"inf"`、`"5000.0"`）此前会抛异常或错误解析。统一到新模块 `slidex/_env.py` 的 `env_float` / `env_int`，与 `config.py` 的 `_safe_int` / `_safe_float` 同约定；`env_int` 额外容忍 `"5000.0"` 这类浮点写法并接住 `OverflowError`（`"inf"`）。

### Notes
- 新增 `slidex/_env.py`：容错解析的唯一实现，带闭区间 `minimum` / `maximum`，告警走 loguru。`_trajectory.slide_end_hold_range()` 保持原有 `lo <= hi` 不变量。
- 测试 +43：`test_env_parsing.py`（单元层坏值矩阵 + **子进程**验证坏 env 下 import 主要模块成功且常量取默认值）、`test_solve_page_nav.py`（含「假超时后 `_run_solve_loop` 仍被调用」的端到端回归）、`test_reload_fallback.py`。本机 711 passed / 12 skipped。

## [0.6.40] - 2026-10-19

密码登录页导航不再被 `networkidle` 假超时打断 —— 新增 `_goto_login_page` 降级策略，与 solver 侧既有做法对齐。

### Fixed
- **密码登录 `page.goto(login_url, wait_until='networkidle', timeout=60000)` 假超时**（`XianyuSliderStealth.login_with_password_playwright`）：闲鱼/淘宝的处罚页会持续跑后台信标，`networkidle`（连续 500ms 无网络连接）**永不达成**，60s 后必抛 `Page.goto: Timeout 60000ms exceeded` —— 而页面其实早已可用。历史实现把这个 Timeout 直接上抛，整条密码登录链路当场中断；上游（xianyu-auto-bot）的「令牌/Session 过期 → 密码登录」恢复因此失败并触发 init-auth 失败循环（生产 2026-10-19 03:01 现场）。新逻辑与 `solver._goto_page` 同策略：**仅当错误类型名含 Timeout 时降级 `domcontentloaded`（30s）继续，其余错误照抛**；连接类错误（`ERR_CONNECTION_CLOSED` / `RESET` / `REFUSED`）仍按原逻辑 3 次重试。登录表单是否存在交由后续 `_find_login_form_with_retry` 判定 —— **导航等待不是成败判据**。

### Notes
- 抽取为 `XianyuSliderStealth._goto_login_page(page, login_url, pure_user_id)` 静态方法（返回实际采用的 `wait_until`），便于单测。降级导航本身再失败也只告警不上抛：页面可能已部分可用，交由后续表单探测。
- 测试 +5（首试成功 / 超时降级 / 降级再失败软放行 / 连接错误重试 / 非超时非连接错误照抛）。本机 668 passed / 12 skipped。

## [0.6.39] - 2026-10-19

跟随 automation-kit `0.5.0` 的发布，把 `automation-kit` extra 的下限提到 `0.5.0` —— 旧上限 `<0.5.0` 会把已发布的 kit `0.5.0` 判成不兼容。

### Changed
- **`automation-kit` extra 依赖范围 `>=0.4.0,<0.5.0` → `>=0.5.0,<0.6.0`**：kit `0.5.0` 已于 2026-10-18 发布（tag `v0.5.0`，`main` 快进到 `a29bb82`，CI 全绿）。跨仓顺序是 automation-kit → Slidex → app；两个 app 仓的 `slidex-contract` 早已声明 `>=0.5.0,<0.6.0` 并对着上游 `main` 校验，本次补上中间这一环。
- **缺依赖提示与 extra 对齐**：`slidex/integrations/automation_kit.py` 的 `ImportError` 文案原写 `automation-kit>=0.3.0`，比 extra 声明的下限还旧两档（`>=0.3.0` / `>=0.4.0` 两代都没跟上），现在两边一致写 `>=0.5.0`。

### Notes
- 纯元数据变更：没有任何代码路径、算法或运行时行为发生变化，测试计数与 0.6.38 相同（本机 663 passed / 12 skipped；12 skip 是需要 sibling 检出 `automation-kit` 的契约测试，CI 里会真跑）。
- `automation-kit` 仍不在包源上，所以 `pip install 'slidex[automation-kit]'` 在任何环境都还没法用（找不到 `automation-kit` 这个发行版，不是范围问题）。本仓 CI 走 sibling 检出 + `-e` 安装，不吃这个范围，契约测试照跑；kit 侧已补发布门禁（构建 → 安装 → 安装件自证，打 tag 触发 `publish.yml`），trusted publisher 配好后即可上架，届时本 extra 无需再改。

## [0.6.38] - 2026-10-18

冻结探针的预算从 2s 提到 6s，并开放 `SLIDEX_CDP_PROBE_TIMEOUT` 覆盖 —— 0.6.37 上线后在生产环境实测发现 2s 比这条链路还紧，探针静默失效。

### Fixed
- **探针默认预算 2.0s → 6.0s**：0.6.37 的 2s 是按局域网想的（活标签毫秒级应答）。生产拓扑是容器 → 反向隧道 → 用户 PC 的 Chrome，**实测活标签的 `connect + Runtime.evaluate` 往返 2.05~2.12s**，比预算还长：六个 page 目标全部超时 → 三态判成 `unknown` → 安全阀按设计把整次探测降级为「无法判定」→ 自愈什么都不做，功能等于永不生效（还白花 2s）。6s 给这条链路约 3 倍余量；8 路并发下即使所有标签都冻住，整体也只多等 6s，相对一次 45s 的握手超时仍然划算。
- **新增 `SLIDEX_CDP_PROBE_TIMEOUT`**：按实际链路调预算，不必为改一个常数发版。解析与 `SLIDEX_CDP_CONNECT_TIMEOUT` 同样容错 —— 空值 / 非数字 / 非正数一律回落默认并告警，不抛 `ValueError`。

### Notes
- 顺带印证了 0.6.37 安全阀的价值：探针在这台生产机上**确实失灵了**（全部 `unknown`），而它没有退化成「把所有标签切一遍前台」，只是安静地什么都不做。失灵是可见的（一条 WARNING + `would_heal_count: 0`），不是静默误伤。
- 生产实测数据来自容器内**只读**探针（只发一条 `Runtime.evaluate("1")`，不 activate / 不 close / 不 rebuild）：`/json` 可达、6 个 page 目标、`_cdp_http_base` 推出 `http://172.19.0.1:9222` 正确。
- 测试 +3（env 解析的六种坏值 + 「默认预算必须明显大于实测隧道延迟」的回归锁 + env 预算真的落到 `websockets.connect`）。本机 663 passed / 12 skipped，CI 双矩阵 675 passed / 0 skipped。

## [0.6.37] - 2026-10-18

连接前冻结标签探针 + 可选重建自愈：不再非要等一个完整握手超时（默认 45s）才发现外部 Chrome 有被内存节省冻结的标签，而是 connect **之前**先用裸 WebSocket 逐个探活，只对**真冻**的标签切前台解冻。

### Added
- **连接前冻结探针**（`SLIDEX_CDP_PROBE_FROZEN=1` 开启，默认关闭）：`_probe_cdp_target_alive` 对每个 page 目标的 `webSocketDebuggerUrl` 发一条 `Runtime.evaluate`，以「**收到应答**」而非「WS 连得上」判活——被冻结的标签 WS 握手常由浏览器进程代答成功，但渲染进程不响应 RPC，所以连得上不等于活着。单探 2s、8 路并发，即使全冻也只需 2s 上下，远小于一次握手超时（**0.6.38 起预算提到 6s 并可用 `SLIDEX_CDP_PROBE_TIMEOUT` 覆盖 —— 2s 在生产反向隧道上比活标签的实测往返 2.1s 还紧，见上**）。
- **探针三态 + 安全阀**：`_probe_cdp_target_state` 返回 `alive` / `frozen` / `unknown`，把「连不上」与「连得上但不答」分开——后者才是冻结标签的签名。若**所有**目标都停在「连不上」，说明问题在探针这一侧（容器里的 `websockets` 版本不兼容这套调用、端点被中间设备拦掉），一律判「无法判定」并跳过自愈：宁可不做，也不能把「探针坏掉」翻译成「所有标签都冻了」而把用户每个 tab 都切一遍前台。探针可用时，连不上的目标仍会参与**解冻**（它同样会挂住握手），但**不参与重建**：一个刚被用户关掉的标签不该被我们重新打开成多余的新 tab。
- **只解冻确证冻结的标签**（`_self_heal_frozen_cdp_tabs`）：探针判负的标签才 `/json/activate`，活标签绝不被切到前台——这是相对 0.6.32 兜底唤醒「激活全部 page」的收敛。
- **可选重建仍冻结的标签**（`SLIDEX_CDP_REBUILD_FROZEN=1`，默认关闭，有损）：激活解冻异步生效，留 `_REBUILD_SETTLE_S` 复探一次，仍无响应的才 `PUT /json/new?<原URL>`（先开后关，避免丢 URL）重建；URL 整段 `quote`，否则其中的 `&` / `#` 会被当成参数分隔符截断。
- **前置自愈与 0.6.32 兜底的分工**：探针路径互斥优先于 `SLIDEX_CDP_PRE_ACTIVATE`（它是"少切几个 tab"的超集）；探针路径任何一步失败只记日志不抛出，退回照常握手——自愈是纯优化，绝不能因自愈本身把连接搞挂。
- 测试 +29（探针判活 / 三态判定 / 并发探冻 / 重建顺序与 quote / 降级路径 / 安全阀 / env 门控 / 连接编排 / 真 websockets 服务端对照）。660 绿 / 12 skip。`websockets` 仍是 remote extra 的可选依赖，未安装时探针自动降级为空操作，不误判全冻。

### Fixed
- **裸 `host:port` 端点的 `/json` 基址不再依赖 `urlparse` 的版本行为**：`urlparse("127.0.0.1:9222")` 在 3.10 上把 `127.0.0.1` 当成 scheme、只剩 `9222` 落进 path，于是 `_cdp_http_base` 推出 `http://9222`，`/json` 与 `/json/activate` 全部不可达；3.11+ 行为又不同（整串落进 path）。原写法 `parsed.netloc or parsed.path` 恰好踩在这个差异上。现在无 scheme 时直接对整串取 `split("/", 1)[0]`，按构造与版本无关。**这个 bug 是 CI 的 3.10 矩阵抓出来的**——本机只有 3.12，跑不出这个差异。

## [0.6.36] - 2026-10-17

CDP 唤醒兜底根治（**补记**：代码随 0.6.34 进入 main，本节此前误置于 `[Unreleased]`）：不再依赖 Chrome `/json` 里并不存在的 `active` 字段。

### Fixed
- **`_wake_cdp_targets` 删掉基于 `target["active"]` 的「跳过当前标签 + 结束还原焦点」**：Chrome 的 `/json` 序列化器（`content/browser/devtools/devtools_http_handler.cc` 的 `DevToolsHttpHandler::SerializeDescriptor`）只输出 `id` / `parentId` / `type` / `title` / `description` / `url` / `faviconUrl` / `webSocketDebuggerUrl` / `devtoolsFrontendUrl`，**没有 `active` 字段**。该判断恒为假 — 既跳不过当前标签，`restore_focus` 分支也永不执行，是死代码而非优化。现在如实写进 docstring：兜底唤醒必然把用户前台 tab 切到某个被激活的页上。要根治切前台请在用户 PC 侧用启动参数关掉内存节省（见主仓 `scripts/cdp_tunnel.ps1`）。
- **唤醒一个目标都没成功时不再重试握手**：首次 `connect_over_cdp` 失败后先兜底唤醒，只有 `woken > 0` 才再等第二轮超时，否则说明失败与冻结标签无关（端点不可达 / 浏览器里没有 page 标签），直接上抛，少白等一个完整握手超时（默认 45s）。
- **`SLIDEX_CDP_CONNECT_TIMEOUT` 容错**：空值 / 非数字 / 非正数一律回落默认 45s，不再抛 `ValueError`。旧写法 `float(os.environ.get("SLIDEX_CDP_CONNECT_TIMEOUT", "45"))` 在 env 存在但为空字符串时直接炸，而空值恰恰是 compose 里最常见的配置形态。
- **唤醒预算常量化并对齐实现**：`_WAKE_REQUEST_TIMEOUT_S=1.0` / `_WAKE_MAX_WORKERS=4` / `_WAKE_TOTAL_BUDGET_S=3.0`。原 docstring 声称「单请求 1s，整体 2s」，与实现（`fut.result(timeout=2.0)` × N）不符。
- 删除已无意义的 `restore_focus` 形参（两个调用点都传 `True`，且还原分支不可能执行）。

人工控制页适配 0.6.34/0.6.35 新增的 `click` 与 `text_input` 事件。后端在 0.6.34/0.6.35 就已支持这两类事件，但控制页从不发它们——**面板永远只能用鼠标拖拽**。本版把最后一公里补上。

### Added
- **控制页按 `challenge_type` 自动切换形态**（`applyChallengeType`）：收到 `session_info` 后据其 `challenge_type` 改写使用说明文案，并按需显示字符输入面板。这是个一直存在却从未被读的字段：后端 `slidex/api.py` 早就在下发它，前端却没有任何消费者，所以面板永远按滑块的假设渲染。
- **字符输入面板**（`#textInputPanel` / `#charsInput` / `#sendCharsBtn`）：`graph_chars` 下显示，发 `{type:'text_input', text}`；回车直接提交。提交后**不清空输入**——若后端判错并换了新图，保留原值便于对照修正。视觉沿用 `session-input` 的规格，避免同一页里两种形态的控件像来自不同产品。
- **点选形态改走单次 `click`**：`handleMouseDown` 在 `point_select` 下发一次 `event_type:'click'` 即返回，`handleMouseMove` / `handleMouseUp` 直接短路。若沿用 down/move/up，松手会被当成一次拖动结束，把「点某位置」的语义变成拖拽序列。

### Notes
- 631 绿 / 12 skip。滑块链路未触碰。
- embed 模式下仍隐藏 `.instructions`：该模式已把 header / 日志 / 状态卡一并精简，是刻意做窄的界面；形态改由可见的输入面板与「挑战类型：…」日志行点明。
- **CI 从此真跑浏览器端到端**：`.github/workflows/ci.yml` 补 `python -m playwright install --with-deps chromium`。此前 `[remote]` extra 只装 pip 包不装浏览器二进制，四个仿真测试各自捕获 launch 失败后 `pytest.skip`，CI 长期「全绿」却一行浏览器端到端都没跑过——而点选与字符图形验证码这两条新链路恰恰只由它们覆盖。实测 run 38993157405：**643 passed / 0 skipped**（3.10 与 3.12 双矩阵），此前为 631 / 12。

## [0.6.35] - 2026-10-17

字符图形验证码通用能力（`GRAPH_CHARS`）：「识别图里的几个字 → 填进输入框 → 提交」。与 0.6.34 的文字点选**共用同一个识别引擎**，但换了一套执行器——两种形态的执行动作根本不同，正好验证了 0.6.34 的两段式解耦是走对的。

形态由真实样本判定：闲鱼日志里的 `FAIL_SYS_USER_VALIDATE` + `RGV587_ERROR::SM::哎哟喂` 是阿里云 baxia，其图形挑战是**字符验证码**（实测样本 150×50、4 位大写字母彩色字符），既不是滑块也不是文字点选，是独立的第三种形态。

### Added
- **`slidex/vision/graph_captcha.py`**：**规划器**（`plan_graph_chars`，纯函数：识别文本 → 校验长度/字符集 → 可提交字符）+ **执行器**（`GraphCaptchaExecutor`：逐字键入 → 提交 → 可选读判决码）。惩罚码判定 `is_punish_graph_code` 复用滑块 300 语义。
- **`ChallengeType.GRAPH_CHARS`** 与 `VisualChallengeRequest.input_element` / `submit_element` / `expected_char_count`；`VisualChallengeSolver._solve_graph_chars` 全流程（取图→识别→规划→填入→提交→判决）。
- **`LlmVisionExtractor.read_chars()`**：字符识别走独立纯文本提示（不要坐标、不要 JSON）——实测 JSON 提示在 token 预算吃紧时内容被截在 `{"text` 就没了，纯文本直出稳定得多。长度校验**不在引擎层**（渠道各异），放规划器，那里才知道期望值。
- 本地风控模拟器 `slidex/fixtures/graph_chars_sim.html` + `tests/test_graph_captcha_sim.py`（严格比对答案；识别注入假引擎故不依赖任何视觉 API）；`tests/test_graph_captcha.py` ×28。

### Changed
- **`max_tokens` 默认 64 → 100000 并从 config 收口**（`SLIDEX_VISION_MAX_TOKENS`）。实测 GLM 系列是 reasoning 模型，会先花 `reasoning_tokens` 再给答案：`max_tokens=64` 时 reasoning 吃掉 59、正文一个字没吐；100 时 3 次里 2 次被截断。**这条阈值不能调低**。
- **网关超时（504/524）重发**（`SLIDEX_VISION_GATEWAY_RETRIES`，默认 3）。524 是 Cloudflare 等网关**等不到上游**，不是模型失败，重发通常命中已预热的连接。不做退避——单次响应本身可能几十秒，退避只会让整体超时更早到来。`vision_timeout_s` 默认同步放宽到 180s，否则重试次数还没用满就先被本地超时砍掉。
- **截断与「没认清」分开**（`_was_truncated`）：reasoning 模型预算耗尽时 `content` 为空、`finish_reason=length`，那是「没说完」不是「没认清」。编排层据此**允许重发**（同一个模型对同一张图的答案不变），而 401/403/通道未配置一律**一次停手**（重发换不来新信息，只空耗配额）。
- **重试编排**同样只包住「取图 + 识别 + 规划」，`submit_attempted` 一旦置位即停手——**提交错字符就是一次判罚**，重发同一个答案只会加深确认样本（0.6.28 纪律）。
- **字符图形验证码成功记录**：`build_graph_chars_record`（schema v1 graph_chars 子块）与点选共用 `successes.jsonl`（`graph_chars_*` 前缀）。同时落**识别原值**与**归一后提交值**——字符识别是离散的（错一字即全错），线上出现「填了 AE20 却没过」时，只有两者并列才能区分是模型读错还是归一改错。
- **人工面板事件语义扩展**（补 0.6.34 的 known gap）：`handle_mouse_event` 新增 `click`（合成一次 move+down+up，点选没有「拖」这回事，不该逼控制页拆三段）；新增 `handle_text_input` 逐字键入（字符图形验证码的动作是「填几个字」，与鼠标事件分成两个方法/消息类型，一套方法管两套语义迟早会混）。WS 新增 `text_input` 消息类型；`click` 与 `up` 同样触发 `check_completion`——否则全靠点击解决的挑战永远不会上报 `completed`。滑块路径为纯增量，`down/move/up` 语义未变。
- `pyproject` 版本 → 0.6.35；keywords 增补 graph-chars/llm-vision。

### Fixed
- **易混字符归一会把对的答案改错**（本版最危险的一个坑）。初版无条件把 `Q→0`、`Z→2`、`S→5`、`B→8`、`G→6`、`D→0`、`I→1`，而 baxia 的字符集是**字母数字混合**，`Q` 与 `0` 都是合法字符——字母本来是对的，改完必然错：实测正确答案 `AEZQ` 被这套映射改写成 `AE20`，由对变错。改为**字符集驱动**：折叠只在调用方声明受限字符集时发生（`ALPHABET_DIGITS_ONLY` 字母→数字、`ALPHABET_UPPER_ONLY` 数字→字母），混合字符集（默认 `ALPHABET_ANY`）一律不动。
- **反向折叠的歧义位**：`D→0` 而 `O`、`Q` 也 `→0`，反向一对多无解。原实现用 dict 推导让顺序悄悄决定结果，会让两个策略得出互不自洽的结论；改为显式 `_REVERSE_AMBIGUOUS`，歧义位原样保留。
- `_safe_int` 在引擎侧补上负数回退：`SlidexConfig._safe_int` 接受负值，一个手滑的 env 值（如 `SLIDEX_VISION_GATEWAY_RETRIES=-5`）会静默把重试关掉。

### Design notes
- **纯识别模式**：`input_element` 留空时只识别、不碰页面，结果停在 `recognition_only=True` 交调用方自己填。识别与执行解耦在这里比点选更有价值——多数宿主本来就有自己的填值逻辑。
- **点完不擅自判成功**：与点选同纪律，结果停在 `graph_chars_verdict_pending`，惩罚码 300 → `graph_chars_punished` 且不可重试。响应形状各家不同，slidex 不猜。
- **填入用逐字键入而非整块 `fill`**：真实用户是逐字敲的，整块填充在行为特征上与真人差异明显。
- **长度不符一律拒判**：长度不对说明漏识或多识了字，填进去必错；宁可换新图重识别（这一步在活页面是可重试的），也不提交一个已知可疑的答案。
- **重试只在「真的换了图」时才发生**：编排层每次重试都重新截图，所以活页面场景下长度不符换图重试有意义。但调用方若传的是静态 `image_bytes`/`image_path`，重试只会把同一张图再认一遍、得到同一个错误答案——这种场景 `retryable=False` 并在 metadata 标 `from_live_page=False`，把换图的责任交还调用方。宁可一次停手，也不要让「重试机制在工作」成为假象。

### Known gaps
- 真实渠道的**提交**已验证（2026-10-17，经用户授权仅对自己名下 `xianyu.mangoqwq.com` 登录页做端到端；仍遵守不在第三方生产风控上做验证）：识别 `BSLZ` → 落到「请输入用户名和密码」，对照组故意填 `XXXX` → 「验证码已过期，请刷新」，两次返回**不同的**后端错误文案，证明校验链路真实生效且该次识别正确。另一轮 `UGNL` → 「用户名或密码错误」同样通过。
- 字符图形验证码**延迟不可控**：实测同模型同图 4.4s~80.5s（reasoning 模型冷热差异极大），且偶发 HTTP 524。`vision_timeout_s` 默认已放宽到 180s，并对 504/524 做网关重试；上层 `timeout_ms` 仍需按最坏情况设置，或换非 reasoning 模型。
- ~~控制页前端本身未同步适配新事件~~：已于 2026-10-17 补齐（见 0.6.36）。

### Notes
- 测试 631 绿（12 skip，均为既有的可选依赖缺失/Windows 符号链接权限，与本版无关）；未触碰滑块链路，既有测试全绿无回归。
- 真实样本端到端验证（仅识别）：`glm-5.3-flash` → `AEZQ`，`truncated=False`（含 reasoning）。
- **`max_tokens` 必须给足**：GLM 系先吐 `reasoning_tokens` 再出正文，`max_tokens=64` 时 reasoning 吃掉 59、正文为空，`max_tokens=100` 时正文被截断 2/3。默认值已从 64 放宽到 **100000**（`vision_max_tokens` / `SLIDEX_VISION_MAX_TOKENS`）。这条不是"宽一点更保险"的调参，是正确性前提——识别结果为空会被误判成"没认出字"，进而触发无意义的重试。

## [0.6.34] - 2026-10-16

识图（文字点选）验证通用能力：把「给一句指令 → 在图里按序点出对应文字」做成引擎无关的通用链路，不绑定任何渠道。

### Added
- **`slidex/ocr/instruction.py`**：点选指令解析（`parse_instruction` / `Instruction`）。把「请依次点击【的】【了】【我】」这类提示解析为有序目标序列，剥离模板噪声、统一全角半角、限制目标数上限 8。**解析不出返回 None 而非空序列**——宁可漏认（上层重截图/降级人工）也绝不猜错，点选判错一字即整体失败。
- **`slidex/ocr/llm.py`**：多模态点选识别器（`LlmVisionExtractor`）。识别与定位由同一模型一次给出（字符粘连时字框会碎，规则匹配难兜），坐标一律 0..1 归一化返回；指令已知时校验模型返回字序列，不符即整体判为不可信（`vision_target_mismatch`）。走标准库 urllib，不给核心依赖树新增 HTTP 库。
- **`slidex/ocr/local.py`**：本地 RapidOCR(onnxruntime) 降级后端，返回全图字框。未安装时调用返回 `ocr_local_unavailable`，让降级链自然回落多模态通道，不在 import 期炸包。
- **`slidex/vision/point_select.py`**：**规划器**（`plan_click_points`，纯函数：识别结果 + 指令 → 有序归一化坐标）+ **执行器**（`PointSelectExecutor`：坐标换算 → 依序人工化点击 → 可选读判决码）。两段解耦是「通用能力」的关键——换识别引擎改第一段输入，换验证形态改第二段，下游渠道只需传一个 `VisualChallengeRequest`。
- **`ChallengeType.POINT_SELECT`** 与 `VisualChallengeRequest.target_element` / `instruction_text`；`VisualChallengeSolver._solve_point_select` 全流程（截图→解析→识别→规划→点击→判决）。
- 本地风控模拟器 `slidex/fixtures/point_select_sim.html` + `tests/test_point_select_sim.py`（只接受 isTrusted、校验点击顺序；识别注入假引擎故不依赖任何视觉 API）；`tests/test_ocr_instruction.py` / `test_point_select.py` / `test_ocr_llm_vision.py`。

### Changed
- **重试编排落地**：`VisualChallengeSolver._solve_point_select` 现在是编排层——识别不可信时**重新截图换通道**再试（默认 3 次、硬顶 5，`metadata.max_attempts` 可调），循环**只包住「截图+识别+规划」**；一旦进入点击阶段即标记 `click_attempted` 停止重试（重复点击比点错更易触发判罚），惩罚码立即停手转被动恢复。0.6.33 只到信号层、本版把纪律编进执行。
- **配置收口**：`LlmVisionConfig` 改为从 `SlidexConfig` 取值（`from_config`），消除与 config 中同名字段的重复 env 解析，改默认值只有一处事实源。
- **点选成功记录**：`build_point_select_record`（schema v1 点选子块）把目标序列、每字置信度、点击坐标、引擎通道、判决码落进共用的 `successes.jsonl`（靠 `outcome` 前缀 `point_select_*` 区分），供调识别准确率时对照。
- `pyproject` 版本 → 0.6.34；`vision` extra 增加可选 `rapidocr-onnxruntime`（选 RapidOCR 而非 PaddleOCR 以保持核心轻依赖）；keywords 增补 point-select/ocr。

### Design notes
- **点击刻意逐点 await、不复用 0.6.24 拖动流水线**：点选时间尺度是「人看图再点」的数百毫秒量级，隧道 RTT 相对可忽略；拖动流水线是为解决 25-75ms 间隔被 RTT 撕碎而设计，照搬反而把点击节奏压成机器匀速。
- **点完默认不声称成功**：各家点选渠道响应形状不同，结果停在 `point_select_verdict_pending` 交调用方判读，避免把「点完了」误报成「过了」（0.6.30 同类假成功教训）；惩罚码 300 复用滑块语义，命中即 `point_select_punished` 停手。
- **置信度闸门**：`low_confidence` / `target_char_not_found` 视为可重试的识别失败，交编排层重新截图，而非在同一猜测上连点（0.6.28 纪律）。

### Known gaps
- 点选尚未接入 `remote.py` 人工面板（面板事件语义仍是滑块专属的 down/move/up）；点选需要的是「点某位置」而非「按下-移动-抬起」。
- 真实验证码上的识别准确率未验证（遵守「禁止以线上真实验证码做验证」）；接真实多模态通道时建议先只跑识别、看 `clicks`/`notes` 遥测确认字面与坐标合理再放开点击。

### Notes
- 测试 580 绿（12 skip）；本版未触碰滑块链路，既有 461 测试全绿无回归。新增 `tests/test_point_select_retry.py` ×9锁定重试纪律（重截图、不连点、点击后不重试、惩罚码停手）。

## [0.6.33] - 2026-10-11

风控惩罚码（300 other-punish）单发停止：判决命中惩罚码即停手转被动恢复，不再重定位重拖。

### Added
- **`slidex/_slide_result.py`**：`PUNISH_SLIDE_CODES = frozenset({300})` 与 `is_punish_slide_code(code)` 判据——300 = baxia other-punish（设备/会话被判罚，与轨迹质量无关）。换手势重拖不改变判决，只会追加风控确认样本并加深惩罚。
- **`solver.last_slide_code`**：本轮 solve 内最近一次 `/slide` 判决码，三个求解入口（`_solve_impl` / `_solve_on_existing_impl` / `solve_on_page`）起始重置，供宿主区分"风控惩罚拒绝"与"本地异常/轨迹质量失败"。

### Changed
- **legacy 循环**：判决命中惩罚码时记录 `slide_punish_stop` 遥测并 `break`，不再进入下一轮"等 verifyRefresh 重建滑块再拖"。
- **provider 循环**：同上，命中惩罚码即停（`mode="provider"`），不再等 2.8~3.6s 重建挑战。
- **provider→legacy 交接**：provider 因惩罚码停止后直接走 `_fallback_or_fail` 被动恢复，跳过 legacy 全套仪式——重建的挑战属于同一惩罚周期，legacy 再等再拖只会追加确认样本。
- **`_provider_mixin.locate_elements` 异常降级**：定位抛错（挑战 iframe 被拆除后 scope 失效、新 iframe 未渲染）不再上抛堆栈，降级为 `provider_locate_failed` 遥测 + 定位失败被动路径。

## [0.6.32] - 2026-10-08

外部 Chrome 睡眠/冻结标签页主动唤醒：
- `_wake_cdp_targets`：在 Playwright 执行 `connect_over_cdp` 握手前，主动向 `/json/activate/<id>` 轮流触发激活，强制唤醒 Chrome Memory Saver 冻结或舍弃渲染进程的后台标签页，杜绝 `connect_over_cdp` 挂满 45s 超时。
- ⚠️ **更正（见 [0.6.36] 的 CDP 补记）**：这条「连接前唤醒」的代价是**每次求解前都先把用户的 tab 逐个切到前台**（即现场看到的「打开前先被切一遍 tab」），已改为默认关闭（`SLIDEX_CDP_PRE_ACTIVATE=1` 才开），只在 connect 失败后兜底调用一次。
- 握手耗时从 45s 降至毫秒级（约 140ms）—更正：这是某次隧道 RTT 下的个案观测，不是稳定承诺。

## [0.6.31] - 2026-10-06

出口探针默认开启：`_maybe_record_success` 的 egress 探针与主仓 `_check_browser_outbound_ip_consistency` 同源同默认（env 未设时用 api.ipify.org，置空关闭）——无需宿主 compose 注入 env 即可记录真实出口 IP。

## [0.6.30] - 2026-10-06

成功链路记录 review 修复：摘要通道真实可用 + 出口 IP 接线 + 看门狗余量。

### Fixed
- **摘要键改名 `success` → `success_record`**：0.6.29 把记录写进 `summary["success"]`，但 `_finalize_telemetry` 随后用布尔覆盖同名键——per-run 摘要 JSON 里的记录块从未真正存在。改名后与布尔共存，docstring 同步修正。
- **看门狗手工等待余量 15s → 25s**：收割后的轨迹导出、成功记录补采（指纹 ≤5s + 出口 IP ≤3s）与遥测落盘都在预算内跑，原余量偏紧。

### Added
- **`capture_egress_ip`**：成功后在页面上下文 fetch `XY_OUTBOUND_IP_PROBE_URL`（复用主仓既有探针 env，ipify 支持 CORS）——CDP 真机模式得到用户浏览器真实出口（风控实际看到的 IP），容器模式得到容器出口；未配置/失败返回 None。记录 `environment.egress_ip` 带回宿主落库（主仓 upsert 同步支持该列）。
- **`settle_source="already_in_jar"`**：票据在结算前已落 jar 时 `_settle_x5sec` 早退无事件——显式标注，消除 None 的歧义。

## [0.6.29] - 2026-10-06

成功链路记录（schema v1）：每次通过风控，把"指纹链路 × 执行环境 × 滑块/票据细节"落成结构化记录，供宿主入库与后续失败对照。

### Added
- **`slidex/_success_record.py`**：`FINGERPRINT_AUDIT_JS` 移为唯一事实源；`capture_fingerprint`（活页面只读补采，5s 预算，CDP 模式也采）；`build_success_record`（schema v1：标识/结局/滑块/票据/指纹/环境六块）；`persist_success_record`（telemetry 目录 `successes.jsonl` append-only）。
- **solver 五个成功出口**标记 `_success_outcome`（provider_pass / legacy_pass / voucher_harvest / manual_pass_wait / jar_landed），三个求解入口（`_solve_impl` / `_solve_on_existing_impl` / `solve_on_page`）成功返回前统一走 `_maybe_record_success`：指纹成功时补采（失败回退 `_audit_browser_fingerprint` 的 init 快照）、挂 `solver.success_record`（宿主同步入库主通道）、并入遥测摘要 `success` 块、落 `successes.jsonl`。记录构建任何失败只降级为 debug 日志，绝不影响求解结果。
- `_audit_browser_fingerprint` 顺手把审计快照存 `_fingerprint_at_init`（成功补采的回退源）。

## [0.6.28] - 2026-09-29

正交手势库：失败重试按校验码换家族，真人点序改为相对起点累计。生产审查追补：`end_hold_scale` 接到 Aliyun、human `-1` 按新 travel 重缩放、GeeTest 填 code、派发失败不再伪装成 `-1`。

### Added
- **`slidex/_gestures.py`**：一次 solve 只建一个 `GestureSession`。`next(distance, last_code)` 规约：`None` 取 `FIRST_TRY_ORDER` 第一个可用；`300` / 有包失败码 consume 当前家族换下一正交家族；`-1` 不换（human 保留同一录音但按本轮 distance 重缩放；合成同家族重采样）。合成五家族独立生成（minimum_jerk / ballistic_corrective / overshoot_snapback / pause_hold / tremor_dense），禁止调用 `generate_trajectory`，也禁止抄四段 eased。
- **`SliderTrajectoryPool.load_unused_human`**：只取 success + `source` 以 `human` 开头、tolerance=0.10 的未用文件；`GestureSession` 禁止 `load_best`。
- **`apply_end_hold_scale` / `DragDispatchError`**：末端握持缩放收进 `_drag`；CDP send 失败上抛，solve 环中止而不是按 `-1` 锁家族。

### Changed
- **legacy / provider 重试环合成一个环**：每次 attempt 必先 `plan=session.next(...)` 再只播 `plan.points`。`recorded` 耗尽 human 时 `next()` 返 `None` 即停，禁止回绕 `generate_trajectory`。CDP 编译失败回退 Playwright 带着同一 plan；`DragDispatchError` 直接 fallback。
- **`extra_overshoot` / `end_hold_scale` 跟 plan**：human 与已烘焙过冲家族 extra_overshoot=False。Aliyun 与 GeeTest `perform_slide` 都接这两个参数；mixin 按签名转发 `extra_overshoot` / `end_hold_scale` / `cdp_session`。
- **300 通路**：`Aliyun.validate_response` 在 `interpret_slide_json` 之前写 `_result_code`；GeeTest 成功写 0、失败写包内 code 或 1；`bind` 同时清 `_result` 与 `_result_code`；`SolveResult` 增 `code`；`get_result` 超时 `code=-1`，有包无数字码的失败为 `1`（不再塌成 `-1`）。
- **`human_events_to_points`** 改为相对 `events[0]` 累计；旧差分条按 last/sum 对齐 distance 升级（不猜中段 `|x|`）。

### Removed
- 死代码 `_replay_recorded_playwright`、solver 未使用的 `generate_trajectory` 导入、`_current_recorded_trajectory`、`_do_slide` 的废弃 `recorded_trajectory` 参数。

### Notes
- `generate_trajectory` 默认行为保持不变，仅兼容旧测与直接调用。

## [0.6.25] - 2026-09-26

review 追补 + 本地风控模拟器。**本版未部署生产**——按用户指令，容器 recreate/真实风控触发需用户批准窗口，拖动行为验证一律先过本地模拟器。

### Fixed
- **0.6.24 流水线覆盖缺口**：CDP 拖动实际有三处实现，0.6.24 只改了 provider 一处——①`_do_slide` 生成轨迹的内联 CDP 派发（逐事件 await，节奏仍被 RTT 撕碎）；②`_replay_recorded_cdp` 录制回放（同病）；③`_slide_playwright` 的 0.6.24 分支**生产不可达**（该函数仅在 `cdp is None` 时被调用，纯死代码+一个测试死代码的假绿）。0.6.25 全部收口到 `_drag` 流水线：生成轨迹与录制回放在 `_do_slide` 层直接派发，`_slide_playwright` 回归纯顺序路径并加注释说明调用约束。释放位语义保持（录制末点=up 位置）。

### Added
- **本地风控模拟器**（用户指令：禁止以线上真实风控做验证）：`slidex/fixtures/slider_sim.html` + `tests/test_slider_sim.py`——本地 Chromium 加载自研仿真滑块（只接受 isTrusted 输入、校验拖拽期 buttons===1、事件数与终点位置判定），用与生产相同的 `_drag` 流水线端到端驱动，断言判过。浏览器不可用自动跳过。今后拖动行为变更先过仿真器，再谈上线。

### Notes
- 测试 461 绿；`test_review_fixes` 回放顺序契约改为语义级断言（按下先于拖动 move / move 携带 buttons=1 / 释放位=录制末点），不再钉旧实现的精确事件序列。

## [0.6.24] - 2026-09-26

拖动平滑度修复：CDP 真机模式下轨迹卡顿、不连贯（用户肉眼实测）。

### Fixed
- **拖动节奏与隧道 RTT 解耦（流水线派发）**：顺序执行路径每个事件都是 `await mouse.move()`/`wait_for_timeout` 的完整往返（VPS→反向隧道→用户 Chrome，30-150ms/次），设计 600ms 的人形时间线（~15 点、25-75ms 间隔）被撕成 2-6 秒台阶，阿里前端看到的是低频大间隔 mousemove 串。新 `slidex/_drag.py`：把拖动 choreography（接近→按下→press-hold→位移点→过冲/回拖→末端握持→松键）编译为 `Input.dispatchMouseEvent` 事件时间线，CDP 会话可用时按设计间隔 fire-and-forget 派发（不等每个 ack）——浏览器收到的事件间隔 = 设计间隔 ± 毫秒级调度抖动。provider 与 legacy 两路接入；容器 patchright 模式（禁 CDP 会话、本地 RTT ~1ms）保持原顺序路径。

### Notes
- isTrusted 保证：Input domain 派发与 playwright mouse 同源可信；按下期间 mousemove 携带 buttons=1。
- 测试：`tests/test_drag_pipeline.py` ×4（事件序/按钮态/无双重过冲/**60ms 模拟 RTT 下节奏还原**——顺序实现在该用例必炸/legacy 分支不触碰 page.mouse）。

## [0.6.23] - 2026-09-26

性能优化批：自愈链耗时与 1GB VPS 内存突发双管齐下。

### Added
- **voucher 短路收割**：provider 失败后若 `_bx_voucher` 已捕获（拖动实际通过、结果等待器没等到自家信号），跳过 legacy 全套仪式直达收割——生产实测这段空耗 ~40s（17:59:04 票据 → 17:59:47 收割）；legacy recorded/generated 重试轮内票据一出现也立即收割，不再烧剩余 attempt。
- **内存守卫旗标** `MEMORY_GUARD_LAUNCH_ARGS`（`--renderer-process-limit=2` + `--js-flags=--max-old-space-size=256`，与 bot 侧 qr 路径 1e7fb80 同款）：削掉每次求解的浏览器内存突发——宿主慢性内存饥饿下（available 常 <150MB、swap 1.27G），一次突发即引发换页风暴（实测 load 25+、PSI mem full 54%@5min）。

### Changed
- 误导标签澄清：`pass! (manual voucher harvest)` → `pass! (auto voucher harvest)`（该路径票据绝大多数来自自动拖动；"manual" 专属 CDP 人工等待期真人拖过的分支）；telemetry `manual_voucher_harvested` → `voucher_harvested`。

### Notes
- 测试：455 绿（+3：provider 失败有票据跳过 legacy / 无票据保持原语义 / generated 轮中票据短路收割）。

## [0.6.22] - 2026-09-25

容器浏览器指纹加固：CDP 真机模式已 2/2 实战过滑，本版针对"VPS 容器浏览器"这一兜底环节——真人拖也 code=300 的环境指纹问题。

### Added
- **浏览器 channel 升级**：`_resolve_browser_channel()` auto 探测 `google-chrome-stable`（自动切 `channel="chrome"`）；约定与 stealth.py 相同的 `XY_SLIDER_BROWSER_CHANNEL` env（显式 `chromium/none/off` 强制回自带 Chromium）。容器自带 Chromium 的 `userAgentData.brands` 露 "Chromium" 是强自动化信号，真 Chrome 无此问题。
- **指纹自审计**：`_init_browser` 容器模式末尾一条只读 evaluate，把 UA / brands / platformVersion / WebGL 渲染串 / 字体集 / 时区 / 硬件并发等硬信号量化成一行 INFO 日志（`XY_SLIDER_FINGERPRINT_AUDIT=0` 可关；CDP 真机模式不跑——真机是对照组）。配套独立探针 `slidex/scripts/fingerprint_probe.py`（支持 `--connect-cdp` 审计外部真机作对照）。

### Changed
- **环境一致性旗标**：新增 `ENV_CONSISTENCY_LAUNCH_ARGS`（`--use-angle=gl` 让 ANGLE 走桌面 GL——配合镜像内 `libgl1-mesa-dri` 即 Mesa llvmpipe，真实 Linux 无加速机器的合法特征，替代基本只在自动化环境出现的 "Google SwiftShader"；`--lang=zh-CN --accept-lang=zh-CN` 对齐账号真实客户端语言）。patchright 后端下只补这组旗标，反检测面继续交给 patchright 本体。

### Notes
- 需镜像侧配合：bot Dockerfile 安装 google-chrome-stable（amd64）+ `libgl1-mesa-dri` + `fonts-noto-cjk`（中文平台会话无 CJK 字体是矛盾信号）。
- 测试：`tests/test_browser_fingerprint.py` ×9（channel 解析 / patchright 旗标最小面 / env 强制 chromium / 审计开关与失败不阻断）。

## [0.6.21] - 2026-09-25

生产级 review 修复：CDP 模式的两个结构性缺口——并发失控与页面劫持。

### Added
- **CDP 端点互斥**：`solve_on_existing_page` 按 endpoint 键控 `asyncio.Lock` 串行化。并发管理器只覆盖容器内浏览器路径（stealth），CDP 并发进入会互抢页面、互注账号 cookie——多账号同时被罚时会在同一真实浏览器里互相污染会话。同 endpoint 排队（busy 打日志），不同 endpoint 互不阻塞；排队等待不占自己的看门狗预算。

### Changed
- **专用标签页**：`_connect_existing_browser` 不再复用 `pages[0]`——原实现把用户已打开的页面导航到 punish 页（原内容被顶掉），且 pages[0] 可能是被省内存模式冻结的页。改为 `context.new_page()` 开专用页（`_cdp_owned_page` 标记），solve 结束后 `_close_cdp_only` 预算化关闭自开页（调用方持有页与用户原页绝不动）。

### Notes
- 测试：`test_cdp_mode.py` fakes 支持 `new_page`/`close` 语义；断言改为"不劫持用户页"；新增同 endpoint 串行 / 跨 endpoint 并行 / 自开页关闭三例。

## [0.6.20] - 2026-09-25

二轮 review 发现的预算错配：CDP 人工等待期（默认 300s）不感知 solve 硬看门狗（600s）——自动拖动阶段一旦耗掉 300s+，人工等待会在中途被看门狗 cancel，用户临门一脚拖过的成果随 cancel 整体丢弃（看门狗出口只断开连接并返回 False/None，不结算票据）。

### Changed
- 三个 solve 入口（`_solve_impl` / `_solve_on_existing_impl` / `solve_on_page`）记录 `self._solve_t0`（monotonic）。
- `_fallback_or_fail` CDP 分支的人工等待按剩余看门狗预算截断：`min(MANUAL_VOUCHER_WAIT_S, SOLVE_WATCHDOG_TIMEOUT_S - elapsed - 15s)`，截断时打日志。自动阶段快时等待仍是足额 300s，只有自动阶段慢时才让位——宁可短等，不可被硬 cancel。

### Notes
- 测试新增预算截断用例（`_solve_t0` 回拨 590s → 等待立即让位）；入口重置用例补验 `_solve_t0` 已记录。

## [0.6.19] - 2026-09-25

生产实测：用户 Chrome 开着省内存模式时，`connect_over_cdp` 默认 180s 超时会在"附加冻结标签"阶段挂满（裸 CDP 与逐 target 探针均正常，仅附加挂起），白白损失一个周期。用户决定保留省内存模式，故在代码侧收敛损失。

### Changed
- `_connect_existing_browser` 的 `connect_over_cdp` 超时收敛为 45s（env `SLIDEX_CDP_CONNECT_TIMEOUT` 秒可调）：慢隧道/大目标数仍够用，冻结标签挂起时快速失败把周期还给 fallback 链路。

## [0.6.18] - 2026-09-25

用户在真实浏览器里手动通过滑块后总结出判定要领：**拖到终点、滑块变绿后不能立刻松开鼠标**——验证请求在松键时刻评估，"到位即松"会被拒。此前所有自动轨迹（provider + generated + recorded，全部 code=300）都在终点后 30-110ms 内立即释放，与此要领相悖。

### Changed
- **末端握持**：全部 5 处拖动路径（generated/recorded × CDP/Playwright + `aliyun` provider）在 mouseup 前增加 `slide_end_hold_range()` 随机握持（默认 0.45-1.10s，env `SLIDEX_SLIDE_END_HOLD_MIN/MAX` 秒可调，min>max 钳制）。CDP 路径同步校正事件时间戳。

### Notes
- 测试 436 绿（新增 `tests/test_slide_end_hold.py` 3 例：默认值/env 覆盖与钳制/Playwright 路径实测 move→up 间隔 ≥ 握持下限）。

## [0.6.17] - 2026-09-25

0.6.16 生产验证：CDP 连接被 Chrome **省内存模式冻结的后台标签**卡死（`connect_over_cdp: Timeout 180s`——`<ws connected>` 后附加冻结 target 挂起；裸 CDP `Browser.getVersion` 与逐 target 探针确认隧道/协议层完好，仅 3 个冻结页挂）。经 `/json/new` + `/json/close` 重建标签后连接秒过。另一时序问题：自动拖动窗口仅 ~60s，用户拖过时监听已关闭（第二次票据流失）。

### Added
- **CDP 人工等待期**：自动拖动全部失败后不立即返回失败，保持响应监听轮询至多 `SLIDEX_MANUAL_VOUCHER_WAIT`（默认 300s，须小于 solve 看门狗 600s）：票据头出现 → 结算收割；x5sec 直接落 jar → 收割；页面关闭 → 提前退出。收割成功返回 `(True, cookies)`，超时才降级。telemetry/step `manual_wait`。仅 `_is_cdp_mode` 且 page 存在时启用（page=None 保持旧语义，兼容既有测试与无页面场景）。

### Notes
- 测试 433 绿（新增 `tests/test_voucher_harvest.py` 4 例：等待期收割票据、jar 直收、超时降级、页面关闭提前退出）。
- 运维建议：用户侧 Chrome 关闭省内存模式（`chrome://settings/performance`），否则冻结标签仍会使 connect 挂起（connect_over_cdp 180s 超时内 solve 看门狗 600s 不会触发，损失一个周期后自愈）。

## [0.6.16] - 2026-09-25

CDP 模式首个生产周期（用户 PC Chrome 经反向隧道）出现决定性一幕：**真人在外部浏览器拖过滑块，`checkCookie` 链走通、`bx voucher header captured`**（0.6.10 旁路票据链首次被打通）——但 provider 结果等待器只认自己流水线的完成信号（`timeout waiting for result`），legacy 循环只见"滑块已消失"（人已拖过），全部重试耗尽走 `_fallback_or_fail`，**把已捕获的 bx-x5sec 票据当失败丢弃**，返回 `(False, None)`。

### Added
- **手动通过收割**：`_fallback_or_fail` 失败出口先检查 `_bx_voucher`——已捕获则走既有 `_settle_x5sec` 结算（bx_header → x5sec 注入 context 并合入 cookies），x5sec 落地才返回成功；结算异常或无 x5sec 照旧返回失败，不谎报。telemetry `manual_voucher_harvested`、step `voucher_harvest`。
- 每个 solve 入口（`_solve_impl` / `_solve_on_existing_impl` / `solve_on_page`）重置 `_bx_voucher`，防止上一轮残留票据跨轮误判。

### Notes
- 测试 429 绿（新增 `tests/test_voucher_harvest.py` 5 例）。
- 结论性验证：设备指纹假设部分成立——真手 + 真机 + 家宽直连能过（code=0 → voucher），VPS 容器内合成轨迹始终 300。剩余问题只是"自动流水线没接住人工通过"，本版修复。

## [0.6.15] - 2026-09-25

重新扫码验证给出决定性证据：**全新登录态 3 秒内即触发 FAIL_SYS_USER_VALIDATE**，且人工面板里的真人轨迹同样被拒（code=300）。风控标记不在 cookie，在设备/会话层——VPS 容器内 headless Chromium 的设备指纹与账号历史登录设备（用户 PC）完全不匹配是主嫌疑。轨迹拟人化在此前提下收益有限。

### Added
- **CDP 模式会话身份保障**：`_connect_existing_browser` 连接外部真实浏览器后、导航前注入 bot 的账号会话 cookie（`_inject_cookies`，按 verify_url 域解析 `cookies_str`）。punish 的 x5secdata 绑定 bot 会话——借外部浏览器（用户 PC Chrome 经 `ssh -R` 反向隧道）的是**设备指纹**，会话身份必须仍是 bot 账号，否则票据发到浏览器自己的会话上。
- CDP 导航改用 `_goto_page`（networkidle 超时降级 domcontentloaded），与浏览器模式一致。

### Notes
- 配套 bot 侧 `XY_SLIDER_CDP_ENDPOINT` 开关（同 commit 链）。测试 424 绿（新增 test_cdp_mode 2 例）。
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
