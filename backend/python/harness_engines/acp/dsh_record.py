"""What the ACP driver reads of DeepSeek Harness's own session log at each turn's end (``sessionRecord: dsh``, WP-105).

DeepSeek Harness's ACP server (``dsh --profile acp``, 0.2.2-rc.1) answers ``session/prompt`` with a stop reason only
and sends ``usage_update`` with the context in use, never a call's tokens; it compacts an overflowing session by itself
and says nothing of it over ACP. Its session log says both: a JSON lines file of events, zstd frames appended as it
goes (``<sessions root>/<workspace key>/<session id>/session.v4.jsonl.zstd``, a zstd frame per write), where each committed model reply is an
``assistant/message`` event with the provider's ``usage``, and each compaction a ``compaction/summary`` (the tokens
it condensed, the summary call's usage) closed by a ``compaction/end`` without an ``error``.

The driver reads the log inside the sandbox with the agent's own Node (which decompresses zstd), and the script prints
only ids and counts: never the messages, the summary or any tool output. The turn's usage is the sum of the calls not
seen before (CF-13: never more than the gateway billed); a new compaction is one ``ContextCompacted`` (MG-10).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.binary import run_check
from harness_core.engines.jsonview import as_int, as_object, as_str
from harness_core.sandbox.errors import SandboxError
from harness_engines.acp.compaction import AgentCompaction

if TYPE_CHECKING:
    from harness_core.sandbox import SandboxHandle

# A session id is a folder name in the log's tree only as letters, digits and dashes (it is a UUID).
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
# Node: argv the sessions root, the session id and the last event sequence number already read. One JSON line per
# later model call with usage (``k: call``) and per compaction committed later (``k: compaction``), then the log's
# last sequence number (``k: seq``); nothing for a session with no log yet. Reading on from the last turn's end keeps
# the output a turn's size however long the session grows.
DSH_RECORD_SCRIPT = r"""
const fs = require("fs"), path = require("path"), zlib = require("zlib");
const [root, id, afterArg] = process.argv.slice(1);
const after = Number.isInteger(Number(afterArg)) ? Number(afterArg) : -1;
let last = after;
const out = (o) => process.stdout.write(JSON.stringify(o) + "\n");
const n = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);
// dsh appends a zstd frame per write; Node decompresses one frame per call, and says how much input it used.
const unzstd = (buf) => {
  const parts = [];
  for (let at = 0; at < buf.length;) {
    const r = zlib.zstdDecompressSync(buf.subarray(at), {info: true});
    parts.push(r.buffer);
    if (!r.engine.bytesWritten) break;
    at += r.engine.bytesWritten;
  }
  return Buffer.concat(parts);
};
let keys = [];
try { keys = fs.readdirSync(root); } catch { process.exit(0); }
for (const key of keys) {
  const folder = path.join(root, key, id);
  let names = [];
  try { names = fs.readdirSync(folder); } catch { continue; }
  for (const name of names.filter((f) => /^session\.v\d+\.jsonl(\.zstd)?$/.test(f)).sort()) {
    let raw;
    try {
      raw = fs.readFileSync(path.join(folder, name));
      if (name.endsWith(".zstd")) raw = unzstd(raw);
    } catch { continue; }
    const ended = new Set(), summaries = [];
    for (const line of raw.toString("utf8").split("\n")) {
      if (!line) continue;
      let r;
      try { r = JSON.parse(line); } catch { continue; }
      const d = (r && r.data) || {};
      const seq = Number.isInteger(r.seq) ? r.seq : -1;
      if (seq > last) last = seq;
      if (r.type === "compaction/summary" && typeof d.compactionId === "string") summaries.push(d);
      if (seq <= after) continue;
      if (r.type === "assistant/message" && d.usage && d.message && typeof d.message.id === "string") {
        out({k: "call", id: d.message.id, in: n(d.usage.inputTokens), out: n(d.usage.outputTokens),
          cr: n(d.usage.cacheReadTokens)});
      } else if (r.type === "compaction/summary" && typeof d.compactionId === "string") {
        if (d.usage) out({k: "call", id: "compaction:" + d.compactionId, in: n(d.usage.inputTokens),
          out: n(d.usage.outputTokens), cr: n(d.usage.cacheReadTokens)});
      } else if (r.type === "compaction/end" && !d.error && typeof d.compactionId === "string") {
        ended.add(d.compactionId);
      }
    }
    for (const d of summaries) {
      if (ended.has(d.compactionId)) out({k: "compaction", id: d.compactionId, before: n(d.shadowedTokenCount),
        after: n((d.usage || {}).outputTokens)});
    }
  }
}
out({k: "seq", seq: last});
"""


@dataclass(frozen=True, slots=True)
class CallUsage:
    """One model call's usage as the agent's log recorded it (the provider's, through the model gateway)."""

    call_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """What the log says, oldest first: each model call's usage and each committed compaction."""

    calls: tuple[CallUsage, ...] = ()
    compactions: tuple[AgentCompaction, ...] = ()
    # The log's last event sequence number read; the next read starts after it.
    last_seq: int = -1


def parse_dsh_record(printed: str) -> AgentRecord:
    calls: list[CallUsage] = []
    compactions: list[AgentCompaction] = []
    last_seq = -1
    for line in printed.splitlines():
        try:
            entry = as_object(json.loads(line))
        except ValueError:
            continue
        if as_str(entry.get("k")) == "seq":
            seq = entry.get("seq")
            last_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else last_seq
            continue
        entry_id = as_str(entry.get("id"))
        if not entry_id:
            continue
        kind = as_str(entry.get("k"))
        if kind == "call":
            calls.append(CallUsage(entry_id, as_int(entry.get("in")), as_int(entry.get("out")), as_int(entry.get("cr"))))
        elif kind == "compaction":
            compactions.append(
                AgentCompaction(entry_id, as_int(entry.get("before")) or None, as_int(entry.get("after")) or None)
            )
    return AgentRecord(tuple(calls), tuple(compactions), last_seq)


async def dsh_record(
    sandbox: SandboxHandle, *, runtime: str, root: str, session_id: str, after_seq: int = -1
) -> AgentRecord | None:
    """The session's log as ``parse_dsh_record`` reads it; ``None`` when it could not be read (the turn goes on
    without its usage or notices rather than fail on a bookkeeping read)."""
    if not _SESSION_ID.match(session_id):
        return None
    try:
        code, printed = await run_check(sandbox, [runtime, "-e", DSH_RECORD_SCRIPT, root, session_id, str(after_seq)])
    except (SandboxError, TimeoutError):
        return None
    return parse_dsh_record(printed) if code == 0 else None


__all__ = ["DSH_RECORD_SCRIPT", "AgentRecord", "CallUsage", "dsh_record", "parse_dsh_record"]
