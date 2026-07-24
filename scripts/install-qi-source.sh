#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
QI_SOURCE_LABEL=${QI_SOURCE_LABEL:-unreleased-abi-baseline}
PREFIX=${QI_PREFIX:-"${RUNNER_TEMP:-${TMPDIR:-/tmp}}/qi-$QI_SOURCE_LABEL"}
WORK=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/qi-source.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

checkout_source() {
    name=$1
    repository=$2
    source_dir=$3
    source_ref=$4
    destination=$5

    if [ -n "$source_dir" ]; then
        test -d "$source_dir/.git" || {
            printf 'error: %s source is not a Git checkout: %s\n' "$name" "$source_dir" >&2
            exit 1
        }
        mkdir -p "$destination"
        rsync -a --exclude .git --exclude target "$source_dir/" "$destination/"
        return
    fi

    test -n "$source_ref" || {
        printf 'error: set %s_SOURCE_DIR or a pinned %s_SOURCE_REF\n' "$name" "$name" >&2
        exit 1
    }
    case "$source_ref" in
        [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
        *) printf 'error: %s_SOURCE_REF must be a full commit SHA, got %s\n' "$name" "$source_ref" >&2; exit 1 ;;
    esac
    git clone --quiet --no-checkout "$repository" "$destination"
    git -C "$destination" checkout --quiet --detach "$source_ref"
    test "$(git -C "$destination" rev-parse HEAD)" = "$source_ref"
}

QI_DIR=$WORK/qi
RUNTIME_DIR=$WORK/qi-runtime
GUI_DIR=$WORK/qi-gui
checkout_source QI https://github.com/qilang-project/qi.git "${QI_SOURCE_DIR:-}" "${QI_SOURCE_REF:-}" "$QI_DIR"
checkout_source QI_RUNTIME https://github.com/qilang-project/qi-runtime.git \
    "${QI_RUNTIME_SOURCE_DIR:-}" "${QI_RUNTIME_SOURCE_REF:-}" "$RUNTIME_DIR"
checkout_source QI_GUI https://github.com/qilang-project/qi-gui.git \
    "${QI_GUI_SOURCE_DIR:-}" "${QI_GUI_SOURCE_REF:-}" "$GUI_DIR"

runtime_target=$WORK/runtime-target
compiler_target=$WORK/compiler-target
cargo build --release --manifest-path "$RUNTIME_DIR/Cargo.toml" --target-dir "$runtime_target" >&2
QI_RUNTIME_DIR="$RUNTIME_DIR" cargo test --release --manifest-path "$QI_DIR/Cargo.toml" \
    --target-dir "$compiler_target" --test stdlib_abi_parity >&2
cargo build --release --manifest-path "$QI_DIR/Cargo.toml" --target-dir "$compiler_target" --bin qi >&2

rm -rf "$PREFIX"
mkdir -p "$PREFIX/bin" "$PREFIX/lib/qi"
cp "$compiler_target/release/qi" "$PREFIX/bin/qi"
cp "$runtime_target/release/libqi_runtime.a" "$PREFIX/lib/qi/libqi_runtime.a"
QI_BIN="$PREFIX/bin/qi" python3 "$ROOT/scripts/check-qi-compat.py" >&2
printf '%s\n' "$PREFIX"
