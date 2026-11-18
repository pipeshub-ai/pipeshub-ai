"""PaidResultCache: a paid model result is computed once per (org, record, content, model, prompt, params).

The unit of storage is one JSON object in the blob store (``keys.CacheKey.path``). ``get_or_compute`` reads it,
and on a miss computes it under a single-flight lock, so retries, replays, rebalances, replica races and
re-updates of one record reuse it. Redis holds only the locks.

Everything here fails open: a storage or Redis error is a miss, and a miss costs one paid call, never the
record. Only the caller's own ``compute`` can raise.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from app.core.org_context import spawn_in_request
from app.services.paid_cache import locks as lock_limits
from app.services.paid_cache.keys import CacheKey, valid_record_id
from app.services.paid_cache.locks import (
    DELETED_TTL_MS,
    LOCK_TTL_MS,
    InProcessLocks,
    LockBackend,
    deleted_name,
    lock_name,
    refresh_name,
)
from app.services.paid_cache.run import RunScope, current_run
from app.services.paid_cache.store import PAID_RESULT_ROUTES, ListedFile
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
    async def purge_record(self, org_id: str, record_id: str) -> None: ...
    async def purge_org(self, org_id: str) -> None: ...


def _encode(key: CacheKey, value: object) -> bytes:
    return json.dumps(
        {"v": FORMAT, "kind": key.kind, "value": value}, ensure_ascii=False, separators=(",", ":"),
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

    async def purge_record(self, org_id: str, record_id: str) -> None:
        await self._inner.purge_record(org_id, record_id)

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

    @property
    def store(self) -> ResultStore:
        return self._store

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
            return _plain(await compute())
        path = key.relative_path()
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

    async def purge_record(self, org_id: str, record_id: str) -> None:
        """Removes every stored result of the record (a refresh; the record goes on). Raises on a storage failure."""
        if not valid_record_id(record_id):
            return  # no result was ever stored under such an id (see ``valid_record_id``)
        await self._store.purge_record(org_id, record_id)

    async def refresh_purge_needed(self, org_id: str, record_id: str, event_id: str) -> bool:
        """False when this refresh event already purged the record's results: a redelivery reuses what its first
        delivery stored instead of deleting it and paying again. True when it has not (or cannot be told)."""
        try:
            backend = await self._backend()
            return not await backend.is_marked(refresh_name(org_id, record_id, event_id))
        except Exception:
            return True

    async def mark_refresh_purged(self, org_id: str, record_id: str, event_id: str) -> None:
        """Remembers (24 h) that this refresh event purged. Best effort: a miss only repeats the purge."""
        try:
            backend = await self._backend()
            await backend.mark(refresh_name(org_id, record_id, event_id), DELETED_TTL_MS)
        except Exception:
            self.logger.debug("Paid-result refresh marker not written", exc_info=True)

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
        await backend.mark(deleted_name(org_id, record_id), DELETED_TTL_MS)
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
        await backend.mark(deleted_name(org_id), DELETED_TTL_MS)
        fenced = bool(getattr(backend, "shared", False))
        if not fenced:
            self.logger.warning("Paid-result delete marker for org %s reached this process only", org_id)
        await self._store.purge_org(org_id)
        return fenced

    # -- internals ------------------------------------------------------------------------------

    async def _backend(self) -> LockBackend:
        return self._locks if not callable(self._locks) else await self._locks()  # type: ignore[no-any-return,misc,return-value]

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
        #: Bytes sit at the key that cannot be used: the next store replaces them.
        unusable = False
        if not refresh:
            found, value, unusable = await self._lookup(key, path, validate)
            if found:
                self._count(key, "hit")
                await self._note(scope, key, path, stored=False)
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
                found, value, unusable = await self._lookup(key, path, validate, report=False)
                if found:
                    self._count(key, "hit")
                    await self._note(scope, key, path, stored=False)
                    return value
            held = lock if lock is not None and lock.token is not None else None
            started = time.monotonic()
            if held is not None:
                held.start_heartbeat()
            try:
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
    ) -> tuple[bool, Any, bool]:
        """(found, value, unusable): ``unusable`` when bytes are stored that this release cannot read.

        ``report`` is False for the lookups after the first of one call, which would say the same thing again.
        """
        try:
            raw = await self._store.get(key.org_id, key.record_id, path)
        except Exception as exc:
            self._storage_error(key, "read", exc)
            return False, None, False
        if raw is None:
            return False, None, False
        found, value = _decode(key, raw)
        if found and validate is not None:
            try:
                validate(value)
            except Exception as exc:
                found = False
                if report:
                    self._unreadable(key, f"the stored value no longer decodes ({type(exc).__name__})")
        elif not found and report:
            self._unreadable(key, "the stored object is not a whole result of this kind")
        if not found and report:
            self._count(key, "error")
        return found, value, not found

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
            else:
                await self._store.put(key.org_id, key.record_id, path, _encode(key, value))
            self._count(key, "stored")
        except Exception as exc:
            self._storage_error(key, "write", exc)

    async def _note(self, scope: RunScope | None, key: CacheKey, path: str, *, stored: bool) -> None:
        if scope is None:
            return
        try:
            await scope.note(self._store, path, opens=stored)
        except Exception as exc:
            self._storage_error(key, "manifest", exc)

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
    def __init__(self, backend: LockBackend, name: str, token: str | None, logger: logging.Logger) -> None:
        self._backend = backend
        self._name = name
        self.token = token
        self._logger = logger
        self._heartbeat: asyncio.Task[None] | None = None
        #: Set when a renewal found the lock gone or taken.
        self.lost = False

    def start_heartbeat(self) -> None:
        """Renews the lock's TTL about every third of it while the holder's paid call runs."""
        self._heartbeat = spawn_in_request(self._renew_loop(), name="paid-cache-lock-renewal")

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(LOCK_TTL_MS / 3000)
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

    async def purge_record(self, org_id: str, record_id: str) -> None:
        return None

    async def refresh_purge_needed(self, org_id: str, record_id: str, event_id: str) -> bool:
        return True

    async def mark_refresh_purged(self, org_id: str, record_id: str, event_id: str) -> None:
        return None

    async def delete_record(self, org_id: str, record_id: str) -> bool:
        return True

    async def purge_org(self, org_id: str) -> bool:
        return True
