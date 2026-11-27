"""UX2 VC-3, VC-6: a child's context starts from the label its parent passed (P2.5a), and imported history counts
by the label Node derived for it. Without either, the context is unknown, as before (fail closed)."""

from __future__ import annotations

from itertools import count

import pytest

from app.harness.labels import (
    HISTORY_LABELLED_EVENT,
    LabelSource,
    SessionSourceLedger,
    label_from_wire,
    own_work_label,
)
from harness_core.journal.types import JournalEvent
from harness_core.security.labels import label_of

_seq = count(1)
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def event(type_: str, run: str, writer: str = "node", stream: str = "s", **data: object) -> JournalEvent:
    return JournalEvent.from_envelope(
        {
            "orgId": "o",
            "sessionId": "s",
            "streamId": stream,
            "seq": next(_seq),
            "runId": run,
            "type": type_,
            "schemaVersion": 1,
            "critical": False,
            "ts": "2026-10-09T00:00:00.000Z",
            "writer": writer,
            "visibility": "owner",
            "data": data,
        }
    )


PUBLIC = label_of((), complete=True)
PRIVATE = label_of({LabelSource("indexed", record_id="rec-1")}, complete=True)
UNKNOWN = label_of((), complete=False)


def child_stream() -> list[JournalEvent]:
    return [event("run.admitted", "child", stream="child", parentRunId="parent", principalRef="p", runKind="child")]


# VC-3


@pytest.mark.parametrize(
    ("inherited", "records"),
    [(PUBLIC, frozenset()), (PRIVATE, frozenset({"rec-1"})), (UNKNOWN, None), (None, None)],
)
def test_vc3_a_child_starts_from_what_its_parent_passed(inherited: object, records: frozenset[str] | None) -> None:
    ledger = SessionSourceLedger(inherited=inherited)  # type: ignore[arg-type]
    ledger.apply(child_stream())
    assert ledger.source_records("child") == records


def test_vc3_the_childs_own_work_joins_its_inheritance() -> None:
    ledger = SessionSourceLedger(inherited=PUBLIC)
    ledger.apply(
        [
            *child_stream(),
            event(
                "x.pipeshub.citations.recorded",
                "child",
                writer="worker",
                stream="child",
                records={"r": {"recordId": "rec-2", "connector": "CONFLUENCE", "recordName": "Plan"}},
            ),
        ]
    )
    assert ledger.source_records("child") == frozenset({"rec-2"})


def test_vc3_a_root_run_ignores_any_inheritance() -> None:
    ledger = SessionSourceLedger(inherited=UNKNOWN)
    ledger.apply([event("run.admitted", "root", principalRef="p", runKind="interactive")])
    assert ledger.source_records("root") == frozenset()


def test_vc3_the_context_label_is_what_a_child_inherits() -> None:
    ledger = SessionSourceLedger()
    ledger.apply([event("run.admitted", "root", principalRef="p", runKind="interactive")])
    assert label_from_wire(ledger.context_label("root")) == PUBLIC


# VC-6


def imported(run: str, digest: str = DIGEST) -> JournalEvent:
    return event("session.imported", run, upToMessageSeq=4, transcriptDigest=digest)


def labelled(run: str, label: dict[str, object], digest: str = DIGEST, writer: str = "node") -> JournalEvent:
    return event(HISTORY_LABELLED_EVENT, run, writer=writer, transcriptDigest=digest, label=label)


@pytest.mark.parametrize(
    ("events", "records"),
    [
        # An import Node labelled: that label.
        (lambda: [imported("r"), labelled("r", {"complete": True, "sources": []})], frozenset()),
        (lambda: [imported("r"), labelled("r", {"complete": True, "sources": [{"kind": "attachment", "recordId": "att-1"}]})], frozenset({"att-1"})),
        # Legacy answers in it: unknown.
        (lambda: [imported("r"), labelled("r", {"complete": False, "sources": []})], None),
        # No label (an older Node), a label for another import, or one a worker wrote: unknown.
        (lambda: [imported("r")], None),
        (lambda: [imported("r"), labelled("r", {"complete": True, "sources": []}, digest=OTHER)], None),
        (lambda: [imported("r"), labelled("r", {"complete": True, "sources": []}, writer="worker")], None),
    ],
)
def test_vc6_an_import_counts_by_the_label_node_derived(events: object, records: frozenset[str] | None) -> None:
    stream = [event("run.admitted", "r", principalRef="p", runKind="interactive"), *events()]  # type: ignore[operator]
    ledger = SessionSourceLedger()
    ledger.apply(stream)
    assert ledger.source_records("r") == records
    own = own_work_label(stream, "r")
    assert (own.complete and {s.record_id for s in own.sources}) == (records if records is not None else False)


def test_vc6_a_finished_run_that_imported_counts_by_its_answer() -> None:
    """A later run reads the importing run by the label its answer recorded, which includes the import."""
    stream = [
        event("run.admitted", "r1", principalRef="p", runKind="interactive"),
        imported("r1"),
        labelled("r1", {"complete": True, "sources": []}),
        event("x.pipeshub.answer.finalized", "r1", writer="worker", label={"complete": True, "sources": []}),
        event("run.completed", "r1", outcome="answered"),
        event("run.admitted", "r2", principalRef="p", runKind="interactive"),
    ]
    ledger = SessionSourceLedger()
    ledger.apply(stream)
    assert ledger.source_records("r2") == frozenset()
