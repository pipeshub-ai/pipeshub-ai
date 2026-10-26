"""Wiring of the PostgreSQL backends (no database needed): what the factories pick for ``postgres``, and the helpers around them."""

import json
from logging import getLogger
from unittest.mock import MagicMock

import pytest

from app.config.constants.store_type import StoreType
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
from app.config.providers.postgres_store import _prefix_end
from app.services.messaging.config import (
    ConsumerType,
    MessageBrokerType,
    RedisConfig,
    RedisStreamsConfig,
)
from app.services.messaging.lanes.interface import LaneConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.utils import MessagingUtils
from app.services.pg_runtime import listen_dsn, runtime_db_config
from app.services.redis.connection_provider_factory import (
    default_redis_mode,
    uses_pg_state,
)

LOG = getLogger("wiring")


def test_the_factory_builds_the_postgres_producer_and_both_consumers() -> None:
    cfg = RedisStreamsConfig(topics=["t"], group_id="g")
    assert isinstance(
        MessagingFactory.create_producer(
            LOG,
            cfg,
            broker_type=MessageBrokerType.POSTGRES,
            lane_config=LaneConfig(lane_count=1),
        ),
        PgMessagingProducer,
    )
    simple = MessagingFactory.create_consumer(
        LOG, cfg, broker_type=MessageBrokerType.POSTGRES
    )
    assert isinstance(simple, PgMessagingConsumer) and simple.indexing is False
    idx = MessagingFactory.create_consumer(
        LOG,
        cfg,
        broker_type=MessageBrokerType.POSTGRES,
        consumer_type=ConsumerType.INDEXING,
    )
    assert idx.indexing is True


def test_the_broker_setting_accepts_postgres(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    assert MessagingUtils.get_broker_type() is MessageBrokerType.POSTGRES


async def test_no_redis_config_is_read_when_the_broker_is_postgres(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    container = MagicMock()
    container.config_service.side_effect = AssertionError(
        "the configuration service must not be asked for a Redis config"
    )
    assert await MessagingUtils._get_redis_config(container) == RedisConfig()


def test_the_key_value_factory_builds_the_postgres_store_without_connecting() -> None:
    store = KeyValueStoreFactory.create_store(
        StoreType.POSTGRES,
        serializer=lambda v: json.dumps(v).encode(),
        deserializer=json.loads,
        config=StoreConfig(host="", port=0),
    )
    assert store is not None
    with pytest.raises(ValueError):
        KeyValueStoreFactory.create_store(
            StoreType.POSTGRES, config=StoreConfig(host="", port=0)
        )


@pytest.mark.parametrize(
    ("prefix", "end"), [("/a/", "/a0"), ("", None), ("abc", "abd")]
)
def test_the_end_of_a_key_prefix_range(prefix, end) -> None:
    assert _prefix_end(prefix) == end


def test_redis_mode_is_postgres_only_when_both_stores_are_postgres(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_MODE", raising=False)
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("MESSAGE_BROKER", "redis")
    assert default_redis_mode() == "standalone" and not uses_pg_state()
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    assert (
        default_redis_mode() == "postgres" and uses_pg_state()
    )  # several processes: shared state lives in the database
    monkeypatch.setenv("REDIS_MODE", "inprocess")  # one process, by explicit choice
    assert default_redis_mode() == "inprocess" and not uses_pg_state()
    monkeypatch.setenv("REDIS_MODE", "cluster")
    assert default_redis_mode() == "cluster"


def test_the_postgres_mode_provider_keeps_only_caches_in_the_process() -> None:
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import (
        RedisConnectionProviderFactory,
    )

    p = RedisConnectionProviderFactory.create(RedisConnectionConfig(), mode="postgres")
    assert p.mode == "postgres" and p.key_namespace == ""
    assert (
        RedisConnectionProviderFactory.create(
            RedisConnectionConfig(), mode="inprocess"
        ).mode
        == "inprocess"
    )


def test_the_factories_pick_the_postgres_tracker_and_lease_manager(monkeypatch) -> None:
    from app.services.pg_state import PgLeaseManager, PgRetryTracker

    monkeypatch.delenv("REDIS_MODE", raising=False)
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("MESSAGE_BROKER", "postgres")
    assert isinstance(
        MessagingFactory.create_retry_manager(LOG, RedisConfig()), PgRetryTracker
    )
    assert isinstance(
        MessagingFactory.create_lease_manager(LOG, RedisConfig()), PgLeaseManager
    )
    monkeypatch.setenv("MESSAGE_BROKER", "redis")
    assert not isinstance(
        MessagingFactory.create_retry_manager(LOG, RedisConfig()), PgRetryTracker
    )


def test_listen_needs_a_direct_address(monkeypatch) -> None:
    monkeypatch.setenv("PG_CORE_URL", "postgresql+psycopg://u@pooler:6432/db")
    monkeypatch.delenv("PG_LISTEN_URL", raising=False)
    monkeypatch.setenv("PG_POOLER", "pgbouncer_txn")
    assert (
        listen_dsn(runtime_db_config()) is None
    )  # LISTEN through a transaction pooler would silently hear nothing
    monkeypatch.setenv("PG_POOLER", "none")
    assert listen_dsn(runtime_db_config()) == "postgresql://u@pooler:6432/db"
    monkeypatch.setenv("PG_LISTEN_URL", "postgresql://u@direct:5432/db")
    assert listen_dsn(runtime_db_config()) == "postgresql://u@direct:5432/db"


async def test_the_configuration_cache_expires_on_the_postgres_store(monkeypatch) -> None:
    """A lost notification must not leave a stale value for ever: on the PostgreSQL store the cache entries expire."""
    import logging

    from cachetools import LRUCache, TTLCache

    from app.config.configuration_service import ConfigurationService
    from app.config.providers.in_memory_store import InMemoryKeyValueStore

    log = logging.getLogger("t")
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("CONFIG_CACHE_TTL_SECONDS", "7")
    svc = ConfigurationService(log, InMemoryKeyValueStore(log))
    assert isinstance(svc.cache, TTLCache) and svc.cache.ttl == 7
    await svc.close()
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    svc = ConfigurationService(log, InMemoryKeyValueStore(log))
    assert isinstance(svc.cache, LRUCache) and not isinstance(svc.cache, TTLCache)
    await svc.close()


async def test_an_idle_ordered_consumer_does_not_poll_every_tenth_of_a_second() -> None:
    """A delivery already due but not handed out is waiting behind a live claim of its key; that ends with an ack, which sends no notification."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    consumer = PgMessagingConsumer(LOG, RedisStreamsConfig(topics=["t"], group_id="g"), queue=MagicMock())
    consumer._wake = asyncio.Event()
    consumer._listener = MagicMock(healthy=lambda: True)
    consumer.queue.next_due = AsyncMock(return_value=-5.0)
    waits: list[float] = []

    async def fake_wait_for(coro, timeout) -> None:
        coro.close()
        waits.append(timeout)
        raise asyncio.TimeoutError

    with patch("app.services.messaging.postgres.consumer.asyncio.wait_for", fake_wait_for):
        consumer._ordered = False
        await consumer._idle(1.0)
        consumer._ordered = True
        await consumer._idle(1.0)
    assert waits[0] < 0.2          # unordered: a due delivery that was not claimed lost a race, try again at once
    assert waits[1] >= 1.0         # ordered: wait for the blocking claim at the normal poll rate
