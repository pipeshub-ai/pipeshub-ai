"""Generic OSS Redis Cluster provider.

Redis Cluster is plain open-source Redis, so OSS ships this (Open Decision
2) both so `REDIS_MODE=cluster` works against a self-hosted Redis Cluster
and so OSS CI can run the full contract-test suite against a real 3-master
cluster and prove the rest of the refactor is cluster-safe. AWS MemoryDB is
protocol-compatible with Redis Cluster; an EE ``memorydb`` provider extends
this class to add IAM ``credentials_provider`` rotation, a NAT map for
private VPC addressing, and TLS-by-default, then registers itself with
:class:`app.services.redis.connection_provider_factory.RedisConnectionProviderFactory`.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, Optional

from redis.asyncio import Redis
from redis.asyncio.cluster import ClusterNode, RedisCluster
from redis.crc import key_slot

from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider
from app.utils.logger import create_logger

logger = create_logger("redis_cluster_provider")


class ClusterRedisProvider(IRedisConnectionProvider):
    def __init__(self, config: RedisConnectionConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._clients: dict[int, tuple[RedisCluster, Optional[asyncio.AbstractEventLoop]]] = {}
        self._created_clients: list[RedisCluster] = []
        self._pubsub_clients: list[Redis] = []
        self._created_lock = threading.Lock()
        logger.info(
            "Redis connection provider: mode=cluster endpoints=%s namespace=%s tls=%s "
            "scale_reads=%s",
            self._startup_nodes_repr(),
            self._config.key_namespace or "(none)",
            self._config.tls,
            self._config.scale_reads,
        )

    def _startup_nodes_repr(self) -> str:
        if self._config.cluster_endpoints:
            return ",".join(self._config.cluster_endpoints)
        return f"{self._config.host}:{self._config.port}"

    def _startup_nodes(self) -> list[ClusterNode]:
        if self._config.cluster_endpoints:
            nodes = []
            for endpoint in self._config.cluster_endpoints:
                host, _, port = endpoint.partition(":")
                nodes.append(ClusterNode(host=host, port=int(port) if port else 6379))
            return nodes
        return [ClusterNode(host=self._config.host, port=self._config.port)]

    def _client_kwargs(self, options: ClientOptions) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "startup_nodes": self._startup_nodes(),
            "decode_responses": options.decode_responses,
            "socket_timeout": options.socket_timeout_seconds,
            "socket_connect_timeout": options.socket_connect_timeout_seconds,
            "max_connections": options.max_connections,
            "read_from_replicas": self._config.scale_reads in ("slave", "all"),
            "require_full_coverage": True,
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
        return kwargs

    def _track(self, client: RedisCluster) -> RedisCluster:
        with self._created_lock:
            self._created_clients.append(client)
        return client

    def get_client(self) -> RedisCluster:
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
                if not stale:
                    return client
                del self._clients[thread_id]

            client = RedisCluster(**self._client_kwargs(ClientOptions()))
            self._track(client)
            self._clients[thread_id] = (client, current_loop)
            return client

    def create_client(self, options: ClientOptions | None = None) -> RedisCluster:
        client = RedisCluster(**self._client_kwargs(options or ClientOptions()))
        return self._track(client)

    def create_pubsub_client(self) -> Redis:
        """A *dedicated* plain connection to one node (R13).

        Regular (non-sharded) ``PUBLISH`` propagates cluster-wide, so a
        subscriber on any single node sees every message. Deliberately not
        ``get_default_node().redis_connection``: that connection belongs to
        the shared cluster client, and ``SUBSCRIBE`` puts a connection into
        subscriber mode where it can no longer serve ordinary commands -- a
        caller that also closes it would take the shared client down with it.

        Falls back to the configured endpoint when the cluster client has not
        discovered its topology yet, so this never depends on connection
        ordering.
        """
        host, port = self._pubsub_endpoint()
        kwargs = self._client_kwargs(ClientOptions(blocking=True))
        kwargs.pop("startup_nodes", None)
        kwargs.pop("read_from_replicas", None)
        kwargs.pop("require_full_coverage", None)
        kwargs.pop("max_connections", None)
        client = Redis(host=host, port=port, **kwargs)
        with self._created_lock:
            self._pubsub_clients.append(client)
        return client

    def _pubsub_endpoint(self) -> tuple[str, int]:
        try:
            node = self.get_client().get_default_node()
        except Exception:  # pragma: no cover - topology not loaded yet
            node = None
        if node is not None:
            return node.host, node.port
        startup = self._startup_nodes()[0]
        return startup.host, startup.port

    async def scan_keys(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        """Keyspace-wide SCAN: ioredis-style ``Cluster.scan()`` hits one node,
        so the equivalent Node.js provider fans out over every master
        explicitly (R2). redis-py's async ``RedisCluster`` does not need
        that -- ``scan_iter`` on the client itself already targets every
        primary and merges the cursors (unlike ``ClusterNode``, which has no
        ``redis_connection`` attribute to iterate on in the async client;
        an earlier version of this method assumed the sync-client shape and
        silently scanned zero nodes)."""
        client = self.get_client()
        async for key in client.scan_iter(match=pattern, count=count):
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    async def load_script(self, body: str) -> str:
        """redis-py's async ``RedisCluster`` already special-cases ``SCRIPT
        LOAD`` as an all-nodes command (verified against a live 3-master
        cluster: a script loaded once evalsha's successfully against keys
        on every node) -- no manual per-node fan-out needed, and the
        previous per-node loop here never actually reached any node (see
        ``scan_keys``)."""
        client = self.get_client()
        sha = await client.script_load(body)
        return sha.decode("utf-8") if isinstance(sha, bytes) else sha

    def key_slot(self, key: str) -> int:
        return key_slot(key.encode("utf-8") if isinstance(key, str) else key)

    def connection_url(self) -> str:
        raise NotImplementedError(
            "Redis Cluster has no single connection URL; Celery/kombu has no "
            "cluster transport (R7) -- set CELERY_BROKER_URL / "
            "CELERY_RESULT_BACKEND to a non-cluster broker instead."
        )

    async def ping(self) -> bool:
        try:
            return bool(await self.get_client().ping())
        except Exception as exc:
            logger.debug("Redis Cluster ping failed: %s", exc)
            return False

    async def close(self) -> None:
        with self._created_lock:
            clients: list[Any] = list(self._pubsub_clients) + list(self._created_clients)
            self._pubsub_clients.clear()
            self._created_clients.clear()
        with self._lock:
            self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception as exc:
                logger.debug("Error closing Redis Cluster client: %s", exc)

    @property
    def is_cluster(self) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "cluster"

    @property
    def key_namespace(self) -> str:
        return self._config.key_namespace
