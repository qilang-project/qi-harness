#!/usr/bin/env python3
"""起假 collector → 跑 qi → 断言收到的 OTLP 字节。

断言的重点是 **parentSpanId**：树形关系只有在这一步才验得出来。
之前的实现根本没写这个字段，qi 侧一切正常，推到 Jaeger 才发现整棵树散了。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
ROOT = HARNESS.parent

# 默认用 PATH 上的 qi（跟 run-offline-tests.sh 其余部分一致）。
# 本地想测「刚构建的那份」就传 QI_BIN —— 装在 PATH 上的往往是旧拷贝。
QI = os.environ.get("QI_BIN", "qi")
RUNTIME = os.environ.get("QI_RUNTIME_LIB", "")
# 从 qi-test 里跑：它的 qi_packages/ 是指向真源码的符号链接。
# 别在 /tmp 下跑 —— 编译器会往上逐级扫每个子目录找同名包，
# 祖先目录里任何一份残留副本都会静默盖掉这里指定的那个。
CWD = ROOT / "qi-test"

HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")

failures: list[str] = []
passes = 0


def check(cond: bool, name: str, detail: str = "") -> None:
    global passes
    if cond:
        print(f"PASS {name}")
        passes += 1
    else:
        print(f"FAIL {name}" + (f"\n  {detail}" if detail else ""))
        failures.append(name)


def spans_of(envelope: dict) -> list[dict]:
    out = []
    for rs in envelope.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            out.extend(ss.get("spans", []))
    return out


def attrs_of(span: dict) -> dict:
    result = {}
    for a in span.get("attributes", []):
        value = a.get("value", {})
        result[a["key"]] = value.get("stringValue", value.get("intValue"))
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-otlp-") as tmp:
        tmp_path = Path(tmp)
        port_file = tmp_path / "port"
        out_file = tmp_path / "captured.jsonl"
        collector = subprocess.Popen(
            [sys.executable, str(HERE / "假collector.py"),
             "--port-file", str(port_file), "--out", str(out_file)]
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not port_file.exists():
                if collector.poll() is not None:
                    raise RuntimeError("collector 起不来")
                time.sleep(0.02)
            if not port_file.exists():
                raise RuntimeError("等 collector 超时")

            port = int(port_file.read_text())
            if port <= 3000:
                raise AssertionError(f"collector 用了不该用的端口 {port}")

            env = os.environ.copy()
            env["QI_OTLP_URL"] = f"http://127.0.0.1:{port}"
            if RUNTIME:
                env["QI_RUNTIME_LIB"] = RUNTIME
            run = subprocess.run(
                [QI, "run", str(HERE / "OTLP导出_测.qi")],
                cwd=CWD, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
            )
            print(run.stdout, end="")
            if run.returncode != 0:
                print("qi 退出码非零")
                return 1

            time.sleep(0.3)
            captured = [json.loads(l) for l in out_file.read_text().splitlines() if l.strip()]

            check(len(captured) == 2, "收到两次导出", f"实际 {len(captured)}")
            if len(captured) < 1:
                return 1

            check(all(c["path"] == "/v1/traces" for c in captured),
                  "打在 /v1/traces 上", str([c["path"] for c in captured]))
            check(all("application/json" in c["content_type"] for c in captured),
                  "Content-Type 是 json")

            first = json.loads(captured[0]["body"])
            spans = spans_of(first)
            check(len(spans) == 5, "第一棵树 5 个 span", f"实际 {len(spans)}")

            # service.name 要落在 resource 上，不是挂在每个 span 上
            res_attrs = {}
            for a in first["resourceSpans"][0]["resource"]["attributes"]:
                res_attrs[a["key"]] = a["value"].get("stringValue")
            check(res_attrs.get("service.name") == "观测台自测",
                  "service.name 在 resource 上", str(res_attrs))

            by_id = {s["spanId"]: s for s in spans}
            check(all(HEX16.match(s["spanId"]) for s in spans),
                  "spanId 都是 16 位 hex", str([s["spanId"] for s in spans]))
            check(all(HEX32.match(s["traceId"]) for s in spans),
                  "traceId 都是 32 位 hex", str([s["traceId"] for s in spans]))
            check(len({s["traceId"] for s in spans}) == 1,
                  "同一棵树 traceId 一致")
            check(spans[0]["traceId"] == "6f1a2b3c4d5e6f708192a3b4c5d6e7f8",
                  "traceId 就是 run_id 去横杠", spans[0]["traceId"])

            # ——— 核心：父子关系 ———
            roots = [s for s in spans if not s.get("parentSpanId")]
            check(len(roots) == 1, "恰好一个根 span",
                  f"实际 {len(roots)} 个：{[s['name'] for s in roots]}")
            children = [s for s in spans if s.get("parentSpanId")]
            check(len(children) == 4, "4 个非根 span 都带 parentSpanId",
                  f"实际 {len(children)}")
            check(all(s["parentSpanId"] in by_id for s in children),
                  "每个 parentSpanId 都指向本批里真实存在的 span")

            # 树形：root(agent) → turn → {llm, tool, llm}
            root = roots[0]
            turn = [s for s in children if s["parentSpanId"] == root["spanId"]]
            check(len(turn) == 1, "根下恰好一个 turn", str([s["name"] for s in turn]))
            if turn:
                leaves = [s for s in children if s["parentSpanId"] == turn[0]["spanId"]]
                check(len(leaves) == 3, "turn 下三个叶子（llm/tool/llm）",
                      str([s["name"] for s in leaves]))
                kinds = sorted(attrs_of(s).get("qi.span.kind", "") for s in leaves)
                check(kinds == ["llm", "llm", "tool"], "叶子种类正确", str(kinds))

            # 时间：结束不早于开始，否则 Jaeger 画成负时长
            check(all(int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"]) for s in spans),
                  "endTime 不早于 startTime")
            check(all(len(s["startTimeUnixNano"]) >= 19 for s in spans),
                  "时间戳是纳秒量级",
                  str([s["startTimeUnixNano"] for s in spans]))

            # token / 出错 属性
            llm_toks = sorted(
                int(attrs_of(s).get("tokens", 0)) for s in spans
                if attrs_of(s).get("qi.span.kind") == "llm"
            )
            check(llm_toks == [0, 120], "llm 的 token 落到了对的 span 上", str(llm_toks))
            errored = [s for s in spans if "error" in attrs_of(s)]
            check(len(errored) == 1 and attrs_of(errored[0])["error"] == "exhausted",
                  "出错的 span 带 error 属性",
                  str([attrs_of(s) for s in errored]))

            # ——— 两次运行的 span id 不能撞车 ———
            if len(captured) == 2:
                second = spans_of(json.loads(captured[1]["body"]))
                check(len(second) == 2, "第二棵树 2 个 span", f"实际 {len(second)}")
                overlap = {s["spanId"] for s in spans} & {s["spanId"] for s in second}
                check(not overlap, "两次运行的 spanId 不重叠", f"重叠: {overlap}")
                check(spans[0]["traceId"] != second[0]["traceId"],
                      "两次运行 traceId 不同")

            print()
            print(f"通过 {passes}，失败 {len(failures)}")
            return 1 if failures else 0
        finally:
            collector.terminate()
            try:
                collector.wait(timeout=2)
            except subprocess.TimeoutExpired:
                collector.kill()


if __name__ == "__main__":
    raise SystemExit(main())
