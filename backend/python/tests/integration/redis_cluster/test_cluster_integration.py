"""Integration tests for Redis Cluster mode.

These tests require a live Redis Cluster on localhost. Bring one up with:

    docker compose -f deployment/docker-compose/docker-compose.cluster.yml up -d

Then run:

    RUN_CLUSTER_TESTS=1 .venv/bin/python -m pytest \
        tests/integration/redis_cluster/ -o addopts=""

The suite covers the three correctness risks called out in the migration plan:

1. SCAN across shards with keys deliberately seeded to different slots.
2. Pub/Sub fan-out across distinct cluster connections (cache invalidation).
3. Streams XADD + XREADGROUP on a single stream key (consumer-group plumbing).

Hash-tagging is intentionally avoided in the SCAN fixture so keys land on
different primaries — the test fails loudly if scan_iter regresses to
single-shard behavior on any future redis-py version.

Tests use explicit asyncio.run() so they work without pytest-asyncio installed.
"""

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CLUSTER_TESTS") != "1",
    reason="set RUN_CLUSTER_TESTS=1 to run (requires live Redis Cluster)",
)


REDIS_NODES = os.environ.get(
    "REDIS_NODES",
    "127.0.0.1:7000,127.0.0.1:7001,127.0.0.1:7002,"
    "127.0.0.1:7003,127.0.0.1:7004,127.0.0.1:7005",
)


def _cluster_config() -> dict:
    return {"mode": "cluster", "nodes": REDIS_NODES}


async def _pick_keys_on_distinct_slots(client, count: int = 4) -> list[str]:
    """Generate `count` keys that are guaranteed to hash to different slots."""
    seen_slots: set[int] = set()
    keys: list[str] = []
    attempt = 0
    while len(keys) < count and attempt < 1024:
        candidate = f"k-{uuid.uuid4().hex[:8]}"
        slot = await client.execute_command("CLUSTER", "KEYSLOT", candidate)
        if slot not in seen_slots:
            seen_slots.add(slot)
            keys.append(candidate)
        attempt += 1
    return keys


async def _scan_iter_returns_keys_from_every_shard() -> None:
    """If scan_iter regresses to single-shard, this test fails — that is
    the entire reason it exists."""
    from app.utils.redis_util import build_redis_client, cluster_aware_scan_iter

    client = build_redis_client(_cluster_config(), decode_responses=True)
    try:
        prefix = f"cluster-scan-test-{uuid.uuid4().hex[:6]}"
        bare_keys = await _pick_keys_on_distinct_slots(client, count=8)
        full_keys = [f"{prefix}:{k}" for k in bare_keys]
        for k in full_keys:
            await client.set(k, "v")

        try:
            collected = []
            async for k in cluster_aware_scan_iter(client, match=f"{prefix}:*"):
                collected.append(k)

            assert sorted(collected) == sorted(full_keys), (
                "SCAN dropped keys across shards — "
                "either redis-py scan_iter ignored target_nodes=PRIMARIES, "
                "or the cluster is under-populated."
            )
        finally:
            for k in full_keys:
                await client.delete(k)
    finally:
        await client.aclose()


async def _pubsub_broadcast_across_nodes() -> None:
    """Regular PUBLISH/SUBSCRIBE must reach a subscriber connected via a
    different cluster client than the publisher. Cache-invalidation path."""
    from app.utils.redis_util import build_redis_client

    channel = f"cluster-pubsub-{uuid.uuid4().hex[:6]}"
    payload = f"msg-{uuid.uuid4().hex[:6]}"

    subscriber = build_redis_client(_cluster_config(), decode_responses=True)
    publisher = build_redis_client(_cluster_config(), decode_responses=True)

    received: list[str] = []
    pubsub = subscriber.pubsub()
    try:
        await pubsub.subscribe(channel)
        # Drain the subscribe-confirmation message.
        for _ in range(3):
            ack = await pubsub.get_message(timeout=1.0)
            if ack and ack.get("type") == "subscribe":
                break

        await publisher.publish(channel, payload)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline:
            msg = await pubsub.get_message(timeout=0.5)
            if msg and msg.get("type") == "message":
                received.append(msg["data"])
                break

        assert payload in received, (
            "Pub/Sub message did not reach the cross-client subscriber — "
            "broadcast assumption is broken."
        )
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await subscriber.aclose()
        await publisher.aclose()


async def _streams_xadd_and_xreadgroup_round_trip() -> None:
    """Single-key Streams flow on cluster. Confirms client construction
    doesn't break the consumer-group API surface."""
    from app.utils.redis_util import build_redis_client

    stream = f"cluster-stream-{uuid.uuid4().hex[:6]}"
    group = f"g-{uuid.uuid4().hex[:6]}"
    consumer = f"c-{uuid.uuid4().hex[:6]}"

    client = build_redis_client(_cluster_config(), decode_responses=True)
    try:
        await client.xgroup_create(stream, group, id="0", mkstream=True)
        msg_id = await client.xadd(stream, {"value": "hello"})

        result = await client.xreadgroup(
            group, consumer, {stream: ">"}, count=10, block=1000
        )
        assert result, "XREADGROUP returned no batches"
        stream_name, entries = result[0]
        assert entries, "Consumer group received no entries"
        entry_id, fields = entries[0]
        assert entry_id == msg_id
        assert fields.get("value") == "hello"

        acked = await client.xack(stream, group, entry_id)
        assert acked == 1
    finally:
        try:
            await client.xgroup_destroy(stream, group)
        except Exception:
            pass
        await client.delete(stream)
        await client.aclose()


def test_scan_iter_returns_keys_from_every_shard():
    asyncio.run(_scan_iter_returns_keys_from_every_shard())


def test_pubsub_broadcast_across_nodes():
    asyncio.run(_pubsub_broadcast_across_nodes())


def test_streams_xadd_and_xreadgroup_round_trip():
    asyncio.run(_streams_xadd_and_xreadgroup_round_trip())
