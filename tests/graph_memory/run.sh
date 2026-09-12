#!/bin/sh
# 图记忆（关系型召回）套件。
#
# 两支：
#   图记忆_测.qi    存储层：安全名往返 / 幂等 / N 跳 / 去重 / 上限 / 路径 / 重开
#   三路合并_测.qi  代理层：启用图记忆 / 记关联，以及 带记忆运行 到底注入了什么
#
# 后者断言的东西 qi 那边看不见（LLM 调用失败时会话历史不落），所以在这里对
# stdout 的 llm_call 事件核对注入的提示 —— 词法块和关系块必须都在，且是同一
# 个标题块下的连续两行。
#
# **依赖不在就跳过，不算失败**：本套件要 qi-graph（包名 Graph）+ qi-kv（包名 KV），
# 而 qi-harness 的其他部分一概不要 —— 不该因为它们没装就把整条质量门卡红。
# 装法：把 qi-graph / qi-kv 放进 QI_PACKAGES_PATH（或任一祖先目录），并让
# qi-graph 能解析到 KV（qi 包 安装，或 qi-graph/qi_packages/KV 指过去）。
set -eu

# 编译器：默认用 workspace 的 release 产物。**不要**直接用 PATH 里的 qi ——
# 本机 /usr/local/bin/qi 是旧拷贝，起手就 dyld 报 libz3 找不到，看着像套件挂了。
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
# 先 QI_BIN，再 PATH 上的 qi（CI 就是这么给的），最后才猜 monorepo 布局。
# 原来只认 $ROOT/target/release/qi —— 仓库单独 checkout 时那儿什么都没有，
# 这条套件在 CI 上从来没真跑过。
QI="${QI_BIN:-$(command -v qi 2>/dev/null || printf '%s' "$ROOT/target/release/qi")}"
export QI_RUNTIME_LIB="${QI_RUNTIME_LIB:-$ROOT/qi-runtime/target/release/libqi_runtime.a}"
[ -x "$QI" ] || { echo "找不到 qi 二进制：$QI" >&2; exit 1; }

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$ROOT"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"; rm -f /tmp/qi_harness_graph_memory_test.kv \
    /tmp/qi_harness_graph_memory_merge_test.db \
    /tmp/qi_harness_graph_memory_merge_test.kv' EXIT

STORE_TEST=tests/graph_memory/图记忆_测.qi
MERGE_TEST=tests/graph_memory/三路合并_测.qi

# ── 依赖探针 ─────────────────────────────────────────────────────
# 只有 compile 会真的去解析包（check 不会），所以探针必须编到底。
if ! "$QI" compile "$STORE_TEST" -o "$TMP/probe" >"$TMP/probe.log" 2>&1; then
    if grep -qE '无法找到导入模块: (Graph|KV)|注册中心依赖|尚未安装' "$TMP/probe.log"; then
        printf 'skip: 图记忆套件需要 qi-graph(Graph) + qi-kv(KV)，当前解析不到 —— 跳过\n'
        sed 's/^/    /' "$TMP/probe.log"
        exit 0
    fi
    printf 'error: 图记忆套件编译失败\n' >&2
    sed 's/^/    /' "$TMP/probe.log" >&2
    exit 1
fi

fail=0

# ── 存储层 ───────────────────────────────────────────────────────
rm -f /tmp/qi_harness_graph_memory_test.kv
if OUT=$("$TMP/probe" 2>&1); then :; else fail=1; fi
printf '%s\n' "$OUT"
case "$OUT" in
    *"FAIL "*) printf 'error: 图记忆_测 有断言失败\n' >&2; fail=1 ;;
esac
case "$OUT" in
    *"图记忆 全部通过"*) ;;
    *) printf 'error: 图记忆_测 没跑到底\n' >&2; fail=1 ;;
esac

# ── 代理层 + 注入的提示 ──────────────────────────────────────────
rm -f /tmp/qi_harness_graph_memory_merge_test.db /tmp/qi_harness_graph_memory_merge_test.kv
if MERGE_OUT=$("$QI" run "$MERGE_TEST" 2>&1); then :; else fail=1; fi
printf '%s\n' "$MERGE_OUT"
case "$MERGE_OUT" in
    *"FAIL "*) printf 'error: 三路合并_测 有断言失败\n' >&2; fail=1 ;;
esac
case "$MERGE_OUT" in
    *"三路合并 全部通过"*) ;;
    *) printf 'error: 三路合并_测 没跑到底\n' >&2; fail=1 ;;
esac

assert_injected() {
    name=$1
    frag=$2
    if printf '%s' "$MERGE_OUT" | grep -qF -- "$frag"; then
        printf 'PASS %s\n' "$name"
    else
        printf 'FAIL %s: llm_call 事件里没找到 [%s]\n' "$name" "$frag" >&2
        fail=1
    fi
}

assert_not_injected() {
    name=$1
    frag=$2
    if printf '%s' "$MERGE_OUT" | grep -qF -- "$frag"; then
        printf 'FAIL %s: llm_call 事件里不该有 [%s]\n' "$name" "$frag" >&2
        fail=1
    else
        printf 'PASS %s\n' "$name"
    fi
}

# 只开词法：标题块 + 词法行，没有关系行
assert_injected "注入/只词法那一路" \
    '"detail":"## 相关记忆与过往经验\n-【偏好】用户喜欢简短回答\n(参考以上背景作答)\n\n只有词法这一路"'
# 两路都开：两个块在同一个标题下连续排布，格式一致（都是 -【…】…）。
# 关系那一路默认 **2 跳**（2026-09-12 起），所以 研究员 → mcp:fetch → 网页 两条都在；
# 只有一条就是默认跳数被改回 1 了。
assert_injected "注入/两路合并成一个块" \
    '"detail":"## 相关记忆与过往经验\n-【偏好】用户喜欢简短回答\n-【用过】研究员 → mcp:fetch\n-【能取】mcp:fetch → 网页\n(参考以上背景作答)\n\n两路都要"'
# 都没命中：原样发出，一个字都不加
assert_injected "注入/都没命中就不注入" '"detail":"谁都不该命中"'
assert_not_injected "注入/没命中时没有标题块" \
    '"detail":"## 相关记忆与过往经验\n(参考以上背景作答)\n\n谁都不该命中"'

if [ "$fail" -ne 0 ]; then
    exit 1
fi
printf '图记忆套件通过\n'
