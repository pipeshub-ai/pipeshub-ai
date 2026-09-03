"""Configuration types for :mod:`app.services.redis`.

Kept free of any ``redis`` import so it can be constructed by callers that
never touch a client directly (e.g. ``ConfigurationService`` bootstrap code).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional

ScaleReads = Literal["master", "slave", "all"]


@dataclass
class Credentials:
    """Rotating credentials returned by ``RedisConnectionConfig.credentials_provider``."""

    username: Optional[str]
    password: str


@dataclass
class ClientOptions:
    """Portable client knobs; each provider maps these onto its own client (R12).

    ``blocking=True`` is for dedicated connections used for ``XREADGROUP
    BLOCK``, ``SUBSCRIBE``, or any other long-lived call that must not be
    reclaimed by a bounded pool -- standalone honours it by disabling the
    pool's blocking wait; cluster/EE providers honour it however their
    client library requires (e.g. a dedicated node connection).
    """

    decode_responses: bool = True
    # Maps to the pool size when ``blocking=True`` (standalone builds a
    # ``BlockingConnectionPool``). A consumer that parks one connection in
    # ``XREADGROUP BLOCK`` while acking and claiming on the same client needs
    # headroom here; redis-py's own default pool is effectively unbounded, so
    # anything that previously relied on that must size this explicitly.
    max_connections: int = 10
    socket_timeout_seconds: float = 5.0
    socket_connect_timeout_seconds: float = 5.0
    health_check_interval_seconds: int = 30
    retry_attempts: int = 3
    blocking: bool = False


@dataclass
class RedisConnectionConfig:
    """Everything a connection provider needs to build clients.

    ``db`` is DEPRECATED (R4): honoured only by ``StandaloneRedisProvider``.
    The factory rejects ``db != 0`` when the selected mode is not
    ``standalone`` so an upgrade never silently falls back to an empty
    database. New deployments should isolate tenants via ``key_namespace``
    instead, which is applied inside explicit key builders (R9) -- never as
    a client-level prefix.
    """

    host: str = "localhost"
    port: int = 6379
    username: Optional[str] = None
    password: Optional[str] = None
    # Rotating credentials (e.g. MemoryDB IAM auth); takes precedence over
    # username/password when set (R21). Signature intentionally stable so an
    # EE provider never needs an interface change to support IAM rotation.
    credentials_provider: Optional[Callable[[], Awaitable[Credentials]]] = None
    tls: bool = False
    tls_reject_unauthorized: bool = True
    tls_ca_path: Optional[str] = None
    db: int = 0
    key_namespace: str = ""
    connect_timeout_seconds: float = 10.0
    # Cluster-specific; ignored by StandaloneRedisProvider (R21).
    cluster_endpoints: list[str] = field(default_factory=list)
    scale_reads: ScaleReads = "master"
    nat_map: dict[str, tuple[str, int]] = field(default_factory=dict)

    @staticmethod
    def from_host_port(
        host: str,
        port: int,
        password: Optional[str] = None,
        db: int = 0,
        username: Optional[str] = None,
        tls: bool = False,
    ) -> "RedisConnectionConfig":
        """Adapt the legacy ``host``/``port``/``password``/``db`` shape used
        throughout the messaging and config-store modules (e.g.
        ``app.services.messaging.config.RedisConfig``,
        ``ConfigurationService.get_redis_config()``) into a full connection
        config. Process-wide settings that shape predates -- cluster
        endpoints, key namespace, TLS trust material -- are layered on top
        from the environment, since every one of those call sites talks to
        the same Redis deployment as everything else in the process.

        ``tls`` is OR-ed with ``REDIS_TLS_ENABLED`` rather than replacing it:
        an install that enabled TLS through the admin UI has the flag only in
        its stored config and no env var set, so ignoring it here would
        downgrade that deployment to plaintext.
        """
        base = RedisConnectionConfig.from_env()
        base.host = host
        base.port = port
        base.password = password
        base.username = username
        base.db = db
        base.tls = base.tls or bool(tls)
        return base

    @staticmethod
    def from_env(prefix: str = "REDIS_") -> "RedisConnectionConfig":
        """Build config from the standard ``REDIS_*`` environment variables."""
        endpoints_raw = os.getenv(f"{prefix}CLUSTER_ENDPOINTS", "")
        endpoints = [e.strip() for e in endpoints_raw.split(",") if e.strip()]
        return RedisConnectionConfig(
            host=os.getenv(f"{prefix}HOST", "localhost"),
            port=int(os.getenv(f"{prefix}PORT", "6379")),
            username=os.getenv(f"{prefix}USERNAME") or None,
            password=os.getenv(f"{prefix}PASSWORD") or None,
            tls=os.getenv(f"{prefix}TLS_ENABLED", "false").lower() == "true",
            tls_reject_unauthorized=os.getenv(
                f"{prefix}TLS_REJECT_UNAUTHORIZED", "true"
            ).lower()
            == "true",
            tls_ca_path=os.getenv(f"{prefix}TLS_CA_PATH") or None,
            db=int(os.getenv(f"{prefix}DB", "0")),
            key_namespace=os.getenv(f"{prefix}KEY_NAMESPACE", ""),
            connect_timeout_seconds=float(os.getenv(f"{prefix}TIMEOUT", "10000")) / 1000,
            cluster_endpoints=endpoints,
            scale_reads=os.getenv(f"{prefix}CLUSTER_SCALE_READS", "master"),  # type: ignore[arg-type]
        )
