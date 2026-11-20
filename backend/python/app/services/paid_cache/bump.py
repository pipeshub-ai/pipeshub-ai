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
#: What the bump adds to an envelope (``touchedMs``, ``originMs``), counted against the byte budget.
TOUCH_OVERHEAD = 96
#: Records handled at once. A batch writes for every record, waits ``POST_CHECK_DELAY_S`` once, then checks every
#: record's marker: a cycle is about 2.5 s plus the uploads, so one process sustains roughly ``QUEUE_MAX`` entries per
#: cycle (about 100 per second) however many records they span.
RECORD_CONCURRENCY = 8
#: Lost protection (a full queue, a stale entry, the byte budget) is logged at most this often, with the counts since.
LOSS_LOG_EVERY_S = 60.0

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


def touched_bytes(data: bytes, now_ms: int, origin_ms: int | None = None) -> bytes | None:
    """The same envelope with ``touchedMs`` set to a value no earlier than any previous one (so the bytes, hence the
    ETag, always change); None when the object is not a JSON object. ``originMs`` records when the object was first
    written by the store (the earliest value wins): a reader that finds a refresh began at or after it knows the result
    predates that refresh, whatever became of the check that follows the write (``cache.py``)."""
    try:
        body = json.loads(data)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    previous = body.get("touchedMs")
    floor = previous + 1 if isinstance(previous, int) and not isinstance(previous, bool) else 0
    body["touchedMs"] = max(now_ms, floor)
    if origin_ms is not None:
        earlier = body.get("originMs")
        body["originMs"] = min(origin_ms, earlier) if isinstance(earlier, int) and not isinstance(earlier, bool) else origin_ms
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
        self._lost: dict[str, int] = {}
        self._last_loss_log = 0.0
        self._last_write = 0.0
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
        if len(self._queue) >= QUEUE_MAX or self._queued_bytes + len(entry.data) + TOUCH_OVERHEAD > QUEUE_BYTES_MAX:
            return self._lose("dropped")
        if self._uploaded_last_hour(now) + self._queued_bytes + len(entry.data) + TOUCH_OVERHEAD > BYTES_PER_HOUR:
            return self._lose("over_budget")
        self._recent[key] = now
        self._recent.move_to_end(key)
        while len(self._recent) > 4096:
            self._recent.popitem(last=False)
        self._queue.append(entry)
        self._queued_bytes += len(entry.data) + TOUCH_OVERHEAD
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

    def _lose(self, outcome: str, n: int = 1) -> str:
        """Protection that was not applied because the queue fell behind: counted, and logged (at most once a minute,
        with the totals since): a reuse left unprotected falls back to the old race, a re-pay and never wrong data."""
        for _ in range(n):
            BUMP_TOTAL.inc(outcome)
        self._lost[outcome] = self._lost.get(outcome, 0) + n
        now = time.monotonic()
        if now - self._last_loss_log >= LOSS_LOG_EVERY_S:
            self._last_loss_log = now
            self._log.warning(
                "Paid-result bump queue is behind: reuses left unprotected from the retention sweep since the last "
                "report: %s",
                ", ".join(f"{k}={v}" for k, v in sorted(self._lost.items())),
            )
            self._lost = {}
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
                self._queued_bytes -= len(entry.data) + TOUCH_OVERHEAD
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
        stale = 0
        for entry in batch:
            if time.monotonic() - entry.enqueued > ENTRY_MAX_AGE_S:
                stale += 1
            else:
                groups.setdefault((entry.org_id, entry.record_id), []).append(entry)
        if stale:
            self._lose("stale", stale)
        gate = asyncio.Semaphore(RECORD_CONCURRENCY)

        async def write(org_id: str, record_id: str, entries: list[BumpEntry]) -> list[tuple[BumpEntry, bytes]]:
            async with gate:
                try:
                    return await self._write_record(org_id, record_id, entries)
                except Exception as exc:
                    self._log.warning("Paid-result bump of a record failed (%s); dropped", type(exc).__name__)
                    return []

        results = await asyncio.gather(*(write(o, r, es) for (o, r), es in groups.items()))
        written = {key: w for key, w in zip(groups, results, strict=True) if w}
        if not written:
            return
        # One wait for the whole batch (the last write of any record sets it), then every record is checked.
        await self._sleep(max(0.0, POST_CHECK_DELAY_S - (time.monotonic() - self._last_write)))

        async def check(org_id: str, record_id: str, items: list[tuple[BumpEntry, bytes]]) -> None:
            async with gate:
                try:
                    await self._post_check(org_id, record_id, items)
                except Exception as exc:
                    self._log.warning("Paid-result bump check of a record failed (%s)", type(exc).__name__)

        await asyncio.gather(*(check(o, r, items) for (o, r), items in written.items()))

    def _honours(self, org_id: str, capability: str) -> bool:
        per_org = getattr(self._store, "honours", None)
        if per_org is not None:
            return bool(per_org(org_id, capability))
        return bool(getattr(self._store, "supports_bump" if capability == "bump" else "supports_conditional_delete", False))

    async def _marker(self, org_id: str, record_id: str) -> tuple[bool, int | None]:
        try:
            return True, await self._store.refresh_marker_ms(org_id, record_id)
        except Exception as exc:
            self._log.debug("Paid-result refresh marker unreadable (%s)", type(exc).__name__)
            return False, None

    async def _write_record(self, org_id: str, record_id: str, entries: list[BumpEntry]) -> list[tuple[BumpEntry, bytes]]:
        if not self._honours(org_id, "bump"):
            for _ in entries:
                self._count("unsupported")
            return []
        if await self._store.fenced(org_id, record_id):
            for _ in entries:
                self._count("deleted_record")
            return []
        readable, marker = await self._marker(org_id, record_id)
        if not readable:
            # Absence is never assumed: with no marker answer nothing is written.
            for _ in entries:
                self._count("marker_unreadable")
            return []
        live: list[BumpEntry] = []
        for entry in entries:
            if entry.past_deadline():
                self._count("deadline")
            elif _refreshed_since(marker, entry.origin_ms):
                self._count("refreshed")
            else:
                live.append(entry)
        if not live:
            return []
        try:
            signed = await self._store.presign(org_id, record_id, [(e.path, "bump", e.etag) for e in live])
        except Exception as exc:
            # An earlier-release replica rejects the mode (400): this batch goes unbumped, nothing is latched.
            self._log.debug("Paid-result bump presign failed (%s)", type(exc).__name__)
            for _ in live:
                self._count("presign_failed")
            return []
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
        return written

    async def _bump_one(self, entry: BumpEntry, signed: Any, marker: int | None) -> bytes | None:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        """The bytes now stored at the key through this bump (to undo exactly them), or None when nothing was written."""
        new = touched_bytes(entry.data, int(self._clock() * 1000), entry.origin_ms)
        if new is None:
            self._count("not_json")
            return None
        outcome, _ = await self._store.bump_to(signed, new)
        if outcome == BUMPED:
            self._wrote(len(new))
            self._count("bumped")
            return new
        if outcome in (CHANGED, CONFLICT):
            # A store may answer 412 (or an exhausted 409) to a conditional write of a key that is gone: one read decides.
            # A write that landed before a retried request was refused reads back as our own bytes: it counts as written
            # (and gets its check), not as a change by someone else.
            current = await self._store.get_versioned(entry.org_id, entry.record_id, entry.path)
            if current is not None:
                if current.data == new:
                    self._wrote(len(new))
                    self._count("bumped")
                    return new
                self._count("changed")
                return None
        elif outcome != GONE:
            return None
        return await self._restore(entry, marker)

    def _wrote(self, size: int) -> None:
        self._last_write = time.monotonic()
        self._uploaded.append((self._last_write, size))

    async def _restore(self, entry: BumpEntry, marker: int | None) -> bytes | None:
        """The object is gone: store the bytes the run holds, under the guards of a paid store."""
        if entry.past_deadline():
            self._count("deadline")
            return None
        if _refreshed_since(marker, entry.origin_ms):
            self._count("refreshed")
            return None
        data = touched_bytes(entry.data, int(self._clock() * 1000), entry.origin_ms) or entry.data
        stored = await self._store.put(entry.org_id, entry.record_id, entry.path, data)
        if not stored:
            self._count("restore_lost")
            return None
        self._wrote(len(data))
        self._count("restored")
        return data

    async def _post_check(self, org_id: str, record_id: str, written: list[tuple[BumpEntry, bytes]]) -> None:
        """Run no sooner than ``POST_CHECK_DELAY_S`` after the writes (the caller waits once per batch): a refresh whose
        marker landed since must not keep an object it purges. Such an object is removed again, conditionally on the
        version this bump wrote."""
        readable, marker = await self._marker(org_id, record_id)
        if not readable:
            readable, marker = await self._marker(org_id, record_id)
        fenced = await self._store.fenced(org_id, record_id)
        for entry, data in written:
            if readable and not fenced and not _refreshed_since(marker, entry.origin_ms):
                continue
            if not self._honours(org_id, "conditionalDelete"):
                # Removing the object again would be an unconditional delete that could take a refresh's newer result: it
                # is left, and the next reuse refuses it by its ``originMs`` and the marker (``cache.py``).
                self._count("undo_unavailable")
                continue
            try:
                current = await self._store.get_versioned(org_id, record_id, entry.path)
                if current is not None and current.etag and current.data == data:
                    await self._store.delete_files(org_id, record_id, [entry.path], {entry.path: current.etag})
                    self._count("undone")
            except Exception as exc:
                self._log.warning("Paid-result bump undo failed (%s)", type(exc).__name__)
                self._count("undo_failed")
