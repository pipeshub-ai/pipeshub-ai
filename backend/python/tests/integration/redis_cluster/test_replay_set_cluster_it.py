"""The replay-set Lua on a real Redis Cluster.

Both keys of a record carry the record id as hash tag, so the two-key script never fails CROSSSLOT, for record ids
that land on every master. The trim keeps the newest 32 deliveries and the TTL is 7 days, as on fakeredis.
"""
from __future__ import annotations

import uuid

import pytest

from app.modules.indexing import replay_set
from app.services.redis.config import ClientOptions
from app.services.redis.connection_provider_factory import get_redis_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_replay_set_on_a_real_cluster(redis_cluster_available) -> None:
    client = get_redis_provider(redis_cluster_available, mode="cluster").create_client(ClientOptions(decode_responses=True))
    records = [f"rec-{uuid.uuid4().hex}" for _ in range(24)]  # spread over the three masters
    try:
        for record_id in records:
            for n in range(40):
                await replay_set.record_delivery(client, record_id, event_id=f"evt-{n}", delivery_id=f"t-0-{n}",
                                                 flags="F" if n % 2 else "", completed_at_ms=1_000 + n)
            assert await client.zcard(replay_set.order_key(record_id)) == replay_set.REPLAY_SET_SIZE
            assert await client.hlen(replay_set.hash_key(record_id)) == replay_set.REPLAY_SET_SIZE
            ttl = await client.pttl(replay_set.hash_key(record_id))
            assert 0 < ttl <= replay_set.REPLAY_SET_TTL_MS
            entries = await replay_set.read_entries(client, record_id, {"_event_id": "evt-39"})
            assert entries["evt-39"] == replay_set.StoredDelivery(flags="F", delivery_id="t-0-39", completed_at_ms=1_039)
            assert "evt-7" not in entries  # trimmed
            # A hash whose order key is gone is dropped by the next write, on the cluster too.
            await client.delete(replay_set.order_key(record_id))
            await replay_set.record_delivery(client, record_id, event_id="evt-40", delivery_id="t-0-40",
                                             flags="", completed_at_ms=1_040)
            assert await client.hlen(replay_set.hash_key(record_id)) == 1
        slots = {await client.cluster_keyslot(replay_set.hash_key(r)) for r in records}
        assert len(slots) > 1
    finally:
        for record_id in records:
            await client.delete(replay_set.hash_key(record_id))
            await client.delete(replay_set.order_key(record_id))
        await client.aclose()
