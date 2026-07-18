# Changelog

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
