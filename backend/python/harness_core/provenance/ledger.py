"""``ProvenanceLedger``: the session's one store of what its tool results carried (ADR-RUN-01 D3).

Fed the session's stream in seq order (as every ``SourceLedger`` is), it keeps each run's facts: the provenance
records its tool port journaled (``x.pipeshub.provenance.recorded``) and, for everything without one, what its
``Replay`` derives from the run's other events (an older run's outcomes and labels, the pre-run steps, recalled
memories, imported history). Every consumer reads the same facts: the flow guard the readers and integrity, the
answer its citable sources (web pages and repository files alike), audit the whole record.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from harness_core.provenance.stamp import PROVENANCE_EVENT
from harness_core.provenance.types import (
    NO_CONTENT,
    RecordRef,
    RepositoryRef,
    ResultProvenance,
    WebPage,
    attributed,
    join_all,
    provenance_from_wire,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent
    from harness_core.provenance.types import SourceAttributes, SourceRef


@dataclass(frozen=True, slots=True)
class ProvenanceFact:
    run_id: str
    provenance: ResultProvenance
    invocation_id: str | None
    tool_id: str
    rule: str
    # Journaled by the stamp; otherwise derived by the replay from the run's older events.
    recorded: bool

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {
            "runId": self.run_id, "rule": self.rule, "recorded": self.recorded, "provenance": self.provenance.to_wire(),
        }
        if self.invocation_id is not None:
            wire["invocationId"] = self.invocation_id
        if self.tool_id:
            wire["toolId"] = self.tool_id
        return wire


class Replay(Protocol):
    """What a run's events say about the results the stamp did not record (``recorded``: the invocations it did)."""

    def facts(self, events: Sequence[JournalEvent], run_id: str, recorded: frozenset[str]) -> list[ProvenanceFact]: ...


_UNKNOWN = ResultProvenance("unknown", "first_party", ())


class CoreReplay:
    """The harness core's reading of an outcome without a record: its item labels (REPORT 6.5.1). A refusal or a
    cancellation is the harness's own text; public readers with no sources is public; sources by id are records;
    anything else is unknown. A host with richer evidence provides its own ``Replay``."""

    def facts(self, events: Sequence[JournalEvent], run_id: str, recorded: frozenset[str]) -> list[ProvenanceFact]:
        denied = {str(e.data.get("invocationId")) for e in events if e.type == "tool.denied"}
        facts: list[ProvenanceFact] = []
        for event in events:
            if event.type == "session.imported":
                facts.append(ProvenanceFact(run_id, _UNKNOWN, None, "", "imported", recorded=False))
            if event.type != "tool.outcome":
                continue
            invocation = str(event.data.get("invocationId"))
            if invocation in recorded:
                continue
            provenance = (
                NO_CONTENT if invocation in denied or event.data.get("status") == "cancelled" else outcome_labels(event.data)
            )
            facts.append(ProvenanceFact(run_id, provenance, invocation, "", "labels", recorded=False))
        return facts


def outcome_labels(data: dict[str, JsonValue] | object) -> ResultProvenance:
    """A tool outcome's item labels as a provenance (REPORT 6.5.1)."""
    labels = cast("dict[str, JsonValue]", data).get("labels") if isinstance(data, dict) else None
    labels = labels if isinstance(labels, dict) else {}
    integrity = "external" if labels.get("integrity") == "external" else "first_party"
    confidentiality = labels.get("confidentiality")
    if not isinstance(confidentiality, dict):
        return ResultProvenance("unknown", integrity, ())
    sources = confidentiality.get("sources")
    if confidentiality.get("readers") == "public" and sources in (None, []):
        return ResultProvenance("public", integrity, ())
    if isinstance(sources, list) and sources and all(isinstance(s, str) and s for s in sources):
        return ResultProvenance("principals", integrity, tuple(RecordRef(str(s)) for s in sources))
    return ResultProvenance("unknown", integrity, ())


class ProvenanceLedger:
    def __init__(self, replay: Replay | None = None) -> None:
        self._replay: Replay = replay if replay is not None else CoreReplay()
        self._seq = 0
        self._order: list[str] = []
        self._events: dict[str, list[JournalEvent]] = {}
        self._recorded: dict[str, dict[str, ProvenanceFact]] = {}
        self._facts: dict[str, list[ProvenanceFact]] = {}
        self._attributes: dict[str, SourceAttributes] | None = None

    def apply(self, events: Sequence[JournalEvent]) -> None:
        for event in events:
            if event.seq <= self._seq:
                continue
            self._seq = event.seq
            run = event.run_id
            if run is None:
                continue
            if run not in self._events:
                self._order.append(run)
                self._events[run] = []
            self._events[run].append(event)
            _ = self._facts.pop(run, None)
            self._attributes = None
            if event.type == PROVENANCE_EVENT:
                self._record(run, event.data)

    def _record(self, run: str, data: dict[str, JsonValue]) -> None:
        invocation = data.get("invocationId")
        if not isinstance(invocation, str) or not invocation:
            return
        provenance = provenance_from_wire(data.get("provenance"))
        tool_id, rule = data.get("toolId"), data.get("rule")
        fact = ProvenanceFact(
            run,
            # A record nobody can read back names nothing checkable: unknown, never public.
            provenance if provenance is not None else _UNKNOWN,
            invocation,
            tool_id if isinstance(tool_id, str) else "",
            rule if isinstance(rule, str) else "",
            recorded=True,
        )
        _ = self._recorded.setdefault(run, {}).setdefault(invocation, fact)

    # ---- reading ----

    @property
    def runs(self) -> tuple[str, ...]:
        """The session's runs, in the order the stream first named them."""
        return tuple(self._order)

    def events_of(self, run_id: str) -> Sequence[JournalEvent]:
        return self._events.get(run_id, ())

    def facts(self, run_id: str) -> list[ProvenanceFact]:
        """The run's facts: what was recorded, then what the replay derives for the rest, in stream order."""
        cached = self._facts.get(run_id)
        if cached is not None:
            return cached
        events = self._events.get(run_id, [])
        recorded = self._recorded.get(run_id, {})
        derived = self._replay.facts(events, run_id, frozenset(recorded))
        spawned = {str(e.data.get("invocationId")) for e in events if e.type == "child.spawned"}
        # A call that spawned a child run carries the child's work, which the child's own stream (and the host's join
        # of its label) says: nothing of its own here.
        facts = [
            dataclasses.replace(f, provenance=ResultProvenance("public", f.provenance.integrity, f.provenance.sources))
            if f.invocation_id is not None and f.invocation_id in spawned
            else f
            for f in (*recorded.values(), *derived)
        ]
        self._facts[run_id] = facts
        return facts

    def run_provenance(self, run_id: str) -> ResultProvenance:
        return join_all(f.provenance for f in self.facts(run_id))

    def sources(self, run_id: str | None = None) -> tuple[SourceRef, ...]:
        """Every source the run (``None``: the session) read, of any kind, first seen first."""
        found: dict[tuple[str, ...], SourceRef] = {}
        for run in (run_id,) if run_id is not None else self._order:
            for fact in self.facts(run):
                for source in fact.provenance.sources:
                    _ = found.setdefault(source.key, source)
        return tuple(found.values())

    def citable(self, run_id: str | None = None) -> tuple[WebPage | RepositoryRef, ...]:
        """The web pages and repository sources the run (``None``: the session) read, first seen first."""
        found: dict[tuple[str, ...], WebPage | RepositoryRef] = {}
        for run in (run_id,) if run_id is not None else self._order:
            for fact in self.facts(run):
                for source in fact.provenance.citable:
                    _ = found.setdefault(source.key, source)
        return tuple(found.values())

    def repositories(self, run_id: str | None = None, *, public_only: bool = False) -> tuple[RepositoryRef, ...]:
        """The repositories fetched (a ``RepositoryRef`` with no path), each at its fetched commit. ``public_only``:
        only those whose fetch was recorded as public, the ones a citation may show anyone."""
        found: dict[tuple[str, ...], RepositoryRef] = {}
        for run in (run_id,) if run_id is not None else self._order:
            for fact in self.facts(run):
                if public_only and fact.provenance.readers != "public":
                    continue
                for source in fact.provenance.sources:
                    if isinstance(source, RepositoryRef) and not source.path:
                        _ = found.setdefault(source.key, source)
        return tuple(found.values())

    def source_attributes(self) -> dict[str, SourceAttributes]:
        """What an edition recorded beside each record and live item the session read (``RecordRef.attributes``), by
        ``classified_id``, as it was when read. A source read without any is not here."""
        if self._attributes is None:
            self._attributes = attributed(
                source for run in self._order for fact in self.facts(run) for source in fact.provenance.sources
            )
        return self._attributes

    def untrusted(self, run_id: str | None = None) -> bool:
        """Whether content an outsider wrote reached the run (``None``: any run of the session)."""
        runs = (run_id,) if run_id is not None else self._order
        return any(f.provenance.integrity == "external" for run in runs for f in self.facts(run))

    def export(self) -> list[dict[str, JsonValue]]:
        """The session's whole record, for audit: one entry per fact, run by run."""
        return [fact.to_wire() for run in self._order for fact in self.facts(run)]


class ProvenanceFeed:
    """A kernel ledger that only keeps the session's ``ProvenanceLedger`` (what the outcome lists as gathered) for a
    run whose flow rule and classification read none: it labels nothing and holds no grants, so the kernel's child
    label and grant hand-offs see no ledger, as before."""

    def __init__(self, provenance: ProvenanceLedger) -> None:
        self.provenance = provenance

    def apply(self, events: Sequence[JournalEvent]) -> None:
        self.provenance.apply(events)

    def source_records(self, run_id: str) -> frozenset[str] | None:
        del run_id


def provenance_of(ledger: object) -> ProvenanceLedger | None:
    """The ``ProvenanceLedger`` a ``SourceLedger`` holds (``provenance``), through wrappers that keep theirs as
    ``inner`` (``GrantingLedger``); ``None`` for a ledger that keeps none."""
    for _ in range(8):
        found = getattr(ledger, "provenance", None)
        if isinstance(found, ProvenanceLedger):
            return found
        ledger = getattr(ledger, "inner", None)
        if ledger is None:
            return None
    return None


__all__ = ["CoreReplay", "ProvenanceFact", "ProvenanceLedger", "Replay", "ProvenanceFeed", "outcome_labels", "provenance_of"]
