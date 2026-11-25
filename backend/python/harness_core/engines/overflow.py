"""A turn the provider refused as past the model's context window is compacted and run again, once (MG-10).

The model gateway names a provider's context overflow ``context_length_exceeded`` on every wire, and tags the message
an engine keeps with it (``[gateway:context_length_exceeded]``), so each adapter reports it as that code. An engine
that compacts and retries on its own never fails the turn; one that fails it instead would leave the person an error
and a session that fails the same way on the next message. Here such a turn's failure is held: when the engine did not
compact during the turn, the session runs the engine's own compaction (the adapter's ``compact``, as ``/compact``
does) and the same task once more. The chat sees one ``ContextCompacted`` and the retried turn; the overflow reaches
it only when the compaction cannot run or the person stopped the turn.

An engine with no compaction of its own (ACP has no request for one; Gemini CLI 0.63 fails the prompt) is restarted
from a summary instead, when its session offers it (``RestartsFromSummary``, C3.67): the same one retry, the same one
``ContextCompacted``, journaled as ``context.compacted`` trigger ``auto`` like every other.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

from harness_core.engines.errors import CompactFailed, CompactUnsupported
from harness_core.engines.events import (
    ContextCompacted,
    EngineError,
    EngineEventMeta,
    TurnCompleted,
)
from harness_core.engines.native import RecordsNative
from harness_core.engines.ports import RestartsFromSummary
from harness_core.engines.types import EngineTask
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.context.user_compact import CompactReport
    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.ports import EngineSession
    from harness_core.engines.types import FollowUp, SessionHealth
    from harness_core.interactions.ports import InteractionDecision

LOGGER = "harness_core.engines.overflow"
# The gateway's code, and Codex's own name for the same refusal (``codexErrorInfo: contextWindowExceeded``).
OVERFLOW_CODES = frozenset({"context_length_exceeded", "context_window_exceeded"})


def _renumbered(event: EngineEvent, seq: int) -> EngineEvent:
    return dataclasses.replace(event, meta=EngineEventMeta(event.meta.turn_id, seq))  # type: ignore[type-var]


class OverflowCompactingSession:
    """The session the controller hands every turn to: an overflowed turn is compacted and retried at most once."""

    def __init__(
        self, inner: EngineSession, log: HarnessLog | None = None, *, restart: RestartsFromSummary | None = None
    ) -> None:
        self._inner = inner
        # The raw session's restart, when it has one (the wrappers inside this one do not forward it).
        self._restart = restart
        self._log = log or HarnessLog.named(LOGGER)
        self._stopping = False
        # Between an overflowed attempt's end and its retry: the engine runs no turn a stop could reach.
        self._between = False
        self._compacting: asyncio.Task[CompactReport] | None = None

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        self._stopping = False
        # Every event the chat sees is numbered here, so the retry's events go on from the first attempt's with no gap.
        seq = 0
        held: list[EngineEvent] = []
        withheld: TurnCompleted | None = None
        compacted_itself = False
        async for event in self._inner.run_turn(task):
            if isinstance(event, EngineError) and event.code in OVERFLOW_CODES:
                held.append(event)
                continue
            if isinstance(event, TurnCompleted) and withheld is None:
                if event.reason == "failed" and held and not compacted_itself and not self._stopping:
                    withheld = event
                    self._between = True
                    continue
                # Handed on as it arrives: a stop the controller sends after the end must find the turn ended.
                for kept in (*held, event):
                    yield _renumbered(kept, seq)
                    seq += 1
                held = []
                continue
            compacted_itself = compacted_itself or isinstance(event, ContextCompacted)
            yield _renumbered(event, seq)
            seq += 1
        try:
            report = await self._compact(task) if withheld is not None and not self._stopping else None
        finally:
            self._between = False
        if report is None or self._stopping:
            for event in (*held, *(() if withheld is None else (withheld,))):
                yield _renumbered(event, seq)
                seq += 1
            return
        assert withheld is not None
        yield ContextCompacted(
            EngineEventMeta(withheld.meta.turn_id, seq),
            report.before_tokens,
            report.after_tokens,
            user_messages_kept=report.user_messages_kept,
        )
        seq += 1
        async for event in self._inner.run_turn(task):
            yield _renumbered(event, seq)
            seq += 1

    async def _compact(self, task: EngineTask | FollowUp) -> CompactReport | None:
        instructions = task.instructions if isinstance(task, EngineTask) else ""
        self._compacting = asyncio.get_running_loop().create_task(self._inner.compact(None, instructions=instructions))
        try:
            try:
                report = await self._compacting
            except CompactUnsupported:
                if self._restart is None or self._stopping:
                    raise
                self._compacting = asyncio.get_running_loop().create_task(self._restart.restart_from_summary(task))
                report = await self._compacting
                self._log.info("engine.turn.overflow_restarted", afterTokens=report.after_tokens)
        except (CompactUnsupported, CompactFailed) as refused:
            self._log.warning("engine.turn.overflow_not_compacted", reasonCode=refused.reason_code)
            return None
        except asyncio.CancelledError:
            current = asyncio.current_task()
            # Only the compaction was cancelled (``interrupt``); a cancelled turn stays cancelled.
            if not self._stopping or (current is not None and current.cancelling()):
                raise
            return None
        finally:
            self._compacting = None
        if report.outcome != "compacted":
            self._log.warning("engine.turn.overflow_not_compacted", reasonCode=f"compact_{report.outcome}")
            return None
        self._log.info(
            "engine.turn.overflow_compacted", beforeTokens=report.before_tokens, afterTokens=report.after_tokens
        )
        return report

    async def interrupt(self) -> None:
        self._stopping = True
        if self._compacting is not None:
            _ = self._compacting.cancel()
        if not self._between:
            await self._inner.interrupt()

    def tap_native(self, tape: NativeTape) -> None:
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
        self._stopping = True
        if self._compacting is not None:
            _ = self._compacting.cancel()
        await self._inner.close()


def overflow_compacting(session: EngineSession, *, restart: object = None) -> EngineSession:
    """``session`` compacted and retried on an overflow; ``restart``, the raw session, is its summary-and-restart when
    it offers one (``RestartsFromSummary``)."""
    if isinstance(session, OverflowCompactingSession):
        return session
    return OverflowCompactingSession(session, restart=restart if isinstance(restart, RestartsFromSummary) else None)


__all__ = ["OVERFLOW_CODES", "OverflowCompactingSession", "overflow_compacting"]
