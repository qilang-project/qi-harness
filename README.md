# qi-harness

LLM agent 框架，基于 [Martin Fowler — Harness Engineering](https://martinfowler.com/articles/harness-engineering.html) 模式。

跟 `qi-cli`（命令行）/ `qi-web`（HTTP 服务）平级，专门给 LLM agent 开发场景：把模型调用包在**可观测 + 可重试 + 可评估**的外壳里。

## 项目结构

```
qi-harness/
├── qi.toml            # 包配置
├── Harness.qi         # 入口（re-export 全部公开模块）
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
    模型配置, 大模型, 配置系统提示, 开启会话,
    创建代理, 添加工具, 运行, 简单问, 流式问, 流式运行, 关闭代理,
    工具
};

// 1. 一行配置 — 任何 OpenAI 兼容 API
变量 配置 = 大模型("https://api.deepseek.com", 密钥, "deepseek-chat");
配置 = 配置系统提示(配置, "你是助手。");

// 2. 开会话 + 创建 agent
变量 会话: 整数 = 开启会话(配置);
变量 代理值: 代理 = 创建代理("我的助手", 会话);

// 3. 注册工具（可选）
代理值 = 添加工具(代理值, (工具 {
    名字: "查天气",
    描述: "查指定城市天气",
    参数schema: "{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}}}",
    处理: 我的天气函数,
}));

// 4. 跑 — 自动 tool dispatch loop（支持并行 tool_calls）+ trace
变量 回复 = 运行(代理值, "东京天气怎样？");

// 或：一次性问答 / 流式输出
变量 简短 = 简单问(代理值, "用一句话介绍 qi");
流式问(代理值, "详细解释 LLVM", 我的块回调);   // 块回调: 函数(字符串): 整数

// 流式 + 工具：边流式吐字、边自动 dispatch tool_call（一行就够）
变量 完整 = 流式运行(代理值, "东京几点了？顺便看看天气", 我的块回调);

关闭代理(代理值);
```

## Harness engineering 三件套（工业下沿）

### 1. 可观测 —— 事件 + span 调用树 + 文件 sink

每次 LLM 调用 / tool 派发都带 `trace_id` 打 JSON 行；**span**（开始/结束配对）自动算
`dur_ms`，可记 token / cost，串成一棵调用树。JSON 转义已修（之前 detail 含引号/换行会打烂行）。

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

```qi
导入 Harness.重试::{默认策略, 智能重试调用, 错误类别, 配置熔断, 配置限流};

配置熔断(5, 30000);        // 连续失败 5 次 → 断路打开，冷却 30s 内快失败
配置限流(10, 20);          // 令牌桶：10 QPS、突发 20
变量 回复 = 智能重试调用(默认策略(), "助手", 我的调用);  // 函数():字符串
```
`错误类别` 把响应分成 **0 成功 / 1 可重试(429·5xx·超时·网络) / 2 致命(401·403·4xx)**——
致命错误立即失败不浪费退避；可重试才 backoff。演示：`qi run examples/重试_熔断测.qi`

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
纯 Qi + `标准库.HTTP` + `标准库.JSON`，余弦在 Qi 里手算（不依赖数学模块）。

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

## 跟其他 qi-* 项目的关系

| 项目 | 定位 |
|---|---|
| `qi`（编译器） | qi 语言本身 + LLVM codegen + stdlib runtime |
| `qi-web` | HTTP 框架（Express+ 等价，5.5x 性能） |
| `qi-cli` | CLI 框架（Cobra 等价） |
| **`qi-harness`** | **LLM agent 框架（本项目）** |
| `qi-lsp` | 语言服务器 |
| `qi-tools` | qifmt 等开发工具 |
| `qi-gui` | GUI 库（FFI 链接到 qi-compiler） |

## License

MIT
