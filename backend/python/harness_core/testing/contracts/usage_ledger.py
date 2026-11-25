"""Contract suite for ``RunUsageLedger`` (OWD-8, CF-13: the turn's usage is the ledger's).

Provide ``usage_env``: the ledger under test for a run, and how a settled call of that run, or of its child, reaches
the ledger behind it (the model gateway's commit, in PipesHub).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.budget.types import NO_USAGE, LedgerUsage

if TYPE_CHECKING:
    from harness_core.engines.usage import RunUsageLedger


class UsageEnv(Protocol):
    @property
    def ledger(self) -> RunUsageLedger: ...

    async def settle(self, usage: LedgerUsage, *, child: bool = False) -> None:
        """One call of the run (or of a child run of it) settled with ``usage``."""
        ...


class RunUsageLedgerContract:
    @pytest.fixture
    def usage_env(self) -> UsageEnv:
        raise NotImplementedError("provide a usage_env fixture")

    async def test_ul1_a_new_run_has_used_nothing(self, usage_env: UsageEnv) -> None:
        assert (await usage_env.ledger.read()).used == NO_USAGE

    async def test_ul2_every_settled_call_adds_its_usage_and_a_turn_is_the_difference(self, usage_env: UsageEnv) -> None:
        before = (await usage_env.ledger.read()).used
        await usage_env.settle(LedgerUsage(120, 7, 100, 3))
        await usage_env.settle(LedgerUsage(30, 2))
        after = (await usage_env.ledger.read()).used
        assert after - before == LedgerUsage(150, 9, 100, 3)

    async def test_ul3_a_child_runs_calls_are_not_the_runs_own(self, usage_env: UsageEnv) -> None:
        await usage_env.settle(LedgerUsage(10, 1))
        await usage_env.settle(LedgerUsage(500, 50), child=True)
        assert (await usage_env.ledger.read()).used == LedgerUsage(10, 1)


__all__ = ["RunUsageLedgerContract", "UsageEnv"]
