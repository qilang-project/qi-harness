---
name: qi-harness
description: Build LLM agents in the Qi (奇语) programming language using the qi-harness framework — an observable / retryable / evaluable wrapper around the 标准库.大模型 LLM API. Covers one-shot chat, streaming, automatic tool-call dispatch loops (parallel tool_calls), tracing events, and retry with backoff. Use when the user builds Qi LLM agents, chatbots, or tool-using assistants. Requires the qi-lang skill for base language syntax.
metadata:
  author: qilang
  version: "0.1"
---

# qi-harness — Qi 语言 LLM Agent 框架

基于 Martin Fowler "harness engineering" 思路：把 LLM 调用包在**可观测 + 可重试 + 可评估**的外壳里。底层是 `标准库.大模型`（OpenAI 兼容协议），harness 层提供 agent loop、自动工具派发、追踪、重试。

> **先读 `qi-lang` 技能**。本技能只讲 qi-harness 的 API。

## 何时使用

- 用户用 Qi 写 LLM 应用 / 聊天机器人 / 工具调用 agent
- 用户问 qi-harness 的会话、对话、流式、工具调用、追踪
- 用户提到 `导入 Harness`、`大模型(...)`、`创建代理`

## 配置与会话

一行 builder 配置任何 OpenAI 兼容端点（DeepSeek、OpenAI、本地 …）：

```qi
导入 Harness::{ 模型配置, 大模型, 配置系统提示, 开启会话, 关闭会话Harness };

变量 配置: 模型配置 = 大模型("https://api.deepseek.com", 密钥, "deepseek-chat");
配置 = 配置系统提示(配置, "你是简洁的中文助手。");
// 可选：配置温度(配置, "0.7")、配置最大令牌(配置, 2048)
变量 会话: 整数 = 开启会话(配置);   // >0 成功
// 用完：关闭会话Harness(会话)  —— 注意是 关闭会话Harness，不是 关闭会话
```

> ⚠️ API 密钥从环境变量读，**不要硬编码**：`变量 密钥 = 系统.获取环境变量("QI_LLM_KEY");`

## 代理（Agent）

```qi
导入 Harness::{ 创建代理, 简单问, 流式问, 添加工具, 运行, 关闭代理 };

变量 代理值: 代理 = 创建代理("助手", 会话);

// 1. 一次性问答（无工具）
变量 回复: 字符串 = 简单问(代理值, "用一句话介绍 qi 语言");

// 2. 流式问答 —— 块回调签名固定 函数(字符串): 整数（返回 1 继续）
函数 打印片段(片段: 字符串) : 整数 { IO.打印(片段); 返回 1; }
变量 完整: 字符串 = 流式问(代理值, "解释 LLVM", 打印片段);

关闭代理(代理值);
```

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

    变量 天气工具: 工具 = (工具 {
        名字: "天气查询",
        描述: "查询城市天气。",
        参数schema: "{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}}}",
        处理: 天气查询,
    });
    代理值 = 添加工具(代理值, 天气工具);

    // 运行：自动 detect tool_calls → 派发 → 回写结果 → 继续，直到模型给最终文本
    变量 回复: 字符串 = 运行(代理值, "北京天气怎样？");
    IO.打印行(回复);
    关闭代理(代理值);
}
```

`运行` 内部循环支持 **parallel tool_calls**：模型一轮返回多个工具调用时，逐个派发并各自 `回写工具结果`，再 `继续工具对话`。`设置最大步数(代理, N)` 防止无限循环（默认 10）。

## 追踪（可观测）

每次 LLM 调用、每次工具派发都 emit JSON 事件到 stdout（默认开）：

```qi
导入 Harness.追踪 作为 追踪;
追踪.禁用即时打印();   // 关掉实时打印（例如和流式回调输出混在一起时）
追踪.禁用();           // 完全关闭追踪
```

事件类型：`llm_call` / `llm_response` / `tool_call` / `tool_result` / `error` / `done` / `stream_start` / `stream_chunk` / `stream_end`。

## 重试

```qi
导入 Harness::{ 默认策略, 重试调用 };
// 重试调用(策略, 代理名, 调用) — 调用是 函数(): 字符串，429/网络抖动指数退避重试
```

## 底层直连（不用 agent 外壳）

```qi
导入 标准库.大模型 作为 大模型;
变量 会话 = 大模型.创建会话();
变量 回复 = 大模型.对话(会话, "你好");
```

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
