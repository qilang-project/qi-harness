---
name: qi-harness
description: Build LLM agents in the Qi (奇语) programming language using the qi-harness framework — an observable / retryable / evaluable wrapper around the 标准库.大模型 LLM API. Covers one-shot chat, streaming, automatic tool-call dispatch loops (parallel tool_calls), graph control flow with checkpoints and human-in-the-loop resume, structured JSON output, real token usage and budget enforcement, multi-agent teams with handoff, vector memory and hybrid-retrieval RAG, an MCP client (stdio/HTTP), guardrails, evaluation suites, tracing (OTLP), and retry with backoff. Use when the user builds Qi LLM agents, chatbots, tool-using assistants, RAG pipelines, or multi-agent systems. Requires the qi-lang skill for base language syntax.
metadata:
  author: qilang
  version: "0.2"
---

# qi-harness — Qi 语言 LLM Agent 框架

基于 Martin Fowler "harness engineering" 思路：把 LLM 调用包在**可观测 + 可重试 + 可评估**的外壳里。底层是 `标准库.大模型`（OpenAI 兼容协议），harness 层提供 agent loop、自动工具派发、追踪、重试。

> **先读 `qi-lang` 技能**。本技能只讲 qi-harness 的 API。

## 何时使用

- 用户用 Qi 写 LLM 应用 / 聊天机器人 / 工具调用 agent / RAG 管线 / 多智能体系统
- 用户问 qi-harness 的会话、对话、流式、工具调用、MCP 客户端、结构化输出、预算、handoff、向量记忆/检索、图控制流/断点续跑/HITL、评估、护栏、追踪
- 用户提到 `导入 Harness`、`大模型(...)`、`创建代理`、`连接MCP_stdio`、`创建图`、`创建团队`

## 配置与会话

一行 builder 配置任何 OpenAI 兼容端点（DeepSeek、OpenAI、本地 …）：

```qi
导入 Harness::{ 模型配置, 大模型, 配置系统提示, 配置超时, 开启会话, 关闭会话Harness };

变量 配置: 模型配置 = 大模型("https://api.deepseek.com", 密钥, "deepseek-chat");
配置 = 配置系统提示(配置, "你是简洁的中文助手。");
配置 = 配置超时(配置, 60);
// 可选：配置温度(配置, "0.7")、配置最大令牌(配置, 2048)
变量 会话: 整数 = 开启会话(配置);   // >0 成功
// 用完：关闭会话Harness(会话)  —— 注意是 关闭会话Harness，不是 关闭会话
```

> ⚠️ API 密钥从环境变量读，**不要硬编码**：`变量 密钥 = 系统.获取环境变量("QI_LLM_KEY");`

## 代理（Agent）

```qi
导入 Harness::{ 创建代理, 简单问, 流式问, 添加工具, 运行, 关闭代理 };

变量 代理值: 代理 = 创建代理("助手", 会话);

// 非流式调用默认最多尝试 3 次（首次调用计入）；可用 设置重试策略 覆盖。

// 1. 一次性问答（无工具）
变量 回复: 字符串 = 简单问(代理值, "用一句话介绍 qi 语言");

// 2. 流式问答 —— 块回调签名固定 函数(字符串): 整数
函数 打印片段(片段: 字符串) : 整数 { IO.打印(片段); 返回 1; }
变量 完整: 字符串 = 流式问(代理值, "解释 LLVM", 打印片段);

关闭代理(代理值);
```

当前流式实现会忽略块回调的整数返回值，返回 `0` 也不会取消流。`流式运行` 遇到同一批多个工具调用时
固定按顺序串行执行，不使用非流式 `运行` 的并发批处理。

跨会话 token/成本预算可以附加到代理。成功的非流式请求会自动累计真实 usage，达到上限后
下一次调用在请求 provider 前返回 `[harness] budget_exceeded`：

```qi
导入 Harness::{附加预算};
导入 Harness.预算::{创建预算, 预算报告};

变量 预算 = 创建预算(100000, 0);
代理值 = 附加预算(代理值, 预算);
变量 回复 = 简单问(代理值, "分析这份资料");
IO.打印行(预算报告(预算));
```

> 自动重试和自动预算目前只覆盖非流式调用；流式调用避免在已输出片段后自动重放。

## 工具调用（自动派发循环）

工具处理函数签名固定：`函数(参数JSON: 字符串): 字符串`。

```qi
导入 Harness::{ 工具, 创建代理, 添加工具, 运行, 关闭代理 };

函数 天气查询(参数JSON: 字符串) : 字符串 {
    返回 "{\"temp\":18,\"condition\":\"晴\"}";
}

函数 入口() {
    // ... 开启会话 ...
    变量 代理值: 代理 = 创建代理("天气助手", 会话);

    变量 天气工具: 工具 = 新建 工具 {
        名字: "天气查询",
        描述: "查询城市天气。",
        参数schema: "{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}}}",
        处理: 天气查询,
    };
    代理值 = 添加工具(代理值, 天气工具);

    // 运行：自动 detect tool_calls → 派发 → 回写结果 → 继续，直到模型给最终文本
    变量 回复: 字符串 = 运行(代理值, "北京天气怎样？");
    IO.打印行(回复);
    关闭代理(代理值);
}
```

`运行` 支持 **parallel tool_calls**。本地工具默认并发，MCP 固定串行；一批调用只有在全部已放行且
都是本地并发工具时才整批并发，否则整批串行。结果按原 ToolCall 顺序回写。
`设置最大步数(代理, N)` 防止无限循环（默认 10）。

```qi
导入 Harness.工具::{设置执行模式, 设置工具钩子, 工具成功, 工具错误};
代理值.工具表 = 设置执行模式(代理值.工具表, "天气查询", 1); // 0 并发，1 串行
代理值.工具表 = 设置工具钩子(代理值.工具表, "天气查询", 前钩子, 后钩子);
```

本地派发只验证参数和 before hook 输出是 JSON 对象，不执行完整 JSON Schema。`工具成功` /
`工具错误` 是可选标准 JSON helper，原始字符串结果不会自动包装。取消、进度、超时和 panic 隔离未实现。

## 技能（agentskills.io）

qi-harness 支持 [agentskills.io](https://agentskills.io/specification) 规范的 Skill：加载 `SKILL.md`（YAML frontmatter 含 `name`/`description` + Markdown 正文），装备到 agent 后注入系统提示，指导模型完成对应任务。

```qi
导入 Harness::{ 技能, 加载技能文件, 装备技能, 大模型, 开启会话, 创建代理, 简单问 };

变量 技能值: 技能 = 加载技能文件("技能/海盗腔.md");   // 解析 frontmatter + 正文
变量 配置 = 大模型("https://api.deepseek.com", 密钥, "deepseek-chat");
配置 = 装备技能(配置, "你是一个助手。", 技能值);          // ⚠️ 必须在 开启会话 之前
变量 会话 = 开启会话(配置);
变量 代理值 = 创建代理("助手", 会话);
IO.打印行(简单问(代理值, "..."));                       // 行为受技能影响
```

API：`解析技能(原始文本)` / `加载技能文件(路径)` / `技能摘要(技能)` / `装备技能(配置, 基础提示, 技能)` / `注入技能清单(配置, 基础提示, 清单)`。
`技能` 结构体字段：`名称` / `描述` / `正文`。

**批量装备整个技能库目录**（扫 `<目录>/<名>/SKILL.md`，全部加载注入；也适用于 bundle 目录）：

```qi
导入 Harness::{ 装备技能目录, 技能目录清单 };

// 一次装备一个 skills 库（兼容 ~/.agents/skills 这类 agentskills.io 目录）
配置 = 装备技能目录(配置, "你是助手。", "/Users/x/.agents/skills");

// 或先看看目录里有哪些技能（返回摘要清单）
IO.打印行(技能目录清单("/Users/x/.agents/skills"));
```

> ⚠️ `装备技能` 作用于**配置**（开会话前），不是会话——奇语的会话 system 提示在 `开启会话` 时随配置注入。

## MCP 客户端（连外部 server）

qi-harness **内置 MCP 客户端**（stdio + Streamable HTTP），把外部 MCP server 的工具拉进 agent 工具循环。底层 `标准库.MCP客户端`（Rust `qi_mcpc_*` FFI），Qi 层封装在 `MCP客户端.qi`。

```qi
导入 Harness::{ 连接MCP_stdio, 连接MCP_http, 装备MCP, 关闭MCP };

变量 描述符 = 连接MCP_stdio("npx", "[\"-y\",\"@playwright/mcp@latest\"]");  // → "mcpc|1"，失败返 ""
变量 代理值 = 装备MCP(创建代理("助手", 会话), 描述符);   // tools/list → 逐个注册为工具
变量 回复 = 运行(代理值, "...");                          // 模型调工具时自动转发到 MCP
关闭MCP(描述符);
```

出站：`MCP列出工具`/`MCP调用工具`/`MCP列出资源`/`MCP读取资源`/`MCP列出提示`/`MCP获取提示`/`MCP补全`。入站（双向，仅 stdio）：`设置采样处理`/`设置询问处理`/`设置根目录`/`取通知`（处理器签名 `函数(字符串):字符串`）。**agent 循环优先用 stdio**（HTTP 会话绑 TCP 连接，LLM 间隙可能超时断开；HTTP host 须 `localhost`）。完整 API + 一致性矩阵见 [MCP.md](MCP.md)。

奇语标准库另有 `标准库.MCP服务器`（qi 反向作为 **MCP server**：注册工具/资源/提示、SSE 推送）——见 `qi/示例/标准库/MCP服务器/`。

## 结构化输出

三条路子：① 语言级原语 `询问::<T>`（类型即 schema，见 qi-lang 大模型参考）；② agent 层 `结构化运行(代理值, 提示, 必需字段)`（json_object + 本地合法对象/顶层字段校验 + 纠正循环）；③ `结构化运行严格(代理值, 提示, schema串)`（仅把 strict json_schema 交给 provider，一次调用后原样返回）。严格路径当前没有本地 JSON Schema 验证或纠正循环。`对话.校验JSON对象(JSON文本, 必需字段)` 可单独校验顶层必填。

## 真实 token 用量 与 预算

- 用量来自 runtime 记录的上一次响应 `usage`（非估算）：底层 `大模型.用量(会话)` 返回 JSON；`运行`/`简单问` 内部会 emit `usage` 事件。
- **预算账本**（`Harness.预算`，token+成本双上限）：

```qi
导入 Harness.预算::{ 创建预算, 记入, 超预算, 已用令牌, 令牌余量, 预算报告 };
变量 预算 = 创建预算(5000, 100);       // token上限, 成本上限(分)；0=不限
记入(预算, 会话);                      // 每次调用后把该会话用量累加进账本
如果 (超预算(预算) == 1) { /* 停 */ }
IO.打印行(预算报告(预算));
```

（会话级硬上限用语言原语 `大模型.设置预算`；这里是库级跨会话账本。）

## 多代理（handoff 分诊）

```qi
导入 Harness.多代理::{ 团队, 创建团队, 添加成员, 运行团队 };
变量 团队值: 团队 = 创建团队();
团队值 = 添加成员(团队值, "分诊", 分诊会话);   // 每个成员是一个独立会话（各带系统提示）
团队值 = 添加成员(团队值, "退款", 退款会话);
变量 回复 = 运行团队(团队值, "分诊", "我要申请退款", 3);  // (团队, 起点成员, 提示, 最大交接次数)
```

分诊成员在系统提示里被要求"务必转交"，`运行团队` 解析 handoff 目标并把对话交给对应成员，直到有成员给出终答或达最大交接次数。

## 上下文窗口

`Harness.上下文`：`估算令牌(文本)` / `历史令牌估算(会话)` / `需要压缩(会话, 令牌上限)` / `滑窗裁剪(...)`（保留首尾丢中段）/ `摘要压缩(...)`（用 LLM 把旧轮次压成摘要）。长对话防爆窗。

## 向量记忆 与 RAG 检索

```qi
// 语义记忆（内存精确 top-K；无固定性能承诺，按实际数据量基准测试）
导入 Harness.向量记忆::{ 打开向量库, 语义记住, 语义回忆, 向量记忆条数, 关闭向量库 };

// RAG：加载→分块→双索引→混合召回(向量+词法)→可选 LLM 重排
导入 Harness.文档::{ 加载文本, 分块, HTML转文本 };
导入 Harness.检索::{ 创建检索配置, 索引文档按配置, 检索按配置, 关闭检索配置 };

变量 检索配置 = 创建检索配置(嵌入端点, 嵌入密钥, 嵌入模型);
变量 入数 = 索引文档按配置(检索配置, 库, 库, 全文, 400, 80);
变量 命中表 = 检索按配置(检索配置, 库, 库, 会话, 查询, 6, 3);
关闭检索配置(检索配置);
```

显式配置句柄可隔离不同 embedding 配置；旧 `配置检索`/`索引文档`/`检索` API 使用进程级默认配置，
并发或多租户代码应优先用 `*按配置` API。

agent 也可 `启用记忆(代理值, 库路径)` + `带记忆运行(代理值, 提示, 查询关键词)` 自动把回忆注入上下文，`提炼经验(...)` 把结果沉淀回记忆库。

## 图控制流：断点续跑 + HITL

有向图编排 + 检查点持久化（SQLite），支持人在环路中断/恢复，进程崩溃可续跑：

```qi
导入 Harness.图::{ 图, 创建图, 添加节点, 添加边, 运行图带检查点, 恢复图,
                   是中断, 中断问题, 是完成, 有检查点, 终点 };

变量 g: 图 = 创建图();
g = 添加节点(g, "问", 问处理);          // 节点处理器签名 函数(状态JSON): 状态JSON
g = 添加节点(g, "处理", 处理处理);
g = 添加边(g, "问", "处理");
g = 添加边(g, "处理", 终点());

变量 结果 = 运行图带检查点(g, 库, "任务001", "问", "{}", 50);  // (图,库,运行ID,起点,初始状态,最大步数)
如果 (是中断(结果) == 1) {                       // 节点可发起中断问人工
    // …拿到人工回复后…
    结果 = 恢复图(g, 库, "任务001", 人工回复);  // 从检查点续跑
}
```

`有检查点(库, 运行ID)` 查是否有未完成运行；完成后检查点自动清除。崩溃续跑用 `运行图持久化`/`恢复运行`（见 `examples/图_崩溃续跑.qi`、`examples/图_检查点HITL.qi`，均无需 LLM）。

## 普通 Agent 的 SQLite 持久会话

持久会话与图检查点是独立模型。普通对话使用 append-only entry 树和当前 leaf：

```qi
导入 Harness.代理::{代理, 附加会话存储, 恢复持久会话};
导入 Harness.会话存储::{打开会话存储, 关闭会话存储, 设置当前会话叶,
    导出持久会话JSON, 导入持久会话JSON};

变量 库 = 打开会话存储("/tmp/agent-sessions.db");
变量 代理值: 代理 = 附加会话存储(创建代理("助手", 开启会话(配置)), 库, "session-123");
变量 回复 = 运行(代理值, "继续任务");

// 新建 runtime 会话或切换 leaf 后显式恢复。
变量 新代理: 代理 = 附加会话存储(创建代理("助手", 开启会话(配置)), 库, "session-123");
变量 恢复数 = 恢复持久会话(新代理);
变量 导出JSON = 导出持久会话JSON(库, "session-123");
关闭会话存储(库);
```

附加后，非流式调用记录用户、provider 原始助手消息、工具结果、最终消息和错误。恢复只重放当前
root-to-leaf 路径中的消息与工具结果。流式调用不自动记录；配置/模型/摘要 entry 不会自动恢复
Agent 配置。`打开内存会话存储()` 使用 SQLite `:memory:`，关闭唯一句柄后数据消失，并非独立内存 backend。
版本 1 导入/导出 API 已可用，但存储不负责认证、租户归属或加密。

`创建代理` 接管 runtime 会话，`关闭代理` 会关闭会话和代理自建的重试资源；`附加会话存储` 不接管
数据库句柄。共享重试资源和 MCP 连接由创建它们的调用方释放或关闭；预算句柄不归 Agent 所有，
当前没有独立释放 API。调用方负责会话存储和其他共享资源的关闭顺序。

## 评估 与 护栏

- `Harness.评估`：`创建套件`/`添加测例`/`运行套件(套件, 代理, 裁判会话)`/`平均分百分`/`保存基线JSON`/`基线门禁(套件, 基线路径, 容差百分)`——CI 里跑回归门禁（配 LLM 磁带 REPLAY 确定性）。判分方式含子串匹配与 LLM 裁判。
- `Harness.护栏`：`检测注入(文本)`（prompt injection）/`脱敏(文本)`（PII）/`校验输出(...)`/`工具参数放行(...)`。

## 生命周期与追踪

非流式 `运行`、`简单问`、`结构化运行` 和 `结构化运行严格` 会发 canonical lifecycle v1：
`agent_start/end`、`turn_start/end`、`llm_start/end`，以及 `运行` 的 `tool_start/end`。
总线是进程内同步订阅，按注册顺序交付，不能捕获回调 panic。事件模块支持显式 bus 句柄，但 Agent
当前固定发到默认 bus，尚不能给单个 Agent 注入自定义 bus；多数 Agent lifecycle payload 仍是空对象。

```qi
导入 Harness.事件::{订阅生命周期事件, 清空生命周期订阅者};
导入 Harness.追踪::{安装生命周期追踪适配器};
导入 Harness.报告::{安装生命周期报告适配器, 文本报告};

安装生命周期追踪适配器();
安装生命周期报告适配器("任务");
变量 回复 = 运行(代理值, "完成任务");
IO.打印行(文本报告());
清空生命周期订阅者();
```

trace/report adapters 是 opt-in，避免和 Agent 仍保留的 legacy 直写 trace 重复。旧 trace 开关仍可用：

```qi
导入 Harness.追踪 作为 追踪;
追踪.禁用即时打印();   // 关掉实时打印（例如和流式回调输出混在一起时）
追踪.禁用();           // 完全关闭追踪
```

legacy trace 类型包括 `llm_call` / `llm_response` / `tool_call` / `tool_result` / `error` /
`done` / `stream_start` / `stream_chunk` / `stream_end`。流式 API 当前只走这套 legacy trace，
尚未接入 canonical lifecycle。

## 重试

```qi
导入 Harness::{ 默认策略, 重试调用 };
// 重试调用(策略, 代理名, 调用) — 调用是 函数(): 字符串，429/网络抖动指数退避重试
```

每个 `创建代理` 默认创建独立重试资源（熔断 + 令牌桶）。需要跨 Agent 共享 provider 配额时：

```qi
导入 Harness::{共享重试资源};
导入 Harness.重试::{创建重试资源, 配置资源熔断, 配置资源限流};
变量 资源 = 创建重试资源();
配置资源熔断(资源, 5, 30000);
配置资源限流(资源, 10, 20);
代理值 = 共享重试资源(代理值, 资源);
```

旧的 `配置熔断` / `配置限流` API 使用共享资源 `0`。自动重试仍只覆盖非流式 provider 调用。

## Run Context

`Harness.运行上下文` 提供显式句柄：`创建运行上下文`、`进入运行上下文`、`退出运行上下文`、
`取运行ID`、`推进轮次`、`推进步骤`、trace metadata 与 request-scoped 工具键值。非流式 Agent
运行自动建立并清理 context；工具处理器可继续通过 `Harness.工具上下文` 的兼容 API 读取当前值。
句柄状态表和 current-context 栈仍是进程内实现。报告已有独立句柄 API；legacy trace sink 和
兼容无句柄报告 API 仍保留进程级默认状态。

## 最小 CLI 与 HTTP 适配器

CLI 入口是 `cmd/qi-harness.qi`：

```bash
qi run cmd/qi-harness.qi -- check
qi run cmd/qi-harness.qi -- test
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session list
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session show session-123
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session path session-123
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session export session-123 session.json
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session import session.json
qi run cmd/qi-harness.qi -- --db /tmp/sessions.db session branch session-123 entry-456
```

它只做 provider 环境诊断、显示 `./run-offline-tests.sh` 命令，以及 SQLite 会话查看、版本 1 导入导出和
移动 current leaf 创建后续分支，不是 Agent runner，也不会恢复后继续调用模型。

`Harness.代理服务` 当前提供 `GET /health`、`POST /chat`、`POST /json`。`/chat` 有服务工具时走
工具循环，无工具时纯问答；`/json` 不装备工具。调用
`配置服务持久会话(SQLite路径, 默认所有者)` 后，请求使用字符串 `会话ID`，服务每次从 SQLite
恢复到新 runtime；未知 ID、owner 不匹配和 owner 映射缺失统一为 404。未配置时保留旧整数 `会话号` 协议。
默认应用层上限为 1 MiB body、256 KiB 提示，检查发生在 Web 已读完整 body 后；`/health` 仅是 liveness，
不检查 provider/SQLite/MCP readiness。owner 只是相等校验，不是身份认证；配置/工具/DB 仍为模块全局状态，
也没有关闭会话、流式事件、跨实例并发协调或实例隔离，不能宣传为生产就绪多租户服务。

## 质量门禁与限制

仓库根运行 `./run-offline-tests.sh`。它保留 suite 的非零退出状态，并覆盖当前接入的 diff whitespace、
语法、离线示例、事件/适配器、工具管道/调度、run context、会话持久化/导入导出/Agent 集成、CLI、
HTTP 持久会话、文件沙箱/报告/检索隔离和 M1 reliability。GitHub Actions 在 Ubuntu 22.04 与 macOS 14
运行同一门禁；真实 provider smoke test 不在离线门禁内。

- 自动 retry/usage/budget/lifecycle/session recording 主要覆盖非流式路径。
- 工具管道无完整 JSON Schema、取消、进度、超时和 panic isolation。
- lifecycle 有显式 bus，但 Agent 固定使用默认 bus；legacy trace sink、默认报告、服务配置和旧检索兼容 API 仍有进程级默认状态。
- 文件沙箱、白名单、护栏不是 OS/container 隔离。

## 底层直连（不用 agent 外壳）

```qi
导入 标准库.大模型 作为 大模型;
变量 会话 = 大模型.创建会话(URL, 模型, 密钥);   // 参数顺序 URL→模型→密钥，无隐式默认
变量 回复 = 大模型.对话(会话, "你好");
```

语言级 LLM 原语（`询问::<T>` 结构化输出、`流式` 一等流、`嵌入`/`相似度`、`工具模式`/`工具适配` 签名即工具、磁带录放）见 **qi-lang** 技能的大模型参考——它们是语言关键字，不需要 harness。

## 已知约定

- 关闭会话用 `关闭会话Harness`（`关闭` 这类短名以前被 codegen 劫持，现已修复，但 harness 保留了显式命名）
- 块回调 / 工具处理函数签名固定，别改返回类型
- 跨包导入用 destructure：`导入 Harness::{大模型, 开启会话, 创建代理, ...}`

## 运行

```bash
QI_LLM_KEY=sk-... qi run examples/deepseek测试.qi
QI_LLM_KEY=sk-... qi run examples/deepseek工具.qi    # 工具调用
QI_LLM_KEY=sk-... qi run examples/deepseek流式.qi    # 流式
```
