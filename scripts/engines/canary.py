#!/usr/bin/env python3
"""The engine canary (P9): one synthetic engine run on a live deployment, through its public routes.

    python3 scripts/engines/canary.py --base-url https://pipeshub.example.com --agent-key <key> \
        [--definition deployment/canary/engine-canary.json] [--token-env PIPESHUB_CANARY_TOKEN] [--report out.json]

Admits the definition's request on ``POST /api/v1/agents/{agentKey}/runs`` with an API token (scope
agent:execute and agent:read), polls ``GET …/conversations/{sessionId}/runs/{runId}`` until the run ends, reads
its answer from ``…/runs/{runId}/timeline`` and checks it holds the definition's marker. Prints one JSON report
(and writes it to ``--report``) with timings in milliseconds; exits 0 pass, 1 fail (the run failed, timed out,
or answered without the marker), 2 when the deployment could not be reached or refused the token. A run that
times out is cancelled. Standard library only, so cron and CI run it anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEFINITION = ROOT / "deployment" / "canary" / "engine-canary.json"
TERMINAL = {"completed", "failed", "cancelled"}


class Unreachable(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass
class Report:
    status: str = "fail"
    reason: str | None = None
    runId: str | None = None
    sessionId: str | None = None
    runStatus: str | None = None
    timings: dict[str, int] = field(default_factory=dict)


class Deployment:
    def __init__(self, base_url: str, token: str, timeout_s: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise Unreachable("unauthorized", f"{method} {path}: HTTP {exc.code}") from exc
            raise Unreachable("http_error", f"{method} {path}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise Unreachable("unreachable", f"{method} {path}: {type(exc).__name__}") from exc
        return json.loads(raw) if raw else {}


def run_canary(
    deployment: Deployment,
    agent_key: str,
    definition: dict[str, Any],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    report = Report()
    started = clock()

    def ms() -> int:
        return int((clock() - started) * 1000)

    body = {**definition["request"], "idempotencyKey": f"canary-{uuid.uuid4().hex}"}
    admitted = deployment.call("POST", f"/api/v1/agents/{agent_key}/runs", body)
    run = (admitted.get("admission") or {}).get("run") or {}
    if not run.get("runId"):
        report.reason = "not_admitted"
        report.timings["admitMs"] = ms()
        return report
    report.runId, report.sessionId = run["runId"], run["sessionId"]
    report.timings["admitMs"] = ms()
    base = f"/api/v1/agents/{agent_key}/conversations/{report.sessionId}/runs/{report.runId}"
    deadline = started + float(definition.get("timeoutS", 600))
    status = run.get("status")
    while status not in TERMINAL:
        if clock() > deadline:
            report.reason = "timeout"
            report.runStatus = status
            deployment.call("POST", f"{base}/cancel", {})
            report.timings["totalMs"] = ms()
            return report
        sleep(float(definition.get("pollIntervalS", 2)))
        status = (deployment.call("GET", base).get("run") or {}).get("status")
        if status not in (None, "queued") and "firstProgressMs" not in report.timings:
            report.timings["firstProgressMs"] = ms()
    report.runStatus = status
    report.timings["terminalMs"] = ms()
    timeline = deployment.call("GET", f"{base}/timeline")
    answer = str(((timeline.get("answer") or {}).get("answer")) or "")
    report.timings["totalMs"] = ms()
    if status != "completed":
        end = timeline.get("end") or {}
        report.reason = str(end.get("errorType") or end.get("reason") or f"run_{status}")
    elif str(definition["expect"]) not in answer:
        report.reason = "answer_mismatch"
    else:
        report.status, report.reason = "pass", None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One synthetic engine run on a deployment (P9 canary).")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--agent-key", required=True)
    parser.add_argument("--definition", default=str(DEFAULT_DEFINITION))
    parser.add_argument("--token-env", default="PIPESHUB_CANARY_TOKEN")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    definition = json.loads(Path(args.definition).read_text())
    token = os.environ.get(args.token_env, "")
    try:
        if not token:
            raise Unreachable("unauthorized", f"{args.token_env} is empty")
        report = run_canary(Deployment(args.base_url, token), args.agent_key, definition)
        code = 0 if report.status == "pass" else 1
    except Unreachable as exc:
        report = Report(status="error", reason=exc.reason)
        code = 2
    text = json.dumps(asdict(report))
    print(text)
    if args.report:
        Path(args.report).write_text(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
