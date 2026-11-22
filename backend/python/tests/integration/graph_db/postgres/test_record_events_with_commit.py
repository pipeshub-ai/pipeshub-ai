"""Record events commit with the record write when the graph and the broker are one PostgreSQL database.

A crash after the write commits must not lose its event (the record would never be indexed, or its vectors never
removed), and a write that rolls back must send none. The events are read back through a consumer group of the queue,
as the indexing service reads them.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as dsp,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordType
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.services.pg_runtime import RuntimeDb

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = pytest.mark.integration

TOPIC = "record-events"
log = logging.getLogger("record-events-with-commit")


class _World:
    def __init__(self, provider: PostgresGraphProvider, org: str, queue: PgQueue, group: str) -> None:
        self.p, self.org, self.queue, self.group = provider, org, queue, group
        self.conn = f"conn-{org[-8:]}"
        self.store = GraphDataStore(log, provider)
        self.processor = DataSourceEntitiesProcessor(log, self.store, AsyncMock())
        self.processor.org_id = org
        self.processor.messaging_producer = PgMessagingProducer(log, queue=queue)

    def record(self, name: str) -> FileRecord:
        return FileRecord(
            id=f"{name}-{self.org[-8:]}", org_id=self.org, record_name=f"{name}.txt", record_type=RecordType.FILE,
            external_record_id=f"ext-{name}", version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE,
            connector_id=self.conn, mime_type="text/plain", is_file=True, extension="txt", path=f"{name}.txt", size_in_bytes=10,
        )

    async def events(self, record_id: str, group: str | None = None) -> list[str]:
        """The event types delivered to this test's consumer group (or ``group``) for the record."""
        group = group or self.group
        got = await self.queue.claim(group, [TOPIC], "test", 1000, 30.0)
        for d in got:
            await self.queue.ack(group, d)
        return [json.loads(d.value)["eventType"] for d in got if d.key == record_id]

    async def stored(self, record_id: str) -> dict | None:
        return await self.p.get_document(record_id, CollectionNames.RECORDS.value)


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    monkeypatch.setenv("PG_RUNTIME_SERVICE", "connectors")
    monkeypatch.setattr(dsp, "is_soft_delete_enabled", AsyncMock(return_value=False))
    org = uuid.uuid4().hex[:24]
    queue = PgQueue(RuntimeDb())
    group = f"record-events-it-{org[-8:]}"
    await queue.ensure_group(TOPIC, group, from_start=False)
    try:
        with org_ctx(org):
            world = _World(pg_provider, org, queue, group)
            await pg_provider.batch_upsert_nodes([{
                "_key": world.conn, "orgId": org, "name": world.conn, "type": "Drive", "appGroup": "Google Workspace",
                "scope": "team", "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            }], CollectionNames.APPS.value)
            yield world
    finally:
        await queue.drop_group(TOPIC, group)
        with org_ctx(org):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_a_crash_after_the_commit_still_delivers_the_new_record_event(w: _World) -> None:
    record = w.record("a")
    # The process dies between the commit and what used to publish after it.
    w.processor._after_slice_committed = AsyncMock(side_effect=RuntimeError("process died"))

    with pytest.raises(RuntimeError, match="process died"):
        await w.processor.on_new_records([(record, [])])

    assert await w.stored(record.id) is not None
    assert await w.events(record.id) == ["newRecord"]


async def test_a_write_that_rolls_back_sends_no_event(w: _World) -> None:
    record = w.record("b")
    # The last step of the block before COMMIT fails, after the events were written into the transaction.
    w.store._flush_scope_generation = AsyncMock(side_effect=RuntimeError("connection lost"))

    with pytest.raises(RuntimeError, match="connection lost"):
        await w.processor.on_new_records([(record, [])])

    assert await w.stored(record.id) is None
    assert await w.events(record.id) == []


async def test_a_committed_write_delivers_its_event_once(w: _World) -> None:
    record = w.record("c")
    await w.processor.on_new_records([(record, [])])
    assert await w.events(record.id) == ["newRecord"]
    assert (await w.stored(record.id))["indexingStatus"] == "QUEUED"   # promoted after the commit, as before


async def test_a_crash_after_a_delete_commits_still_delivers_its_delete_event(w: _World) -> None:
    record = w.record("d")
    await w.processor.on_new_records([(record, [])])
    assert await w.events(record.id) == ["newRecord"]
    # indexing gives the record its virtual record id; a delete sends the cleanup event only for one that has it
    await w.p.batch_update_nodes([{"_key": record.id, "virtualRecordId": f"vr-{record.id}"}], CollectionNames.RECORDS.value)
    w.processor._publish_delete_events = AsyncMock(side_effect=RuntimeError("process died"))

    with pytest.raises(RuntimeError, match="process died"):
        await w.processor.on_record_deleted(record.id)

    assert await w.stored(record.id) is None
    assert await w.events(record.id) == ["deleteRecord"]


async def test_another_broker_keeps_publishing_after_the_commit(w: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the broker elsewhere the event cannot share the transaction: it is sent after the commit, as before."""
    monkeypatch.setenv("MESSAGE_BROKER", "redis")
    sent: list[str] = []
    w.processor.messaging_producer = MagicMock(send_messages=AsyncMock(
        side_effect=lambda topic, m: sent.extend(k for k, _ in m if topic == TOPIC) or [True] * len(m)))
    record = w.record("e")
    await w.processor.on_new_records([(record, [])])
    assert sent == [record.id]
    assert await w.events(record.id) == []


async def test_a_consumer_group_created_while_a_write_is_open_does_not_wait_for_it_and_gets_its_event(w: _World) -> None:
    """Creating a group locks the group table against every publisher's group read. That read now runs right before the write's
    COMMIT, so a consumer starting meanwhile does not wait for the rest of the write; and the group, committed before that read,
    gets the event (exactly once: it is not in the backfill, which ran before the event existed)."""
    record = w.record("f")
    reached, release = asyncio.Event(), asyncio.Event()

    async def hold(_tx_store: object) -> None:  # after the event was published into the transaction, before COMMIT
        reached.set()
        await release.wait()

    w.store._flush_scope_generation = hold
    write = asyncio.create_task(w.processor.on_new_records([(record, [])]))
    late = f"{w.group}-late"
    try:
        await asyncio.wait_for(reached.wait(), 30)
        assert await asyncio.wait_for(w.queue.ensure_group(TOPIC, late, from_start=True), 5) is True
        release.set()
        await write
        assert await w.events(record.id, late) == ["newRecord"]
        assert await w.events(record.id) == ["newRecord"]
    finally:
        release.set()
        await asyncio.gather(write, return_exceptions=True)
        await w.queue.drop_group(TOPIC, late)
