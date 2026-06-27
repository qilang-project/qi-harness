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

## Harness engineering 三件套

1. **可观测**：每次 LLM 调用 / 每次 tool 派发 / 错误 / 完成都打 JSON 行到 stderr。
   ```json
   {"ts":1746649800123,"type":"llm_call","agent":"我的助手","detail":"东京天气..."}
   {"ts":1746649801432,"type":"tool_call","agent":"我的助手","detail":"查天气({\"city\":\"Tokyo\"})"}
   {"ts":1746649801435,"type":"tool_result","agent":"我的助手","detail":"查天气 → {\"temp\":18}"}
   {"ts":1746649802100,"type":"done","agent":"我的助手","detail":"东京现在 18°C，晴。"}
   ```

2. **可重试**：`重试::默认策略()` + `重试::重试调用` 自动 backoff。429 / 超时 / 5xx 不再炸。

3. **可评估**：
   ```qi
   变量 套件: 评估套件 = 创建套件();
   套件 = 添加测例(套件, "天气问题", "东京天气", "Tokyo");
   套件 = 添加测例(套件, "时间问题", "现在几点", "2026");
   套件 = 评估::运行(套件, 代理值);
   // → "===== 结果: 2 PASS / 0 FAIL ====="
   ```

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
