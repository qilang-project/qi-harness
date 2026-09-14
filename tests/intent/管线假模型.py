#!/usr/bin/env python3
"""问答管线的假模型：按提示词内容分辨这一步在问什么。

管线三步各要一种回答：
  1. 展示型意图分类  提示里有 "## 待分类"        → {"类别","理由"}
  2. 问题种类分类    提示里有 "该由哪条链路处理"  → {"类别","子类别","理由"}
  3. 文本转查询      提示里有 "### 输出格式"      → {"关系","推理","查询"}

第 3 步真的按模式里写的语法生成一条 qi-graph 图查询语句 —— 测试拿它去查真图，
所以这里不能随便回一个字符串，得回一条真能跑通的。
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

dump_path: Path | None = None


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def answer_for(prompt: str) -> str:
    if "该由哪条链路处理" in prompt:
        # 两级分类
        if "导出" in prompt.split("## 待分类")[-1]:
            return json.dumps({"类别": "系统相关", "子类别": "文档相关",
                               "理由": "问的是导出能力，属于功能说明"}, ensure_ascii=False)
        return json.dumps({"类别": "数据相关", "子类别": "",
                           "理由": "直接查图就能答"}, ensure_ascii=False)
    if "## 待分类" in prompt:
        return json.dumps({"类别": "表格", "理由": "要的是一批包，适合列成表"},
                          ensure_ascii=False)
    if "### 输出格式" in prompt:
        # 照模式里教的 图语句 语法生成
        return json.dumps({
            "关系": ["依赖"],
            "推理": "问的是「谁依赖 KV」，依赖边从依赖方指向被依赖方，所以 KV 在箭头右边，"
                    "左边留一个带 包 标签的变量；意图是表格，用 返回 只留这个变量。",
            "查询": "匹配 (?p:包)-[:依赖]->(KV) 返回 ?p",
        }, ensure_ascii=False)
    return json.dumps({"类别": "反馈与抱怨", "理由": "认不出这是什么提示"},
                      ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        prompt = last_user_text(request.get("messages") or [])
        if dump_path is not None:
            with dump_path.open("a", encoding="utf-8") as fh:
                fh.write(prompt + "\n===提示边界===\n")
        body = {
            "id": "chatcmpl-pipeline", "object": "chat.completion", "created": 1,
            "model": "fixture-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": answer_for(prompt)},
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
