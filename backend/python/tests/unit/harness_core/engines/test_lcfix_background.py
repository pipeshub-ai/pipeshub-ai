"""LCFIX: a command an engine left running when its turn ended is stopped before the sandbox is released, and the
turn's answer says so (Codex's ``exec_command`` returned after about 10 s while ``sleep 40`` ran; the sandbox was
destroyed and nothing in the chat said so)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.engines.background import parse_table, shell_commands, stopped_note
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.kernel.durable.transcript import decode_assistant
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

RUN = "run-bg"


def _stat(pid: int, comm: str, ppid: int, start: int, state: str = "S") -> str:
    return f"{pid} ({comm}) {state} {ppid} " + " ".join(["0"] * 17) + f" {start} 0 0"


def test_lcfix_proc_stat_lines_are_read_without_the_probe_and_zombies() -> None:
    output = "\n".join([
        "77",
        _stat(1, "tini", 0, 5),
        _stat(9, "opencode", 1, 10),
        _stat(30, "my (odd) name", 9, 400),
        _stat(31, "dead", 9, 401, state="Z"),
        _stat(77, "sh", 9, 500),
        _stat(78, "cat", 77, 501),
        "garbage",
    ])
    assert parse_table(output) == {(1, 5): (0, "tini"), (9, 10): (1, "opencode"), (30, 400): (9, "my (odd) name")}
    assert parse_table("") is None and parse_table("no pid\n") is None


BEFORE = {(1, 1): (0, "tini"), (9, 2): (1, "opencode"), (15, 3): (1, "codex-app-serve")}


def test_lcfix_only_what_the_shell_tool_ran_is_attributed_to_the_turn() -> None:
    """The LIFECYCLE false positive: the sandbox's relay (started by the broker, not under the engine) and an engine's
    own worker are never the turn's commands; a shell it started is, with what it runs, counted once; a Codex command
    is its app-server's child (``bash -lc`` exec'd it in its place)."""
    after = {
        **BEFORE,
        (40, 9): (0, "pxh-relay"),
        (41, 9): (9, "node"),
        (42, 9): (9, "bash"),
        (43, 9): (42, "sleep"),
        (44, 9): (15, "sleep"),
    }
    assert shell_commands(BEFORE, after) == ({42, 43}, 1)
    assert shell_commands(BEFORE, after, frozenset({"codex-app-serve"})) == ({42, 43, 44}, 2)
    assert shell_commands(BEFORE, {**BEFORE, (40, 9): (0, "pxh-relay"), (41, 9): (9, "node")}) == (set(), 0)


def test_lcfix_the_note_counts_commands() -> None:
    assert stopped_note(1) == "1 background command was still running when the turn ended and was stopped."
    assert stopped_note(2).startswith("2 background commands were")


class _LeavesACommandRunning:
    """The engine's shell tool returned while what it started still runs (Codex ``exec_command`` yielding)."""

    def __init__(self, inner: object, sandbox: FakeSandboxHandle) -> None:
        self._inner = inner
        self._sandbox = sandbox

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        _ = await self._sandbox.exec(["bash", "-c", "sleep 40"], env={})
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            yield event


async def test_lcfix_a_command_still_running_at_turn_end_is_stopped_and_reported() -> None:
    w = EngineWorld()
    adapter = w.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    opened = adapter.open
    sandboxes: list[FakeSandboxHandle] = []

    async def sleep_forever(_context: FakeProcessContext) -> int:
        await asyncio.Event().wait()
        return 0

    async def open_with_background(sandbox: FakeSandboxHandle, *args: object, **kwargs: object) -> object:
        sandbox.install("bash", sleep_forever)
        sandboxes.append(sandbox)
        return _LeavesACommandRunning(await opened(sandbox, *args, **kwargs), sandbox)  # type: ignore[arg-type]

    adapter.open = open_with_background  # type: ignore[method-assign]
    w.admit(RUN, "say done")
    assert (await w.attempt(RUN)).status == "completed"
    (sandbox,) = sandboxes
    (sleeper,) = [p for p in sandbox.processes if p.context.argv == ["bash", "-c", "sleep 40"]]
    assert not sleeper.running
    state = fold_engine_run(w.events(RUN), run_id=RUN)
    answer = state.last_turn
    assert answer is not None and answer.response_ref is not None
    text = decode_assistant(await w.content.get(answer.response_ref)).text
    assert text == f"done\n\n{stopped_note(1)}"


async def test_lcfix_a_turn_that_leaves_nothing_running_says_nothing() -> None:
    w = EngineWorld()
    w.admit(RUN, "say done")
    assert (await w.attempt(RUN)).status == "completed"
    state = fold_engine_run(w.events(RUN), run_id=RUN)
    assert state.last_turn is not None and state.last_turn.response_ref is not None
    assert decode_assistant(await w.content.get(state.last_turn.response_ref)).text == "done"
