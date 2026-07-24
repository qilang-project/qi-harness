# qi-harness

LLM agent 框架，基于 [Martin Fowler — Harness Engineering](https://martinfowler.com/articles/harness-engineering.html) 模式。

跟 `qi-cli`（命令行）/ `qi-web`（HTTP 服务）平级，专门给 LLM agent 开发场景：把模型调用包在**可观测 + 可重试 + 可评估**的外壳里。

qi-harness `0.2.x` requires Qi `2026.07.24-1` or newer. That release provides the stream-v2 timed-poll, tool-control, and Web transport body-limit ABIs used by reliable streams, controlled tools, and persistent services. CI and release preflight pin the exact Qi, qi-runtime, qi-gui, and qi-web commits used for this release and run compile/link probes for every required ABI family.

## 项目结构

```
qi-harness/
├── qi.toml            # 包配置
├── Harness.qi         # 主入口（仅 re-export 模型/对话/工具/代理/追踪/事件/重试/技能/MCP客户端/运行上下文/工具上下文/会话存储）
├── 模型.qi             # provider 抽象（OpenAI/Anthropic/DeepSeek/Moonshot/智谱/本地）+ 配置 builder
├── 对话.qi             # 助手消息 + 工具调用解析
├── 工具.qi             # Tool 定义 + 注册表 + 派发
├── 代理.qi             # Agent loop（核心）— 自动 tool_call dispatch
├── 追踪.qi             # event log（JSON 行，可挂 sink）
├── 重试.qi             # exponential backoff
├── 评估.qi             # Eval harness — 跑测试用例 + 期望对比
├── 循环.qi             # Loop engineering — 目标驱动递归循环：制造者→校验者→升级
├── 文件工具.qi         # 文件系统工具（带沙箱 + 权限）：钉死根目录，挡绝对路径/.. 穿越/符号链接逃逸
└── examples/
    ├── deepseek测试.qi   # 单轮对话（简单问）
    ├── deepseek工具.qi   # tool-using agent（天气 + 时间，含并行工具调用）
    ├── deepseek流式.qi   # 流式输出
    ├── deepseek流式工具.qi # 流式 + 工具：边流式吐字边自动调工具
    ├── 文件助手.qi       # 文件 agent：沙箱内读写 + 越界被拒
    └── 循环_迭代标语.qi   # loop engineering：自动迭代直到验收通过/升级

> 无 LLM 的确定性单元测试（并发派发 / 结构化输出 / 文件沙箱安全）在独立的
> [`qi-test`](../qi-test) 项目里：`cd qi-test && ./跑测试.sh`
```

> 📘 AI 辅助：本项目带 [`SKILL.md`](SKILL.md)，可作为 agent skill 安装，让 AI 助手准确生成 qi-harness 代码。

## 核心 API

```qi
导入 Harness::{
    模型配置, 大模型, 配置系统提示, 配置超时, 开启会话,
    创建代理, 添加工具, 运行, 简单问, 流式问, 流式运行, 关闭代理,
    工具
};

// 1. 一行配置 — 任何 OpenAI 兼容 API
变量 配置 = 大模型("https://api.deepseek.com", 密钥, "deepseek-chat");
配置 = 配置系统提示(配置, "你是助手。");
配置 = 配置超时(配置, 60);  // 默认 60 秒，实际传入 runtime 的 timeout_secs

// 2. 开会话 + 创建 agent
变量 会话: 整数 = 开启会话(配置);
变量 代理值: 代理 = 创建代理("我的助手", 会话);

// 3. 注册工具（可选）
代理值 = 添加工具(代理值, 新建 工具 {
    名字: "查天气",
    描述: "查指定城市天气",
    参数schema: "{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}}}",
    处理: 我的天气函数,
});

// 4. 跑 — 自动 tool dispatch loop（支持并行 tool_calls）+ trace
变量 回复 = 运行(代理值, "东京天气怎样？");

// 或：一次性问答 / 流式输出
变量 简短 = 简单问(代理值, "用一句话介绍 qi");
流式问(代理值, "详细解释 LLVM", 我的块回调);   // 块回调: 函数(字符串): 整数

// 流式 + 工具：边流式吐字、边自动 dispatch tool_call（一行就够）
变量 完整 = 流式运行(代理值, "东京几点了？顺便看看天气", 我的块回调);

关闭代理(代理值);
```

## Harness engineering 三件套

### 1. 可观测 —— lifecycle 总线 + opt-in adapters

非流式 `运行`、`简单问` 和结构化输出会发 canonical lifecycle v1。总线在进程内同步交付，
按注册顺序调用 `函数(事件JSON):整数` 订阅者；`隔离失败` 只能隔离回调返回 `0`，不能捕获 panic。
当前 Agent 事件包括 `agent_start/end`、`turn_start/end`、`llm_start/end` 和 `tool_start/end`。
流式、retry、budget、context 和 checkpoint 的完整 lifecycle 事件尚未接入。

```qi
导入 Harness.事件::{清空生命周期订阅者};
导入 Harness.追踪::{安装生命周期追踪适配器, 设置输出文件};
导入 Harness.报告::{安装生命周期报告适配器, 文本报告};

设置输出文件("/tmp/agent-lifecycle.jsonl");
安装生命周期追踪适配器();
安装生命周期报告适配器("任务");
变量 回复 = 运行(代理值, "完成任务");
打印行(文本报告());
清空生命周期订阅者();
```

trace/report adapters 需要显式安装，避免与 Agent 仍保留的 legacy trace 直写重复。旧 span API 仍可用：

```qi
导入 Harness.追踪::{新追踪, 开始跨度, 结束跨度, 设置输出文件};

新追踪();                                  // 生成 trace_id，之后事件/span 都归它
设置输出文件("/var/log/agent.jsonl");       // sink 切文件（默认 stderr）
变量 父: 整数 = 开始跨度("task", "助手", "跑任务", 0);
变量 子: 整数 = 开始跨度("llm", "助手", "调模型", 父);
// ... 干活 ...
结束跨度(子, "助手", "llm", "返回", 1234, 0);   // → {"span":2,"dur_ms":842,"tokens":1234,...}
结束跨度(父, "助手", "task", "完成", 1234, 0);
```
生产：从文件转发到 Loki / Langfuse / OTLP collector。演示：`qi run examples/追踪_跨度测.qi`

### 2. 可重试 —— 错误分类 + 熔断 + 限流

`创建代理` 默认让每个**非流式**模型调用最多尝试 3 次（首次调用计入 3 次），包括 `简单问`、`运行` 的首调和每次
工具续调、结构化输出。可用 `设置重试策略(代理, 策略)` 覆盖；流式调用暂不自动重试，避免
已经输出片段后从头重放。

每个 `创建代理` 默认创建独立熔断/限流资源；需要共享 provider 配额时才显式共享：

```qi
导入 Harness::{创建代理, 共享重试资源};
导入 Harness.重试::{创建重试资源, 配置资源熔断, 配置资源限流};

变量 资源 = 创建重试资源();
配置资源熔断(资源, 5, 30000);
配置资源限流(资源, 10, 20);
变量 代理甲 = 共享重试资源(创建代理("甲", 会话甲), 资源);
变量 代理乙 = 共享重试资源(创建代理("乙", 会话乙), 资源);
```

旧的 `配置熔断`、`配置限流` 和 `智能重试调用` 兼容 API 仍使用进程级资源 `0`。
`错误类别` 把响应分成 **0 成功 / 1 可重试(429·5xx·超时·网络) / 2 致命(401·403·4xx)**——
致命错误立即失败不浪费退避；可重试才 backoff。演示：`qi run examples/重试_熔断测.qi`

## 工具执行管道

`派发` 当前执行：查找工具 → 参数必须为 JSON 对象 → before hook → 本地处理器或 MCP →
after hook → 回写。它尚未执行完整 JSON Schema 关键字校验；未知工具、无效对象和 `[harness]`
处理器/hook 错误会转换为结构化工具错误字符串，但框架不能捕获 handler 或 hook panic。

```qi
导入 Harness.工具::{设置执行模式, 设置工具钩子};

代理值 = 添加工具(代理值, 新建 工具 {
    名字: "查库存",
    描述: "读取当前库存",
    参数schema: "{\"type\":\"object\"}",
    处理: 查库存,
});
代理值.工具表 = 设置执行模式(代理值.工具表, "查库存", 0); // 0 并发，1 串行
代理值.工具表 = 设置工具钩子(代理值.工具表, "查库存", 前钩子, 后钩子);
```

本地工具默认并发，MCP 固定串行。一批 tool calls 只有在全部已放行且都是本地并发工具时才
整批并发，否则整批按原顺序串行；结果按原 ToolCall 顺序回写。取消、进度和工具超时尚未实现，
写工具应显式设为串行。

### 3. 可评估 —— 四种打分器 + 数据集 + 基线回归

```qi
导入 Harness.评估::{创建套件, 添加测例, 添加测例带方式, 添加裁判测例, 运行套件, 保存基线, 对比基线};

变量 套件 = 创建套件();
套件 = 添加测例(套件, "天气", "东京天气", "Tokyo");            // 方式0 包含
套件 = 添加测例带方式(套件, "编号", "给个订单号", "[0-9]+", 2);  // 方式2 正则
套件 = 添加裁判测例(套件, "礼貌", "跟用户打招呼", "必须是友好的问候语");  // 方式3 LLM裁判
套件 = 运行套件(套件, 被测代理, 裁判会话);   // 裁判会话=0 表示不用 LLM 裁判
// → "===== 结果: 3 PASS / 0 FAIL（通过率 100%）====="

保存基线(套件, "基线.txt");
变量 回归数 = 对比基线(套件, "基线.txt");   // 打印 回归/修复/新增；>0 时 CI 应失败
```
四种打分方式：**0 包含 · 1 精确 · 2 正则 · 3 LLM-as-judge**（裁判按 rubric 判 PASS/FAIL）。
还能 `从JSON加载` 数据集。演示：`qi run examples/评估_打分测.qi`（离线）、`examples/评估_裁判.qi`（需 LLM）

## Loop Engineering（循环工程）

灵感来自 [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering)：
杠杆点从「**手写 prompt**」上移到「**设计驱动 agent 的系统**」——不再每次手敲 prompt，
而是搭一个**目标驱动的递归循环**让它自动迭代直到达成或升级：

```
制造者(maker) 干活  →  校验者(checker) 验收  →  据反馈再来一轮
                       ↑________________________|
       直到：完成 / 校验者升级给人类(human gate) / 达到最大轮数
```

复用既有外壳：每轮打 trace（可观测）、跨轮累积「状态」当记忆。
**自治级别**：L1 报告级（只跑一轮）· L2 协助级（不确定就升级）· L3 自动级（尽量自主）。

```qi
导入 Harness.循环::{运行循环, 循环结果, 循环结果转文本};

// 干活代理带工具（用 运行：自动 tool dispatch）；校验代理只读（用 简单问：只验收）
变量 结果值: 循环结果 = 运行循环(干活代理, 校验代理, "把所有高优先级待办都补上截止日期", 6, 3);
变量 报告: 字符串 = 循环结果转文本(结果值);
打印行(报告);   // 结局：完成 / 升级 / 超限 + 每轮过程记忆
```

跑：`QI_LLM_KEY=sk-... qi run examples/循环_迭代标语.qi`

## 图式控制流（LangGraph-lite）

线性 agent 循环做不了「按中间结果决定走哪条分支」。`图` 模块把执行建成一张有向图：
**节点** = 状态转换 `函数(状态JSON):字符串`；**边** = 跳转，可带条件 `函数(状态JSON):整数`；
执行器从起点走到 `终点()` 或撞步数上限。分支 / 回环 / 汇合都能表达。

```qi
导入 Harness.图::{图, 创建图, 添加节点, 添加边, 添加条件边, 运行图, 终点};

变量 g: 图 = 创建图();
g = 添加节点(g, "取数", 我的取数);            // 函数(字符串):字符串
g = 添加节点(g, "判断", 判断);
g = 添加边(g, "取数", "判断");                 // 无条件边
g = 添加条件边(g, "判断", 需要重试, "取数");    // 命中则回环（条件在前）
g = 添加边(g, "判断", 终点());                 // 兜底无条件边放最后
变量 末态: 字符串 = 运行图(g, "取数", "{}", 20);  // 末参=步数上限，防回环死循环
```

节点内部想调 LLM/工具，就在处理函数里用 `运行(代理, ...)` —— 图负责编排，agent 负责干活。
每步打 `graph_node`/`graph_edge` trace。演示（无需 LLM）：`qi run examples/图_计数循环.qi`

## 语义记忆（embedding 检索）

`记忆` 的回忆是 SQL `LIKE '%词%'` 词法匹配，同义不同词召不回。`向量记忆` 用**语义**替掉它：
写入时把内容送 embeddings API 拿向量存进 SQLite，回忆时把查询也 embed、按**余弦相似度**取 top-N。
搜索走 `标准库.向量索引` 的内存态精确 top-K；写入时增量加入索引，打开库时从 SQLite 全量重建。
当前实现未承诺固定吞吐、延迟或数据规模上限，使用方应按自己的数据量和硬件做基准测试。

```qi
导入 Harness.向量记忆::{打开向量库, 语义记住, 语义回忆, 关闭向量库};

变量 库: 整数 = 打开向量库("/tmp/agent向量记忆.db");
语义记住(库, 端点, 密钥, "text-embedding-v4", "经验", "用连接池提高吞吐", "性能,吞吐", 4);
打印行(语义回忆(库, 端点, 密钥, "text-embedding-v4", "怎样让系统跑得更快", 3));
// → 尽管查询与记忆无共同词，连接池/缓存类经验按相似度排在最前
关闭向量库(库);
```

> ⚠ 嵌入端点/模型跟对话模型**分开配**（DeepSeek 无 /embeddings；OpenAI 用 text-embedding-3-small、
> 智谱 embedding-3、阿里云百炼 text-embedding-v4、ollama nomic-embed-text）。端点传**完整** `.../embeddings` URL。

演示：`QI_EMBED_URL=... QI_EMBED_KEY=... QI_EMBED_MODEL=text-embedding-v4 qi run examples/向量记忆_语义检索.qi`

## 上下文窗口管理

LLM 会话历史随轮次无界增长，长对话迟早撑爆上下文窗口。`上下文` 给两种策略：

```qi
导入 Harness.上下文::{估算令牌, 历史令牌估算, 需要压缩, 滑窗裁剪, 摘要压缩};

// 机械滑窗（无 LLM、快）：保留 system + 最近 N 条，丢更早的
如果 (需要压缩(会话, 3000) == 1) { 滑窗裁剪(会话, 8); }

// 智能摘要（调一次 LLM）：把最早那批摘要成一条 system，再接最近 M 条 —— 省 token 不丢要点
// 摘要会话是另开的同配置会话，仅供摘要调用（别传主会话，否则污染其历史）
摘要压缩(会话, 摘要会话, 6);
```

靠 runtime 新增的 `大模型.历史JSON` / `大模型.设置历史JSON` 读写整段历史，策略全在 Qi 侧。
令牌数是**估算**（无内置 tokenizer，按字节折算，偏保守），用于阈值判断够用、别当计费依据。
演示：`qi run examples/上下文_滑窗测.qi`（无需 LLM）、`examples/上下文_摘要压缩.qi`（需 LLM）

## 断点续跑 + Human-in-the-loop（图检查点）

内存版 `运行图` 一崩全丢；长任务 / 要人工审批的流程需要状态落库、崩溃可 resume、
节点可暂停等人工。`图` 模块把这些做进执行器（SQLite 检查点，纯 Qi）：

```qi
导入 Harness.图::{运行图带检查点, 恢复图, 是中断, 中断问题, 是完成};
导入 标准库.数据库;

变量 库: 整数 = 数据库.连接("/tmp/流程.db");
变量 结果值: 字符串 = 运行图带检查点(g, 库, "任务001", "起点", "{}", 50);
如果 (是中断(结果值) == 1) {          // 某节点发起了中断（在返回状态里设 "__中断__"）
    打印行("需要人工：" + 中断问题(结果值));
    // …拿到人工答复后（可以是另一个进程/另一天）…
    结果值 = 恢复图(g, 库, "任务001", "批准");   // 注入 "__人工回复__" 从断点续跑
}
如果 (是完成(结果值) == 1) { 打印行("完成"); }
```

每步状态落 `图检查点` 表，`运行ID` 存在检查点就自动续跑——进程崩了重跑同一 `运行ID` 即从断点继续。
节点想暂停就在返回状态里设 `"__中断__": "<问题>"`；下游节点读 `"__人工回复__"`。演示：`qi run examples/图_检查点HITL.qi`

## 结构化输出（JSON 模式）

`结构化运行` 使用 provider 端 `response_format=json_object` 约束，并在本地校验合法 JSON 对象和
顶层必需字段；不合规时会在 `最大步数` 内向模型发纠正提示后重试：

```qi
导入 Harness.代理::{结构化运行, 结构化运行严格};
变量 必需 = 列表库::创建字符串列表();
列表库::添加字符串(必需, "城市"); 列表库::添加字符串(必需, "人口");
变量 回复 = 结构化运行(代理值, "给出北京的城市名和人口", 必需);   // → {"城市":"北京","人口":21540000}

// 支持 json_schema strict 的 provider（如新版 OpenAI）可传完整 schema：
结构化运行严格(代理值, 提示, "{\"type\":\"json_schema\",\"json_schema\":{...}}");
```

`结构化运行严格` 只把 schema 交给 provider，一次调用后原样返回结果；框架当前**不在本地执行
JSON Schema 验证，也没有结构化纠正循环**。provider 不支持 strict schema 时的实际行为由 provider/runtime 决定。

## 成本 / 真实 token 用量

`运行`/`简单问` 结束会打一条 `usage` trace 事件，token 数是 **provider 返回的真值**
（读 runtime 的 `大模型.用量(会话)`，不再是手填假值）：

```qi
导入 标准库.大模型 作为 底层模型;
简单问(代理值, "介绍北京");
打印行(底层模型.用量(会话));   // {"prompt":15,"completion":248,"total":263}
```

## 预算强制（token / 成本硬上限）

`大模型.用量` 能捕获真实 token，但"捕获 ≠ 管控"。`预算` 给一个账本：累加每次调用的真实
token、设上限、达到上限即停止下一次调用——防 agent 循环跑飞烧钱。预算附加到代理后，
每次成功的非流式 provider 请求（包括工具续调）都会自动记入；流式调用暂不自动记账。

```qi
导入 Harness::{附加预算};
导入 Harness.预算::{创建预算, 超预算, 预算报告};
变量 预算 = 创建预算(100000, 0);        // 令牌上限 10 万，成本上限 0=不限
代理值 = 附加预算(代理值, 预算);          // 后续简单问/运行/结构化输出自动累计
变量 回复 = 运行(代理值, "完成任务");
如果 (超预算(预算) == 1) { 打印行("预算已达到上限"); }
打印行(预算报告(预算));                  // 预算[令牌 196/100000 成本(分) 0]
```
演示：`qi run examples/预算_测.qi`（离线）；handoff 示例里跨 3 会话累加真实 token。

## SQLite 持久会话

`会话存储` 使用 SQLite 保存 append-only entry 树和 current leaf。移动 leaf 后追加会形成新分支，
旧分支不会删除。Agent 用 `附加会话存储` 记录非流式用户消息、provider 原始响应、工具结果、
最终展示消息和错误；新 runtime 会话通过 `恢复持久会话` 显式恢复所选 root-to-leaf 路径。

```qi
导入 Harness.代理::{代理, 附加会话存储, 恢复持久会话};
导入 Harness.会话存储::{打开会话存储, 关闭会话存储,
    导出持久会话JSON, 导入持久会话JSON};

变量 库 = 打开会话存储("/tmp/agent-sessions.db");
变量 代理值: 代理 = 附加会话存储(
    创建代理("助手", 开启会话(配置)), 库, "session-123");
变量 回复 = 运行(代理值, "记住这条信息");

变量 新代理: 代理 = 附加会话存储(
    创建代理("助手", 开启会话(配置)), 库, "session-123");
变量 恢复条数 = 恢复持久会话(新代理);
变量 导出JSON = 导出持久会话JSON(库, "session-123");
关闭会话存储(库);
```

导入/导出格式版本为 `1`；导入先验证 parent 顺序、leaf 引用和 ID 冲突，再用单事务写入。
`打开内存会话存储()` 也可创建进程内 SQLite `:memory:` 存储，关闭唯一句柄后内容消失；它不是独立
的内存 backend。当前没有 JSONL backend。恢复不自动应用模型/配置/摘要 entry，流式调用也不自动记录，
存储不提供内建加密或通用认证策略。

资源所有权：`创建代理` 接管传入的 runtime 会话，并在 `关闭代理` 时关闭它和该代理自建的重试资源；
`附加会话存储` 不接管数据库句柄，调用方仍需在所有关联代理关闭后调用 `关闭会话存储`。共享重试资源、
共享重试资源和 MCP 连接由创建/共享它们的调用方释放或关闭；预算句柄不归 Agent 所有，当前也没有
独立释放 API。调用方应避免重复关闭或在关联 Agent 仍运行时提前关闭资源。

## 多智能体协作（团队 + handoff）

单 agent 干不了"分诊/专家协作"。`多代理` 给去中心的 **handoff**：一组各有专长的成员
（名字 + 带角色 system 提示的会话），谁觉得另一位更合适就在回复里输出 `转交给:<成员> <话>`，
团队循环据此切换过去继续。

```qi
导入 Harness.多代理::{团队, 创建团队, 添加成员, 运行团队};
变量 团队值 = 创建团队();
团队值 = 添加成员(团队值, "分诊", 分诊会话);   // system: 你负责判断该谁处理，务必转交
团队值 = 添加成员(团队值, "退款", 退款会话);
团队值 = 添加成员(团队值, "技术", 技术会话);
变量 回复 = 运行团队(团队值, "分诊", "我要申请退款", 3);
// 分诊 → 转交给 退款 → 退款给出最终答复
```
每次应答/转交打 `handoff` trace。演示：`qi run examples/多代理_分诊.qi`（需 LLM）

## 护栏（注入防御 / PII 脱敏 / 输出守卫 / 工具参数策略）

文件沙箱只管住了文件工具；面向用户的 agent 还需要几道安全护栏（工具层强制，模型绕不过）：

```qi
导入 Harness.护栏::{检测注入, 脱敏, 校验输出, 工具参数放行, 守卫运行};

如果 (检测注入(用户输入) == 1) { 拒绝; }        // 挡"忽略以上指令/你现在是…"越权改写
变量 干净 = 脱敏(回复);                          // 邮箱/手机/长号码 → [邮箱][手机][号码]
校验输出(输出, 必需字段);                         // 输出必须是含指定字段的合法 JSON
工具参数放行(参数JSON, 拒绝词表);                 // 挡 "DROP TABLE" / "../" / "rm -rf" 等

// 一步到位：入口挡注入 + 出口脱敏，套在 代理.运行 外
变量 回复 = 守卫运行(代理值, 用户输入);
```
演示：`qi run examples/护栏_测.qi`（离线 12/12）

## 可观测：OTLP 导出

span 除了打 stderr/文件，还能导到 **OpenTelemetry collector**（Jaeger / Tempo / Grafana / Langfuse）：

```qi
导入 Harness.追踪::{设置OTLP, 新追踪, 开始跨度, 结束跨度};
新追踪();
设置OTLP("http://localhost:4318");    // 每个完成的 span POST 到 {基址}/v1/traces
变量 s = 开始跨度("llm", "助手", "调模型", 0);
结束跨度(s, "助手", "llm", "返回", 1234, 0);   // → 标准 OTLP resourceSpans JSON
```
trace_id 32-hex、span_id 16-hex、纳秒时间戳、attributes(agent/tokens/cost)——标准 OTLP/HTTP，
真实 collector 直接可摄入。

## 文件系统工具（沙箱 + 权限）

harness 不内置裸文件 tool（fs 无沙箱 = agent 拿到进程全部权限，prompt 注入即可读 `~/.ssh` / 删库）。
`文件工具` 模块给一个**受控外壳**：所有路径钉死在沙箱根目录内，权限按需逐项放开。

```qi
导入 Harness.文件工具::{文件沙箱, 创建文件沙箱, 允许写, 允许删除, 设置读上限, 装备文件工具};

变量 箱: 文件沙箱 = 创建文件沙箱("/srv/agent工作区");   // 默认只读
箱 = 允许写(箱);              // 放开 写/追加/建目录
箱 = 允许删除(箱);            // 放开 删除文件/目录
箱 = 设置读上限(箱, 65536);   // 单次读文件 ≤64KB，防爆 context

代理值 = 装备文件工具(代理值, 箱);   // 按权限注册工具：只读沙箱连「写文件」工具都不会出现
变量 回复 = 运行(代理值, "在 notes/计划.md 写三条本周计划");
```

**安全层（工具层强制，模型绕不过）**：

- 拒绝绝对路径（`/etc/passwd`）、家目录（`~/...`）、任何 `..` 穿越段
- 拼进沙箱根后 **canonicalize**，必须仍以「根 + `/`」为前缀 —— 连**符号链接逃逸**都挡
- **最小权限**：只读沙箱里模型根本看不到 写/删 工具；可写/可删要显式放开
- 读文件可设字节上限，避免大文件爆掉上下文

确定性安全自测（无 LLM，10 条断言）在 [`qi-test`](../qi-test)：`用例/文件沙箱_测.qi`
LLM 实跑演示：`QI_LLM_KEY=sk-... qi run examples/文件助手.qi`

> ⚠️ 工具内部只做路径围栏，不做内容审查；给 agent 文件权限前自己评估根目录选址（别把 `/` 或家目录当沙箱根）。

## Provider 支持

任何 OpenAI 兼容 API 都用 `大模型(baseurl, key, model)` 一行配置：

```qi
大模型("https://api.openai.com/v1",       密钥, "gpt-4o-mini")
大模型("https://api.deepseek.com",        密钥, "deepseek-chat")
大模型("https://api.moonshot.cn/v1",      密钥, "moonshot-v1-8k")
大模型("https://open.bigmodel.cn/api/paas/v4", 密钥, "glm-4")
大模型("http://127.0.0.1:11434/v1",       "ollama", "llama3.1")    // ollama 本地
大模型("http://127.0.0.1:8000/v1",        "x",  "Qwen2.5")          // vllm 本地
```

baseurl 跟模型名是字面值，写多少 provider 都 OK，不需要内置常量。
密钥从环境变量读，不要硬编码：`变量 密钥 = 系统.获取环境变量("QI_LLM_KEY");`

## 运行示例

```bash
QI_LLM_KEY=sk-... qi run examples/deepseek测试.qi      # 单轮问答
QI_LLM_KEY=sk-... qi run examples/deepseek工具.qi      # 工具调用（并行 tool_calls）
QI_LLM_KEY=sk-... qi run examples/deepseek流式.qi      # 流式输出
QI_LLM_KEY=sk-... qi run examples/deepseek流式工具.qi  # 流式 + 工具（边吐字边调工具）
```

## 开发者 CLI

当前 CLI 是最小诊断和 SQLite 会话查看器，不是交互式 Agent runner，也不会恢复后继续调用模型：

```bash
qi run cmd/qi-harness.qi -- check
qi run cmd/qi-harness.qi -- test
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session list
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session show session-123
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session path session-123
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session export session-123 session.json
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session import session.json
qi run cmd/qi-harness.qi -- --db /tmp/agent-sessions.db session branch session-123 entry-456
```

- `check` 只检查 provider 环境变量是否按仓库约定配置，不打印 secret，也不发网络请求。
- `test` 只显示质量门禁命令；为了保留真实退出状态，应在仓库根运行 `./run-offline-tests.sh`。
- `session list/show/path` 只读 SQLite；`export` 导出完整会话及所有分支，`import` 导入版本 1 JSON，
  `branch` 把 current leaf 移到现有 entry，下一次追加由该点形成新分支。数据库路径优先级是
  `--db`、`QI_SESSION_DB`、`~/.qi-harness-sessions.db`。

## 质量门禁

仓库统一离线入口：

```bash
./run-offline-tests.sh
```

当前门禁执行 diff whitespace 检查、核心/服务/服务示例语法、离线示例、生命周期总线与 Agent lifecycle、
trace/report adapters、工具管道与调度、run context、SQLite 会话持久化与导入导出、Agent 恢复/记录、
CLI、HTTP 持久会话、文件沙箱/报告/检索配置隔离和 M1 reliability fixture。需要 `qi` 在 `PATH`；
任何已接入 suite 失败都会非零退出。GitHub Actions 已在 Ubuntu 22.04 和 macOS 14 上运行同一门禁。
真实 provider smoke test 不是该离线门禁的一部分。

## HTTP 服务当前语义

`Harness.代理服务` 目前是简单常驻适配器：`GET /health`、`POST /chat`、`POST /json`。
`/chat` 在已注册服务工具时走 `运行`，否则走 `简单问`；`/json` 始终走 `结构化运行`，不会装备
服务工具。调用 `配置服务持久会话(SQLite路径, 默认所有者)` 后，`/chat` 和 `/json` 使用字符串
`会话ID`：未传时生成 UUID，传入未知 ID 返回 404；每次请求创建新 runtime 会话，从 SQLite 恢复、
执行、记录并关闭。可用固定默认所有者，或由请求 `所有者` 字段匹配服务保存的 owner 映射；未知 ID、
owner 不匹配或 owner 映射缺失统一返回 404，避免通过响应枚举归属。未启用持久模式时，为兼容旧客户端
仍接受进程内整数 `会话号`。

服务默认最多接收 1 MiB 请求体和 256 KiB `提示`，可用
`配置服务请求上限(最大主体字节, 最大提示字节)` 调整；主体上限由 Web 传输层在 `Content-Length`
头到达或 chunked 累计将越界时提前拒绝，提示上限仍在 JSON 解码后执行。`GET /health` 只表示进程/路由存活并回显配置状态，不探测 provider、SQLite 或 MCP，
不能用作 readiness 保证。

```qi
导入 Harness.代理服务::{配置服务, 配置服务持久会话, 启动代理服务};
配置服务(端点, 密钥, 模型, "你是助手。");
配置服务持久会话("/tmp/service-sessions.db", "tenant-a");
启动代理服务("127.0.0.1", 6798);
```

owner 字段只是应用级相等校验，不是认证机制。服务配置、SQLite 句柄和工具集合仍是模块级全局状态；
当前没有 credential 验证、中间件认证、会话关闭/删除路由、SSE/WebSocket lifecycle 输出或实例级隔离；
同一会话并发请求使用进程内服务共享的 SQLite 锁表并返回 409，但不等于分布式锁或完整并发策略。
服务还缺少优雅停机/资源清理、TLS 和跨实例协调。持久模式优于暴露 runtime
handle，但仍不能据此宣称生产就绪。

## 当前限制

- 自动重试、真实 usage 自动记账、预算阻止、lifecycle 和 SQLite Agent 记录主要覆盖非流式 API。
- 流式 API 仍走旧 trace 事件，不自动重试/记预算/持久化，也未接入 canonical lifecycle 总线。
- lifecycle 已支持显式事件总线句柄，但 Agent 目前固定发布到默认总线，无法把自定义 bus 注入单个 Agent；
  事件 payload 多数仍是空对象。legacy trace sink 和兼容无句柄报告 API 仍有进程级默认状态。
- 工具管道没有完整 JSON Schema、取消、进度、超时或 panic 隔离。
- 文件沙箱、工具白名单和参数护栏是应用级约束，不是 OS/container 安全边界。
- HTTP persistent 模式有不透明 UUID 和 owner 相等校验，但无真正认证、资源清理路由或实例隔离。
- 流式块回调的整数返回值当前被忽略，不能用返回 `0` 取消；`流式运行` 的一批工具调用固定串行派发。
- 检索支持显式不可变配置句柄及按配置 API，隔离测试已进入门禁；旧 `配置检索`/`检索` API 仍委托给进程级默认配置。
- HTTP adapter 与最小 CLI 仍面向开发和诊断；发布、迁移和多租户生产能力属后续工作。

## 跟其他 qi-* 项目的关系

| 项目 | 定位 |
|---|---|
| `qi`（编译器） | qi 语言本身 + LLVM codegen + stdlib runtime |
| `qi-web` | HTTP 框架 |
| `qi-cli` | CLI 框架（Cobra 等价） |
| **`qi-harness`** | **LLM agent 框架（本项目）** |
| `qi-lsp` | 语言服务器 |
| `qi-tools` | qifmt 等开发工具 |
| `qi-gui` | GUI 库（FFI 链接到 qi-compiler） |

## License

MIT
