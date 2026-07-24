# Migrating qi-harness

qi-harness is currently in the `0.x` series. Necessary API changes may occur before `1.0`, but each release must identify breaking changes here and in `CHANGELOG.md`.

## Migrating to 0.2.0

### Qi Toolchain Baseline

qi-harness `0.2.x` requires Qi `2026.07.24-1` or newer. Earlier releases lack `标准库.大模型.限时读取流事件V2`, `标准库.工具控制`, and `标准库.Web运行时.请求主体超过上限`, so they cannot compile and link the current package and Web service path.

The governed source baseline is Qi `05568a72a92698502fe006bd3223536e9cb04887`, qi-runtime `ceada461d2aca568b2b3788f3b310fcc07748423`, qi-gui `825802276688a528fcad53f831a92f8e35f89963`, and qi-web `120576d4c53888535e1ef617510fc3d340d2e640`. CI and release preflight verify these exact commits with `scripts/install-qi-source.sh` and `scripts/check-qi-compat.py`.

### Retry State

New agents create and own an isolated retry resource. Code that intentionally shared circuit-breaker or rate-limit state between agents must now create one resource and attach it explicitly with `共享重试资源`. Close each agent normally; a shared resource remains owned by the caller and must be released explicitly.

The no-handle retry functions remain convenience APIs backed by the default resource. Prefer the resource-taking variants in concurrent services and tests to avoid cross-request state leakage.

### Stateful Subsystems

Reports, file sandboxes, retrieval configuration, lifecycle events, and run context now expose explicit resource or scope APIs. Existing default-handle helpers remain available where compatibility requires them, but new concurrent or multi-tenant code should:

1. Create a resource per independent operation or tenant.
2. Pass that handle through the operation instead of mutating process-global defaults.
3. Release or close the resource at the end of its lifecycle.

### Persistent Sessions

Persistent Agent sessions use the `Harness.会话存储` direct-import module. Open a store, attach it to an agent with a stable session ID, and explicitly restore a selected branch before continuing it. Session import/export is versioned; callers must not edit the exported JSON shape or assume future versions can be loaded by older releases.

Persistent services may call `Harness.代理服务.配置服务会话租约` before startup to tune the lease duration for their operational timeout envelope. The default remains 300000 milliseconds, so existing service configuration does not require a change.

### Model Timeouts

`模型配置` includes `超时秒`. Builders provide a default. Code constructing this public type directly must initialize the field, or migrate to `大模型(...)` followed by `配置超时(...)`.

### Public API Changes

`public-api.txt` is the release baseline for both import styles:

- `导入 Harness::{...}`
- `导入 Harness.<模块>::{...}`

Run `./check-public-api.py` before release. The release policy rejects removed or signature-changed declarations with exit code `20`; there is no CI override. Additive declarations are accepted only when the package minor or major version increases. If an intentional API change causes drift:

For the first governed release only, `v0.2.0` compares against the API generated from historical tag `2026.05.30-1`. That unsigned tag is accepted only through `scripts/historical-api-baseline.py`, which requires its immutable commit `11ad18011059726535163dfd3280996f03c095ca` and the checked SHA-256 digest of the generated manifest. Later releases use the nearest reachable signed `v*.*.*` tag containing `public-api.txt` and `qi.toml`; mutable branch or event baselines are not release authorities.

1. Preserve the existing declaration and add the replacement API alongside it.
2. Document the new API and migration path in this file and `CHANGELOG.md`.
3. Update affected examples and release tests.
4. For an additive minor release, run `./check-public-api.py --update` and review the manifest diff.
5. Run `./run-offline-tests.sh` on the supported Qi toolchain.

Do not update the manifest merely to make CI pass; its diff is the review point for public compatibility.
