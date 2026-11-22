"""``REDIS_MODE=inprocess``: an in-memory Redis for a single-process deployment with no Redis server.

Every client the provider hands out talks to one ``fakeredis`` server held by this process, so leader keys, capacity
leases, counters, rate limits and the caches that sit on Redis keep working with the same code, on one node. Nothing
is shared with another process and nothing survives a restart: durable state (config, credentials, queued events)
lives in PostgreSQL (``KV_STORE_TYPE=postgres``, ``MESSAGE_BROKER=postgres``); this holds only what is rebuilt after a
restart. Do not use it when more than one PipesHub process serves the same data.
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from app.services.redis.connection_provider import IRedisConnectionProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

    from app.services.redis.config import ClientOptions, RedisConnectionConfig

_server_lock = threading.Lock()
_server: object | None = None


def _default_decode() -> bool:
    from app.services.redis.config import ClientOptions

    return ClientOptions().decode_responses


_DECODE = _default_decode()


def _shared_server() -> object:
    global _server  # noqa: PLW0603
    with _server_lock:
        if _server is None:
            try:
                from fakeredis import FakeServer
            except ImportError as e:  # pragma: no cover - depends on the install
                raise RuntimeError(
                    "REDIS_MODE=inprocess needs fakeredis[lua] installed"
                ) from e
            _server = FakeServer()
        return _server


class InProcessRedisProvider(IRedisConnectionProvider):
    def __init__(
        self, config: RedisConnectionConfig, server: object | None = None
    ) -> None:
        self._config = config
        self._server = server if server is not None else _shared_server()
        # One client per thread, bound to the loop it was made on: a client is replaced when that loop is closed or another one runs.
        self._clients: dict[int, tuple["Redis", asyncio.AbstractEventLoop | None]] = {}
        self._lock = threading.Lock()

    def _new(self, *, decode_responses: bool = False) -> "Redis":
        import fakeredis.aioredis

        return fakeredis.aioredis.FakeRedis(
            server=self._server, decode_responses=decode_responses
        )

    def get_client(self) -> "Redis":
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        thread = threading.get_ident()
        with self._lock:
            held = self._clients.get(thread)
            if held is not None and held[1] is loop and (loop is None or not loop.is_closed()):
                return held[0]
            client = self._new(decode_responses=_DECODE)
            self._clients[thread] = (client, loop)
            return client

    def create_client(self, options: ClientOptions | None = None) -> "Redis":
        return self._new(decode_responses=options.decode_responses if options else _DECODE)

    def create_pubsub_client(self) -> "Redis":
        return self._new()

    async def publish(self, channel: str, message: str) -> int:
        return int(await self.get_client().publish(channel, message))

    async def scan_keys(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        async for key in self.get_client().scan_iter(match=pattern, count=count):
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    async def load_script(self, body: str) -> str:
        sha = await self.get_client().script_load(body)
        return sha.decode("utf-8") if isinstance(sha, bytes) else sha

    def key_slot(self, key: str) -> int:
        return 0

    def connection_url(self) -> str:
        raise NotImplementedError("an in-process Redis has no network address")

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        with self._lock:
            self._clients.clear()

    @property
    def is_cluster(self) -> bool:
        return False

    @property
    def mode(self) -> str:
        return "inprocess"

    @property
    def key_namespace(self) -> str:
        return self._config.key_namespace


class PgStateRedisProvider(InProcessRedisProvider):
    """``REDIS_MODE=postgres``. What a Redis client would hold across processes (leader keys, leases, counters) is in PostgreSQL
    (``app.services.pg_state``); this provider serves only what may live in one process: caches. Code that still reaches for a Redis
    client for a cross-process job must branch on ``uses_pg_state()``."""

    @property
    def mode(self) -> str:
        return "postgres"
