"""CM-1..CM-8 (E8): app-server notifications → ``EngineEvent``s, on transcripts recorded from 0.160.0.

The fixtures are every message ``codex-app-server`` 0.160.0 wrote to stdout in one probe (initialize
and thread/turn responses included), run with the adapter's rendered config against E3b's real model
gateway and E2's tool gateway; local paths and the host name were replaced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.controller import BUDGET_STOP_CODES
from harness_core.engines.events import (
    ApprovalRequested,
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineError,
    FileChanged,
    MessageCompleted,
    SubAgentFinished,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
)
from harness_engines.codex.mapper import ASKS, CodexEventMapper, answer_for, error_code

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent

FIXTURES = Path(__file__).parent / "fixtures"
TURN = "trn_conformance"
THREAD = "11111111-1111-1111-1111-111111111111"
CODEX_TURN = "22222222-2222-2222-2222-222222222222"


def _messages(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines() if line]


def _result(messages: list[dict[str, Any]], request_id: int) -> dict[str, Any]:
    return next(m["result"] for m in messages if m.get("id") == request_id and "method" not in m)


def replay(name: str, *, turn_request: int = 13) -> tuple[list[EngineEvent], CodexEventMapper]:
    """Feed a transcript as the session does: from the thread and turn the responses name."""
    messages = _messages(name)
    mapper = CodexEventMapper(thread_id=_result(messages, 12)["thread"]["id"], turn_id=TURN)
    mapper.begin(_result(messages, turn_request)["turn"]["id"])
    events: list[EngineEvent] = []
    for message in messages:
        if "method" in message and not mapper.done:
            events.extend(mapper.feed(message))
    return events, mapper


def _kinds(events: list[EngineEvent]) -> list[str]:
    return [type(e).__name__ for e in events]


def _mapper() -> CodexEventMapper:
    mapper = CodexEventMapper(thread_id=THREAD, turn_id=TURN)
    mapper.begin(CODEX_TURN)
    return mapper


def _note(method: str, **params: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"method": method, "params": {"threadId": THREAD, "turnId": CODEX_TURN, **params}}


def test_cm1_a_text_turn() -> None:
    events, mapper = replay("interrupt", turn_request=15)
    assert _kinds(events) == ["TextDelta", "MessageCompleted", "UsageReported", "TurnCompleted"]
    assert [e.meta.seq for e in events] == list(range(len(events)))
    assert {e.meta.turn_id for e in events} == {TURN}
    completed = events[-1]
    assert isinstance(completed, TurnCompleted)
    assert (completed.reason, completed.final_text) == ("completed", "never again")
    usage = events[2]
    assert isinstance(usage, UsageReported)
    assert (usage.input_tokens, usage.output_tokens, usage.sub_session_id) == (10, 5, None)
    assert mapper.done


def test_cm2_a_gateway_tool_call() -> None:
    events, _ = replay("tool")
    assert _kinds(events) == [
        "ToolCallSeen", "ToolCallSeen", "UsageReported", "TextDelta", "MessageCompleted", "UsageReported", "TurnCompleted"
    ]
    seen = [e for e in events if isinstance(e, ToolCallSeen)]
    assert [(e.tool, e.status) for e in seen] == [("search", "started"), ("search", "completed")]
    assert seen[0].call_id == seen[1].call_id


def test_cm2_a_failed_tool_call() -> None:
    mapper = _mapper()
    item = {"type": "mcpToolCall", "id": "call_9", "tool": "search", "status": "failed"}
    assert [e.status for e in mapper.feed(_note("item/completed", item=item)) if isinstance(e, ToolCallSeen)] == ["failed"]


def test_cm3_a_shell_command() -> None:
    events, _ = replay("shell")
    started = next(e for e in events if isinstance(e, CommandStarted))
    finished = next(e for e in events if isinstance(e, CommandFinished))
    assert "made.txt" in started.command
    assert (finished.call_id, finished.exit_code, finished.status) == (started.call_id, 0, "completed")


def test_cm3_command_output_and_a_failing_exit() -> None:
    mapper = _mapper()
    out = mapper.feed(_note("item/commandExecution/outputDelta", itemId="call_1", delta="hello\n"))
    assert out == [CommandOutput(out[0].meta, "call_1", "hello\n")]
    item = {"type": "commandExecution", "id": "call_1", "status": "failed", "exitCode": 2}
    (finished,) = mapper.feed(_note("item/completed", item=item))
    assert isinstance(finished, CommandFinished)
    assert (finished.exit_code, finished.status) == (2, "failed")


def test_cm4_file_changes() -> None:
    mapper = _mapper()
    changes = [
        {"path": "/workspace/a.txt", "kind": {"type": "add"}, "diff": ""},
        {"path": "/workspace/b.txt", "kind": {"type": "update", "move_path": None}, "diff": ""},
        {"path": "/workspace/c.txt", "kind": {"type": "delete"}, "diff": ""},
    ]
    events = mapper.feed(_note("item/completed", item={"type": "fileChange", "id": "p1", "status": "completed", "changes": changes}))
    assert [(e.path, e.change) for e in events if isinstance(e, FileChanged)] == [
        ("/workspace/a.txt", "added"),
        ("/workspace/b.txt", "modified"),
        ("/workspace/c.txt", "deleted"),
    ]


def test_cm5_a_sub_agent_whose_turn_outlives_the_parents() -> None:
    events, mapper = replay("spawn")
    started = [e for e in events if isinstance(e, SubAgentStarted)]
    finished = [e for e in events if isinstance(e, SubAgentFinished)]
    assert len(started) == 1 and len(finished) == 1
    assert (finished[0].sub_session_id, finished[0].call_id, finished[0].status) == (
        started[0].sub_session_id,
        started[0].call_id,
        "completed",
    )
    child_usage = [e for e in events if isinstance(e, UsageReported) and e.sub_session_id is not None]
    assert [e.sub_session_id for e in child_usage] == [started[0].sub_session_id]
    texts = [e.text for e in events if isinstance(e, MessageCompleted)]
    assert texts == ["top"]
    # In this recording the parent's turn ended first; its end is reported after the child's.
    assert _kinds(events)[-2:] == ["SubAgentFinished", "TurnCompleted"]
    assert [e.meta.seq for e in events] == list(range(len(events)))
    assert mapper.done


def test_cm6_an_interrupted_turn() -> None:
    events, _ = replay("interrupt")
    assert _kinds(events) == ["TurnCompleted"]
    assert isinstance(events[0], TurnCompleted) and events[0].reason == "interrupted"


def test_cm6_a_failed_turn_reports_its_error_once() -> None:
    mapper = _mapper()
    error = {"message": "secret upstream text", "codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 402}}}
    assert mapper.feed(_note("error", error=error, willRetry=True)) == []
    (reported,) = mapper.feed(_note("error", error=error, willRetry=False))
    assert isinstance(reported, EngineError) and reported.code == "budget_exhausted"
    assert "secret" not in reported.message
    turn = {"id": CODEX_TURN, "status": "failed", "error": error}
    events = mapper.feed({"method": "turn/completed", "params": {"threadId": THREAD, "turn": turn}})
    assert _kinds(events) == ["TurnCompleted"]
    assert isinstance(events[0], TurnCompleted) and events[0].reason == "failed"


def test_cm6_a_failed_turn_without_an_error_notification() -> None:
    mapper = _mapper()
    turn = {"id": CODEX_TURN, "status": "failed", "error": {"message": "x", "codexErrorInfo": "usageLimitExceeded"}}
    events = mapper.feed({"method": "turn/completed", "params": {"threadId": THREAD, "turn": turn}})
    assert _kinds(events) == ["EngineError", "TurnCompleted"]
    assert isinstance(events[0], EngineError) and events[0].code == "usage_limit_exceeded"


@pytest.mark.parametrize(
    ("info", "code"),
    [
        ("contextWindowExceeded", "context_window_exceeded"),
        ({"httpConnectionFailed": {"httpStatusCode": 503}}, "http_503"),
        ({"httpConnectionFailed": {"httpStatusCode": 402}}, "budget_exhausted"),
        ({"responseStreamDisconnected": {"httpStatusCode": None}}, "response_stream_disconnected"),
        (None, "engine_error"),
        ("Not A Code!", "engine_error"),
    ],
)
def test_cm6_error_codes_are_short_identifiers(info: Any, code: str) -> None:  # noqa: ANN401
    assert error_code({"message": "free text", "codexErrorInfo": info}) == code


@pytest.mark.parametrize(
    ("method", "params", "approved", "denied"),
    [
        ("item/commandExecution/requestApproval", {"command": "rm -rf x"}, {"decision": "accept"}, {"decision": "decline"}),
        ("item/fileChange/requestApproval", {}, {"decision": "accept"}, {"decision": "decline"}),
        ("item/permissions/requestApproval", {"permissions": {"network": {"enabled": True}}},
         {"permissions": {"network": {"enabled": True}}, "scope": "turn"}, {"permissions": {}}),
        ("mcpServer/elicitation/request", {"serverName": "tools"}, {"action": "accept"}, {"action": "decline"}),
        ("item/tool/requestUserInput", {"questions": []}, {"answers": {}}, {"answers": {}}),
        ("execCommandApproval", {}, {"decision": "approved"}, {"decision": "denied"}),
        ("applyPatchApproval", {}, {"decision": "approved"}, {"decision": "denied"}),
    ],
)
def test_cm7_codexs_own_asks_become_approval_requests(method: str, params: dict[str, Any], approved: Any, denied: Any) -> None:  # noqa: ANN401
    mapper = _mapper()
    (asked,) = mapper.feed({"id": 41, "method": method, "params": {"threadId": THREAD, "turnId": CODEX_TURN, "itemId": "call_3", **params}})
    assert isinstance(asked, ApprovalRequested)
    assert (asked.request_id, asked.permission, asked.call_id) == ("41", ASKS[method].permission, "call_3")
    assert asked.patterns == ((params["command"],) if "command" in params else ())
    assert answer_for(method, params, "approved") == approved
    assert answer_for(method, params, "denied") == denied
    assert answer_for(method, params, "expired") == denied


def test_cm7_server_requests_that_are_not_asks_map_to_nothing() -> None:
    mapper = _mapper()
    for method in ("item/tool/call", "account/chatgptAuthTokens/refresh", "attestation/generate"):
        assert mapper.feed({"id": 5, "method": method, "params": {"threadId": THREAD, "turnId": CODEX_TURN}}) == []


def test_cm8_other_threads_and_other_turns_are_ignored() -> None:
    mapper = _mapper()
    delta = {"itemId": "m", "delta": "x"}
    assert mapper.feed({"method": "item/agentMessage/delta", "params": {"threadId": "other", "turnId": CODEX_TURN, **delta}}) == []
    assert mapper.feed({"method": "item/agentMessage/delta", "params": {"threadId": THREAD, "turnId": "older", **delta}}) == []
    (event,) = mapper.feed(_note("item/agentMessage/delta", **delta))
    assert isinstance(event, TextDelta) and event.meta.seq == 0


def test_cm6_the_gateways_402_is_a_code_the_controller_stops_on() -> None:
    """P6.5: the ledger answers 402 ``budget_exhausted`` or ``budget_insufficient``; Codex shows only the 402."""
    info = {"httpConnectionFailed": {"httpStatusCode": 402}}
    assert error_code({"message": "x", "codexErrorInfo": info}) in BUDGET_STOP_CODES


def test_cm9_a_completion_carries_its_whole_text_whatever_arrived_before_it() -> None:
    """The opencode race (completion before its text, 2d6dae4d9) cannot happen here: Codex's ``item/completed``
    carries the item's whole text, and nothing is deduplicated, so no completion is dropped."""
    mapper = _mapper()
    item = {"type": "agentMessage", "id": "msg_1", "text": "final words"}
    (completed,) = mapper.feed(_note("item/completed", item=item))
    assert isinstance(completed, MessageCompleted) and completed.text == "final words"
    late = mapper.feed(_note("item/agentMessage/delta", itemId="msg_1", delta="final words"))
    assert [type(e).__name__ for e in late] == ["TextDelta"]
    turn = {"id": CODEX_TURN, "status": "completed"}
    (ended,) = mapper.feed({"method": "turn/completed", "params": {"threadId": THREAD, "turn": turn}})
    assert isinstance(ended, TurnCompleted) and ended.final_text == "final words"
    assert [e.meta.seq for e in (completed, *late, ended)] == [0, 1, 2]


@pytest.mark.parametrize(
    ("message", "info", "code"),
    [
        # What Codex 0.160 sent for the guard's refusal on the real lane (RL-3), with the gateway's tag.
        (
            "unexpected status 403 Forbidden: the pinned key belongs to another user [gateway:account_not_owned], "
            "url: http://model-gateway:8097/v1/responses",
            {"httpConnectionFailed": {"httpStatusCode": 403}},
            "account_not_owned",
        ),
        ("unexpected status 402: spent [gateway:budget_insufficient]", {"httpConnectionFailed": {"httpStatusCode": 402}}, "budget_insufficient"),
        ("unexpected status 403 Forbidden: not ours", {"httpConnectionFailed": {"httpStatusCode": 403}}, "http_403"),
        ("x [gateway:Not A Code]", {"httpConnectionFailed": {"httpStatusCode": 400}}, "http_400"),
    ],
)
def test_cm6_a_gateway_refusal_is_its_code_not_its_status(message: str, info: Any, code: str) -> None:  # noqa: ANN401
    assert error_code({"message": message, "codexErrorInfo": info}) == code


def test_ctresults_a_command_whose_output_never_streamed_shows_its_aggregated_output() -> None:
    """Codex: ``echo`` ran with exit 0 and the chat's command row had no output at all."""
    mapper = _mapper()
    item = {"type": "commandExecution", "id": "call_2", "status": "completed", "exitCode": 0, "aggregatedOutput": "hi\n"}
    output, finished = mapper.feed(_note("item/completed", item=item))
    assert output == CommandOutput(output.meta, "call_2", "hi\n")
    assert isinstance(finished, CommandFinished) and finished.status == "completed"
    _ = mapper.feed(_note("item/commandExecution/outputDelta", itemId="call_3", delta="streamed\n"))
    item = {"type": "commandExecution", "id": "call_3", "status": "completed", "exitCode": 0, "aggregatedOutput": "streamed\n"}
    assert _kinds(mapper.feed(_note("item/completed", item=item))) == ["CommandFinished"]


_HOOK_RUN = {
    "id": "hook-1", "eventName": "preToolUse", "handlerType": "command", "executionMode": "sync", "scope": "managed",
    "sourcePath": "/etc/codex/hooks.json", "displayOrder": 0, "status": "completed", "statusMessage": None,
    "startedAt": 1, "completedAt": 2, "durationMs": 1, "entries": [],
}
# What Codex 0.160.0 sent in one recorded run (44 in one turn) that the mapper logged as unknown (EV-13).
_QUIET_IN_RUN_2 = [
    _note("hook/started", run={**_HOOK_RUN, "status": "running"}),
    _note("hook/completed", run=_HOOK_RUN),
    _note("hook/completed", run={**_HOOK_RUN, "status": "blocked", "entries": [{"kind": "feedback", "text": "no"}]}),
    _note("item/commandExecution/terminalInteraction", itemId="call_1", processId="7", stdin="hunter2\n"),
    {"method": "skills/changed", "params": {}},
]


@pytest.mark.parametrize("message", _QUIET_IN_RUN_2, ids=lambda m: f"{m['method']}")
def test_wp65_native_1_hooks_skills_and_terminal_input_are_known_quiet_not_unknown(message: dict[str, Any]) -> None:
    """Each is the run's own policy hook, a skills notice, or a command's stdin (never shown: it can be a secret);
    none is an unknown kind, so none is kept sealed or counted in ``engine.native.unknown``."""
    mapper = _mapper()
    assert mapper.feed(message) == []
    assert not mapper.done


def test_wp65_native_2_typed_input_never_reaches_the_activity_while_the_command_output_does() -> None:
    mapper = _mapper()
    events = [
        *mapper.feed(_note("item/started", item={"type": "commandExecution", "id": "call_1", "command": "login"})),
        *mapper.feed(_note("item/commandExecution/outputDelta", itemId="call_1", delta="Password: ")),
        *mapper.feed(_note("item/commandExecution/terminalInteraction", itemId="call_1", processId="7", stdin="s3cr3t\n")),
        *mapper.feed(_note("item/commandExecution/outputDelta", itemId="call_1", delta="ok\n")),
    ]
    assert _kinds(events) == ["CommandStarted", "CommandOutput", "CommandOutput"]
    assert all("s3cr3t" not in getattr(e, "text", "") for e in events)
