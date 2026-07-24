#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages", [])
        user_messages = [message for message in messages if message.get("role") == "user"]
        latest_user = user_messages[-1].get("content") if user_messages else None
        if latest_user == "slow":
            time.sleep(0.5)
        if latest_user == "model error":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        has_tool_result = any(message.get("role") == "tool" for message in messages)
        wants_tool = latest_user in {"use tool", "use blocking tool"}
        finish_reason = "stop"
        if wants_tool and not has_tool_result:
            message: dict[str, object] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_service_1",
                    "type": "function",
                    "function": {"name": "fixture_tool", "arguments": "{}"},
                }],
            }
            finish_reason = "tool_calls"
        elif wants_tool:
            message = {"role": "assistant", "content": "tool-finished"}
        elif request.get("response_format"):
            content = json.dumps({"turns": len(user_messages)}, separators=(",", ":"))
            message = {"role": "assistant", "content": content}
        else:
            content = f"turn-{len(user_messages)}"
            message = {"role": "assistant", "content": content}
        body = {
            "id": "chatcmpl-service",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
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
