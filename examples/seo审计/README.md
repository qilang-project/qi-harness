# SEO/GEO 审计器（qi-web + qi-harness 本地综合示例）

输入 URL → Playwright MCP 真实抓取页面 → LLM 给 SEO/GEO 评分与修改建议。

> 这是绑定 `127.0.0.1` 的本地演示，不是可直接暴露到公网的抓取服务。代码仅接受
> `http/https`，并拒绝明显的 localhost、回环、链路本地、常见私网地址和含凭据 URL；这只是
> 基础拦截，**不构成完整 SSRF 防护**。生产实现还必须校验 DNS 解析结果和每次重定向、限制出站
> 网络、设置抓取超时/响应大小上限，并使用认证、限流和隔离的浏览器环境。

## 前置

- Node.js（`npx @playwright/mcp@latest`，首次会下载浏览器）
- `QI_LLM_KEY`（DeepSeek/OpenAI 兼容密钥）
- 已编译的 qi 编译器（`cargo build` 在 workspace 根执行后 `target/debug/qi` 存在）

## 包解析机制

本示例同时依赖 `Harness`（qi-harness）和 `Web`（qi-web）两个包，**无需设置 `QI_PACKAGES_PATH`**，也无需 `qi_packages/` 软链。

解析路径（自动，无需配置）：

- **Harness**：编译器沿源文件祖先目录查找含 `qi.toml`（`名称 = "Harness"`）的目录，自动找到 `qi-harness/`。
- **Web**：编译器沿祖先目录查找 `qi_packages/` 子目录，workspace 根的 `qi_packages/Web -> qi-web` 软链被自动发现。

## 运行

```bash
# 从 qi-harness/examples/seo审计 目录执行
QI=../../../target/debug/qi

# ——— 方式 A：默认 stdio（最稳，适合重型 SPA）———
# 每个请求 spawn 一个 Playwright MCP 子进程。慢但对复杂页面最可靠。
QI_LLM_KEY=sk-... $QI run 服务.qi
# 浏览器打开 http://127.0.0.1:43517/ ，填 URL → 审计

# JSON API
curl -s -X POST http://127.0.0.1:43517/api/audit \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'

# ——— 方式 B：HTTP 传输（浏览器进程复用）———
# 先起一台常驻 Playwright MCP（host 必须 localhost）：
npx -y @playwright/mcp@latest --port 43528
# 再用 QI_MCP_URL 指向它启动服务（设了就走 HTTP，没设走 stdio）：
QI_MCP_URL=http://localhost:43528/mcp QI_LLM_KEY=sk-... $QI run 服务.qi
# ⚠️ 重型 SPA 的大 browser_evaluate 在纯 Qi 的 SSE 处理下偶有 mcp_no_response，
#    遇到不稳就用方式 A（stdio）。
```

## 架构

```
浏览器表单 (GET /)
    └─ fetch POST /api/audit
           └─ 处理API() → 审计(网址)
                  ├─ 开启会话(DeepSeek配置)
                  ├─ 连接MCP_stdio(...)  或  连接MCP_http(QI_MCP_URL)   ← 传输按环境变量选择
                  ├─ 设置最大步数(代理值, 20)   ← 重型页面需要更多工具轮次
                  ├─ 装备MCP(代理值, mcp描述符)  ← 自动注册 Playwright 工具
                  └─ 运行(代理值, "审计 <url>")
                         ├─ browser_navigate → 一次 browser_evaluate 采全部信号
                         └─ 剥离 ```json 围栏并验证 JSON 与顶层必需字段后返回
```

## 注意

- **传输选择**：设了 `QI_MCP_URL` → HTTP（复用常驻 server）；否则 stdio（每请求 spawn）。两种方式都可能受页面、浏览器和 MCP 状态影响。
- **步数**：`设置最大步数(代理值, 20)`——复杂 SPA 的采集会多轮调用工具，默认 10 步会触发「tool 循环达到上限」。
- **提示**：系统提示要求模型「只用一次 browser_evaluate 一次性采全部信号、拿到后不再调工具」，减少轮次、提升稳定。
- 路由路径使用 ASCII（`/api/audit`），qi-web 运行时对中文路径有字节边界限制。
- 全局字符串常量在 qi codegen 有已知限制；端口/主机/URL 用函数局部变量。
- HTTP 传输的已知边界：纯 Qi 的 SSE 处理对重型页面的大 `browser_evaluate` 偶有不稳（`mcp_no_response` 后 `Session not found`）；这种页面用 stdio。
- 输出检查只能确认合法 JSON 且含 `seo分数`、`geo分数`、`维度`、`建议` 顶层键，不验证完整 JSON Schema 或分数范围。
