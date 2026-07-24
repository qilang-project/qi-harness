# qi-harness Technical Design

## Status

This document specifies the next architecture of qi-harness after the current non-streaming runtime, lifecycle events, tool validation, persistent sessions, service adapter, CLI, isolation handles, and quality gates.

It covers capabilities intentionally deferred by the current implementation:

- reliable streaming, cancellation, usage, budgets, and recovery
- controlled tool execution, progress, enforceable timeout, and crash isolation
- authenticated multi-tenant HTTP services and OS-level execution isolation
- staged physical package separation without breaking existing `Harness` imports
- governed release automation for the `0.x` line

This is an implementation specification. `更新计划.md` remains the delivery tracker.

## Principles

1. Never retry a model generation transparently after externally committed output.
2. Never claim exactly-once external side effects without transactional or idempotent tool support.
3. Persistent state is the recovery source of truth; runtime handles are caches.
4. Authentication identity is created only by trusted adapters, never by request JSON or model output.
5. Application guardrails are not process, network, syscall, or container isolation.
6. Every long-lived handle has explicit ownership, close/release semantics, and bounded storage.
7. Existing `Harness::{...}` and `Harness.<module>::{...}` imports remain available during `0.x` migration.
8. Existing public structs do not gain fields during compatibility releases.
9. New evolving APIs use constructors, builders, and opaque handles instead of public struct literals.
10. Release gates enforce API drift, migration notes, reproducible tests, and immutable version tags.

All v2 type blocks below are conceptual schemas unless explicitly marked as existing APIs. Initial Qi implementations use validated generation-stamped handles plus constructors/getters/setters, or versioned JSON. Callers are not required or encouraged to construct v2 aggregates with `新建`.

## Unified Architecture

```text
Authenticated client
        |
        v
HarnessAdapters
  HTTP / CLI / MCP / Trace / Report / OTLP
        |
        v
HarnessWorkflows
  Graph / Loop / RAG / Evaluation / Guardrails / Multi-agent
        |
        v
HarnessRuntime
  Agent / Model / Messages / Tools / Events / Run config / Reliability
        |
        v
HarnessPersistence
  Sessions / Runs / Events / Attempts / Tool executions / Budgets / Memory
```

The existing `Harness` package remains a compatibility facade over these implementation packages.

## Shared Protocol

Reliable streaming, tools, persistence, HTTP reconnect, and observability use one run event envelope.

```json
{
  "version": 2,
  "run_id": "uuid",
  "request_id": "tenant-scoped-idempotency-key",
  "session_id": "persistent-session-id",
  "turn_id": "uuid",
  "step_id": "model-0",
  "attempt_id": "uuid",
  "seq": 17,
  "ts_ms": 1780000000000,
  "type": "assistant.text.delta",
  "payload": {}
}
```

Rules:

- `seq` is strictly increasing within a run.
- An event is persisted before it is delivered externally.
- Delivery is at-least-once; consumers deduplicate using `(run_id, seq)`.
- Exactly one run terminal event is allowed.
- No events may follow a run terminal event.
- Unknown event types are ignored by compatible consumers.
- Client reconnect uses `after_seq`; replay starts at `after_seq + 1`.

## Reliable Streaming

### Commitment Boundary

Retry is allowed only before the first semantic output event is durably committed.

These do not commit output:

- HTTP headers
- keep-alives
- role-only deltas
- empty deltas
- provider request IDs
- usage preambles

These commit output:

- assistant text delta
- externally exposed tool-call delta
- reasoning or media delta when enabled

After commitment, a transport or provider failure returns `incomplete`; it does not restart the generation.

### Public API

New module: `Harness.可靠流`.

```qi
公开 类型 可靠流配置 {
    首输出超时毫秒: 整数,
    空闲超时毫秒: 整数,
    总超时毫秒: 整数,
    最大总试次: 整数,
    起始退避毫秒: 整数,
    最大退避毫秒: 整数,
    部分输出策略: 整数,
    断连策略: 整数,
    文本块最大字节: 整数,
    预算预留令牌: 整数,
}

公开 类型 取消令牌 {
    句柄: 整数,
}

公开 类型 流运行结果 {
    状态: 字符串,
    运行ID: 字符串,
    请求ID: 字符串,
    文本: 字符串,
    用量JSON: 字符串,
    错误JSON: 字符串,
    最后序号: 整数,
}

公开 函数 默认可靠流配置() : 可靠流配置;
公开 函数 创建取消令牌() : 取消令牌;
公开 函数 请求取消(令牌: 取消令牌, 原因: 字符串) : 整数;
公开 函数 已请求取消(令牌: 取消令牌) : 整数;
公开 函数 释放取消令牌(令牌: 取消令牌) : 整数;

公开 函数 可靠流式运行(
    安全执行上下文句柄: 整数,
    代理值: 代理,
    提示: 字符串,
    请求ID: 字符串,
    配置值: 可靠流配置,
    取消值: 取消令牌,
    事件回调: 函数(字符串): 整数
) : 流运行结果;

公开 函数 重放流事件(
    安全执行上下文句柄: 整数,
    运行ID: 字符串,
    之后序号: 整数,
    回调: 函数(字符串): 整数
) : 整数;
```

The validated security execution-context handle contains the principal, tenant, service instance, persistence instance, and authorization adapter. Raw database handles, session IDs, and request IDs are never sufficient authorization for run creation, replay, cancellation, or recovery.

Callback return values:

- `1`: accepted, continue
- `0`: request cancellation
- `-1`: consumer failure

Statuses:

```text
completed
failed
incomplete
cancelled
budget_exceeded
consumer_failed
needs_reconciliation
already_running
```

Existing `流式问` and `流式运行` remain compatibility adapters. They must not retry after visible output. Callback return `0`, currently ignored, becomes cancellation once the v2 runtime is available.

### State Machine

```text
NEW
 -> RESERVING
 -> MODEL_READY
 -> ATTEMPT_OPENING
 -> PREFETCHING
 -> STREAMING | MODEL_COMPLETE | RETRY_WAIT | FAILED | CANCELLING

STREAMING
 -> MODEL_COMPLETE | INCOMPLETE | CANCELLING

MODEL_COMPLETE
 -> TOOLS_READY | COMMITTING_FINAL | FAILED

TOOLS_READY
 -> TOOLS_RUNNING
 -> CONTINUING | NEEDS_RECONCILIATION | FAILED | CANCELLING

CONTINUING
 -> ATTEMPT_OPENING

COMMITTING_FINAL
 -> COMPLETED
```

Additional transitions:

```text
RESERVING -> BUDGET_EXCEEDED
PREFETCHING | STREAMING -> CONSUMER_FAILED
```

Terminal states are `COMPLETED`, `FAILED`, `INCOMPLETE`, `CANCELLED`, `BUDGET_EXCEEDED`, `CONSUMER_FAILED`, and `NEEDS_RECONCILIATION`. `already_running` is an API disposition returning the existing run ID, not a second run state, and emits no additional terminal event.

### Runtime FFI v2

The runtime owns one provider attempt and emits normalized events. Harness owns retries and recovery policy.

```text
大模型.打开可靠流(会话, 请求JSON) -> 流句柄
大模型.读取可靠流事件(流句柄) -> 事件JSON
大模型.取消可靠流(流句柄, 原因) -> 状态
大模型.可靠流快照(流句柄) -> JSON
大模型.提交可靠流消息(流句柄, 期望历史版本) -> 新历史版本
大模型.放弃可靠流(流句柄) -> 状态
大模型.关闭可靠流(流句柄) -> 状态
大模型.历史版本(会话) -> 整数
大模型.运行时能力("stream_v2") -> 整数
```

These functions require a minimum Qi compiler/runtime version that already declares the capability-query symbol. Harness cannot call a missing symbol to detect an older runtime. Every FFI addition must also be registered in `qi/src/codegen/module_registry.rs`, and CI must compare compiler declarations with runtime exports.

Version baselines:

```text
current governed 0.2 release minimum:   Qi 2026.07.24-1
compiler source baseline:               qi@05568a72
runtime source baseline:                qi-runtime@ceada461
required source capabilities:           stream-v2 timed poll + tool-control + web body-limit
```

Qi `2026.07.24-1` is the first governed release containing all required ABIs. The package entry point references reliable stream and controlled-tool APIs, while the service path relies on the Web body-limit ABI. CI pins full source SHAs and must pass compiler/runtime ABI parity plus compile/link probes before release.

Required normalized events:

```text
response.started
text.delta
tool_call.delta
usage
response.completed
error
cancelled
```

Stream v2 supports only explicitly listed provider/protocol combinations. Unsupported modes are rejected before sending a request. Each provider adapter maps its native completion marker, usage, tool calls, and errors into the normalized event set. EOF without that provider's valid terminal marker is never treated as success.

Capability matrix:

| Milestone | Provider protocol | Text | Tool calls | Usage | Cancellation | Required terminal marker |
|---|---|---:|---:|---:|---:|---|
| v2-M1 | OpenAI Chat Completions-compatible SSE | yes | yes | `stream_options.include_usage` when supported; otherwise `usage_unknown` | transport abort | `[DONE]` plus completed choice/finish state |
| v2-M2 | Anthropic Messages SSE | yes | tool-use blocks and input JSON deltas | `message_start`/`message_delta` usage | transport abort | `message_stop` |
| v2-M3 | Gemini `streamGenerateContent` | yes | function-call parts | final `usageMetadata` when supplied | transport abort | completed response with no blocked/error state |

Only v2-M1 is enabled in the first implementation. Anthropic and Gemini requests fail with `unsupported_stream_protocol` before network dispatch until their respective milestones and deterministic fixtures pass. Within OpenAI-compatible mode, providers that do not support usage frames remain usable but settle budget conservatively as `usage_unknown`.

EOF without a provider terminal marker is an error, not successful completion.

The runtime must not commit stream history on close. It commits only after Harness validates a complete assistant message and supplies the expected session revision.

### Runtime Concurrency

The global stream map must not be held during blocking network reads.

Each stream uses:

- an `Arc<StreamState>`
- an async HTTP task or dedicated reader thread
- a bounded normalized-event channel
- an atomic cancellation token
- explicit terminal state

`读取可靠流事件` waits on the channel. Cancellation aborts the HTTP task and wakes blocked readers.

## Persistent Run Journal

Reliable recovery uses companion tables in the session database.

```sql
CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    current_state TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at INTEGER,
    lease_fence INTEGER NOT NULL DEFAULT 0,
    last_seq INTEGER NOT NULL DEFAULT 0,
    partial_output_committed INTEGER NOT NULL DEFAULT 0,
    final_text TEXT,
    usage_json TEXT,
    error_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (tenant_id, session_id, request_id)
);

CREATE TABLE agent_run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE model_attempts (
    attempt_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    provider_request_id TEXT,
    first_output_at INTEGER,
    usage_json TEXT,
    usage_status TEXT NOT NULL,
    error_json TEXT,
    UNIQUE (run_id, step_id, attempt_number)
);

CREATE TABLE tool_executions (
    execution_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    dispatch_started_at INTEGER,
    external_commit_state TEXT NOT NULL,
    reconciliation_status TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    started_at INTEGER,
    completed_at INTEGER
);

CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL,
    reserved_cost_micros INTEGER NOT NULL DEFAULT 0,
    actual_tokens INTEGER,
    actual_cost_micros INTEGER,
    status TEXT NOT NULL,
    usage_status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    settled_at INTEGER,
    UNIQUE (attempt_id)
);
```

Each coordinator transition is one transaction:

1. verify lease owner, unexpired lease, exact fencing token, and expected state
2. increment `last_seq`
3. insert event
4. update run state
5. update related attempt, tool, or budget record
6. commit
7. deliver callback

Lease acquisition atomically increments `lease_fence`. Every transition includes the fence in its update predicate. A stale worker cannot commit after lease loss.

## Budget Reservation

Post-call accounting is insufficient for reliable streaming. Every provider attempt reserves budget before sending the request.

```text
reservation = estimated_input + configured_max_output + safety_margin
```

Settlement rules:

| Outcome | Settlement |
|---|---|
| actual usage known | charge actual, release remainder |
| definite failure before send | release all |
| request may have been sent, usage unknown | retain conservative reservation |
| partial output, no final usage | retain until reconciliation |
| cancellation before send | release all |
| cancellation after send | charge actual or conservative reservation |

Each retry creates a separate attempt reservation.

Canonical events:

```text
budget.reserved
budget.reservation_rejected
budget.usage_observed
budget.settled
budget.usage_unknown
budget.released
```

## Controlled Tool Execution

### New Types

Existing `工具` remains source-compatible. New tools use `受控工具`.

```qi
公开 类型 工具执行策略 {
    模式: 整数,
    超时毫秒: 整数,
    取消宽限毫秒: 整数,
    并发键: 字符串,
    最大并发: 整数,
    执行器: 整数,
    执行器配置JSON: 字符串,
    模型结果格式: 整数,
    副作用模式: 整数,
    幂等键模式: 整数,
    对账处理句柄: 整数,
}

公开 类型 工具调用上下文 {
    版本: 整数,
    运行ID: 字符串,
    轮次ID: 字符串,
    步骤ID: 字符串,
    调用ID: 字符串,
    工具名: 字符串,
    控制句柄: 整数,
    运行上下文句柄: 整数,
    开始时间毫秒: 整数,
    截止时间毫秒: 整数,
}

公开 类型 工具进度 {
    阶段: 字符串,
    消息: 字符串,
    当前: 整数,
    总数: 整数,
    数据JSON: 字符串,
}

公开 类型 工具结果 {
    版本: 整数,
    状态: 字符串,
    内容类型: 字符串,
    内容: 字符串,
    错误代码: 字符串,
    错误消息: 字符串,
    可重试: 整数,
    终止运行: 整数,
    元数据JSON: 字符串,
}

公开 类型 受控工具 {
    名字: 字符串,
    描述: 字符串,
    参数schema: 字符串,
    处理: 函数(工具调用上下文, 字符串): 工具结果,
    执行策略值: 工具执行策略,
}
```

Default controlled-tool policy is serial and in-process. Read-only tools explicitly opt into parallel execution.

Side-effect modes are `read_only`, `idempotent`, and `non_idempotent`. Recovery retries a tool only when dispatch definitely did not occur or when the tool declares an enforceable idempotency contract. Ambiguous non-idempotent execution transitions to `needs_reconciliation` and is never automatically repeated.

### Control API

```qi
公开 函数 工具已取消(ctx: 工具调用上下文) : 整数;
公开 函数 工具剩余毫秒(ctx: 工具调用上下文) : 整数;
公开 函数 报告工具进度(ctx: 工具调用上下文, 更新: 工具进度) : 整数;

公开 函数 工具文本结果(内容: 字符串) : 工具结果;
公开 函数 工具JSON结果(内容JSON: 字符串) : 工具结果;
公开 函数 工具失败结果(代码: 字符串, 消息: 字符串, 可重试: 整数) : 工具结果;
公开 函数 工具取消结果(消息: 字符串) : 工具结果;
公开 函数 工具超时结果(超时毫秒: 整数) : 工具结果;
公开 函数 工具结果转JSON(结果值: 工具结果) : 字符串;

公开 函数 适配旧工具(旧工具: 工具) : 受控工具;
公开 函数 添加受控工具(代理值: 代理, 工具值: 受控工具) : 代理;
```

Legacy success strings retain their current model-visible projection. Internally they become standardized results.

`添加工具` preserves the effective `0.x` execution mode already stored for a legacy tool. Only newly constructed controlled tools default to serial. Changing legacy scheduling defaults requires a documented minor-version behavioral break and concurrency regression tests.

### Scheduling

Replace all-or-nothing batch scheduling with waves and serial barriers.

```text
A parallel
B parallel
C serial
D parallel
E parallel

wave 1: A + B
barrier: C
wave 2: D + E
```

Additional rules:

- same non-empty concurrency key never overlaps
- per-tool and run-level concurrency limits both apply
- results are written to model history in original tool-call order
- lifecycle completion order reflects actual execution order
- `terminate_run` prevents later waves, requests cooperative cancellation from in-process tools, and forcibly terminates only subprocess/container/remote executors
- MCP concurrency is capability-based; stdio without multiplexing remains serial

### Cancellation and Timeout

In-process tools provide cooperative cancellation and response deadlines. The host cannot safely kill an in-process thread.

The coordinator does not report an in-process tool as safely terminated while its callback is still executing. Production workloads requiring bounded termination use Tier 1 or stronger.

Subprocess/container tools provide enforceable timeout:

```text
cancel request
 -> wait grace
 -> terminate process group/container
 -> wait shutdown interval
 -> kill
 -> discard worker generation
```

No progress is accepted after terminal result. Exactly one `tool_end` is emitted.

### Tool Lifecycle

```text
tool_queued
tool_start
tool_update*
tool_cancel_requested?
tool_timeout?
tool_end
```

Validation failures emit `tool_queued` and `tool_end`, but no `tool_start`.

## Subprocess Worker Protocol

Subprocess execution is the minimum hard crash-containment boundary.

Protocol: newline-delimited JSON on stdout; stderr is diagnostics only.

Handshake:

```json
{"version":1,"type":"hello","protocol":"qi-tool-worker"}
{"version":1,"type":"ready","worker_id":"worker-7","capabilities":{"progress":true,"cancellation":true}}
```

Invocation:

```json
{
  "version": 1,
  "type": "invoke",
  "id": "call_123",
  "tool": "build_index",
  "arguments": {},
  "context": {"run_id":"run_1","turn_id":"turn-1","step_id":"tool-0"},
  "deadline_ms": 1784800005000
}
```

Worker messages:

```text
progress
result
cancel_ack
```

Protocol violations, oversized lines, duplicate terminal results, unknown invocation IDs, and EOF before result terminate and replace the worker.

## Runtime Tool FFI

```text
qi_tool_control_create
qi_tool_control_cancel
qi_tool_control_is_cancelled
qi_tool_control_remaining_ms
qi_tool_control_emit_progress
qi_tool_control_try_progress
qi_tool_control_finish
qi_tool_control_wait
qi_tool_control_release
```

Safe closure invocation can catch a Qi `抛出` only through a Qi-generated `尝试/捕获` trampoline on the same thread. Rust panic recovery is available only in unwind builds. Abort, signals, process exit, deadlock, and memory corruption require subprocess or container isolation.

## Production Security

### Trust Model

- Request JSON, session IDs, owner strings, URLs, model output, and MCP output are untrusted.
- A trusted authentication adapter creates an immutable principal.
- Authorization is checked at session access and again immediately before every tool call.
- Session IDs identify resources; they do not authorize access.
- Model-selected tools and arguments never expand authority.

### Principal and Authentication

New module: `Harness.安全`.

```qi
公开 类型 安全主体 {
    句柄: 整数,
}

公开 类型 认证结果 {
    状态: 整数,
    主体: 安全主体,
    错误代码: 字符串,
    挑战头: 字符串,
}

公开 类型 认证适配器 {
    句柄: 整数,
}

公开 函数 安全主体有效(主体: 安全主体) : 整数;
公开 函数 安全主体ID(主体: 安全主体) : 字符串;
公开 函数 安全主体租户ID(主体: 安全主体) : 字符串;
公开 函数 安全主体签发者(主体: 安全主体) : 字符串;

公开 函数 创建认证适配器(
    名称: 字符串,
    认证函数: 函数(整数, 整数): 认证结果
) : 认证适配器;

公开 类型 授权适配器 {
    句柄: 整数,
}
```

Only a trusted authentication adapter can allocate a principal handle. Handles are generation-stamped and validated on every operation; a positive integer alone is not trusted. HTTP-specific `Web.上下文` is normalized by `HarnessAdapters` into a request-authentication-context handle before authentication, so the security/runtime layer does not depend on qi-web.

Production startup fails without authentication and authorization adapters.

The current client-supplied `所有者` field is removed from the production protocol. Migration maps legacy owner values through operator-controlled configuration, never through request data.

### Authorization

```qi
公开 类型 授权请求 {
    主体: 安全主体,
    动作: 字符串,
    资源类型: 字符串,
    资源ID: 字符串,
    会话ID: 字符串,
    工具名: 字符串,
    能力: 字符串,
    风险级别: 整数,
    属性JSON: 字符串,
}

公开 类型 授权决定 {
    允许: 整数,
    决定ID: 字符串,
    原因代码: 字符串,
    策略版本: 字符串,
    审计级别: 整数,
}
```

Default behavior is deny:

- empty tool allowlist means no tools
- unknown action/capability/tool is denied
- adapter failure is denied
- discovered MCP tools are intersected with a static manifest allowlist
- legacy context-free tools are denied in production unless explicitly allowlisted

### Service Instance

Replace process-global production configuration with an instance handle.

```qi
公开 类型 代理服务配置 {
    模式: 字符串,
    主机: 字符串,
    端口: 整数,
    实例ID: 字符串,
    模型配置句柄: 整数,
    会话配置句柄: 整数,
    认证配置句柄: 整数,
    授权配置句柄: 整数,
    配额配置句柄: 整数,
    审计配置句柄: 整数,
    HTTP安全配置句柄: 整数,
    出站策略句柄: 整数,
    MCP策略句柄: 整数,
}

公开 类型 代理服务 {
    句柄: 整数,
}

公开 函数 创建代理服务(配置值: 代理服务配置) : 代理服务;
公开 函数 验证代理服务配置(配置值: 代理服务配置) : 字符串;
公开 函数 运行代理服务(服务: 代理服务);
公开 函数 停止代理服务(服务: 代理服务, 宽限毫秒: 整数) : 整数;
公开 函数 关闭代理服务(服务: 代理服务) : 整数;
```

The service uses explicit run-context handles. Security decisions never depend on the process-global current-context stack.

### HTTP Contract

```text
GET    /health/live
GET    /health/ready
POST   /v1/sessions
POST   /v1/sessions/{id}/messages
POST   /v1/sessions/{id}/structured
POST   /v1/sessions/{id}/close
DELETE /v1/sessions/{id}
GET    /v1/runs/{id}/events?after_seq=N
POST   /v1/runs/{id}/cancel
```

Status semantics:

- `400`: malformed input
- `401`: missing or invalid credentials
- `403`: authenticated but prohibited non-resource action
- `404`: absent, wrong tenant, wrong owner, closed, or policy-hidden session
- `409`: revision or lease conflict
- `413`: transport-enforced body limit
- `429`: quota exceeded
- `503`: mandatory dependency unavailable

`/health/live` is process liveness. `/health/ready` checks authentication key material, session storage, mandatory audit sink, and required workers.

### Session Ownership and Lease

Ownership is stored as `(tenant_id, issuer, subject_id)`. Every session query includes those fields.

The persistence migration adds an ownership relation:

```sql
CREATE TABLE session_principals (
    tenant_id TEXT NOT NULL,
    issuer TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, issuer, subject_id, session_id, relation)
);
```

Production service code uses only scoped session APIs accepting a validated security context. Legacy unscoped session functions remain for local compatibility and are unavailable in production mode.

Concurrent session operations use expiring leases with fencing tokens, not permanent lock rows.

Every write verifies:

- lease ID
- fencing token
- expected session leaf/revision

### Quotas

Edge limits occur before full body buffering. Identity quotas occur after authentication.

Quota dimensions include:

- requests and concurrent requests
- sessions
- input/output bytes
- provider tokens and cost
- Agent steps
- tool calls and process count
- network and file usage

Admission reserves capacity before provider or tool execution; settlement applies actual usage.

### Audit

Security audit is separate from ordinary trace output.

Audit records identity, decision, policy version, action, resource hash, tool/MCP identity, and result. They never contain bearer tokens, cookies, API keys, raw prompts, or unrestricted tool payloads by default.

Privileged operations fail closed when mandatory audit durability is unavailable beyond the configured local buffer.

### CORS and CSRF

- CORS is disabled by default.
- Origins are exact scheme/host/port values.
- Wildcard origin with credentials is invalid configuration.
- Cookie authentication requires `Secure`, `HttpOnly`, appropriate `SameSite`, Origin validation, and CSRF tokens for state-changing requests.
- Bearer-token APIs do not enable credentialed CORS by default.

### SSRF

All model-, user-, tool-, redirect-, and MCP-selected URLs pass one shared egress policy.

Production defaults:

- HTTPS only
- host allowlist
- loopback/private/link-local/metadata/multicast/CGNAT denied
- DNS answers validated and connected through the validated address set
- every redirect revalidated
- ambient proxy environment disabled
- response, decompression, duration, and concurrency limits
- network firewall or controlled egress proxy as enforcement boundary

String checks in examples are not the production SSRF implementation.

### MCP Policy

Production services connect only by static manifest ID. Request-selected commands, arguments, URLs, package downloads, and unpinned versions are denied.

Manifest fields include executable/image digest, tool allowlist, resource limits, isolation tier, egress policy, and explicit secret mappings.

MCP children receive a minimal environment and never inherit service provider keys, cloud credentials, SSH/Docker sockets, home configuration, or proxy variables unless explicitly mapped.

### Isolation Tiers

| Tier | Boundary | Use |
|---|---|---|
| 0 | trusted in-process tool | reviewed deterministic operations |
| 1 | restricted subprocess | reviewed first-party helper |
| 2 | rootless sandboxed container | third-party MCP, browser automation |
| 3 | microVM/dedicated worker VM | untrusted code, high-value tenants |
| 4 | dedicated tenant environment | regulated/high-assurance workloads |

Containers use non-root users, read-only root filesystems, dropped capabilities, no host runtime sockets, resource limits, explicit mounts, and network denied by default.

## Package Separation

Use four implementation packages plus the existing facade.

```text
HarnessPersistence

HarnessRuntime
  -> HarnessPersistence

HarnessWorkflows
  -> HarnessRuntime
  -> HarnessPersistence

HarnessAdapters
  -> HarnessRuntime
  -> HarnessPersistence
  -> HarnessWorkflows

Harness
  -> all implementation packages
```

### Module Ownership

`HarnessRuntime`:

- 模型, 对话, 工具, 代理
- 事件, 运行上下文, 工具上下文, 运行配置
- 重试, 预算, 上下文

`HarnessPersistence`:

- 会话存储, 记忆
- vector record/index persistence from 向量记忆
- run journal, attempts, tool executions, budget reservations

`HarnessWorkflows`:

- 图, 循环, 多代理, 递归分解
- 评估, 检索, 文档, 提示模板
- 目标监控, 优先级, 并行, 护栏, 技能

`HarnessAdapters`:

- MCP客户端, MCP服务
- 文件工具
- 追踪, 报告, OTLP
- 代理服务, 命令行

`Harness`:

- owns all existing `0.x` public modules, nominal public types, and compatibility functions; delegates only private primitive-, JSON-, or handle-based operations to implementation packages

### Boundary Changes Before Split

1. `工具` becomes transport-neutral; MCP registers an external dispatcher.
2. Agent emits lifecycle events and does not call trace/report adapters directly.
3. Graph execution delegates checkpoint persistence.
4. Vector persistence no longer owns embedding provider calls.
5. Runtime owns policy hook interfaces; `护栏` implements them without creating `代理 <-> 护栏` cycles.

### Compatibility

Preserve both surfaces:

```qi
导入 Harness::{创建代理, 工具, 运行};
导入 Harness.评估::{创建套件};
```

Qi cross-package public struct re-export is currently limited. Therefore, throughout `0.x`, each existing `Harness.<module>` file remains the compatibility surface, not merely root `Harness.qi`:

- existing public structs remain nominally declared by `Harness` during migration
- facade functions forward primitive values and opaque handles
- public aggregate types and functions accepting them are not physically moved before `1.0`; only private handle-based implementations move during `0.x`
- no existing public struct gains fields
- new v2 types use constructors/builders

All implementation packages release in lockstep versions from one repository.

### Migration Phases

```text
0.1.x  freeze API and dependency rules
0.2.x  logical boundaries, no physical moves
0.3.x  extract persistence and workflows
0.4.x  extract adapters and event-only observability
0.5.x  introduce opt-in HarnessRuntime APIs
0.6.x  move facade Agent calls onto the runtime engine
1.0.0  stabilize packages and retain Harness facade
```

## Release Automation

qi-harness uses SemVer. Qi compiler/runtime compatibility uses its independent date-build version.

First governed release target: `v0.2.0`.

Release governance is Phase 0 and precedes stream v2, production security, and physical package extraction. The project must be able to release the existing architecture safely before beginning later minor-version migrations.

Required identity:

```text
tag:                     v0.2.0
qi.toml version:         0.2.0
CHANGELOG heading:       [0.2.0] - YYYY-MM-DD
MIGRATING heading:       Migrating to 0.2.0
artifact prefix:         qi-harness-v0.2.0/
```

### Planned Scripts

```text
scripts/version.py
scripts/api-diff.py
scripts/check-release-notes.py
scripts/prepare-release.py
scripts/package.sh
scripts/test-package.sh
scripts/provider-smoke.sh
```

API drift classification exit codes:

```text
0  no drift
10 additive
20 breaking
30 malformed/unclassifiable
```

For `0.x`:

- patch: no public API additions or breaks
- minor: additive public API only; breaking drift remains rejected

### CI Jobs

```text
policy
offline matrix
ecosystem
package-consumer
upstream-canary
```

Offline matrix:

- Ubuntu minimum Qi
- Ubuntu current Qi
- macOS minimum Qi
- macOS current Qi
- Windows current Qi once stable

The ecosystem job installs pinned qi-web and qi-cli commits and removes corresponding example skips.

### Release Workflow

Tag-triggered job graph:

```text
preflight
 -> offline-release
 -> ecosystem-release
 -> provider-smoke when required
 -> package
 -> package-verification
 -> supply-chain
 -> github-release
 -> post-publish
```

Preflight requires:

- signed annotated SemVer tag
- tag commit contained in `origin/main`
- matching version/changelog/migration
- successful required CI
- explicit license file
- no prohibited tracked files

The first governed `v0.2.0` release has a one-time immutable API bootstrap from historical tag `2026.05.30-1`: policy verifies the tag resolves to commit `11ad18011059726535163dfd3280996f03c095ca`, regenerates the manifest from that commit's source blobs, and verifies its checked SHA-256 digest. It does not trust a branch, push-before SHA, or other mutable candidate baseline. Every later release must use the nearest reachable signed `v*.*.*` tag containing `public-api.txt` and `qi.toml`.

Artifacts:

- source `.tar.gz` and `.zip`
- SHA-256 checksums
- SPDX JSON SBOM
- Sigstore bundle
- GitHub build provenance attestation

Published tags are immutable and never reused or moved.

There is no Qi registry yank mechanism. A defective release is marked `[YANKED]` in GitHub and replaced with a new version; the tag remains immutable.

## Implementation Order

### Phase 0: Release Baseline

1. Add an explicit license.
2. Implement version, API-diff, release-note, packaging, and package-consumer scripts.
3. Establish minimum/current Qi compatibility jobs.
4. Release `v0.2.0` from the existing logical architecture.

### Phase A: Runtime Foundations

1. Make `qi-runtime` the canonical LLM runtime source.
2. Add runtime capability checks and source/ABI parity tests.
3. Add normalized stream v2 events and explicit terminal status.
4. Add abort-without-history-commit and session history revisions.
5. Add interruptible stream workers and cancellation tokens.

### Phase B: Reliable Stream Coordinator

1. Add validated principal/security execution contexts and scoped persistence APIs.
2. Add run journal and sequence numbers.
3. Add request idempotency and replay.
4. Implement retry-before-first-output.
5. Add usage capture and persistent budget reservation.
6. Add recovery leases and state transitions.

### Phase C: Controlled Tools

1. Add standardized internal tool results and legacy adapter.
2. Add control handles, cooperative cancellation, timeout, and progress.
3. Implement wave/barrier scheduling.
4. Add subprocess worker execution and forced termination.
5. Add container and remote executors.
6. Replace append-only registries with generation-stamped reclaimable handles.

### Phase D: Production Service Security

1. Add authentication and authorization adapters.
2. Remove client-supplied owner from production protocol.
3. Add service instances and explicit security run contexts.
4. Add quotas, audit, CORS/CSRF, egress, and MCP manifests.
5. Add lease/fencing session writes and lifecycle streaming endpoints.

### Phase E: Package and Release Migration

1. Enforce logical dependency direction inside `Harness`.
2. Extract persistence and workflows.
3. Extract adapters.
4. Introduce opt-in runtime package.
5. Extend the existing governed release pipeline to the lockstep implementation packages.

## Acceptance Gates

### Streaming

- retry occurs only before committed output
- partial-output failure returns `incomplete` and does not enter model history
- cancellation interrupts blocked runtime reads
- usage and budget settle exactly once per attempt
- reconnect replay is ordered and duplicate-safe
- recovery retries a tool only when dispatch did not occur or an enforceable idempotency contract exists; ambiguous non-idempotent execution becomes `needs_reconciliation`

### Tools

- exactly one terminal result per call
- no progress after terminal result
- queued tools stop after run cancellation
- serial barriers and concurrency keys are enforced
- subprocess crash, abort, timeout, and malformed protocol do not kill the host
- completed registry entries and workers are reclaimable

### Security

- request owner strings cannot create trusted principals
- cross-tenant session access is indistinguishable from missing resources
- authorization is checked immediately before tool execution
- forbidden egress is blocked by application policy and network enforcement
- third-party MCP runs at Tier 2 or stronger
- secrets are never inherited or logged by default

### Packaging

- implementation package graph is acyclic
- facade API manifest stays unchanged in compatibility releases
- direct struct-literal fixtures continue compiling
- package-consumer tests pass from path and immutable Git tag
- release artifacts are signed, checksummed, attested, and reproducible

## Explicit Non-Claims

qi-harness does not claim:

- exactly-once model generation
- exactly-once arbitrary external side effects
- hard timeout for in-process non-cooperative tools
- panic, signal, or memory-corruption isolation inside the service process
- secure multi-tenancy from prompt filters, path checks, owner strings, or tool allowlists alone
- complete SSRF prevention without network-level egress control
- production readiness before authentication, quotas, audit, service instances, and OS isolation are deployed
