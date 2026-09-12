#!/usr/bin/env python3
"""长时程 + 知识图的闭环假模型。

第一段：调一次 记事实 工具，把一条事实写进图。
第二段起：不再写，只干活。
问进度那一问（带 response_format）永远回合法的段末 JSON。

--dump-prompts 把每次收到的**第一条 user 消息**追加进一个文件，测试据此断言
「上一段写进图的事实，这一段真的出现在提示里」—— 不落盘就只能靠函数返回值
自证，那证明不了注入这一步。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

state = {"reports": 0, "recorded": False}
dump_path: Path | None = None


def first_user_text(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        messages = request.get("messages") or []
        is_report = bool(request.get("response_format"))

        if dump_path is not None and not is_report:
            with dump_path.open("a", encoding="utf-8") as fh:
                fh.write(first_user_text(messages) + "\n===段边界===\n")

        message: dict = {"role": "assistant", "content": None}
        if is_report:
            i = state["reports"]
            state["reports"] += 1
            message["content"] = json.dumps(
                {
                    # 每段推一个里程碑。报「完成」时里程碑必须真的全达成 ——
                    # 长时程 现在会拦下「报完成但里程碑没做完」那种自说自话。
                    "完成": [i],
                    "笔记": f"第 {i + 1} 段结束。",
                    "结局": "完成" if i >= 1 else "继续",
                },
                ensure_ascii=False,
            )
        elif not state["recorded"] and not any(m.get("role") == "tool" for m in messages):
            state["recorded"] = True
            message["tool_calls"] = [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "记事实",
                        "arguments": json.dumps(
                            {"主体": "登录接口", "关系": "需要请求头", "客体": "X-Token"},
                            ensure_ascii=False,
                        ),
                    },
                }
            ]
        else:
            message["content"] = "干完了。"

        body = {
            "id": "chatcmpl-graphloop",
            "object": "chat.completion",
            "created": 1,
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
    global dump_path
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--dump-prompts", type=Path, default=None)
    args = parser.parse_args()
    dump_path = args.dump_prompts
    if dump_path is not None and dump_path.exists():
        dump_path.unlink()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    if port <= 3000:
        raise RuntimeError(f"fixture selected disallowed port {port}")
    args.port_file.write_text(str(port), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
