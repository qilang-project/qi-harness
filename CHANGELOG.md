# Changelog

All notable changes to qi-harness are recorded here. The project follows semantic versioning, with the qualification that public APIs may still change during the `0.x` series. Breaking changes must be called out and accompanied by migration instructions.

## [Unreleased]

### Added

- **技能库渐进式披露**：`装备技能库(代理值, 目录)`。系统提示只放 name + description 清单
  （保留原系统提示，多次调用叠加多个根目录、重名后者覆盖并在 stderr 警告），给代理注册
  两个本地工具 `load_skill {"name"}`（返回正文 + 技能目录内 `scripts/` `references/`
  `assets/` 等资源文件清单 + allowed-tools 建议；同一技能第二次只回「已加载过」）与
  `read_skill_file {"name","path"}`（只读技能目录内相对路径，拒绝绝对路径 / `~` / `..`，
  超 256KB 截断并注明）。每个代理一个槽位（上限 8），与 `文件工具` 同一套 before-hook
  注入办法。附 `技能库清单` / `技能库已加载` / `应用技能工具白名单`（按技能 allowed-tools
  收窄 `设置工具白名单`，宿主显式调）。老的 `装备技能` / `装备技能目录` / `注入技能清单`
  原样保留。
- `技能.允许工具` 字段 + `技能允许的工具(技能值)`：解析 frontmatter `allowed-tools`
  （空格 / 逗号分隔，规整成逗号分隔）。
- **MCP 资源 / 提示进代理**（新模块 `MCP装备.qi`，已从 `Harness.qi` 再导出）：
  `装备MCP资源` 注册 `mcp_list_resources` / `mcp_read_resource {uri}`；`装备MCP提示` 把
  prompts/list 的每个 prompt 注册成 `prompt_<name>`（ASCII 安全化，参数 = 声明的
  arguments，只透传声明过的参数），调用时 prompts/get 的 messages 拼成文本；
  `装备MCP全部` = 三件一起。多台 server 工具名撞了加 `_2` / `_3` 后缀。
- **MCP服务 Streamable HTTP 传输**：`运行MCP服务_HTTP(服务, 主机, 端口)`。`POST /mcp`
  收 JSON-RPC 回 `application/json`；`initialize` 签发 `Mcp-Session-Id` 之后逐请求校验
  （缺头 400、未知/已结束 404）；通知 202；`DELETE /mcp` 结束会话；`GET /mcp` 405；
  `OPTIONS` 204。用 `标准库.网络` 裸 TCP 实现（每连接一个 goroutine，keep-alive），
  不依赖 qi-web，也没走 Rust 的 `qi_mcp_serve_http`（那条把所有响应包成 SSE、不校验会话、
  DELETE 回 405）。
- `tests/skills/`、`tests/mcp_equip/`（含 `fake_mcp_server.py` 假 stdio server）、
  `tests/mcp_transport/` 三组离线断言，`examples/MCP服务HTTP_往返测.qi` 往返示例，
  `examples/技能/技能库/` 技能库 fixture；都已接进 `run-offline-tests.sh`。

- `常驻工.qi`：让 agent 主动开口。定时/cron 巡检 + 去重节流出口 + 事件桥。
  三层各管各的：常驻工管什么时候醒、出口管说不说/说给谁、接收者管怎么送出去
  （qi-web 广播 / Redis / webhook 由调用方写）。重心在出口那层的**指纹去重 +
  静默期** —— 每天照发一条的推送最后没人看，只在有东西可说时说才有人读。
  排期写错**拒绝启动**而不是退化成每分钟一次（那是半夜刷屏）。
- `排期.qi`：cron 表达式解析/匹配/算下次触发，纯计算。五段标准 cron + 三字母
  月份周几缩写 + `@每天`/`@daily` 一类别名。两处按标准而非直觉实现，都写在注释里：
  匹配走**本地墙钟**（qi 的时间字段取值器是 UTC，而 `0 9 * * *` 指的是本地九点）；
  日和周同时限定时按 POSIX 取**「或」**（`0 0 13 * 5` = 每月 13 号外加每个周五）。
- `tests/scheduler/`：排期 55 条 + 常驻工 47 条断言，已接进 `run-offline-tests.sh`。

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

- **`解析技能` 的 frontmatter 是全文子串查找**：`name:` 会撞到 description 里出现的
  「name:」字样，也不限定在首个 `---` 块内（正文里再出现 `---` + `name:` 也会被吃）。
  改成只认首个 `---` … `---` 块、按行 `^键:` 精确匹配（`username:` 不算 `name:`），
  值去成对引号，支持 `description: >` / `|` 折叠多行，CRLF 也认；没有 frontmatter 的
  文件名称为空、全文作正文。用例见 `tests/skills/技能_测.qi`。

- **给公开结构体加字段不再被门禁判成破坏性变更**。`api-diff.py` 逐行比对声明文本，
  结构体多一个字段 = 老声明消失 + 新声明出现 = 「有删除」= breaking，于是
  `模型配置` 加 `额外参数`（2026-08-20，为 `开启联网搜索` 铺路）之后 CI 一直红。
  现在差分器认得「同名结构体、老字段一个没动、只多了字段」这一种形状，归为兼容。
  依据是本仓的公开结构体只由自己的 builder 构造（`大模型()` / `默认配置()` +
  `配置端点()` 链式改），全仓与所有示例里没有一处用户侧 `新建 模型配置 { … }`，
  多个字段调用方一行都不用改。**删字段 / 改名 / 改类型 / 换结构体名仍然算破坏**，
  加字段的同时删掉别的东西也照样拦 —— 三条新测试钉住这四种形状。
- **`事件.清空生命周期订阅者` 泄漏订阅者列表**：只 `设置` 新列表不 `删除` 旧的，
  旧列表连同里面的闭包一直挂着（`QI_RC_REPORT` 里活跃闭包不归零）。
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
