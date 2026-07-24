#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

cleanup_generated_artifacts() {
    rm -f tests/service_persistence/service_fixture.o \
        tests/m1_reliability/timeout_test.o \
        tests/m1_reliability/budget_atomic_test.o \
        tests/m1_reliability/budget_test.o
}

trap cleanup_generated_artifacts EXIT HUP INT TERM
cleanup_generated_artifacts

run() {
    printf '\n==> %s\n' "$*"
    "$@"
}

run_suite() {
    name=$1
    shift
    printf '\n==> suite: %s\n' "$name"
    started=$(date +%s)
    status=0
    "$@" || status=$?
    elapsed=$(($(date +%s) - started))
    if [ "$status" -ne 0 ]; then
        printf '==> failed: %s (exit %s, %ss)\n' "$name" "$status" "$elapsed" >&2
        return "$status"
    fi
    printf '==> passed: %s (%ss)\n' "$name" "$elapsed"
}

run_clean_path_suite() {
    name=$1
    paths=$2
    shift 2
    for path in $paths; do
        rm -rf "$path"
    done
    status=0
    run_suite "$name" "$@" || status=$?
    for path in $paths; do
        rm -rf "$path"
    done
    return "$status"
}

run_discovered_test() {
    name=$1
    shift
    for test_path in "$@"; do
        if [ ! -f "$test_path" ]; then
            continue
        fi
        case "$test_path" in
            *.sh) run_suite "$name" "$test_path" ;;
            *.py) run_suite "$name" python3 "$test_path" ;;
            *.qi) run_suite "$name" tests/run-qi-test.sh "$test_path" ;;
            *) continue ;;
        esac
        return
    done
    printf 'error: required suite "%s" has no test runner or test file candidate\n' "$name" >&2
    return 1
}

if [ "${1:-}" = "--self-test-exit-propagation" ]; then
    run_clean_path_suite "intentional failure" \
        "/tmp/qi_harness_offline_gate_exit_test.$$" sh -c 'exit 23'
    exit 0
fi

if [ "${1:-}" = "--self-test-controlled-tools" ]; then
    run_discovered_test "controlled tool" \
        tests/controlled_tool/run.sh tests/controlled-tool/run.sh tests/controlled_tools/run.sh \
        tests/controlled_tool/run.py tests/controlled-tool/run.py tests/controlled_tools/run.py \
        tests/controlled_tool/controlled_tool_test.qi tests/controlled-tool/controlled_tool_test.qi \
        tests/controlled_tools/controlled_tools_test.qi
    exit 0
fi

if [ "${1:-}" = "--self-test-missing-candidate" ]; then
    run_discovered_test "missing candidate" \
        tests/offline_gate/does-not-exist.sh tests/offline_gate/does-not-exist.qi
    exit 0
fi

if ! command -v qi >/dev/null 2>&1; then
    printf 'error: qi is not on PATH\n' >&2
    exit 127
fi

run_suite "Qi compatibility" python3 scripts/check-qi-compat.py

if [ -n "${QI_DIFF_BASE:-}" ] && git cat-file -e "${QI_DIFF_BASE}^{commit}" 2>/dev/null; then
    run git diff --check "$QI_DIFF_BASE" HEAD
else
    run git diff --check
    run git diff --cached --check
fi

run_suite "Release policy" scripts/tests/run.sh
run_suite "offline gate exit propagation" tests/offline_gate/run.sh
run_suite "Public API baseline" ./check-public-api.py
run_suite "All example syntax" ./check-examples.py

run_suite "Harness syntax" qi check Harness.qi
run_suite "MCP service syntax" qi check MCP服务.qi
run_suite "Agent service syntax" qi check 代理服务.qi
run_suite "MCP service example syntax" qi check examples/MCP服务示例.qi
run_suite "Agent service example syntax" qi check examples/代理服务示例.qi

run_suite "retry resource isolation" tests/run-qi-test.sh examples/重试_熔断测.qi
run_suite "budget example" tests/run-qi-test.sh examples/预算_测.qi
run_suite "context window example" tests/run-qi-test.sh examples/上下文_滑窗测.qi
run_suite "evaluation example" tests/run-qi-test.sh examples/评估_打分测.qi

run_suite "event bus" tests/run-qi-test.sh tests/events/lifecycle_event_test.qi
run_suite "Agent lifecycle" tests/events/run-agent-lifecycle.sh
run_clean_path_suite "event adapters" /tmp/qi_event_adapter_trace.jsonl \
    tests/run-qi-test.sh tests/events/event_adapters_test.qi
run_suite "tool pipeline" tests/run-qi-test.sh tests/tool_pipeline/工具管线_测.qi
run_suite "tool scheduling" tests/tool_scheduling/run.sh
run_discovered_test "run configuration" \
    tests/run_config/run.sh tests/run_config/run.py tests/run_config/run_config_test.qi
run_suite "run context" tests/run_context/run.sh
run_discovered_test "run journal" \
    tests/run_journal/run.sh tests/run_journal/run.py tests/run_journal/run_journal_test.qi
run_discovered_test "reliable stream" \
    tests/reliable_stream/run.sh tests/reliable-stream/run.sh \
    tests/reliable_stream/run.py tests/reliable-stream/run.py \
    tests/reliable_stream/reliable_stream_test.qi tests/reliable-stream/reliable_stream_test.qi
run_discovered_test "controlled tool" \
    tests/controlled_tool/run.sh tests/controlled-tool/run.sh tests/controlled_tools/run.sh \
    tests/controlled_tool/run.py tests/controlled-tool/run.py tests/controlled_tools/run.py \
    tests/controlled_tool/controlled_tool_test.qi tests/controlled-tool/controlled_tool_test.qi \
    tests/controlled_tools/controlled_tools_test.qi
run_clean_path_suite "session persistence" /tmp/qi_harness_session_persistence_test.db \
    tests/run-qi-test.sh tests/session/session_persistence_test.qi
run_suite "session export/import" tests/run-qi-test.sh tests/session/session_export_import_test.qi
run_clean_path_suite "Agent session integration" \
    "/tmp/qi_harness_agent_restore_test.db /tmp/qi_harness_agent_recording_test.db /tmp/qi_harness_failed_turn_persistence_test.db" \
    python3 tests/session_integration/run.py
run_clean_path_suite "CLI unit" /tmp/qi_harness_cli_test.db \
    tests/run-qi-test.sh tests/cli/cli_测.qi
run_suite "CLI native" python3 tests/cli/run.py
run_suite "service persistence" python3 tests/service_persistence/run.py
run_clean_path_suite "filesystem sandbox isolation" \
    "/tmp/qi_harness_sandbox_isolation_a /tmp/qi_harness_sandbox_isolation_b" \
    tests/run-qi-test.sh tests/isolation/filesystem_sandbox_isolation_test.qi
run_suite "tool metadata concurrency" \
    tests/run-qi-test.sh tests/isolation/tool_metadata_concurrency_test.qi
run_suite "report isolation" tests/run-qi-test.sh tests/isolation/report_isolation_test.qi
run_suite "retrieval config isolation" \
    tests/run-qi-test.sh tests/isolation/retrieval_config_isolation_test.qi
run_suite "M1 reliability" tests/m1_reliability/run.sh
run_suite "Package reproducibility and path consumer" scripts/test-package.sh

printf '\nQuality gate passed.\n'
