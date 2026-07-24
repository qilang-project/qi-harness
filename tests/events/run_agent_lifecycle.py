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
                ["qi", "run", str(TEST_DIR / "agent_lifecycle_test.qi")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
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
