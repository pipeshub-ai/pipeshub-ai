"""A reuse refreshes the version of the stored result it read, so the retention sweep cannot delete it from under the run.

The sweep deletes an object only while it is still the version it listed (``retention.py``). A run that reuses an
object old enough to be swept (``BUMP_AFTER_MS``) therefore re-writes the bytes it read with one changed envelope
field (``touchedMs``), conditional on the ETag it read: new bytes are a new ETag on every store (a copy in place keeps
the content-hash ETag on S3 and MinIO, so it would not do). If the object is gone by then, the run stores the bytes it
holds again: no model call.

The work is off the hit's path: a hit returns at once and queues the bump on a bounded queue. Every write here is a
write of the cache: it runs only with ``PAID_CACHE_WRITE`` on, inside the stage deadline, and never carries an
object past a refresh's purge (the refresh marker the storage service writes before it takes the cutoff is read
before the write and again no sooner than ``POST_CHECK_DELAY_S`` after it; an object the refresh should have
removed is removed again, conditionally on the version written). A marker that cannot be read stops the bump.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.org_context import spawn_detached
from app.services.paid_cache import locks as lock_limits
from app.services.paid_cache.run import RunScope
from app.services.paid_cache.store import (
    BUMPED,
    CHANGED,
    CONFLICT,
    GONE,
    VersionedBytes,
)
from app.telemetry.backend import METRICS_BACKEND

#: A reused object younger than this is out of the sweep's reach for a good while (``retention.GRACE_MS`` is an hour).
#: 30 min + ``LRU_WINDOW_S`` (10 min) + a skew allowance of 10 min stays under it. Provisional: the bench sets it.
BUMP_AFTER_MS = 30 * 60 * 1000
LRU_WINDOW_S = 600.0
QUEUE_MAX = 256
QUEUE_BYTES_MAX = 64 * 1024 * 1024
#: A queued bump older than this is dropped: the refresh mark and the deadline are checked at dequeue, so a long wait
#: widens what they must cover.
ENTRY_MAX_AGE_S = 60.0
#: The post-write marker check waits this long: a store stamps whole seconds, so a write and a marker set right after it
#: can share the second the purge cutoff will carry.
POST_CHECK_DELAY_S = 2.5
#: Bytes re-uploaded are capped: one object, and a rolling hour for the process.
MAX_OBJECT_BYTES = 2 * 1024 * 1024
BYTES_PER_HOUR = 512 * 1024 * 1024
CONCURRENCY = 8

BUMP_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_paid_bump_total", "Reuse bumps of stored paid results by outcome", ["outcome"],
)


class BumpStore(Protocol):
    """The (fenced) store the queue works through."""

    supports_bump: bool

    async def fenced(self, org_id: str, record_id: str) -> bool: ...
    async def presign(self, org_id: str, record_id: str, files: Sequence[Any]) -> Mapping[tuple[str, str], Any]: ...
    async def bump_to(self, entry: Any, data: bytes) -> tuple[str, str | None]: ...  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
    async def get_versioned(self, org_id: str, record_id: str, path: str) -> VersionedBytes | None: ...
    async def refresh_marker_ms(self, org_id: str, record_id: str) -> int | None: ...
    async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool: ...
    async def delete_files(
        self, org_id: str, record_id: str, paths: list[str], etags: Mapping[str, str] | None = None,
    ) -> Any: ...  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape


@dataclass(slots=True)
class BumpEntry:
    org_id: str
    record_id: str
    path: str
    data: bytes
    etag: str
    #: The write time of the object as read (the store's clock), compared with the refresh marker's.
    origin_ms: int
    scope: RunScope | None
    started: float = field(default_factory=time.monotonic)
    enqueued: float = field(default_factory=time.monotonic)

    def past_deadline(self) -> bool:
        if self.scope is not None:
            return self.scope.past_deadline()
        return time.monotonic() - self.started > lock_limits.stage_deadline_s()


def touched_bytes(data: bytes, now_ms: int) -> bytes | None:
    """The same envelope with ``touchedMs`` set to a value no earlier than any previous one (so the bytes, hence the
    ETag, always change); None when the object is not a JSON object."""
    try:
        body = json.loads(data)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    previous = body.get("touchedMs")
    floor = previous + 1 if isinstance(previous, int) and not isinstance(previous, bool) else 0
    body["touchedMs"] = max(now_ms, floor)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _refreshed_since(marker_ms: int | None, origin_ms: int) -> bool:
    """A refresh began at or after the object was written (whole seconds on both sides: a tie counts as yes)."""
    return marker_ms is not None and marker_ms // 1000 >= origin_ms // 1000


class BumpQueue:
    def __init__(
        self, store: BumpStore, logger: logging.Logger | None = None, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], float] = time.time,
        autostart: bool = True,
    ) -> None:
        self._store = store
        self._log = logger or logging.getLogger(__name__)
        self._sleep = sleep
        self._clock = clock
        #: False in tests that drive ``drain_once`` themselves (a worker would run beside them).
        self._autostart = autostart
        self._queue: deque[BumpEntry] = deque()
        self._queued_bytes = 0
        self._recent: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._uploaded: deque[tuple[float, int]] = deque()
        self._worker: asyncio.Task[None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    # -- offering --------------------------------------------------------------------------------

    def offer(self, entry: BumpEntry) -> str:
        """Queues one bump; never blocks and never raises. Returns what became of it."""
        if len(entry.data) > MAX_OBJECT_BYTES:
            return self._count("too_large")
        now = time.monotonic()
        key = (entry.org_id, entry.record_id, entry.path)
        seen = self._recent.get(key)
        if seen is not None and now - seen < LRU_WINDOW_S:
            return self._count("recent")
        if len(self._queue) >= QUEUE_MAX or self._queued_bytes + len(entry.data) > QUEUE_BYTES_MAX:
            return self._count("dropped")
        if self._uploaded_last_hour(now) + self._queued_bytes + len(entry.data) > BYTES_PER_HOUR:
            return self._count("over_budget")
        self._recent[key] = now
        self._recent.move_to_end(key)
        while len(self._recent) > 4096:
            self._recent.popitem(last=False)
        self._queue.append(entry)
        self._queued_bytes += len(entry.data)
        self._idle.clear()
        self._ensure_worker()
        return "queued"

    def _uploaded_last_hour(self, now: float) -> int:
        while self._uploaded and now - self._uploaded[0][0] > 3600:
            self._uploaded.popleft()
        return sum(n for _, n in self._uploaded)

    def _count(self, outcome: str) -> str:
        BUMP_TOTAL.inc(outcome)
        return outcome

    def _ensure_worker(self) -> None:
        if not self._autostart:
            return
        if self._worker is None or self._worker.done():
            self._worker = spawn_detached(self._run(), name="paid-cache-bump")

    async def wait_idle(self) -> None:
        """Returns once everything queued has been handled (tests, shutdown)."""
        await self._idle.wait()

    # -- worker ----------------------------------------------------------------------------------

    async def _run(self) -> None:
        while self._queue:
            batch: list[BumpEntry] = []
            while self._queue:
                entry = self._queue.popleft()
                self._queued_bytes -= len(entry.data)
                batch.append(entry)
            try:
                await self._process(batch)
            except Exception as exc:
                self._log.warning("Paid-result bump batch failed (%s); dropped", type(exc).__name__)
        self._idle.set()

    async def drain_once(self) -> None:
        """Handles what is queued now, in the caller's task (tests)."""
        batch = list(self._queue)
        self._queue.clear()
        self._queued_bytes = 0
        if batch:
            await self._process(batch)
        self._idle.set()

    async def _process(self, batch: list[BumpEntry]) -> None:
        groups: dict[tuple[str, str], list[BumpEntry]] = {}
        for entry in batch:
            if time.monotonic() - entry.enqueued > ENTRY_MAX_AGE_S:
                self._count("stale")
            else:
                groups.setdefault((entry.org_id, entry.record_id), []).append(entry)
        for (org_id, record_id), entries in groups.items():
            await self._process_record(org_id, record_id, entries)

    async def _marker(self, org_id: str, record_id: str) -> tuple[bool, int | None]:
        try:
            return True, await self._store.refresh_marker_ms(org_id, record_id)
        except Exception as exc:
            self._log.debug("Paid-result refresh marker unreadable (%s)", type(exc).__name__)
            return False, None

    async def _process_record(self, org_id: str, record_id: str, entries: list[BumpEntry]) -> None:
        if not self._store.supports_bump:
            for _ in entries:
                self._count("unsupported")
            return
        if await self._store.fenced(org_id, record_id):
            for _ in entries:
                self._count("deleted_record")
            return
        readable, marker = await self._marker(org_id, record_id)
        if not readable:
            # Absence is never assumed: with no marker answer nothing is written.
            for _ in entries:
                self._count("marker_unreadable")
            return
        live: list[BumpEntry] = []
        for entry in entries:
            if entry.past_deadline():
                self._count("deadline")
            elif _refreshed_since(marker, entry.origin_ms):
                self._count("refreshed")
            else:
                live.append(entry)
        if not live:
            return
        try:
            signed = await self._store.presign(org_id, record_id, [(e.path, "bump", e.etag) for e in live])
        except Exception as exc:
            # An earlier-release replica rejects the mode (400): this batch goes unbumped, nothing is latched.
            self._log.debug("Paid-result bump presign failed (%s)", type(exc).__name__)
            for _ in live:
                self._count("presign_failed")
            return
        gate = asyncio.Semaphore(CONCURRENCY)
        written: list[tuple[BumpEntry, bytes]] = []

        async def one(entry: BumpEntry) -> None:
            async with gate:
                try:
                    result = await self._bump_one(entry, signed[(entry.path, "bump")], marker)
                except Exception as exc:
                    self._log.debug("Paid-result bump failed (%s)", type(exc).__name__)
                    self._count("error")
                    return
                if result is not None:
                    written.append((entry, result))

        await asyncio.gather(*(one(e) for e in live))
        if written:
            await self._post_check(org_id, record_id, written)

    async def _bump_one(self, entry: BumpEntry, signed: Any, marker: int | None) -> bytes | None:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        """The bytes now stored at the key through this bump (to undo exactly them), or None when nothing was written."""
        new = touched_bytes(entry.data, int(self._clock() * 1000))
        if new is None:
            self._count("not_json")
            return None
        outcome, _ = await self._store.bump_to(signed, new)
        if outcome == BUMPED:
            self._uploaded.append((time.monotonic(), len(new)))
            self._count("bumped")
            return new
        if outcome in (CHANGED, CONFLICT):
            # A store may answer 412 (or an exhausted 409) to a conditional write of a key that is gone: one read decides.
            if await self._store.get_versioned(entry.org_id, entry.record_id, entry.path) is not None:
                self._count("changed")
                return None
        elif outcome != GONE:
            return None
        return await self._restore(entry, marker)

    async def _restore(self, entry: BumpEntry, marker: int | None) -> bytes | None:
        """The object is gone: store the bytes the run holds, under the guards of a paid store."""
        if entry.past_deadline():
            self._count("deadline")
            return None
        if _refreshed_since(marker, entry.origin_ms):
            self._count("refreshed")
            return None
        stored = await self._store.put(entry.org_id, entry.record_id, entry.path, entry.data)
        if not stored:
            self._count("restore_lost")
            return None
        self._uploaded.append((time.monotonic(), len(entry.data)))
        self._count("restored")
        return entry.data

    async def _post_check(self, org_id: str, record_id: str, written: list[tuple[BumpEntry, bytes]]) -> None:
        """No sooner than ``POST_CHECK_DELAY_S`` after the writes: a refresh whose marker landed since must not keep
        an object it purges. Such an object is removed again, conditionally on the version this bump wrote."""
        await self._sleep(POST_CHECK_DELAY_S)
        readable, marker = await self._marker(org_id, record_id)
        if not readable:
            readable, marker = await self._marker(org_id, record_id)
        fenced = await self._store.fenced(org_id, record_id)
        for entry, data in written:
            if readable and not fenced and not _refreshed_since(marker, entry.origin_ms):
                continue
            try:
                current = await self._store.get_versioned(org_id, record_id, entry.path)
                if current is not None and current.etag and current.data == data:
                    await self._store.delete_files(org_id, record_id, [entry.path], {entry.path: current.etag})
                    self._count("undone")
            except Exception as exc:
                self._log.warning("Paid-result bump undo failed (%s)", type(exc).__name__)
                self._count("undo_failed")
