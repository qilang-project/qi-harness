#!/usr/bin/env python3
"""Run the deterministic Agent lifecycle suite against a local fixture."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent
FIXTURE = ROOT / "tests" / "m1_reliability" / "fake_openai.py"
# PATH 上那个 qi 往往是**旧拷贝**（装的时候复制过去的，不是符号链接），
# 改了编译器不重装就还是老的 —— 于是测的根本不是刚改的那份。
QI_BIN = os.environ.get("QI_BIN", "qi")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-agent-lifecycle-") as temporary:
        temporary_path = Path(temporary)
        port_file = temporary_path / "port"
        state_file = temporary_path / "state.json"
        fixture = subprocess.Popen(
            [
                sys.executable,
                str(FIXTURE),
                "--port-file",
                str(port_file),
                "--state-file",
                str(state_file),
            ],
            cwd=ROOT,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not port_file.exists():
                if fixture.poll() is not None:
                    raise RuntimeError(f"fixture exited with status {fixture.returncode}")
                time.sleep(0.02)
            if not port_file.exists():
                raise RuntimeError("timed out waiting for fixture")

            port = int(port_file.read_text(encoding="utf-8"))
            if port <= 3000:
                raise AssertionError(f"fixture used disallowed port {port}")
            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port}"
            completed = subprocess.run(
                [QI_BIN, "run", str(TEST_DIR / "agent_lifecycle_test.qi")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # 这一条**编译再运行**一个 .qi，而且被测行为里有一次故意的 1 秒重试。
                # 闲时整套 3 秒，但 15 秒的余量只有 5 倍 —— 机器一忙就够不着：
                # 2026-09-14 有别的活把 load average 顶到 183，这里实测 19.9 秒，
                # 于是门禁红在超时上，而不是红在被测的东西上。
                # 超时是用来兜死循环的，不是用来卡性能的；90 秒仍然能兜住真卡死。
                timeout=90,
                check=False,
            )
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.returncode != 0 or "FAIL " in completed.stdout:
                raise AssertionError(
                    f"Agent lifecycle test failed with status {completed.returncode}"
                )
            return 0
        finally:
            fixture.terminate()
            try:
                fixture.wait(timeout=2)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.wait(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
