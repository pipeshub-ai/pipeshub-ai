"""Bounded stopping of an engine, the same for every adapter (CF-09).

An interrupted turn ends within ``INTERRUPT_STOP_S``; an engine that does not stop is dead within ``KILL_BOUND_S``
of the interrupt. ``stop_process`` is the escalation every adapter's ``close`` runs: SIGINT, then SIGTERM, then
SIGKILL. ``StopBoundedSession`` is what the controller (and the conformance suite) hand every turn to: when the
engine does not answer its native interrupt in time, the turn ends interrupted there and then, and the session is
closed, which runs that escalation.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.events import EngineEventMeta, TurnCompleted
from harness_core.engines.native import RecordsNative
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable

    from harness_core.context.user_compact import CompactReport
    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import EngineTask, FollowUp, SessionHealth
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.sandbox.ports import ProcessHandle

LOGGER = "harness_core.engines.stopping"
INTERRUPT_STOP_S = 2.0
KILL_BOUND_S = 10.0
# How long the engine's own interrupt has before the turn is ended for it: the rest of ``INTERRUPT_STOP_S`` is the
# margin for ending it and handing the end to the consumer.
INTERRUPT_GRACE_S = 1.5


@dataclass(frozen=True, slots=True)
class StopSchedule:
    """Seconds after the stop starts at which each signal goes, and after which waiting ends.

    ``INTERRUPT_GRACE_S + give_up_at_s`` stays within ``KILL_BOUND_S``: the escalation of an interrupt the engine
    never answered starts that late, and must still end in time.
    """

    sigint_at_s: float = 1.0
    sigterm_at_s: float = 2.0
    sigkill_at_s: float = 5.0
    give_up_at_s: float = 7.0


STOP_SCHEDULE = StopSchedule()
# A server the harness reaches over the network has no stdin whose end asks it to exit: it is signalled at once.
SERVER_STOP_SCHEDULE = StopSchedule(sigint_at_s=0.0)


async def stop_after_failed_open(stop: Awaitable[object], log: HarnessLog) -> None:
    """Runs an adapter's cleanup while the error that failed its ``open`` propagates. The kernel types the attempt by
    that error (P9 FB-9): a cleanup that fails as well, as it does when the broker link the open lost is still gone
    or the exit code never comes, is logged and never raised in its place."""
    try:
        _ = await stop
    except Exception as exc:
        log.warning("engine.session.stop_failed", errorType=type(exc).__name__)


async def stop_process(process: ProcessHandle, *, schedule: StopSchedule = STOP_SCHEDULE) -> int | None:
    """Stop a process its owner has already asked to exit (its stdin or protocol closed). The exit code; ``None``
    when it outlived even SIGKILL's wait (a sandbox whose runtime no longer answers)."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    steps: tuple[tuple[int | None, float], ...] = (
        (None, schedule.sigint_at_s),
        (signal.SIGINT, schedule.sigterm_at_s),
        (signal.SIGTERM, schedule.sigkill_at_s),
        (signal.SIGKILL, schedule.give_up_at_s),
    )
    for sig, until in steps:
        if sig is not None:
            await process.signal(sig)
        try:
            return await process.wait(timeout_s=max(0.0, start + until - loop.time()))
        except TimeoutError:
            continue
    return None


_END = object()


@dataclass
class _ActiveTurn:
    turn_id: str
    queue: asyncio.Queue[object]
    # The engine's turn is over (its ``TurnCompleted`` or its stream's end), whether or not the consumer read it.
    ended: asyncio.Event
    pump: asyncio.Task[None] | None = None
    watchdog: asyncio.Task[None] | None = None
    killed: bool = False


class StopBoundedSession:
    """An ``EngineSession`` whose interrupt is bounded (CF-09), whatever the adapter under it.

    Every event, the stream's end and any error after the ``TurnCompleted`` reach the consumer as the adapter
    produced them; only an interrupt the engine leaves unanswered changes the turn: it ends interrupted.
    """

    def __init__(self, inner: EngineSession, *, log: HarnessLog | None = None) -> None:
        self._inner = inner
        self._log = log or HarnessLog.named(LOGGER)
        self._turn: _ActiveTurn | None = None
        self._closing: asyncio.Task[None] | None = None
        self._pumps: set[asyncio.Task[None]] = set()

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        # One slot: a slow consumer still slows the engine's stream (the port's bound), one event later.
        turn = _ActiveTurn(task.turn_id, asyncio.Queue(maxsize=1), asyncio.Event())
        self._turn = turn
        turn.pump = asyncio.get_running_loop().create_task(self._pump(task, turn))
        self._pumps.add(turn.pump)
        turn.pump.add_done_callback(self._pumps.discard)
        last_seq, completed = -1, False
        try:
            while True:
                if turn.killed:
                    if not completed:
                        yield TurnCompleted(EngineEventMeta(turn.turn_id, last_seq + 1), "interrupted")
                    return
                item = await turn.queue.get()
                if item is _END:
                    if turn.killed:
                        continue
                    return
                if isinstance(item, BaseException):
                    raise item
                event: EngineEvent = item  # type: ignore[assignment]
                last_seq = event.meta.seq
                completed = completed or isinstance(event, TurnCompleted)
                yield event
        finally:
            if self._turn is turn:
                self._turn = None
            turn.ended.set()
            if turn.watchdog is not None and not turn.watchdog.done():
                _ = turn.watchdog.cancel()
            if not turn.pump.done():
                _ = turn.pump.cancel()
                # A killed turn's stream unwinds once ``close`` has killed its engine, and ``close`` waits for it.
                if not turn.killed:
                    _ = await asyncio.wait({turn.pump}, timeout=INTERRUPT_STOP_S)

    async def _pump(self, task: EngineTask | FollowUp, turn: _ActiveTurn) -> None:
        try:
            async for event in self._inner.run_turn(task):
                if isinstance(event, TurnCompleted):
                    turn.ended.set()
                await turn.queue.put(event)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # handed to the consumer, which raises it
            turn.ended.set()
            await turn.queue.put(exc)
            return
        turn.ended.set()
        await turn.queue.put(_END)

    async def interrupt(self) -> None:
        turn = self._turn
        if turn is None:
            await self._inner.interrupt()
            return
        if turn.ended.is_set():
            # The pump may already have run the adapter's end-of-turn code, which some adapters' interrupt reads
            # (Codex drops its turn there); a turn that ended is not interrupted, whatever the adapter.
            self._log.info("engine.turn.interrupt_after_end", source="stop_bounded")
            return
        if turn.watchdog is None:
            turn.watchdog = asyncio.get_running_loop().create_task(self._watch(turn))
        try:
            await asyncio.wait_for(self._inner.interrupt(), INTERRUPT_GRACE_S)
        except TimeoutError:
            # The watchdog ends the turn: an engine that cannot even take the request is what it is for.
            self._log.warning("engine.turn.interrupt_unacknowledged", reasonCode="interrupt_unanswered")

    async def _watch(self, turn: _ActiveTurn) -> None:
        try:
            await asyncio.wait_for(turn.ended.wait(), INTERRUPT_GRACE_S)
            return
        except TimeoutError:
            pass
        self._log.warning("engine.turn.interrupt_unanswered", reasonCode="interrupt_unanswered", turnId=turn.turn_id)
        turn.killed = True
        if turn.pump is not None:
            _ = turn.pump.cancel()
        # Wakes a consumer waiting on an empty queue; one about to read finds ``killed`` first.
        with contextlib.suppress(asyncio.QueueFull):
            turn.queue.put_nowait(_END)
        await self._close()

    def tap_native(self, tape: NativeTape) -> None:
        """The adapter's native transcript (C2.39), when it records one."""
        if isinstance(self._inner, RecordsNative):
            self._inner.tap_native(tape)

    async def steer(self, text: str) -> None:
        await self._inner.steer(text)

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        return await self._inner.compact(focus, instructions=instructions)

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        await self._inner.answer(request_id, decision)

    async def capture_state(self) -> EngineState:
        return await self._inner.capture_state()

    async def health(self) -> SessionHealth:
        return await self._inner.health()

    async def close(self) -> None:
        await self._close()

    def _close(self) -> asyncio.Future[None]:
        # One close, however many ask: the controller's own close waits for the watchdog's escalation to finish.
        if self._closing is None:
            self._closing = asyncio.get_running_loop().create_task(self._close_once())
        return asyncio.shield(self._closing)

    async def _close_once(self) -> None:
        try:
            await self._inner.close()
        finally:
            # A turn's stream that unwinds only once its engine is gone.
            for pump in list(self._pumps):
                _ = pump.cancel()
            if self._pumps:
                _ = await asyncio.wait(set(self._pumps), timeout=INTERRUPT_STOP_S)


def stop_bounded(session: EngineSession) -> EngineSession:
    return session if isinstance(session, StopBoundedSession) else StopBoundedSession(session)


__all__ = [
    "INTERRUPT_GRACE_S",
    "INTERRUPT_STOP_S",
    "KILL_BOUND_S",
    "SERVER_STOP_SCHEDULE",
    "STOP_SCHEDULE",
    "StopBoundedSession",
    "StopSchedule",
    "stop_after_failed_open",
    "stop_bounded",
    "stop_process",
]
