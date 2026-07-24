#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages", [])
        if self.path.endswith("/turns/chat/completions"):
            self.handle_turns(messages)
            return
        has_result = any(message.get("role") == "tool" for message in messages)
        message: dict[str, object]
        finish_reason: str
        if has_result:
            message = {"role": "assistant", "content": "tool-finished"}
            finish_reason = "stop"
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_session_1",
                    "type": "function",
                    "function": {"name": "fixture_tool", "arguments": "{}"},
                }],
            }
            finish_reason = "tool_calls"
        body = {
            "id": "chatcmpl-session",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def handle_turns(self, messages: list[dict[str, object]]) -> None:
        current = str(messages[-1].get("content", "")) if messages else ""
        if current.startswith("timeout-failed"):
            time.sleep(2)
            self.send_json_message("late-timeout")
            return
        if current.startswith("server-failed"):
            encoded = b'{"error":{"message":"fixture 500"}}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if current.startswith("recover-"):
            prior_users = [
                str(message.get("content", ""))
                for message in messages[:-1]
                if message.get("role") == "user"
            ]
            scenario = current.removeprefix("recover-")
            clean = (
                f"seed-{scenario}" in prior_users
                and not any("failed" in prompt or "blocked" in prompt for prompt in prior_users)
            )
            self.send_json_message("recovered-clean" if clean else "recovered-contaminated")
            return
        self.send_json_message("seed-ok", total_tokens=5)

    def send_json_message(self, content: str, total_tokens: int = 2) -> None:
        body = {
            "id": "chatcmpl-turns",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": max(total_tokens - 1, 0),
                "total_tokens": total_tokens,
            },
        }
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


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
