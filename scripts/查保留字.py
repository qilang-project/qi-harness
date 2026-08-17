#!/usr/bin/env python3
"""扫 .qi 源里「用保留字当标识符」的声明位置。

qi 的真词表是 grammar.lalrpop 里的字面量，不是 keywords.rs（那只是诊断表），
所以直接从 grammar 里抽 —— 词表改了这个脚本自动跟上。

只看声明位置（变量/常量/函数名/参数名），不看用法：
用法里出现「列表」多半是 列表库:: 这类合法前缀。
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRAMMAR = ROOT / "qi" / "src" / "parser" / "grammar.lalrpop"

def reserved():
    text = GRAMMAR.read_text(encoding="utf-8")
    return {w for w in re.findall(r'"([一-鿿]+)"', text)}

DECL = [
    re.compile(r'(?:变量|常量)\s+([一-鿿\w]+)\s*[:：=]'),
    re.compile(r'函数\s+([一-鿿\w]+)\s*\('),
]
# 形参表可以再套一层括号：函数类型参数 `比较: 函数(指针, 指针): 整数`
# （`外部` 块声明 C 回调槽就长这样）。只吃到第一个 `)` 会把内层的**类型名**
# 当成形参名，`指针`/`整数` 立刻被误报成保留字。所以允许一层嵌套括号，
# 再把内层的 函数(...) 整段抹掉——那里面是类型，不是名字。
PARAM = re.compile(r'函数\s+[一-鿿\w]+\s*\(((?:[^()]|\([^()]*\))*)\)')
FNTYPE = re.compile(r'函数\s*\([^()]*\)')

def scan(path, words):
    bad = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("//")[0]
        for pat in DECL:
            for name in pat.findall(code):
                if name in words:
                    bad.append((lineno, name, "声明"))
        for group in PARAM.findall(code):
            for part in FNTYPE.sub("", group).split(","):
                name = part.split(":")[0].strip()
                if name in words:
                    bad.append((lineno, name, "参数"))
    return bad

def main(argv):
    words = reserved()
    fail = 0
    for arg in argv or ["."]:
        p = pathlib.Path(arg)
        files = sorted(p.rglob("*.qi")) if p.is_dir() else [p]
        for f in files:
            for lineno, name, kind in scan(f, words):
                print(f"{f}:{lineno}: {kind} `{name}` 是保留字")
                fail = 1
    if not fail:
        print(f"没有保留字冲突（词表 {len(words)} 个）")
    return fail

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
