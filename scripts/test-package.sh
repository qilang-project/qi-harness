#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=${VERSION:-$(python3 "$ROOT/scripts/version.py" current)}
case "$VERSION" in
    v*) VERSION=${VERSION#v} ;;
esac

WORK=$(mktemp -d "${TMPDIR:-/tmp}/qi-harness-package.XXXXXX")
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

DIST=${DIST:-"$WORK/dist"}
if [ ! -f "$DIST/qi-harness-v$VERSION.tar.gz" ] || [ ! -f "$DIST/qi-harness-v$VERSION.zip" ]; then
    OUTPUT_DIR=$DIST VERSION=$VERSION SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-0} "$ROOT/scripts/package.sh"
fi

python3 "$ROOT/scripts/tests/verify_package.py" "$DIST" "$VERSION" "$WORK"

REPRO_DIST=$WORK/repro-dist
OUTPUT_DIR=$REPRO_DIST VERSION=$VERSION SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-0} "$ROOT/scripts/package.sh"
for archive in "qi-harness-v$VERSION.tar.gz" "qi-harness-v$VERSION.zip" SHA256SUMS; do
    if ! cmp -s "$DIST/$archive" "$REPRO_DIST/$archive"; then
        printf 'error: package is not reproducible: %s\n' "$archive" >&2
        exit 1
    fi
done
printf 'package reproducibility verified for v%s\n' "$VERSION"

if ! command -v qi >/dev/null 2>&1; then
    printf 'error: qi is not on PATH; package consumer verification cannot run\n' >&2
    exit 127
fi
python3 "$ROOT/scripts/check-qi-compat.py"

mkdir -p "$WORK/qi_packages"
cp -R "$WORK/tar/qi-harness-v$VERSION" "$WORK/qi_packages/Harness"
cat > "$WORK/consumer.qi" <<'EOF'
包 主程序;

导入 Harness::{模型配置, 默认配置};

函数 入口() {
    变量 配置: 模型配置 = 默认配置();
}
EOF

(
    cd "$WORK"
    QI_PACKAGES_PATH="$WORK/qi_packages" qi check consumer.qi
)

printf 'package consumer verification passed for v%s\n' "$VERSION"
