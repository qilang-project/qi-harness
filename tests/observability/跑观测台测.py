#!/usr/bin/env python3
"""起假 OpenAI + 观测台 → 抓 HTML 和 /metrics → 断言。

抓的是浏览器真会拿到的字节。渲染函数「返回了字符串」证明不了看板可用：
瀑布条的宽度算错、指标没注册、/metrics 没挂上，这些全都只在这一步暴露。
"""
from __future__ import annotations

import os
import json
import re
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
ROOT = HARNESS.parent
FIXTURE = HARNESS / "tests" / "m1_reliability" / "fake_openai.py"

# 默认用 PATH 上的 qi（跟 run-offline-tests.sh 其余部分一致）。
# 本地想测「刚构建的那份」就传 QI_BIN —— 装在 PATH 上的往往是旧拷贝。
QI = os.environ.get("QI_BIN", "qi")
RUNTIME = os.environ.get("QI_RUNTIME_LIB", "")
# 在哪个目录起 qi。
#
# monorepo 里用 qi-test：它的 qi_packages/ 是指向真源码的符号链接。
# 但 CI 里 qi-harness 是**独立 checkout**，根本没有兄弟目录 qi-test ——
# 写死就是 FileNotFoundError。那边靠 QI_PACKAGES_PATH 定位依赖，
# 从 harness 根起就行。
CWD = ROOT / "qi-test"
if not CWD.is_dir():
    CWD = HARNESS

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


def free_port() -> int:
    """要一个空闲的高位端口。写死端口在 CI 上会撞，撞了报的是连接被拒，很难指向真因。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    if port <= 3000:
        raise RuntimeError(f"拿到了不该用的端口 {port}")
    return port


def get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-obs-ui-") as tmp:
        tmp_path = Path(tmp)
        port_file = tmp_path / "port"
        state_file = tmp_path / "state.json"
        obs_port = free_port()

        fixture = subprocess.Popen(
            [sys.executable, str(FIXTURE),
             "--port-file", str(port_file), "--state-file", str(state_file)],
            cwd=HARNESS,
        )
        app = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not port_file.exists():
                if fixture.poll() is not None:
                    raise RuntimeError("夹具起不来")
                time.sleep(0.02)

            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{int(port_file.read_text())}"
            if RUNTIME:
                env["QI_RUNTIME_LIB"] = RUNTIME
            env["QI_OBS_PORT"] = str(obs_port)
            # 不设这个 qi-web 就不挂 /metrics（默认不开口子）
            env["QI_METRICS_TOKEN"] = "public"

            app = subprocess.Popen(
                [QI, "run", str(HERE / "观测台_测.qi")],
                cwd=CWD, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )

            # 等 agent 跑完（qi 那边打 READY）。qi 的 stdout 是全缓冲的，
            # 所以别指望能逐行读到 —— 改成轮询 HTTP 直到页面里出现运行记录。
            deadline = time.monotonic() + 90
            body = ""
            while time.monotonic() < deadline:
                if app.poll() is not None:
                    out = app.stdout.read() if app.stdout else ""
                    raise RuntimeError(f"观测台进程提前退出:\n{out}")
                try:
                    status, body = get(f"http://127.0.0.1:{obs_port}/")
                    if status == 200 and "看板自测代理" in body:
                        break
                except Exception:
                    pass
                time.sleep(0.3)

            status, body = get(f"http://127.0.0.1:{obs_port}/")
            check(status == 200, "看板页返回 200", str(status))
            check("观测台自测" in body, "标题用的是设置的那个")
            check('id="qi-live"' in body, "有 LiveView 挂载点")
            check("window.qiSend" in body or "qiSend" in body, "带上了实时运行时")
            check("setInterval" in body, "带上了定时刷新脚本")

            # ——— 运行确实出现在看板上 ———
            check("看板自测代理" in body, "左栏列出了这次运行")
            check("fixture_echo" in body, "瀑布图上显示的是工具名",
                  "工具名没进事件流的话这里会是 step-0-tool-0")
            check("obs-bar" in body, "画出了瀑布条")

            # ——— 瀑布条的几何 ———
            bars = re.findall(r'class="obs-bar[^"]*"\s+style="left:(\d+)%;width:(\d+)%"', body)
            check(len(bars) >= 4, "至少 4 条（agent/turn/llm/tool）", f"实际 {len(bars)}")
            check(all(int(l) + int(w) <= 100 for l, w in bars),
                  "没有条超出轨道右边界", str(bars))
            check(all(int(w) >= 1 for l, w in bars),
                  "再快的跨度也有最小可见宽度", str(bars))

            # 种类着色：llm 和 tool 要能一眼分开
            check("kind-llm" in body and "kind-tool" in body, "llm/tool 分别着色")

            # ——— /metrics ———
            status, metrics = get(f"http://127.0.0.1:{obs_port}/metrics")
            check(status == 200, "/metrics 返回 200", str(status))
            for name in [
                "harness_agent_runs_total",
                "harness_llm_calls_total",
                "harness_llm_duration_seconds",
                "harness_llm_tokens_total",
                "harness_tool_calls_total",
                "harness_tool_duration_seconds",
            ]:
                check(name in metrics, f"/metrics 有 {name}")

            check("# TYPE harness_llm_duration_seconds histogram" in metrics,
                  "LLM 时延是直方图")
            # 桶组_慢：LLM 是秒到分钟量级，用快桶的话全落 +Inf
            slow = re.findall(r'harness_llm_duration_seconds_bucket\{[^}]*le="([0-9.+inf]+)"', metrics)
            check(any(float(x) >= 60 for x in slow if x != "+Inf"),
                  "LLM 直方图用的是慢桶（有 ≥60s 的桶）", str(slow))

            check('harness_tool_calls_total{' in metrics and 'tool="fixture_echo"' in metrics,
                  "工具指标带 tool 标签")
            m = re.search(r'harness_llm_tokens_total\{[^}]*\}\s+([0-9]+)', metrics)
            check(m is not None and int(m.group(1)) > 0,
                  "token 计数非零", m.group(0) if m else "没匹配到")

            # run_id 绝不能当标签 —— 高基数标签是能把 Prometheus 撑爆的那种错
            check('run="' not in metrics and "run_id" not in metrics,
                  "指标标签里没有 run id（高基数）")

            # ——— 「实时」这一条 ———
            # 只验首屏的话，WS 断了看板就是个静态页，而首屏永远是对的。
            sys.path.insert(0, str(HERE))
            from 迷你ws import 迷你WS

            ws = 迷你WS("127.0.0.1", obs_port, "/ws", timeout=8.0)
            try:
                # 带准入的连接**首帧必须是 __订阅__**，否则服务端不建状态、
                # 一帧都不推就断开。浏览器那边这步由内嵌运行时代劳，
                # 手写客户端得自己发 —— 不发的表现是「握手成功但永远收不到东西」。
                ws.send(json.dumps({"event": "__订阅__", "payload": {}}))
                first = ws.recv_json()
                check("html" in first, "连上就收到一帧初始渲染", str(first)[:200])
                check("看板自测代理" in first.get("html", ""),
                      "初始帧里有运行记录")

                # 页面上那个定时器发的就是这个
                ws.send(json.dumps({"event": "刷新", "payload": {}}))
                frame = ws.recv_json()
                check("html" in frame or "commands" in frame,
                      "刷新事件能拿到下行帧", str(frame)[:200])

                # 点左栏某次运行 → 服务端应答一帧
                run_id = re.search(r'data-value-run="([^"]+)"', body)
                check(run_id is not None, "页面里有可点的运行项")
                if run_id:
                    ws.send(json.dumps({
                        "event": "选运行",
                        "payload": {"run": run_id.group(1)},
                    }))
                    picked = ws.recv_json()
                    check("html" in picked or "commands" in picked,
                          "选运行能拿到下行帧", str(picked)[:200])
            finally:
                ws.close()

            print()
            print(f"通过 {passes}，失败 {len(failures)}")
            return 1 if failures else 0
        finally:
            # 被 terminate 的 qi 进程来不及自清，会在源码旁边留下编译产物。
            # 交给 .gitignore 只是安全网，这儿主动扫掉。
            for junk in ("观测台_测", "观测台_测.o"):
                (HERE / junk).unlink(missing_ok=True)
            for proc in (app, fixture):
                if proc is None:
                    continue
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
