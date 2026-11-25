"""GA-04 (G-05) on PipesHub's ports: a verify command runs through the run's own ``run_code`` (as a person's ``/run``
does) and reads its exit status; a run without ``run_code`` has no command port, so a command check fails closed."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.harness.assembly.command_run import RUN_CODE
from app.harness.run_gates import RunCodeCommands, pipeshub_verify_ports
from harness_core.effects.types import ToolContext, ToolExecution

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.core.tool_schema import ToolSchema

CTX = ToolContext("run-1", "org-1", "verify", "att-1", "org-1:run-1:verify")


class _RunCode:
    def __init__(self, execution: ToolExecution) -> None:
        self.execution = execution
        self.calls: list[ToolCall] = []

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return None

    def schemas(self) -> list[ToolSchema] | None:
        return None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        self.calls.append(call)
        return self.execution


async def test_ga04_a_command_runs_through_run_code_and_reads_its_exit_status() -> None:
    tool = _RunCode(ToolExecution(status="error", content=json.dumps({"exit_code": 1, "stdout": "1 failed, 3 passed"})))
    outcome = await RunCodeCommands(tool, CTX).run(["pytest", "-q"], timeout_s=60)
    assert (outcome.exit_code, outcome.timed_out) == (1, False)
    assert "1 failed" in outcome.tail
    (call,) = tool.calls
    assert call.name == RUN_CODE and "pytest -q" in str(call.arguments["code"]) and call.arguments["timeout"] == 60.0


async def test_ga04_a_command_that_passes_and_one_that_times_out() -> None:
    ok = await RunCodeCommands(_RunCode(ToolExecution(status="ok", content={"exit_code": 0})), CTX).run(["make"], timeout_s=5)
    assert ok.exit_code == 0
    late = await RunCodeCommands(_RunCode(ToolExecution(status="timeout", content="")), CTX).run(["make"], timeout_s=5)
    assert late.timed_out


def test_ga04_without_run_code_there_is_no_command_port() -> None:
    ports = pipeshub_verify_ports(context=object(), workspace=None, tools=_RunCode(ToolExecution("ok", "")), has_run_code=False, ctx=CTX)
    assert ports.commands is None and ports.files is None and ports.citations is not None
