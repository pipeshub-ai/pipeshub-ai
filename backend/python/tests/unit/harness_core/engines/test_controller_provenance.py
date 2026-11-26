"""EA-2 (P2.5a): an engine run publishes its files with the source records its ledger answers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.durable.ledger import ItemLabelLedger
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import ORG, SESSION, EngineWorld

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.journal.types import JournalEvent

RUN = "run-provenance"


class _Fixed:
    """A ledger whose answer the test chooses, recording what it was fed."""

    def __init__(self, sources: frozenset[str] | None) -> None:
        self.sources = sources
        self.seen: list[int] = []
        self.asked: list[str] = []

    def apply(self, events: Sequence[JournalEvent]) -> None:
        self.seen.extend(e.seq for e in events)

    def source_records(self, run_id: str) -> frozenset[str] | None:
        self.asked.append(run_id)
        return self.sources


async def _published_sources(world: EngineWorld) -> frozenset[str] | None:
    world.admit(RUN, "write out/report.md quarterly numbers\nsay all done")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    binding = RunBinding(ORG, SESSION, RUN)
    (artifact,) = world.artifacts.listing(binding)
    return (await world.artifacts.sources_of(binding, [artifact.artifact_id]))[artifact.artifact_id]


async def test_ea2_published_files_carry_the_ledgers_sources() -> None:
    ledger = _Fixed(frozenset({"rec-1", "rec-2"}))
    world = EngineWorld(ledger=lambda: ledger)
    assert await _published_sources(world) == frozenset({"rec-1", "rec-2"})
    assert set(ledger.asked) == {RUN}
    # Fed the whole session stream at assembly, then each commit once: no event twice, none skipped.
    assert ledger.seen[0] == 1
    assert sorted(set(ledger.seen)) == list(range(1, max(ledger.seen) + 1))


async def test_ea2_an_unknown_answer_keeps_the_files_hidden() -> None:
    world = EngineWorld(ledger=lambda: _Fixed(None))
    assert await _published_sources(world) is None


async def test_ea2_no_ledger_keeps_the_files_hidden_as_before() -> None:
    assert await _published_sources(EngineWorld()) is None


async def test_ea2_the_reference_ledger_over_a_run_that_read_nothing() -> None:
    assert await _published_sources(EngineWorld(ledger=ItemLabelLedger)) == frozenset()
