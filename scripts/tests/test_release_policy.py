#!/usr/bin/env python3
"""Deterministic tests for release policy scripts."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_script(name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("QI_COMPAT_ALLOW_DEVELOPMENT", None)
    return subprocess.run(
        ["python3", str(ROOT / "scripts" / name), *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class VersionTests(unittest.TestCase):
    def test_current_check_and_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "qi.toml"
            config.write_text('[包]\n版本 = "0.1.0"\n', encoding="utf-8")
            self.assertEqual(run_script("version.py", "--file", str(config), "current").stdout.strip(), "0.1.0")
            self.assertEqual(run_script("version.py", "--file", str(config), "check", "v0.1.0").returncode, 0)
            self.assertEqual(run_script("version.py", "--file", str(config), "set", "0.2.0").returncode, 0)
            self.assertIn('版本 = "0.2.0"', config.read_text(encoding="utf-8"))

    def test_rejects_invalid_semver(self) -> None:
        result = run_script("version.py", "check", "01.2.3")
        self.assertEqual(result.returncode, 2)


class ApiDiffTests(unittest.TestCase):
    def classify(self, before: str, after: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = root / "before.txt", root / "after.txt"
            baseline.write_text(before, encoding="utf-8")
            candidate.write_text(after, encoding="utf-8")
            return run_script("api-diff.py", *arguments, str(baseline), str(candidate))

    def test_classifications(self) -> None:
        baseline = "[Harness]\n函数 alpha()\n"
        self.assertEqual(self.classify(baseline, baseline).returncode, 0)
        self.assertEqual(self.classify(baseline, baseline + "函数 beta()\n").returncode, 10)
        self.assertEqual(self.classify(baseline, "[Harness]\n函数 alpha(x: 整数)\n").returncode, 20)
        self.assertEqual(self.classify("函数 alpha()\n", baseline).returncode, 30)

    def test_additive_drift_is_accepted_only_for_minor_or_major_releases(self) -> None:
        baseline = "[Harness]\n函数 alpha()\n"
        additive = baseline + "函数 beta()\n"
        self.assertEqual(
            self.classify(additive, additive, "--baseline-version", "0.2.0", "--candidate-version", "0.2.1").returncode,
            0,
        )
        self.assertEqual(
            self.classify(baseline, additive, "--baseline-version", "0.1.0", "--candidate-version", "0.2.0").returncode,
            0,
        )
        self.assertEqual(
            self.classify(baseline, additive, "--baseline-version", "0.2.0", "--candidate-version", "0.2.1").returncode,
            10,
        )

    def test_breaking_drift_is_rejected_for_minor_and_major_releases(self) -> None:
        baseline = "[Harness]\n函数 alpha()\n"
        breaking = "[Harness]\n函数 alpha(x: 整数)\n"
        for candidate_version in ("0.2.0", "1.0.0"):
            with self.subTest(candidate_version=candidate_version):
                self.assertEqual(
                    self.classify(
                        baseline,
                        breaking,
                        "--baseline-version",
                        "0.1.0",
                        "--candidate-version",
                        candidate_version,
                    ).returncode,
                    20,
                )


class HistoricalApiBaselineTests(unittest.TestCase):
    def test_historical_baseline_is_generated_from_pinned_tag_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "public-api.txt"
            result = run_script("historical-api-baseline.py", "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = output.read_text(encoding="utf-8")
            self.assertIn("[Harness]", manifest)
            self.assertIn("[Harness.MCP客户端]", manifest)

    def test_policy_runner_allows_additive_drift_from_verified_historical_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "public-api.txt"
            generated = run_script("historical-api-baseline.py", "--output", str(output))
            self.assertEqual(generated.returncode, 0, generated.stderr)
            env = os.environ.copy()
            env.pop("QI_DIFF_BASE", None)
            env["QI_RELEASE_POLICY_SELF_TEST"] = "1"
            env["QI_API_BASELINE_FILE"] = str(output)
            env["QI_API_BASELINE_VERSION"] = "0.1.0"
            result = subprocess.run(
                ["sh", str(ROOT / "scripts" / "tests" / "run.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("additive public API drift", result.stdout)
            self.assertIn("additive drift allowed for 0.1.0 -> 0.2.0", result.stdout)


class ReleaseNotesTests(unittest.TestCase):
    def test_release_and_unreleased_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            changelog, migrating = root / "CHANGELOG.md", root / "MIGRATING.md"
            changelog.write_text("# Changelog\n\n## [Unreleased]\n\n## [0.2.0] - 2026-07-23\n", encoding="utf-8")
            migrating.write_text("# Migrating qi-harness\n\n## Migrating to 0.2.0\n\nDo this.\n", encoding="utf-8")
            common = ("--changelog", str(changelog), "--migrating", str(migrating))
            self.assertEqual(run_script("check-release-notes.py", "0.2.0", *common).returncode, 0)
            self.assertEqual(run_script("check-release-notes.py", "--unreleased", *common).returncode, 0)
            self.assertEqual(run_script("check-release-notes.py", "0.3.0", *common).returncode, 1)


class QiCompatibilityTests(unittest.TestCase):
    def fake_qi(self, root: Path, version: str) -> tuple[Path, Path]:
        log = root / "calls.log"
        executable = root / "qi"
        executable.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            f"  printf 'qi v{version}\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        return executable, log

    def test_published_minimum_matches_governed_release(self) -> None:
        manifest = (ROOT / "qi.toml").read_text(encoding="utf-8")
        self.assertIn('最低Qi版本 = "2026.07.24-1"', manifest)

    def test_source_baseline_runs_all_compile_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable, log = self.fake_qi(Path(temporary), "2026.7.24-1")
            result = run_script("check-qi-compat.py", "--qi", str(executable))
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls[0], "--version")
            self.assertEqual(sum(call.startswith("compile ") for call in calls), 3)

    def test_development_override_runs_all_compile_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable, log = self.fake_qi(Path(temporary), "2026.7.22-1")
            result = run_script(
                "check-qi-compat.py",
                "--qi",
                str(executable),
                "--allow-development-capabilities",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls[0], "--version")
            self.assertEqual(sum(call.startswith("compile ") for call in calls), 3)


class QiSourcePolicyTests(unittest.TestCase):
    def test_unpublished_qi_version_is_never_claimed(self) -> None:
        files = [
            ROOT / ".github" / "workflows" / "ci.yml",
            ROOT / ".github" / "workflows" / "release.yml",
            *sorted((ROOT / "scripts").glob("*.sh")),
            *sorted((ROOT / "scripts").glob("*.py")),
        ]
        prohibited = (
            "2026.08.01-1",
            "releases/download/2026.08.01-1",
            "refs/tags/2026.08.01-1",
        )
        for path in files:
            text = path.read_text(encoding="utf-8")
            for value in prohibited:
                self.assertNotIn(value, text, f"{path.relative_to(ROOT)} references unavailable {value}")

    def test_workflows_build_qi_from_source(self) -> None:
        for name in ("ci.yml", "release.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("scripts/install-qi-source.sh", text)
            self.assertNotRegex(text, re.compile(r"qilang-project/qi/releases/download"))

    def test_workflows_pin_explicit_source_commits(self) -> None:
        for name in ("ci.yml", "release.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("05568a72a92698502fe006bd3223536e9cb04887", text)
            self.assertIn("ceada461d2aca568b2b3788f3b310fcc07748423", text)

    def test_release_runs_canonical_gate_with_pinned_web_package(self) -> None:
        text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("QI_WEB_REF:", text)
        self.assertIn("qilang-project/qi-web.git", text)
        self.assertIn('checkout --quiet "$QI_WEB_REF"', text)
        self.assertIn('run: "$GITHUB_WORKSPACE/run-offline-tests.sh"', text)
        self.assertNotIn('run: "$GITHUB_WORKSPACE/scripts/tests/run.sh"', text)

    def test_tag_release_uses_prior_verified_api_baseline(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        policy_runner = (ROOT / "scripts" / "tests" / "run.sh").read_text(encoding="utf-8")
        self.assertIn('git tag --merged "$GITHUB_SHA^" --list \'v*.*.*\'', workflow)
        self.assertIn('git verify-tag "$tag"', workflow)
        self.assertIn('git cat-file -e "$tag:public-api.txt"', workflow)
        self.assertIn('QI_DIFF_BASE: ${{ steps.api_baseline.outputs.ref }}', workflow)
        self.assertIn('QI_API_BASELINE_FILE: ${{ steps.api_baseline.outputs.manifest }}', workflow)
        self.assertIn('QI_API_BASELINE_VERSION: ${{ steps.api_baseline.outputs.version }}', workflow)
        self.assertNotIn('QI_DIFF_BASE: ${{ github.event.before }}', workflow)
        self.assertIn("QI_DIFF_BASE is not a commit", policy_runner)
        self.assertIn("QI_DIFF_BASE lacks public-api.txt or qi.toml", policy_runner)

    def test_first_governed_release_uses_immutable_historical_api_bootstrap(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        generator = (ROOT / "scripts" / "historical-api-baseline.py").read_text(encoding="utf-8")
        self.assertIn('if [[ "${{ steps.version.outputs.version }}" != "0.2.0" ]]', workflow)
        self.assertIn("python3 scripts/historical-api-baseline.py", workflow)
        self.assertIn('HISTORICAL_TAG = "2026.05.30-1"', generator)
        self.assertIn('HISTORICAL_COMMIT = "11ad18011059726535163dfd3280996f03c095ca"', generator)
        self.assertIn('HISTORICAL_VERSION = "0.1.0"', generator)
        self.assertIn('"merge-base", "--is-ancestor", HISTORICAL_COMMIT, "HEAD"', generator)
        self.assertRegex(generator, r'EXPECTED_MANIFEST_SHA256 = "[0-9a-f]{64}"')
        self.assertNotIn("github.event.before", workflow)

    def test_runtime_probe_covers_every_required_abi_family(self) -> None:
        text = (ROOT / "tests" / "compatibility" / "runtime_abi_probe.qi").read_text(encoding="utf-8")
        for symbol in (
            "限时读取流事件V2",
            "等待状态",
            "请求主体超过上限",
        ):
            self.assertIn(symbol, text)

    def test_source_installer_requires_full_commit_refs(self) -> None:
        text = (ROOT / "scripts" / "install-qi-source.sh").read_text(encoding="utf-8")
        self.assertIn("must be a full commit SHA", text)
        self.assertIn("stdlib_abi_parity", text)


if __name__ == "__main__":
    unittest.main()
