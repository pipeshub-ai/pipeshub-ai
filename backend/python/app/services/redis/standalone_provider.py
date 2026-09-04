"""OSS default provider: a single standalone Redis (or a Sentinel/replica pair
fronted by one endpoint) via ``redis.asyncio.Redis``.

Absorbs the per-event-loop client affinity that used to live independently
in ``RedisClientRegistry`` (messaging) and
``RedisDistributedKeyValueStore._get_client`` (config KV store): both now
delegate to this provider's ``get_client()`` instead of keeping their own
thread-keyed client dict.
"""
from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator, Optional
from urllib.parse import quote

from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider
from app.utils.logger import create_logger

logger = create_logger("redis_standalone_provider")

_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 30.0
_POOL_WAIT_SECONDS = 5.0


class StandaloneRedisProvider(IRedisConnectionProvider):
    def __init__(self, config: RedisConnectionConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        # Keyed by thread, with the bound loop stored alongside so a client
        # left over from a closed loop is discarded rather than raising
        # "attached to a different loop" on first use.
        self._clients: dict[int, tuple[Redis, Optional[asyncio.AbstractEventLoop]]] = {}
        self._created_clients: list[Redis] = []
        self._created_lock = threading.Lock()
        self._logged_startup = False
        self._log_startup_once()

    def _log_startup_once(self) -> None:
        if self._logged_startup:
            return
        self._logged_startup = True
        logger.info(
            "Redis connection provider: mode=standalone host=%s port=%s db=%s "
            "namespace=%s tls=%s",
            self._config.host,
            self._config.port,
            self._config.db,
            self._config.key_namespace or "(none)",
            self._config.tls,
        )
        if self._config.db:
            logger.warning(
                "REDIS_DB=%s is deprecated; prefer REDIS_KEY_NAMESPACE for tenant "
                "isolation. Ignored entirely in cluster mode.",
                self._config.db,
            )
        if self._config.tls and not self._config.tls_reject_unauthorized:
            # Kept as an escape hatch (self-signed certs, cert-rotation
            # windows) but never silent: with verification off the connection
            # is encrypted yet unauthenticated, so it does not protect against
            # an active man-in-the-middle. REDIS_TLS_CA_PATH is the fix for a
            # private CA.
            logger.warning(
                "REDIS_TLS_REJECT_UNAUTHORIZED=false: Redis TLS certificates are "
                "NOT verified, so the connection is encrypted but not "
                "authenticated. Set REDIS_TLS_CA_PATH to trust a private CA "
                "instead of disabling verification."
            )

    def _retry_policy(self) -> Retry:
        return Retry(
            ExponentialBackoff(cap=_RETRY_MAX_DELAY, base=_RETRY_BASE_DELAY),
            retries=3,
        )

    def _connection_kwargs(self, options: ClientOptions) -> dict:
        kwargs: dict = {
            "host": self._config.host,
            "port": self._config.port,
            "db": self._config.db,
            "decode_responses": options.decode_responses,
            "socket_timeout": options.socket_timeout_seconds,
            "socket_connect_timeout": options.socket_connect_timeout_seconds,
            "health_check_interval": options.health_check_interval_seconds,
            "retry": self._retry_policy(),
            "retry_on_error": [RedisConnectionError, RedisTimeoutError, OSError],
        }
        if self._config.username:
            kwargs["username"] = self._config.username
        if self._config.password:
            kwargs["password"] = self._config.password
        if self._config.tls:
            kwargs["ssl"] = True
            kwargs["ssl_cert_reqs"] = "required" if self._config.tls_reject_unauthorized else None
            if self._config.tls_ca_path:
                kwargs["ssl_ca_certs"] = self._config.tls_ca_path
        if options.blocking:
            # A dedicated BlockingConnectionPool: contention queues on a
            # fixed number of connections instead of opening new ones.
            pool = BlockingConnectionPool(
                timeout=_POOL_WAIT_SECONDS,
                max_connections=options.max_connections,
                **kwargs,
            )
            return {"connection_pool": pool}
        return kwargs

    def _track(self, client: Redis) -> Redis:
        with self._created_lock:
            self._created_clients.append(client)
        return client

    def get_client(self) -> Redis:
        """Client bound to the currently running loop, created on first use per loop."""
        thread_id = threading.get_ident()
        try:
            current_loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
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
                    logger.debug("Discarding stale Redis client for thread %s", thread_id)
                    del self._clients[thread_id]
                else:
                    return client

            client = Redis(**self._connection_kwargs(ClientOptions()))
            self._track(client)
            self._clients[thread_id] = (client, current_loop)
            return client

    def create_client(self, options: ClientOptions | None = None) -> Redis:
        client = Redis(**self._connection_kwargs(options or ClientOptions()))
        return self._track(client)

    def create_pubsub_client(self) -> Redis:
        return self.create_client(ClientOptions(blocking=True))

    async def scan_keys(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        client = self.get_client()
        async for key in client.scan_iter(match=pattern, count=count):
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    async def load_script(self, body: str) -> str:
        client = self.get_client()
        sha = await client.script_load(body)
        return sha.decode("utf-8") if isinstance(sha, bytes) else sha

    def key_slot(self, key: str) -> int:  # noqa: ARG002
        return 0

    def connection_url(self) -> str:
        scheme = "rediss" if self._config.tls else "redis"
        auth = ""
        if self._config.username:
            auth = quote(self._config.username, safe="")
            if self._config.password:
                auth += f":{quote(self._config.password, safe='')}"
            auth += "@"
        elif self._config.password:
            auth = f":{quote(self._config.password, safe='')}@"
        return f"{scheme}://{auth}{self._config.host}:{self._config.port}/{self._config.db}"

    async def ping(self) -> bool:
        try:
            return bool(await self.get_client().ping())
        except Exception as exc:
            logger.debug("Redis ping failed: %s", exc)
            return False

    async def close(self) -> None:
        with self._created_lock:
            clients = list(self._created_clients)
            self._created_clients.clear()
        with self._lock:
            self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception as exc:
                logger.debug("Error closing Redis client: %s", exc)

    @property
    def is_cluster(self) -> bool:
        return False

    @property
    def mode(self) -> str:
        return "standalone"

    @property
    def key_namespace(self) -> str:
        return self._config.key_namespace
