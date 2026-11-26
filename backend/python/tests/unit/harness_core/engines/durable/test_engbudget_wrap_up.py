"""ENGBUDGET: an engine run a limit stops before it wrote anything answers from what its tools returned.

A Codex child made 22 Jira calls, its next model call was refused for budget, and the run's answer was the
no-model fallback (``engine.kernel.cut_short limit=budget partial=False``). The kernel now holds the run's wrap-up
before each turn and, when the engine wrote nothing, answers on it from the journaled tool results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import (
    EngineError,
    EngineEventMeta,
    TextDelta,
    TurnCompleted,
)
from harness_core.kernel.core.turn_limit import is_cut_short_fallback
from harness_core.testing.engines.durable import (
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)
from harness_core.testing.engines.script import ModelUsage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from harness_core.engines.durable.ports import GatheredResult
    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.journal.types import Claim
    from harness_core.kernel.core.turn_limit import CutShort

RUN = "run-engbudget"
ANSWER = "Pick up PP-7 first."


@dataclass
class FakeWrapUp:
    answer_text: str | None = ANSWER
    holds: int = 0
    released: int = 0
    asked: list[tuple[str, tuple[GatheredResult, ...], str]] = field(default_factory=list)

    async def hold(self) -> bool:
        self.holds += 1
        return True

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None:
        del earlier
        self.asked.append((request, tuple(gathered), reason))
        return self.answer_text

    async def release(self) -> None:
        self.released += 1


class _StoppedForBudget(ScriptedEngineAdapter):
    """The engine runs its tools, then the gateway refuses its next call: the turn ends ``failed`` for budget with
    what it wrote so far (nothing, unless ``keep_text``)."""

    def __init__(self, *, keep_text: bool = False) -> None:
        super().__init__()
        self.keep_text = keep_text

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        return _Stopped(await super().open(sandbox, launch, restore, correlation=correlation), self.keep_text)


class _Stopped:
    def __init__(self, inner: object, keep_text: bool) -> None:
        self._inner = inner
        self._keep_text = keep_text

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TextDelta) and not self._keep_text:
                continue
            if isinstance(event, TurnCompleted):
                text = event.final_text if self._keep_text else ""
                yield EngineError(EngineEventMeta(task.turn_id, event.meta.seq), "budget_insufficient", "refused")
                yield TurnCompleted(EngineEventMeta(task.turn_id, event.meta.seq + 1), "failed", final_text=text)
                return
            yield event


def _world(adapter: ScriptedEngineAdapter, wrap_up: FakeWrapUp | None) -> EngineWorld:
    def build(claim: Claim) -> FakeWrapUp | None:
        del claim
        return wrap_up

    return EngineWorld(adapter=adapter, wrap_up=build)


async def _answer_text(world: EngineWorld) -> str:
    return await world.plain_answer()


async def test_a_budget_stop_with_results_answers_on_the_wrap_up() -> None:
    wrap_up = FakeWrapUp()
    world = _world(_StoppedForBudget(), wrap_up)
    world.admit(RUN, 'tool lookup {"jql": "project = PP"}\ntool lookup {"jql": "priority = High"}\nsay done')
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    completed = [e.data for e in world.journal.events(SESSION) if e.type == "run.completed"]
    assert completed == [{"outcome": "answered", "truncatedBy": "budget"}]
    answer = await _answer_text(world)
    assert answer.startswith(ANSWER) and "stopped early" in answer and not is_cut_short_fallback(answer)
    ((request, gathered, reason),) = wrap_up.asked
    assert "project = PP" in request and reason == "budget"
    assert [g.tool for g in gathered] == ["lookup", "lookup"]
    assert "priority = High" in gathered[1].text
    assert wrap_up.holds == 1 and wrap_up.released == 1


async def test_without_results_the_answer_is_the_no_model_one() -> None:
    wrap_up = FakeWrapUp()
    world = _world(_StoppedForBudget(), wrap_up)
    world.admit(RUN, "say nothing to see")
    assert (await world.attempt(RUN)).status == "completed"
    assert wrap_up.asked == []
    assert is_cut_short_fallback(await _answer_text(world))


@pytest.mark.parametrize("answer_text", [None, ""])
async def test_a_wrap_up_that_cannot_answer_leaves_the_no_model_one(answer_text: str | None) -> None:
    world = _world(_StoppedForBudget(), FakeWrapUp(answer_text=answer_text))
    world.admit(RUN, 'tool lookup {"jql": "project = PP"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    assert is_cut_short_fallback(await _answer_text(world))


async def test_wp70_cut_the_engines_last_text_is_the_wrap_ups_draft_not_the_answer() -> None:
    """WP70-CUT (amends ENGBUDGET's "what the engine wrote stays its answer"): a limit cuts an engine mid-work, so its
    last text is as often a progress remark as an answer. The wrap-up answers from the results, the
    engine's notes and that text, which it reads as an unfinished draft."""
    wrap_up = FakeWrapUp()
    world = _world(_StoppedForBudget(keep_text=True), wrap_up)
    world.admit(RUN, 'tool lookup {"jql": "project = PP"}\nsay PP-3 looks first')
    assert (await world.attempt(RUN)).status == "completed"
    ((_, gathered, _),) = wrap_up.asked
    assert [g.tool for g in gathered] == ["lookup", "engine draft"] and gathered[-1].text == "PP-3 looks first"
    assert (await _answer_text(world)).startswith(ANSWER)


async def test_wp70_cut_without_a_wrap_up_the_engines_text_stays_its_answer() -> None:
    """WP70-CUT: an unmetered run (no wrap-up) that owes no file keeps what its engine wrote, flagged partial."""
    world = _world(_StoppedForBudget(keep_text=True), None)
    world.admit(RUN, 'tool lookup {"jql": "project = PP"}\nsay PP-3 looks first')
    assert (await world.attempt(RUN)).status == "completed"
    answer = await _answer_text(world)
    assert "PP-3 looks first" in answer and "This result is partial." in answer


async def test_a_completed_run_releases_its_reserve_unused() -> None:
    wrap_up = FakeWrapUp()
    world = _world(ScriptedEngineAdapter(), wrap_up)
    world.admit(RUN, "say hi")
    assert (await world.attempt(RUN)).status == "completed"
    assert wrap_up.asked == [] and wrap_up.holds == 1 and wrap_up.released == 1


@dataclass
class _ChargedWrapUp(FakeWrapUp):
    """A wrap-up whose answer is a model call the run's ledger charges (here the conformance gateway's)."""

    world: EngineWorld | None = None

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None:
        assert self.world is not None
        self.world.gateway.billed.append(ModelUsage(input_tokens=300, output_tokens=20))
        return await super().answer(request, gathered, reason, earlier=earlier)


async def test_c901_the_wrap_up_calls_usage_is_journaled_so_the_run_adds_up_to_its_ledger() -> None:
    """C9.01, EV-10: the wrap-up's model call is charged after the turn; its ``usage.recorded`` makes the run's
    journal usage equal what its ledger account holds."""
    wrap_up = _ChargedWrapUp()
    world = EngineWorld(adapter=_StoppedForBudget(), wrap_up=lambda claim: wrap_up, metered=True)
    wrap_up.world = world
    world.admit(RUN, 'tool lookup {"jql": "project = PP"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    events = world.events(RUN)
    [recorded] = [e.data for e in events if e.type == "usage.recorded"]
    assert recorded["tokens"] == {  # type: ignore[index]
        "inputTokens": 300, "outputTokens": 20, "cacheReadTokens": 0, "cacheWriteTokens": 0, "inputIncludesCached": True,
    }
    journal = [e.data["usage"] for e in events if e.type == "model.response"] + [recorded["tokens"]]  # type: ignore[index]
    ledger = (await world.gateway.read()).used
    assert sum(u["inputTokens"] for u in journal) == ledger.input_tokens  # type: ignore[index]
    assert sum(u["outputTokens"] for u in journal) == ledger.output_tokens  # type: ignore[index]
