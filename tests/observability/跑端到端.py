#!/usr/bin/env python3
"""起假 OpenAI → 真跑一次 agent → 验跨度树。

复用 tests/m1_reliability/fake_openai.py 那个夹具：确定性、离线、不花钱。
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
ROOT = HARNESS.parent
FIXTURE = HARNESS / "tests" / "m1_reliability" / "fake_openai.py"

# 默认用 PATH 上的 qi（跟 run-offline-tests.sh 其余部分一致）。
# 本地想测「刚构建的那份」就传 QI_BIN —— 装在 PATH 上的往往是旧拷贝。
QI = os.environ.get("QI_BIN", "qi")
RUNTIME = os.environ.get("QI_RUNTIME_LIB", "")
CWD = ROOT / "qi-test"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-obs-e2e-") as tmp:
        tmp_path = Path(tmp)
        port_file = tmp_path / "port"
        state_file = tmp_path / "state.json"
        fixture = subprocess.Popen(
            [sys.executable, str(FIXTURE),
             "--port-file", str(port_file), "--state-file", str(state_file)],
            cwd=HARNESS,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not port_file.exists():
                if fixture.poll() is not None:
                    raise RuntimeError(f"夹具退出，状态 {fixture.returncode}")
                time.sleep(0.02)
            if not port_file.exists():
                raise RuntimeError("等夹具超时")

            port = int(port_file.read_text(encoding="utf-8"))
            if port <= 3000:
                raise AssertionError(f"夹具用了不该用的端口 {port}")

            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port}"
            if RUNTIME:
                env["QI_RUNTIME_LIB"] = RUNTIME
            run = subprocess.run(
                [QI, "run", str(HERE / "端到端_测.qi")],
                cwd=CWD, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
            )
            print(run.stdout, end="" if run.stdout.endswith("\n") else "\n")
            if run.returncode != 0 or "FAIL " in run.stdout:
                print("端到端观测测试失败")
                return 1
            return 0
        finally:
            fixture.terminate()
            try:
                fixture.wait(timeout=2)
            except subprocess.TimeoutExpired:
                fixture.kill()


if __name__ == "__main__":
    raise SystemExit(main())
