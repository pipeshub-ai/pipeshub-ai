"""Keeps the live ``records`` schema at least as wide as this process's own.

A process that is older than this one applies its own, narrower record schema when it starts. A record that
holds a newer field then fails every write with errorNum 1620. This guard:

* reads the schema's generation (a ``$comment`` on the rule) and applies this process's schema only while the
  live generation is lower, never when it is higher (no union: a narrowed enum is replaced, not merged);
* widens the schema **outside** any transaction: a schema change waits for every open stream transaction that wrote
  the collection and every later write queues behind it, so inside a transaction it would wait on itself;
* runs one widening at a time across processes (a Redis lock), at a moment no stream transaction is open when one
  can be found within 5 s, with a 5 s client timeout.

A 1620 inside a stream transaction aborts the transaction and raises ``RecordSchemaNarrowError`` (see
``ArangoHTTPClient``); the owner of the transaction runs it again once ``wait_wide`` says the schema is wide.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from app.core.org_context import spawn_detached
from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from logging import Logger

REWIDEN_TIMEOUT_S = 5.0
QUIET_POLL_S = 0.2
POLL_INTERVAL_S = 10.0
LOCK_KEY = "pipeshub:record-schema:rewiden"
LOCK_TTL_MS = 15_000

REWIDEN_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_total",
    "Times this process widened the live records schema; above 0 in steady state means an older image is running",
    ["trigger"],
)
REWIDEN_CONTENDED_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_contended_total",
    "Widenings issued while a stream transaction was still open after the quiet-point wait",
    ["trigger"],
)
REWIDEN_WAIT_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_record_schema_rewiden_wait_seconds",
    "Time from sending the schema change to its answer or its client timeout",
    ["trigger"],
    (0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10),
)

# "does not match the record schema" but not "the file record schema" and the other record types' messages.
_RECORD_SCHEMA_ERROR = re.compile(r"does not match the record schema")
_GENERATION = re.compile(r"gen=(\d+)")


def is_record_schema_error(error: BaseException | str) -> bool:
    """errorNum 1620 raised by the ``records`` schema (not a record type's)."""
    text = str(error)
    return "1620" in text and bool(_RECORD_SCHEMA_ERROR.search(text))


def generation_of_schema(schema: dict[str, Any] | None) -> int:
    """The generation a collection schema carries; none or unparsable is 0 (every image before the marker)."""
    if not isinstance(schema, dict):
        return 0
    rule = schema.get("rule")
    comment = rule.get("$comment") if isinstance(rule, dict) else None
    found = _GENERATION.search(comment) if isinstance(comment, str) else None
    return int(found.group(1)) if found else 0


class Lock(Protocol):
    async def acquire(self, ttl_ms: int) -> bool: ...
    async def release(self) -> None: ...


class RedisLock:
    """``SET key token NX PX``; released only by its holder."""

    _RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"

    def __init__(self, client: Any, key: str) -> None:  # noqa: ANN401
        self._client, self._key, self._token = client, key, uuid.uuid4().hex

    async def acquire(self, ttl_ms: int) -> bool:
        return bool(await self._client.set(self._key, self._token, nx=True, px=ttl_ms))

    async def release(self) -> None:
        await self._client.eval(self._RELEASE, 1, self._key, self._token)


LockFactory = Callable[[], Awaitable[Lock | None]]


class RecordSchemaGuard:
    """One per ``ArangoHTTPClient``. ``schema`` is the schema this process applies, ``generation`` its generation."""

    def __init__(
        self,
        client: Any,  # noqa: ANN401
        logger: Logger,
        *,
        schema: dict[str, Any],
        generation: int,
        collection: str = "records",
        lock_factory: LockFactory | None = None,
    ) -> None:
        self._client = client
        self._logger = logger
        self._schema = schema
        self.generation = generation
        self._collection = collection
        self._lock_factory = lock_factory
        self._narrowed = False
        self._aborted: OrderedDict[str, float] = OrderedDict()
        self._loop_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
        self._poller: asyncio.Task[None] | None = None

    # -- aborted transactions ---------------------------------------------------------------------------------
    def mark_aborted(self, txn_id: str) -> None:
        self._aborted[txn_id] = time.monotonic()
        while len(self._aborted) > 4096:
            self._aborted.popitem(last=False)

    def is_aborted(self, txn_id: str | None) -> bool:
        return txn_id is not None and txn_id in self._aborted

    def check_usable(self, txn_id: str | None) -> None:
        if self.is_aborted(txn_id):
            raise RecordSchemaNarrowError(f"transaction {txn_id} was aborted by a records schema refusal; run it again")

    # -- state ------------------------------------------------------------------------------------------------
    @property
    def narrowed(self) -> bool:
        return self._narrowed

    def note_narrow(self) -> None:
        self._narrowed = True

    async def live_generation(self) -> int:
        return generation_of_schema(await self._client.get_collection_schema(self._collection))

    async def is_wide(self) -> bool:
        """Whether the live schema is at least this process's generation. An unreadable schema is "not known to be wide"."""
        try:
            return await self.live_generation() >= self.generation
        except Exception as error:  # treated as narrow: the caller widens and retries, as for a narrowing
            self._logger.warning("could not read the records schema generation: %s", error)
            return False

    async def wait_wide(self, timeout: float = REWIDEN_TIMEOUT_S) -> bool:
        """Wait until the live schema is at least this process's generation (or ``timeout``); True when it is."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                if await self.live_generation() >= self.generation:
                    self._narrowed = False
                    return True
            except Exception as error:
                self._logger.warning("could not read the records schema generation: %s", error)
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.1)

    # -- widening ---------------------------------------------------------------------------------------------
    async def ensure_wide(self, trigger: str, *, transaction: str | None = None) -> bool:
        """Apply this process's schema if the live generation is lower. Never inside a transaction."""
        assert transaction is None, "the records schema is never widened inside a transaction"
        loop = asyncio.get_running_loop()
        local = self._loop_locks.setdefault(loop, asyncio.Lock())
        async with local:
            if await self.live_generation() >= self.generation:
                self._narrowed = False
                return False
            lock: Lock | None = None
            if self._lock_factory is not None:
                try:
                    lock = await self._lock_factory()
                    if lock is not None and not await lock.acquire(LOCK_TTL_MS):
                        # another process is fixing it
                        await self.wait_wide(REWIDEN_TIMEOUT_S)
                        return False
                except Exception as error:
                    self._logger.warning("record schema widening runs without its lock: %s", error)
                    lock = None
            try:
                if await self.live_generation() >= self.generation:
                    self._narrowed = False
                    return False
                contended = not await self._wait_quiet()
                if contended:
                    REWIDEN_CONTENDED_TOTAL.inc(trigger)
                started = time.monotonic()
                try:
                    applied = await self._client.update_collection_schema(
                        self._collection, self._schema, timeout=REWIDEN_TIMEOUT_S,
                    )
                except Exception as error:
                    self._logger.warning("record schema widening did not finish in %.0fs: %s", REWIDEN_TIMEOUT_S, error)
                    applied = False
                elapsed = time.monotonic() - started
                REWIDEN_WAIT_SECONDS.observe(trigger, value=elapsed)
                REWIDEN_TOTAL.inc(trigger)
                self._logger.warning(
                    "widened the records schema to generation %d (trigger=%s, contended=%s, %.2fs, applied=%s)",
                    self.generation, trigger, contended, elapsed, applied,
                )
                if applied:
                    self._narrowed = False
                return bool(applied)
            finally:
                if lock is not None:
                    try:
                        await lock.release()
                    except Exception:
                        self._logger.debug("could not release the record schema lock", exc_info=True)

    async def _wait_quiet(self) -> bool:
        """True when no stream transaction is running before the 5 s are up."""
        deadline = time.monotonic() + REWIDEN_TIMEOUT_S
        while True:
            try:
                if not await self._client.list_running_transactions():
                    return True
            except Exception:
                return False
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(QUIET_POLL_S)

    # -- polling ----------------------------------------------------------------------------------------------
    def start(self, interval: float = POLL_INTERVAL_S) -> None:
        """Check the live generation every ``interval`` seconds (one GET) on the running loop."""
        if self._poller is not None and not self._poller.done():
            return

        async def poll() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    if await self.live_generation() < self.generation:
                        await self.ensure_wide("poll")
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._logger.warning("record schema poll failed: %s", error)

        self._poller = spawn_detached(poll(), name="record-schema-poll")

    async def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            self._poller = None
