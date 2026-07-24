#!/usr/bin/env python3
"""Syntax-check every Qi example whose external packages are available."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ALLOWLIST = ROOT / "example-check-allowlist.txt"


def load_allowlist() -> dict[tuple[str, str], str]:
    entries: dict[tuple[str, str], str] = {}
    for number, raw_line in enumerate(ALLOWLIST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3 or not all(parts):
            raise ValueError(f"{ALLOWLIST.name}:{number}: expected FILE|PACKAGE|REASON")
        key = (parts[0], parts[1])
        if key in entries:
            raise ValueError(f"{ALLOWLIST.name}:{number}: duplicate {parts[0]} / {parts[1]}")
        entries[key] = parts[2]
    return entries


def package_roots() -> list[Path]:
    roots = [Path.home() / ".qi" / "packages"]
    roots.extend(Path(value) for value in os.environ.get("QI_PACKAGES_PATH", "").split(os.pathsep) if value)
    return roots


def package_available(package: str, roots: list[Path]) -> bool:
    return any((root / package).exists() for root in roots)


def external_packages(path: Path) -> set[str]:
    source = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
    packages = set(re.findall(r"\b导入\s+([A-Za-z][A-Za-z0-9_]*)", source))
    return packages - {"Harness"}


def main() -> int:
    try:
        allowlist = load_allowlist()
    except (OSError, ValueError) as error:
        print(f"error: cannot load example allowlist: {error}", file=sys.stderr)
        return 1

    roots = package_roots()
    checked = 0
    skipped = 0
    used_entries: set[tuple[str, str]] = set()

    for path in sorted((ROOT / "examples").rglob("*.qi")):
        relative = path.relative_to(ROOT).as_posix()
        missing = sorted(package for package in external_packages(path) if not package_available(package, roots))
        if missing:
            reasons = []
            for package in missing:
                key = (relative, package)
                reason = allowlist.get(key)
                if reason is None:
                    print(
                        f"error: {relative} requires unavailable package {package}; "
                        f"add an intentional entry to {ALLOWLIST.name}",
                        file=sys.stderr,
                    )
                    return 1
                used_entries.add(key)
                reasons.append(f"{package}: {reason}")
            print(f"SKIP {relative} ({'; '.join(reasons)})")
            skipped += 1
            continue

        print(f"CHECK {relative}")
        result = subprocess.run(["qi", "check", str(path)], cwd=ROOT)
        if result.returncode != 0:
            return result.returncode
        checked += 1

    stale = sorted(set(allowlist) - used_entries)
    for relative, package in stale:
        path = ROOT / relative
        if not path.exists() or package not in external_packages(path):
            print(f"error: stale example allowlist entry: {relative}|{package}", file=sys.stderr)
            return 1

    print(f"examples syntax: checked {checked}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
