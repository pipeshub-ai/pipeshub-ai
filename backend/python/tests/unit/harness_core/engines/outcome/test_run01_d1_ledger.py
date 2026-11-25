"""RUN01-D1-PIN, RUN01-D1-LEDGER, RUN01-D1-OUTCOME: the deliverable is resolved once and pinned, the ledger holds
what a run produced as typed facts (journaled, and replayed from older runs' events), and a run ends with one
typed outcome read from it (ADR-RUN-01 D1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.contracts import validate_contract
from harness_core.engines.outcome.deliverable import (
    ANSWER,
    Deliverable,
    pinned_deliverable,
    resolve_deliverable,
)
from harness_core.engines.outcome.ledger import (
    OUTCOME_RECORDED,
    PLACEHOLDER_MAX_BYTES,
    ArtifactWritten,
    ChildEnded,
    LimitReached,
    NoteSaved,
    OutcomeLedger,
    SaveRefused,
    SourceGathered,
    StepCompleted,
    fact_of,
    fact_wire,
    ledger_from_events,
    recorded_events,
)
from harness_core.engines.outcome.outcome import (
    Completed,
    Failed,
    Partial,
    outcome_wire,
    settle,
)
from harness_core.journal.types import JournalEvent
from harness_core.testing.engines.durable import engine_run_spec

VECTOR = Path(__file__).resolve().parents[7] / "contracts" / "harness" / "vectors" / "deliverable.json"
REPORT = Deliverable("file", "report.md")
BIG = PLACEHOLDER_MAX_BYTES


def _vector() -> list[dict[str, object]]:
    return json.loads(VECTOR.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _vector(), ids=lambda c: str(c["id"]))
def test_run01_d1_pin_1_the_request_resolves_as_the_shared_vector_says(case: dict[str, object]) -> None:
    """Node pins with the same cases (deliverable.test.ts); a run admitted before the pin resolves them here."""
    resolved = resolve_deliverable(str(case["request"]))
    assert resolved.wire() == case["deliverable"]
    assert resolved.partial_name() == case.get("partialName")


def test_run01_d1_pin_2_a_pinned_deliverable_is_read_never_parsed_again() -> None:
    spec = engine_run_spec()
    assert pinned_deliverable(spec, "write report.md") == REPORT, "a run admitted before the pin resolves once"
    pinned = spec.model_copy(update={"extensions": {"x.pipeshub": {"deliverable": {"kind": "answer"}}}})
    assert pinned_deliverable(pinned, "write report.md") == ANSWER, "the pin wins over the request's words"
    broken = spec.model_copy(update={"extensions": {"x.pipeshub": {"deliverable": {"kind": "file", "name": ""}}}})
    assert pinned_deliverable(broken, "answer me") == ANSWER
    assert Deliverable.from_wire(REPORT.wire()) == REPORT and Deliverable.from_wire({"kind": "answer", "name": "x"}) is None


ALL_FACTS = [
    ArtifactWritten("out/report.md", 3, 4096, "final", "engine"),
    ArtifactWritten("out/r.md", 4, None, "placeholder", "wrap_up"),
    SourceGathered("https://docs.onyx.app/a", "fetch_url", "https://docs.onyx.app/a", "A"),
    SourceGathered("jira:sha256:ab", "jira"),
    NoteSaved("notes/handoff.json"),
    StepCompleted(2),
    LimitReached("time"),
    ChildEnded("run-c", "failed"),
    SaveRefused("WORKSPACE_TOO_LARGE"),
]


def test_run01_d1_ledger_1_every_fact_round_trips_through_its_journal_event() -> None:
    for fact in ALL_FACTS:
        assert fact_of(fact_wire(fact)) == fact
    assert fact_of({"kind": "from_a_newer_writer"}) is None
    (event,) = recorded_events(ALL_FACTS)
    assert event.type == OUTCOME_RECORDED and not event.critical
    journaled = [JournalEvent.from_envelope(_envelope(1, OUTCOME_RECORDED, event.data))]
    assert ledger_from_events(journaled, run_id="run-1").facts == ALL_FACTS


def test_run01_d1_ledger_2_a_fact_recorded_twice_is_kept_once_and_sources_dedupe_by_ref() -> None:
    ledger = OutcomeLedger()
    page = SourceGathered("https://a", "fetch_url", "https://a")
    assert ledger.record(page, page, StepCompleted(1)) == [page, StepCompleted(1)]
    assert ledger.record(page) == []
    _ = ledger.record(SourceGathered("https://a", "search", "https://a"))
    assert [s.tool for s in ledger.sources] == ["fetch_url"]


def test_run01_d1_ledger_3_an_older_run_replays_its_facts_from_its_events() -> None:
    events = [
        _envelope(1, "tool.intent", {"invocationId": "i1", "toolId": "pipeshub/fetch_url", "argsDigest": "sha256:" + "a" * 64}),
        _envelope(2, "tool.outcome", {"invocationId": "i1", "status": "ok", "labels": {}}),
        _envelope(3, "workspace.committed", {"workspaceId": "ws:s", "revision": 4, "changed": ["/out/report.md", "/notes/a.md"]}),
        _envelope(4, "turn.completed", {"turn": 1}),
        _envelope(5, "run.completed", {"outcome": "answered", "truncatedBy": "turns"}),
    ]
    ledger = ledger_from_events([JournalEvent.from_envelope(e) for e in events], run_id="run-1")
    assert ledger.facts == [
        SourceGathered("invocation:i1", "fetch_url"),
        ArtifactWritten("out/report.md", 4, None),
        StepCompleted(1),
        LimitReached("turns"),
    ]


def test_run01_d1_ledger_4_which_file_is_the_deliverable_is_read_from_its_facts() -> None:
    ledger = OutcomeLedger()
    _ = ledger.record(ArtifactWritten("out/report.md", 1, 178, "placeholder"))
    assert ledger.written(REPORT) is None
    assert ledger.owed_after_cut(REPORT) == "report.md" and ledger.owed_at_end(REPORT) == "report.md"
    _ = ledger.record(ArtifactWritten("notes/report.md", 2, BIG))
    assert ledger.written(REPORT) == ArtifactWritten("notes/report.md", 2, BIG), "the named file, anywhere"
    assert ledger.owed_at_end(REPORT) is None and ledger.owed_after_cut(REPORT) == "report.md"
    small_real = OutcomeLedger([ArtifactWritten("out/summary.csv", 1, 40, "draft")])
    assert small_real.owed_at_end(Deliverable("file", "summary.csv")) is None, "a short file that reads as one"
    assert OutcomeLedger().owed_at_end(Deliverable("file")) is None, "an unnamed file may be answered in chat"
    assert OutcomeLedger().owed_after_cut(Deliverable("file", "plan.docx")) == "plan-partial.md"
    assert OutcomeLedger().written(ANSWER) is None and OutcomeLedger().owed_after_cut(ANSWER) is None


def test_run01_d1_outcome_1_one_typed_outcome_from_the_ledger() -> None:
    ledger = OutcomeLedger()
    assert isinstance(settle(ledger, file=None, owed=None), Completed)
    nothing = settle(ledger, file=None, owed="report.md")
    assert isinstance(nothing, Partial) and nothing.reason == "unfinished" and nothing.missing == ("report.md",)
    assert nothing.action == "retry", "nothing was gathered: there is nothing to continue from"
    _ = ledger.record(SourceGathered("https://a", "fetch_url", "https://a"), LimitReached("time"))
    cut = settle(ledger, file=None, owed="report.md", kept=True)
    assert isinstance(cut, Partial) and cut.reason == "time" and cut.action == "continue"
    written = ArtifactWritten("out/report.md", 5, 140_000, "final")
    delivered = settle(ledger, file=written, owed=None)
    assert isinstance(delivered, Partial) and delivered.missing == ()
    failed = settle(ledger, file=None, owed=None, failure="engine_failed")
    assert isinstance(failed, Failed) and failed.action == "retry"
    assert outcome_wire(cut) == {
        "kind": "partial", "reason": "time", "missing": ["report.md"], "action": "continue",
        "delivered": {"sources": 1, "notes": [], "kept": True},
    }


def test_run01_d1_outcome_2_the_settled_and_recorded_events_match_their_schemas() -> None:
    from app.harness.contracts.generated.pipeshub.events.x_pipeshub_outcome_recorded import (
        PipeshubOutcomeRecordedEvent,
    )
    from app.harness.contracts.generated.pipeshub.events.x_pipeshub_outcome_settled import (
        PipeshubOutcomeSettledEvent,
    )
    from harness_core.engines.outcome.outcome import settled_event

    (recorded,) = recorded_events(ALL_FACTS)
    validate_contract(PipeshubOutcomeRecordedEvent, {**_envelope(1, OUTCOME_RECORDED, recorded.data), "writer": "worker"})
    ledger = OutcomeLedger(list(ALL_FACTS))
    settled = settled_event(settle(ledger, file=ALL_FACTS[0], owed=None, kept=True), REPORT)  # type: ignore[arg-type]
    validate_contract(PipeshubOutcomeSettledEvent, {**_envelope(2, settled.type, settled.data), "writer": "worker"})


def _envelope(seq: int, kind: str, data: object) -> dict[str, object]:
    return {
        "orgId": "org_1", "sessionId": "ses_1", "streamId": "ses_1", "seq": seq, "runId": "run-1",
        "schemaVersion": 1, "critical": False, "ts": "2026-11-05T12:00:00.000Z", "visibility": "owner",
        "writer": "worker", "type": kind, "data": data,
    }


def test_run01_d1_pin_3_a_save_of_the_runs_own_deliverable_is_known_as_it() -> None:
    """The seam an artifact-saving tool asks (report.md saved again after the harness published it)."""
    for given in ("report.md", "out/report.md", "/workspace/out/report.md"):
        assert REPORT.owns(given) == "out/report.md"
    assert REPORT.owns("notes.md") is None and ANSWER.owns("report.md") is None
    assert Deliverable("file").owns("report.md") is None
