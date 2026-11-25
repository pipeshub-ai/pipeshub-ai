"""MG-10: a turn the provider refused as past the model's context window is compacted and retried once.

The engine's own compaction runs (the adapter's ``compact``) only when the engine failed the turn on the overflow and
did not compact during it; the retry is the same task, its events numbered on from the first attempt's, and the chat
sees one ``ContextCompacted`` instead of the error. One retry at most, none once the turn is being stopped, and the
overflow is handed on when the engine cannot compact. The real binaries' cases are the contract's ``cf21``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_context_compacted import (
    PipeshubContextCompactedEvent,
)
from harness_core.context.user_compact import CONTEXT_COMPACTED_EVENT, CompactReport
from harness_core.engines.errors import CompactFailed, CompactUnsupported
from harness_core.engines.events import (
    ContextCompacted,
    EngineError,
    EngineEvent,
    EngineEventMeta,
    MessageCompleted,
    TurnCompleted,
    turn_id_for,
)
from harness_core.engines.overflow import OverflowCompactingSession, overflow_compacting
from harness_core.engines.types import EngineTask
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter
from tests.unit.harness_core.engines.test_controller import World, task

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

TURN = turn_id_for("run-mg10", 0)
TASK = EngineTask(turn_id=TURN, prompt="overflow\nsay recovered", instructions="Be brief.")


def _meta(seq: int) -> EngineEventMeta:
    return EngineEventMeta(TURN, seq)


def overflowed() -> list[EngineEvent]:
    return [
        MessageCompleted(_meta(0), f"{TURN}:m1", ""),
        EngineError(_meta(1), "context_length_exceeded", "the engine reported a turn error"),
        TurnCompleted(_meta(2), "failed"),
    ]


def answered() -> list[EngineEvent]:
    return [MessageCompleted(_meta(0), f"{TURN}:m1", "recovered"), TurnCompleted(_meta(1), "completed", "recovered")]


@dataclass
class _Session:
    """An engine session that plays one scripted attempt per ``run_turn`` and records what it was asked."""

    attempts: list[Callable[[], list[EngineEvent]]]
    compact_with: CompactReport | Exception = field(default_factory=lambda: CompactReport("compacted", 250_000, 9_000))
    compact_gate: asyncio.Event | None = None
    compacting: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: list[object] = field(default_factory=list[object])
    compacts: list[tuple[str | None, str]] = field(default_factory=list[tuple[str | None, str]])
    interrupts: int = 0
    streaming: bool = False

    async def run_turn(self, task: object) -> AsyncIterator[EngineEvent]:
        self.tasks.append(task)
        self.streaming = True
        for event in self.attempts.pop(0)():
            yield event
        self.streaming = False

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        self.compacts.append((focus, instructions))
        self.compacting.set()
        if self.compact_gate is not None:
            await self.compact_gate.wait()
        if isinstance(self.compact_with, Exception):
            raise self.compact_with
        return self.compact_with

    async def interrupt(self) -> None:
        self.interrupts += 1


async def _events(session: OverflowCompactingSession) -> list[EngineEvent]:
    return [event async for event in session.run_turn(TASK)]


def _contiguous(events: list[EngineEvent]) -> bool:
    return [e.meta.seq for e in events] == list(range(len(events)))


async def test_mg10_an_overflowed_turn_is_compacted_and_retried_once_with_one_event_for_the_chat() -> None:
    inner = _Session([overflowed, answered])
    events = await _events(OverflowCompactingSession(inner))  # type: ignore[arg-type]
    assert [type(e).__name__ for e in events] == [
        "MessageCompleted", "ContextCompacted", "MessageCompleted", "TurnCompleted",
    ]
    compacted = events[1]
    assert isinstance(compacted, ContextCompacted)
    assert (compacted.before_tokens, compacted.after_tokens) == (250_000, 9_000)
    assert events[-1] == TurnCompleted(_meta(3), "completed", "recovered")
    assert _contiguous(events)
    assert inner.tasks == [TASK, TASK]
    assert inner.compacts == [(None, "Be brief.")]


async def test_mg10_codex_s_own_name_for_the_overflow_is_recovered_too() -> None:
    def codex() -> list[EngineEvent]:
        return [EngineError(_meta(0), "context_window_exceeded", "x"), TurnCompleted(_meta(1), "failed")]

    events = await _events(OverflowCompactingSession(_Session([codex, answered])))  # type: ignore[arg-type]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "completed"


async def test_mg10_a_second_overflow_ends_the_turn_failed_without_another_compaction() -> None:
    inner = _Session([overflowed, overflowed])
    events = await _events(OverflowCompactingSession(inner))  # type: ignore[arg-type]
    assert len(inner.compacts) == 1 and len(inner.tasks) == 2
    assert [e.code for e in events if isinstance(e, EngineError)] == ["context_length_exceeded"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "failed"
    assert _contiguous(events)


@pytest.mark.parametrize(
    "refusal", [CompactUnsupported("no"), CompactFailed("no summary"), CompactReport("failed")], ids=type
)
async def test_mg10_an_engine_that_cannot_compact_hands_the_overflow_on(refusal: Exception | CompactReport) -> None:
    inner = _Session([overflowed], compact_with=refusal)  # type: ignore[arg-type]
    events = await _events(OverflowCompactingSession(inner))  # type: ignore[arg-type]
    assert [type(e).__name__ for e in events] == ["MessageCompleted", "EngineError", "TurnCompleted"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "failed"
    assert _contiguous(events) and len(inner.tasks) == 1


async def test_mg10_an_engine_that_compacted_during_the_turn_is_not_compacted_again() -> None:
    def compacted_then_overflowed() -> list[EngineEvent]:
        return [ContextCompacted(_meta(0), 260_000, 200_000), *overflowed()[1:]]

    inner = _Session([compacted_then_overflowed])
    events = await _events(OverflowCompactingSession(inner))  # type: ignore[arg-type]
    assert inner.compacts == [] and len(inner.tasks) == 1
    assert [type(e).__name__ for e in events] == ["ContextCompacted", "EngineError", "TurnCompleted"]


@pytest.mark.parametrize(
    "attempt",
    [
        lambda: [EngineError(_meta(0), "provider_unavailable", "x"), TurnCompleted(_meta(1), "failed")],
        lambda: [EngineError(_meta(0), "context_length_exceeded", "x"), TurnCompleted(_meta(1), "interrupted")],
        answered,
    ],
    ids=["another-error", "interrupted", "completed"],
)
async def test_mg10_any_other_end_passes_through_unchanged(attempt: Callable[[], list[EngineEvent]]) -> None:
    inner = _Session([attempt])
    events = await _events(OverflowCompactingSession(inner))  # type: ignore[arg-type]
    assert events == attempt() and inner.compacts == []


async def test_mg10_a_turn_stopped_while_compacting_is_not_retried() -> None:
    gate = asyncio.Event()
    inner = _Session([overflowed, answered], compact_gate=gate)
    session = OverflowCompactingSession(inner)  # type: ignore[arg-type]
    running = asyncio.get_running_loop().create_task(_events(session))
    await asyncio.wait_for(inner.compacting.wait(), 5)
    await session.interrupt()
    events = await asyncio.wait_for(running, 5)
    # Between the attempts the engine runs no turn: the stop ends the compaction, and nothing is sent to the engine.
    assert len(inner.tasks) == 1 and inner.interrupts == 0
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "failed"
    assert _contiguous(events)


@pytest.mark.parametrize("attempt", [answered, lambda: [*overflowed()[:1], TurnCompleted(_meta(1), "failed")]])
async def test_mg10_an_end_not_retried_is_handed_on_while_the_engines_turn_is_still_open(
    attempt: Callable[[], list[EngineEvent]],
) -> None:
    """CF-8 on the real lanes: a stop sent on seeing the end must reach a turn that has ended, not none at all."""
    inner = _Session([attempt])
    async for event in OverflowCompactingSession(inner).run_turn(TASK):  # type: ignore[arg-type]
        if isinstance(event, TurnCompleted):
            assert inner.streaming
            break


def test_mg10_the_wrapper_is_applied_once() -> None:
    inner = _Session([])
    wrapped = overflow_compacting(inner)  # type: ignore[arg-type]
    assert overflow_compacting(wrapped) is wrapped


async def test_mg10_the_controller_turn_on_the_fake_engine_completes_compacted() -> None:
    world = World()
    outcome = await world.controller.run_turn(world.run(), task(0, "overflow\nsay recovered"))
    assert (outcome.reason, outcome.final_text, outcome.error_code) == ("completed", "recovered", None)
    assert outcome.auto_compacted is not None and outcome.auto_compacted.outcome == "compacted"
    sunk = [e for _, e in world.sink.events]
    assert [type(e).__name__ for e in sunk].count("ContextCompacted") == 1
    assert not [e for e in sunk if isinstance(e, EngineError)]
    # The overflowed call, the engine's summary, and the retry.
    assert len(world.gateway.model_calls) == 3


async def test_mg10_the_chat_journal_shows_the_compaction_and_the_answer() -> None:
    w = EngineWorld(
        extension_writers={CONTEXT_COMPACTED_EVENT: frozenset({"worker"})},
        extension_models={CONTEXT_COMPACTED_EVENT: PipeshubContextCompactedEvent},
        adapter=ScriptedEngineAdapter(),
    )
    w.admit("run-a", "overflow\nsay recovered")
    for _ in range(6):
        if w.journal.status("run-a") in TERMINAL_STATUSES:
            break
        _ = await w.attempt("run-a")
    assert w.journal.status("run-a") == "completed"
    (data,) = [dict(e.data) for e in w.events("run-a") if e.type == CONTEXT_COMPACTED_EVENT]
    assert (data["trigger"], data["outcome"]) == ("auto", "compacted")
