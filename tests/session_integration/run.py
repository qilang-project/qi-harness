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


def run_qi(name: str, env: dict[str, str]) -> None:
    completed = subprocess.run(
        ["qi", "run", str(TEST_DIR / name)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    assertion_failed = any(
        line == "FAIL" or line.startswith("FAIL ")
        for line in completed.stdout.splitlines()
    )
    if completed.returncode != 0 or assertion_failed:
        raise AssertionError(f"{name} failed with status {completed.returncode}")


def main() -> int:
    env = os.environ.copy()
    run_qi("restore_test.qi", env)
    with tempfile.TemporaryDirectory(prefix="qi-session-integration-") as temporary:
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
            port = int(port_file.read_text(encoding="utf-8"))
            if port <= 3000:
                raise AssertionError(f"fixture used disallowed port {port}")
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port}"
            run_qi("agent_recording_test.qi", env)
            run_qi("failed_turn_persistence_test.qi", env)
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
