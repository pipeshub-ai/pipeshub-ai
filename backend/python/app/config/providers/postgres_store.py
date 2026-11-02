"""PostgreSQL implementation of the key-value store (``KV_STORE_TYPE=postgres``): one table, ``platform.kv_entries``.

Same semantics as the Redis store: bytes in, bytes out through the caller's serializer; a TTL makes the key dead at
``expires_at`` (readers filter on it, a writer replaces it); ``overwrite=False`` is SET NX; ``update_value`` is SET XX
(and, as in Redis, drops the TTL when none is given). Change notification across processes is LISTEN/NOTIFY on one channel.
"""

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from app.config.key_value_store import KeyValueStore
from app.services.pg_runtime import KV_CHANNEL, PgListener, RuntimeDb, listen_dsn
from app.utils.logger import create_logger

logger = create_logger("postgres_store")

T = TypeVar("T")

CLEAR_ALL = "__CLEAR_ALL__"
_MAX_NOTIFY_PAYLOAD = 7000  # NOTIFY payloads are limited to 8000 bytes
_PURGE_EVERY_S = 60.0

_UPSERT = """/* pipeshub:kv.set */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, $2, CASE WHEN $3::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $3::double precision) END, now())
ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at, updated_at = excluded.updated_at
RETURNING true"""
# SET NX: an expired row counts as absent, so only a dead row may be overwritten
_INSERT_ABSENT = """/* pipeshub:kv.setnx */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, $2, CASE WHEN $3::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $3::double precision) END, now())
ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at, updated_at = excluded.updated_at
WHERE platform.kv_entries.expires_at <= now()
RETURNING true"""
_UPDATE_PRESENT = """/* pipeshub:kv.update */
UPDATE platform.kv_entries SET value = $2, updated_at = now(),
  expires_at = CASE WHEN $3::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $3::double precision) END
WHERE key = $1 AND (expires_at IS NULL OR expires_at > now()) RETURNING true"""
_GET = """/* pipeshub:kv.get */
SELECT value FROM platform.kv_entries WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())"""
_DELETE = """/* pipeshub:kv.delete */
DELETE FROM platform.kv_entries WHERE key = $1 RETURNING (expires_at IS NULL OR expires_at > now()) AS live"""
_RANGE = """/* pipeshub:kv.range */
SELECT key FROM platform.kv_entries WHERE key >= $1 AND ($2::text IS NULL OR key < $2) AND (expires_at IS NULL OR expires_at > now())
ORDER BY key"""
_PURGE = """/* pipeshub:kv.purge */
DELETE FROM platform.kv_entries WHERE key IN (SELECT key FROM platform.kv_entries WHERE expires_at <= now() LIMIT 1000 FOR UPDATE SKIP LOCKED)"""
_NOTIFY = (
    "/* pipeshub:kv.notify */ SELECT pg_notify('pipeshub_kv_changed', $1)"  # KV_CHANNEL
)


def _prefix_end(prefix: str) -> str | None:
    """Smallest string above every string that starts with ``prefix`` (byte order, as the "C" collation sorts)."""
    for i in range(len(prefix) - 1, -1, -1):
        if ord(prefix[i]) < 0x10FFFF:
            return prefix[:i] + chr(ord(prefix[i]) + 1)
    return None


class PostgresKeyValueStore(KeyValueStore[T], Generic[T]):
    def __init__(
        self,
        serializer: Callable[[T], bytes],
        deserializer: Callable[[bytes], T],
        runtime: RuntimeDb | None = None,
    ) -> None:
        self.serializer = serializer
        self.deserializer = deserializer
        self._rt = runtime or RuntimeDb()
        self._watchers: dict[str, list[tuple[Callable[[T | None], None], str]]] = {}
        self._listeners: dict[int, PgListener] = {}
        self._last_purge = 0.0

    @property
    def client(self) -> object | None:
        return None

    async def health_check(self) -> bool:
        try:
            db = await self._rt.db()
            async with db.runtime_uow() as tx:
                return (
                    await tx.fetchval(
                        "/* pipeshub:kv.health */ SELECT count(*) >= 0 FROM platform.kv_entries WHERE false"
                    )
                    is True
                )
        except Exception as e:
            logger.error("PostgreSQL KV health check failed: %s", e)
            return False

    async def wait_for_connection(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        delay = 0.5
        while time.monotonic() < deadline:
            if await self.health_check():
                return True
            await asyncio.sleep(min(delay, max(deadline - time.monotonic(), 0)))
            delay = min(delay * 2, 10)
        return False

    async def _write(self, sql: str, *args: Any) -> Any:  # noqa: ANN401
        db = await self._rt.db()
        async with db.runtime_uow(write=True) as tx:
            res = await tx.fetchval(sql, *args)
        if time.monotonic() - self._last_purge > _PURGE_EVERY_S:
            self._last_purge = time.monotonic()
            try:  # housekeeping after the write has committed: a failure here must not fail the caller's write
                async with db.runtime_uow(write=True) as tx:
                    await tx.execute(_PURGE)
            except Exception as e:
                logger.warning("PostgreSQL KV expired-row purge failed: %s", e)
        return res

    async def create_key(
        self,
        key: str,
        value: T,
        overwrite: bool = True,  # noqa: FBT001, FBT002
        ttl: int | None = None,
    ) -> bool:
        try:
            data = self.serializer(value)
            if overwrite:
                await self._write(_UPSERT, key, data, ttl)
            else:
                # inserts when absent; a live row blocks it, a dead (expired) row is replaced
                if not await self._write(_INSERT_ABSENT, key, data, ttl):
                    return False
        except Exception as e:
            raise ConnectionError(f"Failed to create key: {e}") from e
        await self._notify_watchers(key, value)
        return True

    async def update_value(self, key: str, value: T, ttl: int | None = None) -> None:
        try:
            ok = await self._write(_UPDATE_PRESENT, key, self.serializer(value), ttl)
        except Exception as e:
            raise ConnectionError(f"Failed to update key: {e}") from e
        if not ok:
            raise KeyError(f'Key "{key}" does not exist.')
        await self._notify_watchers(key, value)

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> T | None:
        try:
            db = await self._rt.db()
            async with db.runtime_uow() as tx:
                data = await tx.fetchval(_GET, key)
        except Exception as e:
            raise ConnectionError(f"Failed to get key: {e}") from e
        if data is None:
            return None
        try:
            out = self.deserializer(bytes(data))
            if out is None and data and raise_on_error:
                raise ValueError("Stored value could not be decoded")
            return out
        except json.JSONDecodeError:
            if raise_on_error:
                raise
            return None

    async def delete_key(self, key: str) -> bool:
        try:
            live = await self._write(_DELETE, key)
        except Exception as e:
            raise ConnectionError(f"Failed to delete key: {e}") from e
        if live:
            await self._notify_watchers(key, None)
        return bool(live)

    async def _keys(self, prefix: str) -> list[str]:
        try:
            db = await self._rt.db()
            async with db.runtime_uow() as tx:
                return [
                    r["key"]
                    for r in await tx.fetch(_RANGE, prefix, _prefix_end(prefix))
                ]
        except Exception as e:
            raise ConnectionError(f"Failed to list keys: {e}") from e

    async def get_all_keys(self) -> list[str]:
        return await self._keys("")

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return await self._keys(
            directory if directory.endswith("/") else f"{directory}/"
        )

    # Watches fire for writes made through this instance, as in the Redis store; other processes use subscribe_changes.
    async def _notify_watchers(self, key: str, value: T | None) -> None:
        for callback, _wid in list(self._watchers.get(key, [])):
            try:
                callback(value)
            except Exception as e:
                logger.error("Error in watcher callback: %s", e)

    async def watch_key(
        self,
        key: str,
        callback: Callable[[T | None], None],
        error_callback: Callable[[Exception], None] | None = None,
        watch_id: str | None = None,
    ) -> str:
        wid = watch_id if watch_id is not None else uuid.uuid4().hex
        self._watchers.setdefault(key, []).append((callback, wid))
        return wid

    async def cancel_watch(self, key: str, watch_id: str) -> None:
        left = [(cb, w) for cb, w in self._watchers.get(key, []) if w != watch_id]
        if left:
            self._watchers[key] = left
        else:
            self._watchers.pop(key, None)

    async def subscribe_changes(self, callback: Callable[[str], None]) -> Any | None:  # noqa: ANN401
        dsn = listen_dsn(self._rt.cfg)
        if dsn is None:
            logger.warning(
                "No direct PostgreSQL address for LISTEN (set PG_LISTEN_URL or PG_POOLER=none): "
                "config changes made by other processes are seen when the configuration cache entry expires "
                "(CONFIG_CACHE_TTL_SECONDS, 60 s by default)"
            )
            return None

        def on_reconnect() -> None:
            callback(
                CLEAR_ALL
            )  # notifications sent while disconnected are lost: drop every cached value

        lst = PgListener(dsn, KV_CHANNEL, callback, on_reconnect=on_reconnect)
        task = lst.start()
        self._listeners[id(task)] = lst
        return task

    async def publish_change(self, key: str) -> None:
        payload = key if len(key.encode()) <= _MAX_NOTIFY_PAYLOAD else CLEAR_ALL
        try:
            db = await self._rt.db()
            async with db.runtime_uow(write=True) as tx:
                await tx.execute(_NOTIFY, payload)
        except Exception as e:
            logger.error("Failed to publish change for %s: %s", key, e)

    async def unsubscribe_changes(self, handle: Any) -> None:  # noqa: ANN401
        lst = self._listeners.pop(id(handle), None)
        if lst is not None:
            await lst.stop()

    async def close(self) -> None:
        for lst in list(self._listeners.values()):
            await lst.stop()
        self._listeners.clear()
        self._watchers.clear()
