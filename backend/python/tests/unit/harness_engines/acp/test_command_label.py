"""CTRESULTS (opencode over ACP): a command step names the command it ran, not its tool's title.

opencode announces a shell call with ``title: "bash"`` and an empty ``rawInput``, then sends the command in a
``tool_call_update``; the step was started on the first and read "Ran bash" for every command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.events import CommandFinished, CommandOutput, CommandStarted
from harness_engines.acp.mapper import AcpEventMapper

from .test_file_changes import POLICY

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue


def _call(**update: JsonValue) -> dict[str, JsonValue]:
    return {"sessionUpdate": "tool_call_update", "toolCallId": "c1", **update}


def test_a_command_announced_before_its_input_is_named_by_its_input() -> None:
    mapper = AcpEventMapper(turn_id="t-1", policy=POLICY)
    events = mapper.update({**_call(title="bash", kind="execute", status="pending", rawInput={}), "sessionUpdate": "tool_call"})
    assert events == []
    events = mapper.update(_call(status="in_progress", rawInput={"command": "python3 hello.py"}))
    events += mapper.update(_call(status="completed", rawOutput={"output": "4\n", "metadata": {"exit": 0}}))
    started = [e for e in events if isinstance(e, CommandStarted)]
    assert [e.command for e in started] == ["python3 hello.py"]
    assert [type(e).__name__ for e in events] == ["CommandStarted", "CommandOutput", "CommandFinished"]
    assert next(e for e in events if isinstance(e, CommandOutput)).text == "4\n"
    assert next(e for e in events if isinstance(e, CommandFinished)).exit_code == 0


def test_a_command_that_ends_without_ever_naming_its_input_still_shows() -> None:
    mapper = AcpEventMapper(turn_id="t-1", policy=POLICY)
    _ = mapper.update({**_call(title="bash", kind="execute", status="pending"), "sessionUpdate": "tool_call"})
    events = mapper.update(_call(status="failed"))
    assert [(type(e).__name__, getattr(e, "command", None)) for e in events][:1] == [("CommandStarted", "bash")]
