"""CM-1..CM-8 (E9): Claude Code's stream-json messages → engine events, on transcripts recorded from 2.1.289.

The fixtures are the binary's stdout for one scripted turn each (``fixtures/provenance.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.engines.controller import BUDGET_STOP_CODES
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineError,
    EngineEvent,
    FileChanged,
    MessageCompleted,
    ReasoningDelta,
    SubAgentFinished,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
)
from harness_engines.claude_code.mapper import ClaudeEventMapper, error_code

FIXTURES = Path(__file__).parent / "fixtures"
TURN = "trn_e9"


def _messages(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines() if line.strip()]


def _map(name: str, *, interrupt: bool = False) -> tuple[list[EngineEvent], ClaudeEventMapper]:
    mapper = ClaudeEventMapper(turn_id=TURN)
    if interrupt:
        mapper.mark_interrupted()
    events: list[EngineEvent] = []
    for message in _messages(name):
        events += mapper.feed(message)
        if mapper.done:
            break
    return events, mapper


def _kinds(events: list[EngineEvent]) -> list[str]:
    return [type(e).__name__ for e in events]


def _check_seq(events: list[EngineEvent]) -> None:
    assert [e.meta.seq for e in events] == list(range(len(events)))
    assert {e.meta.turn_id for e in events} == {TURN}


def test_cm1_a_text_turn() -> None:
    events, mapper = _map("text")
    _check_seq(events)
    assert _kinds(events) == ["TextDelta", "MessageCompleted", "UsageReported", "TurnCompleted"]
    text, completed, usage, ended = events
    assert isinstance(text, TextDelta) and isinstance(completed, MessageCompleted)
    assert text.text == completed.text == "hello there" and text.message_id == completed.message_id
    assert isinstance(usage, UsageReported)
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (100, 7, 3, 2)
    assert usage.sub_session_id is None
    assert ended == TurnCompleted(ended.meta, "completed", "hello there")
    assert mapper.done and mapper.feed({"type": "assistant"}) == []


def test_cm1_a_turn_with_a_steer_waiting_runs_on_until_it_is_taken_in_or_dropped() -> None:
    mapper = ClaudeEventMapper(turn_id=TURN, steers_waiting=True)
    first = [e for m in _messages("text") for e in mapper.feed(m)]
    assert _kinds(first) == ["TextDelta", "MessageCompleted", "UsageReported"] and not mapper.done
    ended = mapper.finish()
    assert ended == [TurnCompleted(ended[0].meta, "completed", "hello there")] and mapper.done
    mapper = ClaudeEventMapper(turn_id=TURN, steers_waiting=True)
    mapper.mark_interrupted()
    assert _kinds([e for m in _messages("interrupt") for e in mapper.feed(m)]) == ["TurnCompleted"]


def test_cm1_noise_is_ignored() -> None:
    mapper = ClaudeEventMapper(turn_id=TURN)
    for noise in (
        {"type": "system", "subtype": "init", "session_id": "x"},
        {"type": "control_response", "response": {}},
        {"type": "system", "subtype": "background_tasks_changed"},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}},
        {"type": "rate_limit_event"},
    ):
        assert mapper.feed(noise) == []


def test_cm2_gateway_tools_by_their_granted_name() -> None:
    events, _ = _map("mcp")
    _check_seq(events)
    seen = [(e.tool, e.status) for e in events if isinstance(e, ToolCallSeen)]
    assert seen == [("search", "started"), ("search", "completed"), ("broken", "started"), ("broken", "failed")]


def test_cm3_shell_commands() -> None:
    events, _ = _map("bash")
    _check_seq(events)
    commands = [e for e in events if isinstance(e, CommandStarted | CommandOutput | CommandFinished)]
    assert _kinds(commands) == ["CommandStarted", "CommandOutput", "CommandFinished"] * 2
    first, failed_output, failed, second, output, finished = commands
    assert isinstance(first, CommandStarted) and first.command == "echo hi; exit 3"
    assert isinstance(failed_output, CommandOutput) and "hi" in failed_output.text
    assert isinstance(failed, CommandFinished) and (failed.exit_code, failed.status) == (3, "failed")
    assert isinstance(second, CommandStarted) and second.command == "echo ok"
    assert isinstance(output, CommandOutput) and output.text == "ok"
    assert isinstance(finished, CommandFinished) and (finished.exit_code, finished.status) == (0, "completed")
    assert first.call_id == failed.call_id != second.call_id == finished.call_id


def test_cm4_file_tools_change_files_only_when_they_succeed() -> None:
    events, _ = _map("write")
    _check_seq(events)
    changes = [(e.path, e.change) for e in events if isinstance(e, FileChanged)]
    assert changes == [("notes.md", "added"), ("notes.md", "modified"), ("notes.md", "modified")]
    seen = [(e.tool, e.status) for e in events if isinstance(e, ToolCallSeen)]
    assert seen[-2:] == [("Edit", "started"), ("Edit", "failed")]


def test_cm5_a_sub_agent_is_its_own_session_and_its_text_is_not_the_parents() -> None:
    events, _ = _map("agent")
    _check_seq(events)
    started = [e for e in events if isinstance(e, SubAgentStarted)]
    finished = [e for e in events if isinstance(e, SubAgentFinished)]
    assert len(started) == len(finished) == 1
    assert (started[0].agent, finished[0].status) == ("helper", "completed")
    assert started[0].sub_session_id == finished[0].sub_session_id and started[0].call_id == finished[0].call_id
    child_usage = [e for e in events if isinstance(e, UsageReported) and e.sub_session_id is not None]
    assert [u.sub_session_id for u in child_usage] == [started[0].sub_session_id]
    texts = [e.text for e in events if isinstance(e, MessageCompleted)]
    assert texts == ["top"]
    # A helper's text and thinking reach the stream only with --forward-subagent-text; if they do, they stay the
    # helper's. Its tool calls always do (EV-07, test_ev07_helper_steps).
    child = {"type": "assistant", "parent_tool_use_id": started[0].call_id,
             "message": {"id": "m-child", "content": [{"type": "text", "text": "deep"}, {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}}
    assert ClaudeEventMapper(turn_id=TURN).feed(child) == []
    ended = events[-1]
    assert isinstance(ended, TurnCompleted) and ended.final_text == "top"


@pytest.mark.parametrize("interrupt", [True, False])
def test_cm6_an_interrupted_turn(interrupt: bool) -> None:
    events, _ = _map("interrupt", interrupt=interrupt)
    assert _kinds(events) == ["TurnCompleted"]
    assert isinstance(events[0], TurnCompleted) and events[0].reason == "interrupted"


def test_cm6_the_model_gateways_402_is_a_budget_stop_and_claude_codes_own_error_text_is_not_the_answer() -> None:
    events, _ = _map("error")
    assert _kinds(events) == ["EngineError", "TurnCompleted"]
    error, ended = events
    assert isinstance(error, EngineError) and error.code == "budget_exhausted" and error.code in BUDGET_STOP_CODES
    assert isinstance(ended, TurnCompleted) and (ended.reason, ended.final_text) == ("failed", "")


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"api_error_status": 429}, "http_429"),
        # FB-3: the model gateway names its code in the error text Claude Code puts in the result.
        ({"api_error_status": 403, "result": "API Error: 403 not yours [gateway:account_not_owned]"}, "account_not_owned"),
        ({"api_error_status": 402, "result": "API Error: 402 spent [gateway:budget_insufficient]"}, "budget_insufficient"),
        ({"api_error_status": 400, "result": "API Error: 400 x [gateway:Not A Code]"}, "http_400"),
        ({"api_error_status": 402}, "budget_exhausted"),
        ({"subtype": "error_max_turns"}, "max_turns"),
        ({"subtype": "error_during_execution"}, "during_execution"),
        ({"subtype": "success", "terminal_reason": "api_error"}, "api_error"),
        ({"subtype": "error_NOT a code!"}, "engine_error"),
        ({}, "engine_error"),
    ],
)
def test_cm6_error_codes_never_carry_messages(result: dict[str, Any], code: str) -> None:
    assert error_code({"result": "a message that must not travel", **result}) == code


@pytest.mark.parametrize(
    ("models", "expected"),
    [
        ({"engine-model": {"costBasis": "unknown"}}, None),
        ({}, None),
        ({"claude-x": {"costBasis": "list_price"}}, 0.25),
        ({"claude-x": {"costBasis": "list_price"}, "engine-model": {"costBasis": "unknown"}}, None),
    ],
)
def test_cm7_cost_only_when_claude_code_knows_the_price(models: dict[str, Any], expected: float | None) -> None:
    mapper = ClaudeEventMapper(turn_id=TURN)
    events = mapper.feed(
        {"type": "result", "subtype": "success", "is_error": False, "result": "x", "total_cost_usd": 0.25,
         "usage": {"input_tokens": 1, "output_tokens": 1}, "modelUsage": models}
    )
    usage = events[0]
    assert isinstance(usage, UsageReported) and usage.cost_usd == expected


def test_cm8_a_denied_native_permission_is_counted_never_an_approval() -> None:
    events, mapper = _map("denied")
    assert mapper.denied == 1
    assert not [e for e in events if type(e).__name__ == "ApprovalRequested"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"


def test_thinking_is_reasoning() -> None:
    mapper = ClaudeEventMapper(turn_id=TURN)
    events = mapper.feed(
        {"type": "assistant", "message": {"id": "m", "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "thinking", "thinking": ""}]}}
    )
    assert events == [ReasoningDelta(events[0].meta, "m", "hmm")]


def test_mg10_claude_codes_prompt_too_long_is_the_gateways_overflow_code() -> None:
    """Recorded from 2.1.294 on the real lane (cf21): the gateway's overflow comes back as Claude Code's own words."""
    result = {
        "type": "result", "subtype": "success", "is_error": True, "api_error_status": 400,
        "terminal_reason": "prompt_too_long",
        "result": "Prompt is too long · this conversation is a single exchange and cannot be compacted",
    }
    assert error_code(result) == "context_length_exceeded"


@pytest.mark.parametrize(
    ("content", "detail", "shown"),
    [
        # Claude Code 2.1.294 on the real binary (conformance ``test_cf8_a_failed_command_reads_failed_with_its_exit_code``).
        ("Exit code 3\nout-r32\nerr-r32", "Error: Exit code 3\nout-r32\nerr-r32", ["out-r32\nerr-r32"]),
        ("Error: Exit code 1", None, []),
        # A failed result that does not lead with the status line is shown whole.
        ("permission denied\nExit code 2", None, ["permission denied\nExit code 2"]),
    ],
)
def test_r32engfu_a_failed_commands_status_line_is_its_status_not_its_output(
    content: str, detail: Any, shown: list[str]  # noqa: ANN401
) -> None:
    mapper = ClaudeEventMapper(turn_id=TURN)
    mapper.feed({"type": "assistant", "message": {"id": "m1", "content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "exit 3"}}]}})
    message: dict[str, Any] = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": content}]}}
    if detail is not None:
        message["tool_use_result"] = detail
    events = mapper.feed(message)
    (finished,) = [e for e in events if isinstance(e, CommandFinished)]
    assert finished.status == "failed"
    assert [e.text for e in events if isinstance(e, CommandOutput)] == shown
