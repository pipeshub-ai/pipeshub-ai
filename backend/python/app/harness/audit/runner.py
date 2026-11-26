"""The grant auditor as a process (P9): follows the gateways' request logs, checks every executed call and the
effect log of every run they name against the run's pinned grant (Node), alerts once per violation.

    python -m app.harness.audit --log /var/log/worker.log --log /var/log/model-gateway.log --once
    python -m app.harness.audit --log ... --follow --interval 30 --state /var/lib/pipeshub/audit.json

``--once`` exits 0 when clean, 1 on a violation, 2 when Node cannot answer (cron, CI, the nightly).
``--follow`` runs until stopped; a violation is an ``audit.violation`` error line (the alert hook).
The Node address is ``AGENT_HARNESS_STATE_URL`` (as the worker's); the worker credential comes from the
deployment's configuration, or with ``--credential-from-env`` from ``AGENT_HARNESS_AUDIT_CREDENTIAL``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.harness.audit.node_source import AuditSourceUnavailable, NodeRunEvidenceSource
from app.harness.inline import node_state_url
from harness_core.audit import GrantAuditor, MalformedLine, parse_call
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from harness_core.audit.ports import RunEvidenceSource
    from harness_core.audit.types import ExecutedCall

# PipesHub's platform tools are granted by other spec fields than grants.tools (the assembler's rules).
PLATFORM_TOOL_PREFIX = "pipeshub@"
AUDIT_CREDENTIAL_ENV = "AGENT_HARNESS_AUDIT_CREDENTIAL"
_LOG = HarnessLog.named("app.harness.audit")


def outside_grant(tool_id: str) -> bool:
    return tool_id.startswith(PLATFORM_TOOL_PREFIX)


@dataclass
class LogCursor:
    """Where each followed file was read to, with the file's identity: a file replaced (rotated) or truncated
    is read again from its start."""

    offsets: dict[str, tuple[int, int]] = field(default_factory=dict[str, tuple[int, int]])

    @classmethod
    def load(cls, path: Path | None) -> LogCursor:
        if path is None or not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls({str(k): (int(v[0]), int(v[1])) for k, v in raw.items()})

    def save(self, path: Path | None) -> None:
        if path is not None:
            aside = path.with_suffix(".tmp")
            aside.write_text(json.dumps({k: list(v) for k, v in self.offsets.items()}))
            aside.replace(path)

    def read_new(self, files: list[Path]) -> tuple[list[ExecutedCall], int]:
        calls: list[ExecutedCall] = []
        malformed = 0
        for file in files:
            if not file.exists():
                continue
            key, stat = str(file), file.stat()
            inode, start = self.offsets.get(key, (stat.st_ino, 0))
            if inode != stat.st_ino or stat.st_size < start:
                start = 0
            with file.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
            complete = data.rfind(b"\n") + 1
            self.offsets[key] = (stat.st_ino, start + complete)
            for line in data[:complete].decode("utf-8", "replace").splitlines():
                try:
                    found = parse_call(line)
                except MalformedLine:
                    malformed += 1
                    continue
                if found is not None:
                    calls.append(found)
        return calls, malformed


async def audit_once(auditor: GrantAuditor, cursor: LogCursor, files: list[Path]) -> tuple[int, int, int]:
    """One pass: (calls checked, new violations, malformed lines)."""
    calls, malformed = cursor.read_new(files)
    report = await auditor.audit(calls, malformed=malformed)
    return report.calls, len(report.violations), malformed


class _EnvCredential:
    async def worker_credential(self) -> str:
        value = os.environ.get(AUDIT_CREDENTIAL_ENV, "")
        if not value:
            raise AuditSourceUnavailable(f"{AUDIT_CREDENTIAL_ENV} is empty")
        return value


def _source(node_url: str, *, credential_from_env: bool) -> NodeRunEvidenceSource:
    if credential_from_env:
        return NodeRunEvidenceSource(base_url=node_url, credentials=_EnvCredential())
    from app.harness.assembly.services import HarnessServices
    from app.harness.journal_node.credentials import ConfiguredWorkerCredential

    return NodeRunEvidenceSource(
        base_url=node_url, credentials=ConfiguredWorkerCredential(HarnessServices().config_service())
    )


async def run(args: argparse.Namespace, source: RunEvidenceSource | None = None) -> int:
    files = [Path(p) for p in args.log]
    state = Path(args.state) if args.state else None
    cursor = LogCursor.load(state)
    owned = source is None
    evidence = source or _source(args.node_url, credential_from_env=args.credential_from_env)
    auditor = GrantAuditor(evidence, outside_grant=outside_grant, log=_LOG)
    found = 0
    try:
        while True:
            try:
                calls, violations, malformed = await audit_once(auditor, cursor, files)
            except AuditSourceUnavailable as exc:
                _LOG.warning("audit.unavailable", reason=AuditSourceUnavailable.reason_code, errorType=str(exc))
                if not args.follow:
                    return 2
                await asyncio.sleep(args.interval)
                continue
            found += violations
            cursor.save(state)
            if not args.follow:
                sys.stdout.write(json.dumps({"calls": calls, "violations": found, "malformed": malformed}) + "\n")
                return 1 if found else 0
            await asyncio.sleep(args.interval)
    finally:
        if owned and isinstance(evidence, NodeRunEvidenceSource):
            await evidence.aclose()


def parser() -> argparse.ArgumentParser:
    parsed = argparse.ArgumentParser(prog="python -m app.harness.audit", description=(__doc__ or "").split("\n\n")[0])
    parsed.add_argument("--log", action="append", required=True, help="a gateway request log to follow (repeat)")
    mode = parsed.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one pass; exit 0 clean, 1 violation, 2 Node unavailable")
    mode.add_argument("--follow", action="store_true", help="keep following the logs")
    parsed.add_argument("--interval", type=float, default=30.0, help="seconds between passes with --follow")
    parsed.add_argument("--state", help="file keeping how far each log was read")
    parsed.add_argument("--node-url", default=node_state_url())
    parsed.add_argument(
        "--credential-from-env", action="store_true", help=f"read the worker credential from {AUDIT_CREDENTIAL_ENV}"
    )
    return parsed


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
