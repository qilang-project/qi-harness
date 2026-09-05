#!/usr/bin/env python3
"""Minimal stdio MCP server used by MCP装备_测.qi: tools + resources + prompts with arguments.

Line-delimited JSON-RPC 2.0. No external dependencies.
"""
from __future__ import annotations

import json
import sys

RESOURCES = [
    {"uri": "memo://hello", "name": "hello", "description": "问候资源", "mimeType": "text/plain"},
    {"uri": "memo://data", "name": "data", "description": "JSON 数据", "mimeType": "application/json"},
]
RESOURCE_TEXT = {
    "memo://hello": "你好，资源",
    "memo://data": "{\"k\": 1}",
}
PROMPTS = [
    {
        "name": "greet",
        "description": "打招呼",
        "arguments": [
            {"name": "name", "description": "对方名字", "required": True},
            {"name": "style", "description": "语气", "required": False},
        ],
    },
    {"name": "总结 文档", "description": "中文名的提示", "arguments": []},
]


def result(req_id, payload):
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(req):
    method = req.get("method", "")
    params = req.get("params") or {}
    req_id = req.get("id")
    if req_id is None:
        return None  # notification
    if method == "initialize":
        return result(req_id, {
            "protocolVersion": params.get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
        })
    if method == "ping":
        return result(req_id, {})
    if method == "tools/list":
        return result(req_id, {"tools": [{
            "name": "echo",
            "description": "回显",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }]})
    if method == "tools/call":
        if params.get("name") != "echo":
            return error(req_id, -32602, "unknown tool")
        text = (params.get("arguments") or {}).get("text", "")
        return result(req_id, {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False})
    if method == "resources/list":
        return result(req_id, {"resources": RESOURCES})
    if method == "resources/read":
        uri = params.get("uri")
        if uri not in RESOURCE_TEXT:
            return error(req_id, -32002, f"resource not found: {uri}")
        return result(req_id, {"contents": [{"uri": uri, "mimeType": "text/plain", "text": RESOURCE_TEXT[uri]}]})
    if method == "prompts/list":
        return result(req_id, {"prompts": PROMPTS})
    if method == "prompts/get":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "greet":
            if "name" not in args:
                return error(req_id, -32602, "missing argument: name")
            unexpected = sorted(set(args) - {"name", "style"})
            if unexpected:
                return error(req_id, -32602, f"unexpected arguments: {unexpected}")
            style = args.get("style", "普通")
            return result(req_id, {
                "description": "问候模板",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": f"请用{style}风格向{args['name']}问好"}},
                    {"role": "assistant", "content": [
                        {"type": "text", "text": "好的，"},
                        {"type": "text", "text": f"{args['name']}你好！"},
                    ]},
                ],
            })
        if name == "总结 文档":
            return result(req_id, {"messages": [{"role": "user", "content": {"type": "text", "text": "请总结这份文档"}}]})
        return error(req_id, -32602, f"unknown prompt: {name}")
    return error(req_id, -32601, f"method not found: {method}")


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
