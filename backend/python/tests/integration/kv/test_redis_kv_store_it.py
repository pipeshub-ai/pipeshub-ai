"""The Redis KeyValueStore against a real Redis (OPS01-FU-1): the shared claim cases (``kv_contract.py``), then
what only a server shows: a value is ciphertext at rest, a TTL expires, and a write in one process drops the
value another process cached (the Pub/Sub invalidation ``ConfigurationService`` subscribes to on its own thread
and event loop). ``tests/unit/services/redis/test_kv_store_contract.py`` runs the same store on fake transports.

Needs Docker, and skips when REDIS_IT_HOST is unset:

  docker compose -f deployment/docker-compose/docker-compose.integration.kv.yml up -d --wait redis-kv-it
  cd backend/python && REDIS_IT_HOST=localhost REDIS_IT_PORT=6390 \
    pytest tests/integration/kv/test_redis_kv_store_it.py -m integration

Environment: REDIS_IT_HOST (required), REDIS_IT_PORT (default 6379), REDIS_IT_PASSWORD (optional). Every key
lives under a prefix of this run's own, so a shared Redis keeps its other data.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.utils.encryption.encryption_service import EncryptionService
from tests.integration.kv.kv_contract import KvStoreContract
from tests.support import it_backends

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

LOGGER = logging.getLogger("redis-kv-it")
SECRET = "0cc027328ec9b923d445ea7c80a544943a3ceef09e18ecd9636978ad88bc4771"
# How long a cross-process invalidation may take to arrive; it is one PUBLISH on a local server.
DELIVERY_S = 10.0


@pytest.fixture(scope="module")
def redis_env() -> Iterator[dict[str, str]]:
    """The environment production reads for KV_STORE_TYPE=redis, on this run's own key prefix."""
    env = {
        "KV_STORE_TYPE": "redis",
        "REDIS_HOST": it_backends.endpoint("REDIS_IT_HOST"),
        "REDIS_PORT": os.environ.get("REDIS_IT_PORT", "6379"),
        "REDIS_KV_PREFIX": f"pxh-it:{uuid.uuid4().hex[:8]}:kv:",
        "SECRET_KEY": SECRET,
    }
    with pytest.MonkeyPatch.context() as mp:
        for name, value in env.items():
            mp.setenv(name, value)
        if os.environ.get("REDIS_IT_PASSWORD"):
            mp.setenv("REDIS_PASSWORD", os.environ["REDIS_IT_PASSWORD"])
        else:
            mp.delenv("REDIS_PASSWORD", raising=False)
        mp.setattr(EncryptionService, "_instance", None)
        yield env


@pytest.fixture(scope="module")
async def store(redis_env: dict[str, str]) -> AsyncIterator[EncryptedKeyValueStore]:
    """The encrypted store production builds for KV_STORE_TYPE=redis."""
    kv = EncryptedKeyValueStore(LOGGER)
    try:
        healthy = await asyncio.wait_for(kv.store.health_check(), timeout=10.0)
    except Exception as exc:
        it_backends.unavailable("REDIS_IT_HOST", exc)
    if not healthy:
        it_backends.unavailable("REDIS_IT_HOST", "health check answered False")
    yield kv
    for key in await kv.get_all_keys():
        await kv.delete_key(key)
    await kv.close()


class TestRedisKvStore(KvStoreContract):
    async def test_a_value_is_ciphertext_on_the_server(self, store, key, redis_env) -> None:
        marker = f"plain-{uuid.uuid4().hex}"
        await store.create_key(key, {"token": marker})

        raw = await store.store._get_client().get(redis_env["REDIS_KV_PREFIX"] + key)

        assert raw and marker.encode() not in raw
        assert await store.get_key(key) == {"token": marker}

    async def test_a_ttl_expires_the_key_on_the_server(self, store, key) -> None:
        await store.create_key(key, {"owner": "a"}, ttl=1)
        assert await store.get_key(key) == {"owner": "a"}

        deadline = time.monotonic() + 5.0
        while await store.get_key(key) is not None:
            assert time.monotonic() < deadline, "the key outlived its TTL"
            await asyncio.sleep(0.2)

    async def test_a_write_in_one_process_drops_what_another_cached(self, store, key, redis_env) -> None:
        """Two replicas, each with its own store, client and watch thread: B's write reaches A's cache."""
        del store  # the server is up and the environment set; each replica builds its own store
        replicas = [ConfigurationService(LOGGER, EncryptedKeyValueStore(LOGGER)) for _ in range(2)]
        reader, writer = replicas
        try:
            await writer.set_config(key, {"version": 1})
            await _subscribed(reader, writer, key)
            assert await reader.get_config(key, use_cache=True) == {"version": 1}

            await writer.set_config(key, {"version": 2})

            deadline = time.monotonic() + DELIVERY_S
            while await reader.get_config(key, use_cache=True) != {"version": 2}:
                assert time.monotonic() < deadline, "the cached value outlived the other process's write"
                await asyncio.sleep(0.1)
        finally:
            for replica in replicas:
                await replica.close()


async def _subscribed(reader: ConfigurationService, writer: ConfigurationService, key: str) -> None:
    """Waits until the reader's watch thread delivers invalidations: until then a write could go unseen."""
    probe = f"{key}/probe"
    deadline = time.monotonic() + DELIVERY_S
    n = 0
    while True:
        n += 1
        await writer.set_config(probe, n)
        await asyncio.sleep(0.1)
        if await reader.get_config(probe, use_cache=True) == n:
            # Cached now; the next write must reach the cache through the channel, not a miss.
            await writer.set_config(probe, n + 1)
            inner = time.monotonic() + 1.0
            while time.monotonic() < inner:
                if await reader.get_config(probe, use_cache=True) == n + 1:
                    return
                await asyncio.sleep(0.05)
            n += 1
        assert time.monotonic() < deadline, "the reader's change subscription never came up"
