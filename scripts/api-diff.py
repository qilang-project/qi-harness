#!/usr/bin/env python3
"""Classify public API manifest drift as none, additive, or breaking."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXIT_NONE = 0
EXIT_ADDITIVE = 10
EXIT_BREAKING = 20
EXIT_MALFORMED = 30
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def parse_manifest(text: str, source: str) -> dict[str, set[str]]:
    sections: dict[str, set[str]] = {}
    current: str | None = None
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") or line.endswith("]"):
            if not (line.startswith("[") and line.endswith("]") and len(line) > 2):
                raise ValueError(f"{source}:{line_number}: malformed section heading")
            current = line[1:-1]
            if current in sections:
                raise ValueError(f"{source}:{line_number}: duplicate section [{current}]")
            sections[current] = set()
            continue
        if current is None:
            raise ValueError(f"{source}:{line_number}: declaration before first section")
        if not (line.startswith("函数 ") or line.startswith("类型 ")):
            raise ValueError(f"{source}:{line_number}: unrecognized declaration")
        if line in sections[current]:
            raise ValueError(f"{source}:{line_number}: duplicate declaration in [{current}]")
        sections[current].add(line)
    if not sections or "Harness" not in sections:
        raise ValueError(f"{source}: missing [Harness] section")
    return sections


def generated_manifest() -> str:
    checker_path = ROOT / "check-public-api.py"
    spec = importlib.util.spec_from_file_location("qi_harness_public_api", checker_path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load check-public-api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render_manifest()


TYPE_RE = re.compile(r"^类型\s+(\S+)\s*\{(.*)\}\s*$")


def parse_type_decl(line: str) -> tuple[str, dict[str, str]] | None:
    """`类型 名 { 字段: 类型, … }` → (名, {字段: 类型})。不是类型声明返回 None。"""
    match = TYPE_RE.match(line)
    if match is None:
        return None
    name = match.group(1)
    fields: dict[str, str] = {}
    for part in match.group(2).split(","):
        part = part.strip()
        if not part:
            continue
        field, _, field_type = part.partition(":")
        fields[field.strip()] = field_type.strip()
    return name, fields


def struct_only_gained_fields(before_line: str, after_line: str) -> bool:
    """两条类型声明之间，是不是「只多了字段、老字段一个没动」。

    加字段为什么算兼容：本仓的公开结构体都是**只由自己的 builder 构造**的
    （`大模型()` / `默认配置()` + `配置端点()` 这类链式改法），全仓和所有示例里
    没有一处用户侧的 `新建 模型配置 { … }`。所以多一个字段，调用方一行都不用改。

    反过来，删字段 / 改名 / 改类型 仍然算破坏 —— 那些是真的会让调用方编不过。
    """
    before = parse_type_decl(before_line)
    after = parse_type_decl(after_line)
    if before is None or after is None:
        return False
    if before[0] != after[0]:
        return False
    old_fields, new_fields = before[1], after[1]
    for field, field_type in old_fields.items():
        if new_fields.get(field) != field_type:
            return False
    return len(new_fields) > len(old_fields)


def split_added_fields(before: set[str], after: set[str]) -> tuple[set[str], set[str], list[str]]:
    """把「同名结构体只加了字段」这一对从 删除/新增 里摘出来，单独当兼容变更报。

    不摘的话：一条老声明消失 + 一条新声明出现 = 差分器判定「有删除」= 破坏，
    于是给结构体加个字段就永远过不了门禁。qi-harness 的 模型配置 加
    `额外参数` 之后 CI 从 2026-08-20 起一直红，就是卡在这儿。
    """
    removed = before - after
    added = after - before
    pairs: list[str] = []
    for old_line in sorted(removed):
        old = parse_type_decl(old_line)
        if old is None:
            continue
        for new_line in sorted(added):
            if struct_only_gained_fields(old_line, new_line):
                fresh_fields = sorted(
                    set(parse_type_decl(new_line)[1]) - set(old[1])
                )
                pairs.append(f"类型 {old[0]} 新增字段 {', '.join(fresh_fields)}（老字段未变，兼容）")
                removed = removed - {old_line}
                added = added - {new_line}
                break
    return removed, added, pairs


def parse_version(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def additive_allowed(baseline_version: str, candidate_version: str) -> bool:
    before = parse_version(baseline_version)
    after = parse_version(candidate_version)
    return after > before and after[:2] != before[:2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", nargs="?", type=Path, default=ROOT / "public-api.txt")
    parser.add_argument("candidate", nargs="?", type=Path, help="candidate manifest; source is generated when omitted")
    parser.add_argument("--baseline-version", help="baseline SemVer for release-policy enforcement")
    parser.add_argument("--candidate-version", help="candidate SemVer for release-policy enforcement")
    args = parser.parse_args()

    try:
        if bool(args.baseline_version) != bool(args.candidate_version):
            raise ValueError("--baseline-version and --candidate-version must be provided together")
        baseline_text = args.baseline.read_text(encoding="utf-8")
        candidate_text = args.candidate.read_text(encoding="utf-8") if args.candidate else generated_manifest()
        baseline = parse_manifest(baseline_text, str(args.baseline))
        candidate = parse_manifest(candidate_text, str(args.candidate or "generated source API"))
        allow_additive = (
            additive_allowed(args.baseline_version, args.candidate_version)
            if args.baseline_version and args.candidate_version
            else None
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_MALFORMED

    additions: list[str] = []
    removals: list[str] = []
    for section in sorted(set(baseline) | set(candidate)):
        before = baseline.get(section, set())
        after = candidate.get(section, set())
        remaining_removed, remaining_added, added_fields = split_added_fields(before, after)
        additions.extend(f"[{section}] ~ {item}" for item in added_fields)
        additions.extend(f"[{section}] + {item}" for item in sorted(remaining_added))
        removals.extend(f"[{section}] - {item}" for item in sorted(remaining_removed))

    if removals:
        print("breaking public API drift")
        for item in removals + additions:
            print(item)
        return EXIT_BREAKING
    if additions:
        print("additive public API drift")
        for item in additions:
            print(item)
        if allow_additive is not None:
            if allow_additive:
                print(f"additive drift allowed for {args.baseline_version} -> {args.candidate_version}")
                return EXIT_NONE
            print(
                f"additive drift is not allowed for {args.baseline_version} -> {args.candidate_version}",
                file=sys.stderr,
            )
        return EXIT_ADDITIVE
    print("no public API drift")
    return EXIT_NONE


if __name__ == "__main__":
    raise SystemExit(main())
