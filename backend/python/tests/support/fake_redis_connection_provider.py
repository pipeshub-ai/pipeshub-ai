"""Test-only `IRedisConnectionProvider` (R18).

Lets a test patch `get_redis_provider` (from
`app.services.redis.connection_provider_factory`) to return a fully
controllable fake instead of patching `StandaloneRedisProvider.create_client`
or reaching for `redis.asyncio.Redis` directly. This is the intended way to
unit test anything built on `IRedisConnectionProvider` -- through the
abstraction, not the client class underneath it -- and is the Python
counterpart of `tests/helpers/fake-redis-provider.ts` on the Node.js side.

Every client handed out (`get_client()`, `create_client()`,
`create_pubsub_client()`) wraps the *same* underlying `fakeredis` server, so
data written through one is visible through another -- exactly like several
connections to one real Redis/MemoryDB endpoint.

Usage::

    provider = FakeRedisConnectionProvider()
    with patch(
        "app.services.messaging.retry_manager.get_redis_provider",
        return_value=provider,
    ):
        manager = RetryManager(logger, redis_config=some_config)
        await manager.initialize()
        ...
        assert provider.create_client_calls == 1
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from redis.crc import key_slot

from app.services.redis.connection_provider import IRedisConnectionProvider
from tests.support.fake_cluster_redis import FakeClusterRedis

if TYPE_CHECKING:
    from app.services.redis.config import ClientOptions

__all__ = ["FakeRedisConnectionProvider"]


class FakeRedisConnectionProvider(IRedisConnectionProvider):
    def __init__(
        self,
        *,
        is_cluster: bool = False,
        mode: str | None = None,
        key_namespace: str = "",
    ) -> None:
        self._is_cluster = is_cluster
        self._mode = mode or ("cluster" if is_cluster else "standalone")
        self._key_namespace = key_namespace
        self._shared_client = FakeClusterRedis()
        self.created_clients: list[FakeClusterRedis] = []
        self.pubsub_clients: list[FakeClusterRedis] = []
        self.get_client_calls = 0
        self.create_client_calls = 0
        self.load_script_calls: list[str] = []
        self.closed = False

    def _new_client(self) -> FakeClusterRedis:
        return FakeClusterRedis(server=self._shared_client.server)

    def get_client(self) -> FakeClusterRedis:
        self.get_client_calls += 1
        return self._shared_client

    def create_client(self, options: "ClientOptions | None" = None) -> FakeClusterRedis:  # noqa: ARG002
        self.create_client_calls += 1
        client = self._new_client()
        self.created_clients.append(client)
        return client

    def create_pubsub_client(self) -> FakeClusterRedis:
        client = self._new_client()
        self.pubsub_clients.append(client)
        return client

    async def scan_keys(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        client = self.get_client()
        async for key in client.scan_iter(match=pattern, count=count):
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    async def load_script(self, body: str) -> str:
        self.load_script_calls.append(body)
        client = self.get_client()
        sha = await client.script_load(body)
        return sha.decode("utf-8") if isinstance(sha, bytes) else sha

    def key_slot(self, key: str) -> int:
        return key_slot(key.encode() if isinstance(key, str) else key)

    def connection_url(self) -> str:
        if self._is_cluster:
            raise NotImplementedError(
                "connection_url is not supported for cluster providers (R7)"
            )
        return "redis://fake:6379/0"

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True

    @property
    def is_cluster(self) -> bool:
        return self._is_cluster

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def key_namespace(self) -> str:
        return self._key_namespace
