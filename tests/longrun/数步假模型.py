#!/usr/bin/env python3
"""每次都回一个 写文件 工具调用，文件名带序号。

用来数「一段到底放行了多少步工具」：跑完之后沙箱里有几个 步N.txt，
就是实际的步数上限。设分段(任务, 3, …) 之后应该正好 3 个 —— 曾经不是，
段配置 里的默认 最大工具步数 10 把它盖掉了。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

state = {"step": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        if request.get("response_format"):
            content = json.dumps({"完成": [], "笔记": "数步。", "结局": "完成"},
                                 ensure_ascii=False)
            message = {"role": "assistant", "content": content}
        else:
            state["step"] += 1
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{state['step']}",
                    "type": "function",
                    "function": {
                        "name": "写文件",
                        "arguments": json.dumps(
                            {"路径": f"步{state['step']}.txt", "内容": "x"},
                            ensure_ascii=False),
                    },
                }],
            }
        body = {
            "id": "chatcmpl-steps", "object": "chat.completion", "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
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
