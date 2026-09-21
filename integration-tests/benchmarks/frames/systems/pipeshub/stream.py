"""Folding one AG-UI chat stream into a `StreamTrace` and the final answer.

Table-driven: each frame type has one small handler. Frames carrying
`parentRunId` belong to sub-agents; their tool calls are kept (the guard must
see them) but they never supply the answer or a terminal outcome.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from agui_sse import answer_from_completion, is_child_frame
from benchmarks.frames.models import CallUsage, Citation, RetrievalEvent, RunStats, StreamTrace, SystemFailure, ToolCallTrace

logger = logging.getLogger(__name__)

_RESULT_PREVIEW_CHARS = 1000

# Frames that neither start nor end a batch of tool calls.
_WAVE_NEUTRAL = frozenset({"TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT", "CUSTOM", "HEARTBEAT"})


@dataclass
class FinalAnswer:
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    confidence: str | None = None
    # From the `run_usage` frame; None when the backend sent none.
    llm_calls: list[CallUsage] | None = None


def _int_list(values: Any) -> list[int]:  # noqa: ANN401
    return [int(v) for v in values or [] if isinstance(v, int) and not isinstance(v, bool)]


def parse_citation(raw: dict[str, Any]) -> Citation:
    meta = raw.get("metadata") or {}
    index = raw.get("chunkIndex")
    return Citation(
        display_index=index if isinstance(index, int) else None,
        record_id=meta.get("recordId"),
        virtual_record_id=meta.get("virtualRecordId"),
        record_name=meta.get("recordName"),
        content=str(raw.get("content") or ""),
        block_nums=_int_list(meta.get("blockNum")),
        web_url=meta.get("webUrl"),
    )


def parse_run_usage(value: dict[str, Any]) -> list[CallUsage]:
    """Per-call `[input, output, cacheRead]` triples; the totals are only a
    fallback for a backend that sends no breakdown."""
    calls = [
        CallUsage(input_tokens=int(c[0]), output_tokens=int(c[1]), cached_tokens=int(c[2]), purpose="agent")
        for c in value.get("calls") or [] if isinstance(c, list) and len(c) == 3
    ]
    if calls:
        return calls
    return [CallUsage(
        input_tokens=int(value.get("inputTokens") or 0), output_tokens=int(value.get("outputTokens") or 0),
        cached_tokens=int(value.get("cacheReadTokens") or 0), purpose="agent",
    )]


class StreamCollector:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._opened = clock()
        self.frames_seen = 0
        self._counts: Counter[str] = Counter()
        self._conversation_id: str | None = None
        self._tool_calls: dict[str, ToolCallTrace] = {}
        self._events: list[RetrievalEvent] = []
        self._waves = 0
        self._in_wave = False
        self._final = FinalAnswer()
        self._llm_calls: list[CallUsage] | None = None
        self._run_stats: RunStats | None = None
        self._transcript: list[dict[str, Any]] = []
        self._have_final = False
        self._finished = False
        self._failure: SystemFailure | None = None
        self._handlers: dict[str, Callable[[dict[str, Any], bool], None]] = {
            "CUSTOM": self._on_custom,
            "TOOL_CALL_START": self._on_tool_start,
            "TOOL_CALL_ARGS": self._on_tool_args,
            "TOOL_CALL_RESULT": self._on_tool_result,
            "STATE_SNAPSHOT": self._on_snapshot,
            "RUN_FINISHED": self._on_run_finished,
            "RUN_ERROR": self._on_run_error,
        }

    def _elapsed_ms(self) -> int:
        return int((self._clock() - self._opened) * 1000)

    def feed(self, envelope: dict[str, str]) -> None:
        self.frames_seen += 1
        try:
            payload = json.loads(envelope.get("data") or "{}")
        except json.JSONDecodeError:
            self._counts["_unparseable"] += 1
            return
        if not isinstance(payload, dict):
            return
        frame_type = str(payload.get("type") or envelope.get("event") or "")
        self._counts[frame_type] += 1
        child = is_child_frame(payload)
        if not child:
            self._track_wave(frame_type)
        handler = self._handlers.get(frame_type)
        if handler is not None:
            handler(payload, child)

    def _track_wave(self, frame_type: str) -> None:
        if frame_type == "TOOL_CALL_START":
            if not self._in_wave:
                self._waves += 1
            self._in_wave = True
        elif frame_type not in _WAVE_NEUTRAL:
            self._in_wave = False

    def _on_custom(self, payload: dict[str, Any], child: bool) -> None:
        name, value = payload.get("name"), payload.get("value") or {}
        self._counts[f"CUSTOM:{name}"] += 1
        if name == "conversation_created":
            self._conversation_id = value.get("conversationId")
        elif name == "retrieval_context":
            try:
                self._events.append(RetrievalEvent.model_validate(value))
            except ValidationError as exc:
                self._counts["_bad_retrieval_context"] += 1
                logger.warning("unparseable retrieval_context frame: %s", exc)
        elif name == "run_usage" and not child:
            self._llm_calls = parse_run_usage(value)
            self._run_stats = RunStats(
                turns=value.get("turns"), max_turns=value.get("maxTurns"),
                completion_gate_nudges=int(value.get("completionGateNudges") or 0),
                auxiliary_llm_calls=int(value.get("auxiliaryLlmCalls") or 0),
                agent_error=value.get("agentError"),
            )

    def _on_tool_start(self, payload: dict[str, Any], child: bool) -> None:
        call_id = str(payload.get("toolCallId") or f"_anon{len(self._tool_calls)}")
        self._tool_calls[call_id] = ToolCallTrace(
            tool_call_id=call_id, name=str(payload.get("toolCallName") or ""),
            parent_run_id=payload.get("parentRunId"), started_ms=self._elapsed_ms(),
        )

    def _on_tool_args(self, payload: dict[str, Any], child: bool) -> None:
        call = self._tool_calls.get(str(payload.get("toolCallId")))
        if call is not None:
            call.args_json += str(payload.get("delta") or "")

    def _on_tool_result(self, payload: dict[str, Any], child: bool) -> None:
        call = self._tool_calls.get(str(payload.get("toolCallId")))
        if call is not None:
            call.status = payload.get("status")
            call.result_summary = payload.get("resultSummary")
            call.ended_ms = self._elapsed_ms()
            content = payload.get("content")
            call.result_preview = str(content)[:_RESULT_PREVIEW_CHARS] if content else None

    def _on_snapshot(self, payload: dict[str, Any], child: bool) -> None:
        snapshot = payload.get("snapshot") or {}
        if child or not snapshot.get("final"):
            return
        self._transcript = [p for p in snapshot.get("parts") or [] if isinstance(p, dict)]
        self._final = FinalAnswer(
            answer=str(snapshot.get("answer") or ""),
            citations=[parse_citation(c) for c in snapshot.get("citations") or [] if isinstance(c, dict)],
            confidence=snapshot.get("confidence"),
        )
        self._have_final = True

    def _on_run_finished(self, payload: dict[str, Any], child: bool) -> None:
        if child or not payload.get("result"):
            return
        self._finished = True
        if not self._have_final:
            self._final.answer = answer_from_completion(payload["result"])

    def _on_run_error(self, payload: dict[str, Any], child: bool) -> None:
        if not child:
            self._failure = SystemFailure(
                kind="run_error", code=payload.get("code"), message=str(payload.get("message") or "RUN_ERROR"),
            )

    def result(self) -> tuple[StreamTrace, FinalAnswer, SystemFailure | None]:
        trace = StreamTrace(
            conversation_id=self._conversation_id,
            tool_calls=list(self._tool_calls.values()),
            retrieval_events=sorted(self._events, key=lambda e: e.seq),
            tool_waves=self._waves,
            frame_counts=dict(self._counts),
            finished=self._finished,
            confidence=self._final.confidence,
            run_stats=self._run_stats,
            transcript=self._transcript,
        )
        self._final.llm_calls = self._llm_calls
        failure = self._failure
        if failure is None and not self._finished:
            failure = SystemFailure(kind="protocol", message="stream ended without a root RUN_FINISHED")
        return trace, self._final, failure
