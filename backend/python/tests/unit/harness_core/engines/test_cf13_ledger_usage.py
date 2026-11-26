"""CF-13, OWD-8: a turn's usage is the gateway ledger's, whatever the engine reports.

opencode over ACP answers a turn with its last model call's usage only (WP-04's finding); the controller takes the
turn's usage from the run's ledger account instead, read before and after the turn, so every adapter's recorded
usage equals what the gateway billed. The engine's own report stays the fallback when the ledger cannot be read.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import (
    ZERO,
    Amount,
    Available,
    Cap,
    LedgerUsage,
    Remaining,
    RunAccountRef,
)
from harness_core.engines.events import TurnCompleted, UsageReported
from harness_core.engines.usage import AccountUsageLedger
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.usage_ledger import RunUsageLedgerContract
from tests.unit.harness_core.engines.test_controller import WatchingAdapter, World, task

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent

TWO_CALLS = 'tool lookup {"q": "usage"}\nwrite notes.md hi\nsay counted'


class _LastCallOnly:
    """An engine session that reports only its turn's last model call, as opencode over ACP does."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401 - any EngineSession
        self._inner = inner

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)

    async def run_turn(self, task: Any) -> AsyncIterator[EngineEvent]:  # noqa: ANN401
        held: UsageReported | None = None
        async for event in self._inner.run_turn(task):
            if isinstance(event, UsageReported):
                held = event
                continue
            if held is not None and isinstance(event, TurnCompleted):
                yield held
            yield event


class _UnderReportingAdapter(WatchingAdapter):
    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        return _LastCallOnly(await super().open(sandbox, launch, restore, correlation=correlation))


class _Unreadable:
    async def read(self) -> Remaining:
        raise ConnectionError("node down")


class _Fixed:
    """A ledger that never moves: every call the engine reports is one the gateway never billed."""

    async def read(self) -> Remaining:
        return Remaining(Available(None, None), Amount(0, 8), ZERO, exhausted=False, used=LedgerUsage(7, 1))


def _billed(world: World) -> LedgerUsage:
    return LedgerUsage(sum(u.input_tokens for u in world.gateway.billed), sum(u.output_tokens for u in world.gateway.billed))


async def test_cf13_the_turns_usage_is_the_ledgers_when_the_engine_reports_only_its_last_call() -> None:
    world = World(adapter=_UnderReportingAdapter())
    outcome = await world.controller.run_turn(world.run(usage=world.gateway), task(0, TWO_CALLS))
    assert outcome.reason == "completed"
    assert len(world.gateway.billed) >= 2
    reported = [e for _, e in world.sink.events if isinstance(e, UsageReported)]
    assert len(reported) == 1, "the engine reported only its last call"
    assert (outcome.input_tokens, outcome.output_tokens) == (_billed(world).input_tokens, _billed(world).output_tokens)
    assert outcome.usage_source == "ledger"
    assert outcome.input_tokens > reported[0].input_tokens


async def test_cf13_a_second_turn_counts_only_its_own_calls() -> None:
    world = World()
    run = world.run(usage=world.gateway)
    first = await world.controller.run_turn(run, task(0, TWO_CALLS))
    before = _billed(world)
    second = await world.controller.run_turn(run, task(1, "say again"))
    after = _billed(world)
    assert (first.input_tokens, first.output_tokens) == (before.input_tokens, before.output_tokens)
    assert (second.input_tokens, second.output_tokens) == (
        after.input_tokens - before.input_tokens,
        after.output_tokens - before.output_tokens,
    )


async def test_cf13_without_a_ledger_the_engines_report_is_the_turns_usage() -> None:
    world = World()
    outcome = await world.controller.run_turn(world.run(), task(0, TWO_CALLS))
    assert outcome.usage_source == "engine"
    assert (outcome.input_tokens, outcome.output_tokens) == (_billed(world).input_tokens, _billed(world).output_tokens)


async def test_cf13_an_unreadable_ledger_falls_back_to_the_engines_report(caplog: pytest.LogCaptureFixture) -> None:
    world = World()
    with caplog.at_level(logging.WARNING):
        outcome = await world.controller.run_turn(world.run(usage=_Unreadable()), task(0, "say hi"))
    assert outcome.reason == "completed" and outcome.usage_source == "engine"
    assert outcome.input_tokens == _billed(world).input_tokens
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # The usage read before and after the turn; the writing reserve may name the same cause once (R15-MONEY).
    assert [r.__dict__["harness"]["reasonCode"] for r in warnings if r.harness_event == "engine.turn.usage_unreadable"] == [  # type: ignore[attr-defined]
        "usage_unreadable",
        "usage_unreadable",
    ]
    assert {r.__dict__["harness"]["reasonCode"] for r in warnings} == {"usage_unreadable"}


async def test_cf13_an_engine_reporting_more_than_the_ledger_is_flagged(caplog: pytest.LogCaptureFixture) -> None:
    world = World()
    with caplog.at_level(logging.INFO):
        outcome = await world.controller.run_turn(world.run(usage=_Fixed()), task(0, "say hi"))
    assert (outcome.usage_source, outcome.input_tokens, outcome.output_tokens) == ("ledger", 0, 0)
    [line] = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.turn.usage"]
    assert line.levelno == logging.WARNING and line.__dict__["harness"]["reasonCode"] == "usage_over_ledger"



class _AccountEnv:
    """``AccountUsageLedger`` over the in-memory ledger, settled as the model gateway settles a call."""

    def __init__(self) -> None:
        self.budget = InMemoryBudgetLedger(ManualClock())
        self.run, self.child = RunAccountRef("org-1", "run-1"), RunAccountRef("org-1", "child-1")
        self.ledger = AccountUsageLedger(self.budget, self.run)
        self._calls = 0

    async def settle(self, usage: LedgerUsage, *, child: bool = False) -> None:
        await self.budget.open_root(self.run, Cap())
        await self.budget.open_child(self.run, self.child, Cap())
        self._calls += 1
        account = self.child if child else self.run
        held = await self.budget.reserve(account, f"call-{self._calls}", Amount(0, 1), minimum=ZERO, ttl_s=60)
        _ = await self.budget.commit(held, Amount(0, 1), usage=usage)


class TestAccountUsageLedger(RunUsageLedgerContract):
    @pytest.fixture
    async def usage_env(self) -> _AccountEnv:
        env = _AccountEnv()
        await env.budget.open_root(env.run, Cap())
        return env
