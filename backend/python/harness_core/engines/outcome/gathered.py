"""The sources an engine run gathered, from the session's provenance ledger (ADR-RUN-01 D1 x D3).

The outcome ledger's ``SourceGathered.ref`` is the source's id in the provenance ledger (``source_id``: its
``SourceRef`` key), so the outcome's ``delivered`` lists the sources D3 recorded: a web page by its address, a
repository file by the host's page at the fetched commit and its plain label, a record or connector item by id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.outcome.ledger import (
    OUTCOME_RECORDED,
    Fact,
    OutcomeLedger,
    SourceGathered,
    ledger_from_events,
    recorded_events,
)
from harness_core.provenance.ledger import provenance_of
from harness_core.provenance.repository import blob_url, label
from harness_core.provenance.types import RepositoryRef, WebPage, source_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.journal.types import JournalEvent, NewEvent


def gathered_sources(ledger: object, run_id: str) -> tuple[SourceGathered, ...]:
    """``SourceGathered`` for each source the run read, as ``ledger``'s provenance names it; none without one."""
    provenance = provenance_of(ledger)
    if provenance is None:
        return ()
    gathered: list[SourceGathered] = []
    for source in provenance.sources(run_id):
        if isinstance(source, WebPage):
            gathered.append(SourceGathered(source_id(source), source.kind, source.url))
        elif isinstance(source, RepositoryRef):
            gathered.append(SourceGathered(source_id(source), source.kind, blob_url(source), label(source)))
        else:
            gathered.append(SourceGathered(source_id(source), source.kind))
    return tuple(gathered)


def with_sources(events: Sequence[JournalEvent], run_id: str, ledger: object) -> OutcomeLedger:
    """The run's outcome ledger from its journal (``ledger_from_events``) with each call the provenance ledger names
    sources for counted by those sources, not by its invocation: what a loop run gathered, as D3 recorded it."""
    base = ledger_from_events(events, run_id=run_id)
    provenance = provenance_of(ledger)
    if provenance is None:
        return base
    covered = {f"invocation:{f.invocation_id}" for f in provenance.facts(run_id) if f.provenance.sources}
    kept = [f for f in base.facts if not (isinstance(f, SourceGathered) and f.ref in covered)]
    out = OutcomeLedger()
    _ = out.record(*gathered_sources(ledger, run_id), *kept)
    return out


def loop_outcome_events(events: Sequence[JournalEvent], run_id: str, ledger: object) -> list[NewEvent]:
    """The ``x.pipeshub.outcome.recorded`` events of what a loop run's journal does not record yet (WP-95's seam, for a
    run with no kernel ledger): its steps, children, files and the sources its provenance ledger names."""
    recorded: set[Fact] = (
        set(ledger_from_events(events, run_id=run_id).facts)
        if any(e.type == OUTCOME_RECORDED and e.run_id == run_id for e in events)
        else set()
    )
    return recorded_events([f for f in with_sources(events, run_id, ledger).facts if f not in recorded])


__all__ = ["gathered_sources", "loop_outcome_events", "with_sources"]
