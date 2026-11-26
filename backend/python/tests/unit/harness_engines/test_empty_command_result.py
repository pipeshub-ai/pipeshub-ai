"""R25OPS-FU-2: a shell command that printed nothing reads the same on every engine.

Each engine says it its own way (opencode's bash tool returns the text "(no output)", Claude Code an empty stdout,
Codex an empty aggregated output, an ACP agent no content); every mapper turns that into no ``CommandOutput`` step, so
the job panel says "No output." in the reader's language (``activityPanel.command.noOutput``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.events import (
    EMPTY_RESULT_PLACEHOLDERS,
    CommandFinished,
    CommandOutput,
    CommandStarted,
    command_output,
)
from harness_core.engines.stream import TurnSeq
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper

from .acp.test_file_changes import POLICY

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent

HERE = Path(__file__).parent


def _commands(events: list[EngineEvent]) -> list[str]:
    return [type(e).__name__ for e in events if isinstance(e, CommandStarted | CommandOutput | CommandFinished)]


def _gapless(events: list[EngineEvent]) -> None:
    assert [e.meta.seq for e in events] == list(range(len(events)))


def _opencode(output: str) -> list[EngineEvent]:
    """opencode 1.18.34's own capture (the probe's bash turn) with the command's result set to ``output``."""
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id="trn_1")
    events: list[EngineEvent] = []
    for line in (HERE / "opencode/fixtures/native-locked-bash_ask.events.jsonl").read_text().splitlines():
        event = json.loads(line)
        state = event.get("properties", {}).get("part", {}).get("state", {})
        if state.get("status") == "completed" and event["properties"]["part"].get("tool") == "bash":
            state["output"] = state["metadata"]["output"] = output
        events.extend(mapper.feed(event))
        if mapper.done:
            break
    return events


def _claude_code(stdout: str) -> list[EngineEvent]:
    mapper = ClaudeEventMapper(turn_id="trn_cc")
    use = {"type": "assistant", "message": {"id": "m1", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "true"}}]}}
    result = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": stdout, "is_error": False}]},
        "tool_use_result": {"interrupted": False, "isImage": False, "stderr": "", "stdout": stdout}}
    return [*mapper.feed(use), *mapper.feed(result)]


def _codex(output: str) -> list[EngineEvent]:
    mapper = CodexEventMapper(thread_id="thr_1", turn_id="trn_cx")
    mapper.begin("turn_1")

    def note(method: str, **params: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"method": method, "params": {"threadId": "thr_1", "turnId": "turn_1", **params}}

    item = {"type": "commandExecution", "id": "call_1", "command": "true", "status": "inProgress"}
    done = {**item, "status": "completed", "exitCode": 0, "aggregatedOutput": output}
    return [*mapper.feed(note("item/started", item=item)), *mapper.feed(note("item/completed", item=done))]


def _acp(output: str) -> list[EngineEvent]:
    mapper = AcpEventMapper(turn_id="trn_acp", policy=POLICY)
    call = {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash", "kind": "execute",
            "status": "in_progress", "rawInput": {"command": "true"}}
    done = {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
            "rawOutput": {"output": output, "metadata": {"exit": 0}}}
    return [*mapper.update(call), *mapper.update(done)]


ENGINES = {"opencode": _opencode, "claude_code": _claude_code, "codex": _codex, "acp": _acp}


@pytest.mark.parametrize("engine", sorted(ENGINES))
@pytest.mark.parametrize("empty", ["", "(no output)", "(no output)\n"])
def test_efu2_an_empty_result_is_no_output_step_on_every_engine(engine: str, empty: str) -> None:
    events = ENGINES[engine](empty)
    _gapless(events)
    commands = _commands(events)
    assert commands == ["CommandStarted", "CommandFinished"], (engine, commands)
    finished = next(e for e in events if isinstance(e, CommandFinished))
    assert (finished.exit_code, finished.status) == (0, "completed")


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_efu2_a_result_with_text_is_still_shown_on_every_engine(engine: str) -> None:
    events = ENGINES[engine]("4\n")
    _gapless(events)
    assert _commands(events) == ["CommandStarted", "CommandOutput", "CommandFinished"]
    assert next(e for e in events if isinstance(e, CommandOutput)).text == "4\n"


def test_efu2_the_helper_takes_a_seq_only_for_a_step() -> None:
    seq = TurnSeq("trn")
    assert command_output(seq.next, "c1", "") == []
    assert command_output(seq.next, "c1", "(no output)") == []
    shown = command_output(seq.next, "c1", "x", "stderr")
    assert [(e.meta.seq, e.stream) for e in shown if isinstance(e, CommandOutput)] == [(0, "stderr")]
    assert "(no output)" in EMPTY_RESULT_PLACEHOLDERS
