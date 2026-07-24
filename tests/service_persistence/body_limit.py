#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from run import (
    check,
    compile_service_fixture,
    free_port,
    health,
    oversized_chunked_stream,
    oversized_content_length_headers,
    post,
    start_service,
    stop,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="qi-service-body-limit-") as temporary:
        temporary_path = Path(temporary)
        service_port = free_port()
        service_binary = temporary_path / "service-fixture"
        compile_service_fixture(temporary_path, service_binary)
        env = os.environ.copy()
        env["QI_TEST_URL"] = "http://127.0.0.1:43591/v1/chat/completions"
        env["QI_TEST_SESSION_DB"] = str(temporary_path / "sessions.db")
        env["QI_TEST_SERVICE_PORT"] = str(service_port)
        env["QI_TEST_SERVICE_BIN"] = str(service_binary)
        env["QI_TEST_AUDIT_LOG"] = str(temporary_path / "audit.jsonl")

        service = start_service(env)
        try:
            status, _ = oversized_content_length_headers(service_port)
            check(
                "service_oversized_content_length_rejected_before_body_and_auth",
                status == 413,
                f"response status was {status}, expected 413",
            )
            check(
                "service_oversized_content_length_rejected_before_body_and_auth",
                health(service_port),
                "server did not accept a new health connection after rejection",
            )
            print("PASS service_oversized_content_length_rejected_before_body_and_auth")

            status, _ = oversized_chunked_stream(service_port)
            check(
                "service_oversized_chunked_stream_rejected_before_full_body",
                status == 413,
                f"response status was {status}, expected 413",
            )
            check(
                "service_oversized_chunked_stream_rejected_before_full_body",
                health(service_port),
                "server did not accept a new health connection after rejection",
            )
            print("PASS service_oversized_chunked_stream_rejected_before_full_body")

            status, body = post(service_port, "/chat", {"提示": "x" * 33})
            check(
                "service_application_prompt_limit_preserved",
                status == 413 and body.get("错误") == "提示过大",
                f"response: status={status}, body={body!r}",
            )
            print("PASS service_application_prompt_limit_preserved")
            return 0
        finally:
            stop(service)


if __name__ == "__main__":
    raise SystemExit(main())
