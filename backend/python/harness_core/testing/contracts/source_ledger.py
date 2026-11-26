"""Contract suite for ``SourceLedger`` (P2.5a): what may have reached an engine run's work.

Subclass :class:`SourceLedgerContract` and provide ``ledger_world``: a :class:`LedgerWorld` whose ``ledger``
is fresh, and whose builders write, as that host journals them, a tool call whose content is exactly some
records, a tool call whose content has no recorded provenance, and how a run ends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.journal.types import JournalEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from harness_core.engines.ports import SourceLedger

ORG = "org-ledger"
SESSION = "ses-ledger"
PARENT = "run-parent"
ENGINE = "run-engine"
EARLIER = "run-earlier"


@dataclass
class Stream:
    """Numbers events as one session stream does."""

    events: list[JournalEvent] = field(default_factory=list[JournalEvent])

    def add(self, event_type: str, data: dict[str, JsonValue], *, run_id: str | None, writer: str = "worker") -> JournalEvent:
        event = JournalEvent(
            org_id=ORG,
            session_id=SESSION,
            stream_id=SESSION,
            seq=len(self.events) + 1,
            run_id=run_id,
            type=event_type,
            critical=not event_type.startswith("x."),
            ts="2026-10-04T00:00:00Z",
            writer=writer,  # type: ignore[arg-type]
            data=data,
        )
        self.events.append(event)
        return event

    def admitted(self, run_id: str, parent: str | None = None) -> None:
        data: dict[str, JsonValue] = {"runKind": "interactive" if parent is None else "child", "principalRef": "p_alice"}
        if parent is not None:
            data["parentRunId"] = parent
        _ = self.add("run.admitted", data, run_id=run_id, writer="node")


class LedgerWorld(Protocol):
    @property
    def ledger(self) -> SourceLedger: ...

    def recorded(self, stream: Stream, run_id: str, invocation_id: str, record_ids: Sequence[str]) -> None:
        """A tool call of ``run_id`` whose result is the content of exactly ``record_ids``."""
        ...

    def unrecorded(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        """A tool call of ``run_id`` whose result's provenance nobody recorded."""
        ...

    def refused(self, stream: Stream, run_id: str, invocation_id: str) -> None:
        """A call of ``run_id`` the harness refused (its outcome is the harness's own text)."""
        ...

    def ended(self, stream: Stream, run_id: str) -> None:
        """What ``run_id`` leaves when it ends (its answer, as this host journals it)."""
        ...


class SourceLedgerContract:
    @pytest.fixture
    def ledger_world(self) -> LedgerWorld:
        raise NotImplementedError("provide a ledger_world fixture")

    @staticmethod
    def _session(world: LedgerWorld, build: Callable[[Stream], None]) -> Stream:
        stream = Stream()
        stream.admitted(PARENT)
        stream.admitted(ENGINE, parent=PARENT)
        build(stream)
        world.ledger.apply(stream.events)
        return stream

    def test_sl1_work_that_read_nothing_has_no_sources(self, ledger_world: LedgerWorld) -> None:
        _ = self._session(ledger_world, lambda s: None)
        assert ledger_world.ledger.source_records(ENGINE) == frozenset()

    def test_sl2_the_runs_own_tool_results_are_its_sources(self, ledger_world: LedgerWorld) -> None:
        _ = self._session(ledger_world, lambda s: ledger_world.recorded(s, ENGINE, "inv-1", ["rec-1", "rec-2"]))
        assert ledger_world.ledger.source_records(ENGINE) == frozenset({"rec-1", "rec-2"})

    def test_sl3_what_the_session_read_before_is_a_source_too(self, ledger_world: LedgerWorld) -> None:
        stream = Stream()
        stream.admitted(EARLIER)
        ledger_world.recorded(stream, EARLIER, "inv-e", ["rec-earlier"])
        ledger_world.ended(stream, EARLIER)
        stream.admitted(PARENT)
        stream.admitted(ENGINE, parent=PARENT)
        ledger_world.recorded(stream, PARENT, "inv-p", ["rec-goal"])
        ledger_world.recorded(stream, ENGINE, "inv-1", ["rec-1"])
        ledger_world.ledger.apply(stream.events)
        assert ledger_world.ledger.source_records(ENGINE) == frozenset({"rec-earlier", "rec-goal", "rec-1"})

    def test_sl4_content_of_unknown_provenance_makes_it_unknown(self, ledger_world: LedgerWorld) -> None:
        def build(stream: Stream) -> None:
            ledger_world.recorded(stream, ENGINE, "inv-1", ["rec-1"])
            ledger_world.unrecorded(stream, ENGINE, "inv-2")

        _ = self._session(ledger_world, build)
        assert ledger_world.ledger.source_records(ENGINE) is None

    def test_sl4_so_does_the_parents(self, ledger_world: LedgerWorld) -> None:
        _ = self._session(ledger_world, lambda s: ledger_world.unrecorded(s, PARENT, "inv-p"))
        assert ledger_world.ledger.source_records(ENGINE) is None

    def test_sl5_history_imported_from_before_labels_makes_it_unknown(self, ledger_world: LedgerWorld) -> None:
        def build(stream: Stream) -> None:
            _ = stream.add(
                "session.imported",
                {"upToMessageSeq": 2, "transcriptDigest": "sha256:" + "b" * 64, "messages": 2},
                run_id=PARENT,
                writer="node",
            )

        _ = self._session(ledger_world, build)
        assert ledger_world.ledger.source_records(ENGINE) is None

    def test_sl6_a_refused_call_names_nothing(self, ledger_world: LedgerWorld) -> None:
        def build(stream: Stream) -> None:
            ledger_world.refused(stream, ENGINE, "inv-r")
            ledger_world.recorded(stream, ENGINE, "inv-1", ["rec-1"])

        _ = self._session(ledger_world, build)
        assert ledger_world.ledger.source_records(ENGINE) == frozenset({"rec-1"})

    def test_sl7_events_applied_again_or_in_batches_change_nothing(self, ledger_world: LedgerWorld) -> None:
        stream = Stream()
        stream.admitted(PARENT)
        stream.admitted(ENGINE, parent=PARENT)
        ledger_world.recorded(stream, ENGINE, "inv-1", ["rec-1"])
        ledger_world.recorded(stream, ENGINE, "inv-2", ["rec-2"])
        half = len(stream.events) // 2
        ledger_world.ledger.apply(stream.events[:half])
        ledger_world.ledger.apply(stream.events[half:])
        ledger_world.ledger.apply(stream.events)
        assert ledger_world.ledger.source_records(ENGINE) == frozenset({"rec-1", "rec-2"})


__all__ = ["ENGINE", "EARLIER", "ORG", "PARENT", "SESSION", "LedgerWorld", "SourceLedgerContract", "Stream"]
