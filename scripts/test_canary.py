"""P9 CA-1..CA-4: the engine canary against a fake deployment that answers as Node's agent session routes do
(bearer auth, the admission body's required fields and bounds, 202 with the public run, the run GET, the
timeline). CA-5 (the shipped definition against Node's own schema) is a Node test.

Run: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "engines"))

import canary  # noqa: E402

TOKEN = "canary-token"
DEFINITION = json.loads(canary.DEFAULT_DEFINITION.read_text())


class FakeDeployment:
    """``statuses``: what successive run GETs answer; ``answer``/``end``: the timeline."""

    def __init__(self, statuses: list[str], answer: str = "PXH-CANARY-OK", end: dict[str, Any] | None = None) -> None:
        self.statuses = list(statuses)
        self.answer = answer
        self.end = end or {"status": "completed", "outcome": "answered"}
        self.admitted: list[dict[str, Any]] = []
        self.cancelled = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._reply(401, {"error": {"message": "unauthenticated"}})
                    return False
                return True

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path.endswith("/cancel"):
                    fake.cancelled += 1
                    self._reply(200, {"receipt": {}})
                    return
                # As Node's admissionBodySchema: these are required and bounded.
                valid = (
                    isinstance(body.get("idempotencyKey"), str)
                    and isinstance(body.get("query"), str)
                    and body["query"]
                    and 60_000 <= int(body.get("deadlineMs", 3_600_000)) <= 86_400_000
                )
                if not valid:
                    self._reply(400, {"error": {"message": "validation"}})
                    return
                fake.admitted.append(body)
                run = {"runId": "run-c1", "sessionId": "ses-c1", "status": "queued"}
                self._reply(202, {"admission": {"run": run}})

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                if self.path.endswith("/timeline"):
                    self._reply(200, {"answer": {"answer": fake.answer, "outcome": "answered"}, "end": fake.end})
                    return
                status = fake.statuses.pop(0) if len(fake.statuses) > 1 else fake.statuses[0]
                self._reply(200, {"run": {"runId": "run-c1", "status": status}})

            def log_message(self, *args: object) -> None:
                del args

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()


def run(fake: FakeDeployment, *, token: str = TOKEN, definition: dict[str, Any] | None = None) -> canary.Report:
    return canary.run_canary(
        canary.Deployment(fake.url, token), "agent-c", definition or {**DEFINITION, "pollIntervalS": 0}
    )


class CanaryTest(unittest.TestCase):
    def test_ca1_a_healthy_deployment_passes_with_timings(self) -> None:
        fake = FakeDeployment(["queued", "running", "completed"])
        try:
            report = run(fake)
        finally:
            fake.close()
        self.assertEqual((report.status, report.reason, report.runId), ("pass", None, "run-c1"))
        self.assertTrue({"admitMs", "firstProgressMs", "terminalMs", "totalMs"} <= report.timings.keys())
        self.assertEqual(fake.admitted[0]["query"], DEFINITION["request"]["query"])

    def test_ca2_a_failed_run_fails_with_its_error_type(self) -> None:
        fake = FakeDeployment(["failed"], end={"status": "failed", "errorType": "engine_failed"})
        try:
            report = run(fake)
        finally:
            fake.close()
        self.assertEqual((report.status, report.reason), ("fail", "engine_failed"))

    def test_ca2_a_run_past_the_timeout_is_cancelled(self) -> None:
        fake = FakeDeployment(["running"])
        try:
            report = run(fake, definition={**DEFINITION, "pollIntervalS": 0, "timeoutS": 0.2})
        finally:
            fake.close()
        self.assertEqual((report.status, report.reason, report.runStatus), ("fail", "timeout", "running"))
        self.assertEqual(fake.cancelled, 1)

    def test_ca3_an_answer_without_the_marker_fails(self) -> None:
        fake = FakeDeployment(["completed"], answer="I cannot help with that")
        try:
            report = run(fake)
        finally:
            fake.close()
        self.assertEqual((report.status, report.reason), ("fail", "answer_mismatch"))

    def test_ca4_a_refused_token_and_an_unreachable_deployment_exit_2(self) -> None:
        fake = FakeDeployment(["completed"])
        script = str(Path(canary.__file__))
        try:
            refused = subprocess.run(
                [sys.executable, script, "--base-url", fake.url, "--agent-key", "a", "--token-env", "CANARY_TEST_TOKEN"],
                capture_output=True, text=True, env={"CANARY_TEST_TOKEN": "wrong", "PATH": "/usr/bin:/bin"}, check=False,
            )
            passed = subprocess.run(
                [sys.executable, script, "--base-url", fake.url, "--agent-key", "a", "--token-env", "CANARY_TEST_TOKEN"],
                capture_output=True, text=True, env={"CANARY_TEST_TOKEN": TOKEN, "PATH": "/usr/bin:/bin"}, check=False,
            )
        finally:
            fake.close()
        down = subprocess.run(
            [sys.executable, script, "--base-url", "http://127.0.0.1:1", "--agent-key", "a", "--token-env", "CANARY_TEST_TOKEN"],
            capture_output=True, text=True, env={"CANARY_TEST_TOKEN": TOKEN, "PATH": "/usr/bin:/bin"}, check=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stdout)["reason"], "unauthorized")
        self.assertEqual(down.returncode, 2)
        self.assertEqual(json.loads(down.stdout)["reason"], "unreachable")
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)


if __name__ == "__main__":
    unittest.main()
