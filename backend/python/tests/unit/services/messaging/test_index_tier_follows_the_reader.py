"""The index pool and the memory kind follow the reader a record event's labels select (F23i).

Two real shapes: a SQL table or view (no extension, a mime with no tier of its own, read by the light SQL reader) and
a CSV sent as ``application/vnd.ms-excel`` (what Windows sends for .csv, read by the XLS reader). Real consumers, the
test governor and a real ``EventProcessor`` with its parsers stubbed.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.capacity.classes import CostClass
from app.events.events import DedupDecision, EventProcessor
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.resource_governor import ResourceGovernor
from app.services.resource_governor.kinds import ParseKind
from app.services.resource_governor.models import ParseTier, Pool
from app.services.resource_governor.policy import (
    PDF_PARENT_WORKING_SET_GB,
    HeavyWorkingSet,
)
from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.services.messaging.governor_test_helpers import make_test_governor
from tests.unit.services.messaging.test_admission_consumers import _kafka, _redis

SQL_TABLE = {"recordType": "SQL_TABLE", "recordName": "public.orders", "mimeType": "application/vnd.sql.table"}
SQL_VIEW = {"recordType": "SQL_VIEW", "recordName": "public.v_orders", "mimeType": "application/vnd.sql.view"}
CSV_AS_XLS = {"recordType": "FILE", "recordName": "Sales.csv", "extension": "csv", "mimeType": "application/vnd.ms-excel"}
CSV = {"recordType": "FILE", "recordName": "Sales.csv", "extension": "csv", "mimeType": "text/csv"}
JIRA_ISSUE = {"recordType": "TICKET", "recordName": "PROJ-1", "mimeType": "application/blocks"}
MIB = 2**20


def _consumer(broker: str) -> Any:  # noqa: ANN401
    consumer = _redis() if broker == "redis" else _kafka()
    consumer.governor = make_test_governor()  # 4 CPUs: 8 heavy / 16 light index slots
    consumer.concurrency_manager = MagicMock()
    return consumer


def _message(i: int, labels: dict[str, str]) -> StreamMessage:
    return StreamMessage(eventType="newRecord", payload={"recordId": f"r-{i}", "orgId": "o", "connectorId": "c", **labels})


BROKERS = pytest.mark.parametrize("broker", ["redis", "kafka"])


@BROKERS
@pytest.mark.parametrize(("labels", "tier"), [
    (SQL_TABLE, ParseTier.LIGHT), (SQL_VIEW, ParseTier.LIGHT), (CSV_AS_XLS, ParseTier.HEAVY),
    (CSV, ParseTier.LIGHT), (JIRA_ISSUE, ParseTier.LIGHT),
    ({"extension": "pdf", "mimeType": "application/octet-stream"}, ParseTier.HEAVY),
    ({"extension": "unknown", "mimeType": "unknown"}, ParseTier.HEAVY),
], ids=["sql_table", "sql_view", "csv+xls", "csv", "jira", "pdf+octet", "unknown"])
def test_the_index_tier_is_the_readers(broker: str, labels: dict[str, str], tier: ParseTier) -> None:
    assert concurrency.dispatch_tier(_consumer(broker), _message(1, labels)) is tier


@BROKERS
def test_under_classes_a_csv_read_as_xls_has_a_class_and_a_sql_table_is_light(
    broker: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    consumer = _consumer(broker)
    assert isinstance(concurrency.dispatch_tier(consumer, _message(1, CSV_AS_XLS)), CostClass)
    assert concurrency.dispatch_tier(consumer, _message(2, SQL_TABLE)) is ParseTier.LIGHT


async def test_csvs_read_as_xls_leave_the_light_index_pool_to_light_records() -> None:
    """Was the repro's starvation: CSV-as-XLS records held INDEX_LIGHT while they queued for a heavy parse."""
    consumer = _consumer("redis")
    light = consumer.governor.gate(Pool.INDEX_LIGHT)
    csvs = [asyncio.ensure_future(concurrency.acquire_index_slot(
        consumer, concurrency.dispatch_tier(consumer, _message(i, CSV_AS_XLS)))) for i in range(light.limit)]
    for _ in range(3):
        await asyncio.sleep(0)
    issue = asyncio.ensure_future(concurrency.acquire_index_slot(
        consumer, concurrency.dispatch_tier(consumer, _message(99, JIRA_ISSUE))))
    for _ in range(3):
        await asyncio.sleep(0)
    admitted, light_in_use = issue.done(), light.in_use
    for task in [*csvs, issue]:
        task.cancel()
    await asyncio.gather(*csvs, issue, return_exceptions=True)
    assert admitted and light_in_use == 1


async def _start_parsing(labels: dict[str, str]) -> tuple[Any, str | None]:
    """One record through a real EventProcessor (in-process): its START_PARSING tier and the parser it reached."""
    processor = gen._processor()
    graph = AsyncMock()
    graph.update_node = AsyncMock(return_value=True)
    graph.get_document.return_value = {"_key": "r1", "recordType": labels["recordType"], "recordName": labels["recordName"]}
    ep = EventProcessor(gen._Silent(), processor, graph, MagicMock())
    tier = None
    data = {"eventType": "newRecord", "payload": {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": None, "version": 1, "connectorName": "",
        "extension": labels.get("extension"), "mimeType": labels["mimeType"], "recordName": labels["recordName"],
        "recordType": labels["recordType"], "buffer": b'{"columns": [], "rows": []}',
    }}
    with gen._quiet_env(), patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                                        return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        async for event in ep.on_event(data):
            if event.event == IndexingEvent.START_PARSING:
                tier = event.data.tier
    return tier, (processor.routed[0] if processor.routed else None)


@pytest.mark.parametrize(("labels", "tier", "reader"), [
    (SQL_TABLE, ParseTier.LIGHT, "process_sql_structured_data"),
    (SQL_VIEW, ParseTier.LIGHT, "process_sql_structured_data"),
    (CSV_AS_XLS, ParseTier.HEAVY, "process_xls_document"),
    (CSV, ParseTier.LIGHT, "process_delimited_document"),
], ids=["sql_table", "sql_view", "csv+xls", "csv"])
async def test_the_parse_tier_is_the_readers(labels: dict[str, str], tier: ParseTier, reader: str) -> None:
    assert await _start_parsing(labels) == (tier, reader)


@pytest.mark.parametrize(("labels", "tier", "low", "high"), [
    (CSV_AS_XLS, ParseTier.HEAVY, 1279 * MIB, 1281 * MIB),  # the Excel document over the 0.25 GiB permit base
    ({"extension": "txt", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
     ParseTier.HEAVY, 1279 * MIB, 1281 * MIB),  # a local Docling conversion
    (SQL_TABLE, ParseTier.LIGHT, 1, 64 * MIB),  # a text parse of 1 MiB, not the 1.5 GiB unknown-kind fallback
], ids=["csv+xls", "txt+docx", "sql_table"])
async def test_the_memory_reserved_is_the_readers_kind(
    labels: dict[str, str], tier: ParseTier, low: int, high: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the measured permit base (PARSE_LANES=on, shared lanes) a kind's parent excess is reserved before the permit."""
    monkeypatch.setattr(ResourceGovernor, "heavy_working_set",
                        property(lambda _self: HeavyWorkingSet(PDF_PARENT_WORKING_SET_GB, "measured")))
    host = _consumer("redis")
    kind = concurrency.record_parse_kind(labels)
    assert kind is not ParseKind.UNKNOWN
    admission = await concurrency.reserve_record_memory(host, tier, MIB, timeout=1, kind=kind)
    reserved = admission.cost if admission is not None else 0
    concurrency.release_admission(admission)
    assert low <= reserved <= high


async def test_a_fallback_permit_base_reserves_no_kind_excess() -> None:
    host = _consumer("redis")
    for labels in (CSV_AS_XLS, SQL_TABLE):
        tier = concurrency.dispatch_tier(host, _message(1, labels))
        admission = await concurrency.reserve_record_memory(
            host, tier, MIB, timeout=1, kind=concurrency.record_parse_kind(labels))
        assert admission is None, labels


