"""Shared SSE parsing and AG-UI accessors for conversation streaming tests.

The conversation streaming routes speak AG-UI exclusively — the legacy
``connected``/``answer_chunk``/``complete``/``error`` vocabulary was removed in
the agent-loop change, and `resolveProtocol()` in the Node proxy pins every
request to AG-UI regardless of what the client asks for. The accessors here
exist so the shape of each AG-UI payload is decoded in exactly one place.

Frames are hybrid-framed by both the Node proxy (``frameAGUI``) and the Python
side (``protocol/agui.py::frame``): the ``event:`` line carries the AG-UI type
name and ``data:`` carries the full JSON including a matching ``"type"``.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Iterator
from typing import Any, NamedTuple

import requests

SSE_MAX_EVENTS = 10_000
SSEEnvelope = dict[str, str]

_READ_CHUNK_SIZE = 512


class StreamOutcome(NamedTuple):
    """Terminal state of one conversation stream.

    ``finished`` and ``error`` are independent: a stream that emits neither is
    a hang or a truncated response, which is distinct from one that failed
    cleanly with ``RUN_ERROR``. Negative tests must assert ``error`` rather
    than ``not finished`` — the latter is also true when nothing arrived.
    """

    conversation_id: str | None
    answer: str
    finished: bool
    error: str | None
    # RUN_FINISHED `result`, so callers can assert on the echoed conversation without
    # re-reading the stream.
    result: dict[str, Any] | None = None

    @property
    def errored(self) -> bool:
        return self.error is not None


class AGUI:
    """AG-UI type names asserted on by the conversation tests."""

    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STATE_DELTA = "STATE_DELTA"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    CUSTOM = "CUSTOM"


CONVERSATION_CREATED = "conversation_created"

# Path of the running normalized answer inside a STATE_DELTA JSON-Patch op.
_NORMALIZED_ANSWER_PATH = "/normalizedAnswer"


def _parse_frame(raw: str) -> SSEEnvelope | None:
    """One frame's lines → envelope, or ``None`` when the frame carries nothing."""
    event_name: str | None = None
    data_lines: list[str] = []

    for raw_line in raw.split("\n"):
        line = raw_line.rstrip("\r")
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].removeprefix(" "))
        # Ignore optional SSE fields (id:, retry:, etc.)

    if event_name is None and not data_lines:
        return None
    return {"event": event_name or "", "data": "\n".join(data_lines)}


def iter_sse_envelopes(
    resp: requests.Response,
    *,
    max_events: int = SSE_MAX_EVENTS,
) -> Iterator[SSEEnvelope]:
    """
    Minimal SSE parser for frames like:

      event: <name>
      data: <payload>

    Frames are separated by a blank line. Returns envelopes:
    { "event": <name>, "data": <string> }.

    Frames are cut out of a buffer here rather than read via
    ``resp.iter_lines(delimiter="\\n")``: when a socket read happens to end on a
    newline, ``iter_lines`` yields a trailing empty string that is
    indistinguishable from a real frame separator. A read boundary landing
    between an ``event:`` line and its ``data:`` line therefore splits one frame
    into an empty-``data`` envelope plus an orphan, which is why callers used to
    see ``JSONDecodeError`` on arbitrary frames. Owning the buffer keeps framing
    independent of where reads land. Bytes are decoded incrementally so a read
    splitting a multi-byte character is also safe.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    emitted = 0

    for chunk in resp.iter_content(chunk_size=_READ_CHUNK_SIZE):
        if not chunk:
            continue
        buffer += decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        buffer = buffer.replace("\r\n", "\n")
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            env = _parse_frame(raw)
            if env is None:
                continue
            yield env
            emitted += 1
            if emitted >= max_events:
                raise AssertionError(f"SSE exceeded max_events={max_events}")

    buffer += decoder.decode(b"", final=True)
    env = _parse_frame(buffer)
    if env is not None:
        yield env


def is_child_frame(payload: Any) -> bool:
    """True for a nested sub-agent frame, which carries ``parentRunId``.

    When ``chatMode`` spawns domain sub-agents, each child run emits its own
    ``RUN_STARTED``/``RUN_FINISHED``/``RUN_ERROR``. Node forwards those to the
    client verbatim; it swallows only the *root* ``RUN_FINISHED`` and re-emits
    an enriched one carrying the persisted conversation.

    So a child ``RUN_FINISHED`` arrives BEFORE the terminal one and carries no
    ``result`` — treating it as terminal ends the stream early with an empty
    answer. Likewise a child ``RUN_ERROR`` is one sub-agent failing, not the
    stream failing. Callers must ignore both.
    """
    return isinstance(payload, dict) and payload.get("parentRunId") is not None


def is_root_finished(event: str, payload: Any) -> bool:
    """True for the single terminal ``RUN_FINISHED`` that carries the result."""
    return event == AGUI.RUN_FINISHED and not is_child_frame(payload)


def is_root_error(event: str, payload: Any) -> bool:
    """True for a stream-level ``RUN_ERROR`` (not a sub-agent's own failure)."""
    return event == AGUI.RUN_ERROR and not is_child_frame(payload)


def is_conversation_created(payload: Any) -> bool:
    """True for the ``CUSTOM`` frame that replaced the legacy ``connected``."""
    return isinstance(payload, dict) and payload.get("name") == CONVERSATION_CREATED


def conversation_created_value(payload: Any) -> dict[str, Any]:
    """``CUSTOM{name: conversation_created}`` payload — ``{conversationId?, title?}``.

    Only the create-conversation routes put a ``conversationId`` here; the
    add-message routes stream onto a conversation the caller already named, so
    their frame carries just a connection message.
    """
    if not is_conversation_created(payload):
        return {}
    value = payload.get("value")
    return value if isinstance(value, dict) else {}


def run_finished_result(payload: Any) -> dict[str, Any]:
    """``RUN_FINISHED`` payload — ``{conversation, recordsUsed, meta}`` under ``result``.

    This is the same completion shape the legacy ``complete`` event carried
    at the top level, moved down one nesting level.
    """
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def state_delta_answer(payload: Any) -> str | None:
    """Running normalized answer from a ``STATE_DELTA`` JSON-Patch op, if present.

    The citation-rewritten answer rides on ``STATE_DELTA``, not
    ``TEXT_MESSAGE_CONTENT``: citation refs mutate mid-stream and preamble turns
    reset the text to "", which would violate AG-UI's append-only
    ``TEXT_MESSAGE_CONTENT`` contract. ``TEXT_MESSAGE_CONTENT`` carries raw
    append-only token deltas and is the wrong channel to accumulate from.
    """
    if not isinstance(payload, dict):
        return None
    ops = payload.get("delta")
    if not isinstance(ops, list):
        return None
    for op in ops:
        if not isinstance(op, dict) or op.get("path") != _NORMALIZED_ANSWER_PATH:
            continue
        value = op.get("value")
        if isinstance(value, str):
            return value
    return None


def run_error_message(payload: Any) -> str:
    """``RUN_ERROR`` payload — ``{message, code}``."""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    return message if isinstance(message, str) else ""


def answer_from_completion(payload: dict[str, Any]) -> str:
    """Latest assistant text from a ``RUN_FINISHED`` result payload.

    Pass the unwrapped ``result`` (see :func:`run_finished_result`), not the
    raw event payload.
    """
    conv = payload.get("conversation") or {}
    msgs = conv.get("messages") or []
    for m in reversed(msgs if isinstance(msgs, list) else []):
        if not isinstance(m, dict):
            continue
        if m.get("messageType") == "bot_response":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
        if m.get("role") == "assistant":
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content
    answer = payload.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer
    return ""


def parse_envelope(envelope: SSEEnvelope) -> tuple[str, Any]:
    """``(event_name, decoded_payload)`` for one envelope."""
    return envelope["event"], json.loads(envelope["data"])
