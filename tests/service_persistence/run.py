#!/usr/bin/env python3
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent
QI = os.environ.get("QI_BIN", "qi")


class TestFailure(RuntimeError):
    pass


def check(test_name: str, condition: bool, details: object) -> None:
    if not condition:
        raise TestFailure(f"{test_name} failed: {details}")


def compile_service_fixture(workspace: Path, output: Path) -> None:
    packages = workspace / "qi_packages"
    packages.mkdir()
    (packages / "Harness").symlink_to(ROOT, target_is_directory=True)
    env = os.environ.copy()
    existing_packages = env.get("QI_PACKAGES_PATH")
    env["QI_PACKAGES_PATH"] = str(packages) + (
        os.pathsep + existing_packages if existing_packages else ""
    )
    command = [QI]
    optimization = os.environ.get("QI_TEST_OPTIMIZATION")
    if optimization:
        command.extend(["-O", optimization])
    command.extend(["compile", str(TEST_DIR / "service_fixture.qi"), "-o", str(output)])
    subprocess.run(
        command,
        cwd=workspace,
        env=env,
        check=True,
        timeout=120,
    )


def free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port > 3000:
            return port


def wait_for(predicate, process: subprocess.Popen[object], message: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(
                f"{message}: process exited with status {process.returncode}\n{output}"
            )
        if predicate():
            return
        time.sleep(0.03)
    output = ""
    if process.stdout is not None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        output = process.stdout.read()
    raise RuntimeError(f"timed out waiting for {message}\n{output}")


def post(port: int, path: str, payload: dict[str, object], token: str | None = "token-a") -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("POST", path, body=encoded, headers=headers)
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()
    return response.status, body


def capture_thread_result(
    results: list[tuple[int, dict[str, object]]],
    errors: list[BaseException],
    port: int,
    path: str,
    payload: dict[str, object],
    token: str | None = "token-a",
) -> None:
    try:
        results.append(post(port, path, payload, token=token))
    except BaseException as error:
        errors.append(error)


def raw_post(port: int, path: str, body: bytes, token: str | None = "token-a") -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("POST", path, body=body, headers=headers)
    response = connection.getresponse()
    decoded = response.read()
    connection.close()
    return response.status, decoded


def read_raw_response(sock: socket.socket) -> tuple[int, bytes]:
    response = bytearray()
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response.extend(chunk)
    head, body = bytes(response).split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    return status, body


def oversized_content_length_headers(port: int) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            b"POST /chat HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 257\r\n\r\n"
        )
        return read_raw_response(sock)


def oversized_chunked_stream(port: int) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(
            b"POST /chat HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Authorization: Bearer token-a\r\n"
            b"Content-Type: application/json\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"80\r\n" + b"x" * 128 + b"\r\n"
        )
        sock.sendall(b"81\r\n")
        return read_raw_response(sock)


def assert_running(process: subprocess.Popen[object]) -> None:
    if process.poll() is None:
        return
    output = process.stdout.read() if process.stdout is not None else ""
    raise RuntimeError(f"service exited with status {process.returncode}\n{output}")


def health(port: int, expected_mode: str = "persistent") -> bool:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status == 200 and body.get("会话模式") == expected_mode
    except (OSError, json.JSONDecodeError):
        return False


def stop(process: subprocess.Popen[object]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def start_service(
    env: dict[str, str], expected_mode: str = "persistent"
) -> subprocess.Popen[object]:
    process = subprocess.Popen(
        [env["QI_TEST_SERVICE_BIN"]],
        cwd=ROOT,
        env=env,
    )
    wait_for(
        lambda: health(int(env["QI_TEST_SERVICE_PORT"]), expected_mode),
        process,
        "service",
    )
    return process


def session_locked(database: Path, session_id: str) -> bool:
    try:
        with sqlite3.connect(database, timeout=0.1) as connection:
            row = connection.execute(
                'SELECT COUNT(*) FROM "服务会话请求锁" WHERE "会话ID" = ?',
                (session_id,),
            ).fetchone()
        return row is not None and row[0] == 1
    except sqlite3.Error:
        return False


def session_lease(database: Path, session_id: str) -> tuple[str, int, int] | None:
    try:
        with sqlite3.connect(database, timeout=0.1) as connection:
            row = connection.execute(
                'SELECT "所有者令牌", "租约到期毫秒", "栅栏" '
                'FROM "服务会话请求锁" WHERE "会话ID" = ?',
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row[0]), int(row[1]), int(row[2])
    except sqlite3.Error:
        return None


def session_contains(database: Path, session_id: str, text: str) -> bool:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            'SELECT COUNT(*) FROM "会话条目" WHERE "会话ID" = ? AND "载荷JSON" LIKE ?',
            (session_id, f"%{text}%"),
        ).fetchone()
    check(
        "session_contains_query",
        row is not None,
        f"database query returned no row for session_id={session_id!r}, text={text!r}",
    )
    return int(row[0]) > 0


def session_entries(database: Path, session_id: str) -> list[tuple[str, str, str]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            'SELECT "条目ID", "类型", "载荷JSON" FROM "会话条目" '
            'WHERE "会话ID" = ? ORDER BY "序号"',
            (session_id,),
        ).fetchall()
    return [(str(entry_id), str(entry_type), str(payload)) for entry_id, entry_type, payload in rows]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-service-persistence-") as temporary:
        temporary_path = Path(temporary)
        port_file = temporary_path / "model-port"
        fixture = subprocess.Popen(
            [sys.executable, str(TEST_DIR / "fake_openai.py"), "--port-file", str(port_file)],
            cwd=ROOT,
        )
        service: subprocess.Popen[object] | None = None
        try:
            wait_for(port_file.exists, fixture, "model fixture")
            model_port = int(port_file.read_text(encoding="utf-8"))
            service_port = free_port()
            service_binary = temporary_path / "service-fixture"
            compile_service_fixture(temporary_path, service_binary)
            env = os.environ.copy()
            env["QI_TEST_URL"] = f"http://127.0.0.1:{model_port}/v1/chat/completions"
            session_database = temporary_path / "sessions.db"
            with sqlite3.connect(session_database) as connection:
                connection.execute(
                    'CREATE TABLE "服务会话请求锁" ('
                    '"会话ID" TEXT PRIMARY KEY, "锁令牌" TEXT NOT NULL)'
                )
                connection.execute(
                    'INSERT INTO "服务会话请求锁" ("会话ID", "锁令牌") '
                    'VALUES (?, ?)',
                    ("legacy-orphan", "legacy-token"),
                )
            env["QI_TEST_SESSION_DB"] = str(session_database)
            env["QI_TEST_SERVICE_PORT"] = str(service_port)
            env["QI_TEST_SERVICE_BIN"] = str(service_binary)
            audit_log = temporary_path / "audit.jsonl"
            env["QI_TEST_AUDIT_LOG"] = str(audit_log)

            service = start_service(env)
            with sqlite3.connect(session_database) as connection:
                lock_columns = {
                    str(row[1])
                    for row in connection.execute(
                        'PRAGMA table_info("服务会话请求锁")'
                    ).fetchall()
                }
                migrated_lock = connection.execute(
                    'SELECT "所有者令牌", "租约到期毫秒", "栅栏" '
                    'FROM "服务会话请求锁" WHERE "会话ID" = ?',
                    ("legacy-orphan",),
                ).fetchone()
            check(
                "service_legacy_forever_lock_schema_migrates_to_leases",
                lock_columns == {"会话ID", "所有者令牌", "租约到期毫秒", "栅栏"},
                f"unexpected lock columns: {lock_columns!r}",
            )
            check(
                "service_legacy_forever_lock_schema_migrates_to_leases",
                migrated_lock == ("legacy-token", 0, 0),
                f"unexpected migrated lock: {migrated_lock!r}",
            )
            print("PASS service_legacy_forever_lock_schema_migrates_to_leases")
            status, missing = post(service_port, "/chat", {"提示": "missing"}, token=None)
            check(
                "service_missing_and_invalid_bearer",
                status == 401 and "Bearer" in str(missing.get("错误")),
                f"missing bearer response: status={status}, body={missing!r}",
            )
            status, invalid = post(service_port, "/chat", {"提示": "invalid"}, token="bad-token")
            check(
                "service_missing_and_invalid_bearer",
                status == 401 and invalid == missing,
                f"invalid bearer response differs: status={status}, body={invalid!r}, missing={missing!r}",
            )
            print("PASS service_missing_and_invalid_bearer")

            status, created = post(service_port, "/chat", {
                "提示": "first", "所有者": "tenant-forged", "租户ID": "tenant-forged",
                "主体ID": "forged", "角色": "admin", "权限": "*",
            })
            session_id = created.get("会话ID")
            check(
                "service_session_create",
                status == 200 and isinstance(session_id, str) and bool(session_id),
                f"create response: status={status}, body={created!r}",
            )
            check(
                "service_session_create",
                created.get("回复") == "turn-1",
                f"unexpected create reply: {created!r}",
            )
            with sqlite3.connect(session_database) as connection:
                owner = connection.execute(
                    'SELECT "所有者" FROM "服务会话所有者" WHERE "会话ID" = ?',
                    (session_id,),
                ).fetchone()
            check(
                "service_forged_identity_body_ignored",
                owner == ("tenant-a",),
                f"persisted owner was {owner!r}",
            )
            print("PASS service_forged_identity_body_ignored")
            print("PASS service_session_create")

            stop(service)
            service = start_service(env)
            try:
                status, continued = post(service_port, "/chat", {
                    "提示": "second", "会话ID": session_id,
                })
            except http.client.RemoteDisconnected:
                assert_running(service)
                raise
            check(
                "service_session_continue_after_restart",
                status == 200 and continued.get("会话ID") == session_id,
                f"continue response: status={status}, body={continued!r}, expected session={session_id!r}",
            )
            check(
                "service_session_continue_after_restart",
                continued.get("回复") == "turn-2",
                f"unexpected continue reply: {continued!r}",
            )
            print("PASS service_session_continue_after_restart")

            status, orphan_created = post(service_port, "/chat", {"提示": "orphan first"})
            orphan_session_id = orphan_created.get("会话ID")
            check(
                "service_unexpired_orphan_lease_conflicts",
                status == 200 and isinstance(orphan_session_id, str),
                f"orphan create response: status={status}, body={orphan_created!r}",
            )

            # Graceful service termination lets in-flight handlers release their leases.
            # Seed the exact durable state left by an abrupt process crash instead.
            stop(service)
            old_lease = ("orphaned-process-owner", int(time.time() * 1000) + 60000, 41)
            with sqlite3.connect(session_database) as connection:
                connection.execute(
                    'INSERT INTO "服务会话请求锁" '
                    '("会话ID", "所有者令牌", "租约到期毫秒", "栅栏") '
                    'VALUES (?, ?, ?, ?)',
                    (orphan_session_id, *old_lease),
                )
            service = start_service(env)
            check(
                "service_unexpired_orphan_lease_conflicts",
                session_lease(session_database, orphan_session_id) == old_lease,
                f"lease changed on restart; expected={old_lease!r}, actual={session_lease(session_database, orphan_session_id)!r}",
            )
            status, orphan_conflict = post(service_port, "/chat", {
                "提示": "before expiry", "会话ID": orphan_session_id,
            })
            check(
                "service_unexpired_orphan_lease_conflicts",
                status == 409 and orphan_conflict.get("错误") == "会话正由另一个请求更新，请重试",
                f"conflict response: status={status}, body={orphan_conflict!r}",
            )
            check(
                "service_unexpired_orphan_lease_conflicts",
                session_lease(session_database, orphan_session_id) == old_lease,
                f"conflict mutated lease; expected={old_lease!r}, actual={session_lease(session_database, orphan_session_id)!r}",
            )
            print("PASS service_unexpired_orphan_lease_conflicts")

            stop(service)
            with sqlite3.connect(session_database) as connection:
                connection.execute(
                    'UPDATE "服务会话请求锁" SET "租约到期毫秒" = ? '
                    'WHERE "会话ID" = ?',
                    (int(time.time() * 1000) - 1, orphan_session_id),
                )
            service = start_service(env)

            takeover_result: list[tuple[int, dict[str, object]]] = []
            takeover_errors: list[BaseException] = []
            takeover_thread = threading.Thread(
                target=capture_thread_result,
                args=(takeover_result, takeover_errors, service_port, "/chat", {
                    "提示": "slow", "会话ID": orphan_session_id,
                }),
            )
            takeover_thread.start()
            wait_for(
                lambda: (
                    (lease := session_lease(session_database, orphan_session_id)) is not None
                    and lease[0] != old_lease[0]
                ),
                service,
                "expired lease takeover",
            )
            new_lease = session_lease(session_database, orphan_session_id)
            check(
                "service_stale_lease_takeover_after_restart",
                new_lease is not None,
                "takeover lease was not persisted",
            )
            check(
                "service_stale_lease_takeover_after_restart",
                new_lease is not None
                and new_lease[0] != old_lease[0]
                and new_lease[2] > old_lease[2],
                f"invalid takeover lease: old={old_lease!r}, new={new_lease!r}",
            )

            with sqlite3.connect(session_database) as connection:
                stale_touch = connection.execute(
                    'UPDATE "服务会话请求锁" SET "租约到期毫秒" = ? '
                    'WHERE "会话ID" = ? AND "所有者令牌" = ? AND "栅栏" = ?',
                    (int(time.time() * 1000) + 999999, orphan_session_id,
                     old_lease[0], old_lease[2]),
                ).rowcount
                stale_unlock = connection.execute(
                    'DELETE FROM "服务会话请求锁" WHERE "会话ID" = ? '
                    'AND "所有者令牌" = ? AND "栅栏" = ?',
                    (orphan_session_id, old_lease[0], old_lease[2]),
                ).rowcount
            check(
                "service_old_owner_cannot_touch_or_unlock_new_lease",
                stale_touch == 0 and stale_unlock == 0,
                f"stale operations changed rows: touch={stale_touch}, unlock={stale_unlock}",
            )
            current_lease = session_lease(session_database, orphan_session_id)
            check(
                "service_old_owner_cannot_touch_or_unlock_new_lease",
                current_lease is not None,
                "current lease disappeared after stale operations",
            )
            check(
                "service_old_owner_cannot_touch_or_unlock_new_lease",
                current_lease is not None
                and new_lease is not None
                and current_lease[0] == new_lease[0]
                and current_lease[2] == new_lease[2],
                f"current lease identity changed: expected={new_lease!r}, actual={current_lease!r}",
            )
            check(
                "service_old_owner_cannot_touch_or_unlock_new_lease",
                current_lease is not None
                and current_lease[1] != int(time.time() * 1000) + 999999,
                f"stale owner extended current lease: {current_lease!r}",
            )
            print("PASS service_stale_lease_takeover_after_restart")
            print("PASS service_old_owner_cannot_touch_or_unlock_new_lease")

            status, takeover_conflict = post(service_port, "/chat", {
                "提示": "competing", "会话ID": orphan_session_id,
            })
            check(
                "service_active_taken_over_lease_conflicts",
                status == 409 and takeover_conflict.get("错误") == "会话正由另一个请求更新，请重试",
                f"conflict response: status={status}, body={takeover_conflict!r}",
            )
            takeover_thread.join(timeout=5)
            check(
                "service_active_taken_over_lease_conflicts",
                not takeover_thread.is_alive(),
                "takeover request thread did not finish within 5 seconds",
            )
            check(
                "service_active_taken_over_lease_conflicts",
                not takeover_errors and bool(takeover_result) and takeover_result[0][0] == 200,
                f"takeover request result: {takeover_result!r}, errors={takeover_errors!r}",
            )
            check(
                "service_active_taken_over_lease_conflicts",
                bool(takeover_result)
                and takeover_result[0][1].get("会话ID") == orphan_session_id,
                f"takeover returned wrong session: result={takeover_result!r}, expected={orphan_session_id!r}",
            )
            print("PASS service_active_taken_over_lease_conflicts")

            slow_result: list[tuple[int, dict[str, object]]] = []
            slow_errors: list[BaseException] = []
            slow_thread = threading.Thread(
                target=capture_thread_result,
                args=(slow_result, slow_errors, service_port, "/chat", {
                    "提示": "slow", "会话ID": session_id,
                }),
            )
            slow_thread.start()
            wait_for(
                lambda: session_locked(session_database, str(session_id)) or bool(slow_result),
                service,
                "session request lock",
            )
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                not slow_result,
                f"slow request completed before locking: {slow_result!r}",
            )
            status, conflict = post(service_port, "/chat", {
                "提示": "competing", "会话ID": session_id,
            })
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                status == 409 and conflict.get("错误") == "会话正由另一个请求更新，请重试",
                f"conflict response: status={status}, body={conflict!r}",
            )
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                not session_contains(session_database, str(session_id), "competing"),
                "competing request was persisted despite conflict",
            )
            slow_thread.join(timeout=5)
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                not slow_thread.is_alive(),
                "slow request thread did not finish within 5 seconds",
            )
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                not slow_errors and bool(slow_result) and slow_result[0][0] == 200,
                f"slow request result: {slow_result!r}, errors={slow_errors!r}",
            )
            check(
                "service_concurrent_same_session_conflicts_without_interleaving",
                bool(slow_result) and slow_result[0][1].get("回复") == "turn-3",
                f"unexpected slow request reply: {slow_result!r}",
            )
            print("PASS service_concurrent_same_session_conflicts_without_interleaving")
            print("PASS service_session_conflict_does_not_consume_or_leak_quota")

            block_file = temporary_path / "blocked-tool"
            stop(service)
            env["QI_TEST_BLOCK_TOOL_FILE"] = str(block_file)
            env["QI_TEST_LEASE_MS"] = "150"
            service = start_service(env)
            stale_result: list[tuple[int, dict[str, object]]] = []
            stale_errors: list[BaseException] = []
            stale_thread = threading.Thread(
                target=capture_thread_result,
                args=(stale_result, stale_errors, service_port, "/chat", {
                    "提示": "use blocking tool", "会话ID": session_id,
                }),
            )
            stale_thread.start()
            wait_for(
                lambda: Path(str(block_file) + ".entered").exists(),
                service,
                "blocking Agent tool",
            )
            stale_lease = session_lease(session_database, str(session_id))
            check(
                "service_stale_agent_writer_is_fenced",
                stale_lease is not None,
                "old Agent request had no durable lease",
            )
            with sqlite3.connect(session_database) as connection:
                connection.execute(
                    'UPDATE "服务会话请求锁" SET "租约到期毫秒" = ? '
                    'WHERE "会话ID" = ? AND "所有者令牌" = ? AND "栅栏" = ?',
                    (int(time.time() * 1000) - 1, session_id,
                     stale_lease[0], stale_lease[2]),
                )
            status, takeover = post(service_port, "/chat", {
                "提示": "takeover", "会话ID": session_id,
            }, token="token-takeover")
            check(
                "service_active_non_idempotent_tool_blocks_takeover",
                status == 409 and takeover.get("错误") == "会话正由另一个请求更新，请重试",
                f"takeover response: status={status}, body={takeover!r}",
            )
            Path(str(block_file) + ".release").write_text("1", encoding="utf-8")
            stale_thread.join(timeout=5)
            check(
                "service_active_non_idempotent_tool_blocks_takeover",
                not stale_thread.is_alive() and not stale_errors and bool(stale_result)
                and stale_result[0][0] == 409,
                f"stale Agent response: {stale_result!r}, errors={stale_errors!r}",
            )
            check(
                "service_active_non_idempotent_tool_blocks_takeover",
                Path(str(block_file) + ".entered").read_text(encoding="utf-8") == "1",
                "non-idempotent handler executed more than once",
            )
            status, takeover = post(service_port, "/chat", {
                "提示": "takeover", "会话ID": session_id,
            }, token="token-takeover")
            check(
                "service_active_non_idempotent_tool_blocks_takeover",
                status == 200 and takeover.get("回复") == "turn-4",
                f"post-handler takeover response: status={status}, body={takeover!r}",
            )
            print("PASS service_active_non_idempotent_tool_blocks_takeover")
            stop(service)
            env.pop("QI_TEST_BLOCK_TOOL_FILE", None)
            env.pop("QI_TEST_LEASE_MS", None)
            service = start_service(env)

            status, malformed = post(service_port, "/chat", {
                "提示": "malformed", "会话ID": "not-a-session",
            })
            check(
                "service_malformed_session_id",
                status == 400 and malformed.get("错误") == "会话ID格式无效",
                f"response: status={status}, body={malformed!r}",
            )
            print("PASS service_malformed_session_id")

            unknown_id = "00000000-0000-4000-8000-000000000000"
            status, unknown = post(service_port, "/chat", {
                "提示": "unknown", "会话ID": unknown_id,
            })
            check(
                "service_unknown_session",
                status == 404 and unknown.get("错误") == "会话不可用",
                f"response: status={status}, body={unknown!r}",
            )
            print("PASS service_unknown_session")

            status, mismatch = post(service_port, "/chat", {
                "提示": "forbidden", "会话ID": session_id, "所有者": "tenant-a",
            }, token="token-b")
            check(
                "service_owner_mismatch_not_enumerable",
                mismatch == unknown and status == 404,
                f"owner mismatch leaked information: status={status}, body={mismatch!r}, unknown={unknown!r}",
            )
            print("PASS service_owner_mismatch_not_enumerable")

            status, legacy = post(service_port, "/chat", {
                "提示": "legacy", "会话ID": session_id, "会话号": 1,
            })
            check(
                "service_rejects_legacy_runtime_handle",
                status == 400 and legacy.get("错误") == "持久模式不接受 会话号",
                f"response: status={status}, body={legacy!r}",
            )
            print("PASS service_rejects_legacy_runtime_handle")
            print("PASS service_prequota_rejection_does_not_release_other_request")

            status, oversized_prompt = post(service_port, "/chat", {
                "提示": "x" * 33,
            })
            check(
                "service_oversized_prompt",
                status == 413 and oversized_prompt.get("错误") == "提示过大",
                f"response: status={status}, body={oversized_prompt!r}",
            )
            print("PASS service_oversized_prompt")

            status, oversized_body = raw_post(service_port, "/chat", b"{" + b" " * 255 + b"}")
            check(
                "service_oversized_body",
                status == 413,
                f"response: status={status}, body={oversized_body!r}",
            )
            print("PASS service_oversized_body")

            status, oversized_unauthenticated = oversized_content_length_headers(service_port)
            check(
                "service_oversized_content_length_rejected_before_body_and_auth",
                status == 413,
                f"response: status={status}, body={oversized_unauthenticated!r}",
            )
            print("PASS service_oversized_content_length_rejected_before_body_and_auth")

            status, oversized_chunked = oversized_chunked_stream(service_port)
            check(
                "service_oversized_chunked_stream_rejected_before_full_body",
                status == 413,
                f"response: status={status}, body={oversized_chunked!r}",
            )
            print("PASS service_oversized_chunked_stream_rejected_before_full_body")

            status, model_error = post(service_port, "/chat", {
                "提示": "model error",
            })
            check(
                "service_runtime_cleanup_after_model_error",
                status == 200 and "500" in str(model_error.get("回复")),
                f"model error response: status={status}, body={model_error!r}",
            )
            status, after_error = post(service_port, "/chat", {
                "提示": "after error",
            })
            check(
                "service_runtime_cleanup_after_model_error",
                status == 200 and after_error.get("回复") == "turn-1",
                f"post-error response: status={status}, body={after_error!r}",
            )
            print("PASS service_runtime_cleanup_after_model_error")

            status, tool_reply = post(service_port, "/chat", {
                "提示": "use tool",
            })
            check(
                "service_persistent_tool_loop",
                status == 200 and tool_reply.get("回复") == "tool-finished",
                f"response: status={status}, body={tool_reply!r}",
            )
            check(
                "service_persistent_tool_loop",
                isinstance(tool_reply.get("会话ID"), str),
                f"missing persistent session ID: {tool_reply!r}",
            )
            print("PASS service_persistent_tool_loop")

            status, structured = post(service_port, "/json", {
                "提示": "structured", "字段": "turns",
            })
            structured_reply = json.loads(str(structured.get("回复")))
            check(
                "service_json_persistent_semantics",
                status == 200 and structured_reply == {"turns": 1},
                f"response: status={status}, body={structured!r}, decoded reply={structured_reply!r}",
            )
            check(
                "service_json_persistent_semantics",
                isinstance(structured.get("会话ID"), str) and "会话号" not in structured,
                f"unexpected session fields: {structured!r}",
            )
            print("PASS service_json_persistent_semantics")

            status, expired = post(service_port, "/chat", {"提示": "expired"}, token="token-expired")
            check(
                "service_expired_bearer",
                status == 401 and "失效" in str(expired.get("错误")),
                f"response: status={status}, body={expired!r}",
            )
            print("PASS service_expired_bearer")

            status, quota_first = post(service_port, "/chat", {"提示": "quota"}, token="token-quota")
            check(
                "service_request_quota_429",
                status == 200 and bool(quota_first.get("会话ID")),
                f"first quota response: status={status}, body={quota_first!r}",
            )
            status, quota_second = post(service_port, "/chat", {
                "提示": "quota again", "会话ID": quota_first.get("会话ID"),
            }, token="token-quota")
            check(
                "service_request_quota_429",
                status == 429 and "配额" in str(quota_second.get("错误")),
                f"second quota response: status={status}, body={quota_second!r}",
            )
            check(
                "service_quota_denial_releases_session_lock",
                not session_locked(session_database, str(quota_first.get("会话ID"))),
                f"session remained locked: {quota_first.get('会话ID')!r}",
            )
            print("PASS service_request_quota_429")
            print("PASS service_quota_denial_releases_session_lock")

            concurrent_result: list[tuple[int, dict[str, object]]] = []
            concurrent_errors: list[BaseException] = []
            concurrent_thread = threading.Thread(
                target=capture_thread_result,
                args=(concurrent_result, concurrent_errors, service_port, "/chat",
                      {"提示": "slow"}, "token-concurrent"),
            )
            concurrent_thread.start()
            time.sleep(0.1)
            status, concurrent_denied = post(
                service_port, "/chat", {"提示": "competing"}, token="token-concurrent"
            )
            check(
                "service_concurrent_run_quota_429",
                status == 429 and "配额" in str(concurrent_denied.get("错误")),
                f"denied response: status={status}, body={concurrent_denied!r}",
            )
            concurrent_thread.join(timeout=5)
            check(
                "service_quota_counters_release_after_429_and_completion",
                not concurrent_errors and bool(concurrent_result) and concurrent_result[0][0] == 200,
                f"concurrent request result: {concurrent_result!r}, errors={concurrent_errors!r}",
            )
            status, after_concurrent = post(
                service_port, "/chat", {"提示": "after concurrent"}, token="token-concurrent"
            )
            check(
                "service_quota_counters_release_after_429_and_completion",
                status == 200 and bool(after_concurrent.get("会话ID")),
                f"post-concurrency response: status={status}, body={after_concurrent!r}",
            )
            print("PASS service_concurrent_run_quota_429")
            print("PASS service_quota_counters_release_after_429_and_completion")

            audit = audit_log.read_text(encoding="utf-8")
            check(
                "service_audit_redaction",
                "token-a" not in audit and "bad-token" not in audit,
                "audit log contains a bearer token",
            )
            check(
                "service_audit_redaction",
                "tenant-forged" not in audit and "forged" not in audit,
                "audit log contains forged request identity",
            )
            check(
                "service_audit_redaction",
                '"subject_id":"subject-a"' in audit and '"reason_code":"allowed"' in audit,
                "audit log lacks the authenticated subject or allowed reason code",
            )
            print("PASS service_audit_redaction")

            stop(service)
            env["QI_TEST_ANONYMOUS_DEV"] = "1"
            env["QI_TEST_SESSION_DB"] = str(temporary_path / "dev-sessions.db")
            env["QI_TEST_DEV_REQUEST_QUOTA"] = "100"
            env["QI_TEST_AUDIT_LOG"] = str(temporary_path / "dev-audit.jsonl")
            service = start_service(env)
            status, dev = post(service_port, "/chat", {"提示": "dev compatibility"}, token=None)
            check(
                "service_anonymous_dev_mode_explicit_opt_in",
                status == 200 and dev.get("回复") == "turn-1",
                f"response: status={status}, body={dev!r}",
            )
            print("PASS service_anonymous_dev_mode_explicit_opt_in")

            stop(service)
            env["QI_TEST_SESSION_DB"] = ""
            env["QI_TEST_DEV_REQUEST_QUOTA"] = "1"

            env["QI_TEST_ANONYMOUS_DEV"] = "0"
            fail_closed_start = subprocess.Popen(
                [env["QI_TEST_SERVICE_BIN"]], cwd=ROOT, env=env
            )
            fail_closed_start.wait(timeout=5)
            check(
                "service_auth_without_persistence_startup_fail_closed",
                fail_closed_start.returncode == 0,
                f"startup process returned {fail_closed_start.returncode}",
            )
            check(
                "service_auth_without_persistence_startup_fail_closed",
                not health(service_port, "authenticated-fail-closed"),
                "service unexpectedly remained reachable after fail-closed startup",
            )
            print("PASS service_auth_without_persistence_startup_fail_closed")

            env["QI_TEST_ANONYMOUS_DEV"] = "1"
            service = start_service(env, "authenticated-fail-closed")

            status, auth_without_store = post(
                service_port, "/chat", {"提示": "must fail closed"}, token="token-a"
            )
            check(
                "service_auth_without_persistence_request_fail_closed",
                status == 503 and "持久会话" in str(auth_without_store.get("错误")),
                f"response: status={status}, body={auth_without_store!r}",
            )
            print("PASS service_auth_without_persistence_request_fail_closed")

            status, dev_legacy = post(
                service_port, "/chat", {"提示": "legacy dev"}, token=None
            )
            raw_handle = dev_legacy.get("会话号")
            check(
                "service_authenticated_guessed_handle_rejected",
                status == 200 and isinstance(raw_handle, int) and raw_handle > 0,
                f"anonymous legacy response: status={status}, body={dev_legacy!r}",
            )

            status, guessed = post(
                service_port,
                "/chat",
                {"提示": "guessed", "会话号": raw_handle},
                token="token-a",
            )
            check(
                "service_authenticated_guessed_handle_rejected",
                guessed == auth_without_store and status == 503,
                f"guessed handle response: status={status}, body={guessed!r}, expected={auth_without_store!r}",
            )
            print("PASS service_authenticated_guessed_handle_rejected")

            status, cross_tenant = post(
                service_port,
                "/chat",
                {"提示": "cross tenant", "会话号": raw_handle},
                token="token-b",
            )
            check(
                "service_authenticated_cross_tenant_handle_rejected",
                cross_tenant == auth_without_store and status == 503,
                f"cross-tenant response: status={status}, body={cross_tenant!r}, expected={auth_without_store!r}",
            )
            print("PASS service_authenticated_cross_tenant_handle_rejected")

            status, dev_quota = post(
                service_port,
                "/chat",
                {"提示": "quota again", "会话号": raw_handle},
                token=None,
            )
            check(
                "service_nonpersistent_anonymous_dev_quota_429",
                status == 429 and "配额" in str(dev_quota.get("错误")),
                f"response: status={status}, body={dev_quota!r}",
            )
            print("PASS service_nonpersistent_anonymous_dev_quota_429")
            return 0
        finally:
            if service is not None and service.poll() is None:
                stop(service)
            stop(fixture)


if __name__ == "__main__":
    raise SystemExit(main())
