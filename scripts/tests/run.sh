#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

if [ "${QI_RELEASE_POLICY_SELF_TEST:-0}" != "1" ]; then
    python3 "$ROOT/scripts/tests/test_release_policy.py"
fi
CURRENT_VERSION=$(python3 "$ROOT/scripts/version.py" current)
python3 "$ROOT/scripts/version.py" check "$CURRENT_VERSION"
python3 "$ROOT/scripts/check-release-notes.py" --unreleased

BASELINE=$ROOT/public-api.txt
TEMP_BASELINE=
TEMP_CONFIG=
cleanup() {
    if [ -n "$TEMP_BASELINE" ]; then
        rm -f "$TEMP_BASELINE"
    fi
    if [ -n "$TEMP_CONFIG" ]; then
        rm -f "$TEMP_CONFIG"
    fi
}
trap cleanup EXIT HUP INT TERM

if [ -n "${QI_API_BASELINE_FILE:-}" ] || [ -n "${QI_API_BASELINE_VERSION:-}" ]; then
    if [ -z "${QI_API_BASELINE_FILE:-}" ] || [ -z "${QI_API_BASELINE_VERSION:-}" ]; then
        printf 'error: QI_API_BASELINE_FILE and QI_API_BASELINE_VERSION must be set together\n' >&2
        exit 1
    fi
    if [ ! -f "$QI_API_BASELINE_FILE" ]; then
        printf 'error: QI_API_BASELINE_FILE does not exist: %s\n' "$QI_API_BASELINE_FILE" >&2
        exit 1
    fi
    CANDIDATE_VERSION=$(python3 "$ROOT/scripts/version.py" current)
    printf 'comparing source API with verified generated baseline %s\n' "$QI_API_BASELINE_FILE"
    python3 "$ROOT/scripts/api-diff.py" \
        --baseline-version "$QI_API_BASELINE_VERSION" \
        --candidate-version "$CANDIDATE_VERSION" \
        "$QI_API_BASELINE_FILE"
    exit 0
fi

if [ -n "${QI_DIFF_BASE:-}" ]; then
    if ! git -C "$ROOT" cat-file -e "${QI_DIFF_BASE}^{commit}" 2>/dev/null; then
        printf 'error: QI_DIFF_BASE is not a commit: %s\n' "$QI_DIFF_BASE" >&2
        exit 1
    fi
    if ! git -C "$ROOT" cat-file -e "${QI_DIFF_BASE}:public-api.txt" 2>/dev/null \
        || ! git -C "$ROOT" cat-file -e "${QI_DIFF_BASE}:qi.toml" 2>/dev/null; then
        printf 'error: QI_DIFF_BASE lacks public-api.txt or qi.toml: %s\n' "$QI_DIFF_BASE" >&2
        exit 1
    fi
    TEMP_BASELINE=$(mktemp "${TMPDIR:-/tmp}/qi-harness-api-baseline.XXXXXX")
    TEMP_CONFIG=$(mktemp "${TMPDIR:-/tmp}/qi-harness-version-baseline.XXXXXX")
    git -C "$ROOT" show "${QI_DIFF_BASE}:public-api.txt" > "$TEMP_BASELINE"
    git -C "$ROOT" show "${QI_DIFF_BASE}:qi.toml" > "$TEMP_CONFIG"
    BASELINE=$TEMP_BASELINE
    BASELINE_VERSION=$(python3 "$ROOT/scripts/version.py" --file "$TEMP_CONFIG" current)
    CANDIDATE_VERSION=$(python3 "$ROOT/scripts/version.py" current)
    printf 'comparing source API with public-api.txt from %s\n' "$QI_DIFF_BASE"
    python3 "$ROOT/scripts/api-diff.py" \
        --baseline-version "$BASELINE_VERSION" \
        --candidate-version "$CANDIDATE_VERSION" \
        "$BASELINE"
    exit 0
fi

python3 "$ROOT/scripts/api-diff.py" "$BASELINE"
