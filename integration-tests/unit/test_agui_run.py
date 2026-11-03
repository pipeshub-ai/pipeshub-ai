"""helper.agui_run gathers tool calls, artifacts and the saved answer from a stream.

The AI and agent suite asserts on these instead of on answer wording, so a
parsing slip here would make a real tool call look missing (or the reverse).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from helper.agui_run import ArtifactRef, parse_artifact_markers, read_run

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, frames: list[tuple[str, dict[str, Any]]], status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = "refused" if status_code >= 300 else ""
        body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in frames)
        self._body = body.encode()

    def iter_content(self, chunk_size: int = 512):
        # Odd chunk size, so frames are split across reads like a real socket.
        for i in range(0, len(self._body), 7):
            yield self._body[i : i + 7]


_MARKER = "::artifact[note.docx](record:abc){application/vnd.openxmlformats|doc1|abc|DOCUMENT|2}"


def _frames() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("CUSTOM", {"type": "CUSTOM", "name": "conversation_created", "value": {"conversationId": "c1"}}),
        ("RUN_STARTED", {"type": "RUN_STARTED", "runId": "r1"}),
        ("TOOL_CALL_START", {"type": "TOOL_CALL_START", "toolCallId": "t1", "toolCallName": "load_skill"}),
        ("TOOL_CALL_ARGS", {"type": "TOOL_CALL_ARGS", "toolCallId": "t1", "delta": '{"name":'}),
        ("TOOL_CALL_ARGS", {"type": "TOOL_CALL_ARGS", "toolCallId": "t1", "delta": '"docx"}'}),
        ("TOOL_CALL_END", {"type": "TOOL_CALL_END", "toolCallId": "t1"}),
        ("TOOL_CALL_RESULT", {"type": "TOOL_CALL_RESULT", "toolCallId": "t1", "status": "completed", "content": "ok"}),
        ("STATE_DELTA", {"type": "STATE_DELTA", "delta": [
            {"op": "add", "path": "/artifacts/-", "value": {"artifactId": "abc", "fileName": "note.docx", "version": 1}},
            {"op": "replace", "path": "/normalizedAnswer", "value": "partial"},
        ]}),
        # A sub-agent finishing is not the end of the stream.
        ("RUN_FINISHED", {"type": "RUN_FINISHED", "runId": "child", "parentRunId": "r1"}),
        ("RUN_FINISHED", {"type": "RUN_FINISHED", "runId": "r1", "result": {"conversation": {
            "_id": "c1",
            "messages": [
                {"messageType": "user_query", "content": "make a doc"},
                {"messageType": "bot_response", "content": f"Here it is.\n{_MARKER}",
                 "citations": [{"citationId": "x", "citationData": {"metadata": {"recordId": "r9"}}}]},
            ],
        }}}),
    ]


def test_a_run_is_read_to_its_root_finish() -> None:
    trace = read_run(_FakeResponse(_frames()))

    assert trace.finished and trace.error is None
    assert trace.conversation_id == "c1"
    assert trace.tool_names == ["load_skill"]
    call = trace.tool_calls[0]
    assert (json.loads(call.args), call.status, call.result) == ({"name": "docx"}, "completed", "ok")
    assert trace.streamed_artifacts == [ArtifactRef("note.docx", "abc", 1)]
    assert trace.saved_artifacts == [ArtifactRef("note.docx", "abc", 2, "application/vnd.openxmlformats")]
    assert trace.citations[0]["citationData"]["metadata"]["recordId"] == "r9"
    assert trace.answer.startswith("Here it is.")


def test_a_root_error_ends_the_run_as_failed() -> None:
    frames = _frames()[:3] + [("RUN_ERROR", {"type": "RUN_ERROR", "message": "too long", "code": "request_too_large"})]
    trace = read_run(_FakeResponse(frames))

    assert not trace.finished
    assert trace.error == "too long"


def test_a_refused_request_keeps_its_status_and_body() -> None:
    trace = read_run(_FakeResponse([], status_code=400))

    assert trace.status_code == 400 and trace.error == "refused" and not trace.finished


def test_markers_without_an_id_or_version_are_handled() -> None:
    assert parse_artifact_markers("::artifact[a.txt](record:){text/plain|||CODE|}") == []
    assert parse_artifact_markers("::artifact[a.txt](u){text/plain|d1}") == [ArtifactRef("a.txt", "d1", None, "text/plain")]
