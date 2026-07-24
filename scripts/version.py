#!/usr/bin/env python3
"""Read, validate, or update the package version in qi.toml."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
VERSION_LINE_RE = re.compile(r'^(\s*版本\s*=\s*")([^"]+)("\s*(?:#.*)?)$', re.MULTILINE)


def normalize_version(value: str) -> str:
    version = value[1:] if value.startswith("v") else value
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"not a valid semantic version: {value}")
    return version


def read_version(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    matches = list(VERSION_LINE_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one package version in {path}")
    return normalize_version(matches[0].group(2))


def set_version(path: Path, version: str) -> None:
    source = path.read_text(encoding="utf-8")
    matches = list(VERSION_LINE_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one package version in {path}")
    match = matches[0]
    updated = source[: match.start()] + match.group(1) + version + match.group(3) + source[match.end() :]
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, default=ROOT / "qi.toml", help="qi.toml to inspect")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("current", help="print the current package version")
    check = subparsers.add_parser("check", help="require qi.toml to match VERSION")
    check.add_argument("version")
    update = subparsers.add_parser("set", help="replace the package version with VERSION")
    update.add_argument("version")
    args = parser.parse_args()

    try:
        current = read_version(args.file)
        if args.command == "current":
            print(current)
            return 0

        expected = normalize_version(args.version)
        if args.command == "check":
            if current != expected:
                print(f"error: qi.toml version is {current}, expected {expected}", file=sys.stderr)
                return 1
            print(f"qi.toml version matches {expected}")
            return 0

        set_version(args.file, expected)
        print(f"updated {args.file} from {current} to {expected}")
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
