"""``IRedisConnectionProvider`` -- the one seam every Redis feature depends on.

No feature code should import ``redis.asyncio.Redis`` /
``redis.asyncio.cluster.RedisCluster`` directly (enforced by the
architecture-guard test in ``tests/unit/agent_loop_lib`` sibling package --
see ``tests/unit/services/redis/test_architecture_guard.py``). Everything
goes through a provider obtained from
:class:`app.services.redis.connection_provider_factory.RedisConnectionProviderFactory`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator, Union

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from redis.asyncio.cluster import RedisCluster

    from app.services.redis.config import ClientOptions

    RedisClient = Union[Redis, RedisCluster]
else:
    RedisClient = object


class IRedisConnectionProvider(ABC):
    """Owns Redis topology; every implementation/algorithm depends on this, not a client class."""

    @abstractmethod
    def get_client(self) -> "RedisClient":
        """Shared client for request/response traffic.

        Standalone binds one client per event loop (absorbing what used to
        be ``RedisClientRegistry`` / ``redis_store._get_client``'s own
        per-loop affinity dict); cluster implementations may do the same
        internally. Callers must not cache the return value across loops.
        """

    @abstractmethod
    def create_client(self, options: "ClientOptions | None" = None) -> "RedisClient":
        """Fresh, caller-owned client for blocking reads / pub-sub / worker connections."""

    @abstractmethod
    def create_pubsub_client(self) -> "Redis":
        """Plain connection suitable for ``SUBSCRIBE`` (R13).

        Cluster implementations hand back a connection to a single node;
        regular (non-sharded) ``PUBLISH`` still propagates cluster-wide, so
        any subscriber sees it regardless of which node it is subscribed to.
        """

    @abstractmethod
    def scan_keys(self, pattern: str, count: int = 100) -> AsyncIterator[str]:
        """Keyspace-wide SCAN (R2). Cluster implementations fan out over every master."""

    @abstractmethod
    async def load_script(self, body: str) -> str:
        """``SCRIPT LOAD`` everywhere the script may execute; returns its SHA (R6).

        Cluster implementations load on every master so a subsequent
        ``EVALSHA`` against any key never hits ``NOSCRIPT``. Callers still
        handle ``NOSCRIPT`` defensively (a master added after the initial
        load, or a cluster resharding mid-flight).
        """

    @abstractmethod
    def key_slot(self, key: str) -> int:
        """Hash slot for ``key``. Standalone returns 0 for every key (R1)."""

    @abstractmethod
    def connection_url(self) -> str:
        """A ``redis://`` URL for sync consumers that build their own client (Celery).

        Raises :class:`NotImplementedError` on cluster providers -- Celery's
        ``kombu`` transport has no Redis Cluster support (R7); callers must
        supply ``CELERY_BROKER_URL`` / ``CELERY_RESULT_BACKEND`` instead.
        """

    async def prepare(self) -> None:
        """Resolve anything that needs an event loop before clients are built.

        The sync ``get_client``/``create_client`` builders cannot await
        ``RedisConnectionConfig.credentials_provider`` (MemoryDB IAM tokens,
        which rotate every 12h -- R21), so a provider that uses one resolves
        it here and caches the result for those builders to read. Default is
        a no-op: neither OSS provider has rotating credentials.

        Idempotent, and safe to call from any startup path; an EE provider
        that also refreshes on a timer starts that timer here.
        """
        return None

    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None:
        """Close every client this provider handed out."""

    @property
    @abstractmethod
    def is_cluster(self) -> bool: ...

    @property
    @abstractmethod
    def mode(self) -> str:
        """The registered mode name this instance was created under (e.g. ``standalone``)."""

    @property
    @abstractmethod
    def key_namespace(self) -> str:
        """``REDIS_KEY_NAMESPACE``, or ``""`` when unset (R9).

        Callers building an explicit key (``KEY_PREFIX`` constants,
        ``_build_key`` helpers) or a pub/sub channel name prepend this
        themselves -- it is never applied as a client-level prefix, which
        would silently miss ``SCAN`` patterns, Lua script bodies, and
        channel names.
        """
