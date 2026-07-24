#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

qi check "$ROOT/运行上下文.qi"
qi check "$ROOT/工具上下文.qi"
exec python3 "$ROOT/tests/run_context/run.py"
