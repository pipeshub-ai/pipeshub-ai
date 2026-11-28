"""The pipeline a comparison is judged under is wired through the event processor and the promotion.

``application/json`` with a ``csv`` extension is csv@1 in process and json@1 on the service. Each site that derives a graph key
must use the process's pipeline; a site that falls back to the in-process key reads the service's own copies as another parser's.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.modules.pipeline import parser_identity as pi
from tests.unit.events.test_events import _make_event_processor

REC = {"_key": "r1", "mimeType": "application/json", "recordName": "a.csv", "recordType": "FILE",
       "virtualRecordId": "v1", "lastIndexTimestamp": 5, pi.IDENTITY_FIELD: "svc:json@1", pi.IDENTITY_AT_FIELD: 5,
       pi.IDENTITY_VRID_FIELD: "v1", pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_PARSED}


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")


def _ep(service: bool):  # noqa: ANN202
    ep, _, _, gp = _make_event_processor()
    gp.get_document = AsyncMock(return_value={"extension": "csv"})
    return patch.object(ep, "_use_service_pipeline", return_value=service), ep


@pytest.mark.asyncio
async def test_the_gate_skips_a_service_copy_of_a_json_with_a_csv_extension() -> None:
    p, ep = _ep(True)
    with p:
        assert await ep._parser_gate(dict(REC), "r1") == (True, None)


@pytest.mark.asyncio
async def test_the_gate_runs_a_service_copy_in_full_for_a_process_that_parses_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "false")
    p, ep = _ep(False)
    with p:
        assert (await ep._parser_gate(dict(REC), "r1"))[0] is False


@pytest.mark.asyncio
async def test_the_twin_check_follows_the_pipeline_for_both_sides() -> None:
    p, ep = _ep(True)
    twin_known = dict(REC, _key="t1")
    twin_unknown = {k: v for k, v in REC.items() if not k.startswith("indexedParser")} | {"_key": "t2"}
    doc = {"_key": "r2", "mimeType": "application/json", "recordName": "b.csv", "recordType": "FILE"}
    with p:
        assert await ep._twin_shares_parser(doc, twin_known) is True       # the record side
        assert await ep._twin_shares_parser(doc, twin_unknown) is True     # the twin side


def test_the_promotion_judges_the_duplicate_under_the_process_pipeline() -> None:
    queued = {"_key": "q", "mimeType": "application/json", "recordName": "q.csv", "recordType": "FILE"}
    got = pi.promotion_fields(dict(REC), queued, {"extension": "csv"}, at=9, vrid="v1")
    assert got is not None and got.get(pi.IDENTITY_FIELD) == "svc:json@1"


# The event processor's own predicate needs the service clients as well as the setting, and its comparisons must follow it:
# a setting that is on with no clients runs in process, and the copy it builds is an in-process one.
REC_INPROC = dict(REC, **{pi.IDENTITY_FIELD: "inproc:csv@1"})


@pytest.mark.asyncio
async def test_the_gate_follows_the_event_processors_pipeline_not_the_setting_alone() -> None:
    p, ep = _ep(False)   # USE_PARSING_SERVICE=true (fixture) but no clients: this processor parses in process
    with p:
        assert await ep._parser_gate(dict(REC_INPROC), "r1") == (True, None)


@pytest.mark.asyncio
async def test_the_twin_check_follows_the_event_processors_pipeline_not_the_setting_alone() -> None:
    p, ep = _ep(False)
    twin_known = dict(REC_INPROC, _key="t1")
    twin_unknown = {k: v for k, v in REC.items() if not k.startswith("indexedParser")} | {"_key": "t2"}
    doc = {"_key": "r2", "mimeType": "application/json", "recordName": "b.csv", "recordType": "FILE"}
    with p:
        assert await ep._twin_shares_parser(doc, twin_known) is True
        assert await ep._twin_shares_parser(doc, twin_unknown) is True
