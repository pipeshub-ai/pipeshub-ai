"""CF-09: an interrupted turn ends within 2 s and an engine that does not stop is killed within 10 s.

The conformance suite times this on every adapter's fake (``spec_cf09_*``); Codex and Claude Code have no fake
binary, so their ``close`` is timed here against a process that takes no signal but SIGKILL, as their Docker lanes
time it on the real binaries.

The timed tests run on ``VirtualTimeLoop``: the bounds are deadlines on the loop's clock, and a wall-clock reading
also held whatever paused the test process (2.28 s against 2.0 in a loaded full gate, for a turn the code ended at
1.5). There a reading is exactly the deadlines the code waited on.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.engines.events import EngineEventMeta, TextDelta, TurnCompleted
from harness_core.engines.stopping import (
    INTERRUPT_GRACE_S,
    INTERRUPT_STOP_S,
    KILL_BOUND_S,
    SERVER_STOP_SCHEDULE,
    STOP_SCHEDULE,
    StopBoundedSession,
    StopSchedule,
    stop_bounded,
    stop_process,
)
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog
from harness_core.testing.virtual_time import run_on_virtual_time
from harness_engines.claude_code.session import ClaudeSession
from harness_engines.codex.session import CodexSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.ports import EngineSession
    from harness_core.sandbox.ports import ProcessHandle

LOG = HarnessLog(logging.getLogger("test.cf09"))
TASK = EngineTask(turn_id="trn-1", prompt="hold")
FAST = StopSchedule(sigint_at_s=0.01, sigterm_at_s=0.02, sigkill_at_s=0.03, give_up_at_s=0.5)


class Process:
    """A process that exits on ``exits_on`` (or at once when its stdin closes, with ``exits_on=None``)."""

    def __init__(self, exits_on: int | None = signal.SIGKILL, *, exits_unasked: bool = False) -> None:
        self.signals: list[int] = []
        self._exits_on = exits_on
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        if exits_unasked:
            self._exit.set_result(0)

    async def signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == self._exits_on and not self._exit.done():
            self._exit.set_result(128 + sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(asyncio.shield(self._exit), timeout_s)


def test_the_bounds_hold_together() -> None:
    assert INTERRUPT_GRACE_S < INTERRUPT_STOP_S <= KILL_BOUND_S
    for schedule in (STOP_SCHEDULE, SERVER_STOP_SCHEDULE):
        assert 0 <= schedule.sigint_at_s <= schedule.sigterm_at_s <= schedule.sigkill_at_s < schedule.give_up_at_s
        # An interrupt nobody answered starts the escalation at the grace's end; the kill still lands in the bound.
        assert INTERRUPT_GRACE_S + schedule.give_up_at_s <= KILL_BOUND_S


async def test_a_process_that_exits_when_asked_gets_no_signal() -> None:
    process = Process(exits_unasked=True)
    assert await stop_process(cast("ProcessHandle", process), schedule=FAST) == 0
    assert process.signals == []


@pytest.mark.parametrize(
    ("exits_on", "sent"),
    [
        (signal.SIGINT, [signal.SIGINT]),
        (signal.SIGTERM, [signal.SIGINT, signal.SIGTERM]),
        (signal.SIGKILL, [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]),
    ],
)
async def test_the_escalation_is_sigint_then_sigterm_then_sigkill(exits_on: int, sent: list[int]) -> None:
    process = Process(exits_on)
    assert await stop_process(cast("ProcessHandle", process), schedule=FAST) == 128 + exits_on
    assert process.signals == sent


def _now() -> float:
    return asyncio.get_running_loop().time()


def test_a_process_not_even_sigkill_ends_is_given_up_on_in_time() -> None:
    async def main() -> None:
        process = Process(exits_on=None)
        started = _now()
        assert await stop_process(cast("ProcessHandle", process), schedule=STOP_SCHEDULE) is None
        assert _now() - started == pytest.approx(STOP_SCHEDULE.give_up_at_s)
        assert process.signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]

    run_on_virtual_time(main)


@dataclass
class _Channel:
    """Codex's ``CodexRpc`` and Claude Code's ``ClaudeStream`` as ``close`` uses them."""

    closed: bool = False
    unparsed_lines: int = 0

    async def close(self) -> None:
        self.closed = True


def _codex(process: Process) -> CodexSession:
    return CodexSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=process,  # type: ignore[arg-type]
        rpc=_Channel(),  # type: ignore[arg-type]
        reported_version="0.160.0",
        resume_thread=None,
        secrets=frozenset(),
        log=LOG,
    )


def _claude(process: Process) -> ClaudeSession:
    return ClaudeSession(
        engine=None,  # type: ignore[arg-type]
        sandbox=SimpleNamespace(id="sbx-1"),  # type: ignore[arg-type]
        process=process,  # type: ignore[arg-type]
        stream=_Channel(),  # type: ignore[arg-type]
        reported_version="2.1.294",
        resume_session=None,
        secrets=frozenset(),
        log=LOG,
    )


@pytest.mark.parametrize("session", [_codex, _claude], ids=["codex", "claude_code"])
def test_closing_an_engine_that_takes_only_sigkill_ends_it_within_the_bound(
    session: Callable[[Process], EngineSession],
) -> None:
    """CF-09 on Codex and Claude Code, with the production schedule: SIGINT and SIGTERM ignored, SIGKILL in time."""

    async def main() -> None:
        process = Process(signal.SIGKILL)
        opened = session(process)
        started = _now()
        await opened.close()
        assert _now() - started == pytest.approx(STOP_SCHEDULE.sigkill_at_s)
        assert _now() - started <= KILL_BOUND_S - INTERRUPT_GRACE_S
        assert process.signals == [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]

    run_on_virtual_time(main)


@dataclass
class Inner:
    """An adapter's session: ``deaf`` engines neither end the turn on ``interrupt`` nor answer it."""

    deaf: bool = False
    fail_after_turn: bool = False
    closes: int = 0
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def run_turn(self, task: EngineTask) -> AsyncIterator[EngineEvent]:
        yield TextDelta(EngineEventMeta(task.turn_id, 0), "msg-1", "partial")
        await self._stop.wait()
        yield TurnCompleted(EngineEventMeta(task.turn_id, 1), "interrupted")
        if self.fail_after_turn:
            raise RuntimeError("the stream ended badly")

    async def interrupt(self) -> None:
        if self.deaf:
            await asyncio.Event().wait()
        self._stop.set()

    async def close(self) -> None:
        self.closes += 1
        await asyncio.sleep(0.05)
        self._stop.set()


async def _until_ended(session: StopBoundedSession) -> tuple[list[EngineEvent], float]:
    events: list[EngineEvent] = []
    ended_at = 0.0
    async for event in session.run_turn(TASK):
        events.append(event)
        if isinstance(event, TurnCompleted):
            ended_at = _now()
    return events, ended_at


async def test_an_answered_interrupt_passes_through_untouched() -> None:
    inner = Inner()
    session = StopBoundedSession(inner, log=LOG)  # type: ignore[arg-type]
    runner = asyncio.ensure_future(_until_ended(session))
    await asyncio.sleep(0.01)
    await session.interrupt()
    events, _ = await runner
    assert [(type(e).__name__, e.meta.seq) for e in events] == [("TextDelta", 0), ("TurnCompleted", 1)]
    assert inner.closes == 0


async def test_an_error_after_the_turn_still_reaches_the_consumer() -> None:
    inner = Inner(fail_after_turn=True)
    session = StopBoundedSession(inner, log=LOG)  # type: ignore[arg-type]
    last: list[EngineEvent | None] = [None]

    async def consume() -> None:
        async for event in session.run_turn(TASK):
            last[0] = event

    runner = asyncio.ensure_future(consume())
    await asyncio.sleep(0.01)
    await session.interrupt()
    with pytest.raises(RuntimeError, match="ended badly"):
        await runner
    assert isinstance(last[0], TurnCompleted)


def test_an_unanswered_interrupt_ends_the_turn_in_time_and_closes_the_engine_once() -> None:
    async def main() -> None:
        inner = Inner(deaf=True)
        session = StopBoundedSession(inner, log=LOG)  # type: ignore[arg-type]
        runner = asyncio.ensure_future(_until_ended(session))
        await asyncio.sleep(0.01)
        started = _now()
        await session.interrupt()
        events, ended_at = await runner
        # The turn ends when the grace runs out, with no other wait on the way: the rest of the bound is the
        # margin for the CPU work of ending it.
        assert ended_at - started == pytest.approx(INTERRUPT_GRACE_S)
        assert ended_at - started <= INTERRUPT_STOP_S
        assert [(type(e).__name__, e.meta.seq) for e in events] == [("TextDelta", 0), ("TurnCompleted", 1)]
        last = events[-1]
        assert isinstance(last, TurnCompleted) and last.reason == "interrupted"
        await session.close()
        await session.close()
        assert inner.closes == 1

    run_on_virtual_time(main)


def test_wrapping_twice_is_one_wrapper() -> None:
    once = stop_bounded(Inner())  # type: ignore[arg-type]
    assert stop_bounded(once) is once
