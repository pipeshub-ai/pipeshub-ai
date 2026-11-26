"""FIX-429: a provider that keeps refusing the loop's calls with 429 is waited out on a bound, then the run ends.

A chat's model answered every call with "429 Too Many Requests". The loop yielded ``llm_backoff`` for the provider's
Retry-After (20 s) and was re-claimed, without end: the person watched a spinner for hours. The loop now waits on the
engine kernel's bound (``provider_wait``): the waits are counted in a journaled slot, so the count survives every
re-claim, and past the bound the run completes cut short by the provider (``truncatedBy: provider``) with what it
gathered, written without a model. A call the provider answers starts the count again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from harness_core.engines.outcome.locales.en import MESSAGES
from harness_core.kernel.durable.ports import ModelBackoff
from harness_core.kernel.durable.provider_wait import (
    PROVIDER_RECHECK_S,
    PROVIDER_WAIT_BUDGET_S,
    PROVIDER_WAIT_SLOT,
    ProviderWaits,
)
from harness_core.testing import ErrorSpec, Reply
from tests.unit.harness_core.durable.support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.durable import (
        DeltaKind,
        Disposition,
        ModelCall,
        SampledTurn,
    )
    from harness_core.testing.durable import ScriptedModelPort

LLM_BACKOFF = "llm_backoff"


def _world() -> World:
    world = World()
    world.admit()
    return world


def _refused(retry_after_s: float = 20.0) -> Reply:
    return Reply(error=ErrorSpec(kind="rate_limited", retry_after_s=retry_after_s))


def _waits(world: World) -> list[dict[str, Any]]:
    """Every value the provider-wait slot was journaled with, in order."""
    return [
        cast("dict[str, Any]", e.data["slots"])[PROVIDER_WAIT_SLOT]
        for e in world.events("loop.state_saved")
        if PROVIDER_WAIT_SLOT in cast("dict[str, Any]", e.data["slots"])
    ]


async def _answer(world: World) -> str:
    final = [e for e in world.events("message.completed") if e.data.get("isFinal")]
    return (await world.blob(cast("dict[str, Any]", final[-1].data["textRef"]))).decode()


async def _attempt(world: World, port: ScriptedModelPort) -> Disposition:
    """One claim of the run on a fresh kernel, as the worker re-claims it after each yield."""
    return await world.run(world.kernel(port))


def _wait_out(world: World, delay_s: float) -> None:
    assert world.journal.status("run1") == "queued"
    _ = world.db_clock.advance(delay_s + 1)


def test_fix429_policy_is_one_bound_of_waits_and_time() -> None:
    assert ProviderWaits().next_delay() == PROVIDER_RECHECK_S[0]
    assert ProviderWaits().next_delay(asked_s=20.0) == 20.0, "the provider's Retry-After is honoured"
    assert ProviderWaits(1, 15.0).next_delay(asked_s=1.0) == PROVIDER_RECHECK_S[1], "never sooner than the schedule"
    assert ProviderWaits.of_count(len(PROVIDER_RECHECK_S)).next_delay() is None
    assert ProviderWaits(2, PROVIDER_WAIT_BUDGET_S).next_delay() is None, "the time is bounded on its own"
    assert PROVIDER_WAIT_BUDGET_S <= 300, "a provider outage holds the run at most five minutes"
    assert ProviderWaits.of_slot(ProviderWaits(3, 110.0).slot()) == ProviderWaits(3, 110.0)
    for unreadable in (None, "x", {"count": "2", "waitedS": 1}, {"count": True, "waitedS": 1}, {"count": 1}):
        assert ProviderWaits.of_slot(unreadable) == ProviderWaits()


async def test_fix429_a_provider_that_always_refuses_ends_the_run_at_the_bound_with_what_it_gathered() -> None:
    world = _world()
    delays = [20.0, 30.0, 60.0, 120.0]
    _, port = scripted(reply("", ("lookup", {"q": "travel cap"})), *(_refused() for _ in range(len(delays) + 1)))
    for count, delay in enumerate(delays, start=1):
        yielded = await _attempt(world, port)
        assert (yielded.status, yielded.reason) == ("yielded", LLM_BACKOFF), yielded
        # Counted in the journal, so the next claim (a new kernel) reads where this one stopped.
        assert _waits(world)[-1] == {"count": count, "waitedS": sum(delays[:count])}
        _wait_out(world, delay)

    done = await _attempt(world, port)
    assert done.status == "completed", done
    assert dict(world.events("run.completed")[0].data) == {"outcome": "answered", "truncatedBy": "provider"}
    assert [e.data["reason"] for e in world.events("run.yielded")] == [LLM_BACKOFF] * len(delays)
    answer = await _answer(world)
    assert MESSAGES["outcome.reason.provider.stopped"] in answer
    assert MESSAGES["outcome.reason.provider.continue"] in answer
    assert len(port.calls) == len(delays) + 2, "no answer-only call to a provider that refuses every call"
    assert "run.failed" not in world.types() and "run.quarantined" not in world.types()


async def test_fix429_a_provider_that_recovers_completes_normally_and_its_next_outage_gets_the_whole_bound() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("lookup", {"q": "a"})),
        _refused(1.0),
        _refused(1.0),
        reply("", ("lookup", {"q": "b"})),
        _refused(1.0),
        reply("The travel cap is 400 USD."),
    )
    for delay in (15.0, 30.0, 15.0):
        yielded = await _attempt(world, port)
        assert (yielded.status, yielded.reason) == ("yielded", LLM_BACKOFF), yielded
        _wait_out(world, delay)
    done = await _attempt(world, port)
    assert done.status == "completed", done
    completed = dict(world.events("run.completed")[0].data)
    assert completed == {"outcome": "answered"}, completed
    assert await _answer(world) == "The travel cap is 400 USD."
    assert _waits(world) == [
        {"count": 1, "waitedS": 15.0},
        {"count": 2, "waitedS": 45.0},
        {"count": 0, "waitedS": 0.0},
        {"count": 1, "waitedS": 15.0},
        {"count": 0, "waitedS": 0.0},
    ]


async def test_fix429_the_budget_ledger_s_own_wait_is_not_counted_against_the_provider() -> None:
    world = _world()
    _, port = scripted(reply("done"))
    ledger_waits = len(PROVIDER_RECHECK_S) + 2
    original = port.sample
    calls = 0

    async def ledger_down(
        call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        nonlocal calls
        calls += 1
        if calls <= ledger_waits:
            raise ModelBackoff(5.0, provider=False)
        return await original(call, on_delta)

    port.sample = ledger_down  # type: ignore[method-assign]
    for _ in range(ledger_waits):
        yielded = await _attempt(world, port)
        assert (yielded.status, yielded.reason) == ("yielded", LLM_BACKOFF), yielded
        _wait_out(world, 5.0)
    done = await _attempt(world, port)
    assert done.status == "completed" and "truncatedBy" not in world.events("run.completed")[0].data
    assert _waits(world) == []
