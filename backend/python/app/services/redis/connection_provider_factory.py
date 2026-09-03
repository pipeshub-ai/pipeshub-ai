"""Factory + process-level singleton registry for :class:`IRedisConnectionProvider`.

An EE repo adds MemoryDB support entirely through this module's extension
points, with zero changes to any file in this package:

1. Implement ``IRedisConnectionProvider`` (or subclass ``ClusterRedisProvider``).
2. Call ``RedisConnectionProviderFactory.register("memorydb", MemoryDBProvider)``
   at import time.
3. Set ``REDIS_PROVIDER_MODULE=ee.backend.python.app.services.redis.memorydb_provider``
   (or publish a ``pipeshub.redis_providers`` entry point) and ``REDIS_MODE=memorydb``.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import os
import threading
from typing import Callable

from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider
from app.utils.logger import create_logger

logger = create_logger("redis_provider_factory")

_ENTRY_POINT_GROUP = "pipeshub.redis_providers"


class RedisConnectionProviderFactory:
    _registry: dict[str, Callable[[RedisConnectionConfig], IRedisConnectionProvider]] = {}
    _discovered_modules: set[str] = set()

    @classmethod
    def register(
        cls,
        mode: str,
        provider_factory: Callable[[RedisConnectionConfig], IRedisConnectionProvider],
    ) -> None:
        cls._registry[mode] = provider_factory

    @classmethod
    def registered_modes(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def create(
        cls, config: RedisConnectionConfig | None = None, mode: str | None = None
    ) -> IRedisConnectionProvider:
        config = config or RedisConnectionConfig.from_env()
        resolved_mode = mode or os.getenv("REDIS_MODE", "standalone")

        provider_factory = cls._registry.get(resolved_mode)
        if provider_factory is None:
            cls._discover(resolved_mode)
            provider_factory = cls._registry.get(resolved_mode)

        if provider_factory is None:
            raise ValueError(
                f"Unknown REDIS_MODE '{resolved_mode}'; registered modes: "
                f"{cls.registered_modes()}"
            )

        # Credentials over an unauthenticated channel (R14/CWE-295). TLS with
        # verification off is encrypted but *unauthenticated*: any MITM can
        # present a self-signed cert, terminate the session, and harvest the
        # password. Enforced here rather than in each provider so every
        # implementation -- including one registered by an EE repo -- is
        # covered by the same rule.
        if (
            config.tls
            and not config.tls_reject_unauthorized
            and (config.password or config.username)
        ):
            raise ValueError(
                "REDIS_TLS_REJECT_UNAUTHORIZED=false with Redis credentials set: "
                "the connection would be encrypted but not authenticated, so the "
                "password is exposed to anyone who can intercept it. Point "
                "REDIS_TLS_CA_PATH at the CA that signed your Redis certificate "
                "instead of disabling verification."
            )

        if resolved_mode != "standalone" and config.db:
            raise ValueError(
                "REDIS_DB is not supported outside standalone mode "
                f"(REDIS_MODE={resolved_mode}); use REDIS_KEY_NAMESPACE for "
                "tenant isolation instead."
            )

        return provider_factory(config)

    @classmethod
    def _discover(cls, mode: str) -> None:
        """Import ``REDIS_PROVIDER_MODULE`` and/or entry points, then retry (R10).

        The standalone and cluster providers register themselves at the
        bottom of this module, so importing this factory is enough for the
        OSS modes; this hook exists purely for the EE extension point.
        """
        module_name = os.getenv("REDIS_PROVIDER_MODULE")
        if module_name and module_name not in cls._discovered_modules:
            cls._discovered_modules.add(module_name)
            try:
                importlib.import_module(module_name)
                logger.info("Loaded Redis provider module '%s'", module_name)
            except ImportError as exc:
                logger.error(
                    "Failed to import REDIS_PROVIDER_MODULE '%s': %s", module_name, exc
                )

        if mode in cls._registry:
            return

        try:
            entry_points = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
        except Exception as exc:  # pragma: no cover - defensive, metadata API varies
            logger.debug("Could not enumerate '%s' entry points: %s", _ENTRY_POINT_GROUP, exc)
            return

        for entry_point in entry_points:
            if entry_point.name in cls._discovered_modules:
                continue
            cls._discovered_modules.add(entry_point.name)
            try:
                entry_point.load()
            except Exception as exc:
                logger.error("Failed to load Redis provider entry point '%s': %s", entry_point.name, exc)


# --- Process-level singleton accessor (R11) ---------------------------------
#
# Node builds RedisService in multiple containers today; the Python side has
# the equivalent risk once every Redis-backed service asks the factory for a
# provider independently. Cache by config fingerprint so the common case --
# every caller sharing the same REDIS_* env -- collapses onto one provider
# (and, on cluster, one connection to every node) per process.

_provider_lock = threading.Lock()
_provider_cache: dict[tuple, IRedisConnectionProvider] = {}


def _fingerprint(config: RedisConnectionConfig, mode: str) -> tuple:
    # Every field consumed by StandaloneRedisProvider._connection_kwargs() /
    # ClusterRedisProvider._client_kwargs() must be here: two configs that
    # differ only in one of these fields need distinct provider instances,
    # or the second caller silently reuses the first caller's connection
    # (wrong credentials/TLS/read routing) instead of getting its own.
    return (
        mode,
        config.host,
        config.port,
        config.username,
        config.password,
        config.tls,
        config.tls_reject_unauthorized,
        config.tls_ca_path,
        config.db,
        config.key_namespace,
        tuple(config.cluster_endpoints),
        config.scale_reads,
    )


def get_redis_provider(
    config: RedisConnectionConfig | None = None, mode: str | None = None
) -> IRedisConnectionProvider:
    """Return the shared provider for this config, creating it on first use."""
    config = config or RedisConnectionConfig.from_env()
    resolved_mode = mode or os.getenv("REDIS_MODE", "standalone")
    fingerprint = _fingerprint(config, resolved_mode)

    with _provider_lock:
        existing = _provider_cache.get(fingerprint)
        if existing is not None:
            return existing
        provider = RedisConnectionProviderFactory.create(config, mode=resolved_mode)
        _provider_cache[fingerprint] = provider
        return provider


_prepared: set[int] = set()


async def get_prepared_redis_provider(
    config: RedisConnectionConfig | None = None, mode: str | None = None
) -> IRedisConnectionProvider:
    """``get_redis_provider()`` plus a one-time ``await provider.prepare()``.

    Use this from any async startup path. Sync call sites can keep using
    ``get_redis_provider()``; providers whose ``prepare()`` does real work
    (an EE MemoryDB provider resolving rotating IAM credentials -- R21) are
    expected to be wired through an async startup that calls this first, and
    the two share the same cached instance either way.
    """
    provider = get_redis_provider(config, mode)
    if id(provider) not in _prepared:
        await provider.prepare()
        _prepared.add(id(provider))
    return provider


def reset_redis_provider_registry() -> None:
    """Test-only: drop cached singleton providers between test cases."""
    with _provider_lock:
        _provider_cache.clear()
    _prepared.clear()


# Self-registration: importing this factory module is enough for both OSS modes.
from app.services.redis.cluster_provider import ClusterRedisProvider  # noqa: E402
from app.services.redis.standalone_provider import StandaloneRedisProvider  # noqa: E402

RedisConnectionProviderFactory.register("standalone", StandaloneRedisProvider)
RedisConnectionProviderFactory.register("cluster", ClusterRedisProvider)
