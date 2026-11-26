"""UX1 AR-4 and AR-3 (host): where a PipesHub call sends its content, and the sources the card names."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.security.labels import AskSource

SLACK = ToolEffect(tool_id_for("send_message", source="slack"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))


def where(**args: object) -> tuple[str, str] | None:
    found = pipeshub_destination(ToolCall(id="c", name="send_message", arguments=args), SLACK)  # type: ignore[arg-type]
    return (found.label, found.key) if found is not None else None


@pytest.mark.parametrize(
    ("args", "label"),
    [
        ({"channel": "sales", "message": "hi"}, "#sales"),
        ({"channel": "#sales"}, "#sales"),
        ({"channel": "C0123ABCD"}, "C0123ABCD"),
        ({"channels": ["sales", "ops"]}, "#sales, #ops"),
        # SEC-B-04: a cc recipient is named as one in to is, never only counted.
        ({"to_recipients": ["ana@example.com"], "cc_recipients": ["bo@example.com"]}, "ana@example.com, bo@example.com"),
        ({"mail_to": ["ana@example.com"]}, "ana@example.com"),
        ({"project_key": "OPS", "summary": "x"}, "OPS"),
        ({"user": "U123"}, "U123"),
    ],
)
def test_ar4_the_destination_as_a_person_names_it(args: dict[str, object], label: str) -> None:
    found = where(**args)
    assert found is not None and found[0] == label


def test_ar4_no_recognised_destination_is_none() -> None:
    assert where(text="hi") is None
    assert where(channel="  ") is None


def test_ar4_the_key_changes_with_any_recipient_and_ignores_case() -> None:
    def key(**args: object) -> str:
        found = where(**args)
        assert found is not None
        return found[1]

    one = key(to_recipients=["ana@example.com"])
    assert one == key(to_recipients=["Ana@Example.com"])
    assert one != key(to_recipients=["ana@example.com"], cc_recipients=["eve@example.com"])
    assert key(channel="sales") != key(channel="general")


def test_ar4_a_long_label_is_clipped() -> None:
    found = where(channels=[f"channel-{i}" for i in range(40)])
    assert found is not None and len(found[0]) <= 120


_seq = iter(range(1, 10_000))


def event(type_: str, run: str, writer: str = "worker", **data: object) -> JournalEvent:
    return JournalEvent.from_envelope(
        {
            "orgId": "o",
            "sessionId": "s",
            "streamId": "s",
            "seq": next(_seq),
            "runId": run,
            "type": type_,
            "schemaVersion": 1,
            "critical": False,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "writer": writer,
            "data": data,
        }
    )


def test_ar3_the_session_ledger_names_its_sources_by_title() -> None:
    ledger = SessionSourceLedger()
    ledger.apply(
        [
            event("run.admitted", "r", "node"),
            event(
                CITATIONS_EVENT,
                "r",
                records={
                    "v1": {"metadata": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}},
                    "v2": {"recordId": "rec-2", "connector": "DRIVE"},
                    "v3": {"origin": "WEB_SEARCH", "webUrl": "https://example.com"},
                },
            ),
        ]
    )
    assert ledger.source_records("r") == frozenset({"rec-1", "rec-2"})
    assert ledger.describe_sources("r") == (AskSource("Q3 plan", "rec-1"), AskSource(None, "rec-2"))


def test_ar3_unknown_context_still_names_what_was_cited() -> None:
    ledger = SessionSourceLedger()
    ledger.apply(
        [
            event("session.imported", "r", "node"),
            event(CITATIONS_EVENT, "r", records={"v1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}),
        ]
    )
    assert ledger.source_records("r") is None
    assert ledger.describe_sources("r") == (AskSource("Q3 plan", "rec-1"),)
