"""PaidResultCache: a paid model result is computed once per (org, record, content, model, prompt, params).

The unit of storage is one JSON object in the blob store (``keys.CacheKey.path``). ``get_or_compute`` reads it,
and on a miss computes it under a single-flight lock, so retries, replays, rebalances, replica races and
re-updates of one record reuse it. Redis holds only the locks.

Everything here fails open: a storage or Redis error is a miss, and a miss costs one paid call, never the
record. Only the caller's own ``compute`` can raise.
"""

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from time import time as wall_clock
from typing import Any, Generic, TypeVar

from app.core.org_context import spawn_in_request
from app.services.paid_cache import locks as lock_limits
from app.services.paid_cache.bump import (
    BUMP_AFTER_MS,
    PROCESS_BYTE_BUDGET,
    BumpEntry,
    BumpQueue,
    _refreshed_since,
)
from app.services.paid_cache.keys import CacheKey, valid_record_id
from app.services.paid_cache.locks import (
    DELETED_TTL_MS,
    LOCK_TTL_MS,
    InProcessLocks,
    LockBackend,
    deleted_name,
    lock_name,
    manifest_lock_name,
    refresh_done_name,
    refresh_name,
)
from app.services.paid_cache.orphan import STOPPED, RecordGone, current_guard
from app.services.paid_cache.run import RunScope, current_run
from app.services.paid_cache.store import (
    PAID_RESULT_ROUTES,
    BumpOutcome,
    FinalFence,
    GuardUnsupported,
    ListedFile,
    PurgeCutoffNoMarker,
    VersionedBytes,
)
from app.telemetry.backend import METRICS_BACKEND

T = TypeVar("T")

#: A stored object's envelope version.
FORMAT = 1
#: Losers of a lock poll for the winner's object this often, backing off to the ceiling.
POLL_FIRST_S = 0.2
POLL_MAX_S = 2.0
#: Losers wait for as long as the holder's lock lives: a live holder renews it, a dead one lapses within
#: ``LOCK_TTL_MS``. This only caps a holder that renews for ever (a hung call with no timeout).
MAX_WAIT_S = 3600.0
#: How often a replica asks again for a manifest lock another replica holds.
MANIFEST_LOCK_POLL_S = 0.05
#: How long a delivery of a refresh event waits for another delivery that is purging, and how often it looks.
REFRESH_PURGE_WAIT_S = 20.0
REFRESH_PURGE_POLL_S = 0.2
#: The longest a purge is expected to take, and the hard deadline of its lease: twice that. Past it the holder stops
#: renewing and gives the lease up (it is logged), so a duplicate purges instead of waiting for a holder that never ends.
REFRESH_PURGE_EXPECTED_S = 300.0
REFRESH_PURGE_MAX_AGE_S = 2 * REFRESH_PURGE_EXPECTED_S
#: Kept for callers: the holder's lease, not this, bounds the wait.
DEFAULT_WAIT_S = MAX_WAIT_S

PAID_CACHE_TOTAL = METRICS_BACKEND.counter(
    "pipeshub_paid_cache_total",
    "Paid-result cache lookups and stores by kind and outcome",
    ["kind", "outcome"],
)


def enabled() -> bool:
    """``PAID_CACHE=off`` switches the cache off: every call computes."""
    return os.getenv("PAID_CACHE", "on").strip().lower() not in ("off", "0", "false", "no")


class RefreshPurgeInProgress(ConnectionError):
    """Another delivery of the same refresh is purging and has not finished: transient, the broker redelivers it."""


_refresh_lease: ContextVar[Any] = ContextVar("paid_cache_refresh_lease", default=None)


class RefreshLockUnavailable(ConnectionError):
    """The refresh lease cannot be taken safely (the lock backend failed, or is process-local) while writes are on.

    An ordinary, counted failure: the delivery is retried under the normal attempt limit and ends as failed, rather
    than being handed back for ever. Hand-backs are for confirmed contention or a lost lease.
    """


class PurgeCutoffUnavailable(ConnectionError):
    """The storage service has no purge-cutoff route (an earlier release) while paid-result writes are on.

    An ordinary failure, not a hand-back: the delivery is retried and ends as failed when the storage service is
    still not updated, so the wait has an end that is visible.
    """


def writes_enabled() -> bool:
    """``PAID_CACHE_WRITE=true`` lets the cache store results and manifests; off by default for the release that
    first ships it, so a record a consumer of the previous release deletes (which purges nothing) never leaves a
    stored result behind. Reads and the purge are always on."""
    return os.getenv("PAID_CACHE_WRITE", "off").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class NotCached(Generic[T]):
    """A result to return to the caller but never store (empty, truncated, reflection-failed, degraded)."""

    value: T


class ResultStore:
    """What the cache needs of storage (``NodePaidResultStore`` implements it)."""

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None: ...
    async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool: ...
    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        """Overwrites a manifest."""
    async def overwrite(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        """Overwrites one result object: a refresh run's, or one that was found unreadable."""
    async def list_files(self, org_id: str, record_id: str, prefix: str = "") -> list[ListedFile]: ...
    async def delete_files(self, org_id: str, record_id: str, paths: list[str]) -> None: ...
    async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None: ...
    async def purge_records(self, org_id: str, record_ids: Sequence[str]) -> dict[str, BaseException | None]: ...
    async def purge_cutoff(self, org_id: str, record_id: str) -> int | None: ...
    async def purge_org(self, org_id: str) -> None: ...


#: How long a record's refresh marker read stays good for the bumped objects it is compared with.
MARKER_CACHE_S = 30.0


def _origin_ms(raw: bytes) -> int | None:
    """The ``originMs`` a bump wrote into the envelope, when there is one."""
    if b'"originMs"' not in raw:
        return None
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    origin = body.get("originMs") if isinstance(body, dict) else None
    return origin if isinstance(origin, int) and not isinstance(origin, bool) else None


def _encode(key: CacheKey, value: object) -> bytes:
    # ``w``: one value per write, so two writes of the same result are not the same bytes (on S3 and MinIO the ETag is the
    # content hash, and a delete that names a version must not match a later write that happens to equal it).
    return json.dumps(
        {"v": FORMAT, "kind": key.kind, "value": value, "w": secrets.token_hex(8)},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")


def _decode(key: CacheKey, raw: bytes) -> tuple[bool, Any]:
    """(found, value); an object that is not ours or not whole is a miss."""
    try:
        body = json.loads(raw)
    except ValueError:
        return False, None
    if not isinstance(body, dict) or body.get("v") != FORMAT or body.get("kind") != key.kind or "value" not in body:
        return False, None
    return True, body["value"]


class _FencedStore:
    """The store every cache and manifest write goes through: a deleted record or org takes no more writes.

    The delete handler sets the marker first, then purges. A write that starts after the marker is refused
    (``put`` answers False, the others return); one that was already in flight when it was set is removed
    again once it lands. So a request that outlives the indexing run that sent it (the parsing service keeps
    parsing after its client gave up) cannot repopulate a purged root. A marker that cannot be read (Redis down
    or restarted) does not stop the write: Redis is never what correctness rests on. The delete handler also
    publishes a durable delayed re-purge of the record (``repurge.py``), which removes whatever landed.
    """

    def __init__(self, inner: ResultStore, is_deleted: Callable[[str, str], Awaitable[bool]], logger: logging.Logger) -> None:
        self._inner = inner
        self._is_deleted = is_deleted
        self._logger = logger

    async def _fenced(self, org_id: str, record_id: str) -> bool:
        try:
            return await self._is_deleted(org_id, record_id)
        except Exception as exc:
            self._logger.debug("Paid-result delete marker unreadable (%s); not fenced", type(exc).__name__)
            return False

    async def _written(self, org_id: str, record_id: str, path: str) -> None:
        if await self._fenced(org_id, record_id):
            await self._inner.delete_files(org_id, record_id, [path])

    async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool:
        if await self._fenced(org_id, record_id):
            return False
        wrote = await self._inner.put(org_id, record_id, path, data)
        if wrote:
            await self._written(org_id, record_id, path)
        return wrote

    async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        if await self._fenced(org_id, record_id):
            return
        await self._inner.replace(org_id, record_id, path, data)
        await self._written(org_id, record_id, path)

    async def overwrite(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
        if await self._fenced(org_id, record_id):
            return
        await self._inner.overwrite(org_id, record_id, path, data)
        await self._written(org_id, record_id, path)

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        return await self._inner.get(org_id, record_id, path)

    async def list_files(self, org_id: str, record_id: str, prefix: str = "") -> list[ListedFile]:
        return await self._inner.list_files(org_id, record_id, prefix)

    async def delete_files(self, org_id: str, record_id: str, paths: list[str]) -> None:
        await self._inner.delete_files(org_id, record_id, paths)

    async def delete_files_if_match(self, org_id: str, record_id: str, items: Mapping[str, str]) -> list[str]:
        """Deletes each path only while it is still the version named; the paths kept (changed since listed). Raises
        ``GuardUnsupported`` when the storage cannot delete by version. A store with no such operation (a test fake with no
        versions) raises it too: nothing was deleted, and the caller takes its fallback."""
        guarded = getattr(self._inner, "delete_files_if_match", None)
        if guarded is None:
            raise GuardUnsupported("this store cannot delete by version")
        return list(await guarded(org_id, record_id, items))

    async def fenced(self, org_id: str, record_id: str) -> bool:
        """The record or its org is deleted: nothing more is written for it."""
        return await self._fenced(org_id, record_id)

    # -- bump support: a store without these (a test fake, a service of an earlier release) bumps nothing --------

    @property
    def supports_bump(self) -> bool:
        return bool(getattr(self._inner, "supports_bump", False))

    def honours(self, org_id: str, capability: str) -> bool:
        """What the storage behind the org honours; a store that says nothing per org falls back to its overall flags."""
        per_org = getattr(self._inner, "honours", None)
        if per_org is not None:
            return bool(per_org(org_id, capability))
        return bool(getattr(self._inner, "supports_bump", False)) if capability == "bump" else False

    async def presign(self, org_id: str, record_id: str, files: Sequence[Any]) -> Mapping[tuple[str, str], Any]:
        return await self._inner.presign(org_id, record_id, files)  # type: ignore[attr-defined,no-any-return]

    async def get_versioned(self, org_id: str, record_id: str, path: str) -> VersionedBytes | None:
        versioned = getattr(self._inner, "get_versioned", None)
        if versioned is None:
            raw = await self._inner.get(org_id, record_id, path)
            return None if raw is None else VersionedBytes(raw, None, None)
        return await versioned(org_id, record_id, path)  # type: ignore[no-any-return]

    async def refresh_marker_ms(self, org_id: str, record_id: str) -> int | None:
        return await self._inner.refresh_marker_ms(org_id, record_id)  # type: ignore[attr-defined,no-any-return]

    async def bump_to(
        self, entry: Any, data: bytes, charge: Callable[[int], None] | None = None,  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        eligible: Callable[[], bool] | None = None,
    ) -> tuple[BumpOutcome, str | None]:
        return await self._inner.bump_to(entry, data, charge, eligible)  # type: ignore[attr-defined,no-any-return]

    async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None:
        await self._inner.purge_record(org_id, record_id, before_ms=before_ms)

    async def purge_records(self, org_id: str, record_ids: Sequence[str]) -> dict[str, BaseException | None]:
        return await self._inner.purge_records(org_id, record_ids)

    async def purge_cutoff(self, org_id: str, record_id: str, *, require_marker: bool = False) -> int | None:
        if require_marker and getattr(self._inner, "supports_cutoff_marker", False):
            return await self._inner.purge_cutoff(org_id, record_id, require_marker=True)  # type: ignore[call-arg,no-any-return]
        return await self._inner.purge_cutoff(org_id, record_id)

    async def purge_org(self, org_id: str) -> None:
        await self._inner.purge_org(org_id)


class PaidResultCache:
    def __init__(
        self,
        store: ResultStore,
        locks: Callable[[], Awaitable[LockBackend]] | LockBackend | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._locks = locks if locks is not None else InProcessLocks()
        self.logger = logger or logging.getLogger(__name__)
        self._store: ResultStore = _FencedStore(store, self._deleted, self.logger)
        #: Callers of one key on one event loop attach to the first one's computation. The indexing service runs
        #: more than one loop, and a future cannot be awaited from another loop, so the loop is part of the key.
        self._inflight: dict[tuple[int, str], asyncio.Future[Any]] = {}
        self._bumps: dict[int, BumpQueue] = {}
        #: (org, record) -> (read at, a refresh marker's store time that rejected an object): see ``_predates_refresh``.
        self._markers: dict[tuple[str, str], tuple[float, int]] = {}

    @property
    def store(self) -> ResultStore:
        return self._store

    @property
    def bumps(self) -> BumpQueue:
        """The queue of reuse bumps (``bump.py``) of the running event loop, made on first use."""
        # One queue (and worker) per event loop: the indexing service runs more than one, and a queue's worker and
        # counters belong to the loop that drains it. The byte and entry caps are per queue.
        loop = id(asyncio.get_running_loop())
        queue = self._bumps.get(loop)
        if queue is None:
            queue = self._bumps[loop] = BumpQueue(self._store, self.logger, budget=PROCESS_BYTE_BUDGET)  # type: ignore[arg-type]
        return queue

    def _bump_after_hit(
        self, key: CacheKey, path: str, hit: "VersionedBytes | None", scope: RunScope | None, *, noted: bool,
        started: float,
    ) -> None:
        """Queues the version refresh of a reused object that could be swept (see ``bump.py``). Never raises."""
        try:
            if hit is None or not writes_enabled() or not self._store.honours(key.org_id, "bump"):  # type: ignore[attr-defined]
                return
            if not hit.etag or hit.last_modified_ms is None:
                return
            age_ms = int(time.time() * 1000) - hit.last_modified_ms
            if noted and age_ms < BUMP_AFTER_MS:
                return
            entry = BumpEntry(key.org_id, key.record_id, path, hit.data, hit.etag, hit.last_modified_ms, scope, started)
            self.bumps.offer(entry)
        except Exception as exc:
            self.logger.debug("Paid-result bump not queued (%s)", type(exc).__name__)

    # -- public ---------------------------------------------------------------------------------

    async def get_or_compute(
        self,
        key: CacheKey,
        compute: Callable[[], Awaitable[T | NotCached[T]]],
        *,
        wait_s: float = DEFAULT_WAIT_S,
        validate: Callable[[Any], object] | None = None,
    ) -> T:
        """The stored result of ``key``, or ``compute()``'s, stored when it is a result worth keeping.

        ``compute`` returns the value, or ``NotCached(value)`` for one to hand back without storing. A value
        must be JSON-serialisable. ``None``, an empty string and an empty collection are returned to the caller
        and never stored. ``validate`` is the caller's decoder: a stored value it raises on is a miss, and the
        recomputed value replaces it. ``wait_s`` is ignored: a caller waits while the holder's lock lives.
        """
        scope = self._scope_of(key)
        if not (enabled() and PAID_RESULT_ROUTES.available):
            await self._stop_if_orphan(key, "call")
            return _plain(await compute())
        path = key.relative_path()
        if not writes_enabled():
            return await self._read_only(key, path, compute, scope, validate)
        # A refresh run does not share a normal run's in-flight work: that one may return the stored result the
        # refresh exists to replace.
        flight = (id(asyncio.get_running_loop()), key.path(), scope is not None and scope.refresh)
        while True:
            inflight = self._inflight.get(flight)
            if inflight is None:
                break
            try:
                shared = await asyncio.shield(inflight)
                # The follower's run used this result too: its manifest lists the key, so retention keeps it.
                await self._note(scope, key, path, stored=False)
                return shared  # type: ignore[no-any-return]
            except asyncio.CancelledError:
                if inflight.cancelled():
                    continue
                raise
            except Exception:
                continue
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[flight] = future
        try:
            value = await self._lead(key, path, compute, scope, validate)
        except BaseException as exc:
            self._inflight.pop(flight, None)
            if not future.done():
                future.set_exception(exc if isinstance(exc, Exception) else RuntimeError("leader cancelled"))
                future.exception()  # consumed: a leader with no followers must not log "never retrieved"
            raise
        self._inflight.pop(flight, None)
        future.set_result(value)
        return value

    async def _read_only(
        self, key: CacheKey, path: str, compute: Callable[[], Awaitable[Any]], scope: RunScope | None,
        validate: Callable[[Any], object] | None,
    ) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        """Writes are off: a stored result is reused when there is one, otherwise the call is made and nothing is stored
        (no object, no manifest, no lock), exactly as before the cache existed."""
        if not (scope is not None and scope.refresh):
            found, value, _ = await self._lookup(key, path, validate)
            if found:
                self._count(key, "hit")
                return value
        self._count(key, "miss")
        await self._stop_if_orphan(key, "call")
        return _plain(await compute())

    async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None:
        """Removes every stored result of the record (a refresh; the record goes on). Raises on a storage failure.

        ``before_ms``: only objects last written before that instant go (a purge that may land late must not remove
        what a newer one's run stored since).
        """
        if not valid_record_id(record_id):
            return  # no result was ever stored under such an id (see ``valid_record_id``)
        await self._store.purge_record(org_id, record_id, before_ms=before_ms)

    async def purge_records(self, org_id: str, record_ids: Sequence[str]) -> dict[str, BaseException | None]:
        """Removes every stored result of many records in few storage requests (a re-purge page). One entry per id:
        ``None`` when done, else the error. An id no result can be stored under has nothing to remove (done)."""
        valid = [r for r in record_ids if valid_record_id(r)]
        outcomes: dict[str, BaseException | None] = dict.fromkeys(record_ids)
        if valid:
            outcomes.update(await self._store.purge_records(org_id, valid))
        return outcomes

    async def purge_cutoff(self, org_id: str, record_id: str, *, require_marker: bool = False) -> int | None:
        """The store's clock now (ms): the cutoff for one refresh purge, taken once so that every DELETE of it agrees.

        ``require_marker``: raises ``PurgeCutoffNoMarker`` when the storage service took it without writing the refresh
        marker (an earlier release)."""
        if not valid_record_id(record_id):
            return None
        if require_marker:
            return await self._store.purge_cutoff(org_id, record_id, require_marker=True)  # type: ignore[call-arg]
        return await self._store.purge_cutoff(org_id, record_id)

    async def require_purge_cutoff(self, org_id: str, record_id: str) -> int | None:
        """The cutoff of a refresh purge. ``None`` only while paid-result writes are off (a purge without a cutoff
        removes what a delivery that took over has stored, which cannot exist then); with writes on, a storage
        service that cannot name one raises ``PurgeCutoffUnavailable``."""
        writes = writes_enabled()
        try:
            cutoff = await self.purge_cutoff(org_id, record_id, require_marker=writes)
        except PurgeCutoffNoMarker as exc:
            # With writes on, a reuse may bump an object past this purge unless the refresh marker is there: the refresh
            # fails and is retried until the storage service (every replica) is updated.
            raise PurgeCutoffUnavailable("the storage service takes no refresh marker with its cutoff; update it") from exc
        if cutoff is None and valid_record_id(record_id) and writes:
            raise PurgeCutoffUnavailable("the storage service cannot yet give a purge cutoff; update it")
        return cutoff

    async def purge_for_refresh(self, org_id: str, record_id: str) -> None:
        """The purge of a refresh, inside ``refresh_purge``: the cutoff is the store's clock, and the lease is checked
        again after it, so a holder whose lease had already lapsed cannot take a cutoff that falls after a successor's
        results (the successor stored nothing before this check, and what it stores later is past the cutoff)."""
        cutoff = await self.require_purge_cutoff(org_id, record_id)
        held = _refresh_lease.get()
        if held is not None and (held.lost or not await held.still_owned()):
            raise RefreshPurgeInProgress("the purge lease was lost before the delete; another delivery owns it")
        await self.purge_record(org_id, record_id, before_ms=cutoff)

    async def mark_refresh_done(self, org_id: str, record_id: str, event_id: str) -> bool:
        """Records that the refresh run under ``event_id`` has ended, for ``DELETED_TTL_MS``. True when the marker is written; False when
        it could not be. This is the second copy of a fact the record carries (``refreshDoneId``, written with the clear of the request):
        the backend may be this process's own (Redis unreachable), where no other worker sees it, which the record's copy covers."""
        try:
            backend = await self._backend()
            await backend.mark(refresh_done_name(org_id, record_id, event_id), DELETED_TTL_MS)
            return True
        except Exception:
            self.logger.debug("Paid-result refresh-done marker not written", exc_info=True)
            return False

    async def refresh_state(self, org_id: str, record_id: str, event_id: str) -> str:
        """``"done"`` when the refresh under ``event_id`` ran, ``"open"`` when the marker can be read and is not set, ``"unavailable"``
        when it could not be read (not the same as a miss: the caller must not start a refresh on it without bound). By identity only."""
        try:
            backend = await self._backend()
            return "done" if await backend.is_marked(refresh_done_name(org_id, record_id, event_id)) else "open"
        except Exception:
            self.logger.debug("Paid-result refresh-done marker not read", exc_info=True)
            return "unavailable"

    async def claim_refresh(self, org_id: str, record_id: str, event_id: str) -> str | None:
        """Claims the refresh named ``event_id`` for its first request, atomically (``SET NX`` with a TTL on Redis).

        The claim token for the one request that wins; None for every other (a redelivery of the event, or the parsing
        client's own retry), which reuses what the winner paid for. Without a lock backend that can be asked, the
        request is treated as the first: a refresh is never dropped for want of a marker.
        """
        try:
            backend = await self._backend()
            token = await backend.acquire(refresh_name(org_id, record_id, event_id), DELETED_TTL_MS)
        except Exception:
            self.logger.debug("Paid-result refresh claim unavailable", exc_info=True)
            return "unclaimed"
        return token

    @contextlib.asynccontextmanager
    async def refresh_purge(self, org_id: str, record_id: str, event_id: str) -> AsyncIterator[bool]:
        """Yields True when this delivery of a refresh event must purge the record's results, False when it may reuse.

        False only once the 24 h marker is written, i.e. after a purge has returned. A short lease (renewed while the
        purge runs) keeps two deliveries from purging at once; a delivery that finds it held with no marker waits for
        its holder (bounded by ``REFRESH_PURGE_WAIT_S``): the holder marks, and this delivery reuses; or the holder's
        lease lapses (a dead holder), and this delivery purges. Past the wait it raises ``RefreshPurgeInProgress``,
        which the broker redelivers later, never reuse before a purge. Without a lock backend the delivery purges.
        """
        held: _Held | None = None
        backend: LockBackend | None = None
        name = refresh_name(org_id, record_id, event_id)
        must_purge = True
        # One cleanup scope from the first acquisition on: a cancellation anywhere after the lease was taken (the
        # marker recheck, the purge, the marker write) still stops the heartbeat and releases the token.
        try:
            try:
                backend = await self._backend()
                if writes_enabled() and getattr(backend, "degraded", False):
                    # The process-local stand-in for an unreachable Redis cannot see another worker's marker or lease: a retry here could purge
                    # what the first delivery already paid for.
                    raise RefreshLockUnavailable("the paid-result lock backend is this process only (Redis unreachable); a refresh needs the shared one")
                deadline = time.monotonic() + REFRESH_PURGE_WAIT_S
                while True:
                    if await backend.is_marked(name):
                        must_purge = False
                        break
                    token = await backend.acquire(name + ":lease", LOCK_TTL_MS)
                    if token is not None:
                        held = _Held(backend, name + ":lease", token, self.logger, max_age_s=REFRESH_PURGE_MAX_AGE_S)  # type: ignore[arg-type]
                        held.start_heartbeat()
                        if await backend.is_marked(name):      # a holder marked between the check and the acquire
                            must_purge = False
                            await _uninterrupted(_stop_and_release(held))
                            held = None
                        break
                    if time.monotonic() > deadline:
                        raise RefreshPurgeInProgress(f"another delivery of refresh {event_id} is purging {record_id}")
                    await asyncio.sleep(REFRESH_PURGE_POLL_S)
            except (RefreshPurgeInProgress, RefreshLockUnavailable):
                raise
            except Exception as exc:
                if writes_enabled():
                    # Writes on: a purge without the lease could delete what a delivery that took over has stored, and
                    # nothing here tells. A counted failure: retried, then terminal, never an endless hand-back.
                    raise RefreshLockUnavailable(f"the refresh lease of {record_id} cannot be taken: {exc!r}") from exc
                self.logger.debug("Paid-result refresh claim unavailable", exc_info=True)
                must_purge = True
            lease_token = _refresh_lease.set(held if must_purge else None)
            try:
                yield must_purge
                if must_purge and held is not None and backend is not None:
                    lost = held.lost
                    if not lost:
                        # Ownership and the marker in one atomic step: a lease that lapses between a check and a write
                        # could let a third delivery start writing while the successor's purge is still to come.
                        mark_if_owned = getattr(backend, "mark_if_owned", None)
                        if mark_if_owned is not None and held.token is not None:
                            lost = not await mark_if_owned(name, DELETED_TTL_MS, name + ":lease", held.token)
                        else:
                            lost = not await held.still_owned()
                            if not lost:
                                await backend.mark(name, DELETED_TTL_MS)     # only after the purge returned
                    if lost:
                        # The lease was given up (past its deadline) or taken while the purge ran: another delivery has
                        # purged or is purging, so this one writes no marker and its caller opens no run.
                        raise RefreshPurgeInProgress(f"the purge lease of refresh {event_id} was lost; another delivery owns it")
            finally:
                _refresh_lease.reset(lease_token)
        finally:
            if held is not None:
                await _uninterrupted(_stop_and_release(held))

    async def delete_record(self, org_id: str, record_id: str) -> bool:
        """The record is deleted: no writer may store under it again, and what is stored goes.

        Returns True when the delete marker went to the shared backend (every replica refuses late writes),
        False when it reached this process only (the shared backend could not be built): the caller then relies
        on the delayed re-purge alone and nothing here claims the fleet is fenced. Raises on a storage or marker
        failure (the caller's event is redelivered).
        """
        if not valid_record_id(record_id):
            return True
        backend = await self._backend()
        await backend.mark(deleted_name(org_id, record_id), DELETED_TTL_MS, str(int(wall_clock() * 1000)))
        fenced = bool(getattr(backend, "shared", False))
        if not fenced:
            self.logger.warning(
                "Paid-result delete marker for record %s reached this process only; "
                "a late write by a parse still running for it is not fenced fleet-wide", record_id,
            )
        await self._store.purge_record(org_id, record_id)
        return fenced

    async def purge_org(self, org_id: str) -> bool:
        """The org is deleted: its folder goes, and no writer may store under it again. Raises on a failure.

        True when the marker went to the shared backend (see ``delete_record``).
        """
        backend = await self._backend()
        await backend.mark(deleted_name(org_id), DELETED_TTL_MS, str(int(wall_clock() * 1000)))
        fenced = bool(getattr(backend, "shared", False))
        if not fenced:
            self.logger.warning("Paid-result delete marker for org %s reached this process only", org_id)
        await self._store.purge_org(org_id)
        return fenced

    # -- internals ------------------------------------------------------------------------------

    async def _backend(self) -> LockBackend:
        return self._locks if not callable(self._locks) else await self._locks()  # type: ignore[no-any-return,misc,return-value]

    async def _backend_or_none(self) -> LockBackend | None:
        """The lock backend for an existence check: a backend that cannot be built is advisory (None), so the guard still
        confirms with the graph and a live record's call is never failed by the check."""
        try:
            return await self._backend()
        except Exception as exc:
            self.logger.debug("Paid-result lock backend unavailable (%s); the existence check goes on", type(exc).__name__)
            return None

    async def _stop_if_orphan(self, key: CacheKey, site: str) -> None:
        """Before a paid call and before a store: a record deleted while its run was in flight stops paying (see
        ``orphan``). No guard (a call outside a delivery or a parse request) means no check."""
        guard = current_guard(key.org_id, key.record_id)
        if guard is not None and await guard.check(await self._backend_or_none()):
            STOPPED.inc(site)
            raise RecordGone(key.record_id, f"its {key.kind} is not {'paid for' if site == 'call' else 'stored'}")

    async def read_markers(self, org_id: str, record_id: str) -> tuple[str | None, str | None] | None:
        """The record's and the org's delete markers as two single-key reads (never a multi-key command: they hash to
        different slots). None when Redis cannot be read."""
        try:
            backend = await self._backend()
            return (
                await backend.marker_value(deleted_name(org_id, record_id)),
                await backend.marker_value(deleted_name(org_id)),
            )
        except Exception as exc:
            self.logger.debug("Paid-result delete markers unreadable (%s)", type(exc).__name__)
            return None

    async def ensure_alive(self, org_id: str, record_id: str, site: str) -> None:
        """The check of a stage boundary outside the cache (before the embeddings, before the vector and blob writes)."""
        guard = current_guard(org_id, record_id)
        if guard is not None and await guard.check(await self._backend_or_none()):
            STOPPED.inc(site)
            raise RecordGone(record_id)

    async def _deleted(self, org_id: str, record_id: str) -> bool:
        backend = await self._backend()
        return await backend.is_marked(deleted_name(org_id, record_id)) or await backend.is_marked(deleted_name(org_id))

    def _scope_of(self, key: CacheKey) -> RunScope | None:
        scope = current_run()
        return scope if scope is not None and scope.matches(key.org_id, key.record_id) else None

    async def _lead(
        self, key: CacheKey, path: str, compute: Callable[[], Awaitable[Any]], scope: RunScope | None,
        validate: Callable[[Any], object] | None,
    ) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        refresh = scope is not None and scope.refresh
        if refresh and scope is not None and scope.reuse_prior_attempt and path in await scope.prior_attempt_keys(self._store):
            # The retry of a refresh: a result this refresh's own earlier attempt stored (it is listed in that attempt's
            # manifest) is reused; anything else at the key is older than the refresh and is bypassed.
            refresh = False
        #: Bytes sit at the key that cannot be used: the next store replaces them.
        unusable = False
        lead_started = time.monotonic()
        hit: list[VersionedBytes] = []
        if not refresh:
            found, value, unusable = await self._lookup(key, path, validate, hit_out=hit)
            if found:
                self._count(key, "hit")
                noted = await self._note(scope, key, path, stored=False)
                self._bump_after_hit(key, path, hit[0] if hit else None, scope, noted=noted, started=lead_started)
                return value
        self._count(key, "miss")
        lock = await self._acquire(key)
        if lock is not None and lock.token is None:
            # Another process is computing it: wait for its object. A waiter that finds the lock lapsed takes it
            # (it is returned held, never released and re-asked for), so the one that computes is always the one
            # holding the lock. ``None`` is the counted fallback when no lock can be had at all (Redis unreachable).
            # A refresh run bypasses the stored result, so what is stored while it waits is accepted only if it
            # is not the object this run is meant to replace: the holder's replacement.
            stale: tuple[bool, Any] = (False, None)
            if refresh:
                before, old, _ = await self._lookup(key, path, validate, report=False)
                stale = (before, old)
            found, value, lock = await self._wait_for(key, path, validate, scope, stale=stale)
            if found:
                return value
        try:
            if lock is not None and lock.token is not None and not refresh:
                found, value, unusable = await self._lookup(key, path, validate, report=False, hit_out=hit)
                if found:
                    self._count(key, "hit")
                    noted = await self._note(scope, key, path, stored=False)
                    self._bump_after_hit(key, path, hit[0] if hit else None, scope, noted=noted, started=lead_started)
                    return value
            held = lock if lock is not None and lock.token is not None else None
            started = time.monotonic()
            if held is not None:
                held.start_heartbeat()
            try:
                await self._stop_if_orphan(key, "call")
                result = await compute()
            except BaseException:
                if held is not None:
                    await _uninterrupted(held.stop_heartbeat())
                raise
            value, keep = (result.value, False) if isinstance(result, NotCached) else (result, not _is_empty(result))
            # The call has been paid for. Stopping the timer, the ownership check and the store run to the end
            # even if this caller is cancelled meanwhile (a sibling's outage cancelling its batch): a paid result
            # is kept, so the retry does not pay for it again. The cancellation is let through afterwards.
            await _uninterrupted(self._settle(key, path, value, held, scope, started, keep=keep, overwrite=unusable or refresh))
            return value
        finally:
            if lock is not None:
                await _uninterrupted(lock.release())

    async def _settle(
        self, key: CacheKey, path: str, value: Any, held: "_Held | None", scope: RunScope | None,  # noqa: ANN401
        started: float, *, keep: bool, overwrite: bool,
    ) -> None:
        """After a paid call: store the result if it is still worth and safe to, then stop the lock's timer.

        The timer keeps renewing the lease through the ownership check and the write (up to ``WRITE_DEADLINE_S``),
        so the lease cannot lapse mid-write and let another replica pay for the key again.
        """
        try:
            await self._store_if_valid(key, path, value, held, scope, started, keep=keep, overwrite=overwrite)
        finally:
            if held is not None:
                await held.stop_heartbeat()

    async def _store_if_valid(
        self, key: CacheKey, path: str, value: Any, held: "_Held | None", scope: RunScope | None,  # noqa: ANN401
        started: float, *, keep: bool, overwrite: bool,
    ) -> None:
        await self._stop_if_orphan(key, "store")
        if not keep:
            self._count(key, "not_cached")
        elif held is not None and not await held.still_owned():
            # The lease was lost while the call ran: another payer may hold the key now, so this result is
            # handed back to the caller (counted once) and not stored.
            self._count(key, "lock_lost")
            self.logger.warning("Paid-result lock for %s was lost during the call; the result is not stored", key.kind)
        elif (scope.past_deadline() if scope is not None else False) or (
            time.monotonic() - started > lock_limits.stage_deadline_s()
        ):
            # Past the stage's deadline (measured from the stage's start; from the call's start when there is no
            # run): no longer inside what the delayed re-purge covers.
            self._count(key, "deadline_exceeded")
            self.logger.warning(
                "Paid-result for %s took longer than the stage deadline (%s s); the result is not stored",
                key.kind, lock_limits.stage_deadline_s(),
            )
        else:
            try:
                async with asyncio.timeout(lock_limits.WRITE_DEADLINE_S):
                    await self._store_result(key, path, value, scope, overwrite=overwrite)
            except TimeoutError:
                self._count(key, "deadline_exceeded")
                self.logger.warning(
                    "Paid-result write for %s did not finish within %s s; abandoned", key.kind, lock_limits.WRITE_DEADLINE_S,
                )

    async def _lookup(
        self, key: CacheKey, path: str, validate: Callable[[Any], object] | None = None, *, report: bool = True,
        hit_out: "list[VersionedBytes] | None" = None,
    ) -> tuple[bool, Any, bool]:
        """(found, value, unusable): ``unusable`` when bytes are stored that this release cannot read.

        ``report`` is False for the lookups after the first of one call, which would say the same thing again.
        """
        got: VersionedBytes | None = None
        if hit_out is not None:
            hit_out.clear()          # what an earlier lookup of this call saw is not what a bump may be built on
        try:
            if hit_out is not None:
                # The version and write time travel with the bytes (what a bump of this hit needs). Asked whatever the
                # storage is known to honour: the read that discovers bump support must carry them too.
                got = await self._store.get_versioned(key.org_id, key.record_id, path)  # type: ignore[attr-defined]
                raw = None if got is None else got.data
            else:
                raw = await self._store.get(key.org_id, key.record_id, path)
        except Exception as exc:
            self._storage_error(key, "read", exc)
            return False, None, False
        if raw is None:
            return False, None, False
        found, value = _decode(key, raw)
        if found:
            predates = await self._predates_refresh(key, raw)
            if predates is None:
                # The marker could not be read: the result is neither reused nor replaced (an unknown marker proves
                # nothing about it), so the call is made and the stored object is left as it is.
                self._count(key, "error")
                return False, None, False
            if predates:
                # A bumped result whose original write is not after the latest refresh survived a purge it should not
                # have (a stop between the bump and its check): replaced like an unreadable one, never reused.
                self._count(key, "predates_refresh")
                return False, None, True
        if found and validate is not None:
            try:
                validate(value)
            except Exception as exc:
                found = False
                if report:
                    self._unreadable(key, f"the stored value no longer decodes ({type(exc).__name__})")
        elif not found and report:
            self._unreadable(key, "the stored object is not a whole result of this kind")
        if found and got is not None and hit_out is not None:
            hit_out.append(got)          # only a version the caller accepted can be bumped or restored
        if not found and report:
            self._count(key, "error")
        return found, value, not found

    async def _predates_refresh(self, key: CacheKey, raw: bytes) -> bool | None:
        """Only a bumped object carries ``originMs`` (``bump.py``); for one, the record's refresh marker is read (kept for
        ``MARKER_CACHE_S``). True: the marker proves the object predates a refresh. None: the marker could not be read, so the
        object is neither reused nor replaced."""
        origin = _origin_ms(raw)
        if origin is None:
            return False
        read = getattr(self._store, "refresh_marker_ms", None)
        if read is None:
            return None
        name = (key.org_id, key.record_id)
        now = time.monotonic()
        cached = self._markers.get(name)
        # A marker only moves later: one read that already rejects an object rejects it (and every older one) for good. A
        # read that did not reject is never reused, since a refresh may have begun since.
        if cached is not None and now - cached[0] < MARKER_CACHE_S and _refreshed_since(cached[1], origin):
            return True
        try:
            marker = await read(key.org_id, key.record_id)
        except Exception as exc:
            self.logger.debug("Paid-result refresh marker unreadable (%s)", type(exc).__name__)
            return None
        rejects = _refreshed_since(marker, origin)
        # Only a fence that is final (the cutoff) is kept: a provisional marker can still move to a cutoff that accepts this
        # object, and a rejection cached from it would hold for the whole window.
        if rejects and isinstance(marker, FinalFence):
            if len(self._markers) > 4096:
                self._markers.clear()
            self._markers[name] = (now, marker)
        return rejects

    def _unreadable(self, key: CacheKey, why: str) -> None:
        # Logged once per key: the compute that follows replaces the object, so the next lookup hits.
        self.logger.warning("Paid-result cache: %s for %s; computing it again and replacing it", why, key.kind)

    async def _store_result(
        self, key: CacheKey, path: str, value: Any, scope: RunScope | None, *, overwrite: bool = False,  # noqa: ANN401
    ) -> None:
        try:
            await self._note(scope, key, path, stored=True)
            if overwrite:
                # This key only: a refresh run's result, or what replaces bytes that cannot be read.
                await self._store.overwrite(key.org_id, key.record_id, path, _encode(key, value))
                if scope is not None:
                    scope.confirm(path)          # a write of this refresh's own: a retry may reuse it
            else:
                await self._store.put(key.org_id, key.record_id, path, _encode(key, value))
            self._count(key, "stored")
        except Exception as exc:
            self._storage_error(key, "write", exc)

    @contextlib.asynccontextmanager
    async def manifest_lock(self, org_id: str, record_id: str, path: str) -> AsyncIterator[None]:
        """Holds the cross-replica lock of one manifest path for a read-merge-replace.

        The lease is renewed until the section ends, as a result's lock is renewed through its write. A lock that
        another replica holds is a live holder: this call waits for it (bounded by ``WRITE_DEADLINE_S``) and raises
        ``TimeoutError`` rather than write beside it. Only when no lock backend can be asked at all does the write go
        ahead unserialized: a manifest is best effort, never a reason to fail.
        """
        name = manifest_lock_name(org_id, record_id, path)
        held: _Held | None = None
        try:
            backend = await self._backend()
            deadline = time.monotonic() + lock_limits.WRITE_DEADLINE_S
            token = await backend.acquire(name, LOCK_TTL_MS)
            while token is None:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"the manifest lock of {path} is held by another writer")
                await asyncio.sleep(MANIFEST_LOCK_POLL_S)
                token = await backend.acquire(name, LOCK_TTL_MS)
            held = _Held(backend, name, token, self.logger)  # type: ignore[arg-type]
            held.start_heartbeat()
        except TimeoutError:
            raise
        except Exception:
            self.logger.debug("Paid-result manifest lock unavailable", exc_info=True)
        try:
            yield
        finally:
            if held is not None:
                await _uninterrupted(_stop_and_release(held))

    async def _note(self, scope: RunScope | None, key: CacheKey, path: str, *, stored: bool) -> bool:
        """Notes the key in the run's manifest. False when the note could not be written (the manifest does not
        protect the key then, so a reuse bumps it whatever its age)."""
        if scope is None or not writes_enabled():
            return True
        scope.manifest_guard = self.manifest_lock
        try:
            await scope.note(self._store, path, opens=stored)
        except Exception as exc:
            self._storage_error(key, "manifest", exc)
            return False
        return not scope.past_deadline()

    async def _acquire(self, key: CacheKey) -> "_Held | None":
        """The lock on the key: held (token set), contended (token None), or None when no lock can be had."""
        name = lock_name(key.org_id, key.record_id, key.digest())
        try:
            backend = await self._backend()
            token = await backend.acquire(name, LOCK_TTL_MS)
        except Exception as exc:
            self._storage_error(key, "lock", exc)
            return None
        return _Held(backend, name, token, self.logger)  # type: ignore[arg-type]

    async def _wait_for(
        self, key: CacheKey, path: str, validate: Callable[[Any], object] | None, scope: RunScope | None,
        stale: tuple[bool, Any] = (False, None),
    ) -> "tuple[bool, Any, _Held | None]":
        """Poll for the holder's object while its lock lives.

        Returns ``(True, value, None)`` when the object appeared. When the lock lapses (a dead holder) this
        caller takes it and returns ``(False, None, held)``: the caller computes under that very lock, after
        looking the key up again. ``(False, None, None)`` when the lock cannot be asked at all (the fallback).
        """
        deadline = time.monotonic() + MAX_WAIT_S
        delay = POLL_FIRST_S
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            found, value, _ = await self._lookup(key, path, validate, report=False)
            if found and not (stale[0] and value == stale[1]):
                self._count(key, "hit")
                await self._note(scope, key, path, stored=False)
                return True, value, None
            retaken = await self._acquire(key)
            if retaken is None or retaken.token is not None:
                return False, None, retaken
            delay = min(delay * 2, POLL_MAX_S)
        # A holder that renews for ever: the caller computes without the lock, counted.
        self._count(key, "wait_exhausted")
        return False, None, None

    def _count(self, key: CacheKey, outcome: str) -> None:
        PAID_CACHE_TOTAL.inc(key.kind, outcome)

    def _storage_error(self, key: CacheKey, op: str, exc: BaseException) -> None:
        self._count(key, "error")
        # The class only: an error text may carry a signed URL.
        self.logger.warning("Paid-result cache %s failed for %s (%s); continuing without it", op, key.kind, type(exc).__name__)


class _Held:
    def __init__(
        self, backend: LockBackend, name: str, token: str | None, logger: logging.Logger, *, max_age_s: float | None = None,
    ) -> None:
        self._backend = backend
        self._name = name
        self.token = token
        self._logger = logger
        self._heartbeat: asyncio.Task[None] | None = None
        #: The lease is renewed for at most this long in total; None: for as long as its holder runs.
        self._max_age_s = max_age_s
        self._since = time.monotonic()
        #: Set when a renewal found the lock gone or taken.
        self.lost = False

    def start_heartbeat(self) -> None:
        """Renews the lock's TTL about every third of it while the holder's paid call runs."""
        self._heartbeat = spawn_in_request(self._renew_loop(), name="paid-cache-lock-renewal")

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(LOCK_TTL_MS / 3000)
            if self._max_age_s is not None and time.monotonic() - self._since > self._max_age_s:
                # A holder past its hard deadline stops renewing and gives the lease up, so a duplicate can take over.
                self._logger.warning(
                    "The lease %s was held for more than %.0f s; it is no longer renewed and has been given up", self._name,
                    self._max_age_s,
                )
                self.lost = True
                with contextlib.suppress(Exception):
                    if self.token is not None:
                        await self._backend.release(self._name, self.token)
                return
            try:
                if self.token is not None and not await self._backend.renew(self._name, self.token, LOCK_TTL_MS):
                    self.lost = True
                    return
            except Exception:
                self._logger.debug("Paid-result lock renewal failed", exc_info=True)

    async def stop_heartbeat(self) -> None:
        """Ends the timer and waits for it: no task outlives the call, whatever ended it."""
        task, self._heartbeat = self._heartbeat, None
        if task is not None:
            task.cancel()
            await asyncio.wait([task])

    async def still_owned(self) -> bool:
        """One atomic compare on the token. A lock that cannot be asked is treated as lost: nothing is stored."""
        if self.lost or self.token is None:
            return False
        try:
            return await self._backend.owns(self._name, self.token)
        except Exception:
            self._logger.debug("Paid-result lock ownership check failed", exc_info=True)
            return False

    async def release(self) -> None:
        if self.token is None:
            return
        try:
            await self._backend.release(self._name, self.token)
        except Exception:
            self._logger.debug("Paid-result lock release failed", exc_info=True)


async def _stop_and_release(held: "_Held") -> None:
    """Stops the heartbeat and gives the token back as one piece of work, so a cancellation between them cannot skip the release."""
    await held.stop_heartbeat()
    await held.release()


async def _uninterrupted(work: Awaitable[Any]) -> Any:  # noqa: ANN401 - whatever the work returns
    """Runs ``work`` to its end even if the caller is cancelled meanwhile, then lets the cancellation through."""
    task = spawn_in_request(_await(work), name="paid-cache-settle")
    cancelled: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancelled = exc
    if cancelled is not None:
        raise cancelled
    return result


async def _await(work: Awaitable[Any]) -> Any:  # noqa: ANN401
    return await work


def _is_empty(value: Any) -> bool:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    """A result with nothing in it is never worth keeping, whatever a site decided."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict, tuple)) and not value


def _plain(result: Any) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
    return result.value if isinstance(result, NotCached) else result


class NullPaidResultCache:
    """No cache: every call computes. For tests, and for a process with no storage to keep results in."""

    async def get_or_compute(self, key: CacheKey, compute: Callable[[], Awaitable[Any]], *, wait_s: float = 0, validate: Callable[[Any], object] | None = None) -> Any:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        return _plain(await compute())

    async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None:
        return None

    async def purge_records(self, org_id: str, record_ids: Sequence[str]) -> dict[str, BaseException | None]:
        return dict.fromkeys(record_ids)

    async def read_markers(self, org_id: str, record_id: str) -> tuple[str | None, str | None] | None:
        return (None, None)

    async def ensure_alive(self, org_id: str, record_id: str, site: str) -> None:
        """No markers without a cache, but the graph still decides for a run that has a guard."""
        guard = current_guard(org_id, record_id)
        if guard is not None and await guard.check(None):
            STOPPED.inc(site)
            raise RecordGone(record_id)

    async def purge_cutoff(self, org_id: str, record_id: str, *, require_marker: bool = False) -> int | None:
        return None

    async def require_purge_cutoff(self, org_id: str, record_id: str) -> int | None:
        return None

    async def purge_for_refresh(self, org_id: str, record_id: str) -> None:
        return None

    async def claim_refresh(self, org_id: str, record_id: str, event_id: str) -> str | None:
        return "unclaimed"

    async def mark_refresh_done(self, org_id: str, record_id: str, event_id: str) -> bool:
        return True                  # no marker store to keep a record in: nothing is lost by clearing

    async def refresh_state(self, org_id: str, record_id: str, event_id: str) -> str:
        return "open"

    @contextlib.asynccontextmanager
    async def refresh_purge(self, org_id: str, record_id: str, event_id: str) -> AsyncIterator[bool]:
        yield True

    async def delete_record(self, org_id: str, record_id: str) -> bool:
        return True

    async def purge_org(self, org_id: str) -> bool:
        return True
