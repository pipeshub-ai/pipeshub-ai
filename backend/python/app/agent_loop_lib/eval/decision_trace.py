from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry

"""Decision traces (Phase 5): one record per tool-call-shaped decision in
a run — what was called, the model-stated reasoning behind it (when the
tool's schema asks for one — spawn_agent/handoff/replan all require a
reasoning/reason argument), whether a hook vetoed it, and which sources
(if any) the call produced. Built post-hoc from timeline entries, same
raw material as context/graph_builder.py and eval/trajectory.py, read here
for "why did this happen" instead of "what happened" or "what got
touched". Powers citation review and rubric grading input.
"""

# Timeline event_types that represent a decision to call something.
_DECISION_EVENT_TYPES = {"tool_call", "spawn_agent", "handoff", "replan"}
# Keys tool schemas use for model-stated justification (spawn_agent's
# `reasoning`, handoff's/replan's `reason`) — checked in this order.
_REASONING_KEYS = ("reasoning", "reason")


class DecisionTraceEntry(BaseModel):
    sequence_id: int
    run_id: str
    agent_id: str
    timestamp: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    reasoning: str | None = None
    verdict: str = "allowed"  # "allowed" | "blocked"
    block_reason: str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


def _extract_reasoning(args: Any) -> str | None:
    if not isinstance(args, dict):
        return None
    for key in _REASONING_KEYS:
        value = args.get(key)
        if value:
            return value
    return None


def build_decision_trace(entries: list[TimelineEntry]) -> list[DecisionTraceEntry]:
    """One `DecisionTraceEntry` per tool-call/spawn_agent/handoff/replan
    entry (in "allowed" order) plus one per `tool_blocked` entry
    ("blocked"); a later `tool_result_sources` entry for the same run+tool
    is folded onto the most recent still-open trace entry for that pair,
    linking the citation to the decision that produced it rather than
    becoming its own trace entry."""
    traces: list[DecisionTraceEntry] = []
    pending_by_tool: dict[tuple[str, str], DecisionTraceEntry] = {}

    for entry in sorted(entries, key=lambda e: e.sequence_id):
        if entry.event_type in _DECISION_EVENT_TYPES:
            args = entry.detail.get("args", entry.detail)
            tool_name = entry.detail.get("tool") or entry.event_type
            trace = DecisionTraceEntry(
                sequence_id=entry.sequence_id, run_id=entry.run_id, agent_id=entry.agent_id,
                timestamp=entry.timestamp, tool=tool_name,
                args=args if isinstance(args, dict) else {},
                reasoning=_extract_reasoning(args),
                verdict="allowed", summary=entry.summary,
            )
            traces.append(trace)
            pending_by_tool[(entry.run_id, tool_name)] = trace

        elif entry.event_type == "tool_blocked":
            tool_name = entry.detail.get("tool")
            traces.append(DecisionTraceEntry(
                sequence_id=entry.sequence_id, run_id=entry.run_id, agent_id=entry.agent_id,
                timestamp=entry.timestamp, tool=tool_name,
                args=entry.detail.get("args") or {},
                verdict="blocked", block_reason=entry.detail.get("reason"), summary=entry.summary,
            ))

        elif entry.event_type == "tool_result_sources":
            tool_name = entry.detail.get("tool")
            match = pending_by_tool.get((entry.run_id, tool_name))
            if match is not None:
                match.sources = entry.detail.get("sources", [])

    return traces
