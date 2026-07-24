#!/usr/bin/env python3
"""Deterministic OpenAI-compatible fixture for timeout and Agent budget tests."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FixtureState:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.lock = threading.Lock()
        self.counts = {
            "slow": 0,
            "budget": 0,
            "exact": 0,
            "tool": 0,
            "retry_success": 0,
            "retry_exhausted": 0,
            "overrun": 0,
            "policy": 0,
        }
        self.tool_continuation_seen = False
        self.max_tokens: dict[str, list[int | None]] = {
            route: [] for route in self.counts
        }
        self.write()

    def record(
        self,
        route: str,
        max_tokens: int | None,
        tool_continuation_seen: bool = False,
    ) -> None:
        with self.lock:
            self.counts[route] += 1
            self.max_tokens[route].append(max_tokens)
            self.tool_continuation_seen |= tool_continuation_seen
            self.write()

    def write(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "counts": self.counts,
                    "max_tokens": self.max_tokens,
                    "tool_continuation_seen": self.tool_continuation_seen,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def completion(content: str, total_tokens: int) -> dict[str, object]:
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": total_tokens - 1,
            "completion_tokens": 1,
            "total_tokens": total_tokens,
        },
    }


def tool_call_completion() -> dict[str, object]:
    return {
        "id": "chatcmpl-tool-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_fixture_1",
                            "type": "function",
                            "function": {
                                "name": "fixture_echo",
                                "arguments": '{"text":"ping"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "QiM1Fixture/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def fixture_state(self) -> FixtureState:
        return self.server.fixture_state  # type: ignore[attr-defined]

    def send_json(self, status: int, body: dict[str, object]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except BrokenPipeError:
            pass

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            request = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_json(400, {"error": {"message": "invalid JSON"}})
            return
        max_tokens = request.get("max_tokens")
        if not isinstance(max_tokens, int):
            max_tokens = None

        if "/slow/" in self.path:
            self.fixture_state.record("slow", max_tokens)
            time.sleep(5)
            self.send_json(200, completion("too-late", 1))
            return

        if "/budget/" in self.path:
            self.fixture_state.record("budget", max_tokens)
            self.send_json(200, completion("first-ok", 5))
            return

        if "/exact/" in self.path:
            self.fixture_state.record("exact", max_tokens)
            self.send_json(200, completion("exact-ok", 5))
            return

        if "/tool/" in self.path:
            messages = request.get("messages", [])
            continuation = any(
                isinstance(message, dict)
                and message.get("role") == "tool"
                and message.get("content") == "fixture-result"
                for message in messages
            )
            self.fixture_state.record("tool", max_tokens, continuation)
            if continuation:
                self.send_json(200, completion("tool-finished", 5))
            elif self.fixture_state.counts["tool"] == 1:
                self.send_json(200, tool_call_completion())
            else:
                self.send_json(400, {"error": {"message": "missing tool continuation"}})
            return

        if "/retry-success/" in self.path:
            self.fixture_state.record("retry_success", max_tokens)
            if self.fixture_state.counts["retry_success"] == 1:
                self.send_json(503, {"error": {"message": "retry fixture 503"}})
            else:
                self.send_json(200, completion("retry-ok", 5))
            return

        if "/retry-exhausted/" in self.path:
            self.fixture_state.record("retry_exhausted", max_tokens)
            self.send_json(503, {"error": {"message": "exhausted fixture 503"}})
            return

        if "/overrun/" in self.path:
            self.fixture_state.record("overrun", max_tokens)
            self.send_json(200, completion("provider-violated-cap", 6))
            return

        if "/policy/" in self.path:
            self.fixture_state.record("policy", max_tokens)
            self.send_json(200, completion("policy-ok", 5))
            return

        self.send_json(404, {"error": {"message": "unknown fixture route"}})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()

    state = FixtureState(args.state_file)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.fixture_state = state  # type: ignore[attr-defined]
    port = server.server_address[1]
    if port <= 3000:
        raise RuntimeError(f"fixture selected disallowed port {port}")
    args.port_file.write_text(str(port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
