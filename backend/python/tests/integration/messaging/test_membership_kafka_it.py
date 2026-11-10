"""The membership interlock against a real Kafka broker.

Unit tests cannot catch a wrong guess about what aiokafka's admin client returns, so this one
starts real consumers: a pod that follows the stored-write protocol (client id tagged ``/sp3``) and
an older pod (untagged) in one group, and checks that the product's check reports the older pod, that
the entry the publisher writes carries it, and that the verdict Node and the readers evaluate from
that entry refuses to turn artifact reads on.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.messaging import membership
from app.services.messaging.storage_protocol import is_tagged
from tests.integration.messaging.conftest import (
    create_kafka_topic,
    delete_kafka_topic,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

OLD_POD = "indexing-old-pod"
NEW_POD = "indexing/sp3"


async def _stable_with(bootstrap: str, group: str, members: int) -> None:
    from aiokafka.admin import AIOKafkaAdminClient

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        for _ in range(120):
            response = (await admin.describe_consumer_groups([group]))[0]
            _, _, state, _, _, listed = response.groups[0]
            if state == "Stable" and len(listed) == members:
                return
            await asyncio.sleep(0.5)
    finally:
        await admin.close()
    pytest.fail(f"group {group} did not become Stable with {members} members")


async def test_kafka_membership_reports_an_untagged_member_of_a_stable_group(kafka_available, unique_suffix) -> None:
    from aiokafka import AIOKafkaConsumer
    from aiokafka.admin import AIOKafkaAdminClient

    assert is_tagged(NEW_POD)
    assert not is_tagged(OLD_POD)
    topic = f"membership-{unique_suffix}"
    group = f"it-membership-{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 2)
    consumers = [
        AIOKafkaConsumer(topic, bootstrap_servers=kafka_available, group_id=group, client_id=client_id)
        for client_id in (OLD_POD, NEW_POD)
    ]
    polling: list[asyncio.Task[None]] = []
    try:
        for consumer in consumers:
            await consumer.start()

        async def poll(consumer: AIOKafkaConsumer) -> None:
            while True:
                await consumer.getmany(timeout_ms=200)

        # A consumer takes part in the group only while it polls.
        polling = [asyncio.create_task(poll(c)) for c in consumers]
        await _stable_with(kafka_available, group, 2)

        def admin() -> AIOKafkaAdminClient:
            return AIOKafkaAdminClient(bootstrap_servers=kafka_available)

        assert await membership.kafka_untagged(admin, [group]) == [OLD_POD]

        async def check(groups: dict[str, list[str]]) -> list[str]:
            return await membership.kafka_untagged(admin, sorted(groups))

        entries: list[dict] = []

        async def put(key: str, entry: dict, ttl: int) -> None:
            entries.append(entry)

        now = 1_700_000_000_000
        publisher = membership.MembershipPublisher(
            logging.getLogger("it"), check, put, holder="it", now_ms=lambda: now, groups=lambda: {group: [topic]},
        )
        assert await publisher.publish_once() is not None
        assert entries[0]["untagged"] == [OLD_POD]
        verdict = membership.evaluate(entries, now)
        assert (verdict.ok, verdict.untagged, verdict.reason) == (False, (OLD_POD,), "untagged")
    finally:
        for task in polling:
            task.cancel()
        await asyncio.gather(*polling, return_exceptions=True)
        for consumer in consumers:
            await consumer.stop()
        await delete_kafka_topic(kafka_available, topic)


async def test_kafka_membership_with_only_tagged_members_is_clean(kafka_available, unique_suffix) -> None:
    from aiokafka import AIOKafkaConsumer
    from aiokafka.admin import AIOKafkaAdminClient

    topic = f"membership-clean-{unique_suffix}"
    group = f"it-membership-clean-{unique_suffix}"
    await create_kafka_topic(kafka_available, topic, 1)
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=kafka_available, group_id=group, client_id=NEW_POD)
    task: asyncio.Task[None] | None = None
    try:
        await consumer.start()

        async def poll() -> None:
            while True:
                await consumer.getmany(timeout_ms=200)

        task = asyncio.create_task(poll())
        await _stable_with(kafka_available, group, 1)
        found = await membership.kafka_untagged(
            lambda: AIOKafkaAdminClient(bootstrap_servers=kafka_available), [group],
        )
        assert found == []
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await consumer.stop()
        await delete_kafka_topic(kafka_available, topic)
