"""PERFA-7, PERFA-8 (WRK-37, WRK-38; QA22-F1): a run can always be ended.

The commit that ends a run is within the journal's limit however many calls are open, and a journal
that permanently refuses a commit's events still records the run's end, so no run is claimed for ever.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Raise
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import CommitRejected, NewEvent, RunNotClaimable
from harness_core.kernel.core.messages import ToolMessage
from harness_core.testing import armed

from .support import RUN, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import CommitReceipt, CommitRequest
    from harness_core.kernel.core.messages import AssistantMessage, ToolCall
    from harness_core.kernel.durable import (
        DeltaKind,
        Disposition,
        ModelCall,
        SampledTurn,
        ToolEffect,
    )
    from harness_core.kernel.durable.ports import RunEnding
    from harness_core.testing import Reply
    from harness_core.testing.durable import ScriptedModelPort

OPEN_CALLS = [MAX_COMMIT_EVENTS - 1, MAX_COMMIT_EVENTS, MAX_COMMIT_EVENTS + 1, 500]
ANSWER = {"x.test.answer": frozenset({"worker"})}


def wide(count: int) -> Reply:
    return reply("", *[("lookup", {"q": f"q{i}"}) for i in range(count)])


class _CancelledWhileSampling:
    """The model port of a run whose user presses Stop while the model answers."""

    def __init__(self, world: World, inner: ScriptedModelPort) -> None:
        self.world, self.inner = world, inner

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        sampled = await self.inner.sample(call, on_delta)
        self.world.journal.request_cancel(RUN)
        return sampled


class _Answers:
    """A presenter that journals one event with the run's end."""

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return []

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        return [NewEvent("x.test.answer", {"outcome": ending.outcome}, critical=False)]


def _broken_policy(call: ToolCall, effect: ToolEffect) -> str:
    raise RuntimeError("policy bug")


def terminal_commit(world: World, kind: str) -> list[str]:
    (types,) = [types for command, types in world.journal.commits(RUN) if f":{kind}:" in command]
    return types


async def next_run_pairs_every_call(world: World, calls: int, synthetic: int) -> None:
    """The session's next run sends the provider a valid request: every call of the ended turn is answered."""
    world.admit("run2", text="and now?")
    _, port = scripted(reply("ok"))
    done = await world.run(world.kernel(port), "run2")
    assert done.status == "completed"
    messages = port.calls[-1].messages
    assert_paired(messages)
    results = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(results) == calls and all(m.is_error for m in results)
    assert Counter(m.text for m in results) == Counter({"cancelled": calls})
    assert len(world.events("tool.outcome", RUN)) == synthetic


@pytest.mark.parametrize("calls", OPEN_CALLS)
async def test_perfa_7_a_cancelled_run_ends_in_one_commit_within_the_limit(calls: int) -> None:
    world = World()
    world.admit()
    _, port = scripted(wide(calls))
    done = await world.run(world.kernel(_CancelledWhileSampling(world, port)))  # type: ignore[arg-type]
    assert done.status == "cancelled" and world.journal.status(RUN) == "cancelled"
    types = terminal_commit(world, "cancelled")
    kept = min(calls, MAX_COMMIT_EVENTS)
    assert types == ["tool.outcome"] * kept + ["run.cancelled"]
    await next_run_pairs_every_call(world, calls, synthetic=kept)


@pytest.mark.parametrize("calls", OPEN_CALLS)
async def test_perfa_7_a_failed_run_ends_in_one_commit_within_the_limit(calls: int) -> None:
    world = World()
    world.admit()
    _, port = scripted(wide(calls))
    done = await world.run(world.kernel(port, approval=_broken_policy))
    assert (done.status, done.reason) == ("failed", "kernel_error")
    types = terminal_commit(world, "failed")
    kept = min(calls, MAX_COMMIT_EVENTS)
    assert types == ["tool.outcome"] * kept + ["run.failed"]
    await next_run_pairs_every_call(world, calls, synthetic=kept)


def _crashing_reads(world: World) -> None:
    def crash(_world: World) -> None:
        raise Crash

    world.on_read = crash


@pytest.mark.parametrize("calls", OPEN_CALLS)
async def test_perfa_7_a_crash_looping_run_is_quarantined_in_one_commit_within_the_limit(calls: int) -> None:
    """QA22-F1's endless loop: the quarantine commit carried an outcome per open call and was refused."""
    world = World()
    world.admit()
    _crashing_reads(world)
    _, port = scripted(wide(calls))
    for _ in range(3):
        await world.crash(world.kernel(port))
    done = await world.run(world.kernel(port))
    assert done.status == "quarantined"
    assert world.journal.status(RUN) == "quarantined" and world.journal.claims(RUN) == 4
    (quarantined,) = world.events("run.quarantined")
    assert quarantined.data == {"attempts": 4, "lastErrorType": "crash_loop"}
    kept = min(calls, MAX_COMMIT_EVENTS)
    assert terminal_commit(world, "quarantined") == ["tool.outcome"] * kept + ["run.quarantined"]
    with pytest.raises(RunNotClaimable):
        _ = await world.journal.claim(RUN, worker_id="w")
    world.on_read = None
    await next_run_pairs_every_call(world, calls, synthetic=kept)


async def test_perfa_7_what_the_end_presents_keeps_its_place_in_the_one_commit() -> None:
    world = World(extension_writers=ANSWER)  # type: ignore[arg-type]
    world.admit()
    _, port = scripted(wide(500))
    cancelled = _CancelledWhileSampling(world, port)
    done = await world.run(world.kernel(cancelled, presenter=_Answers()))  # type: ignore[arg-type]
    assert done.status == "cancelled"
    types = terminal_commit(world, "cancelled")
    assert types == ["tool.outcome"] * (MAX_COMMIT_EVENTS - 1) + ["x.test.answer", "run.cancelled"]


async def test_perfa_7_a_call_whose_effect_may_have_happened_keeps_its_outcome_when_not_all_fit() -> None:
    """Of more open calls than a commit holds, the one a later turn must know about is the write that may
    have been applied: it is the last call here, and still journaled as unknown."""
    calls = MAX_COMMIT_EVENTS + 20

    def only_the_last(call: ToolCall, effect: ToolEffect) -> str:
        return "allow" if call.arguments.get("text") == "go" else "ask"

    world = World()
    world.admit()
    writes = [("post_message", {"text": f"m{i}"}) for i in range(calls - 1)]
    _, port = scripted(reply("", *writes, ("post_message", {"text": "go"})))
    with armed({"worker.tool.after_intent": Raise(Crash)}):
        await world.crash(world.kernel(port, approval=only_the_last))
    claim = await world.claim()
    world.journal.request_cancel(RUN)
    done = await world.kernel(port, approval=only_the_last).run_claimed(claim)
    assert done.status == "cancelled"
    outcomes = world.events("tool.outcome")
    assert len(outcomes) == MAX_COMMIT_EVENTS
    last = [e for e in outcomes if e.data["invocationId"] == f"t1.{calls - 1}"]
    assert [e.data["status"] for e in last] == ["error"], "the sent write is not one of the omitted"
    assert [e.data["status"] for e in outcomes if e not in last] == ["cancelled"] * (MAX_COMMIT_EVENTS - 1)


# ---- PERFA-8: a commit the journal refuses for good ----


async def test_perfa_8_a_refused_commit_fails_the_run_on_that_attempt_with_a_terminal_event() -> None:
    """A journal that refuses outcomes (no retry changes that): the run's failure is still recorded, as the
    transition alone, and the run is not claimed again."""
    world = World(journal_refuses=frozenset({"tool.outcome"}))
    world.admit()
    _, port = scripted(wide(3), reply("never"))
    done = await world.run(world.kernel(port))
    assert (done.status, done.reason) == ("failed", "commit_rejected")
    assert world.journal.status(RUN) == "failed" and world.journal.claims(RUN) == 1
    assert terminal_commit(world, "failed") == ["run.failed"]
    assert world.events("tool.outcome") == []
    with pytest.raises(RunNotClaimable):
        _ = await world.journal.claim(RUN, worker_id="w")
    await next_run_pairs_every_call(world, 3, synthetic=0)


async def test_perfa_8_a_run_past_the_attempt_cap_is_quarantined_even_when_its_outcomes_are_refused() -> None:
    world = World(journal_refuses=frozenset({"tool.outcome"}))
    world.admit()
    _crashing_reads(world)
    _, port = scripted(wide(3))
    for _ in range(3):
        await world.crash(world.kernel(port))
    done = await world.run(world.kernel(port))
    assert done.status == "quarantined"
    assert terminal_commit(world, "quarantined") == ["run.quarantined"]
    assert world.journal.status(RUN) == "quarantined" and world.journal.claims(RUN) == 4


class _RefusesEvents:
    """A journal that refuses every commit that carries events, whatever they are."""

    def __init__(self, world: World) -> None:
        self._journal = world.journal
        self.claim = world.journal.claim
        self.read = world.journal.read
        self.last_seq = world.journal.last_seq
        self.renew = world.journal.renew
        self.release = world.journal.release
        self.refused = 0

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        if request.events:
            self.refused += 1
            raise CommitRejected("VALIDATION_ERROR")
        return await self._journal.commit(request)


async def test_perfa_8_a_run_whose_every_commit_is_refused_ends_after_one_attempt() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("an answer"))
    refusing = _RefusesEvents(world)
    done: Disposition = await world.run(replace(world.kernel(port), journal=refusing))  # type: ignore[arg-type]
    assert (done.status, done.reason) == ("failed", "commit_rejected")
    assert world.journal.status(RUN) == "failed" and world.journal.claims(RUN) == 1
    assert refusing.refused == 1, "the refused commit is not retried: a validation error is not transient"
