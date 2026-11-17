"""Keeps the live ``records`` schema at least as wide as this process's own.

A process that is older than this one applies its own, narrower record schema when it starts. A record that
holds a newer field then fails every write with errorNum 1620. This guard:

* reads the schema's generation (a ``$comment`` on the rule) and applies this process's schema only when it reads a
  lower live generation (no union: a narrowed enum is replaced, not merged); a read can be out of date by the time the
  write lands, see the limit below;
* widens the schema **outside** any transaction: a schema change waits for every open stream transaction that wrote
  the collection and every later write queues behind it, so inside a transaction it would wait on itself;
* runs one widening at a time across processes under a lease lock kept in ArangoDB itself (``ArangoLock``), at a moment no
  stream transaction is open when one can be found within 5 s, with a 5 s client timeout. A process that cannot take
  the lock, or that cannot tell it still holds it, writes no schema: the refused write is refused again and the poll
  tries again.

What this does not do: a lease cannot fence a schema change, which is not transactional with the lock. A process paused
for longer than the lease between its last look at the live generation and its write, or whose write the server applies
after its client timed out, can still put an older schema over a newer one. The repair below needs the later checks and
writes to succeed: a newer process that cannot read the generation, take the lock or reach the server repairs nothing. The lease, the generation read under it and the
last read right before the write narrow that window; they do not close it. The overwrite is repaired by the poll of a
newer process (every ``POLL_INTERVAL_S``), so the guarantee is eventual: the newer generation is back within one poll
interval of that process finding the lock free and a quiet point, and later than that when the lock is held (up to
``LOCK_TTL_MS`` after a holder died or timed out), when widenings are skipped for lack of a quiet point (up to
``FORCE_AFTER_SKIPS`` polls, then one that waits for the open transactions) or when the schema update is slow. Writes
refused meanwhile raise the transient ``RecordSchemaNarrowError``, which their owner runs again.

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
SCHEMA_LOCK_COLLECTION = "schemaLocks"
SCHEMA_LOCK_KEY = "records-schema"
LOCK_TTL_MS = 15_000
# After this many widenings in a row that found no quiet point and were skipped (to be tried again by the poll or by the write's owner),
# the next one, the (FORCE_AFTER_SKIPS + 1)th try, is sent anyway: with a stream transaction always open the quiet point never comes, and
# the schema would stay narrow for good.
FORCE_AFTER_SKIPS = 3

REWIDEN_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_total",
    "Times this process widened the live records schema; above 0 in steady state means an older image is running",
    ["trigger"],
)
REWIDEN_CONTENDED_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_record_schema_rewiden_contended_total",
    "Widenings not sent because a stream transaction was still open after the quiet-point wait; the poll tries again",
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
    async def renew(self, ttl_ms: int) -> bool: ...
    async def release(self) -> None: ...


_ACQUIRE = """
UPSERT { _key: @key }
INSERT { _key: @key, owner: @me, leaseUntil: DATE_NOW() + @ttl }
UPDATE ((OLD.owner == @me OR OLD.leaseUntil < DATE_NOW()) ? { owner: @me, leaseUntil: DATE_NOW() + @ttl } : {})
IN @@c
RETURN NEW.owner
"""
_RENEW = """
FOR d IN @@c
  FILTER d._key == @key AND d.owner == @me AND d.leaseUntil > DATE_NOW()
  UPDATE d WITH { leaseUntil: DATE_NOW() + @ttl } IN @@c
  RETURN NEW.owner
"""
_RELEASE = """
FOR d IN @@c
  FILTER d._key == @key AND d.owner == @me
  REMOVE d IN @@c
"""


class ArangoLock:
    """A lease lock kept in one document of a small collection of the same ArangoDB (``{owner, leaseUntil}``).

    The lease is read against the server's clock (``DATE_NOW()``), so processes with different clocks agree. A concurrent
    take of the same document loses on the write conflict (or the unique key) and counts as "not acquired". An expired
    lease is taken over by the next ``acquire``. ``renew`` succeeds only while the lease is still this owner's and
    unexpired: the widening renews right before it writes, so a process that finds it lost its lease by then does not write. That is
    not a fence: a write already sent, or one that stalls after the renewal for longer than the lease, is not stopped by it.
    Any error is raised (never read as "acquired")."""

    def __init__(self, client: Any, *, collection: str = SCHEMA_LOCK_COLLECTION, key: str = SCHEMA_LOCK_KEY) -> None:  # noqa: ANN401
        self._client, self._collection, self._key = client, collection, key
        self._owner = uuid.uuid4().hex
        self._ready = False

    async def _bind(self, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
        if not self._ready:
            if not await self._client.create_collection(self._collection):  # creating an existing collection is a success
                raise RuntimeError(f"could not create the lock collection {self._collection}")
            self._ready = True
        return {"@c": self._collection, "key": self._key, "me": self._owner, **extra}

    async def acquire(self, ttl_ms: int) -> bool:
        rows = await self._client.execute_aql(_ACQUIRE, await self._bind(ttl=ttl_ms))
        return bool(rows) and rows[0] == self._owner

    async def renew(self, ttl_ms: int) -> bool:
        return bool(await self._client.execute_aql(_RENEW, await self._bind(ttl=ttl_ms)))

    async def release(self) -> None:
        await self._client.execute_aql(_RELEASE, await self._bind())


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
        self._lock_factory = lock_factory or self._arango_lock
        self._narrowed = False
        self._skips = 0
        self._aborted: OrderedDict[str, float] = OrderedDict()
        self._abort_confirmed: OrderedDict[str, float] = OrderedDict()
        self._loop_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
        self._poller: asyncio.Task[None] | None = None

    async def _arango_lock(self) -> Lock:
        return ArangoLock(self._client)

    # -- aborted transactions ---------------------------------------------------------------------------------
    def mark_aborted(self, txn_id: str) -> None:
        self._aborted[txn_id] = time.monotonic()
        while len(self._aborted) > 4096:
            self._aborted.popitem(last=False)

    def mark_abort_confirmed(self, txn_id: str) -> None:
        """The server answered that ``txn_id`` is gone. ``mark_aborted`` only refuses more writes and the commit."""
        self._abort_confirmed[txn_id] = time.monotonic()
        while len(self._abort_confirmed) > 4096:
            self._abort_confirmed.popitem(last=False)

    def is_abort_confirmed(self, txn_id: str | None) -> bool:
        return txn_id is not None and txn_id in self._abort_confirmed

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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                # each read is bounded by what is left, so a read that never answers cannot outlast the deadline
                if await asyncio.wait_for(self.live_generation(), remaining) >= self.generation:
                    self._narrowed = False
                    return True
            except Exception as error:  # includes the timeout of the read
                self._logger.warning("could not read the records schema generation: %r", error)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.1, remaining))  # never past the deadline, so no read is made after it

    # -- widening ---------------------------------------------------------------------------------------------
    async def ensure_wide(self, trigger: str, *, transaction: str | None = None) -> bool:
        """Apply this process's schema if the live generation is lower. Never inside a transaction. Returns whether it was applied.

        Not applied (False) when the schema is already wide, the lock is held elsewhere or cannot be taken or renewed, no quiet point was
        found (up to ``FORCE_AFTER_SKIPS`` times in a row), or the generation turned out wide before the write."""
        assert transaction is None, "the records schema is never widened inside a transaction"
        loop = asyncio.get_running_loop()
        local = self._loop_locks.setdefault(loop, asyncio.Lock())
        async with local:
            if await self.live_generation() >= self.generation:
                self._narrowed = False
                return False
            # Fails closed: a process that cannot take the lock, or cannot tell whether it has it, writes no schema. Without it another
            # process can install a newer schema after the last generation check and this write would narrow it. The write that was
            # refused is refused again (a transient error its owner runs again) and the poll tries again.
            try:
                lock = await self._lock_factory()
                if lock is None:
                    raise RuntimeError("no lock")
                acquired = await lock.acquire(LOCK_TTL_MS)
            except Exception as error:
                self._logger.warning("record schema widening skipped: could not take its lock: %s", error)
                return False
            if not acquired:
                # another process is fixing it
                await self.wait_wide(REWIDEN_TIMEOUT_S)
                return False
            sent = False  # whether the schema write left this process: after that the lease is never released early
            applied = False
            try:
                if await self.live_generation() >= self.generation:
                    self._narrowed = False
                    return False
                contended = not await self._wait_quiet()
                # the wait can be long: a process of a newer generation may have written its schema in the meantime
                if await self.live_generation() >= self.generation:
                    self._narrowed = False
                    return False
                if contended:
                    REWIDEN_CONTENDED_TOTAL.inc(trigger)
                    if self._skips < FORCE_AFTER_SKIPS:
                        # A schema change waits for every open stream transaction, so one sent now could outlast its 5 s timeout and the
                        # lease. Not sent: the refused write stays refused (transient) and the next try looks for a quiet point again.
                        self._skips += 1
                        self._logger.warning(
                            "record schema widening skipped: a stream transaction is still open (trigger=%s, skip %d of %d)",
                            trigger, self._skips, FORCE_AFTER_SKIPS,
                        )
                        return False
                    self._logger.warning("record schema widening is sent without a quiet point after %d skips (trigger=%s)", self._skips, trigger)
                self._skips = 0
                # Fencing: the lease is renewed (to a full term, longer than the 5 s write) only while it is still ours and unexpired.
                try:
                    holding = await lock.renew(LOCK_TTL_MS)
                except Exception as error:
                    self._logger.warning("record schema widening skipped: could not renew its lock: %s", error)
                    return False
                if not holding:
                    self._logger.warning("record schema widening skipped: the lock lease was lost")
                    return False
                # The last look before the write, after the renewal, which is the longest pause before it.
                # The lease does not fence the write itself: a collection-properties update is not transactional with the lock, so a
                # process that is paused for longer than the lease between this read and the write (or whose write the server applies after
                # its client timed out) can still put an older schema over a newer one. That window is bounded, not closed: every process
                # reads the live generation every POLL_INTERVAL_S and widens again when it is lower than its own, and a write refused
                # meanwhile raises the transient RecordSchemaNarrowError its owner runs again (see ``start`` and the arango client hook).
                try:
                    if await self.live_generation() >= self.generation:
                        self._narrowed = False
                        return False
                except Exception as error:
                    self._logger.warning("record schema widening skipped: could not read the live generation before the write: %s", error)
                    return False
                started = time.monotonic()
                sent = True
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
                    "widened the records schema to generation %d (trigger=%s, %.2fs, applied=%s)",
                    self.generation, trigger, elapsed, applied,
                )
                if applied:
                    self._narrowed = False
                return bool(applied)
            finally:
                if not sent or applied:
                    try:
                        await lock.release()
                    except Exception:
                        self._logger.debug("could not release the record schema lock", exc_info=True)
                # else: the server answered "no", or did not answer in time and may still apply the change; deleting the lock
                # document now would let another process start while this write may be in flight, so the lease runs out instead.

    async def _wait_quiet(self) -> bool:
        """True when no stream transaction is running before the 5 s are up."""
        deadline = time.monotonic() + REWIDEN_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                if not await asyncio.wait_for(self._client.list_running_transactions(), remaining):
                    return True
            except Exception:  # includes the timeout of the request
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(QUIET_POLL_S, remaining))  # never past the deadline, so no request is made after it

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
