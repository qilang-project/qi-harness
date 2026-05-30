# qi-harness MCP 客户端

qi-harness 内置 MCP（Model Context Protocol）客户端，可把外部 MCP server 的工具拉进 agent 的工具循环。底层依赖 `标准库.MCP客户端`（Rust 核心，`qi_mcpc_*` FFI），协议/会话逻辑在 Rust 侧，Qi 层通过 `MCP客户端.qi` 提供中文封装。

## 传输

连接返回一个**描述符字符串**（`"mcpc|<id>"`），后续所有调用都用它：

```qi
导入 Harness::{ 连接MCP_stdio, 连接MCP_http, 装备MCP, 关闭MCP };

// 1) stdio：本进程 spawn server 子进程
变量 描述符 = 连接MCP_stdio("npx", "[\"-y\",\"@playwright/mcp@latest\"]");
//  → "mcpc|1"

// 2) HTTP（Streamable HTTP）：连已在监听的 server
//    server: npx -y @playwright/mcp@latest --port 43528
变量 描述符2 = 连接MCP_http("http://localhost:43528/mcp");
//  → "mcpc|2"
```

`连接*` 内部完成 `initialize` 握手 + `notifications/initialized`；失败返回空串 `""`。

## 出站能力（Client → Server）

| 方法 | 作用 |
|---|---|
| `MCP列出工具(描述符)` | tools/list |
| `MCP调用工具(描述符, 工具名, 参数JSON)` | tools/call |
| `MCP列出资源(描述符)` / `MCP读取资源(描述符, URI)` | resources/list · resources/read |
| `MCP列出提示(描述符)` / `MCP获取提示(描述符, 名字, 参数JSON)` | prompts/list · prompts/get |
| `MCP补全(描述符, 参数JSON)` | completion/complete |
| `关闭MCP(描述符)` | 关闭连接 |

把整台 server 的工具一次性装进 agent：

```qi
变量 代理值 = 创建代理("助手", 会话);
代理值 = 装备MCP(代理值, 描述符);   // tools/list → 逐个注册为 agent 工具
变量 回复 = 运行(代理值, "...");      // 模型调工具时自动派发到 MCP
```

`派发` 按工具来源分流：MCP 工具转发到 `MCP调用工具`，本地工具走函数指针。

## 入站能力（Server → Client，双向，仅 stdio）

Rust 核心在 stdio 连接上启动后台 reader，使客户端既能发请求、也能处理服务器主动发来的请求/通知。

### API

```qi
导入 Harness::{ 设置采样处理, 设置根目录, 取通知, 设置询问处理 };
```

| 方法 | 作用 |
|---|---|
| `设置采样处理(描述符, 处理)` | 注册 `sampling/createMessage` 处理器（Qi 函数 `(字符串): 字符串`） |
| `设置根目录(描述符, 根JSON)` | 设置 `roots/list` 返回的根目录数组 JSON |
| `取通知(描述符)` | 排空已缓冲的服务器通知，返回 JSON 数组串 |
| `设置询问处理(描述符, 处理)` | 注册 `elicitation/create` 处理器 |

处理函数签名统一为 `函数(参数JSON: 字符串): 字符串`：输入为服务器发来的请求 params JSON，输出为符合 MCP 规范的响应 JSON。

### 采样处理示例

```qi
// stub 采样处理器（不需要真实 LLM）
函数 采样处理(参数JSON: 字符串) : 字符串 {
    返回 "{\"role\":\"assistant\",\"content\":{\"type\":\"text\",\"text\":\"来自Qi的采样\"},\"model\":\"qi-stub\",\"stopReason\":\"endTurn\"}";
}

函数 入口() {
    变量 描述符: 字符串 = 连接MCP_stdio("npx", "[\"-y\",\"@modelcontextprotocol/server-everything\"]");
    设置采样处理(描述符, 采样处理);

    // 调用 trigger-sampling-request（args: prompt + maxTokens）
    // 服务器会向客户端发出 sampling/createMessage，客户端调用 采样处理 并回写结果
    变量 结果: 字符串 = MCP调用工具(描述符, "trigger-sampling-request",
        "{\"prompt\":\"测试\",\"maxTokens\":50}");
    IO.打印行(结果);   // 含 "来自Qi的采样"

    变量 通知: 字符串 = 取通知(描述符);  // "[{\"jsonrpc\":\"2.0\",\"method\":\"notifications/...\"}]"
    IO.打印行(通知);
    关闭MCP(描述符);
}
```

完整可运行示例见 `examples/mcp_采样.qi`。

## 服务端（标准库.MCP服务器）

`标准库.MCP服务器` 是另一独立模块，用于在 Qi 程序里**实现** MCP server。功能包括：

- 工具/资源/提示注册与处理
- 通知推送：`通知工具变更` / `日志消息` / `通知进度`
- `logging/setLevel` 动态日志级别
- HTTP 传输：GET /mcp SSE 事件流（服务端→客户端推送）

详见 `qi/示例/标准库/MCP服务器/`。

## HTTP 传输注意事项

HTTP（Streamable HTTP）传输适合背靠背/简单调用场景，但有一个已知限制：

> Playwright MCP 等工具的 HTTP 模式会把 MCP 会话绑定到 TCP 连接。在**有多秒 LLM 间隙的 agent 循环**中，连接可能在服务器空闲超时后断开，导致下一次调用丢失会话。
> **agent 循环请优先使用 stdio 传输**；HTTP 适合无间隙的后台批量/简单调用。

其他限制：HTTP host 必须为 `localhost`（`127.0.0.1` 返回 403）。

## 已验证一致性（Conformance）

| 测试组合 | 结果 |
|---|---|
| 客户端（stdio）× `@modelcontextprotocol/server-everything` tools/resources/prompts | ✓ |
| 客户端（stdio）× `server-everything` 完整采样往返（`trigger-sampling-request` → Qi 处理器） | ✓ |
| 客户端（HTTP）× Playwright MCP server（tools/call 含 SSE 大响应） | ✓ |
| 服务端 × 独立 Python MCP 客户端 | ✓ |
| 服务端 × curl JSON-RPC | ✓ |

## 明确不支持（已知后续事项）

- **HTTP 服务端→客户端双向**：客户端通过 GET /mcp SSE 接收服务器推送（HTTP 模式下 `设置采样处理` 不生效）。
- **`resources/subscribe`**：资源订阅协议。

## 示例

- `examples/mcp_采样.qi` — 双向采样一致性示例（无需 LLM 密钥）
- `examples/mcp测试.qi` — stdio tools/list
- `examples/mcp_http导航.qi` — HTTP 传输 + agent 跑工具循环
- `examples/mcp_资源.qi` — resources/prompts 调用通路
- `examples/mcp导航.qi` — stdio agent 导航
- `examples/seo审计/` — qi-web + qi-cli + qi-harness 综合：URL → Playwright → SEO/GEO 评分
