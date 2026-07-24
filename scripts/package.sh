#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_DIR=${OUTPUT_DIR:-"$ROOT/dist"}
VERSION=${VERSION:-$(python3 "$ROOT/scripts/version.py" current)}

case "$VERSION" in
    v*) VERSION=${VERSION#v} ;;
esac

python3 "$ROOT/scripts/version.py" check "$VERSION"
python3 "$ROOT/scripts/package.py" \
    --version "$VERSION" \
    --output-dir "$OUTPUT_DIR" \
    --source-date-epoch "${SOURCE_DATE_EPOCH:-0}"
