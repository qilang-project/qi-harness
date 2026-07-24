#!/usr/bin/env python3
"""Verify archive layout, checksums, metadata, and cross-format contents."""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_PARTS = {".git", ".playwright-mcp", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".ll", ".log", ".o", ".pyc", ".sqlite", ".sqlite3"}


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def validate_names(names: list[str], prefix: str) -> None:
    if not names:
        fail("archive is empty")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
            fail(f"unsafe or incorrect archive path: {name}")
        if any(part in FORBIDDEN_PARTS for part in path.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            fail(f"prohibited file in archive: {name}")


def tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    if len(sys.argv) != 4:
        fail("usage: verify_package.py DIST VERSION WORK")
    dist, version, work = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    prefix = f"qi-harness-v{version}"
    tar_path = dist / f"{prefix}.tar.gz"
    zip_path = dist / f"{prefix}.zip"
    checksums = dist / "SHA256SUMS"

    expected = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    for path in (tar_path, zip_path):
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected.get(path.name) != actual:
            fail(f"checksum mismatch for {path.name}")

    tar_root, zip_root = work / "tar", work / "zip"
    shutil.rmtree(tar_root, ignore_errors=True)
    shutil.rmtree(zip_root, ignore_errors=True)
    tar_root.mkdir(parents=True)
    zip_root.mkdir(parents=True)
    with tarfile.open(tar_path, "r:gz") as archive:
        members = archive.getmembers()
        if any(not member.isfile() for member in members):
            fail("tar contains a non-regular entry")
        validate_names([member.name for member in members], prefix)
        for member in members:
            source = archive.extractfile(member)
            if source is None:
                fail(f"cannot read tar member: {member.name}")
            destination = tar_root / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read())
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if any(info.is_dir() for info in infos):
            fail("zip contains an unexpected directory entry")
        validate_names([info.filename for info in infos], prefix)
        archive.extractall(zip_root)

    tar_tree = tree_digest(tar_root / prefix)
    zip_tree = tree_digest(zip_root / prefix)
    if tar_tree != zip_tree:
        fail("tar and zip contents differ")
    required = {"Harness.qi", "qi.toml", "LICENSE", "CHANGELOG.md", "MIGRATING.md", "public-api.txt"}
    missing = sorted(required - tar_tree.keys())
    if missing:
        fail("archive is missing required files: " + ", ".join(missing))
    version_line = f'版本 = "{version}"'
    manifest = (tar_root / prefix / "qi.toml").read_text(encoding="utf-8")
    if version_line not in manifest:
        fail(f"packaged qi.toml does not contain {version_line}")
    if not re.search(r'^最低Qi版本 = "2026.07.24-1"$', manifest, re.MULTILINE):
        fail("packaged qi.toml does not require the governed Qi release")
    if "qi@05568a72 + qi-runtime@ceada461 + qi-gui@82580227" not in manifest:
        fail("packaged qi.toml does not declare the governed Qi source baseline")
    print(f"verified {len(tar_tree)} packaged files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
