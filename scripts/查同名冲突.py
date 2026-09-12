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

PKG_DECL = re.compile(r"^包\s+(\S+?)\s*[;；]")
FN_DECL = re.compile(r"^(?:公开\s+)?函数\s+([^\s(]+)\s*\(([^)]*)\)")


def param_count(param_str: str) -> int:
    param_str = param_str.strip()
    if not param_str:
        return 0
    # 形参里不会出现逗号以外的分隔；泛型如 通道<整数> 不含逗号
    return param_str.count(",") + 1


def scan(root: pathlib.Path) -> dict:
    table = collections.defaultdict(list)
    for src_file in sorted(root.glob("*.qi")):
        pkg_name = None
        for line_no, line in enumerate(src_file.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0].strip()
            m = PKG_DECL.match(code)
            if m:
                pkg_name = m.group(1)
                continue
            m = FN_DECL.match(code)
            if m and pkg_name:
                key = (pkg_name, m.group(1), param_count(m.group(2)))
                table[key].append(f"{src_file.name}:{line_no}")
    return table


def main() -> int:
    table = scan(ROOT)
    conflicts = {
        key: positions
        for key, positions in table.items()
        if len({pos.split(":")[0] for pos in positions}) > 1
    }
    if not conflicts:
        print(f"没有同名同元数冲突（扫了 {len(table)} 个函数定义）")
        return 0
    for (pkg_name, fn_name, arity), positions in sorted(conflicts.items()):
        print(
            f"error: 「{fn_name}」在包「{pkg_name}」里有多个形参个数都是 {arity} 的定义，"
            f"无法按元数区分：",
            file=sys.stderr,
        )
        for pos in positions:
            print(f"    {pos}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
