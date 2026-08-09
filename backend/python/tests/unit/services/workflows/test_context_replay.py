"""Deterministic re-execution is a product promise ("generated code is
persisted for deterministic re-execution"), and it rests entirely on step
keys being a function of a call's POSITION in the workflow rather than of
the order the event loop happened to interleave awaits. These tests cover
that, plus the two guards that stop a replay from repeating a side effect:
`ReplayDivergence` and the dry-run WRITE skip.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.workflows.domain.models import JournalEntry, ResultRef, StepOutcome
from app.services.workflows.interface.broker import BrokerResult, RunPrincipal
from app.services.workflows.runtime.replay import ReplayDivergence
from app.services.workflows.sdk.context import Ctx


class _FakeJournal:
    """In-memory `IExecutionJournal` with the two methods `Ctx` uses."""

    def __init__(self, seeded: dict[str, JournalEntry] | None = None) -> None:
        self.entries: dict[str, JournalEntry] = dict(seeded or {})
        self.appended: list[JournalEntry] = []

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:  # noqa: ARG002
        return self.entries.get(step_key)

    async def append(self, entry: JournalEntry) -> None:
        self.appended.append(entry)
        self.entries[entry.step_key] = entry


class _FakeBroker:
    def __init__(self, *, delays: dict[str, float] | None = None) -> None:
        self.calls: list[Any] = []
        self._delays = delays or {}

    async def dispatch(self, call: Any, principal: Any) -> BrokerResult:  # noqa: ARG002
        delay = self._delays.get(str(call.arguments.get("item", "")), 0.0)
        if delay:
            await asyncio.sleep(delay)
        self.calls.append(call)
        return BrokerResult(success=True, data=f"ok:{call.target}")


def _ctx(
    journal: _FakeJournal,
    broker: _FakeBroker,
    *,
    in_replay: bool = False,
    is_dry_run: bool = False,
) -> Ctx:
    return Ctx(
        run_id="run-1",
        journal=journal,
        broker=broker,
        principal=RunPrincipal(org_id="org-1", user_id="u-1", run_id="run-1"),
        in_replay=in_replay,
        is_dry_run=is_dry_run,
    )


def _succeeded(step_key: str, inline: Any = None) -> JournalEntry:
    return JournalEntry(
        run_id="run-1",
        seq=1,
        step_key=step_key,
        entry_kind="tool",
        idempotency_key=step_key,
        outcome=StepOutcome("succeeded"),
        result_ref=ResultRef(inline=inline),
    )


class TestStepKeyDeterminism:
    @pytest.mark.asyncio
    async def test_map_keys_follow_item_position_not_completion_order(self) -> None:
        """The first item is made slow so it finishes last. If keys came from a
        shared counter incremented at await time, its key would drift between
        runs and replay would look up an entry that does not exist."""
        journal = _FakeJournal()
        broker = _FakeBroker(delays={"a": 0.02})
        ctx = _ctx(journal, broker)

        async def handle(child: Ctx, item: str) -> Any:
            return await child.tool("jira__search_issues", item=item)

        results = await ctx.map(handle, ["a", "b", "c"])

        assert results == ["ok:jira__search_issues"] * 3
        assert {e.step_key for e in journal.appended} == {
            "map0[0]/ctx.tool:jira__search_issues#0",
            "map0[1]/ctx.tool:jira__search_issues#0",
            "map0[2]/ctx.tool:jira__search_issues#0",
        }

    @pytest.mark.asyncio
    async def test_map_keys_are_identical_across_two_runs_with_different_timing(self) -> None:
        async def handle(child: Ctx, item: str) -> Any:
            return await child.tool("jira__search_issues", item=item)

        async def keys_for(delays: dict[str, float]) -> set[str]:
            journal = _FakeJournal()
            ctx = _ctx(journal, _FakeBroker(delays=delays))
            await ctx.map(handle, ["a", "b", "c"])
            return {e.step_key for e in journal.appended}

        assert await keys_for({"a": 0.02}) == await keys_for({"c": 0.02})

    @pytest.mark.asyncio
    async def test_sibling_maps_do_not_collide(self) -> None:
        journal = _FakeJournal()
        ctx = _ctx(journal, _FakeBroker())

        async def handle(child: Ctx, item: str) -> Any:
            return await child.tool("jira__search_issues", item=item)

        await ctx.map(handle, ["a"])
        await ctx.map(handle, ["a"])

        assert {e.step_key for e in journal.appended} == {
            "map0[0]/ctx.tool:jira__search_issues#0",
            "map1[0]/ctx.tool:jira__search_issues#0",
        }


class TestReplay:
    @pytest.mark.asyncio
    async def test_journaled_step_is_not_re_executed(self) -> None:
        journal = _FakeJournal({"ctx.tool:jira__search_issues#0": _succeeded(
            "ctx.tool:jira__search_issues#0", inline={"cached": True},
        )})
        broker = _FakeBroker()
        ctx = _ctx(journal, broker, in_replay=True)

        assert await ctx.tool("jira__search_issues") == {"cached": True}
        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_write_step_missing_from_journal_during_replay_diverges(self) -> None:
        """The dangerous case: code changed after a WRITE already executed, so
        re-running it would duplicate the side effect."""
        broker = _FakeBroker()
        ctx = _ctx(_FakeJournal(), broker, in_replay=True)

        with pytest.raises(ReplayDivergence, match="ctx.emit:text#0"):
            await ctx.emit("hello")

        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_write_step_outside_replay_executes_normally(self) -> None:
        broker = _FakeBroker()
        ctx = _ctx(_FakeJournal(), broker)

        await ctx.emit("hello")

        assert len(broker.calls) == 1

    @pytest.mark.asyncio
    async def test_skipped_entry_is_not_re_executed_either(self) -> None:
        """A branch not taken on the original run must stay not-taken; only
        the entry's presence matters, not its outcome."""
        entry = _succeeded("ctx.state.set:k#0").model_copy(
            update={"outcome": StepOutcome("skipped")},
        )
        broker = _FakeBroker()
        ctx = _ctx(_FakeJournal({"ctx.state.set:k#0": entry}), broker, in_replay=True)

        await ctx.state.set("k", "v")

        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_previously_failed_step_raises_instead_of_retrying_silently(self) -> None:
        entry = _succeeded("ctx.tool:jira__search_issues#0").model_copy(
            update={"outcome": StepOutcome("failed")},
        )
        ctx = _ctx(_FakeJournal({"ctx.tool:jira__search_issues#0": entry}), _FakeBroker())

        with pytest.raises(RuntimeError, match="previously failed"):
            await ctx.tool("jira__search_issues")


class TestSleep:
    @pytest.mark.asyncio
    async def test_a_short_pause_is_journaled_once(self) -> None:
        journal = _FakeJournal()
        ctx = _ctx(journal, _FakeBroker())

        await ctx.sleep(0)

        assert [e.step_key for e in journal.appended] == ["ctx.sleep#0"]

    @pytest.mark.asyncio
    async def test_a_journaled_sleep_does_not_pause_again_on_replay(self) -> None:
        journal = _FakeJournal({"ctx.sleep#0": _succeeded("ctx.sleep#0")})
        ctx = _ctx(journal, _FakeBroker(), in_replay=True)

        await ctx.sleep(30)

        assert journal.appended == []

    @pytest.mark.asyncio
    async def test_a_sleep_beyond_the_inline_limit_is_refused(self) -> None:
        """Silently shortening it is the harmful option: the workflow would
        carry on as though the full wait had elapsed."""
        journal = _FakeJournal()
        ctx = _ctx(journal, _FakeBroker())

        with pytest.raises(ValueError, match="wait_for_event"):
            await ctx.sleep(3600)

        assert journal.appended == []


class TestDryRunWrites:
    @pytest.mark.asyncio
    async def test_write_step_is_journaled_as_skipped_and_never_dispatched(self) -> None:
        journal = _FakeJournal()
        broker = _FakeBroker()
        ctx = _ctx(journal, broker, is_dry_run=True)

        await ctx.state.set("counter", 1)

        assert broker.calls == []
        assert [(e.step_key, e.outcome) for e in journal.appended] == [
            ("ctx.state.set:counter#0", StepOutcome("skipped")),
        ]
