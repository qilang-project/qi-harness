#!/usr/bin/env bash
# 意图 / 推理顺序 / 问答管线
#
#   意图与推理顺序_测.qi  纯组装，不需要模型也不需要 Graph
#   问答管线_测.qi        要假模型 + qi-graph（模式从 Graph 来）；解析不到 Graph 就跳过，
#                         跟 tests/graph_memory/run.sh 一个道理 —— 除这两条外
#                         qi-harness 一概不依赖 Graph，不该为它把主干卡红。
# 注意 macOS bash 3.2：shell 变量名一律 ASCII。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# qi 的取法：QI_BIN → monorepo 里刚构建的那份 → PATH。
# **别把 PATH 排在前面**：这台机器上 /usr/local/bin/qi 是很久以前装的拷贝，
# 连 z3 都链不上，一跑就 Abort trap: 6，错误信息还指向 dyld，看半天看不出是选错了二进制。
# CI 上没有 monorepo 的 target/，自然落到 PATH（那儿是现编的 pinned 工具链）。
QI="${QI_BIN:-}"
if [ -z "$QI" ]; then
    if [ -x "$ROOT/../target/release/qi" ]; then
        QI="$ROOT/../target/release/qi"
    else
        QI="$(command -v qi 2>/dev/null || printf '%s' qi)"
    fi
fi
[ -x "$QI" ] || { echo "找不到 qi 二进制：$QI" >&2; exit 1; }
cd "$ROOT"

TMP="$(mktemp -d)"
FAKE_PID=""
cleanup() {
    [ -n "$FAKE_PID" ] && kill "$FAKE_PID" 2>/dev/null
    rm -rf "$TMP"
    rm -f /tmp/qi_harness_问答管线_测.kv
}
trap cleanup EXIT

fail=0

# ── 一、纯组装那条 ──
if ! "$QI" compile tests/intent/意图与推理顺序_测.qi -o "$TMP/a" >"$TMP/log" 2>&1; then
    echo "error: 意图与推理顺序_测 编译失败" >&2; sed 's/^/    /' "$TMP/log" >&2; exit 1
fi
"$TMP/a" || fail=1

# ── 二、端到端那条（要 Graph）──
if ! "$QI" compile tests/intent/问答管线_测.qi -o "$TMP/b" >"$TMP/log" 2>&1; then
    if grep -qE '无法找到导入模块: (Graph|KV)|注册中心依赖|尚未安装' "$TMP/log"; then
        printf 'skip: 问答管线_测 需要 qi-graph(Graph)，当前解析不到 —— 跳过\n'
        sed 's/^/    /' "$TMP/log"
        exit "$fail"
    fi
    echo "error: 问答管线_测 编译失败" >&2; sed 's/^/    /' "$TMP/log" >&2; exit 1
fi

PORT_FILE="$TMP/port"
python3 tests/intent/管线假模型.py --port-file "$PORT_FILE" >/dev/null 2>&1 &
FAKE_PID=$!
n=0
until [ -s "$PORT_FILE" ]; do
    sleep 0.2; n=$((n + 1))
    [ "$n" -gt 100 ] && { echo "假模型起不来" >&2; exit 1; }
done
FAKE_LLM_ENDPOINT="http://127.0.0.1:$(cat "$PORT_FILE")/v1" "$TMP/b" || fail=1

exit "$fail"
