# Changelog

All notable changes to qi-harness are recorded here. The project follows semantic versioning, with the qualification that public APIs may still change during the `0.x` series. Breaking changes must be called out and accompanied by migration instructions.

## [Unreleased]

## [0.2.0] - 2026-07-24

### Added

- A published Qi `2026.07.24-1` baseline for the stream-v2 timed-poll, tool-control, and Web transport body-limit ABIs.
- Pinned-source Qi installation for CI and release preflight, with policy tests prohibiting fabricated Qi versions, tags, and release-download references.
- Compatibility preflight that compiles and links probes for every required standard-library ABI family; no version-only compatibility claim is made before a real Qi release contains them.
- A checked public API manifest covering the `Harness` entry point and every direct-import `Harness.<module>` surface.
- CI drift detection for public function signatures and public type shapes.
- Deterministic first-release API bootstrapping from historical tag `2026.05.30-1`, pinned by exact commit and generated-manifest SHA-256; subsequent releases continue from prior signed `v*` tags.
- Recursive syntax checking for examples, with explicit reporting of examples skipped because an optional package is unavailable.
- Lifecycle events and adapters, run context, persistent session storage and import/export, CLI support, and service session persistence.
- `配置服务会话租约` for explicitly tuning the persistent service session lease duration.
- Isolated resource handles for retry state, reports, file sandboxes, retrieval configuration, and lifecycle event buses.
- Model request timeout configuration and reliability tests for timeout and budget enforcement.

### Changed

- The package and CLI version are now `0.2.0` for the first governed `0.2.x` release line.
- The offline quality gate is the canonical local and CI validation command.
- Release policy rejects breaking public API drift; additive drift is accepted only for a minor or major version increment.
- New agents own an isolated retry resource by default; sharing retry state is now explicit.
- Stateful subsystems increasingly prefer explicit handles while retaining selected default-handle convenience APIs during the `0.x` transition.

### Migration

- See [MIGRATING.md](MIGRATING.md) before updating from `0.1.x` or when intentionally changing `public-api.txt`.

## [0.1.0]

### Added

- Initial qi-harness package with model configuration, conversations, tools, agent loops, tracing, retries, skills, and MCP client support.
