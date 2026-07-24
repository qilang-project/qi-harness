#!/usr/bin/env python3
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
QI = os.environ.get("QI_BIN", "qi")


def wait_for(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        if process.poll() is not None:
            raise RuntimeError(f"fixture exited with {process.returncode}")
        time.sleep(0.02)
    raise RuntimeError("fixture did not publish its port")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-reliable-stream-") as directory:
        temporary = Path(directory)
        port_file = temporary / "port"
        state_file = temporary / "state.json"
        database = temporary / "journal.db"
        fixture = subprocess.Popen(
            [sys.executable, str(TEST_DIR / "fake_openai.py"), "--port-file", str(port_file), "--state-file", str(state_file)],
            cwd=ROOT,
            text=True,
        )
        try:
            wait_for(port_file, fixture)
            port = int(port_file.read_text(encoding="utf-8"))
            if port <= 3000:
                raise AssertionError(f"fixture used disallowed port {port}")
            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{port}"
            env["QI_STREAM_TEST_DB"] = str(database)
            completed = subprocess.run(
                [QI, "run", str(TEST_DIR / "reliable_stream_test.qi")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.returncode != 0 or "FAIL " in completed.stdout:
                raise AssertionError(f"reliable stream Qi test failed with {completed.returncode}")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            expected = {"success": 1, "retry": 2, "partial": 1, "cancel": 1,
                        "stall-before-first": 1, "stall-midstream": 1, "total-deadline": 1,
                        "idempotent": 1, "x": 1, "unknown-usage": 1, "stale-fence": 1,
                        "terminal-failure": 1}
            if state["counts"] != expected:
                raise AssertionError(f"provider counts {state['counts']} != {expected}")
            expected_max_tokens = {
                "retry": [1, 1],
                "x": [1],
                "unknown-usage": [1],
                "stale-fence": [2],
            }
            for prompt, values in expected_max_tokens.items():
                if state["max_tokens"].get(prompt) != values:
                    raise AssertionError(
                        f"{prompt} max_tokens were {state['max_tokens'].get(prompt)}, expected {values}"
                    )
            print("PASS reliable_stream_provider_counts")
            print("PASS reliable_stream_provider_received_max_tokens")
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
