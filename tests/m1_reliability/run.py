#!/usr/bin/env python3
"""Run deterministic M1 timeout and automatic Agent budget reliability tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent


def wait_for_file(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        if process.poll() is not None:
            raise RuntimeError(f"fixture exited with status {process.returncode}")
        time.sleep(0.02)
    raise RuntimeError(f"timed out waiting for {path}")


def run_qi(test_file: str, env: dict[str, str]) -> str:
    completed = subprocess.run(
        ["qi", "run", str(TEST_DIR / test_file)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )
    print(f"===== {test_file} (exit {completed.returncode}) =====")
    print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise AssertionError(f"{test_file} exited with {completed.returncode}")
    if "FAIL " in completed.stdout:
        raise AssertionError(f"{test_file} reported a failed assertion")
    return completed.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-m1-reliability-") as temporary:
        temporary_path = Path(temporary)
        port_file = temporary_path / "port"
        state_file = temporary_path / "state.json"
        fixture = subprocess.Popen(
            [
                sys.executable,
                str(TEST_DIR / "fake_openai.py"),
                "--port-file",
                str(port_file),
                "--state-file",
                str(state_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_file(port_file, fixture)
            port = int(port_file.read_text(encoding="utf-8"))
            if port <= 3000:
                raise AssertionError(f"fixture used disallowed port {port}")
            print(f"fixture_port={port}")
            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port}"
            run_qi("budget_atomic_test.qi", env)
            run_qi("timeout_test.qi", env)
            run_qi("budget_test.qi", env)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            print("===== fixture state =====")
            print(json.dumps(state, sort_keys=True))
            expected_counts = {
                "slow": 1,
                "budget": 1,
                "exact": 1,
                "tool": 2,
                "retry_success": 2,
                "retry_exhausted": 3,
                "overrun": 1,
                "policy": 4,
            }
            if state["counts"] != expected_counts:
                raise AssertionError(
                    f"provider request counts were {state['counts']}, expected {expected_counts}"
                )
            if not state["tool_continuation_seen"]:
                raise AssertionError("provider did not receive the tool result continuation")
            expected_max_tokens = {
                "budget": [1],
                "exact": [1],
                "tool": [2, 2],
                "retry_success": [1, 1],
                "retry_exhausted": [1, 1, 1],
                "overrun": [1],
                "policy": [1, 1, 1, 2048],
            }
            for route, values in expected_max_tokens.items():
                if state["max_tokens"][route] != values:
                    raise AssertionError(
                        f"{route} max_tokens were {state['max_tokens'][route]}, expected {values}"
                    )
            print("PASS provider_request_counts")
            print("PASS provider_received_tool_continuation")
            print("PASS provider_received_admitted_max_tokens")
            return 0
        finally:
            fixture.terminate()
            try:
                fixture.wait(timeout=2)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.wait(timeout=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL runner: {error}", file=sys.stderr)
        raise
