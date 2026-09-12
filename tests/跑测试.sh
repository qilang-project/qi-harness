#!/usr/bin/env bash
# qi-harness 全量测试。
#
# 需要假模型的用例各起各的、跑完就关 —— 共用一个会串：长时程那个假模型按
# response_format 分辨「干活」和「问进度」两步，跟通用假模型语义不同；残留的
# .db 也会让下一个用例读到上一次的状态。
#
# ⚠ 变量名一律 ASCII（bash 不支持非 ASCII 变量名，`中文=1` 会被当命令执行，
#   失败后变量为空、断言静默通过）。写这个脚本时我又栽了一次。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
QI="${QI_BIN:-$ROOT/target/release/qi}"
export QI_RUNTIME_LIB="${QI_RUNTIME_LIB:-$ROOT/qi-runtime/target/release/libqi_runtime.a}"
[ -x "$QI" ] || { echo "找不到 qi：$QI" >&2; exit 1; }

# macOS：SDK 27 的 tbd 里有 arm64e.x1，clang 解析不了，任何程序都链接不出来。
# 不先探一下的话，每个用例都会卡在链接、报错还看不出根因。
if [ "$(uname)" = "Darwin" ]; then
    probe="$(mktemp -d)"
    printf '包 主程序;\n函数 入口() { 打印行("x"); }\n' > "$probe/p.qi"
    if ! "$QI" run "$probe/p.qi" >/dev/null 2>&1; then
        echo "连两行的 hello world 都链接不出来。多半是 macOS SDK 的问题：" >&2
        echo "  export SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.sdk" >&2
        rm -rf "$probe"; exit 1
    fi
    rm -rf "$probe"
fi

total=0
failed=0
FAKE_PID=""

start_fake() {   # $1 = 假模型脚本，$2… = 额外参数 → 打印端点
    local pf script
    script="$1"; shift
    pf="$(mktemp)"
    python3 "$script" --port-file "$pf" "$@" >/dev/null 2>&1 &
    FAKE_PID=$!
    local n=0
    until [ -s "$pf" ]; do
        sleep 0.2
        n=$((n + 1))
        [ "$n" -gt 100 ] && return 1
    done
    echo "http://127.0.0.1:$(cat "$pf")/v1"
}

stop_fake() {
    [ -n "$FAKE_PID" ] && kill "$FAKE_PID" 2>/dev/null
    FAKE_PID=""
}
trap stop_fake EXIT

run_one() {      # $1 = 相对 qi-harness 的 .qi 路径   $2 = 端点（可空）
    local f="$1" out rc summary
    total=$((total + 1))
    out=$(cd "$HERE/.." && FAKE_LLM_ENDPOINT="${2:-}" "$QI" run "$f" 2>&1)
    rc=$?
    summary=$(echo "$out" | grep -oE '通过 [0-9]+，失败 [0-9]+|全部通过' | tail -1)

    # 判据（三条独立的失败信号，任一命中就挂）：
    #   1. 退出码非零
    #   2. 输出里有以 FAIL 开头的行
    #   3. 有「通过 N，失败 M」汇总且 M > 0
    # 其余算通过。**不能把「没有汇总」当通过** —— 第一版就是那么写的，结果
    # 这个 shell 没 SDKROOT，19 个用例全卡在 clang 链接一个都没跑，脚本照报
    # 「19/19 通过」。所以上面加了 hello world 探针，这里也不再放过退出码。
    # 反过来，各家汇总格式不一（有的只打 PASS 行、有的打「导出返回 0」），
    # 所以「没有汇总」本身不是失败信号。
    local fails
    fails=$(echo "$out" | grep -c '^FAIL')
    if [ "$rc" -ne 0 ]; then
        printf "  FAIL %-26s 退出码 %s\n" "$(basename "$f")" "$rc"
        echo "$out" | tail -4 | sed 's/^/         /'
        failed=$((failed + 1))
    elif [ "$fails" -gt 0 ] || echo "$summary" | grep -qE '失败 [1-9]'; then
        printf "  FAIL %-26s %s\n" "$(basename "$f")" "${summary:-$fails 条 FAIL}"
        echo "$out" | grep '^FAIL' | sed 's/^/         /' | head -5
        failed=$((failed + 1))
    else
        printf "  ok   %-26s %s\n" "$(basename "$f")" "${summary:-（无汇总行，退出码 0 且无 FAIL）}"
    fi
}

rm -f /tmp/长时程测.db /tmp/长时程推进测.db /tmp/图记忆背景测.db /tmp/图记忆背景测.kv \
      /tmp/图接线测.db /tmp/图接线测.kv /tmp/图接线提示.txt /tmp/步数上限测.db /tmp/验收测.db /tmp/并发写图测.kv

echo "── 两步协议假模型 ──"
E=$(start_fake "$HERE/longrun/听话的假模型.py") && run_one tests/longrun/推进_测.qi "$E"
stop_fake

echo "── 图闭环假模型（会调 记事实，并把收到的提示存档） ──"
PROMPT_DUMP=/tmp/图接线提示.txt
export PROMPT_DUMP
E=$(start_fake "$HERE/longrun/图循环假模型.py" --dump-prompts "$PROMPT_DUMP") \
    && run_one tests/longrun/图接线_测.qi "$E"
stop_fake
unset PROMPT_DUMP

echo "── 数步假模型（每段最多步 到底生不生效） ──"
E=$(start_fake "$HERE/longrun/数步假模型.py") && run_one tests/longrun/步数上限_测.qi "$E"
stop_fake

echo "── 老报完成的假模型（里程碑验收闸） ──"
E=$(start_fake "$HERE/longrun/老报完成的假模型.py") && run_one tests/longrun/验收_测.qi "$E"
stop_fake

echo "── 通用假模型 ──"
E=$(start_fake "$HERE/service_persistence/fake_openai.py") || { echo "假模型起不来" >&2; exit 1; }
run_one tests/longrun/长时程_测.qi "$E"
run_one tests/context/自动路径_测.qi "$E"
stop_fake

echo "── 不需要模型 ──"
for f in $(cd "$HERE/.." && find tests -name '*_测.qi' | sort); do
    case "$f" in
        */推进_测.qi | */长时程_测.qi | */自动路径_测.qi | */图接线_测.qi \
            | */步数上限_测.qi | */验收_测.qi) continue ;;
        # 这两个要真 LLM 端点：端到端_测 的断言里要 provider 返回的真实 token，
        # 观测台_测 要能真的发出请求。没凭据时它们不该算数。
        */端到端_测.qi | */观测台_测.qi) continue ;;
    esac
    run_one "$f" ""
done

echo ""
echo "qi-harness: $((total - failed))/$total 通过"
[ "$failed" -gt 0 ] && exit 1
exit 0
