#!/usr/bin/env python3
"""假 OTLP/HTTP collector —— 收 /v1/traces 的 POST，原样存盘供断言。

存在的理由：parentSpanId 缺失这个 bug，**只有真 collector 在对面才看得见**。
qi 那边自测「我发出去了」永远是绿的，推到 Jaeger 才发现整棵树散成了孤儿 span。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT: Path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "path": self.path,
                "content_type": self.headers.get("Content-Type", ""),
                "body": body.decode("utf-8", "replace"),
            }) + "\n")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    OUT = Path(args.out)
    OUT.write_text("", encoding="utf-8")

    # 端口 0 = 让内核挑一个空闲的高位端口，然后写给调用方。
    # 固定端口在 CI 上会跟别的东西撞，而且撞了报的是「连接被拒」，很难指向真因。
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    Path(args.port_file).write_text(str(port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
