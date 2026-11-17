"""Stored-record retention on a PostgreSQL-only install: the indexing membership is found from the queue's claims, published to
the key-value store on the indexing login and read back from there, so the retirement sweep runs when every member follows the
stored-write protocol and still pauses when one does not or the result is stale.

The graph and the storage service are the in-memory fakes of the sweep's unit tests; what runs for real here is the membership
path: the queue, the publisher as ``indexing_main`` starts it, and the key-value store.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app import indexing_main
from app.services.messaging import membership as m
from app.services.messaging.config import (
    MessageBrokerType,
    RedisStreamsConfig,
    StreamMessage,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from tests.integration.pg_runtime.test_roles import _config_as
from tests.unit.modules.artifacts.test_stored_retention import (
    GR,
    NOW,
    FakeBlob,
    FakeGraph,
    _retired,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOG = logging.getLogger("membership-pg")


@pytest.fixture
async def indexing(runtime, monkeypatch) -> AsyncIterator[Any]:
    """The configuration service of an indexing process, on the indexing login, with an empty membership directory."""
    svc = _config_as(runtime, monkeypatch, "indexing")

    async def clear() -> None:
        for key in await svc.list_keys_in_directory(m.MEMBERSHIP_DIRECTORY):
            await svc.delete_config(key)

    m.reset_registered_groups()
    await clear()
    try:
        yield svc
    finally:
        await clear()
        m.reset_registered_groups()
        await svc.close()


@contextlib.asynccontextmanager
async def holding_one_claim(client_id: str) -> AsyncIterator[tuple[str, str]]:
    """An indexing consumer of a fresh group, holding one record it has claimed."""
    topic, group = f"t-{uuid.uuid4().hex[:8]}", f"g-{uuid.uuid4().hex[:8]}"
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(_: StreamMessage) -> AsyncIterator[Any]:
        entered.set()
        await release.wait()
        return
        yield

    cfg = RedisStreamsConfig(topics=[topic], group_id=group, client_id=client_id, batch_size=1, block_ms=100)
    consumer = PgMessagingConsumer(LOG, cfg, indexing=True)
    await consumer.start(handler)
    try:
        await PgMessagingProducer(LOG, queue=consumer.queue).send_event(
            topic, "newRecord", {"recordId": "rec-1", "orgId": "org-1", "connectorId": "c1"},
        )
        await asyncio.wait_for(entered.wait(), timeout=15)
        yield group, topic
    finally:
        release.set()
        await consumer.stop()
        await consumer.cleanup()


@contextlib.asynccontextmanager
async def leader(svc) -> AsyncIterator[None]:
    """``indexing_main.run_membership_publisher`` on the PostgreSQL broker, as the indexing service starts it."""
    container = MagicMock()
    container.logger.return_value = LOG
    container.config_service.return_value = svc
    container.kafka_consumers = []
    with patch.object(indexing_main, "get_message_broker_type", return_value=MessageBrokerType.POSTGRES):
        task = asyncio.ensure_future(indexing_main.run_membership_publisher(container))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def published_verdict(svc, timeout: float = 15.0) -> m.MembershipVerdict:
    """The verdict a reader gets once the leader's first entry is in (a new reader each time: a reader caches its verdict)."""
    end = time.monotonic() + timeout
    while True:
        verdict = await m.MembershipReader(lambda: m.read_entries_from_config(svc)).verdict()
        if verdict.reason != "stale" or time.monotonic() > end:
            return verdict
        await asyncio.sleep(0.1)


def replaced_document() -> tuple[FakeGraph, FakeBlob]:
    graph, blob = FakeGraph(), FakeBlob()
    graph.add("v1", retired=[_retired("d-old", 5 * GR)])
    blob.rows = {"d-old": {"retiredAt": NOW - 5 * GR}}
    return graph, blob


async def retention_page(svc, graph: FakeGraph, blob: FakeBlob) -> Any:  # noqa: ANN401
    from app.modules.artifacts.stored_retention import StoredRecordRetention

    reader = m.MembershipReader(lambda: m.read_entries_from_config(svc))
    sweep = StoredRecordRetention(graph, blob, LOG, membership=reader.verdict, now_ms=lambda: NOW)  # type: ignore[arg-type]
    _, report = await sweep.run_page(None)
    return report


async def test_every_member_tagged_gives_a_fresh_verdict_and_the_page_purges_the_replaced_document(indexing) -> None:
    async with holding_one_claim("records"), leader(indexing):
        verdict = await published_verdict(indexing)
    assert verdict == m.MembershipVerdict(True)

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert not report.paused and blob.purged == ["d-old"] and graph.nodes["v1"]["retired"] == []


async def test_a_member_claiming_without_the_tag_pauses_the_page(indexing) -> None:
    queue = PgQueue()
    topic, group = f"t-{uuid.uuid4().hex[:8]}", f"g-{uuid.uuid4().hex[:8]}"
    # an older pod: a member of an indexing group whose claim carries a name without the tag
    m.register_group(group, [topic])
    await queue.ensure_group(topic, group, from_start=False)
    await queue.publish(topic, [("rec-2", "{}")])
    assert len(await queue.claim(group, [topic], "records-old", 1, 60.0)) == 1
    try:
        async with holding_one_claim("records"), leader(indexing):
            verdict = await published_verdict(indexing)
    finally:
        await queue.drop_group(topic, group)
    assert verdict == m.MembershipVerdict(False, ("records-old",), "untagged")

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []


async def test_an_entry_older_than_the_stale_bound_pauses_the_page(indexing) -> None:
    """The leader stopped publishing (it died, or its check keeps failing): its last clean entry does not count."""
    await m.publish_entry(
        indexing, f"{m.MEMBERSHIP_DIRECTORY}gone-leader",
        {"checkedAt": int(time.time() * 1000) - m.STALE_AFTER_MS - 1000, "untagged": [], "groupsChecked": ["g"]},
        m.ENTRY_TTL_SECONDS,
    )
    assert (await published_verdict(indexing, timeout=0)).reason == "stale"

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []


async def test_the_claimers_are_read_for_the_registered_groups_only(runtime, uid) -> None:
    queue = PgQueue(runtime)
    topic = f"t-{uid}"
    for group in (f"mine-{uid}", f"other-{uid}"):
        await queue.ensure_group(topic, group, from_start=False)
    await queue.publish(topic, [("a", "{}")])
    await queue.claim(f"mine-{uid}", [topic], "records/sp3-a", 1, 60.0)
    await queue.claim(f"other-{uid}", [topic], "someone-else", 1, 60.0)
    try:
        assert await queue.claimers({f"mine-{uid}": [topic]}) == ["records/sp3-a"]
    finally:
        for group in (f"mine-{uid}", f"other-{uid}"):
            await queue.drop_group(topic, group)
