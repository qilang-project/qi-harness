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
        additions.extend(f"[{section}] + {item}" for item in sorted(after - before))
        removals.extend(f"[{section}] - {item}" for item in sorted(before - after))

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
