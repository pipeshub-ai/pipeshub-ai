"""The stranded-record sweep is the net under turning a connector back on.

Turning a connector on re-queues the records parked while it was off
(AUTO_INDEX_OFF with reason CONNECTOR_OFF) in one pass. A refused send, or a
pass that dies part-way, used to leave the rest parked with the connector on,
and nothing ever looked at them again: the sweep walked only QUEUED and
NOT_STARTED. It now walks these too, under the same age and back-off.

It must never send a record a connector's filters keep out of auto-indexing
(AUTO_INDEX_OFF with no reason), a trashed one, or one whose connector is off.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, OriginTypes, ProgressStatus
from app.indexing_main import _republish_stranded_records
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils.user_errors import CONNECTOR_OFF

ON, OFF = "github-on", "github-off"


class FakeGraph:
    def __init__(self) -> None:
        self.apps = {ON: {"isActive": True}, OFF: {"isActive": False}}
        self.records: dict[str, dict[str, Any]] = {}
        self.fail_reads_after: int | None = None
        self.reads = 0
        self.record_reads: list[dict] = []

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False) -> dict | None:
        if collection == CollectionNames.APPS.value:
            app = self.apps.get(key)
            return dict(app) if app else None
        doc = self.records.get(key)
        return dict(doc) if doc else None

    async def get_documents_paginated(
        self, collection, skip=0, limit=50, filters=None, sort_field=None,
        transaction=None, *, raise_on_error=False, after_key=None,
    ) -> list[dict]:
        assert after_key is None or sort_field == "_key"
        source = self.records
        if collection == CollectionNames.APPS.value:
            source = {key: {"_key": key, **app} for key, app in self.apps.items()}
        else:
            self.record_reads.append(dict(filters or {}))
            self.reads += 1
            if self.fail_reads_after is not None and self.reads > self.fail_reads_after:
                raise RuntimeError("graph unavailable")
        rows = [
            dict(doc) for doc in source.values()
            if all(doc.get(f) == v for f, v in (filters or {}).items())
            and (after_key is None or doc["_key"] > after_key)
        ]
        rows.sort(key=lambda d: d["_key"])
        return rows[skip:skip + limit]

    async def update_node(self, key, collection, updates, transaction=None) -> bool:
        self.records[key].update(updates)
        return True

    async def update_nodes_fields_if_match(self, collection, rows, transaction=None) -> list[str]:
        applied = []
        for key, updates, expected in rows:
            doc = self.records.get(key)
            if doc is not None and all(doc.get(f) == v for f, v in expected.items()):
                doc.update(updates)
                applied.append(key)
        return applied


class Producer:
    """Takes both the resume's batch send and the sweep's single send."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.refuse: set[str] = set()

    async def send_messages(self, topic, messages) -> list[bool]:
        acked = []
        for key, _message in messages:
            ok = key not in self.refuse
            if ok:
                self.sent.append(key)
            acked.append(ok)
        return acked

    async def send_event(self, topic, event_type, payload, key=None) -> bool:
        self.sent.append(key)
        return True


def _parked(key: str, connector_id: str = ON, **fields: object) -> dict[str, Any]:
    doc = {
        "_key": key,
        "id": key,
        "orgId": "org-1",
        "recordName": f"{key}.md",
        "connectorId": connector_id,
        "connectorName": "GITHUB",
        "origin": OriginTypes.CONNECTOR.value,
        "recordType": "FILE",
        "version": 0,
        "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
        "reason": CONNECTOR_OFF,
        # Parked long ago, so old enough for the sweep.
        "updatedAtTimestamp": 1,
        "queuedAtTimestamp": 1,
    }
    doc.update(fields)
    return doc


async def _sweep(graph: FakeGraph, producer: Producer) -> int:
    async def run_coordination(coro: object) -> object:
        return await coro

    with patch.dict(os.environ, {"STRANDED_RECORD_REPUBLISH_AFTER_SECONDS": "3600"}):
        return await _republish_stranded_records(
            graph_provider=graph,
            logger=logging.getLogger("sweep-test"),
            producer=producer,
            run_coordination=run_coordination,
            concurrency_manager=None,
            page_size=100,
        )


def _resume(graph: FakeGraph, producer: Producer) -> EntityEventService:
    container = MagicMock()
    container.messaging_producer = producer
    return EntityEventService(logging.getLogger("resume-test"), graph, container)


@pytest.mark.asyncio
async def test_a_record_the_resume_could_not_send_is_sent_by_the_next_sweep() -> None:
    graph, producer = FakeGraph(), Producer()
    for key in ("r0", "r1", "r2"):
        graph.records[key] = _parked(key)
    producer.refuse = {"r1"}

    assert await _resume(graph, producer)._requeue_records_parked_while_off(ON) == 2
    assert graph.records["r1"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value

    producer.refuse = set()
    assert await _sweep(graph, producer) == 1
    assert producer.sent.count("r1") == 1
    # The two the resume did re-queue are fresh QUEUED rows, not the sweep's yet.
    assert producer.sent.count("r0") == producer.sent.count("r2") == 1


@pytest.mark.asyncio
async def test_records_after_a_resume_that_died_part_way_are_sent_by_the_next_sweep() -> None:
    graph, producer = FakeGraph(), Producer()
    for key in ("r0", "r1", "r2", "r3"):
        graph.records[key] = _parked(key)
    graph.fail_reads_after = 1

    with patch.object(entity_module, "_REQUEUE_PAGE_SIZE", 2), pytest.raises(RuntimeError):
        await _resume(graph, producer)._requeue_records_parked_while_off(ON)
    assert producer.sent == ["r0", "r1"]

    graph.fail_reads_after = None
    assert await _sweep(graph, producer) == 2
    assert sorted(producer.sent[2:]) == ["r2", "r3"]


@pytest.mark.asyncio
async def test_the_sweep_never_sends_filtered_trashed_or_turned_off_records() -> None:
    graph, producer = FakeGraph(), Producer()
    graph.records["manual-only"] = _parked("manual-only", reason=None)
    graph.records["other-reason"] = _parked("other-reason", reason="Indexing is off for this file")
    graph.records["trashed"] = _parked("trashed", isDeleted=True)
    graph.records["still-off"] = _parked("still-off", connector_id=OFF)
    before = {key: dict(doc) for key, doc in graph.records.items()}

    assert await _sweep(graph, producer) == 0

    assert producer.sent == []
    assert graph.records == before


@pytest.mark.asyncio
async def test_a_record_that_stays_parked_is_not_sent_every_pass() -> None:
    """The connector-off filter could park it again; the back-off bounds that."""
    graph, producer = FakeGraph(), Producer()
    graph.records["r0"] = _parked("r0")

    assert await _sweep(graph, producer) == 1
    assert graph.records["r0"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
    assert await _sweep(graph, producer) == 0
    assert producer.sent == ["r0"]


@pytest.mark.asyncio
async def test_parked_records_are_read_only_per_connector_that_is_on() -> None:
    """A paused connector's parked backlog, and a manual-indexing connector's
    AUTO_INDEX_OFF records, are never read by the sweep: it asks for
    AUTO_INDEX_OFF only with a connector that is on and the turn-off reason,
    which the (connectorId, indexingStatus, reason, key) index answers directly."""
    graph, producer = FakeGraph(), Producer()
    graph.records["still-off"] = _parked("still-off", connector_id=OFF)

    await _sweep(graph, producer)

    parked_reads = [
        f for f in graph.record_reads
        if f.get("indexingStatus") == ProgressStatus.AUTO_INDEX_OFF.value
    ]
    assert parked_reads == [{
        "connectorId": ON,
        "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value,
        "reason": CONNECTOR_OFF,
    }]


@pytest.mark.asyncio
async def test_a_parked_record_with_a_checksum_is_still_sent() -> None:
    """Only a QUEUED copy is released by its twin; a parked one never is."""
    graph, producer = FakeGraph(), Producer()
    graph.records["r0"] = _parked("r0", md5Checksum="abc", virtualRecordId="vr-1", version=1)

    assert await _sweep(graph, producer) == 1
    assert producer.sent == ["r0"]
