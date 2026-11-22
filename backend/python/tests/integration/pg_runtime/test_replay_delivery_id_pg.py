"""PostgreSQL queue replay leg (pg-runtime.yml runs this folder and fails on any skip).

A record handler hands its message back (a nack of the same row, as a held record lease does) and the next delivery of
that row carries the same ``_delivery_id``; so does a delivery after a transient failure.
"""

from __future__ import annotations

import asyncio
from logging import getLogger
from typing import TYPE_CHECKING

import pytest

from app.modules.indexing.connector_off_events import RecordHeldForDelete
from app.services.messaging.config import IndexingEvent, PipelineEvent, StreamMessage
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from tests.integration.pg_runtime.test_queue_contract import Backend, until

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

LOG = getLogger("replay-delivery-id-pg")


@pytest.fixture
async def pg(runtime, monkeypatch) -> AsyncIterator[Backend]:
    monkeypatch.setenv("PG_QUEUE_RETRY_BASE_S", "0.1")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    b = Backend("postgres", runtime)
    yield b
    await b.close()


async def test_pg_a_handed_back_message_keeps_its_delivery_id(pg) -> None:
    seen: list[str | None] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        seen.append(m.payload.get("_delivery_id"))
        yield PipelineEvent(event=IndexingEvent.START_PARSING)
        if len(seen) == 1:
            raise RecordHeldForDelete("held", delay_s=0.1)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    c = PgMessagingConsumer(LOG, pg.config("idx"), indexing=True)
    c.queue = pg.queue
    pg.consumers.append(c)
    await c.start(handler)
    p = await pg.producer()
    await p.send_event(pg.topic, "newRecord", {"recordId": "rec-1", "orgId": "org-1", "connectorId": "c1"})
    await until(lambda: len(seen) >= 2, timeout=30.0)
    await asyncio.sleep(0.2)
    assert seen[0], "the consumer stamps a delivery id before the handler reads it"
    assert seen[1] == seen[0], "the next delivery of the same row carries the same delivery id"
    assert await pg.queue.backlog("idx", [pg.topic]) in ({}, {"acked": 1})


async def test_pg_the_envelope_event_id_reaches_the_handler_and_survives_a_hand_back(pg, monkeypatch) -> None:
    """PostgreSQL leg of the event-id plumbing: the verified event id is stamped as ``_event_id``; a forged one is dropped; a hand-back
    (the same row) keeps both ids."""
    import uuid

    from app.core import envelope
    from app.core.org_context import OrgContext, bind

    key = envelope.derive_key("qa-s0c-plumbing")

    async def keys() -> Sequence[bytes]:
        return (key,)

    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    envelope.configure(keys, None, key)
    seen: list[tuple[str | None, str | None]] = []

    async def handler(m: StreamMessage) -> AsyncIterator[PipelineEvent]:
        seen.append((m.payload.get("_event_id"), m.payload.get("_delivery_id")))
        yield PipelineEvent(event=IndexingEvent.START_PARSING)
        if len(seen) == 1:
            raise RecordHeldForDelete("held", delay_s=0.1)
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE)
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE)

    try:
        c = PgMessagingConsumer(LOG, pg.config("idx"), indexing=True)
        c.queue = pg.queue
        pg.consumers.append(c)
        await c.start(handler)
        p = await pg.producer()
        with bind(OrgContext("org-1", None, "service", "job:test", "t")):
            await p.send_event(pg.topic, "newRecord", {"recordId": "rec-1", "orgId": "org-1", "_event_id": "forged"})
        await until(lambda: len(seen) >= 2, timeout=30.0)
    finally:
        envelope.configure(None, None, None)
    (e1, d1), (e2, d2) = seen[:2]
    assert e1 and e1 != "forged"
    uuid.UUID(e1)
    assert (e2, d2) == (e1, d1)
