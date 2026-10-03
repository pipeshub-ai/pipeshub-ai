"""One conversation stream read to the end, with what the agent did along the way.

``helper.agui_sse`` decodes single frames; this gathers a whole run so a test
can assert on its tool calls, the artifacts it produced, and the saved answer
without re-walking the stream. Tool calls ride on AG-UI ``TOOL_CALL_*`` frames
(``backend/python/app/agents/agent_loop/protocol/agui_emitter.py``); artifacts
on ``STATE_DELTA`` ops that add to ``/artifacts/-``; the persisted answer, its
citations and any ``::artifact[...]`` markers on the terminal ``RUN_FINISHED``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import requests

from helper.agui_sse import (
    AGUI,
    answer_from_completion,
    conversation_created_value,
    is_conversation_created,
    is_root_error,
    is_root_finished,
    iter_sse_envelopes,
    run_error_message,
    run_finished_result,
)

TOOL_CALL_START = "TOOL_CALL_START"
TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

_ARTIFACTS_PATH = "/artifacts/-"
# Same shape the frontend parses (frontend/app/(main)/chat/utils/parse-download-markers.ts).
_ARTIFACT_MARKER = re.compile(r"::artifact\[([^\]]+)\]\(([^)]+)\)\{([^}]*)\}")


@dataclass
class ToolCall:
    call_id: str
    name: str
    args: str = ""
    status: str | None = None
    result: str = ""


@dataclass(frozen=True)
class ArtifactRef:
    file_name: str
    artifact_id: str
    version: int | None
    mime_type: str = ""


@dataclass
class RunTrace:
    status_code: int
    conversation_id: str | None = None
    finished: bool = False
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    streamed_artifacts: list[ArtifactRef] = field(default_factory=list)
    event_types: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]

    def calls_ending_with(self, suffix: str) -> list[ToolCall]:
        return [call for call in self.tool_calls if call.name.endswith(suffix)]

    @property
    def conversation(self) -> dict[str, Any]:
        conversation = self.result.get("conversation")
        return conversation if isinstance(conversation, dict) else {}

    @property
    def bot_message(self) -> dict[str, Any]:
        return last_bot_message(self.conversation)

    @property
    def answer(self) -> str:
        return answer_from_completion(self.result)

    @property
    def citations(self) -> list[dict[str, Any]]:
        citations = self.bot_message.get("citations")
        return [c for c in citations if isinstance(c, dict)] if isinstance(citations, list) else []

    @property
    def saved_artifacts(self) -> list[ArtifactRef]:
        content = self.bot_message.get("content")
        return parse_artifact_markers(content if isinstance(content, str) else "")

    def describe(self) -> str:
        """Enough of the run to diagnose a failure from the log alone."""
        return (
            f"status={self.status_code} finished={self.finished} error={self.error!r} "
            f"tools={self.tool_names} streamed_artifacts={self.streamed_artifacts} "
            f"answer={self.answer[:400]!r}"
        )


def last_bot_message(conversation: dict[str, Any]) -> dict[str, Any]:
    messages = conversation.get("messages") if isinstance(conversation, dict) else None
    for message in reversed(messages if isinstance(messages, list) else []):
        if isinstance(message, dict) and (
            message.get("messageType") == "bot_response" or message.get("role") == "assistant"
        ):
            return message
    return {}


def parse_artifact_markers(content: str) -> list[ArtifactRef]:
    """``::artifact[name](url){mime|documentId|recordId|artifactType|version}`` markers."""
    refs: list[ArtifactRef] = []
    for file_name, _url, meta in _ARTIFACT_MARKER.findall(content or ""):
        parts = (meta.split("|") + [""] * 5)[:5]
        mime, document_id, record_id, _kind, raw_version = (p.strip() for p in parts)
        artifact_id = record_id or document_id
        if not artifact_id:
            continue
        version = int(raw_version) if raw_version.isdigit() else None
        refs.append(ArtifactRef(file_name.strip(), artifact_id, version, mime))
    return refs


def _artifact_from_state_delta(payload: Any) -> list[ArtifactRef]:
    ops = payload.get("delta") if isinstance(payload, dict) else None
    refs: list[ArtifactRef] = []
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict) or op.get("path") != _ARTIFACTS_PATH:
            continue
        value = op.get("value")
        if not isinstance(value, dict):
            continue
        artifact_id = value.get("artifactId") or value.get("recordId")
        if not artifact_id:
            continue
        version = value.get("version")
        refs.append(ArtifactRef(
            str(value.get("fileName") or ""),
            str(artifact_id),
            version if isinstance(version, int) else None,
            str(value.get("mimeType") or ""),
        ))
    return refs


def read_run(resp: requests.Response) -> RunTrace:
    """Read a conversation stream to its terminal frame and return what happened.

    A non-2xx response is returned with its status and body as ``error`` so the
    caller decides whether a refusal was expected.
    """
    trace = RunTrace(status_code=resp.status_code)
    if resp.status_code >= 300:
        trace.error = resp.text[:2000]
        return trace

    calls: dict[str, ToolCall] = {}
    for envelope in iter_sse_envelopes(resp):
        if not envelope["data"]:
            continue
        try:
            payload = json.loads(envelope["data"])
        except ValueError:
            continue
        event = envelope["event"] or (payload.get("type") if isinstance(payload, dict) else "")
        trace.event_types.append(event)

        if event == AGUI.CUSTOM and is_conversation_created(payload):
            trace.conversation_id = conversation_created_value(payload).get("conversationId") or trace.conversation_id
        elif event == TOOL_CALL_START and isinstance(payload, dict):
            call = ToolCall(str(payload.get("toolCallId") or len(calls)), str(payload.get("toolCallName") or ""))
            calls[call.call_id] = call
            trace.tool_calls.append(call)
        elif event == TOOL_CALL_ARGS and isinstance(payload, dict):
            call = calls.get(str(payload.get("toolCallId")))
            if call is not None:
                call.args += str(payload.get("delta") or "")
        elif event == TOOL_CALL_RESULT and isinstance(payload, dict):
            call = calls.get(str(payload.get("toolCallId")))
            if call is not None:
                call.status = payload.get("status")
                call.result = str(payload.get("content") or "")
        elif event == AGUI.STATE_DELTA:
            trace.streamed_artifacts.extend(_artifact_from_state_delta(payload))
        elif is_root_error(event, payload):
            trace.error = run_error_message(payload) or json.dumps(payload)[:2000]
            break
        elif is_root_finished(event, payload):
            trace.finished = True
            trace.result = run_finished_result(payload)
            conversation = trace.result.get("conversation")
            if isinstance(conversation, dict) and not trace.conversation_id:
                trace.conversation_id = conversation.get("_id") or conversation.get("id")
            break
    return trace
