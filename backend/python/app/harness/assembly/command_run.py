"""A person's ``/run <command>`` on the durable loop (SLASH3 §3.5): one ``run_code`` call and no model.

The run is a loop run whose director takes both of its turns itself: turn 1 calls the agent's own ``run_code``
with the line, so the call goes through the same effect runner as the model's would (the approval policy and the
flow guard, its receipt, the activity capture and the workspace save); turn 2 ends the run with the result's exit
status as its answer. Nothing is sent to a model (SD9), and the kernel never asks one: the director plans every
turn of the run.

The line runs as ``bash -lc`` (``sh -c`` where the image has no bash) inside the session workspace's folder, with
no stdin and no TTY, under ``run_code``'s timeout; the sandbox goes when the call ends, and every process with it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final, cast

from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.durable.ports import TurnPlan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.durable.ports import TurnResult
    from harness_core.kernel.fold import RunState

COMMAND_PREFIX: Final = "/run "
RUN_CODE: Final = "run_code"
# The folder run_code stages the session workspace into (SandboxProjection("/workspace", "workspace")).
WORKSPACE_DIR: Final = "workspace"
CALL_ID: Final = "slash-run-1"

# A Python launcher, since run_code runs Python or TypeScript: the agent's code tool could run the same program. A
# line a signal ended exits 128 + the signal, as a shell reports it, so a kill by the sandbox's memory limit reads as
# run_code's own (137) and is named as one; Python's own exit would be 256 minus the signal (247).
_LAUNCHER: Final = """import os, shutil, subprocess, sys
os.makedirs({folder!r}, exist_ok=True)
shell = shutil.which("bash")
argv = [shell, "-lc", {line!r}] if shell else ["sh", "-c", {line!r}]
code = subprocess.run(argv, cwd={folder!r}, stdin=subprocess.DEVNULL).returncode
sys.exit(128 - code if code < 0 else code)
"""


def command_line(query: str) -> str:
    """The line a ``/run`` run's query carries after its prefix (Node writes ``/run <line>``)."""
    return query[len(COMMAND_PREFIX) :] if query.startswith(COMMAND_PREFIX) else query


def launcher(line: str) -> str:
    return _LAUNCHER.format(folder=WORKSPACE_DIR, line=line)


def _result(content: object) -> dict[str, object]:
    data = content
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except ValueError:
            return {}
    return cast("dict[str, object]", data) if isinstance(data, dict) else {}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summary(previous: Sequence[TurnResult]) -> str:
    """The run's answer: how the command ended, from its journaled result (never re-run, never a model's words). A
    command the sandbox's memory limit stopped says so with the limit (R33JGAPS-FU-2), as run_code's result does."""
    result = next((r for r in previous if r.call_id == CALL_ID), None)
    if result is None or result.status != "ok":
        return "The command did not run."
    data = _result(result.content)
    limit = _int(data.get("out_of_memory_mb"))
    if limit is not None and limit > 0:
        # Imported here: the idle worker imports this module and never the agent loop's sandbox stack (D7).
        from app.agent_loop_lib.sandbox.coding.base import ran_out_of_memory

        return ran_out_of_memory(limit, "command")
    code = _int(data.get("exit_code"))
    return "The command did not run." if code is None else f"Exit code {code}."


class CommandDirector:
    """The ``LoopDirector`` of a ``/run`` run: a pure function of the turn, as a re-claimed attempt needs."""

    def __init__(self, *, line: str, timeout_s: int) -> None:
        self._line = line
        self._timeout_s = timeout_s

    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        if turn <= 1:
            call = ToolCall(
                id=CALL_ID,
                name=RUN_CODE,
                arguments={"code": launcher(self._line), "language": "python", "timeout": float(self._timeout_s)},
            )
            return TurnPlan(synthetic=AssistantMessage(content=[], tool_calls=[call]))
        return TurnPlan(synthetic=AssistantMessage(content=[TextPart(text=summary(previous))]))
