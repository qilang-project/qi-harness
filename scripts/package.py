#!/usr/bin/env python3
"""Build deterministic qi-harness source archives."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PARTS = {".git", ".playwright-mcp", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".ll", ".log", ".o", ".pyc", ".sqlite", ".sqlite3"}
REQUIRED_FILES = {"Harness.qi", "qi.toml", "LICENSE", "CHANGELOG.md", "MIGRATING.md", "public-api.txt"}


def source_paths(output_dir: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    try:
        excluded_output = output_dir.resolve().relative_to(ROOT.resolve())
    except ValueError:
        excluded_output = None
    paths: list[Path] = []
    for raw_path in result.stdout.decode("utf-8").split("\0"):
        if not raw_path:
            continue
        relative = PurePosixPath(raw_path)
        if excluded_output is not None:
            output_parts = PurePosixPath(excluded_output.as_posix()).parts
            if relative.parts[: len(output_parts)] == output_parts:
                continue
        if any(part in FORBIDDEN_PARTS for part in relative.parts) or relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            continue
        path = ROOT / relative
        if path.is_file() and not path.is_symlink():
            paths.append(Path(relative))
    missing = sorted(REQUIRED_FILES - {path.as_posix() for path in paths})
    if missing:
        raise ValueError("required package files are missing: " + ", ".join(missing))
    return sorted(paths, key=lambda path: path.as_posix().encode("utf-8"))


def archive_mode(path: Path) -> int:
    executable = path.suffix in {".py", ".sh"} or os.access(ROOT / path, os.X_OK)
    return 0o755 if executable else 0o644


def tar_bytes(paths: list[Path], prefix: str, epoch: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in paths:
            data = (ROOT / path).read_bytes()
            info = tarfile.TarInfo(f"{prefix}/{path.as_posix()}")
            info.size = len(data)
            info.mode = archive_mode(path)
            info.mtime = epoch
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def write_tar_gz(path: Path, payload: bytes, epoch: int) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch, compresslevel=9) as compressed:
            compressed.write(payload)


def write_zip(path: Path, paths: list[Path], prefix: str, epoch: int) -> None:
    # ZIP cannot represent timestamps before 1980.
    timestamp = max(epoch, 315532800)
    date_time = time.gmtime(timestamp)[:6]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in paths:
            info = zipfile.ZipInfo(f"{prefix}/{relative.as_posix()}", date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | archive_mode(relative)) << 16
            archive.writestr(info, (ROOT / relative).read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, default=int(os.environ.get("SOURCE_DATE_EPOCH", "0")))
    args = parser.parse_args()

    try:
        paths = source_paths(args.output_dir)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"qi-harness-v{args.version}"
        tar_path = args.output_dir / f"{prefix}.tar.gz"
        zip_path = args.output_dir / f"{prefix}.zip"
        write_tar_gz(tar_path, tar_bytes(paths, prefix, args.source_date_epoch), args.source_date_epoch)
        write_zip(zip_path, paths, prefix, args.source_date_epoch)
        checksum_path = args.output_dir / "SHA256SUMS"
        lines = []
        for path in (tar_path, zip_path):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.name}\n")
        checksum_path.write_text("".join(lines), encoding="ascii", newline="\n")
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"error: cannot build package: {error}", file=sys.stderr)
        return 1

    print(tar_path)
    print(zip_path)
    print(checksum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
