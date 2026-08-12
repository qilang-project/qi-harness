#!/usr/bin/env python3
"""扫同包内「同名 + 同形参个数」的函数定义。

qi 按 **包 + 函数名 + 形参个数** 解析调用，所以同一个包里两个同名同元数的函数
就是歧义，编译器报：

    函数「X」在包「Harness」里有两个形参个数都是 0 的定义，无法按元数区分

**这个坑本地几乎撞不到**：只有当某个编译单元同时拉进那两个模块时才暴露。
平时 qi check 单个文件、跑单个测试都是绿的，一路绿到 CI 才红 —— 而且红在
「编译 ABI 探针」这种看着与本次改动无关的地方。

私有函数（没有 公开）同样冲突：判定是按包，不是按可见性。
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

包声明 = re.compile(r"^包\s+(\S+?)\s*[;；]")
函数声明 = re.compile(r"^(?:公开\s+)?函数\s+([^\s(]+)\s*\(([^)]*)\)")


def 形参个数(参数串: str) -> int:
    参数串 = 参数串.strip()
    if not 参数串:
        return 0
    # 形参里不会出现逗号以外的分隔；泛型如 通道<整数> 不含逗号
    return 参数串.count(",") + 1


def 扫(根: pathlib.Path) -> dict:
    表 = collections.defaultdict(list)
    for 文件 in sorted(根.glob("*.qi")):
        包名 = None
        for 行号, 行 in enumerate(文件.read_text(encoding="utf-8").splitlines(), 1):
            码 = 行.split("//")[0].strip()
            m = 包声明.match(码)
            if m:
                包名 = m.group(1)
                continue
            m = 函数声明.match(码)
            if m and 包名:
                键 = (包名, m.group(1), 形参个数(m.group(2)))
                表[键].append(f"{文件.name}:{行号}")
    return 表


def main() -> int:
    表 = 扫(ROOT)
    冲突 = {
        键: 位置
        for 键, 位置 in 表.items()
        if len({位.split(":")[0] for 位 in 位置}) > 1
    }
    if not 冲突:
        print(f"没有同名同元数冲突（扫了 {len(表)} 个函数定义）")
        return 0
    for (包名, 名字, 元数), 位置 in sorted(冲突.items()):
        print(
            f"error: 「{名字}」在包「{包名}」里有多个形参个数都是 {元数} 的定义，"
            f"无法按元数区分：",
            file=sys.stderr,
        )
        for 位 in 位置:
            print(f"    {位}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
