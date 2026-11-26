"""SEC-B-05 (ADR-MEM-01): the worker sends the run's untrusted taint with its answer's label, as ``integrity``, so
Node's memory settlement holds a memory a run proposed after outsider-written content reached it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.contracts.generated.pipeshub.events.x_pipeshub_answer_finalized import (
    PipeshubAnswerFinalizedData,
)
from app.harness.labels import (
    SessionSourceLedger,
    label_from_wire,
    label_wire,
    own_work,
)
from harness_core.contracts import validate_contract
from harness_core.journal.types import JournalEvent
from harness_core.kernel.fold import invocation_id
from harness_core.security.labels import label_of

if TYPE_CHECKING:
    from pydantic import JsonValue

RUN = "run-1"
DIGEST = "sha256:" + "a" * 64
REF: dict[str, JsonValue] = {"ref": "cas/m", "hash": DIGEST, "size": 1}


class _Run:
    def __init__(self, run_id: str = RUN) -> None:
        self.run_id = run_id
        self.events: list[JournalEvent] = []
        self.turn = 0

    def _add(self, event_type: str, data: dict[str, JsonValue], *, run_id: str | None = None) -> None:
        self.events.append(
            JournalEvent(
                org_id="o",
                session_id="s",
                stream_id="s",
                seq=len(self.events) + 1,
                run_id=run_id or self.run_id,
                type=event_type,
                critical=not event_type.startswith("x."),
                ts="2026-11-13T00:00:00Z",
                writer="worker",
                data=data,
            )
        )

    def call(self, name: str, *, integrity: str = "internal") -> _Run:
        self.turn += 1
        self._add("turn.started", {"turn": self.turn})
        self._add(
            "model.response",
            {
                "messageRef": REF,
                "toolCalls": [{"callId": "c0", "modelName": name, "argsDigest": DIGEST}],
                "usage": {"inputTokens": 1, "outputTokens": 1},
                "provider": "p",
                "transport": "t",
            },
        )
        self._add("tool.outcome", {"invocationId": invocation_id(self.turn, 0), "status": "ok", "labels": {"integrity": integrity}})
        return self

    def cited(self, **delta: JsonValue) -> _Run:
        self._add("x.pipeshub.citations.recorded", dict(delta))
        return self

    def integrity(self) -> str:
        return own_work(self.events, self.run_id)[1]


SEARCH = "knowledgegraph__search"


def test_a_knowledge_search_of_the_org_s_own_records_is_first_party() -> None:
    run = _Run().call(SEARCH).cited(records={"vr-1": {"id": "rec-1", "connector_name": "SHAREPOINT ONLINE"}})
    assert run.integrity() == "first_party"


def test_a_web_fetch_is_external() -> None:
    run = _Run().call("web_fetch__fetch_url").cited(webRecords=[{"url": "https://attacker.example/page"}])
    assert run.integrity() == "external"


def test_an_outsider_s_email_is_external() -> None:
    run = _Run().call(SEARCH).cited(records={"vr-1": {"id": "rec-1", "connector_name": "GMAIL"}})
    assert run.integrity() == "external"


def test_an_mcp_result_is_external() -> None:
    run = _Run().call("mcp_notes__read", integrity="external")
    assert run.integrity() == "external"


def test_sticky_one_outside_read_then_many_clean_turns() -> None:
    run = _Run().call("web_fetch__fetch_url").cited(webRecords=[{"url": "https://attacker.example/page"}])
    for _ in range(20):
        _ = run.call(SEARCH).cited(records={"vr-1": {"id": "rec-1", "connector_name": "SHAREPOINT ONLINE"}})
    assert run.integrity() == "external"


def test_another_run_s_outside_read_is_not_this_run_s_own_work() -> None:
    run = _Run().call(SEARCH)
    run._add("x.pipeshub.citations.recorded", {"webRecords": [{"url": "https://x.example"}]}, run_id="run-other")
    assert run.integrity() == "first_party"


def test_the_wire_carries_it_and_the_answer_event_accepts_it() -> None:
    label = label_of(set())
    for integrity in ("external", "first_party"):
        wire = label_wire(label, integrity=integrity)  # type: ignore[arg-type]
        assert wire["integrity"] == integrity
        _ = validate_contract(
            PipeshubAnswerFinalizedData,
            {"outcome": "answered", "answer": "", "partTexts": [], "citations": [], "label": wire},
        )
        assert label_from_wire(wire) == label
    assert "integrity" not in label_wire(label)


def test_an_engine_run_s_answer_label_carries_the_session_s_taint() -> None:
    run = _Run().call("web_fetch__fetch_url").cited(webRecords=[{"url": "https://attacker.example/page"}])
    ledger = SessionSourceLedger()
    ledger.apply(run.events)
    assert ledger.answer_label(RUN)["integrity"] == "external"
    clean = _Run().call(SEARCH)
    fresh = SessionSourceLedger()
    fresh.apply(clean.events)
    assert fresh.answer_label(RUN)["integrity"] == "first_party"
