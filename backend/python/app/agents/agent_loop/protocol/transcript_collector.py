"""`TranscriptCollector`: assembles the ordered, typed `MessagePart`
transcript of one request's agent activity — text, reasoning, tool calls,
and sub-agent delegation — for persistence and reload rendering (the
"Parts-Based Agent Message Transcript" plan).

Wired in as an `EventEmitter` sibling of `AGUIEventEmitter` (see
`factory.py::_build_event_emitter`), composed via `CompositeEmitter` so
BOTH see every `AgentEvent` the shared `AgentRuntime.event_emitter` fans
out — root AND every child agent's, distinguished only by
`event.run_context.parent_run_id` (same identity `AGUIEventEmitter`'s
docstring explains). This is deliberately NOT built by replaying
`agent.stream(goal)`'s own generator (what `TerminalAnswerStreamer`
consumes in `stream_bridge.py`): that generator only ever carries the
ROOT run's events (see `agent/streaming.py::stream()`), so a collector
built that way would silently miss every sub-agent's activity.

Matches AG-UI ALIAS event types for the same occurrences `AGUIEventEmitter`
reacts to (`RUN_STARTED`/`RUN_FINISHED`/`TOOL_CALL_START`/`TOOL_CALL_END`)
and the same DIRECT types for turn-text/reasoning (`TEXT_MESSAGE_*`,
`REASONING_MESSAGE_*`) — both legacy and AG-UI-aliased events fire for the
same occurrence (`Agent.emit()`'s `_AG_UI_ALIASES` table), so matching only
the alias side (exactly like `AGUIEventEmitter` does) avoids collecting
every tool call / run lifecycle event twice.

Full external tool results are never stored here beyond the ~200-char
preview `agent_loop_lib/agent/tool_loop.py::execute_tool_call` already
truncates `TOOL_RESULT`/`TOOL_BLOCKED` payloads to before emission — this
collector only ever sees that already-bounded preview, never the raw
tool output, so "don't store full external tool results" holds by
construction, not by an extra truncation step here.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from app.agent_loop_lib.events.base import EventEmitter, EventType, ToolCallStatus

# Matches [text](url) where text is NOT purely digits (already-numbered
# citations are left alone) and the target is a citation reference:
#   - tiny ref:   [source](ref5)  or  [source](https://ref5.xyz)
#   - record URL: [source](https://host/record/{uuid}/preview#blockIndex=N)
# The tiny-ref path is the primary one (TINY_URL_THRESHOLD=0 aliases all URLs).
_NON_NUMBERED_TINY_REF_LINK_RE = re.compile(
    r'\[(?!\d+\])([^\]]*)\]\(((?:https?://)?ref(\d+)(?:\.xyz/?)?[^)]*)\)'
)
_NON_NUMBERED_RECORD_LINK_RE = re.compile(
    r'\[(?!\d+\])([^\]]*)\]\(([^)]*\/record\/([a-f0-9-]+)[^)]*)\)'
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import RunContext
    from app.agent_loop_lib.events.base import AgentEvent

__all__ = ["MessagePart", "TranscriptCollector"]

# Tool args are already bounded by whatever the model produced this turn;
# this is a defensive cap, not the primary truncation mechanism (that's
# tool_loop.py's 200-char result preview).
_MAX_TOOL_ARGS_CHARS = 2000
_MAX_TOOL_RESULT_CHARS = 500

# Longest single argument value kept verbatim. Anything longer is shortened
# in place, leaving the surrounding JSON intact — see `_serialize_tool_args`.
_MAX_ARG_VALUE_CHARS = 1200


def _serialize_tool_args(args: object) -> str:
    """Serialize tool arguments to JSON that always parses, however large the
    inputs were.

    The Node gateway rebuilds a previous turn's tool calls by running
    `JSON.parse` over this string
    (`enterprise_search/utils/utils.ts::toolResultsFromParts`) and, on
    failure, silently drops the arguments. Truncating the finished JSON at a
    byte offset produces exactly that failure, so a `run_code` program over
    the cap did not reach the next turn shortened — it vanished, taking the
    language and package list with it, and the model rewrote from scratch
    instead of editing.

    So the truncation happens per VALUE, before serialization, and says so:
    a caller that reads a prefix believing it is the whole program is the
    other way this goes wrong. The full source of a `run_code` call is
    durable anyway as its CODE artifact — this transcript only has to stay
    honest about being a summary.
    """
    if not isinstance(args, dict):
        args = {"value": args}

    shortened: dict[str, object] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > _MAX_ARG_VALUE_CHARS:
            shortened[str(key)] = (
                f"{value[:_MAX_ARG_VALUE_CHARS]}"
                f"… [truncated, {_MAX_ARG_VALUE_CHARS} of {len(value)} chars]"
            )
        else:
            shortened[str(key)] = value

    serialized = json.dumps(shortened, default=str)
    if len(serialized) <= _MAX_TOOL_ARGS_CHARS:
        return serialized

    # Still oversized: many arguments, each under the per-value cap. Drop
    # whole entries (never a partial one) until it fits, so the result stays
    # parseable, and record how many went.
    keys = list(shortened)
    while keys and len(serialized) > _MAX_TOOL_ARGS_CHARS:
        dropped = keys.pop()
        del shortened[dropped]
        shortened["__truncated__"] = f"{len(args) - len(keys)} argument(s) omitted"
        serialized = json.dumps(shortened, default=str)
    return serialized


class MessagePart(TypedDict, total=False):
    type: Literal["text", "reasoning", "tool_call", "sub_agent"]
    content: str
    toolCallId: str
    toolName: str
    displayName: str
    args: str
    argsSummary: str
    status: Literal["running", "completed", "failed", "blocked"]
    resultPreview: str
    resultSummary: str
    artifactId: str
    runId: str
    roleName: str
    parts: list["MessagePart"]
    # Set ONLY on the one root-level `text` part `replace_final_text` swaps
    # in — the citation-normalized/fallback/error text that IS
    # `completion_data["answer"]`. Every other root `text` part is, by
    # construction, an abandoned preamble turn (narration) — see the
    # "Cursor-Style Agent Transparency" plan. Never set on child (sub-agent)
    # text parts: nothing marks those, since a delegate's own final answer
    # has no separate answer surface to deduplicate against (rendered in
    # full by `SubAgentGroup` on the frontend).
    isFinal: bool


class TranscriptCollector(EventEmitter):
    """One instance per request/run tree (see `AgentContext.transcript_
    collector`) — mirrors `AGUIEventEmitter`'s per-request lifetime and
    `run_id`-keyed bookkeeping exactly, just building `MessagePart`s
    instead of wire frames."""

    def __init__(self) -> None:
        self.parts: list[MessagePart] = []
        # run_id -> the ordered list this run's own parts append to: the
        # top-level `self.parts` for the root run, or a parent `sub_agent`
        # part's own `parts` list for a child run.
        self._containers: dict[str, list[MessagePart]] = {}
        # (run_id, kind) -> the open text/reasoning part still accumulating
        # content, kind in {"text", "reasoning"} — mutated in place since
        # it's the SAME dict object already appended to its container.
        self._open_turn_parts: dict[tuple[str, str], MessagePart] = {}
        # toolCallId -> the open tool_call part, resolved at TOOL_CALL_END.
        self._open_tool_calls: dict[str, MessagePart] = {}

    async def emit(self, event: "AgentEvent") -> None:
        self._collect(event)

    def replace_final_text(self, content: str) -> None:
        """Swaps the ROOT run's last streamed `text` part for the
        citation-normalized final answer — mirrors how `AnswerFinalizer`
        corrects the streamed answer at completion (see `respond.py`).
        Only ever touches top-level parts: a sub-agent's own final text is
        never the thing being finalized here.

        Marks that part `isFinal` so the frontend can render every OTHER
        root `text` part as narration (Cursor-style "Let me check X
        first...") while skipping this one — it's already rendered via
        `AnswerContent`/`displayContent`, not the activity timeline. See
        `MessagePart.isFinal`'s docstring."""
        for part in reversed(self.parts):
            if part.get("type") == "text":
                part["content"] = content
                part["isFinal"] = True
                return
        # No streamed text turn reached this point (e.g. the very first
        # model turn produced neither tool calls nor text before the
        # empty-answer/error fallback) — append one so the final answer is
        # still represented in the transcript.
        self.parts.append({"type": "text", "content": content, "isFinal": True})

    @staticmethod
    def _prenormalize_source_citations(text: str) -> str:
        """Convert ``[source](refN)`` / ``[source](/record/…)`` → ``[N](…)``.

        During streaming the LLM emits ``[source](ref5)`` (or the tiny-URL
        variant ``[source](https://ref5.xyz)``) instead of ``[N](url)``.
        The full ``normalize_citations_and_chunks`` pipeline runs at the end
        of the agent loop, but narration parts are already visible on the
        frontend before that.  This lightweight pass assigns a provisional
        citation number so the frontend renders badges, not raw links.

        Already-numbered links ``[N](url)`` are left untouched (the negative
        lookahead ``(?!\\d+\\])`` skips them).  The full normalization at the
        end may reassign numbers for cross-part consistency — that's fine;
        the persisted data always uses the final numbering.
        """
        seen: dict[str, int] = {}

        def _ref_replacer(m: re.Match) -> str:
            key = f"ref{m.group(3)}"
            url = m.group(2)
            if key not in seen:
                seen[key] = len(seen) + 1
            return f"[{seen[key]}]({url})"

        def _record_replacer(m: re.Match) -> str:
            record_id = m.group(3)
            url = m.group(2)
            if record_id not in seen:
                seen[record_id] = len(seen) + 1
            return f"[{seen[record_id]}]({url})"

        text = _NON_NUMBERED_TINY_REF_LINK_RE.sub(_ref_replacer, text)
        text = _NON_NUMBERED_RECORD_LINK_RE.sub(_record_replacer, text)
        return text

    def _container_for(self, run_id: str) -> list[MessagePart]:
        return self._containers.setdefault(run_id, self.parts)

    def _collect(self, event: "AgentEvent") -> None:  # noqa: C901
        run_ctx: "RunContext" = event.run_context
        payload = event.payload
        run_id = run_ctx.run_id
        parent_run_id = run_ctx.parent_run_id
        is_child = parent_run_id is not None

        if event.event_type == EventType.RUN_STARTED:
            if not is_child:
                self._containers[run_id] = self.parts
                return
            sub_agent_part: MessagePart = {
                "type": "sub_agent", "runId": run_id, "roleName": run_ctx.role_name, "parts": [],
            }
            self._container_for(parent_run_id).append(sub_agent_part)
            self._containers[run_id] = sub_agent_part["parts"]
            return

        if event.event_type in (EventType.RUN_FINISHED, EventType.RUN_ERROR):
            # Nothing to close structurally (the sub_agent part is already
            # in its parent's list) — just drop the now-dead container
            # mapping so a reused run_id (shouldn't happen, but cheap to
            # guard) can't silently append into a finished run's timeline.
            self._containers.pop(run_id, None)
            return

        if event.event_type == EventType.TOOL_CALL_START:
            tool_call_id = payload.get("tool_call_id") or f"call_{event.event_id[:12]}"
            part: MessagePart = {
                "type": "tool_call",
                "toolCallId": tool_call_id,
                "toolName": payload.get("tool", "tool"),
                "args": _serialize_tool_args(payload.get("args", {})),
                "status": "running",
                "runId": run_id,
            }
            display_name = payload.get("display_name")
            if isinstance(display_name, str):
                part["displayName"] = display_name
            args_summary = payload.get("args_summary")
            if isinstance(args_summary, str):
                part["argsSummary"] = args_summary
            self._container_for(run_id).append(part)
            self._open_tool_calls[tool_call_id] = part
            return

        if event.event_type == EventType.TOOL_CALL_END:
            tool_call_id = payload.get("tool_call_id") or ""
            part = self._open_tool_calls.pop(tool_call_id, None)
            if part is None:
                return
            # Same disambiguation `AGUIEventEmitter._translate` uses: both
            # TOOL_RESULT and TOOL_BLOCKED alias to TOOL_CALL_END, and only
            # `payload["status"]` (set by the producer — see `ToolCallStatus`)
            # says which one this actually was.
            if payload.get("status") == ToolCallStatus.BLOCKED:
                part["status"] = "blocked"
                part["resultPreview"] = str(payload.get("reason", ""))[:_MAX_TOOL_RESULT_CHARS]
            else:
                part["status"] = "failed" if payload.get("is_error") else "completed"
                part["resultPreview"] = str(payload.get("content", ""))[:_MAX_TOOL_RESULT_CHARS]
                result_summary = payload.get("result_summary")
                if isinstance(result_summary, str):
                    part["resultSummary"] = result_summary
                artifact_id = payload.get("artifact_id")
                if isinstance(artifact_id, str):
                    part["artifactId"] = artifact_id
            return

        if event.event_type == EventType.TEXT_MESSAGE_START:
            text_part: MessagePart = {"type": "text", "content": "", "runId": run_id}
            self._container_for(run_id).append(text_part)
            self._open_turn_parts[(run_id, "text")] = text_part
            return

        if event.event_type == EventType.TEXT_MESSAGE_CONTENT:
            part = self._open_turn_parts.get((run_id, "text"))
            if part is not None:
                part["content"] = part.get("content", "") + payload.get("delta", "")
            return

        if event.event_type == EventType.TEXT_MESSAGE_END:
            part = self._open_turn_parts.pop((run_id, "text"), None)
            if part is not None and part.get("content"):
                part["content"] = self._prenormalize_source_citations(part["content"])
            return

        if event.event_type == EventType.REASONING_MESSAGE_START:
            reasoning_part: MessagePart = {"type": "reasoning", "content": "", "runId": run_id}
            self._container_for(run_id).append(reasoning_part)
            self._open_turn_parts[(run_id, "reasoning")] = reasoning_part
            return

        if event.event_type == EventType.REASONING_MESSAGE_CONTENT:
            part = self._open_turn_parts.get((run_id, "reasoning"))
            if part is not None:
                part["content"] = part.get("content", "") + payload.get("delta", "")
            return

        if event.event_type == EventType.REASONING_MESSAGE_END:
            self._open_turn_parts.pop((run_id, "reasoning"), None)
            return

        # Everything else (STEP_STARTED/STEP_FINISHED, STATE_SNAPSHOT,
        # TOOL_UNAVAILABLE, legacy AGENT_START/AGENT_COMPLETE/TOOL_CALL/
        # TOOL_RESULT/TOOL_BLOCKED duplicates already handled via their
        # alias above) carries nothing new for the transcript.
