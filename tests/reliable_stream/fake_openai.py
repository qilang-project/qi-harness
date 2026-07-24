#!/usr/bin/env python3
"""Deterministic OpenAI-compatible SSE fixture for Harness reliable streaming."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.max_tokens: dict[str, list[int | None]] = {}
        self.write()

    def record(self, prompt: str, max_tokens: int | None) -> int:
        with self.lock:
            self.counts[prompt] = self.counts.get(prompt, 0) + 1
            self.max_tokens.setdefault(prompt, []).append(max_tokens)
            self.write()
            return self.counts[prompt]

    def write(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"counts": self.counts, "max_tokens": self.max_tokens}, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def json_response(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()

    def event(self, value: object) -> None:
        self.wfile.write(b"data: " + json.dumps(value, separators=(",", ":")).encode() + b"\n\n")
        self.wfile.flush()

    def successful_stream(self, text: str, pause_after_text: float = 0, include_usage: bool = True) -> None:
        self.start_sse()
        self.event({"id": "fixture", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        self.event({"id": "fixture", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})
        if pause_after_text:
            time.sleep(pause_after_text)
        self.event({"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if include_usage:
            self.event({"id": "fixture", "choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def stall_before_first_output(self) -> None:
        self.start_sse()
        time.sleep(0.25)
        self.event({"choices": [{"index": 0, "delta": {"content": "late"}, "finish_reason": None}]})

    def stall_midstream(self) -> None:
        self.start_sse()
        self.event({"choices": [{"index": 0, "delta": {"content": "before-stall"}, "finish_reason": None}]})
        time.sleep(0.25)
        self.event({"choices": [{"index": 0, "delta": {"content": "late"}, "finish_reason": None}]})

    def exceed_total_deadline(self) -> None:
        self.start_sse()
        self.event({"choices": [{"index": 0, "delta": {"content": "total"}, "finish_reason": None}]})
        for suffix in ("-a", "-b", "-c"):
            time.sleep(0.07)
            self.event({"choices": [{"index": 0, "delta": {"content": suffix}, "finish_reason": None}]})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages", [])
        prompt = next(
            (message.get("content", "") for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        max_tokens = request.get("max_tokens")
        if not isinstance(max_tokens, int):
            max_tokens = None
        attempt = self.state.record(prompt, max_tokens)
        try:
            if prompt == "retry" and attempt == 1:
                self.json_response(500, {"error": {"message": "retry fixture 500"}})
                return
            if prompt == "partial":
                self.start_sse()
                self.event({"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]})
                return
            if prompt == "cancel":
                self.successful_stream("cancel-me", pause_after_text=1)
                return
            if prompt == "unknown-usage":
                self.successful_stream("ok-unknown-usage", include_usage=False)
                return
            if prompt == "stall-before-first":
                self.stall_before_first_output()
                return
            if prompt == "stall-midstream":
                self.stall_midstream()
                return
            if prompt == "total-deadline":
                self.exceed_total_deadline()
                return
            self.successful_stream("ok-" + prompt)
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = State(args.state_file)  # type: ignore[attr-defined]
    port = server.server_address[1]
    if port <= 3000:
        raise RuntimeError(f"fixture selected disallowed port {port}")
    args.port_file.write_text(str(port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
