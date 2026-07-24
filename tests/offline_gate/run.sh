#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
TMP=${TMPDIR:-/tmp}/qi_harness_offline_gate_test.$$

cleanup() {
    rm -rf "$TMP"
}

trap cleanup EXIT HUP INT TERM
mkdir -p "$TMP"

status=0
output=$("$ROOT/run-offline-tests.sh" --self-test-exit-propagation 2>&1) || status=$?
printf '%s\n' "$output"

if [ "$status" -ne 23 ]; then
    printf 'error: offline gate returned %s instead of 23\n' "$status" >&2
    exit 1
fi

case "$output" in
    *"==> passed: intentional failure"*|*"Quality gate passed."*)
        printf 'error: offline gate printed a success message after failure\n' >&2
        exit 1
        ;;
esac

QI_OFFLINE_GATE_QI_LOG=$TMP/qi.log \
    PATH=$ROOT/tests/offline_gate/bin:$PATH \
    "$ROOT/run-offline-tests.sh" --self-test-controlled-tools

expected="run $ROOT/tests/controlled_tools/controlled_tools_test.qi"
actual=$(cat "$TMP/qi.log")
if [ "$actual" != "$expected" ]; then
    printf 'error: controlled-tools candidate was "%s", expected "%s"\n' \
        "$actual" "$expected" >&2
    exit 1
fi

status=0
output=$("$ROOT/run-offline-tests.sh" --self-test-missing-candidate 2>&1) || status=$?
printf '%s\n' "$output"

if [ "$status" -eq 0 ]; then
    printf 'error: missing candidate unexpectedly succeeded\n' >&2
    exit 1
fi

case "$output" in
    *'required suite "missing candidate" has no test runner or test file candidate'*) ;;
    *)
        printf 'error: missing candidate did not print the required-suite diagnostic\n' >&2
        exit 1
        ;;
esac

for optimize in "" "-O"; do
    python="python3${optimize:+ $optimize}"
    status=0
    output=$(python3 $optimize "$ROOT/tests/cli/run.py" --self-test-failure 2>&1) || status=$?
    printf '%s\n' "$output"

    if [ "$status" -eq 0 ]; then
        printf 'error: CLI intentional failure unexpectedly succeeded under %s\n' "$python" >&2
        exit 1
    fi
    case "$output" in
        *"check failed: intentional_failure"*) ;;
        *)
            printf 'error: CLI intentional failure lacked its named diagnostic under %s\n' "$python" >&2
            exit 1
            ;;
    esac
    case "$output" in
        *"PASS cli_e2e_native_compile"*)
            printf 'error: CLI intentional failure printed PASS under %s\n' "$python" >&2
            exit 1
            ;;
    esac
done

printf 'offline gate regression tests passed\n'
