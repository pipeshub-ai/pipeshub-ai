"""TR-03: the trigger context Node puts on a scheduled run's input, as the worker reads it and tells the loop."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.harness.assembly.trigger import trigger_note
from app.harness.contracts.state import RunInputSnapshot, RunTrigger
from app.harness.worker.capabilities import WORKER_CAPABILITIES, advertised

CONNECTOR: dict[str, Any] = {
    "kind": "connector",
    "scheduleId": "sch-1",
    "firingId": "fir-1",
    "slotAt": "2026-10-30T09:00:00.000Z",
    "window": {"start": "2026-10-30T09:00:00.000Z", "end": "2026-10-30T09:05:00.000Z"},
    "event": {"connectorId": "con-1", "eventType": "newRecord", "at": "2026-10-30T09:01:02.000Z", "recordId": "rec-1"},
}
WEBHOOK: dict[str, Any] = {
    "kind": "webhook",
    "scheduleId": "sch-2",
    "firingId": "fir-2",
    "slotAt": "2026-10-30T09:00:00.000Z",
    "window": {"start": "2026-10-30T09:00:00.000Z", "end": "2026-10-30T09:05:00.000Z"},
    "webhook": {
        "receivedAt": "2026-10-30T09:00:07.250Z",
        "bodyBytes": 42,
        "bodySha256": "sha256:" + "a" * 64,
        "contentType": "application/json",
    },
}


def _input(trigger: dict[str, Any] | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": "triage",
        "recordIds": [],
        "filters": {},
        "attachments": [],
        "model": {"chatMode": "quick"},
    }
    if trigger is not None:
        body["trigger"] = trigger
    return body


@pytest.mark.parametrize("trigger", [CONNECTOR, WEBHOOK, {k: v for k, v in CONNECTOR.items() if k not in ("window", "event")} | {"kind": "cron"}])
def test_tr03_the_run_input_reads_every_trigger_shape_and_dumps_it_back_as_sent(trigger: dict[str, Any]) -> None:
    snapshot = RunInputSnapshot.model_validate(_input(trigger))
    assert snapshot.trigger is not None
    assert snapshot.trigger.model_dump(mode="json", by_alias=True, exclude_none=True) == trigger


def test_tr03_a_withheld_record_is_simply_absent() -> None:
    event = {k: v for k, v in CONNECTOR["event"].items() if k != "recordId"}
    snapshot = RunInputSnapshot.model_validate(_input({**CONNECTOR, "event": event}))
    assert snapshot.trigger is not None and snapshot.trigger.event is not None
    assert snapshot.trigger.event.record_id is None


def test_tr03_a_run_without_a_trigger_reads_as_before() -> None:
    assert RunInputSnapshot.model_validate(_input(None)).trigger is None


@pytest.mark.parametrize(
    "broken",
    [
        {**CONNECTOR, "payload": {"title": "outsider text"}},
        {**CONNECTOR, "event": {**CONNECTOR["event"], "recordName": "outsider text"}},
        {**WEBHOOK, "webhook": {**WEBHOOK["webhook"], "body": "{}"}},
        {**CONNECTOR, "kind": "slack"},
        {**CONNECTOR, "scheduleId": "has spaces"},
        {**CONNECTOR, "slotAt": "yesterday"},
        {**WEBHOOK, "webhook": {**WEBHOOK["webhook"], "bodySha256": "md5:abc"}},
    ],
)
def test_tr03_extra_or_malformed_fields_are_refused(broken: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ = RunTrigger.model_validate(broken)


def test_tr03_every_worker_advertises_the_trigger_context() -> None:
    assert "trigger_context" in [f.root for f in WORKER_CAPABILITIES.features]
    lean = advertised(engines=False, code=False)
    assert "trigger_context" in [f.root for f in lean.features]


def test_tr03_the_loop_is_told_the_trigger_in_words_with_its_ids() -> None:
    note = trigger_note(CONNECTOR)
    assert note.startswith("This run was started by its schedule's connector event trigger for the slot 2026-10-30T09:00:00.000Z.")
    details = json.loads(note.split(": ", 1)[1])
    assert details == CONNECTOR


def test_tr03_a_run_no_schedule_fired_is_told_nothing() -> None:
    assert trigger_note(None) == ""
    assert trigger_note({}) == ""
