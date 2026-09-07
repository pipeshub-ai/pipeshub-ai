"""Per-event-loop Redis clients with a bounded connection pool.

``redis.asyncio`` clients bind to the loop that first uses them, so the
indexing consumers — which run a worker thread with its own loop alongside
the main uvicorn loop — used to reach Redis by hopping every call onto the
main loop with ``run_coroutine_threadsafe`` plus a 5s ``wait_for``. That hop
is what turned a busy worker loop into a Redis outage: the ``wait_for``
deadline is armed on the *worker* loop, so a loop that stalls past 5s expires
it and cancels the in-flight command, and redis-py disconnects a connection
whose command was cancelled (connection.py, ``except BaseException`` in
``read_response``). With an unbounded pool — redis-py defaults
``max_connections`` to ``2**31`` — every one of those became a fresh TCP
connect, and a production host reached Redis's 10,000-client limit.

Handing each loop its own client removes the hop entirely. Two bounds keep
it that way:

* a ``BlockingConnectionPool``, so contention *queues* on a fixed number of
  connections instead of opening more, and
* a socket timeout well below any caller's deadline, so redis-py always
  raises its own clean error rather than being cancelled mid-command.

Mirrors ``app.config.providers.redis.redis_store.RedisKeyValueStore._get_client``,
which already solved the loop-affinity half of this for the KV store.

Both now delegate client construction to :class:`IRedisConnectionProvider`
(``app.services.redis``) instead of building ``redis.asyncio.Redis``
directly: on ``REDIS_MODE=cluster`` (or an EE MemoryDB mode), the provider
hands back a cluster-aware client with no change needed here. This registry
keeps its own per-loop cache on top of the provider because each call site
(``DistributedConcurrencyManager``, ``RetryManager``) sizes its own
connection pool independently.
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.config import RedisConfig
    from app.services.redis.connection_provider import IRedisConnectionProvider, RedisClient


class RedisClientRegistry:
    """Hands out one bounded Redis client per event loop.

    Thread-safe: the indexing consumers create their worker loop on a
    separate thread, so lookups race with the main loop's own.
    """

    def __init__(
        self,
        logger: "Logger",
        config: "RedisConfig",
        *,
        max_connections: int,
        socket_timeout_seconds: float,
        decode_responses: bool = True,
    ) -> None:
        from app.services.redis.config import ClientOptions, RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

        self._logger = logger
        self._config = config
        self._max_connections = max(1, max_connections)
        self._socket_timeout = max(0.1, socket_timeout_seconds)
        self._options = ClientOptions(
            decode_responses=decode_responses,
            max_connections=self._max_connections,
            socket_timeout_seconds=self._socket_timeout,
            socket_connect_timeout_seconds=self._socket_timeout,
            blocking=True,
        )
        self._provider: "IRedisConnectionProvider" = get_redis_provider(
            RedisConnectionConfig.from_redis_config(config)
        )
        self._lock = threading.Lock()
        # Keyed by thread, with the bound loop stored alongside so a client
        # left over from a closed loop (a worker thread restarted between a
        # stop() and a start()) is discarded rather than raising
        # "attached to a different loop" on first use.
        self._clients: dict[int, tuple["RedisClient", asyncio.AbstractEventLoop | None]] = {}

    @property
    def max_connections(self) -> int:
        return self._max_connections

    @property
    def provider(self) -> "IRedisConnectionProvider":
        """The underlying connection provider, for callers that need
        provider-level operations (``load_script``, ``key_slot``) rather
        than just a client (see ``DistributedConcurrencyManager``, R6)."""
        return self._provider

    def client(self) -> "RedisClient":
        """The client bound to the currently running loop, created on first use."""
        thread_id = threading.get_ident()
        try:
            current_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        with self._lock:
            existing = self._clients.get(thread_id)
            if existing is not None:
                client, bound_loop = existing
                stale = bound_loop is not None and (
                    bound_loop.is_closed()
                    or (current_loop is not None and current_loop is not bound_loop)
                )
                if stale:
                    self._logger.debug(
                        "Discarding stale Redis client for thread %s", thread_id
                    )
                    del self._clients[thread_id]
                else:
                    return client

            client = self._provider.create_client(self._options)
            self._clients[thread_id] = (client, current_loop)
            return client

    async def aclose(self) -> None:
        """Close every client this registry handed out.

        Best-effort per client: a client bound to an already-closed loop
        cannot be awaited, and one failing to close must not strand the rest.
        """
        with self._lock:
            clients = [client for client, _ in self._clients.values()]
            self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception as exc:
                self._logger.debug("Error closing Redis client: %s", exc)
