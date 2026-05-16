"""
PipesHub SSE streaming protocol (v1 legacy + v2 enriched).

v1: existing event names and payloads (answer_chunk, complete, status, tool_call, …).
v2: optional envelope-wrapped events + lifecycle + structured tool/retrieval traces.

Size caps are enforced before emission and before persistence (see cap_stream_trace).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# --- Protocol ----------------------------------------------------------------
PROTOCOL_V1 = 1
PROTOCOL_V2 = 2
PROTOCOL_NAME_V2 = "pipeshub.sse.v2"

# --- Payload caps (bytes / counts) --------------------------------------------
MAX_DELTA_CHARS = 16_000
MAX_RETRIEVAL_SNIPPET_CHARS = 280
MAX_RETRIEVAL_HITS = 50
MAX_TOOL_OBSERVATION_CHARS = 4_096
MAX_REASONING_SUMMARY_CHARS = 2_048
MAX_STREAM_TRACE_TOTAL_CHARS = 64_000

# Error codes (unified shape for v2)
ERR_INTERNAL = "INTERNAL"
ERR_LLM_INIT = "LLM_INIT_FAILED"
ERR_RETRIEVAL = "RETRIEVAL_TIMEOUT"
ERR_TOOL_AUTH = "TOOL_AUTH"
ERR_TOOL_TRANSIENT = "TOOL_TRANSIENT"
ERR_STREAM_ABORTED = "STREAM_ABORTED"
ERR_TOOL_EXEC = "TOOL_EXECUTION"


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:24]}"


def new_message_id(prefix: str = "msg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def truncate_str(s: str | None, max_chars: int) -> str:
    if not s:
        return ""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


@dataclass
class StreamTraceAccumulator:
    """Collects trace fragments during a stream for assistant_message_end.streamTrace."""

    retrieval: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    reasoning_summary: str = ""

    def add_retrieval(self, payload: dict[str, Any]) -> None:
        self.retrieval.append(
            {
                "query": payload.get("query", ""),
                "source": payload.get("source", "kb"),
                "hits": payload.get("hits") or [],
            }
        )

    def add_tool_observation(
        self,
        *,
        call_id: str,
        name: str,
        args: Any,
        observation: str | None,
        latency_ms: float,
        error: str | None = None,
    ) -> None:
        self.tool_calls.append(
            {
                "callId": call_id,
                "name": name,
                "args": args,
                "observation": truncate_str(observation or "", MAX_TOOL_OBSERVATION_CHARS),
                "latencyMs": latency_ms,
                "error": error,
            }
        )

    def append_reasoning_delta(self, delta: str) -> None:
        self.reasoning_summary = truncate_str(
            self.reasoning_summary + delta,
            MAX_REASONING_SUMMARY_CHARS,
        )

    def to_stream_trace_dict(self) -> dict[str, Any] | None:
        out: dict[str, Any] = {}
        if self.reasoning_summary:
            out["reasoningSummary"] = self.reasoning_summary
        if self.retrieval:
            out["retrieval"] = self.retrieval
        if self.tool_calls:
            out["toolCalls"] = self.tool_calls
        if self.steps:
            out["steps"] = self.steps
        if not out:
            return None
        return cap_stream_trace(out)


def cap_stream_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Serialize and truncate entire trace to MAX_STREAM_TRACE_TOTAL_CHARS."""
    raw = json.dumps(trace, default=str)
    if len(raw) <= MAX_STREAM_TRACE_TOTAL_CHARS:
        return trace
    # Truncate tool observations first
    tc = trace.get("toolCalls")
    if isinstance(tc, list):
        for item in tc:
            if isinstance(item, dict) and item.get("observation"):
                item["observation"] = truncate_str(str(item["observation"]), 500)
    raw2 = json.dumps(trace, default=str)
    if len(raw2) <= MAX_STREAM_TRACE_TOTAL_CHARS:
        return trace
    return json.loads(raw2[: MAX_STREAM_TRACE_TOTAL_CHARS - 20] + '"}')


@dataclass
class StreamContext:
    """Per-request streaming context (v2). None => strict v1 behaviour."""

    protocol_version: int = PROTOCOL_V1
    stream_features: frozenset[str] = field(default_factory=frozenset)
    run_id: str = field(default_factory=new_run_id)
    conversation_id: str | None = None
    user_message_id: str = field(default_factory=lambda: new_message_id("usr"))
    assistant_message_id: str = field(default_factory=lambda: new_message_id("asst"))
    model_key: str | None = None
    model_name: str | None = None
    chat_mode: str | None = None
    seq: int = 0
    trace: StreamTraceAccumulator = field(default_factory=StreamTraceAccumulator)
    protocol_announced: bool = False
    lifecycle_started: bool = False

    @property
    def is_v2(self) -> bool:
        return self.protocol_version >= PROTOCOL_V2

    def wants_reasoning(self) -> bool:
        return "reasoning" in self.stream_features

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def next_evt_id(self) -> str:
        return f"evt_{uuid.uuid4().hex[:20]}"

    def envelope(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        if event_type == "user_message":
            mid: str | None = self.user_message_id
        elif event_type.startswith("assistant_") or event_type == "citation_added":
            mid = self.assistant_message_id
        else:
            mid = None
        return {
            "v": PROTOCOL_V2,
            "type": event_type,
            "id": self.next_evt_id(),
            "runId": self.run_id,
            "conversationId": self.conversation_id,
            "messageId": mid,
            "parentId": None,
            "ts": int(time.time() * 1000),
            "seq": self.next_seq(),
            "data": data,
        }


def normalize_error_payload(
    message: str,
    *,
    code: str = ERR_INTERNAL,
    retryable: bool = False,
    transient: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "transient": transient,
        "details": details or {},
    }


def _extract_snippet_from_flat_row(r: dict[str, Any]) -> str:
    """
    Human-readable preview from a flattened KB row.

    Rows use `content` (str | tuple | list), not `text`. Tuple/list shapes come from
    table and block-group results — str() on those produces unusable debug output.
    """
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}

    block_text = meta.get("blockText") or meta.get("block_text")
    if isinstance(block_text, str) and block_text.strip():
        return block_text.strip()

    content = r.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, tuple):
        head = content[0] if content else None
        if isinstance(head, str) and head.strip():
            return head.strip()
        tail = content[1] if len(content) > 1 else None
        if isinstance(tail, list):
            parts: list[str] = []
            for child in tail[:4]:
                if not isinstance(child, dict):
                    continue
                child_meta = child.get("metadata") if isinstance(child.get("metadata"), dict) else {}
                piece = child.get("content") or child_meta.get("blockText") or child_meta.get("block_text")
                if isinstance(piece, str) and piece.strip():
                    parts.append(piece.strip())
            if parts:
                return " · ".join(parts)

    if isinstance(content, list):
        parts = []
        for item in content[:4]:
            if isinstance(item, dict):
                piece = item.get("content") or item.get("text")
                if isinstance(piece, str) and piece.strip():
                    parts.append(piece.strip())
        if parts:
            return " · ".join(parts)

    legacy_text = r.get("text")
    if isinstance(legacy_text, str) and legacy_text.strip():
        return legacy_text.strip()

    return ""


def _hit_from_flat_result(r: dict[str, Any]) -> dict[str, Any]:
    """Build a single sanitized retrieval hit from a flattened search result row."""
    meta = r.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    title = (
        meta.get("recordName")
        or meta.get("title")
        or meta.get("name")
        or r.get("virtual_record_id")
        or "Record"
    )
    snippet = truncate_str(_extract_snippet_from_flat_row(r), MAX_RETRIEVAL_SNIPPET_CHARS)
    connector = (
        meta.get("connector")
        or meta.get("connectorName")
        or meta.get("connector_name")
        or ""
    )
    web_url = meta.get("webUrl") or meta.get("weburl") or meta.get("url") or ""
    return {
        "virtualRecordId": r.get("virtual_record_id") or meta.get("virtualRecordId"),
        "recordId": meta.get("recordId") or meta.get("id") or r.get("record_id"),
        "title": str(title)[:500],
        "snippet": snippet,
        "score": r.get("score") or meta.get("score"),
        "connector": str(connector) if connector else None,
        "mimeType": meta.get("mimeType"),
        "extension": meta.get("extension"),
        "url": str(web_url) if web_url else None,
    }


def build_retrieval_event_data(
    query: str,
    source: str,
    flattened_results: list[dict[str, Any]],
    *,
    max_hits: int = MAX_RETRIEVAL_HITS,
) -> dict[str, Any]:
    hits = [_hit_from_flat_result(r) for r in flattened_results[:max_hits]]
    return {
        "query": query,
        "source": source,
        "hits": hits,
    }


def expand_internal_event_for_sse(
    ctx: StreamContext | None,
    internal: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Turn one internal {event, data} into one or more events for the wire.

    v1 (ctx None or protocol v1): returns [internal] unchanged.
    v2: returns [v2_envelope_event, internal] so v1 clients still receive the legacy event.
    The v2 event uses the same `event` key as the envelope `type` inside JSON... Actually
    we use event name = envelope type for SSE `event:` line, data = full envelope JSON.
    """
    ev = internal.get("event")
    data = internal.get("data")
    if not isinstance(data, (dict, list, str, type(None))):
        data = {"value": data}

    if ctx is None or not ctx.is_v2:
        return [internal]

    if ev == "protocol":
        return [internal]

    # Map legacy -> v2 type + envelope data (payload only in data field of envelope)
    v2_type: str | None = None
    inner: dict[str, Any] = data if isinstance(data, dict) else {"payload": data}

    if ev == "status" and isinstance(data, dict) and data.get("status") == "keepalive":
        v2_type = "heartbeat"
        inner = {"ts": int(time.time() * 1000)}
    elif ev == "error":
        msg = ""
        code = ERR_INTERNAL
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("message") or "Error")
            code = str(data.get("code") or ERR_INTERNAL)
        else:
            msg = str(data)
        v2_type = "run_error"
        inner = {
            "runId": ctx.run_id,
            "error": normalize_error_payload(
                msg,
                code=code,
                retryable=bool(data.get("retryable")) if isinstance(data, dict) else False,
                transient=bool(data.get("transient")) if isinstance(data, dict) else False,
                details=data if isinstance(data, dict) else None,
            ),
        }
    elif ev == "tool_call" and isinstance(data, dict):
        v2_type = "tool_call_start"
        inner = {
            "callId": data.get("call_id"),
            "name": data.get("tool_name"),
            "argsPartial": None,
            "args": data.get("tool_args"),
        }
    elif ev == "tool_success" and isinstance(data, dict):
        v2_type = "tool_call_observation"
        obs = data.get("summary") or ""
        inner = {
            "callId": data.get("call_id"),
            "name": data.get("tool_name"),
            "result": truncate_str(obs, MAX_TOOL_OBSERVATION_CHARS),
            "truncatedResult": None,
            "latencyMs": float(data.get("latencyMs") or 0),
            "recordCount": data.get("record_count"),
            "error": None,
        }
    elif ev == "tool_error" and isinstance(data, dict):
        v2_type = "tool_call_observation"
        inner = {
            "callId": data.get("call_id"),
            "name": data.get("tool_name"),
            "result": None,
            "truncatedResult": None,
            "latencyMs": float(data.get("latencyMs") or 0),
            "recordCount": None,
            "error": data.get("error") or "tool_error",
        }
    elif ev == "answer_chunk" and isinstance(data, dict):
        v2_type = "assistant_text_delta"
        inner = {
            "messageId": ctx.assistant_message_id,
            "delta": truncate_str(data.get("chunk") or "", MAX_DELTA_CHARS),
            "accumulatedLen": len(data.get("accumulated") or ""),
        }
    elif ev == "citation_added" and isinstance(data, dict):
        v2_type = "citation_added"
        inner = {
            "messageId": data.get("messageId", ctx.assistant_message_id),
            "citation": data.get("citation"),
        }
    elif ev == "assistant_reasoning_delta" and isinstance(data, dict):
        v2_type = "assistant_reasoning_delta"
        inner = {
            "messageId": data.get("messageId", ctx.assistant_message_id),
            "delta": truncate_str(data.get("delta") or "", MAX_DELTA_CHARS),
        }
    elif ev == "retrieval" and isinstance(data, dict):
        v2_type = "retrieval"
        inner = data
        ctx.trace.add_retrieval(data)
    elif ev == "heartbeat":
        v2_type = "heartbeat"
        inner = data if isinstance(data, dict) else {"ts": int(time.time() * 1000)}
    elif ev == "tool_call_start" and isinstance(data, dict):
        v2_type = "tool_call_start"
        inner = data
    elif ev == "tool_call_dispatched" and isinstance(data, dict):
        v2_type = "tool_call_dispatched"
        inner = data
    elif ev == "tool_call_observation" and isinstance(data, dict):
        v2_type = "tool_call_observation"
        inner = data
    elif ev == "tool_call_end" and isinstance(data, dict):
        v2_type = "tool_call_end"
        inner = data
    elif ev == "assistant_message_end" and isinstance(data, dict):
        v2_type = "assistant_message_end"
        inner = data
    elif ev == "run_finished" and isinstance(data, dict):
        v2_type = "run_finished"
        inner = data
    elif ev == "run_aborted" and isinstance(data, dict):
        v2_type = "run_aborted"
        inner = data
    elif ev == "run_error" and isinstance(data, dict):
        v2_type = "run_error"
        inner = data
    elif ev == "run_started" and isinstance(data, dict):
        v2_type = "run_started"
        inner = data
    elif ev == "user_message" and isinstance(data, dict):
        v2_type = "user_message"
        inner = data
    elif ev == "assistant_message_start" and isinstance(data, dict):
        v2_type = "assistant_message_start"
        inner = data

    out: list[dict[str, Any]] = []
    if v2_type:
        env = ctx.envelope(v2_type, inner)
        out.append({"event": v2_type, "data": env})
    # Lifecycle / trace-only events have no legacy duplicate on the wire.
    v2_only_no_legacy = frozenset({
        "run_started",
        "user_message",
        "assistant_message_start",
        "assistant_message_end",
        "run_finished",
        "run_aborted",
        "run_error",
        "tool_call_dispatched",
        "tool_call_end",
        "tool_call_observation",
        "citation_added",
        "retrieval",
        "heartbeat",
        "assistant_reasoning_delta",
    })
    if not v2_type or v2_type not in v2_only_no_legacy:
        out.append(internal)
    return out


def protocol_announcement_event() -> dict[str, Any]:
    return {"event": "protocol", "data": {"protocol": PROTOCOL_NAME_V2, "v": PROTOCOL_V2}}


def build_run_started_data(ctx: StreamContext) -> dict[str, Any]:
    return {
        "conversationId": ctx.conversation_id,
        "runId": ctx.run_id,
        "model": {"modelKey": ctx.model_key, "modelName": ctx.model_name},
        "chatMode": ctx.chat_mode,
        "capabilities": sorted(ctx.stream_features),
    }


def build_user_message_echo_data(ctx: StreamContext, query: str, attachments: list | None) -> dict[str, Any]:
    return {
        "messageId": ctx.user_message_id,
        "role": "user",
        "content": query,
        "attachments": attachments or [],
    }


def build_assistant_message_start_data(ctx: StreamContext) -> dict[str, Any]:
    return {"messageId": ctx.assistant_message_id, "role": "assistant"}


def build_assistant_message_end_data(
    ctx: StreamContext,
    *,
    answer: str,
    citations: list | None,
    confidence: Any = None,
    reason: Any = None,
    reference_data: list | None = None,
) -> dict[str, Any]:
    st = ctx.trace.to_stream_trace_dict()
    payload: dict[str, Any] = {
        "messageId": ctx.assistant_message_id,
        "answer": answer,
        "citations": citations or [],
        "confidence": confidence,
        "reason": reason,
        "referenceData": reference_data or [],
    }
    if st:
        payload["streamTrace"] = st
    return payload
