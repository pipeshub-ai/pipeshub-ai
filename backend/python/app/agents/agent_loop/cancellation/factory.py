"""`build_run_cancellation_registry()`: picks the `RunCancellationRegistry`
implementation for this process — KV-backed (cross-process fan-out) when
a KV store can be built, in-process-only otherwise. Registered as a
`providers.Singleton` on `QueryAppContainer` (`containers/query.py`) so
every request shares one registry instance per worker process.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.kv_backed import KVBackedRunCancellationRegistry
from app.config.constants.store_type import StoreType
from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig

if TYPE_CHECKING:
    from app.agents.agent_loop.cancellation.registry import RunCancellationRegistry
    from app.config.key_value_store import KeyValueStore

__all__ = ["build_run_cancellation_registry"]

logger = logging.getLogger(__name__)


def _serialize(value: str) -> bytes:
    return value.encode("utf-8")


def _deserialize(value: bytes) -> str | None:
    if not value:
        return None
    return value.decode("utf-8")


def _build_kv_store() -> "KeyValueStore[str] | None":
    """A plain, unencrypted, string-valued `KeyValueStore` for cancel
    signals — deliberately built directly via `KeyValueStoreFactory`, not
    `ConfigurationService`/`EncryptedKeyValueStore`: a cancel flag is not
    config, and has no confidentiality requirement that justifies the
    encrypt/decrypt round trip `EncryptedKeyValueStore` does on every
    key. Reads the same `KV_STORE_TYPE`/`REDIS_*`/`ETCD_*` env vars that
    class does, so both agree on which backend/host/port this deployment
    is actually running. `None` when etcd is selected but `ETCD_URL` is
    unset (matches `EncryptedKeyValueStore`'s own hard requirement there,
    except this falls back to in-process instead of raising)."""
    store_type_str = os.getenv("KV_STORE_TYPE", "redis").strip().lower()

    if store_type_str == "redis":
        config = StoreConfig(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", None),
            db=int(os.getenv("REDIS_DB", "0")),
            key_prefix=os.getenv("REDIS_KV_PREFIX", "pipeshub:kv:"),
            timeout=float(os.getenv("REDIS_TIMEOUT", "10000")) / 1000,
        )
        return KeyValueStoreFactory.create_store(
            store_type=StoreType.REDIS, serializer=_serialize, deserializer=_deserialize, config=config,
        )

    etcd_url = os.getenv("ETCD_URL")
    if not etcd_url:
        return None
    if "://" in etcd_url:
        etcd_url = etcd_url.split("://")[1]
    parts = etcd_url.split(":")
    config = StoreConfig(
        host=parts[0],
        port=int(parts[1]) if len(parts) > 1 else 2379,
        timeout=float(os.getenv("ETCD_TIMEOUT", "5000")) / 1000,
        username=os.getenv("ETCD_USERNAME", None),
        password=os.getenv("ETCD_PASSWORD", None),
    )
    return KeyValueStoreFactory.create_store(
        store_type=StoreType.ETCD3, serializer=_serialize, deserializer=_deserialize, config=config,
    )


def build_run_cancellation_registry() -> "RunCancellationRegistry":
    try:
        store = _build_kv_store()
    except Exception:
        # `.error()`, not `.warning()`: on the default multi-replica Helm
        # deployment (`replicaCount: 2`) or QUERY_UVICORN_WORKERS>1, this
        # fallback is silently wrong for any cancel request that lands on a
        # different worker/replica than the one running the stream — it
        # returns `cancelled: false` while generation keeps going. Loud by
        # design so it shows up in log-based alerting; this KV store is
        # deliberately built straight from env (see `_build_kv_store`), so
        # construction only fails on a real misconfiguration (bad
        # KV_STORE_TYPE/REDIS_*/ETCD_* value), not a transient outage.
        logger.error(
            "build_run_cancellation_registry: failed to build KV store, "
            "falling back to in-process-only cancellation — stop-generation "
            "requests will silently no-op on any other worker/replica until "
            "this is fixed", exc_info=True,
        )
        store = None
    if store is None:
        return InProcessRunCancellationRegistry()
    return KVBackedRunCancellationRegistry(store)
