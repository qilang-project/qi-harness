#!/usr/bin/env python3
"""Generate the one-time v0.2.0 API baseline from an immutable historical release."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_TAG = "2026.05.30-1"
HISTORICAL_COMMIT = "11ad18011059726535163dfd3280996f03c095ca"
HISTORICAL_VERSION = "0.1.0"
EXPECTED_MANIFEST_SHA256 = "91b95f62194424b8b59fafe081d5d50be96550a99390f396beef5601b5679b90"


def git(*arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], stderr=subprocess.PIPE)


def public_api_module():
    checker = ROOT / "check-public-api.py"
    spec = importlib.util.spec_from_file_location("qi_harness_public_api", checker)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load check-public-api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_historical_identity() -> None:
    resolved = git("rev-parse", f"refs/tags/{HISTORICAL_TAG}^{{commit}}").decode().strip()
    if resolved != HISTORICAL_COMMIT:
        raise ValueError(
            f"historical tag {HISTORICAL_TAG} resolves to {resolved}, expected {HISTORICAL_COMMIT}"
        )
    git("cat-file", "-e", f"{HISTORICAL_COMMIT}^{{commit}}")
    subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", HISTORICAL_COMMIT, "HEAD"],
        check=True,
        stderr=subprocess.PIPE,
    )
    config = git("show", f"{HISTORICAL_COMMIT}:qi.toml").decode()
    versions = re.findall(r'^\s*版本\s*=\s*"([^"]+)"', config, re.MULTILINE)
    if versions != [HISTORICAL_VERSION]:
        raise ValueError(
            f"historical qi.toml version is {versions!r}, expected {HISTORICAL_VERSION}"
        )


def generate_manifest() -> str:
    verify_historical_identity()
    names = git("ls-tree", "-z", "--name-only", HISTORICAL_COMMIT).decode().split("\0")
    source_names = sorted(name for name in names if name.endswith(".qi") and "/" not in name)
    if "Harness.qi" not in source_names:
        raise ValueError("historical commit lacks Harness.qi")

    with tempfile.TemporaryDirectory(prefix="qi-harness-historical-api-") as temporary:
        root = Path(temporary)
        for name in source_names:
            (root / name).write_bytes(git("show", f"{HISTORICAL_COMMIT}:{name}"))
        manifest = public_api_module().render_manifest(root)

    digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    if digest != EXPECTED_MANIFEST_SHA256:
        raise ValueError(
            f"historical API digest is {digest}, expected {EXPECTED_MANIFEST_SHA256}"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="write the verified manifest to this path")
    args = parser.parse_args()

    try:
        manifest = generate_manifest()
        if args.output:
            args.output.write_text(manifest, encoding="utf-8")
        else:
            sys.stdout.write(manifest)
        return 0
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        print(f"error: cannot generate historical API baseline: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
