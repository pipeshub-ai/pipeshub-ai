"""WP91-ACPCOMPACT-W (C3.67, MG-10): where an engine cannot compact itself, the harness restarts it from a summary.

ACP has no request to compact a session and Gemini CLI 0.63 fails the prompt on an overflow, so the chain the
controller drives every engine through (``engine_session_chain``) takes the raw session's ``RestartsFromSummary``,
when it offers one, as the compaction of last resort: the engine's own ``compact`` first, the harness's summary and a
fresh engine session when the engine has none. Everything else is MG-10's wrapper unchanged: one ``ContextCompacted``,
one retry at most, none once the turn is stopping, the overflow handed on when neither can run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.context.user_compact import CompactReport
from harness_core.engines.chain import engine_session_chain
from harness_core.engines.errors import CompactFailed, CompactUnsupported
from harness_core.engines.events import ContextCompacted, EngineError, TurnCompleted
from harness_core.engines.ports import RestartsFromSummary
from tests.unit.harness_core.engines.test_mg10_overflow_compaction import (
    TASK,
    _Session,
    answered,
    overflowed,
)

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp


class _Restarting(_Session):
    """A session whose engine cannot compact, which the harness can restart from a summary."""

    def __init__(self, *, restart_with: CompactReport | Exception, **kwargs: object) -> None:
        super().__init__(compact_with=CompactUnsupported("no compaction"), **kwargs)  # pyright: ignore[reportArgumentType]
        self.restart_with = restart_with
        self.restarts: list[EngineTask | FollowUp] = []

    async def restart_from_summary(self, task: EngineTask | FollowUp) -> CompactReport:
        self.restarts.append(task)
        if isinstance(self.restart_with, Exception):
            raise self.restart_with
        return self.restart_with


async def _events(session: object, task: EngineTask | FollowUp = TASK) -> list[EngineEvent]:
    chain = engine_session_chain(session)  # pyright: ignore[reportArgumentType]
    return [event async for event in chain.run_turn(task)]


def test_wp91_acpcompact_w1_the_port_is_a_runtime_protocol() -> None:
    assert isinstance(_Restarting(restart_with=CompactReport("compacted"), attempts=[]), RestartsFromSummary)
    assert not isinstance(_Session(attempts=[]), RestartsFromSummary)


async def test_wp91_acpcompact_w2_an_engine_that_cannot_compact_is_restarted_from_a_summary_and_retried() -> None:
    session = _Restarting(restart_with=CompactReport("compacted", 250_000, 2_000, user_messages_kept=True), attempts=[overflowed, answered])
    events = await _events(session)
    assert session.compacts == [(None, "Be brief.")]
    assert session.restarts == [TASK]
    assert session.tasks == [TASK, TASK]
    (compacted,) = [e for e in events if isinstance(e, ContextCompacted)]
    assert (compacted.before_tokens, compacted.after_tokens, compacted.user_messages_kept) == (250_000, 2_000, True)
    assert not [e for e in events if isinstance(e, EngineError)]
    ended = events[-1]
    assert isinstance(ended, TurnCompleted) and (ended.reason, ended.final_text) == ("completed", "recovered")
    seqs = [e.meta.seq for e in events]
    assert seqs == sorted(set(seqs))


async def test_wp91_acpcompact_w3_a_restart_that_cannot_run_hands_the_overflow_on() -> None:
    session = _Restarting(restart_with=CompactFailed("the agent has stopped"), attempts=[overflowed])
    events = await _events(session)
    assert session.restarts == [TASK] and session.tasks == [TASK]
    assert [e.code for e in events if isinstance(e, EngineError)] == ["context_length_exceeded"]
    assert isinstance(events[-1], TurnCompleted) and events[-1].reason == "failed"


async def test_wp91_acpcompact_w4_an_engine_that_compacts_itself_is_never_restarted() -> None:
    session = _Restarting(restart_with=CompactReport("compacted"), attempts=[overflowed, answered])
    session.compact_with = CompactReport("compacted", 250_000, 9_000)
    _ = await _events(session)
    assert session.restarts == []


async def test_wp91_acpcompact_w5_a_session_without_the_port_hands_the_overflow_on_as_before() -> None:
    session = _Session(attempts=[overflowed], compact_with=CompactUnsupported("no compaction"))
    events = await _events(session)
    assert [e.code for e in events if isinstance(e, EngineError)] == ["context_length_exceeded"]
