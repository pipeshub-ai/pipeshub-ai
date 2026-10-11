"""Turning a connector off and on again must not strand its indexing backlog.

Pausing parks the connector's queued and in-flight records as AUTO_INDEX_OFF
with reason CONNECTOR_OFF, whose message promises the file will be indexed once
the connector is on again. Before this, nothing did that: a sync does not send
unchanged records again, so they sat as AUTO_INDEX_OFF until someone reindexed
them by hand.

AUTO_INDEX_OFF also means "the connector's filters keep this out of
auto-indexing". Those records carry no CONNECTOR_OFF reason and must survive a
pause and resume untouched.

The graph here is an in-memory stand-in with the providers' semantics; the
queries themselves are run against real Neo4j and ArangoDB in
tests/integration/test_connector_resume_requeue_e2e.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.sync.task_manager import reindex_task_manager
from app.modules.indexing.connector_off_events import connector_off_updates
from app.services.messaging.kafka.handlers import entity as entity_module
from app.services.messaging.kafka.handlers.entity import EntityEventService
from app.utils.user_errors import CONNECTOR_OFF

CONNECTOR_ID = "github-1"
ORG_ID = "org-1"
REQUEUE_KEY = f"reindex:{CONNECTOR_ID}:parked-while-off"


class FakeGraph:
    """Just enough of IGraphDBProvider, with the semantics the providers document."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, dict[str, Any]]] = {
            CollectionNames.APPS.value: {},
            CollectionNames.ORGS.value: {ORG_ID: {"_key": ORG_ID, "accountType": "enterprise"}},
            CollectionNames.RECORDS.value: {},
        }

    def records(self) -> dict[str, dict[str, Any]]:
        return self.docs[CollectionNames.RECORDS.value]

    async def get_document(self, key, collection, transaction=None, *, raise_on_error=False) -> dict | None:
        doc = self.docs.get(collection, {}).get(key)
        return dict(doc) if doc else None

    async def batch_upsert_nodes(self, nodes, collection, transaction=None) -> bool:
        for node in nodes:
            key = node.get("id") or node.get("_key")
            self.docs[collection].setdefault(key, {"_key": key}).update(node)
        return True

    async def update_node(self, key, collection, updates, transaction=None) -> bool:
        self.docs[collection][key].update(updates)
        return True

    async def reset_indexing_status_for_connector(
        self, connector_id, status, exclude_statuses=None, transaction=None,
        *, only_statuses=None, reason=None,
    ) -> None:
        for doc in self.records().values():
            if doc.get("connectorId") != connector_id:
                continue
            if exclude_statuses and doc.get("indexingStatus") in exclude_statuses:
                continue
            if only_statuses is not None and doc.get("indexingStatus") not in only_statuses:
                continue
            doc["indexingStatus"] = status
            if reason is not None:
                doc["reason"] = reason

    async def get_documents_paginated(
        self, collection, skip=0, limit=50, filters=None, sort_field=None,
        transaction=None, *, raise_on_error=False,
    ) -> list[dict]:
        rows = [
            dict(doc) for doc in self.docs[collection].values()
            if all(doc.get(f) == v for f, v in (filters or {}).items())
        ]
        if sort_field:
            rows.sort(key=lambda d: d.get(sort_field))
        return rows[skip:skip + limit]

    async def update_nodes_fields_if_match(self, collection, rows, transaction=None) -> list[str]:
        applied = []
        for key, updates, expected in rows:
            doc = self.docs[collection].get(key)
            if doc is not None and all(doc.get(f) == v for f, v in expected.items()):
                doc.update(updates)
                applied.append(key)
        return applied


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.refuse: set[str] = set()
        self.on_send = None

    async def send_messages(self, topic, messages) -> list[bool]:
        acked = []
        for key, message in messages:
            if key in self.refuse:
                acked.append(False)
                continue
            assert topic == "record-events"
            self.sent.append((key, message))
            if self.on_send:
                self.on_send(key)
            acked.append(True)
        return acked

    async def send_message(self, topic, message, key=None) -> bool:
        return True

    def sent_keys(self) -> set[str]:
        return {key for key, _ in self.sent}


def _record(key: str, status: str, **fields: object) -> dict[str, Any]:
    doc = {
        "_key": key,
        "id": key,
        "orgId": ORG_ID,
        "recordName": f"{key}.md",
        "connectorId": CONNECTOR_ID,
        "connectorName": "GITHUB",
        "origin": OriginTypes.CONNECTOR.value,
        "recordType": "FILE",
        "mimeType": "text/markdown",
        "extension": "md",
        "version": 0,
        "virtualRecordId": None,
        "parsingStatus": ProgressStatus.NOT_STARTED.value,
        "indexingStatus": status,
        "extractionStatus": ProgressStatus.NOT_STARTED.value,
        "reason": None,
    }
    doc.update(fields)
    return doc


def _service(graph: FakeGraph, producer: FakeProducer) -> EntityEventService:
    container = MagicMock()
    container.messaging_producer = producer
    return EntityEventService(logging.getLogger("resume-test"), graph, container)


def _set_active(graph: FakeGraph, active: bool) -> None:
    graph.docs[CollectionNames.APPS.value][CONNECTOR_ID] = {
        "_key": CONNECTOR_ID, "id": CONNECTOR_ID, "name": "GitHub", "type": "GITHUB",
        "appGroup": "Github", "isActive": active, "createdAtTimestamp": 1,
    }


async def _requeue_finished() -> None:
    for _ in range(1000):
        if not reindex_task_manager.is_running(REQUEUE_KEY):
            return
        await asyncio.sleep(0)
    raise AssertionError("the re-queue task did not finish")


async def _toggle_off(svc: EntityEventService, graph: FakeGraph) -> None:
    # The toggle route writes isActive before it publishes appDisabled.
    _set_active(graph, False)
    with patch.object(entity_module, "get_coordinator", return_value=None):
        assert await svc.process_event(
            "appDisabled", {"orgId": ORG_ID, "apps": ["github"], "connectorId": CONNECTOR_ID}
        )


async def _toggle_on(svc: EntityEventService, graph: FakeGraph) -> None:
    _set_active(graph, True)
    assert await svc.process_event(
        "appEnabled",
        {"orgId": ORG_ID, "apps": ["github"], "connectorId": CONNECTOR_ID, "syncAction": "none"},
    )
    await _requeue_finished()


@pytest.mark.asyncio
async def test_pause_then_resume_requeues_what_the_pause_parked_and_nothing_else() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    _set_active(graph, True)
    for doc in (
        _record("queued-1", ProgressStatus.QUEUED.value),
        _record("queued-2", ProgressStatus.QUEUED.value, version=2, virtualRecordId="vr-2"),
        _record("in-flight", ProgressStatus.IN_PROGRESS.value, parsingStatus=ProgressStatus.IN_PROGRESS.value),
        # The connector's filters say manual indexing for this one.
        _record("manual-only", ProgressStatus.AUTO_INDEX_OFF.value),
        _record("failed", ProgressStatus.FAILED.value, reason="parse error"),
        _record("indexed", ProgressStatus.COMPLETED.value),
        _record("trashed", ProgressStatus.QUEUED.value, isDeleted=True),
        _record("other-connector", ProgressStatus.QUEUED.value, connectorId="github-2"),
    ):
        graph.records()[doc["_key"]] = doc

    await _toggle_off(svc, graph)

    records = graph.records()
    for key in ("queued-1", "queued-2", "trashed"):
        assert records[key]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value, key
        assert records[key]["reason"] == CONNECTOR_OFF, key
    # The pause no longer rewrites outcomes or filter decisions.
    assert records["manual-only"]["reason"] is None
    assert records["failed"]["indexingStatus"] == ProgressStatus.FAILED.value
    assert records["failed"]["reason"] == "parse error"
    assert records["indexed"]["indexingStatus"] == ProgressStatus.COMPLETED.value
    assert records["other-connector"]["indexingStatus"] == ProgressStatus.QUEUED.value

    # The indexing service then reaches the in-flight record's event while the
    # connector is still off, and parks it the way its handler does.
    records["in-flight"].update(connector_off_updates(records["in-flight"]))
    assert records["in-flight"]["parsingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value

    await _toggle_on(svc, graph)

    assert producer.sent_keys() == {"queued-1", "queued-2", "in-flight"}
    events = dict(producer.sent)
    assert events["queued-1"]["eventType"] == EventTypes.NEW_RECORD.value
    assert events["queued-2"]["eventType"] == EventTypes.REINDEX_RECORD.value
    assert events["in-flight"]["payload"]["connectorId"] == CONNECTOR_ID
    assert events["in-flight"]["payload"]["recordId"] == "in-flight"

    for key in ("queued-1", "queued-2", "in-flight"):
        assert records[key]["indexingStatus"] == ProgressStatus.QUEUED.value, key
        assert records[key]["reason"] is None, key
        assert records[key]["queuedAtTimestamp"], key
    assert records["in-flight"]["parsingStatus"] == ProgressStatus.NOT_STARTED.value

    assert records["manual-only"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
    assert records["manual-only"]["reason"] is None
    assert records["failed"]["indexingStatus"] == ProgressStatus.FAILED.value
    assert records["indexed"]["indexingStatus"] == ProgressStatus.COMPLETED.value
    # A trashed record is not indexed; it keeps its parked state.
    assert records["trashed"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value


@pytest.mark.asyncio
async def test_nothing_is_requeued_while_the_connector_is_still_off() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    _set_active(graph, False)
    graph.records()["parked"] = _record(
        "parked", ProgressStatus.AUTO_INDEX_OFF.value, reason=CONNECTOR_OFF
    )

    assert await svc._requeue_records_parked_while_off(CONNECTOR_ID) == 0

    assert producer.sent == []
    assert graph.records()["parked"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value


@pytest.mark.asyncio
async def test_a_record_the_indexer_already_took_on_is_not_overwritten() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    _set_active(graph, True)
    graph.records()["fast"] = _record(
        "fast", ProgressStatus.AUTO_INDEX_OFF.value, reason=CONNECTOR_OFF
    )
    # The consumer picks the event up before the status write lands.
    producer.on_send = lambda key: graph.records()[key].update(
        {"indexingStatus": ProgressStatus.IN_PROGRESS.value, "reason": None}
    )

    assert await svc._requeue_records_parked_while_off(CONNECTOR_ID) == 1

    assert graph.records()["fast"]["indexingStatus"] == ProgressStatus.IN_PROGRESS.value


@pytest.mark.asyncio
async def test_an_unsent_record_stays_parked_and_the_pages_after_it_are_still_reached() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    _set_active(graph, True)
    keys = [f"r{i}" for i in range(7)]
    for key in keys:
        graph.records()[key] = _record(key, ProgressStatus.AUTO_INDEX_OFF.value, reason=CONNECTOR_OFF)
    producer.refuse = {"r1"}

    with patch.object(entity_module, "_REQUEUE_PAGE_SIZE", 2):
        assert await svc._requeue_records_parked_while_off(CONNECTOR_ID) == 6

    assert producer.sent_keys() == set(keys) - {"r1"}
    assert len(producer.sent) == 6, "a record was sent twice"
    assert graph.records()["r1"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value
    assert graph.records()["r1"]["reason"] == CONNECTOR_OFF


@pytest.mark.asyncio
async def test_turning_the_connector_off_again_stops_the_requeue() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    graph.records()["parked"] = _record(
        "parked", ProgressStatus.AUTO_INDEX_OFF.value, reason=CONNECTOR_OFF
    )
    sending = asyncio.Event()

    async def stuck(topic, messages) -> list[bool]:
        sending.set()
        await asyncio.Event().wait()

    producer.send_messages = stuck
    _set_active(graph, True)
    assert await svc.process_event(
        "appEnabled",
        {"orgId": ORG_ID, "apps": ["github"], "connectorId": CONNECTOR_ID, "syncAction": "none"},
    )
    await asyncio.wait_for(sending.wait(), timeout=5)

    await _toggle_off(svc, graph)

    assert not reindex_task_manager.is_running(REQUEUE_KEY)
    assert graph.records()["parked"]["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value


@pytest.mark.asyncio
async def test_the_enable_still_succeeds_when_the_requeue_fails() -> None:
    graph, producer = FakeGraph(), FakeProducer()
    svc = _service(graph, producer)
    _set_active(graph, True)

    async def unreadable(*args, **kwargs) -> list[dict]:
        raise RuntimeError("graph unavailable")

    graph.get_documents_paginated = unreadable

    assert await svc.process_event(
        "appEnabled",
        {"orgId": ORG_ID, "apps": ["github"], "connectorId": CONNECTOR_ID, "syncAction": "none"},
    )
    await _requeue_finished()
    assert producer.sent == []
