from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.agent_loop_lib.core.messages import AssistantMessage, Message, ToolMessage, UserMessage

"""Deterministic parent -> child data handoff.

A statically composed child (`AgentTool`, see `agent_tool.py`) receives
ONLY the goal string the parent model writes — it cannot see the parent's
conversation. When the parent already gathered data with its OWN tools
(e.g. `jira_search_issues`) and then delegates a step that needs that
data (e.g. "build a PDF of these tickets") to a child whose tools can't
reach the same systems, a small model reliably writes a goal that
references "the results above" instead of embedding them, and the child
fails or fabricates a placeholder — see the incident this module fixes:
a `coding_agent` child asked to build a Jira ticket report with no way to
read the parent's Jira search results.

This module collects the parent's own recent tool results (from
`RouteContext.messages`, the parent's fresh post-response snapshot at the
point it calls a child) so `AgentTool.handle()` can hand them to the
child mechanically — as an inline digest appended to the goal (this
module) and as a full-fidelity file staged into the child's sandbox (see
`sandbox/input_staging.py`) — instead of relying on the model to paste
data into the goal text.
"""

__all__ = [
    "ParentToolResult",
    "collect_parent_tool_results",
    "extract_dependency_results",
    "format_parent_results_digest",
    "parent_results_as_json",
]

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDED_TOOL_NAMES: frozenset[str] = frozenset({
    "run_code", "install_packages", "read_sandbox_file",
})

_DEFAULT_PER_RESULT_CHAR_CAP = 8_000
_DEFAULT_TOTAL_CHAR_CAP = 48_000
_DEFAULT_JSON_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ParentToolResult:
    """One of the calling agent's own tool results from its CURRENT leg of
    work (see `collect_parent_tool_results`). `tool_name` comes from the
    `AssistantMessage.tool_calls` entry the `ToolMessage` answers — a
    `ToolMessage` itself only carries `tool_call_id`, never the tool name."""

    tool_name: str
    content: str


def collect_parent_tool_results(
    messages: list[Message],
    *,
    exclude_tool_names: frozenset[str] = DEFAULT_EXCLUDED_TOOL_NAMES,
) -> list[ParentToolResult]:
    """Every non-error `ToolMessage` in `messages` that answers a call made
    AFTER the last *real* (non-injected) `UserMessage` — the calling
    agent's work product for this request.

    Programmatically injected `UserMessage`s (recovery nudges from the
    completion gate, truncation recovery, loop-strategy phase transitions)
    are explicitly excluded from the boundary search: they are system
    directives, not new user requests, and treating them as boundaries
    would hide all tool results gathered before the nudge from a child
    agent that needs them (e.g. `coding_agent` losing the parent's Jira
    data after a completion-gate nudge forced a file-generation retry).

    `messages` is expected to be the parent's fresh, post-response
    snapshot — `RouteContext.messages` (`ToolScope.messages`), the exact
    snapshot every special-route handler already receives.
    """
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, UserMessage) and not msg.injected:
            last_user_idx = i
    relevant = messages[last_user_idx + 1:] if last_user_idx >= 0 else messages

    tool_name_by_call_id: dict[str, str] = {}
    for msg in relevant:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for call in msg.tool_calls:
                tool_name_by_call_id[call.id] = call.name

    results: list[ParentToolResult] = []
    for msg in relevant:
        if not isinstance(msg, ToolMessage) or msg.is_error or not msg.content:
            continue
        name = tool_name_by_call_id.get(msg.tool_call_id or "", "")
        if name in exclude_tool_names:
            continue
        results.append(ParentToolResult(tool_name=name or "unknown_tool", content=msg.content))
    return results


_DEPENDENCY_HEADER = "## Results from prerequisite tasks"
_DEPENDENCY_SECTION_RE = re.compile(
    r"### Result from prerequisite task '([^']+)'\n",
)


def extract_dependency_results(
    messages: list[Message],
) -> list[ParentToolResult]:
    """Extract dependency data that ``spawn_scheduler._augment_goal``
    injected into the first ``UserMessage`` of the conversation.

    When a sub-agent is spawned with ``depends_on``, the scheduler
    prepends the prerequisite agents' outputs to the goal in a known
    format::

        ## Results from prerequisite tasks

        ### Result from prerequisite task '{task_id}'
        {content}

        ## Your task
        {original goal}

    ``collect_parent_tool_results`` only examines ``ToolMessage`` objects,
    so this dependency data — which lives in a ``UserMessage`` — is
    invisible to it.  This function bridges that gap: it parses the
    known header/section format and returns each dependency section as a
    ``ParentToolResult`` so that ``AgentTool.handle()`` can stage it for
    grandchild agents (e.g. ``coding_agent``) via the existing digest +
    JSON file pipeline.
    """
    for msg in messages:
        if isinstance(msg, UserMessage):
            goal_text = msg.content if isinstance(msg.content, str) else str(msg.content)
            break
    else:
        return []

    header_pos = goal_text.find(_DEPENDENCY_HEADER)
    if header_pos < 0:
        return []

    dep_block = goal_text[header_pos:]
    task_marker = "## Your task"
    task_pos = dep_block.find(task_marker)
    if task_pos > 0:
        dep_block = dep_block[:task_pos]

    results: list[ParentToolResult] = []
    splits = list(_DEPENDENCY_SECTION_RE.finditer(dep_block))
    for i, match in enumerate(splits):
        task_id = match.group(1)
        content_start = match.end()
        content_end = splits[i + 1].start() if i + 1 < len(splits) else len(dep_block)
        content = dep_block[content_start:content_end].strip()
        if content:
            results.append(ParentToolResult(
                tool_name=f"dependency:{task_id}",
                content=content,
            ))
    return results


def format_parent_results_digest(
    results: list[ParentToolResult],
    *,
    per_result_chars: int = _DEFAULT_PER_RESULT_CHAR_CAP,
    total_chars: int = _DEFAULT_TOTAL_CHAR_CAP,
) -> str:
    """Render `results` into goal-prependable markdown, budgeted so one
    huge tool result can't blow out the child's entire context: each
    result is truncated to `per_result_chars`, and once `total_chars` has
    been spent, any remaining results are counted (not rendered) rather
    than silently dropped. This digest is a convenience preview for small
    payloads — callers needing the FULL, untruncated data should also
    stage it as a file (see `parent_results_as_json` + `input_staging.py`)."""
    if not results:
        return ""
    sections: list[str] = []
    spent = 0
    omitted = 0
    for result in results:
        text = result.content
        if len(text) > per_result_chars:
            text = text[:per_result_chars] + "\n... [truncated]"
        if spent + len(text) > total_chars:
            omitted += 1
            continue
        sections.append(f"### Result from `{result.tool_name}`\n{text}")
        spent += len(text)
    digest = "\n\n".join(sections)
    if omitted:
        digest += f"\n\n... [{omitted} more result(s) omitted for length — see the full data file noted below]"
    return digest


def _try_parse_json(text: str) -> Any:
    """Best-effort: most tool results are already JSON-encoded strings
    (see `Agent.step()`'s tool-result recording) — decoding them back
    gives the child a structured file instead of a JSON string wrapped in
    another layer of JSON. Falls back to the raw string for genuinely
    plain-text results."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _truncate_content(content: Any, available: int) -> Any:
    """Truncate a single result's content to fit within *available* bytes.

    For strings: straight character truncation with a marker.
    For structured data (dict/list from ``_try_parse_json``): serialize to
    a JSON string, truncate that, and return the truncated string (the
    child sees a string value instead of a structured one, but it's still
    parseable up to the cut point and the ``... [truncated]`` marker makes
    the truncation explicit).
    """
    if isinstance(content, str):
        if len(content.encode("utf-8")) <= available:
            return content
        safe = max(0, available - 30)
        return content[:safe] + "\n... [truncated]"
    serialized = json.dumps(content, default=str)
    if len(serialized.encode("utf-8")) <= available:
        return content
    safe = max(0, available - 30)
    return serialized[:safe] + "\n... [truncated]"


def parent_results_as_json(
    results: list[ParentToolResult], *, max_bytes: int = _DEFAULT_JSON_MAX_BYTES,
) -> bytes | None:
    """Budget-aware JSON encoding of ``results``.

    Returns ``None`` only when ``results`` is empty.  For non-empty input
    the file is ALWAYS produced — oversized payloads are truncated
    per-result rather than silently dropped.

    Output shape (consistent whether truncated or not)::

        {
          "_meta": {"truncated": false, "total_results": N,
                    "included_full": N, "included_truncated": 0,
                    "skipped": 0},
          "results": [{"tool": "...", "content": ...}, ...]
        }
    """
    if not results:
        return None

    entries = [
        {"tool": r.tool_name, "content": _try_parse_json(r.content)}
        for r in results
    ]
    meta: dict[str, Any] = {
        "truncated": False,
        "total_results": len(results),
        "included_full": len(results),
        "included_truncated": 0,
        "skipped": 0,
    }
    payload = {"_meta": meta, "results": entries}
    encoded = json.dumps(payload, default=str, indent=2).encode("utf-8")

    if len(encoded) <= max_bytes:
        return encoded

    # --- Budget-aware truncation ---
    # Reserve ~5% for the wrapper/meta overhead; distribute the rest
    # across results in chronological order (primary data-gathering calls
    # come first and are most important to preserve).
    overhead = 512
    budget = max_bytes - overhead
    per_result_budget = budget // len(results) if results else budget

    included_full = 0
    included_truncated = 0
    skipped = 0
    truncated_entries: list[dict[str, Any]] = []
    spent = 0

    for entry in entries:
        entry_full = json.dumps(entry, default=str).encode("utf-8")
        entry_size = len(entry_full)

        if spent + entry_size <= budget:
            truncated_entries.append(entry)
            spent += entry_size
            included_full += 1
            continue

        remaining = budget - spent
        # Need at least enough room for the tool name + a small content snippet
        min_useful = 100
        if remaining < min_useful:
            skipped += 1
            continue

        truncated_content = _truncate_content(entry["content"], remaining - 80)
        truncated_entries.append({"tool": entry["tool"], "content": truncated_content})
        spent += len(json.dumps(truncated_entries[-1], default=str).encode("utf-8"))
        included_truncated += 1

        # Remaining results are metadata-only
        skipped = len(entries) - included_full - included_truncated
        break

    meta.update({
        "truncated": True,
        "included_full": included_full,
        "included_truncated": included_truncated,
        "skipped": skipped,
    })

    if skipped > 0:
        skipped_tools = [
            e["tool"] for e in entries[included_full + included_truncated:]
        ]
        payload["_skipped"] = [{"tool": t} for t in skipped_tools]

    payload["results"] = truncated_entries
    result_bytes = json.dumps(payload, default=str, indent=2).encode("utf-8")

    logger.warning(
        "parent_results_as_json: payload %d bytes exceeds %d cap, truncated "
        "(%d full, %d truncated, %d skipped of %d total results)",
        len(encoded), max_bytes, included_full, included_truncated,
        skipped, len(results),
    )

    return result_bytes
