#!/usr/bin/env python3
"""Verify that Qi supplies every compiler/runtime ABI required by qi-harness."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "qi.toml"
VERSION_RE = re.compile(r"qi v(\d{4})\.(\d{1,2})\.(\d{1,2})-(\d+)\b", re.IGNORECASE)
MINIMUM_RE = re.compile(r'^\s*最低Qi版本\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
UNPUBLISHED = "未发布"
PROBES = (
    ROOT / "tests" / "compatibility" / "legacy_agent_probe.qi",
    ROOT / "tests" / "compatibility" / "historical_eval_probe.qi",
    ROOT / "tests" / "compatibility" / "runtime_abi_probe.qi",
)


def parse_version(value: str) -> tuple[int, int, int, int]:
    match = VERSION_RE.search(value if value.lower().startswith("qi v") else f"qi v{value}")
    if match is None:
        raise ValueError(f"unrecognized Qi version: {value.strip()}")
    return tuple(int(part) for part in match.groups())


def minimum_version() -> str:
    match = MINIMUM_RE.search(MANIFEST.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError("qi.toml is missing 元数据.最低Qi版本")
    return match.group(1)


def run(arguments: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qi", default=os.environ.get("QI_BIN", "qi"), help="Qi executable")
    parser.add_argument(
        "--allow-development-capabilities",
        action="store_true",
        default=os.environ.get("QI_COMPAT_ALLOW_DEVELOPMENT") == "1",
        help="allow a below-baseline development build only if all ABI probes compile and link",
    )
    args = parser.parse_args()

    try:
        required_text = minimum_version()
        required = None if required_text == UNPUBLISHED else parse_version(required_text)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    version = run([args.qi, "--version"])
    if version.returncode != 0:
        print(version.stdout, end="", file=sys.stderr)
        return version.returncode
    try:
        current = parse_version(version.stdout)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if required is not None and current < required and not args.allow_development_capabilities:
        print(
            f"error: qi-harness requires Qi {required_text} or newer; found {version.stdout.strip()}",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="qi-harness-compat-") as temporary:
        workspace = Path(temporary)
        packages = workspace / "qi_packages"
        packages.mkdir()
        (packages / "Harness").symlink_to(ROOT, target_is_directory=True)
        probe_env = os.environ.copy()
        existing_packages = probe_env.get("QI_PACKAGES_PATH")
        probe_env["QI_PACKAGES_PATH"] = str(packages) + (
            os.pathsep + existing_packages if existing_packages else ""
        )
        for probe in PROBES:
            source = workspace / probe.name
            shutil.copyfile(probe, source)
            output = workspace / probe.stem
            compiled = run([args.qi, "compile", str(source), "-o", str(output)], env=probe_env)
            if compiled.returncode != 0:
                print(f"error: Qi ABI probe failed: {probe.relative_to(ROOT)}", file=sys.stderr)
                print(compiled.stdout, end="", file=sys.stderr)
                return compiled.returncode

    if required is None:
        print(
            f"Qi source compatibility verified: {version.stdout.strip()} passes all required ABI probes; "
            "no published Qi release baseline exists yet"
        )
    elif current < required:
        print(
            f"Qi development ABI probes passed: {version.stdout.strip()} "
            f"(release baseline remains {required_text})"
        )
    else:
        print(f"Qi compatibility verified: {version.stdout.strip()} >= {required_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
