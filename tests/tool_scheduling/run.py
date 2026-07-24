#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-tool-scheduling-") as temporary:
        port_file = Path(temporary) / "port"
        fixture = subprocess.Popen(
            [sys.executable, str(TEST_DIR / "fake_openai.py"), "--port-file", str(port_file)],
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

            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port_file.read_text(encoding='utf-8')}"
            completed = subprocess.run(
                ["qi", "run", str(TEST_DIR / "scheduling_test.qi")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=20,
            )
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.returncode != 0 or "FAIL " in completed.stdout:
                raise AssertionError(f"scheduling test failed with status {completed.returncode}")
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
