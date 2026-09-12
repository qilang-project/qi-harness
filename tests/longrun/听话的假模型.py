#!/usr/bin/env python3
"""每段回一条合法的段末 JSON，让长时程真的能推进里程碑并正常收场。

tests/service_persistence/fake_openai.py 只会回 turn-N，长时程的解析路径
（完成哪些里程碑 / 笔记 / 结局）永远走不到，测出来的只有「超限」那条。
这个假模型每被调一次就完成一个里程碑，第三次宣布完成。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

state = {"calls": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length))
        i = state["calls"]
        state["calls"] += 1
        done = "完成" if i >= 2 else "继续"
        payload = {
            "完成": [i] if i < 3 else [],
            "笔记": f"第 {i + 1} 段做完了第 {i} 个里程碑，下一步接着搬。",
            "结局": done,
        }
        content = "干完了。\n" + json.dumps(payload, ensure_ascii=False)
        body = {
            "id": "chatcmpl-longrun",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
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
