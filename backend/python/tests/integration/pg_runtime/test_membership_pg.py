"""Stored-record retention against a real PostgreSQL: the membership directory in the PostgreSQL key-value store, read on the
indexing login, and the publisher as ``indexing_main`` starts it on the PostgreSQL broker.

On that broker nothing is published, so retention stays paused: the queue keeps no durable record of who consumes it, so an older
pod that claims and acks between two checks leaves no trace a leader (this one or the next) could find. A clean entry that another
broker's leader published to this store counts only when it checked every group it was configured to expect.

The graph and the storage service are the in-memory fakes of the sweep's unit tests.
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
from tests.support.stored_retention_fakes import GR, NOW, FakeBlob, FakeGraph
from tests.support.stored_retention_fakes import retired_entry as _retired

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
    """``indexing_main.run_membership_publisher`` on the PostgreSQL broker, as the indexing service starts it, run to its end."""
    container = MagicMock()
    container.logger.return_value = LOG
    container.config_service.return_value = svc
    container.kafka_consumers = []
    with patch.object(indexing_main, "get_message_broker_type", return_value=MessageBrokerType.POSTGRES):
        # On this broker it returns at once; awaited, so a publisher that raises fails the test instead of leaving an empty
        # directory that reads as the expected stale result.
        await asyncio.wait_for(indexing_main.run_membership_publisher(container), timeout=10)
        yield


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


async def test_an_older_pod_that_claims_and_acks_between_checks_leaves_retention_paused_across_a_leader_change(indexing) -> None:
    queue = PgQueue()
    topic, group = f"t-{uuid.uuid4().hex[:8]}", f"g-{uuid.uuid4().hex[:8]}"
    m.register_group(group, [topic])
    await queue.ensure_group(topic, group, from_start=False)
    try:
        async with holding_one_claim("records"):
            for _ in ("first leader", "next leader"):
                await queue.publish(topic, [("rec-old", "{}")])
                # an older pod in an indexing group: claims under a name without the tag and acks before the leader looks
                (delivery,) = await queue.claim(group, [topic], "records-old", 1, 60.0)
                assert await queue.ack(group, delivery)
                async with leader(indexing):
                    verdict = await published_verdict(indexing, timeout=3)
                assert not verdict.ok and verdict.reason == "stale"
    finally:
        await queue.drop_group(topic, group)

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []


async def test_a_stage_group_whose_consumer_starts_late_leaves_retention_paused(indexing) -> None:
    queue = PgQueue()
    stage_topic, stage_group = f"t-{uuid.uuid4().hex[:8]}", f"pipeline_x_{uuid.uuid4().hex[:8]}"
    # this process's stage consumer has not started (its topics were missing); an older pod in that group holds a claim
    await queue.ensure_group(stage_topic, stage_group, from_start=False)
    await queue.publish(stage_topic, [("rec-s", "{}")])
    assert len(await queue.claim(stage_group, [stage_topic], "pipeline-old", 1, 60.0)) == 1
    try:
        async with holding_one_claim("records"), leader(indexing):
            verdict = await published_verdict(indexing, timeout=3)
    finally:
        await queue.drop_group(stage_topic, stage_group)
    assert not verdict.ok

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []


async def test_an_older_pod_parked_on_listen_with_no_claim_leaves_retention_paused(indexing) -> None:
    """A quiet queue: the older pod's consumer waits on LISTEN, holds no claim and writes nothing a check could read."""
    topic, group = f"t-{uuid.uuid4().hex[:8]}", f"g-{uuid.uuid4().hex[:8]}"
    old = PgMessagingConsumer(LOG, RedisStreamsConfig(topics=[topic], group_id=group, client_id="records", block_ms=100), indexing=True)
    old.consumer_name = "records"   # the name an older build claims under: no tag

    async def handler(_: StreamMessage) -> AsyncIterator[Any]:
        return
        yield

    await old.start(handler)
    try:
        assert old._listener is not None   # parked on LISTEN, not polling
        async with holding_one_claim("records"), leader(indexing):
            verdict = await published_verdict(indexing, timeout=3)
        assert not verdict.ok and verdict.reason == "stale"
    finally:
        await old.stop()
        await old.cleanup()
        await PgQueue().drop_group(topic, group)

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []


def _entry(age_ms: int = 0, **over: Any) -> dict[str, Any]:  # noqa: ANN401
    expected = ["pipeline_x_group", "records_consumer_group"]
    return {"checkedAt": int(time.time() * 1000) - age_ms, "untagged": [], "groupsChecked": expected, "groupsExpected": expected, **over}


async def test_a_clean_entry_that_covers_every_expected_group_lets_the_page_purge(indexing) -> None:
    """Published by another broker's leader into this key-value store (KV_STORE_TYPE=postgres beside Kafka or Redis)."""
    await m.publish_entry(indexing, f"{m.MEMBERSHIP_DIRECTORY}leader", _entry(), m.ENTRY_TTL_SECONDS)
    assert await published_verdict(indexing, timeout=0) == m.MembershipVerdict(True)

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert not report.paused and blob.purged == ["d-old"] and graph.nodes["v1"]["retired"] == []


@pytest.mark.parametrize(
    "entry",
    [
        _entry(groupsChecked=["records_consumer_group"]),           # checked before the stage consumer registered
        {k: v for k, v in _entry().items() if k != "groupsExpected"},  # an older publisher: no expected set
        _entry(age_ms=m.STALE_AFTER_MS + 1000),                      # the leader stopped publishing
    ],
    ids=["partial", "unstated", "stale"],
)
async def test_an_entry_that_proves_less_than_every_expected_group_pauses_the_page(indexing, entry) -> None:
    await m.publish_entry(indexing, f"{m.MEMBERSHIP_DIRECTORY}leader", entry, m.ENTRY_TTL_SECONDS)
    assert (await published_verdict(indexing, timeout=0)).reason == "stale"

    graph, blob = replaced_document()
    report = await retention_page(indexing, graph, blob)
    assert report.paused and blob.purged == []
