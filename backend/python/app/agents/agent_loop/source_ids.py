"""Which records a turn drew on, stored with its answer.

Node keeps the list next to the answer so a shared-chat viewer is shown another
person's answer only when they can read every record behind it, cited or not.
An empty list therefore means "no sources"; anything whose records cannot be
named (connector, web, MCP or sub-agent output) adds `OPAQUE_SOURCE`, which no
viewer can read.
"""

from __future__ import annotations

from typing import Any

from app.agents.agent_loop.hooks.ask_user_question import _ASK_USER_QUESTION_TOOL_NAMES

OPAQUE_SOURCE = "*"
MAX_SOURCE_RECORD_IDS = 1000
# Tools whose output is made of records the run also tracked in tool_state.
_RECORD_TOOL_PREFIXES = ("retrieval.", "retrieval_", "knowledgegraph.", "knowledgehub.")
# Record fetches, and tools that only carry the answer or a question back to the person.
_RECORD_TOOLS = frozenset({"dynamic_fetch_full_record", "fetch_record", "final_answer", *_ASK_USER_QUESTION_TOOL_NAMES})


def _record_id(record: Any) -> str | None:  # noqa: ANN401
    if not isinstance(record, dict):
        return None
    meta = record.get("metadata")
    rid = record.get("id") or record.get("_key") or (meta.get("recordId") if isinstance(meta, dict) else None)
    return str(rid) if rid else None


def source_record_ids(tool_state: dict[str, Any]) -> list[str]:
    """Record ids the turn retrieved or looked at, prefetch and tool results included."""
    ids: set[str] = set()
    virtual = tool_state.get("virtual_record_id_to_result") or {}
    for record in virtual.values():
        if rid := _record_id(record):
            ids.add(rid)
    for record in tool_state.get("tool_records") or []:
        if rid := _record_id(record):
            ids.add(rid)
    for entry in tool_state.get("final_results") or []:
        if not isinstance(entry, dict):
            continue
        if rid := _record_id(entry) or _record_id(virtual.get(entry.get("virtual_record_id") or "")):
            ids.add(rid)
    known = tool_state.get("known_record_ids")
    if isinstance(known, set):
        ids.update(str(r) for r in known if r)

    opaque = bool(tool_state.get("web_records"))
    for result in tool_state.get("all_tool_results") or []:
        name = str(result.get("tool_name") or "").replace("__", ".") if isinstance(result, dict) else ""
        if name and name not in _RECORD_TOOLS and not name.startswith(_RECORD_TOOL_PREFIXES):
            opaque = True
    ordered = sorted(ids)
    if len(ordered) > MAX_SOURCE_RECORD_IDS:
        ordered, opaque = ordered[:MAX_SOURCE_RECORD_IDS], True
    return [*ordered, OPAQUE_SOURCE] if opaque else ordered
