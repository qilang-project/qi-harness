# qi-harness MCP 客户端

qi-harness 自带一个**纯 Qi 写的 MCP（Model Context Protocol）客户端**，可把外部 MCP server 的工具拉进 agent 的工具循环。底层依赖运行时的 `标准库.子进程`（stdio）与 `标准库.HTTP.请求`（HTTP），协议/会话逻辑全部在 Qi 侧。

## 传输

连接返回一个**描述符字符串**，后续所有调用都用它：

```qi
导入 Harness::{ 连接MCP_stdio, 连接MCP_http, 装备MCP, 关闭MCP };

// 1) stdio：本进程 spawn server 子进程
变量 描述符 = 连接MCP_stdio("npx", "[\"-y\",\"@playwright/mcp@latest\"]");
//  → "stdio|<子进程句柄>"

// 2) HTTP（Streamable HTTP）：连已在监听的 server
//    server: npx -y @playwright/mcp@latest --port 43528
变量 描述符2 = 连接MCP_http("http://localhost:43528/mcp");
//  → "http|<基址>|<会话id>"   ⚠️ host 必须 localhost（127.0.0.1 会被 403）
```

`连接*` 内部完成 `initialize` 握手 + `notifications/initialized`；失败返回空串 `""`。

## 能力

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

## 健壮性

- stdio 读取走 `子进程.读取行超时(句柄, 30000)`（后台读线程 + 队列），server 挂死不会永久阻塞。
- 不支持的方法返回结构化 JSON-RPC `error`（如 `-32601 Method not found`），不会崩。

## 明确不支持（纯 Qi 同步模型的边界）

- **服务器主动通知**（`notifications/tools/list_changed`、`progress`、`cancelled`）：当前同步请求/响应模型会跳过它们，不做实时处理。
- **双向 / 服务器→客户端请求**：`sampling/createMessage`、`roots/list`、elicitation 不支持（需要后台 reader + 入站请求分发，纯 Qi 做不了）。
- **并发 / 多 id**：JSON-RPC `id` 固定为 1，仅支持串行请求/响应。
- HTTP 传输 host 必须 `localhost`；SEO 示例的 web 层为每个请求 spawn 一个浏览器。

要做到这些需把客户端核心下沉到 Rust（`标准库.MCP客户端`）——当前刻意保持纯 Qi。

## 示例

- `examples/mcp测试.qi` — stdio tools/list
- `examples/mcp_http导航.qi` — HTTP 传输 + agent 跑工具循环
- `examples/mcp_资源.qi` — resources/prompts 调用通路
- `examples/mcp导航.qi` — stdio agent 导航
- `examples/seo审计/` — qi-web + qi-cli + qi-harness 综合：URL → Playwright → SEO/GEO 评分
