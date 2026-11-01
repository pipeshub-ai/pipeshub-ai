"""StreamRetention: trims only behind every consumer group (KVM-01)."""
import logging
from collections.abc import AsyncIterator
from typing import Never

import fakeredis
import pytest

from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.redis_streams.producer import RedisStreamsProducer
from app.services.messaging.redis_streams.retention import StreamRetention, _id_key

S = "record-events.0"
log = logging.getLogger("retention-test")


class Lease:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    async def try_acquire(self) -> bool:
        return self.ok


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


async def fill(r, n: int) -> list[str]:
    pipe = r.pipeline()
    for i in range(n):
        pipe.xadd(S, {"value": str(i)})
    return await pipe.execute()


async def test_t1_backlog_is_never_trimmed_by_producer(redis) -> None:
    # No maxlen on write, and no group delivered anything: 600k never shrinks.
    config = RedisStreamsConfig()
    producer = RedisStreamsProducer(log, config)
    assert producer._xadd_trim() == {}
    await fill(redis, 3000)
    await redis.xgroup_create(S, "g", id="0")
    retention = StreamRetention(redis, log, Lease())
    assert not any((await retention.trim_once([S])).values())
    assert await redis.xlen(S) == 3000


async def test_t2_acked_entries_are_trimmed(redis) -> None:
    # ~ trimming drops whole radix nodes (100 entries), so use a stream larger than a node.
    ids = await fill(redis, 400)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "c", {S: ">"}, count=300)
    for i in ids[:250]:
        await redis.xack(S, "g", i)
    r = StreamRetention(redis, log, Lease())
    assert await r.safe_id(S) == ids[250]
    assert (await r.trim_once([S]))[S] > 0
    remaining = [i for i, _ in await redis.xrange(S)]
    # Stream ids compare numerically (ms, seq), not as strings.
    assert _id_key(remaining[0]) <= _id_key(ids[250]) and ids[250] in remaining and ids[399] in remaining
    assert len(remaining) < 400


async def test_t3_slowest_group_bounds_trimming(redis) -> None:
    ids = await fill(redis, 10)
    await redis.xgroup_create(S, "fast", id="0")
    await redis.xgroup_create(S, "slow", id="0")
    await redis.xreadgroup("fast", "c", {S: ">"}, count=10)
    await redis.xreadgroup("slow", "c", {S: ">"}, count=3)
    for i in ids[:3]:
        await redis.xack(S, "slow", i)
    for i in ids:
        await redis.xack(S, "fast", i)
    # slow has acked 0..2 and nothing pending: floor is its last-delivered (entry 2)
    assert await StreamRetention(redis, log, Lease()).safe_id(S) == ids[2]


async def test_t4_pending_of_crashed_consumer_is_kept(redis) -> None:
    ids = await fill(redis, 10)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "dead", {S: ">"}, count=2)  # never acked
    await redis.xreadgroup("g", "live", {S: ">"}, count=8)
    for i in ids[2:]:
        await redis.xack(S, "g", i)
    r = StreamRetention(redis, log, Lease())
    assert await r.safe_id(S) == ids[0]
    await r.trim_once([S])
    assert await redis.xlen(S) == 10
    await redis.xclaim(S, "g", "live", 0, ids[:2])
    for i in ids[:2]:
        await redis.xack(S, "g", i)
    assert await r.safe_id(S) == ids[9]


async def test_t5_no_group_stream_is_only_capped_and_alerts(redis, caplog) -> None:
    await fill(redis, 5)
    r = StreamRetention(redis, log, Lease(), alert_length=3)
    with caplog.at_level(logging.WARNING):
        await r.trim_once([S])
    # Under the cap nothing goes; the alert still names the long stream.
    assert await redis.xlen(S) == 5
    assert "holds 5 entries" in caplog.text


async def test_stream_without_a_group_is_capped_by_length(redis) -> None:
    await fill(redis, 300)
    # fakeredis trims exactly; a real approximate trim removes whole nodes at or below the cap
    await StreamRetention(redis, log, Lease(), no_group_maxlen=100).trim_once([S])
    assert 100 <= await redis.xlen(S) < 300


async def test_unread_group_without_consumers_is_capped_by_length(redis, caplog) -> None:
    # A group made only to measure another pool's backlog must not lift the cap.
    await fill(redis, 300)
    await redis.xgroup_create(S, "judged", id="0")
    with caplog.at_level(logging.WARNING):
        await StreamRetention(redis, log, Lease(), no_group_maxlen=100).trim_once([S])
    assert 100 <= await redis.xlen(S) < 300
    assert "capped at 100 entries" in caplog.text and "holds back trimming" not in caplog.text


async def test_group_that_has_read_is_not_capped_by_length(redis) -> None:
    ids = await fill(redis, 300)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "c", {S: ">"}, count=1)
    await redis.xack(S, "g", ids[0])
    await StreamRetention(redis, log, Lease(), no_group_maxlen=100).trim_once([S])
    assert ids[1] in [e[0] for e in await redis.xrange(S)]


async def test_trim_once_stops_at_the_slowest_group(redis) -> None:
    """trim_once must use the lowest floor of all groups, not the highest."""
    ids = await fill(redis, 400)
    await redis.xgroup_create(S, "fast", id="0")
    await redis.xgroup_create(S, "slow", id="0")
    await redis.xreadgroup("fast", "c", {S: ">"}, count=400)
    for i in ids:
        await redis.xack(S, "fast", i)
    await redis.xreadgroup("slow", "c", {S: ">"}, count=1)
    await redis.xack(S, "slow", ids[0])
    await StreamRetention(redis, log, Lease()).trim_once([S])
    remaining = [e[0] for e in await redis.xrange(S)]
    # the slow group has only read entry 0: everything after it must stay, even though "fast" read it all
    assert ids[1] in remaining and ids[399] in remaining


async def test_t6_only_the_lease_holder_trims(redis) -> None:
    # Three replicas share one Redis; the real leader lock admits exactly one.
    # fakeredis[lua] runs the lock's Lua; each lock gets the shared client.
    locks = []
    for n in range(3):
        lock = VectorMembershipBackfillLeaderLock(log, RedisStreamsConfig(), f"r{n}", key="k")
        lock._redis = redis
        locks.append(lock)
    assert [await lock.try_acquire() for lock in locks] == [True, False, False]
    ids = await fill(redis, 4)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "c", {S: ">"}, count=4)
    for i in ids:
        await redis.xack(S, "g", i)
    assert await StreamRetention(redis, log, Lease(False)).trim_once([S]) == {}
    assert await redis.xlen(S) == 4


async def test_t7_cluster_safe_single_key_commands(redis) -> None:
    # Each stream is touched only via single-key commands, so hash tags/slots are unaffected.
    await fill(redis, 2)
    await redis.xgroup_create(S, "g", id="0")
    assert not any((await StreamRetention(redis, log, Lease()).trim_once([S, "missing-stream"])).values())


async def test_t8_maxlen_mode_keeps_legacy_cap(caplog) -> None:
    config = RedisStreamsConfig(retention_mode="maxlen", max_len=7)
    producer = RedisStreamsProducer(log, config)
    assert producer._xadd_trim() == {"maxlen": 7, "approximate": True}


async def test_every_produced_topic_is_in_the_trimmer_list(monkeypatch) -> None:
    import re
    from pathlib import Path

    from app.services.messaging.config import Topic
    from app.services.messaging.redis_streams.retention import retained_streams

    monkeypatch.setenv("FAIR_SCHEDULING_LANE_COUNT", "4")
    streams = set(retained_streams())
    node_types = Path(__file__).parents[5] / "nodejs/apps/src/libs/types/messaging.types.ts"
    enum_body = re.search(r"enum BrokerTopic \{(.*?)\}", node_types.read_text(), re.S).group(1)
    produced = set(re.findall(r"'([a-z-]+)'", enum_body)) | {t.value for t in Topic}
    for topic in produced - {"health-check"}:  # capped on write, never consumed
        assert topic in streams or any(s.startswith(f"{topic}.") for s in streams), topic
    assert {f"record-events.{i}" for i in range(4)} <= streams


async def test_group_without_consumers_is_named_in_a_warning(redis, caplog) -> None:
    await fill(redis, 3)
    await redis.xgroup_create(S, "abandoned", id="0")
    with caplog.at_level(logging.WARNING):
        await StreamRetention(redis, log, Lease()).trim_once([S])
    assert "abandoned" in caplog.text and S in caplog.text
    assert "abandoned" in {g["name"] for g in await redis.xinfo_groups(S)}  # never deleted


async def test_stalled_group_is_named_when_stream_is_over_alert_length(redis, caplog) -> None:
    await fill(redis, 5)
    await redis.xgroup_create(S, "slow", id="0")
    await redis.xreadgroup("slow", "c", {S: ">"}, count=1)
    with caplog.at_level(logging.WARNING):
        await StreamRetention(redis, log, Lease(), alert_length=2).trim_once([S])
    assert "slow" in caplog.text and "stalled" in caplog.text


async def test_old_redis_without_minid_logs_once_and_keeps_running(redis, caplog) -> None:
    from redis.exceptions import ResponseError

    ids = await fill(redis, 5)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "c", {S: ">"}, count=5)
    for i in ids:
        await redis.xack(S, "g", i)

    calls = []

    async def old_xtrim(*a, **k) -> Never:
        calls.append(1)
        raise ResponseError("syntax error")

    redis.xtrim = old_xtrim
    r = StreamRetention(redis, log, Lease())
    with caplog.at_level(logging.ERROR):
        assert await r.trim_once([S]) == {S: 0}
        await r.trim_once([S])
    assert len(calls) == 1
    assert caplog.text.count("Redis 6.2") == 1


async def test_loop_logs_failures_instead_of_swallowing_them(redis, caplog) -> None:
    import asyncio

    from app.services.messaging.redis_streams.retention import run_retention_loop

    class Boom:
        async def try_acquire(self) -> bool:
            raise RuntimeError("redis down")

    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(run_retention_loop(StreamRetention(redis, log, Boom()), [S], interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
    assert caplog.text.count("redis down") == 1  # rate limited


async def _acked_stream(redis, n: int = 5) -> None:
    ids = await fill(redis, n)
    await redis.xgroup_create(S, "g", id="0")
    await redis.xreadgroup("g", "c", {S: ">"}, count=n)
    for i in ids:
        await redis.xack(S, "g", i)


async def test_transient_trim_error_does_not_disable_trimming(redis, caplog) -> None:
    from redis.exceptions import ReadOnlyError

    await _acked_stream(redis)
    real, calls = redis.xtrim, []

    async def flaky(*a, **k) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise ReadOnlyError("READONLY You can't write against a read only replica.")
        return await real(*a, **k)

    redis.xtrim = flaky
    r = StreamRetention(redis, log, Lease())
    with caplog.at_level(logging.WARNING):
        assert await r.trim_once([S]) == {}
        assert "Redis 6.2" not in caplog.text
        await r.trim_once([S])
    assert len(calls) == 2  # retried on the next pass


async def test_caught_up_group_is_not_called_stalled(redis, caplog) -> None:
    await _acked_stream(redis, 5)
    await redis.xgroup_create(S, "slow", id="0")
    await redis.xreadgroup("slow", "c2", {S: ">"}, count=1)
    r = StreamRetention(redis, log, Lease(), alert_length=2)
    with caplog.at_level(logging.WARNING):
        await r.trim_once([S])
    assert "Consumer group slow" in caplog.text and "Consumer group g " not in caplog.text


async def test_missing_stream_is_a_debug_log(redis, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        await StreamRetention(redis, log, Lease()).trim_once(["not-created-yet"])
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
