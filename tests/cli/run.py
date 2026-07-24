#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]


def check(name, condition, detail=""):
    if condition:
        return
    message = f"check failed: {name}"
    if detail:
        message += f": {detail}"
    raise AssertionError(message)


def run(command, *, env=None, expected=0):
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"expected exit {expected}, got {result.returncode}: {' '.join(map(str, command))}\n{result.stdout}"
        )
    return result.stdout


def main():
    with tempfile.TemporaryDirectory(prefix="qi-harness-cli-") as directory:
        temp = pathlib.Path(directory)
        cli = temp / "qi-harness"
        fixture = temp / "session-fixture"
        source_db = temp / "source.db"
        target_db = temp / "target.db"
        export_file = temp / "session.json"
        branched_file = temp / "branched.json"
        imported_file = temp / "imported.json"
        malformed_file = temp / "malformed.json"

        run(["qi", "compile", "cmd/qi-harness.qi", "-o", str(cli)])
        run(["qi", "compile", "tests/cli/session_fixture.qi", "-o", str(fixture)])
        fixture_env = os.environ.copy()
        fixture_env["QI_CLI_FIXTURE_DB"] = str(source_db)
        run([str(fixture)], env=fixture_env)

        output = run(
            [str(cli), "--db", str(source_db), "session", "export", "cli-session", str(export_file)]
        )
        check("session_export_redacts_message_content", "deterministic user payload" not in output)
        original = json.loads(export_file.read_text())
        check("session_export_version", original["version"] == 1, repr(original["version"]))
        check(
            "session_export_id",
            original["session_id"] == "cli-session",
            repr(original["session_id"]),
        )
        check("session_export_entry_count", len(original["entries"]) == 2, str(len(original["entries"])))

        root_entry = original["entries"][0]["entry_id"]
        run([str(cli), "--db", str(source_db), "session", "branch", "cli-session", root_entry])
        run(
            [str(cli), "--db", str(source_db), "session", "export", "cli-session", str(branched_file)]
        )
        branched = json.loads(branched_file.read_text())
        check(
            "session_branch_updates_leaf",
            branched["leaf_entry_id"] == root_entry,
            f"expected {root_entry!r}, got {branched['leaf_entry_id']!r}",
        )
        check("session_branch_preserves_entries", branched["entries"] == original["entries"])

        run([str(cli), "--db", str(target_db), "session", "import", str(branched_file)])
        run(
            [str(cli), "--db", str(target_db), "session", "export", "cli-session", str(imported_file)]
        )
        check(
            "session_import_round_trip",
            imported_file.read_bytes() == branched_file.read_bytes(),
        )

        unknown = run(
            [str(cli), "--db", str(source_db), "session", "export", "missing-session", str(temp / "missing.json")],
            expected=1,
        )
        check(
            "unknown_session_error",
            "ERROR session not found: missing-session" in unknown,
            unknown.strip(),
        )

        missing_entry = run(
            [str(cli), "--db", str(source_db), "session", "branch", "cli-session", "missing-entry"],
            expected=1,
        )
        check(
            "unknown_entry_error",
            "ERROR entry not found in session cli-session: missing-entry" in missing_entry,
            missing_entry.strip(),
        )

        malformed_file.write_text("not-json")
        malformed = run(
            [str(cli), "--db", str(target_db), "session", "import", str(malformed_file)], expected=1
        )
        check("malformed_import_error", "ERROR malformed session import" in malformed, malformed.strip())

        collision = run(
            [str(cli), "--db", str(target_db), "session", "import", str(branched_file)], expected=1
        )
        check("import_collision_error", "ERROR session import conflicts" in collision, collision.strip())

        secret = "cli-secret-must-not-appear"
        check_env = os.environ.copy()
        for name in (
            "QI_CHAT_URL", "QI_CHAT_KEY", "QI_CHAT_MODEL", "QI_LLM_BASE", "QI_LLM_KEY",
            "QI_LLM_MODEL", "DEEPSEEK_API_KEY", "QI_EMBED_URL", "QI_EMBED_KEY",
            "QI_EMBED_MODEL", "QI_MCP_COMMAND", "QI_MCP_URL",
        ):
            check_env.pop(name, None)
        check_env["QI_LLM_KEY"] = secret
        check_env["QI_EMBED_URL"] = "https://embedding.invalid/v1"
        check_env["QI_MCP_COMMAND"] = "secret-bearing-command"
        status_output = run([str(cli), "--db", str(source_db), "status"], env=check_env)
        check("status_redacts_chat_secret", secret not in status_output)
        check("status_redacts_embedding_url", "embedding.invalid" not in status_output)
        check("status_redacts_mcp_command", "secret-bearing-command" not in status_output)
        status = json.loads(status_output)
        expected_chat_status = {
            "status": "configured", "mode": "llm", "ready": True,
            "chat_url_configured": False, "chat_key_configured": False,
            "chat_model_configured": False, "llm_base_configured": False,
            "llm_key_configured": True, "llm_model_configured": False,
            "deepseek_key_configured": False,
        }
        check(
            "status_chat_configuration",
            status["chat"] == expected_chat_status,
            f"expected {expected_chat_status!r}, got {status['chat']!r}",
        )
        check(
            "status_embedding_unconfigured",
            status["embedding"]["status"] == "unconfigured",
            repr(status["embedding"]["status"]),
        )
        check("status_embedding_url_configured", status["embedding"]["url_configured"] is True)
        check("status_mcp_command_configured", status["mcp"]["command_configured"] is True)
        check("status_session_db_accessible", status["session_db"]["accessible"] is True)

        run([str(cli), "--db", str(source_db), "status", "--require-chat"], env=check_env)
        run(
            [str(cli), "--db", str(source_db), "status", "--require-embedding"],
            env=check_env,
            expected=1,
        )

        restore_output = run(
            [str(cli), "--db", str(source_db), "session", "restore-info", "cli-session"],
            env=check_env,
        )
        check("restore_info_redacts_message_content", "deterministic user payload" not in restore_output)
        restore_info = json.loads(restore_output)
        check(
            "restore_info_session_id",
            restore_info["session_id"] == "cli-session",
            repr(restore_info["session_id"]),
        )
        check(
            "restore_info_path_entry_count",
            restore_info["path_entry_count"] == 1,
            repr(restore_info["path_entry_count"]),
        )
        check(
            "restore_info_message_count",
            restore_info["restorable_message_count"] == 1,
            repr(restore_info["restorable_message_count"]),
        )
        check(
            "restore_info_message_role",
            restore_info["restorable_messages"][0]["role"] == "user",
            repr(restore_info["restorable_messages"][0]["role"]),
        )

    print("PASS cli_e2e_native_compile")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test-failure"]:
        check("intentional_failure", False)
    if len(sys.argv) != 1:
        raise SystemExit(f"usage: {sys.argv[0]} [--self-test-failure]")
    main()
