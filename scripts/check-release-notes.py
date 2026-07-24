#!/usr/bin/env python3
"""Validate changelog and migration notes for a release or unreleased work."""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def release_heading(changelog: str, version: str) -> str | None:
    match = re.search(rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})\s*$", changelog, re.MULTILINE)
    return match.group(1) if match else None


def has_migration_heading(migrating: str, version: str) -> bool:
    return bool(re.search(rf"^## Migrating to {re.escape(version)}\s*$", migrating, re.MULTILINE | re.IGNORECASE))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="release SemVer, with optional v prefix")
    parser.add_argument("--unreleased", action="store_true", help="validate the current unreleased notes")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--migrating", type=Path, default=ROOT / "MIGRATING.md")
    args = parser.parse_args()

    if args.unreleased == bool(args.version):
        parser.error("provide exactly one of VERSION or --unreleased")

    try:
        changelog = args.changelog.read_text(encoding="utf-8")
        migrating = args.migrating.read_text(encoding="utf-8")
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    errors: list[str] = []
    if args.unreleased:
        if not re.search(r"^## \[Unreleased\]\s*$", changelog, re.MULTILINE):
            errors.append("CHANGELOG.md is missing '## [Unreleased]'")
        if not re.search(r"^# Migrating qi-harness\s*$", migrating, re.MULTILINE):
            errors.append("MIGRATING.md is missing its document heading")
        if not re.search(r"^## .+", migrating, re.MULTILINE):
            errors.append("MIGRATING.md contains no migration section")
    else:
        version = args.version[1:] if args.version and args.version.startswith("v") else args.version
        if version is None or not SEMVER_RE.fullmatch(version):
            errors.append(f"invalid release version: {args.version}")
        else:
            date_text = release_heading(changelog, version)
            if date_text is None:
                errors.append(f"CHANGELOG.md is missing '## [{version}] - YYYY-MM-DD'")
            else:
                try:
                    dt.date.fromisoformat(date_text)
                except ValueError:
                    errors.append(f"CHANGELOG.md has an invalid release date: {date_text}")
            if not has_migration_heading(migrating, version):
                errors.append(f"MIGRATING.md is missing '## Migrating to {version}'")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("release notes are consistent" if args.version else "unreleased notes are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
