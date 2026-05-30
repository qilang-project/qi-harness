# SEO/GEO 审计器（qi-web + qi-harness 综合示例）

输入 URL → Playwright MCP 真实抓取页面 → LLM 给 SEO/GEO 评分与修改建议。

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

# 启动 web 服务（核心）
QI_LLM_KEY=sk-... $QI run 服务.qi
# 浏览器打开 http://127.0.0.1:43517/
# 填写 URL → 点审计 → 等待 LLM 给出报告

# JSON API（命令行测试）
curl -s -X POST http://127.0.0.1:43517/api/audit \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}'
```

## 架构

```
浏览器表单 (GET /)
    └─ fetch POST /api/audit
           └─ 处理API() → 审计(网址)
                  ├─ 开启会话(DeepSeek配置)
                  ├─ 连接MCP_stdio("npx @playwright/mcp")
                  ├─ 装备MCP(代理值, mcp句柄)  ← 自动注册 Playwright 工具
                  └─ 运行(代理值, "审计 <url>")
                         ├─ browser_navigate → browser_evaluate → ...
                         └─ 返回 JSON 报告
```

## 注意

- 路由路径使用 ASCII（`/api/audit`），因为 qi-web 运行时的字符串工具对中文路径有边界检查限制。
- 全局常量若为字符串类型，在 qi codegen 中有已知类型推断限制；故端口/主机声明为函数局部变量。
- 每次请求 spawn 一个 Playwright MCP 子进程，适合 demo；生产建议改为连接池。
