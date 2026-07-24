#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

if [ "$#" -ne 1 ]; then
    printf 'usage: %s TEST_FILE\n' "$0" >&2
    exit 2
fi

case "$1" in
    /*) TEST_FILE=$1 ;;
    *) TEST_FILE=$ROOT/$1 ;;
esac

if output=$(qi run "$TEST_FILE" 2>&1); then
    status=0
else
    status=$?
fi
printf '%s\n' "$output"

if [ "$status" -ne 0 ]; then
    exit "$status"
fi

case "$output" in
    *"FAIL "*)
        printf 'error: test reported a failed assertion: %s\n' "$TEST_FILE" >&2
        exit 1
        ;;
esac
