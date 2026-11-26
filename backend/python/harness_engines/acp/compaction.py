"""What the ACP driver learns of an agent's context compaction where ACP itself says nothing (MG-10, C3.67).

opencode over ACP compacts a session that overflowed and retries the turn by itself, but stable ACP has no compaction
update, so the chat would get no ``ContextCompacted``. Its loopback API (the one ``mcp.status`` already asks) lists the
session's messages; a compaction leaves an assistant message marked ``summary`` whose tokens are the session as it was
(input) and the summary it starts from (output), as the native opencode driver reads them
(``harness_engines/opencode/session.py``).

Qwen Code 0.26.5 does the same and has no such API; it records each compression in the session's own record, a JSON
lines file under its runtime folder, as a ``system`` line of subtype ``chat_compression`` (WP-102). The driver reads
those lines inside the sandbox with the agent's own runtime, which prints only each one's id, outcome and token
counts (the line also holds the compressed history).
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpcore

from harness_core.engines.binary import run_check
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.sandbox.errors import SandboxError
from harness_engines.acp.profiles import WORKDIR
from harness_engines.opencode_api.http import OpencodeHttpError

if TYPE_CHECKING:
    from harness_core.sandbox import SandboxHandle
    from harness_engines.opencode_api.http import OpencodeHttp

_FAILURES = (OpencodeHttpError, OSError, ValueError, httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError)


@dataclass(frozen=True, slots=True)
class AgentCompaction:
    message_id: str
    before_tokens: int | None
    after_tokens: int | None


async def opencode_summaries(http: OpencodeHttp, session_id: str) -> tuple[AgentCompaction, ...] | None:
    """The session's compaction summaries, oldest first; ``None`` when opencode could not be asked (the turn goes on
    without a notice rather than fail on a bookkeeping read)."""
    try:
        messages = as_list(await http.request("GET", f"/session/{session_id}/message"))
    except _FAILURES:
        return None
    found: list[AgentCompaction] = []
    for message in messages:
        info = as_object(as_object(message).get("info"))
        if info.get("summary") is True and as_str(info.get("role")) == "assistant":
            tokens = as_object(info.get("tokens"))
            found.append(
                AgentCompaction(as_str(info.get("id")), as_int(tokens.get("input")) or None, as_int(tokens.get("output")) or None)
            )
    return tuple(found)


# Qwen Code's ``CompressionStatus.COMPRESSED``; every other outcome (no-op, failed) left the session as it was.
_QWEN_COMPRESSED = 1
# A session id names a file in Qwen's record folder only as letters, digits and dashes (it is a UUID).
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
# Node: argv the record file. One JSON line per ``chat_compression`` record; nothing for a missing file.
QWEN_RECORDS_SCRIPT = r"""
const fs = require("fs");
let text = "";
try { text = fs.readFileSync(process.argv[1], "utf8"); } catch { process.exit(0); }
for (const line of text.split("\n")) {
  if (!line.includes('"chat_compression"')) continue;
  try {
    const r = JSON.parse(line);
    if (r.type !== "system" || r.subtype !== "chat_compression") continue;
    const info = (r.systemPayload || {}).info || {};
    process.stdout.write(JSON.stringify({id: r.uuid, status: info.compressionStatus,
      before: info.originalTokenCount, after: info.newTokenCount}) + "\n");
  } catch {}
}
"""


def qwen_records_path(state_root: str, session_id: str) -> str | None:
    """The session's record under Qwen's runtime folder (it names a project's folder by its path, ``/`` as ``-``);
    ``None`` for a session id that is not a file name."""
    if not _SESSION_ID.match(session_id):
        return None
    return posixpath.join(state_root, "projects", WORKDIR.replace("/", "-"), "chats", f"{session_id}.jsonl")


def parse_qwen_records(printed: str) -> tuple[AgentCompaction, ...]:
    found: list[AgentCompaction] = []
    for line in printed.splitlines():
        try:
            record = as_object(json.loads(line))
        except ValueError:
            continue
        record_id = as_str(record.get("id"))
        if record_id and record.get("status") == _QWEN_COMPRESSED:
            found.append(
                AgentCompaction(record_id, as_int(record.get("before")) or None, as_int(record.get("after")) or None)
            )
    return tuple(found)


async def qwen_summaries(sandbox: SandboxHandle, *, runtime: str, path: str) -> tuple[AgentCompaction, ...] | None:
    """The compressions recorded in the session's record, oldest first; ``None`` when it could not be read."""
    try:
        code, printed = await run_check(sandbox, [runtime, "-e", QWEN_RECORDS_SCRIPT, path])
    except (SandboxError, TimeoutError):
        return None
    return parse_qwen_records(printed) if code == 0 else None


__all__ = [
    "QWEN_RECORDS_SCRIPT",
    "AgentCompaction",
    "opencode_summaries",
    "parse_qwen_records",
    "qwen_records_path",
    "qwen_summaries",
]
