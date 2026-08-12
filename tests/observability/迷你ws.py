#!/usr/bin/env python3
"""够用就好的 WebSocket 客户端 —— 只用标准库。

不依赖 websockets 包：测试的依赖越少越好，装不上的机器上「跳过」
最后总会变成「一直没跑过」。文本帧、无分片、无扩展 —— LiveView 只用这些。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct


class 迷你WS:
    def __init__(self, host: str, port: int, path: str, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Origin: http://{host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("握手时连接就断了")
            self.buf += chunk
        head, _, rest = self.buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError(f"升级失败: {head.decode('utf-8', 'replace')[:200]}")
        self.buf = rest

    def send(self, text: str) -> None:
        payload = text.encode()
        # 客户端发的帧**必须**掩码，否则合规的服务端会直接断开
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        header = bytes([0x81])
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(header + mask + masked)

    def _fill(self, need: int) -> None:
        while len(self.buf) < need:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接断了")
            self.buf += chunk

    def recv(self) -> str:
        while True:
            self._fill(2)
            opcode = self.buf[0] & 0x0F
            length = self.buf[1] & 0x7F
            offset = 2
            if length == 126:
                self._fill(4)
                length = struct.unpack(">H", self.buf[2:4])[0]
                offset = 4
            elif length == 127:
                self._fill(10)
                length = struct.unpack(">Q", self.buf[2:10])[0]
                offset = 10
            self._fill(offset + length)
            payload = self.buf[offset:offset + length]
            self.buf = self.buf[offset + length:]
            if opcode == 0x8:
                raise RuntimeError("服务端关闭了连接")
            if opcode == 0x9:      # ping → 回 pong，否则会被踢
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")
            # 0xA pong 之类，跳过

    def recv_json(self) -> dict:
        return json.loads(self.recv())

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
