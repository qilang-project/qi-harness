# Changelog

All notable changes to qi-harness are recorded here. The project follows semantic versioning, with the qualification that public APIs may still change during the `0.x` series. Breaking changes must be called out and accompanied by migration instructions.

## [Unreleased]

### Added

- `跨度.qi`：从生命周期事件流还原带父子关系的调用树。事件流本来就配对且正确嵌套，
  用一个栈即可还原，代理主循环无需改动。每次运行一个独立跨度仓（父指针是 run 内
  局部下标），淘汰老运行时无需重映射。有界：默认保留 20 次运行、单次运行 2048 个
  跨度，丢弃有计数。
- `观测台.qi`：内建实时看板（qi-web LiveView）。运行列表 + 瀑布图 + `/metrics`，
  `开观测台(端口)` 一行起，后台运行不阻塞 agent。
- `观测指标.qi`：Harness 内建 Prometheus 指标，复用 qi-web 的注册表。LLM/运行时延
  用慢桶，工具耗时用快桶；标签只用 agent/tool/status，不含高基数的 run id。
- `追踪.导出树到OTLP`：整棵跨度树一次 POST，默认异步发送。
- `设置OTLP超时` / `设置OTLP同步` / `设置服务名` / `当前OTLP端点`。
- `tests/observability/`：跨度树单测、并发采集测（五路 goroutine）、真 collector 收
  字节的 OTLP 测、真跑 agent 的端到端测、抓 HTML 与 `/metrics` 的看板测。

### Fixed

- **`检索.按来源删块` 返回的不是行数是指针值**：`数据库.执行参数` 返回 JSON 字符串
  `{"成功":1,"影响行数":N}`，之前直接当整数返回 —— 调用方拿到的「删掉的行数」永远是
  个大得离谱的非零数。qi 编译器把注册表 ptr 返回映射为字符串后（2026-08-12）由类型
  检查抓出。现解析 `影响行数`。
- **OTLP 导出丢失整棵树的父子关系**：`导出OTLP跨度` 从未写过 `parentSpanId`
  （父跨度只在 `开始跨度` 那行 JSON 里出现过，结束时无处可取），推送到 Jaeger 是
  一堆孤儿 span。span 登记表现在记录父跨度。
- **`llm_end` 载荷恒为空**：所有下游拿到的 token 与成本都是 0，链路图能显示「哪一步
  慢」却无法显示「哪一步贵」。现改为带上 provider 返回的真实用量。
- **`tool_start` 载荷不含工具名**：链路图只能显示 `step-0-tool-0` 这类步骤编号，
  而「哪个工具慢/爱失败」正是最常问的问题。`tool_end` 一直带 `tool` 字段，`tool_start`
  没有。
- **span 登记表只增不减且按 ID 线性扫描**：长时间运行的 agent 会持续增长（O(n²)）。
  现改为结束即回收，表内只保留仍打开的跨度。
- OTLP 导出由逐条同步 POST 改为异步，不再把 collector 的 RTT 串进 agent 关键路径。

### Changed

- `Harness.qi` 新增 re-export `跨度`。`观测台` / `观测指标` 依赖 qi-web，**故意不**
  re-export，避免不需要看板的程序被迫解析 Web 包。
- CI 与 release 工作流的 `QI_SOURCE_REF` / `QI_RUNTIME_SOURCE_REF` 上调至
  2026.08.12-1（`d527402` / `cba00fb`）：钉住的 2026.07.24-1 编译器不认 qi-web 的
  模块限定类型标注（`变量 x: 查.参数集`），而 qi-web 的指标模块（观测台依赖）比该
  语法更晚出现 —— 不存在两全的旧组合。观测台测试新增工具链预检，旧工具链下明说
  原因并跳过（其余可观测性套件照跑）。
- CI 与 release 工作流的 `QI_WEB_REF` 上调至 `cf0593e`：原先钉的 `120576d` 早于
  qi-web 指标模块，`观测指标.qi` 在 CI 上会报「导入的符号不存在」。

## [0.2.0] - 2026-07-24

### Added

- A published Qi `2026.07.24-1` baseline for the stream-v2 timed-poll, tool-control, and Web transport body-limit ABIs.
- Pinned-source Qi installation for CI and release preflight, with policy tests prohibiting fabricated Qi versions, tags, and release-download references.
- Compatibility preflight that compiles and links probes for every required standard-library ABI family; no version-only compatibility claim is made before a real Qi release contains them.
- A checked public API manifest covering the `Harness` entry point and every direct-import `Harness.<module>` surface.
- CI drift detection for public function signatures and public type shapes.
- Deterministic first-release API bootstrapping from historical tag `2026.05.30-1`, pinned by exact commit and generated-manifest SHA-256; subsequent releases continue from prior signed `v*` tags.
- Recursive syntax checking for examples, with explicit reporting of examples skipped because an optional package is unavailable.
- Lifecycle events and adapters, run context, persistent session storage and import/export, CLI support, and service session persistence.
- `配置服务会话租约` for explicitly tuning the persistent service session lease duration.
- Isolated resource handles for retry state, reports, file sandboxes, retrieval configuration, and lifecycle event buses.
- Model request timeout configuration and reliability tests for timeout and budget enforcement.

### Changed

- The package and CLI version are now `0.2.0` for the first governed `0.2.x` release line.
- The offline quality gate is the canonical local and CI validation command.
- Release policy rejects breaking public API drift; additive drift is accepted only for a minor or major version increment.
- New agents own an isolated retry resource by default; sharing retry state is now explicit.
- Stateful subsystems increasingly prefer explicit handles while retaining selected default-handle convenience APIs during the `0.x` transition.

### Migration

- See [MIGRATING.md](MIGRATING.md) before updating from `0.1.x` or when intentionally changing `public-api.txt`.

## [0.1.0]

### Added

- Initial qi-harness package with model configuration, conversations, tools, agent loops, tracing, retries, skills, and MCP client support.
