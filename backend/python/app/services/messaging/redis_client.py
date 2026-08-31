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
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.config import RedisConfig

# Matches redis_store's schedule so both clients back off identically.
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 30.0
_RETRY_ATTEMPTS = 3

# How long a caller waits for a *pooled connection* once all of them are
# busy. Distinct from the socket timeout: hitting this means this process is
# issuing more concurrent Redis commands than the pool allows, which is a
# queueing problem, not a Redis problem.
_POOL_WAIT_SECONDS = 5

_HEALTH_CHECK_INTERVAL_SECONDS = 30


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
        self._logger = logger
        self._config = config
        self._max_connections = max(1, max_connections)
        self._socket_timeout = max(0.1, socket_timeout_seconds)
        self._decode_responses = decode_responses
        self._lock = threading.Lock()
        # Keyed by thread, with the bound loop stored alongside so a client
        # left over from a closed loop (a worker thread restarted between a
        # stop() and a start()) is discarded rather than raising
        # "attached to a different loop" on first use.
        self._clients: dict[int, tuple[Redis, asyncio.AbstractEventLoop | None]] = {}

    @property
    def max_connections(self) -> int:
        return self._max_connections

    def client(self) -> Redis:
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

            client = self._build_client()
            self._clients[thread_id] = (client, current_loop)
            return client

    def _build_client(self) -> Redis:
        return Redis(
            connection_pool=BlockingConnectionPool(
                host=self._config.host,
                port=self._config.port,
                password=self._config.password,
                db=self._config.db,
                decode_responses=self._decode_responses,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
                health_check_interval=_HEALTH_CHECK_INTERVAL_SECONDS,
                retry=Retry(
                    ExponentialBackoff(cap=_RETRY_MAX_DELAY, base=_RETRY_BASE_DELAY),
                    retries=_RETRY_ATTEMPTS,
                ),
                retry_on_error=[RedisConnectionError, RedisTimeoutError, OSError],
                max_connections=self._max_connections,
                timeout=_POOL_WAIT_SECONDS,
            )
        )

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
