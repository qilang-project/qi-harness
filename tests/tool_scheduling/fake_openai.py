#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def completion(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-scheduling",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def tool_calls(names: list[str]) -> dict[str, object]:
    calls = []
    for index, name in enumerate(names):
        calls.append(
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps({"index": index})},
            }
        )
    return {
        "id": "chatcmpl-scheduling-tools",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": calls},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: int, body: dict[str, object]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages", [])
        results = [message.get("content") for message in messages if message.get("role") == "tool"]

        if "/parallel/" in self.path:
            expected = ["result-A", "result-B"]
            self.send_json(200, completion("parallel-aligned") if results == expected else tool_calls(["slow_a", "slow_b"]))
            return
        if "/serial/" in self.path:
            expected = ["serial-A", "serial-B"]
            self.send_json(200, completion("serial-aligned") if results == expected else tool_calls(["serial_a", "serial_b"]))
            return
        if "/controlled/" in self.path:
            expected = ["controlled-A", "controlled-B", "controlled-serial", "controlled-C", "controlled-D"]
            projected = []
            for result in results:
                try:
                    value = json.loads(result)
                except (TypeError, json.JSONDecodeError):
                    value = None
                projected.append(value.get("content") if isinstance(value, dict) and value.get("status") == "success" else result)
            self.send_json(200, completion("controlled-aligned") if projected == expected else tool_calls(
                ["controlled_a", "controlled_b", "controlled_serial", "controlled_c", "controlled_d"]
            ))
            return
        if "/controlled-status/" in self.path:
            expected = [
                "legacy-visible:{\"index\": 0}",
                '{"version":1,"status":"cancelled","content_type":"none","content":"","error_code":"cancelled","error_message":"operator","retryable":false,"terminate_run":false,"metadata":{}}',
            ]
            if len(results) == 2 and results[0] == expected[0] and '"status":"cancelled"' in results[1]:
                self.send_json(200, completion("controlled-status-aligned"))
            else:
                self.send_json(200, tool_calls(["legacy_controlled", "cancelled_controlled"]))
            return
        if "/controlled-timeout/" in self.path:
            if len(results) == 1 and '"status":"timeout"' in results[0] and "cooperative deadline exceeded" in results[0]:
                self.send_json(200, completion("controlled-timeout-aligned"))
            else:
                self.send_json(200, tool_calls(["timeout_controlled"]))
            return
        if "/controlled-ambiguous/" in self.path:
            if len(results) == 1 and '"status":"needs_reconciliation"' in results[0] and '"retryable":false' in results[0]:
                self.send_json(200, completion("controlled-ambiguous-aligned"))
            else:
                self.send_json(200, tool_calls(["ambiguous_controlled"]))
            return
        if "/controlled-journal-success/" in self.path:
            if len(results) == 1 and '"status":"success"' in results[0] and "journal-counted" in results[0]:
                self.send_json(200, completion("controlled-journal-success-aligned"))
            else:
                self.send_json(200, tool_calls(["journal_controlled"]))
            return
        if "/controlled-journal-rejected/" in self.path:
            if len(results) == 1 and '"error_code":"dispatch_not_started"' in results[0]:
                self.send_json(200, completion("controlled-journal-rejected-aligned"))
            else:
                self.send_json(200, tool_calls(["journal_controlled"]))
            return
        self.send_json(404, {"error": {"message": "unknown route"}})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    if port <= 3000:
        raise RuntimeError(f"fixture selected disallowed port {port}")
    args.port_file.write_text(str(port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
