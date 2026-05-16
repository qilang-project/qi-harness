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
└── examples/
    ├── 简单问答.qi      # 单轮对话
    └── 工具助手.qi      # tool-using agent（天气 + 时间）
```

## 核心 API

```qi
导入 Harness::{
    模型配置, 默认配置, 配置模型, 配置密钥, 配置系统提示, 开启会话,
    创建代理, 添加工具, 运行, 关闭, 简单问,
    工具, 创建套件, 添加测例
};

// 1. 配置 provider
变量 配置: 模型配置 = 默认配置();
配置 = 配置模型(配置, "gpt-4o-mini");
配置 = 配置密钥(配置, 系统.读环境变量("QI_LLM_KEY"));
配置 = 配置系统提示(配置, "你是助手。");

// 2. 开会话 + 创建 agent
变量 会话: 整数 = 开启会话(配置);
变量 代理值: 代理 = 创建代理("我的助手", 会话);

// 3. 注册工具
变量 t: 工具 = (工具 {
    名字: "查天气",
    描述: "查指定城市天气",
    参数schema: "{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}}}",
    处理: 我的天气函数,
});
代理值 = 添加工具(代理值, t);

// 4. 跑 agent（自动 tool dispatch loop）
变量 回复: 字符串 = 运行(代理值, "东京天气怎样？");
关闭(代理值);
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

## Provider 支持

底层用 qi 标准库 `大模型` FFI（OpenAI 兼容 API + Anthropic 路径）。常见 provider 端点常量已内置：

```qi
配置.端点 = OpenAI端点();      // https://api.openai.com/v1
配置.端点 = Anthropic端点();   // https://api.anthropic.com/v1
配置.端点 = DeepSeek端点();    // https://api.deepseek.com/v1
配置.端点 = Moonshot端点();    // https://api.moonshot.cn/v1
配置.端点 = 智谱端点();        // https://open.bigmodel.cn/api/paas/v4
配置.端点 = 本地端点(11434);   // http://127.0.0.1:11434/v1 （ollama）
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
