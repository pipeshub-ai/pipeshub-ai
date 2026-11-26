"""OC-5..OC-8: the event mapper on the probe captures."""

from __future__ import annotations

import json
from pathlib import Path

from harness_core.engines.events import (
    ApprovalRequested,
    CommandFinished,
    CommandStarted,
    EngineError,
    EngineEvent,
    FileChanged,
    MessageCompleted,
    SubAgentFinished,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
)
from harness_engines.opencode.mapper import OpencodeEventMapper

FIXTURES = Path(__file__).parent / "fixtures"


def _replay(name: str, *, session: str = "ses_001", stop_at_done: bool = True) -> list[EngineEvent]:
    mapper = OpencodeEventMapper(session_id=session, turn_id="trn_1")
    out: list[EngineEvent] = []
    for line in (FIXTURES / name).read_text().splitlines():
        out.extend(mapper.feed(json.loads(line)))
        if mapper.done and stop_at_done:
            break
    return out


def _kinds(events: list[EngineEvent]) -> list[str]:
    return [type(e).__name__ for e in events]


def test_oc5_mcp_call() -> None:
    events = _replay("native-locked-mcp_call.events.jsonl")
    assert _kinds(events) == [
        "ToolCallSeen",
        "ToolCallSeen",
        "UsageReported",
        "TextDelta",
        "UsageReported",
        "MessageCompleted",
        "TurnCompleted",
    ]
    assert [e.meta.seq for e in events] == list(range(len(events)))
    seen = [e for e in events if isinstance(e, ToolCallSeen)]
    assert [(e.tool, e.status) for e in seen] == [
        ("pipeshub_pipeshub_search", "started"),
        ("pipeshub_pipeshub_search", "completed"),
    ]
    usage = [e for e in events if isinstance(e, UsageReported)]
    assert [(u.input_tokens, u.output_tokens) for u in usage] == [(10, 5), (10, 5)]
    completed = [e for e in events if isinstance(e, MessageCompleted)]
    assert len(completed) == 1
    assert completed[0].text.startswith("DONE:RESULT for")
    deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert deltas == completed[0].text
    final = events[-1]
    assert isinstance(final, TurnCompleted)
    assert (final.reason, final.final_text) == ("completed", completed[0].text)


def test_oc6_bash_with_approval() -> None:
    events = _replay("native-locked-bash_ask.events.jsonl")
    # The command printed nothing: opencode's "(no output)" is no output step (R25OPS-FU-2).
    assert _kinds(events)[:4] == [
        "CommandStarted",
        "ApprovalRequested",
        "CommandFinished",
        "UsageReported",
    ]
    started = events[0]
    assert isinstance(started, CommandStarted)
    assert started.command == "echo probe > probe.txt"
    ask = events[1]
    assert isinstance(ask, ApprovalRequested)
    assert (ask.request_id, ask.permission, ask.patterns, ask.call_id) == (
        "per_001",
        "bash",
        ("echo probe > probe.txt",),
        "call_1",
    )
    finished = events[2]
    assert isinstance(finished, CommandFinished)
    assert (finished.exit_code, finished.status) == (0, "completed")


def test_oc7_sub_agent_on_the_same_stream() -> None:
    events = _replay("native-default-task.events.jsonl")
    started = [e for e in events if isinstance(e, SubAgentStarted)]
    finished = [e for e in events if isinstance(e, SubAgentFinished)]
    assert [(e.sub_session_id, e.agent) for e in started] == [("ses_002", "general")]
    assert [(e.sub_session_id, e.status) for e in finished] == [("ses_002", "completed")]
    child_usage = [e for e in events if isinstance(e, UsageReported) and e.sub_session_id == "ses_002"]
    assert child_usage
    parent_text = [e for e in events if isinstance(e, MessageCompleted)]
    assert all(m.text.startswith("DONE:") for m in parent_text)
    assert isinstance(events[-1], TurnCompleted)


def test_oc8_denied_task_is_a_failed_tool_call() -> None:
    events = _replay("native-locked-task.events.jsonl")
    seen = [(e.tool, e.status) for e in events if isinstance(e, ToolCallSeen)]
    assert seen == [("task", "started"), ("task", "failed")]
    assert not [e for e in events if isinstance(e, SubAgentStarted)]


def test_oc8_other_sessions_noise_and_pre_busy_events_are_ignored() -> None:
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id="trn_1")
    noise = [
        {"type": "plugin.added", "properties": {}},
        {"type": "catalog.updated", "properties": {}},
        {"type": "session.idle", "properties": {"sessionID": "ses_001"}},
        {"type": "message.part.delta", "properties": {"sessionID": "ses_009", "partID": "p", "field": "text", "delta": "x"}},
        {"type": "session.status", "properties": {"sessionID": "ses_009", "status": {"type": "busy"}}},
    ]
    assert [e for payload in noise for e in mapper.feed(payload)] == []
    assert not mapper.done


def test_oc8_second_turn_of_a_capture_maps_on_its_own() -> None:
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id="trn_2")
    lines = (FIXTURES / "docker-resume-src.events.jsonl").read_text().splitlines()
    first_idle = next(i for i, line in enumerate(lines) if json.loads(line)["type"] == "session.idle")
    events: list[EngineEvent] = []
    for line in lines[first_idle + 1 :]:
        events.extend(mapper.feed(json.loads(line)))
    assert _kinds(events)[0] == "ToolCallSeen"
    assert isinstance(events[-1], TurnCompleted)


def test_session_error_and_abort() -> None:
    mapper = OpencodeEventMapper(session_id="s", turn_id="t")
    busy = {"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}}
    error = {"type": "session.error", "properties": {"sessionID": "s", "error": {"name": "ProviderAuthError"}}}
    idle = {"type": "session.idle", "properties": {"sessionID": "s"}}
    events = [e for p in (busy, error, idle) for e in mapper.feed(p)]
    assert isinstance(events[0], EngineError)
    assert events[0].code == "ProviderAuthError"
    assert isinstance(events[1], TurnCompleted)
    assert events[1].reason == "failed"
    aborted = OpencodeEventMapper(session_id="s", turn_id="t")
    message = {
        "type": "message.updated",
        "properties": {"sessionID": "s", "info": {"id": "m", "role": "assistant", "error": {"name": "MessageAbortedError"}}},
    }
    events = [e for p in (busy, message, idle) for e in aborted.feed(p)]
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].reason == "interrupted"


def test_oc18_a_gateway_refusal_maps_to_its_reason_code() -> None:
    """Found on the real binary: a 402 from the model gateway is an APIError with the gateway's envelope."""
    from harness_engines.opencode.mapper import error_code

    body = json.dumps({"error": {"message": "spent", "type": "insufficient_quota", "code": "budget_exhausted"}})
    payload = {"sessionID": "s", "error": {"name": "APIError", "data": {"statusCode": 402, "responseBody": body}}}
    assert error_code(payload) == "budget_exhausted"
    odd = {"error": {"name": "APIError", "data": {"statusCode": 500, "responseBody": json.dumps({"error": {"code": "Has Spaces and text"}})}}}
    assert error_code(odd) == "http_500"
    assert error_code({"error": {"name": "APIError", "data": {"statusCode": 502, "responseBody": "<html>"}}}) == "http_502"
    assert error_code({"error": {"name": "ProviderAuthError"}}) == "ProviderAuthError"


def _turn(*payloads: dict[str, object]) -> list[EngineEvent]:
    mapper = OpencodeEventMapper(session_id="s", turn_id="t")
    busy = {"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}}
    return [e for p in (busy, *payloads) for e in mapper.feed(p)]


_DONE = {"type": "message.updated", "properties": {"sessionID": "s", "info": {"id": "m", "role": "assistant", "time": {"completed": 1}}}}
_TEXT = {"type": "message.part.updated", "properties": {"sessionID": "s", "part": {"type": "text", "id": "p", "messageID": "m", "text": "the answer"}}}
_IDLE = {"type": "session.idle", "properties": {"sessionID": "s"}}


def test_oc19_a_completion_reported_before_its_text_is_emitted_once_with_the_text() -> None:
    """Found on the real binary under load (CF-6): the first completed update preceded the text part."""
    events = _turn(_DONE, _TEXT, _DONE, _IDLE)
    completed = [e for e in events if isinstance(e, MessageCompleted)]
    assert [c.text for c in completed] == ["the answer"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].final_text == "the answer"
    assert [e.meta.seq for e in events] == list(range(len(events)))


def test_oc19_a_completion_never_repeated_is_emitted_when_the_turn_ends() -> None:
    events = _turn(_DONE, _TEXT, _IDLE)
    assert _kinds(events)[-2:] == ["MessageCompleted", "TurnCompleted"]
    assert events[-1].final_text == "the answer"  # type: ignore[attr-defined]



def _file_changes(tool: str, tool_input: dict[str, object], metadata: dict[str, object]) -> list[tuple[str, str]]:
    """FU2-2: one completed write tool, shaped as opencode 1.18.34 reports it, through the mapper."""
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id="trn_1")
    part = {"id": "prt_9", "messageID": "msg_9", "sessionID": "ses_001", "type": "tool", "tool": tool, "callID": "call_9"}
    events = mapper.feed({"type": "session.status", "properties": {"sessionID": "ses_001", "status": {"type": "busy"}}})
    for state in (
        {"status": "running", "input": tool_input},
        {"status": "completed", "input": tool_input, "output": "ok", "metadata": metadata, "title": ""},
    ):
        events += mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "ses_001", "part": {**part, "state": state}}})
    return [(e.path, e.change) for e in events if isinstance(e, FileChanged)]


def test_fu2_a_write_of_a_new_file_is_added_and_over_an_existing_one_modified() -> None:
    write = {"filePath": "/workspace/notes.md", "content": "hello"}
    assert _file_changes("write", write, {"filepath": "/workspace/notes.md", "exists": False}) == [("notes.md", "added")]
    assert _file_changes("write", write, {"filepath": "/workspace/notes.md", "exists": True}) == [("notes.md", "modified")]
    edit = {"filePath": "/workspace/notes.md", "oldString": "a", "newString": "b"}
    assert _file_changes("edit", edit, {"diff": ""}) == [("notes.md", "modified")]


def test_fu2_apply_patch_reports_each_file_it_lists_not_an_empty_path() -> None:
    files = [
        {"filePath": "/workspace/new.md", "type": "add"},
        {"filePath": "/workspace/old.md", "type": "update"},
        {"filePath": "/workspace/gone.md", "type": "delete"},
        {"filePath": "/workspace/a.md", "movePath": "/workspace/b.md", "type": "move"},
    ]
    assert _file_changes("apply_patch", {"patchText": "*** Begin Patch"}, {"files": files}) == [
        ("new.md", "added"),
        ("old.md", "modified"),
        ("gone.md", "deleted"),
        ("b.md", "modified"),
    ]


def _overflow_turn(*middle: dict[str, object]) -> list[object]:
    mapper = OpencodeEventMapper(session_id="s", turn_id="t")
    busy = {"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}}
    overflow = {"type": "session.error", "properties": {"sessionID": "s", "error": {"name": "ContextOverflowError"}}}
    idle = {"type": "session.idle", "properties": {"sessionID": "s"}}
    return [e for p in (busy, overflow, *middle, idle) for e in mapper.feed(p)]  # type: ignore[arg-type]


def test_mg10_an_overflow_opencode_compacted_away_is_no_error() -> None:
    compacted = {"type": "session.compacted", "properties": {"sessionID": "s"}}
    events = _overflow_turn(compacted)
    assert [type(e).__name__ for e in events] == ["ContextCompacted", "TurnCompleted"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"


def test_mg10_an_overflow_opencode_did_not_compact_fails_the_turn_as_the_gateways_code() -> None:
    events = _overflow_turn()
    assert [(type(e).__name__, getattr(e, "code", None)) for e in events] == [
        ("EngineError", "context_length_exceeded"), ("TurnCompleted", None),
    ]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "failed"
    assert [e.meta.seq for e in events] == [0, 1]  # type: ignore[attr-defined]
