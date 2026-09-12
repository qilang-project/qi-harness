#!/usr/bin/env python3
"""干活那一问什么都不干，收尾那一问永远宣称「里程碑 0 完成」。

真模型实测干过一模一样的事：一段被工具步数上限腰斩，文件一个字没写，
收尾照样回「完成: [0, 1]，结局: 完成」。用这个假模型钉住验收那道闸。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if request.get("response_format"):
            content = json.dumps(
                {"完成": [0], "笔记": "我说我干完了。", "结局": "完成"},
                ensure_ascii=False)
        else:
            content = "干完了（其实什么都没干）。"
        body = {
            "id": "chatcmpl-liar", "object": "chat.completion", "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
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
