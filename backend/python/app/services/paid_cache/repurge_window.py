"""Coalesced re-purge hops: deletes of one org inside one window share the chain that follows their first hop.

A deleted record's stored paid results are purged again and again for ``repurge_horizon_s()`` (about 50 hops of
``REPURGE_HOP_S``), because a parse that outlived its record may still be writing. One chain per record is about
50 delayed messages per deleted record; a bulk delete of 100k records is 5M messages.

Each record keeps its **durable first hop** (``purgePaidResults``, one message per delete, nothing depends on Redis
for it). When that hop has purged, and ``PAID_RESULT_REPURGE_COALESCE`` is on, the record's id goes into the org's
window instead of a per-record second hop. One **seal** message per window (published before the first id is
appended) pages the ids (``PAGE_SIZE`` per page) and starts one chain of **page hops** per page; a page hop carries
its ids in the message body and purges them all each hop. Every path that cannot place an id in a window (the window
is closed or full, another deleter's claim is live, the store failed) falls back to today's per-record second hop,
so no path ends a chain without a covering message.

The window protocol (one Redis hash tag per org, so every script touches one slot; PostgreSQL state mode runs the
same state machine under one advisory lock per window):

* ``own``: ``claim:<token>:<epoch>:<expiryMs>`` (a deleter is publishing the seal) or ``published:<epoch>:<dueMs>``;
* ``ids``: the window's record ids; ``sealed``: set once by the seal; ``done``: the seal published its pages;
* ``open``: the org's windows that are published and not done, scored by their seal's due time (the repair index).

``sealed`` and the ids are never deleted by the seal (the TTL reaps them), so a late append always meets "closed".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.config.constants.arangodb import EventTypes
from app.core.envelope import max_age_seconds
from app.exceptions.indexing_exceptions import ProcessingError
from app.services.paid_cache.keys import valid_record_id
from app.services.paid_cache.locks import REPURGE_HOP_S, repurge_horizon_s
from app.services.paid_cache.repurge import (
    chain_enabled,
    next_hop_payload,
    now_s,
    publish_event,
)
from app.telemetry.backend import METRICS_BACKEND
from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from redis.asyncio import Redis

    #: Purges many ids of an org at once; one entry per id, ``None`` when it is done, else the error.
    PurgeMany = Callable[[str, Sequence[str]], Awaitable[dict[str, BaseException | None]]]

log = logging.getLogger(__name__)

COALESCE_ENV = "PAID_RESULT_REPURGE_COALESCE"
WINDOW_ENV = "PAID_RESULT_REPURGE_WINDOW_S"
PAGE_SIZE_ENV = "PAID_RESULT_REPURGE_PAGE_SIZE"

DEFAULT_WINDOW_S = 300
#: A seal runs this long after its window ends (a late append that meets a closed window falls back to a per-record hop).
SEAL_GRACE_S = 30
CLAIM_TTL_MS = 120_000
#: The window's keys outlive it by this long (a seal that is redelivered still finds its ids).
KEY_TTL_AFTER_S = 24 * 3600
WINDOW_CAP = 250_000
SEAL_READ_CHUNK = 10_000
DEFAULT_PAGE_SIZE = 500
#: The most ids one hop's body may carry (a body is a verified envelope's, but its size is still bounded).
MAX_PAGE_IDS = 1000
#: Page hop 1 of a window is spread over this long, so a window's pages do not all fall due in the same second.
SPREAD_S = 120
#: A seal found overdue by this much (a stale envelope is dropped by the consumer) is published again.
REPAIR_AFTER_S = 600
HANDBACK_S = 5
#: Past the claim's life (``CLAIM_TTL_MS``): a seal that keeps meeting its own live claim hands back this often.
MAX_HANDBACKS = 30
MAX_TAIL_ATTEMPTS = 4
TAIL_BASE_S = 60
PURGE_CONCURRENCY = 8

PHASE_SEAL = "seal"
PHASE_HOP = "hop"

APPENDED, CLOSED, BUSY, FULL, CLAIMED = "APPENDED", "CLOSED", "BUSY", "FULL", "CLAIMED"
SEAL_OK, SEAL_DONE, SEAL_HANDBACK, SEAL_LOST_ABSENT, SEAL_LOST_EPOCH = "OK", "DONE", "HANDBACK", "LOST_ABSENT", "LOST_EPOCH"

MESSAGES = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_messages_total", "Re-purge messages published, by kind", ["kind"],
)
FALLBACK = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_fallback_total",
    "Deleted records whose later hops went per record instead of into a window", ["reason"],
)
WINDOW_LOST = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_window_lost_total", "Windows whose ids were gone or replaced when their seal ran", ["reason"],
)
SEAL_REPAIRED = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_seal_repaired_total", "Overdue seals published again", ["kind"],
)
INVALID_IDS = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_invalid_ids_total", "Ids a window or page dropped because no result can be stored under them", ["where"],
)
FAILED_IDS = METRICS_BACKEND.counter(
    "pipeshub_paid_repurge_failed_ids_total", "Ids whose purge still failed after the tail attempts", ["kind"],
)
WINDOW_IDS = METRICS_BACKEND.histogram(
    "pipeshub_paid_repurge_window_ids", "Ids per sealed window", ["kind"],
    (1, 5, 25, 100, 500, 2_000, 10_000, 50_000, 250_000),
)
PAGES = METRICS_BACKEND.counter("pipeshub_paid_repurge_pages_total", "Pages published by seals", ["kind"])


#: A fallback is counted every time (``pipeshub_paid_repurge_fallback_total``); its WARNING is logged at most this often per
#: kind, so a 100k delete during an outage is one line a minute, not 100k.
WARN_EVERY_S = 60.0
_last_warned: dict[str, float] = {}


def _warn_limited(kind: str, message: str, *args: object) -> None:
    now = time.monotonic()
    if now - _last_warned.get(kind, float("-inf")) >= WARN_EVERY_S:
        _last_warned[kind] = now
        log.warning(message + " (further occurrences are counted, not logged, for %d s)", *args, int(WARN_EVERY_S))


def coalesce_enabled() -> bool:
    """Both the chain and the coalescing are on. Off (the default): every record keeps its per-record chain."""
    return chain_enabled() and (os.getenv(COALESCE_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def window_s() -> int:
    """The window length: 60 s up to 900 s, and never so long that a seal signed at the start of a window is older than the
    envelope accepts when it falls due (the age limit less ``SEAL_GRACE_S`` and the 300 s of consumer lag a hop is allowed)."""
    longest = max(60.0, min(900.0, max_age_seconds() - 300.0 - SEAL_GRACE_S))
    return int(min(max(env_seconds(WINDOW_ENV, DEFAULT_WINDOW_S), 60.0), longest))


def page_size() -> int:
    try:
        value = int(os.getenv(PAGE_SIZE_ENV) or DEFAULT_PAGE_SIZE)
    except ValueError:
        value = DEFAULT_PAGE_SIZE
    return min(max(value, 1), MAX_PAGE_IDS)


def window_of(now: float) -> int:
    return int(now // window_s())


def claim_token(org_id: str, payload: dict[str, Any]) -> str:
    """Stable across redeliveries of one hop-1 message (a hex digest: the claim value uses ``:`` as its separator)."""
    raw = f"{org_id}|{payload.get('recordId')}|{payload.get('deletedAt')}|{payload.get('hop')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# -- the state machine's pure part, shared by the PostgreSQL store (the Redis scripts below mirror it) -------------


@dataclass(frozen=True)
class Opened:
    state: str
    epoch: str | None = None


@dataclass(frozen=True)
class SealRead:
    state: str
    ids: tuple[str, ...] = ()
    #: How many ids the window held when its seal found it replaced or absent (0 when none can be seen).
    seen: int = 0
    #: How many ids the window accepted (its own counter): more than the list holds means the list lost some.
    expected: int = 0


def parse_own(own: str | None) -> tuple[str, str, str, int] | None:
    """``(kind, token, epoch, number)`` of an ``own`` value: a claim's token and expiry, or a published epoch and due."""
    if not own:
        return None
    parts = own.split(":")
    try:
        if parts[0] == "claim" and len(parts) == 4:
            return "claim", parts[1], parts[2], int(parts[3])
        if parts[0] == "published" and len(parts) == 3:
            return "published", "", parts[1], int(parts[2])
    except ValueError:
        return None
    return None


def decide_open(own: str | None, token: str, epoch: str, now_ms: int) -> tuple[str, str | None]:
    """What an ``open_or_append`` does given ``own``: ``("append", None)``, ``("busy", None)``, or
    ``("claim", epoch)`` (take or keep the claim; ``epoch`` is the live own claim's, else the new one)."""
    parsed = parse_own(own)
    if parsed is not None and parsed[0] == "published":
        return "append", None
    if parsed is not None and parsed[0] == "claim" and parsed[3] > now_ms:
        return ("claim", parsed[2]) if parsed[1] == token else ("busy", None)
    return "claim", epoch


def decide_seal(own: str | None, epoch: str, now_ms: int) -> str:
    """The seal's verdict on ``own``: ``OK`` (read the ids), ``HANDBACK`` (its own claim is still live), or a loss."""
    parsed = parse_own(own)
    if parsed is None:
        return SEAL_LOST_ABSENT
    kind, _token, own_epoch, number = parsed
    if own_epoch != epoch:
        return SEAL_LOST_EPOCH
    if kind == "claim":
        return SEAL_HANDBACK if number > now_ms else SEAL_LOST_EPOCH
    return SEAL_OK


class WindowStore(Protocol):
    async def open_or_append(self, org_id: str, window: int, token: str, epoch: str, rid: str, now_ms: int) -> Opened: ...

    async def mark_published(self, org_id: str, window: int, token: str, epoch: str, due_ms: int, now_ms: int) -> bool: ...

    async def release_claim(self, org_id: str, window: int, token: str) -> None: ...

    async def seal_read(self, org_id: str, window: int, epoch: str, now_ms: int) -> SealRead: ...

    async def mark_done(self, org_id: str, window: int, epoch: str) -> None: ...

    async def overdue(self, org_id: str, before_ms: int) -> list[tuple[int, str]]: ...


# -- Redis -------------------------------------------------------------------------------------------------------

_OPEN_OR_APPEND = """
if redis.call('EXISTS', KEYS[3]) == 1 then return {'CLOSED'} end
local own = redis.call('GET', KEYS[1])
local now = tonumber(ARGV[3])
if own and string.sub(own, 1, 10) == 'published:' then
  if redis.call('LLEN', KEYS[2]) >= tonumber(ARGV[5]) then return {'FULL'} end
  redis.call('RPUSH', KEYS[2], ARGV[4])
  redis.call('EXPIRE', KEYS[2], ARGV[6])
  redis.call('INCR', KEYS[4])
  redis.call('EXPIRE', KEYS[4], ARGV[6])
  return {'APPENDED'}
end
if own then
  local tok, ep, exp = string.match(own, '^claim:([^:]*):([^:]*):(%d+)$')
  if tok and tonumber(exp) > now then
    if tok ~= ARGV[1] then return {'BUSY'} end
    return {'CLAIMED', ep}
  end
end
redis.call('SET', KEYS[1], 'claim:' .. ARGV[1] .. ':' .. ARGV[2] .. ':' .. (now + tonumber(ARGV[7])), 'EX', ARGV[6])
return {'CLAIMED', ARGV[2]}
"""

_MARK_PUBLISHED = """
local own = redis.call('GET', KEYS[1])
if not own then return 0 end
local tok, ep, exp = string.match(own, '^claim:([^:]*):([^:]*):(%d+)$')
if (not tok) or tok ~= ARGV[1] or ep ~= ARGV[2] then return 0 end
if tonumber(exp) <= tonumber(ARGV[6]) then return 0 end
redis.call('SET', KEYS[1], 'published:' .. ep .. ':' .. ARGV[3], 'EX', ARGV[4])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return 1
"""

_RELEASE_CLAIM = """
local own = redis.call('GET', KEYS[1])
if own then
  local tok = string.match(own, '^claim:([^:]*):')
  if tok == ARGV[1] then return redis.call('DEL', KEYS[1]) end
end
return 0
"""

_SEAL = """
local own = redis.call('GET', KEYS[1])
local n = redis.call('LLEN', KEYS[2])
if not own then return {'LOST_ABSENT', n} end
local now = tonumber(ARGV[2])
local kind, ep = 'published', nil
local tok, cep, exp = string.match(own, '^claim:([^:]*):([^:]*):(%d+)$')
if tok then kind = 'claim'; ep = cep else ep = string.match(own, '^published:([^:]*):') end
if ep ~= ARGV[1] then return {'LOST_EPOCH', n} end
if kind == 'claim' then
  if tonumber(exp) > now then return {'HANDBACK', n} end
  return {'LOST_EPOCH', n}
end
if redis.call('EXISTS', KEYS[4]) == 1 then return {'DONE', n} end
redis.call('SET', KEYS[3], '1', 'EX', ARGV[3], 'NX')
return {'OK', n, tonumber(redis.call('GET', KEYS[5]) or '0')}
"""

_MARK_DONE = """
redis.call('SET', KEYS[1], '1', 'EX', ARGV[2])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""


def _decode(value: Any) -> str:  # noqa: ANN401 - redis returns bytes or str by client config
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisWindowStore:
    """The window state on one Redis hash tag per org. ``key_of`` adds the deployment's key namespace."""

    def __init__(self, client: Redis, key_of: Callable[[str], str]) -> None:
        self._client = client
        self._key_of = key_of

    def _base(self, org_id: str, window: int) -> str:
        return f"pc:rpw:{{{org_id}}}:{window}"

    def _keys(self, org_id: str, window: int) -> tuple[str, str, str, str]:
        base = self._base(org_id, window)
        return tuple(self._key_of(f"{base}:{part}") for part in ("own", "ids", "sealed", "done"))  # type: ignore[return-value]

    def _count_key(self, org_id: str, window: int) -> str:
        """How many ids the window accepted, kept apart from the list: a list Redis lost (eviction) is then seen as a loss."""
        return self._key_of(f"{self._base(org_id, window)}:count")

    def _open_key(self, org_id: str) -> str:
        return self._key_of(f"pc:rpw:{{{org_id}}}:open")

    async def open_or_append(self, org_id: str, window: int, token: str, epoch: str, rid: str, now_ms: int) -> Opened:
        own, ids, sealed, _done = self._keys(org_id, window)
        ttl = window_s() + KEY_TTL_AFTER_S
        res = await self._client.eval(  # type: ignore[misc]
            _OPEN_OR_APPEND, 4, own, ids, sealed, self._count_key(org_id, window), token, epoch, now_ms, rid, WINDOW_CAP, ttl,
            CLAIM_TTL_MS,
        )
        # ARGV order: 1 token, 2 epoch, 3 now, 4 rid, 5 cap, 6 ttl, 7 claim ttl
        state = _decode(res[0])
        return Opened(state, _decode(res[1]) if len(res) > 1 else None)

    async def mark_published(self, org_id: str, window: int, token: str, epoch: str, due_ms: int, now_ms: int) -> bool:
        own, _ids, _sealed, _done = self._keys(org_id, window)
        ttl = window_s() + KEY_TTL_AFTER_S
        return bool(await self._client.eval(  # type: ignore[misc]
            _MARK_PUBLISHED, 2, own, self._open_key(org_id), token, epoch, due_ms, ttl, f"{window}:{epoch}", now_ms,
        ))

    async def release_claim(self, org_id: str, window: int, token: str) -> None:
        own, _ids, _sealed, _done = self._keys(org_id, window)
        await self._client.eval(_RELEASE_CLAIM, 1, own, token)  # type: ignore[misc]

    async def seal_read(self, org_id: str, window: int, epoch: str, now_ms: int) -> SealRead:
        own, ids, sealed, done = self._keys(org_id, window)
        ttl = window_s() + KEY_TTL_AFTER_S
        res = await self._client.eval(_SEAL, 5, own, ids, sealed, done, self._count_key(org_id, window), epoch, now_ms, ttl)  # type: ignore[misc]
        state = _decode(res[0])
        if state != SEAL_OK:
            return SealRead(state, (), int(res[1]) if len(res) > 1 else 0)
        # The list is frozen once ``sealed`` is set (every later append meets CLOSED), so it is read in chunks: one
        # LRANGE of a 250k list would hold the slot's node for tens of ms.
        out: list[str] = []
        for start in range(0, int(res[1]), SEAL_READ_CHUNK):
            out.extend(_decode(x) for x in await self._client.lrange(ids, start, start + SEAL_READ_CHUNK - 1))
        return SealRead(SEAL_OK, tuple(out), expected=int(res[2]))

    async def mark_done(self, org_id: str, window: int, epoch: str) -> None:
        _own, _ids, _sealed, done = self._keys(org_id, window)
        await self._client.eval(  # type: ignore[misc]
            _MARK_DONE, 2, done, self._open_key(org_id), f"{window}:{epoch}", window_s() + KEY_TTL_AFTER_S,
        )

    async def overdue(self, org_id: str, before_ms: int) -> list[tuple[int, str]]:
        key = self._open_key(org_id)
        # Entries older than the ids' life can only count a loss: dropped, not repaired.
        await self._client.zremrangebyscore(key, "-inf", f"({before_ms - (KEY_TTL_AFTER_S - REPAIR_AFTER_S) * 1000}")
        members = await self._client.zrangebyscore(key, "-inf", f"({before_ms}")
        out: list[tuple[int, str]] = []
        for member in members:
            w, _, epoch = _decode(member).partition(":")
            if w.lstrip("-").isdigit() and epoch:
                out.append((int(w), epoch))
        return out


# -- PostgreSQL state mode ---------------------------------------------------------------------------------------


class PgWindowStore:
    """The same state machine on ``platform.kv_entries`` (``REDIS_MODE=postgres``).

    Append and seal of one window run under the same advisory lock (``PgState.locked``), so a late append either lands
    before the seal reads or finds the window sealed. The cap is enforced by the sequence ``incr`` returns; rows are
    never counted. ``sealed`` and the rows are left to their TTL.
    """

    def __init__(self, state: Any = None) -> None:  # noqa: ANN401 - a PgState (imported lazily: asyncpg)
        if state is None:
            from app.services.pg_state import PgState

            state = PgState()
        self._state = state

    @staticmethod
    def _base(org_id: str, window: int) -> str:
        return f"rpw:{{{org_id}}}:{window}"

    async def open_or_append(self, org_id: str, window: int, token: str, epoch: str, rid: str, now_ms: int) -> Opened:
        base = self._base(org_id, window)
        ttl = float(window_s() + KEY_TTL_AFTER_S)
        async with self._state.locked(base) as tx:
            if await tx.get(f"{base}:sealed") is not None:
                return Opened(CLOSED)
            action, ep = decide_open(await tx.get(f"{base}:own"), token, epoch, now_ms)
            if action == "busy":
                return Opened(BUSY)
            if action == "claim":
                await tx.set(f"{base}:own", f"claim:{token}:{ep}:{now_ms + CLAIM_TTL_MS}", ttl)
                return Opened(CLAIMED, ep)
            seq = await tx.incr(f"{base}:n", ttl)
            if seq > WINDOW_CAP:
                return Opened(FULL)
            await tx.set(f"{base}:ids:{seq:010d}", rid, ttl)
            return Opened(APPENDED)

    async def mark_published(self, org_id: str, window: int, token: str, epoch: str, due_ms: int, now_ms: int) -> bool:
        base = self._base(org_id, window)
        ttl = float(window_s() + KEY_TTL_AFTER_S)
        async with self._state.locked(base) as tx:
            parsed = parse_own(await tx.get(f"{base}:own"))
            if parsed is None or parsed[0] != "claim" or parsed[1] != token or parsed[2] != epoch or parsed[3] <= now_ms:
                return False
            await tx.set(f"{base}:own", f"published:{epoch}:{due_ms}", ttl)
            await tx.set(f"rpw:{{{org_id}}}:open:{window}:{epoch}", str(due_ms), ttl)
            return True

    async def release_claim(self, org_id: str, window: int, token: str) -> None:
        base = self._base(org_id, window)
        async with self._state.locked(base) as tx:
            parsed = parse_own(await tx.get(f"{base}:own"))
            if parsed is not None and parsed[0] == "claim" and parsed[1] == token:
                await tx.delete(f"{base}:own")

    async def seal_read(self, org_id: str, window: int, epoch: str, now_ms: int) -> SealRead:
        base = self._base(org_id, window)
        ttl = float(window_s() + KEY_TTL_AFTER_S)
        async with self._state.locked(base) as tx:
            # The lock is held for these few statements only: how many ids the window holds is its append counter (the
            # rows are not fetched here), and ``sealed`` is committed with the verdict.
            seen = int(await tx.get(f"{base}:n") or 0)
            verdict = decide_seal(await tx.get(f"{base}:own"), epoch, now_ms)
            if verdict != SEAL_OK:
                return SealRead(verdict, (), seen)
            if await tx.get(f"{base}:done") is not None:
                return SealRead(SEAL_DONE, (), seen)
            if await tx.get(f"{base}:sealed") is None:
                await tx.set(f"{base}:sealed", "1", ttl)
        # ``sealed`` is committed: every later append meets CLOSED, so the rows are frozen and are read in pages, outside the
        # lock, so memory stays bounded by a page (plus the ids themselves) and appends are not held behind a big read.
        ids: list[str] = []
        after: str | None = None
        while True:
            page = await self._state.read_rows(f"{base}:ids:", after, SEAL_READ_CHUNK)
            ids.extend(v for _k, v in page)
            if len(page) < SEAL_READ_CHUNK:
                break
            after = page[-1][0]
        return SealRead(SEAL_OK, tuple(ids), expected=min(seen, WINDOW_CAP))

    async def mark_done(self, org_id: str, window: int, epoch: str) -> None:
        base = self._base(org_id, window)
        async with self._state.locked(base) as tx:
            await tx.set(f"{base}:done", "1", float(window_s() + KEY_TTL_AFTER_S))
            await tx.delete(f"rpw:{{{org_id}}}:open:{window}:{epoch}")

    async def overdue(self, org_id: str, before_ms: int) -> list[tuple[int, str]]:
        base = f"rpw:{{{org_id}}}:open:"
        async with self._state.locked(f"{base}index") as tx:
            rows = await tx.rows(base)
        out: list[tuple[int, str]] = []
        oldest = before_ms - (KEY_TTL_AFTER_S - REPAIR_AFTER_S) * 1000
        for key, value in rows:
            w, _, epoch = key[len(base):].partition(":")
            if w.lstrip("-").isdigit() and epoch and value.isdigit() and oldest <= int(value) < before_ms:
                out.append((int(w), epoch))
        return out


async def window_store_for(cache: Any) -> WindowStore | None:  # noqa: ANN401 - a PaidResultCache or its null twin
    """The window store of this deployment, or None where state is not shared between processes (then every record
    keeps its per-record chain): PostgreSQL state mode first, else the Redis the paid-result locks use."""
    from app.services.redis.connection_provider_factory import uses_pg_state

    if uses_pg_state():
        return PgWindowStore()
    backend_of = getattr(cache, "_backend", None)
    if backend_of is None:
        return None
    backend = await backend_of()
    client = getattr(backend, "_client", None)
    if not getattr(backend, "shared", False) or client is None:
        return None
    return RedisWindowStore(client, backend.key_of)


# -- publishing --------------------------------------------------------------------------------------------------


def seal_payload(org_id: str, window: int, epoch: str, *, due_s: float, handbacks: int = 0) -> dict[str, Any]:
    payload: dict[str, Any] = {"orgId": org_id, "phase": PHASE_SEAL, "window": window, "epoch": epoch, "_retry_not_before": due_s}
    if handbacks:
        payload["handbacks"] = handbacks
    return payload


async def _publish_batch(producer: Any, org_id: str, payload: dict[str, Any], key: str, loop: Any, kind: str) -> None:  # noqa: ANN401
    await publish_event(producer, payload, key, loop, event_type=EventTypes.PURGE_PAID_RESULTS_BATCH.value)
    MESSAGES.inc(kind)


def page_hop_payload(
    org_id: str, window: int, page: int, ids: Sequence[str], *, now: float, spread_s: float,
) -> dict[str, Any]:
    """Page hop 1: ``deletedAt`` is the seal's run time, so the chain covers every id the seal read for a full horizon."""
    return {
        "orgId": org_id, "phase": PHASE_HOP, "window": window, "page": page, "recordIds": list(ids),
        "deletedAt": now, "hop": 1, "horizonS": repurge_horizon_s(),
        "_retry_not_before": now + REPURGE_HOP_S + spread_s,
    }


# -- the first hop's successor -----------------------------------------------------------------------------------


async def coalesce_successor(
    producer: Any, org_id: str, payload: dict[str, Any], *, store: WindowStore | None,  # noqa: ANN401
    loop: Any = None, now: float | None = None,  # noqa: ANN401
) -> bool:
    """After a record's first hop purged: True when its later hops are covered by a window (or the chain is over),
    False when the caller must publish the per-record second hop. Raises only when a seal cannot be published (the
    first hop is then redelivered and finds its own claim by its stable token)."""
    if not coalesce_enabled():
        return False
    now = now_s() if now is None else now
    if next_hop_payload(org_id, payload, now=now) is None:
        return True
    rid = payload.get("recordId")
    if rid is None:
        return False                    # an org hop has no record: nothing to place, nothing to count
    if not isinstance(rid, str) or not valid_record_id(rid):
        FALLBACK.inc("invalid_id")
        return False
    if store is None:
        FALLBACK.inc("no_store")
        return False
    carried = payload.get("horizonS")
    if isinstance(carried, int | float) and not isinstance(carried, bool) and carried > repurge_horizon_s():
        # This record's chain was started under a longer horizon than the live one (a budget was lowered since): a page chain
        # would end at the live horizon from its seal, so the record keeps the chain that carries its own.
        FALLBACK.inc("carried_horizon")
        return False
    token, window, now_ms = claim_token(org_id, payload), window_of(now), int(now * 1000)
    try:
        opened = await store.open_or_append(org_id, window, token, secrets.token_hex(8), rid, now_ms)
    except Exception as exc:
        FALLBACK.inc("error")
        _warn_limited("unavailable", "Re-purge window unavailable (%s); the record's later hops go per record", type(exc).__name__)
        return False
    if opened.state == CLAIMED and opened.epoch:
        due = max((window + 1) * window_s() + SEAL_GRACE_S, now)
        try:
            await _publish_batch(producer, org_id, seal_payload(org_id, window, opened.epoch, due_s=due), f"{org_id}:{window}", loop, "seal")
        except BaseException:
            await _release_quietly(store, org_id, window, token)
            raise
        try:
            if not await store.mark_published(org_id, window, token, opened.epoch, int(due * 1000), int(now_s() * 1000)):
                # The claim lapsed (or was taken) while the seal was being published: the window is not this deleter's to open.
                FALLBACK.inc("claim_lost")
                return False
            opened = await store.open_or_append(org_id, window, token, secrets.token_hex(8), rid, now_ms)
        except Exception as exc:
            FALLBACK.inc("error")
            _warn_limited("unavailable", "Re-purge window unavailable (%s); the record's later hops go per record", type(exc).__name__)
            return False
        await _repair(producer, org_id, store, loop, now)
    if opened.state == APPENDED:
        return True
    FALLBACK.inc(opened.state.lower())
    return False


async def _release_quietly(store: WindowStore, org_id: str, window: int, token: str) -> None:
    try:
        await store.release_claim(org_id, window, token)
    except Exception as exc:
        _warn_limited("release", "Re-purge window claim not released (%s); it lapses by itself", type(exc).__name__)


async def _repair(producer: Any, org_id: str, store: WindowStore, loop: Any, now: float) -> None:  # noqa: ANN401
    """Publishes again the seal of every window of this org that is published, not done, and long overdue."""
    try:
        overdue = await store.overdue(org_id, int((now - REPAIR_AFTER_S) * 1000))
        for window, epoch in overdue:
            await _publish_batch(
                producer, org_id, seal_payload(org_id, window, epoch, due_s=now), f"{org_id}:{window}", loop, "seal",
            )
            SEAL_REPAIRED.inc("seal")
    except Exception as exc:
        _warn_limited("repair", "Re-purge seal repair failed (%s); the next window's first record retries it", type(exc).__name__)


# -- the batch event's handlers ----------------------------------------------------------------------------------


class BatchPayloadError(ValueError):
    """A batch hop or seal whose shape no retry can fix."""


def _int_field(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BatchPayloadError(f"purgePaidResultsBatch carries no integer {name}")
    return value


def _ids_of(payload: dict[str, Any]) -> list[str]:
    ids = payload.get("recordIds")
    if not isinstance(ids, list) or len(ids) > MAX_PAGE_IDS or not all(isinstance(i, str) for i in ids):
        raise BatchPayloadError("purgePaidResultsBatch carries no usable recordIds")
    good = [i for i in dict.fromkeys(ids) if valid_record_id(i)]
    if len(good) != len(ids):
        INVALID_IDS.inc("page")
    return good


async def handle_seal(
    producer: Any, org_id: str, payload: dict[str, Any], *, store: WindowStore | None,  # noqa: ANN401
    loop: Any = None, now: float | None = None,  # noqa: ANN401
) -> None:
    """The seal of a window: read its ids, page them, publish page hop 1 of each page. Raises when it cannot publish
    (the redelivery reads the same list again; a page already published runs a duplicate chain, which is harmless)."""
    window = _int_field(payload, "window")
    epoch = payload.get("epoch")
    if not isinstance(epoch, str) or not epoch or len(epoch) > 64:
        raise BatchPayloadError("purgePaidResultsBatch seal carries no epoch")
    if store is None:
        raise RuntimeError("No window store: a purgePaidResultsBatch seal cannot be read")
    now = now_s() if now is None else now
    read = await store.seal_read(org_id, window, epoch, int(now * 1000))
    if read.state == SEAL_DONE:
        return
    if read.state == SEAL_HANDBACK:
        handbacks = payload.get("handbacks")
        handbacks = handbacks if isinstance(handbacks, int) and not isinstance(handbacks, bool) else 0
        if handbacks < MAX_HANDBACKS:
            again = seal_payload(org_id, window, epoch, due_s=now + HANDBACK_S, handbacks=handbacks + 1)
            await _publish_batch(producer, org_id, again, f"{org_id}:{window}", loop, "seal")
            return
        WINDOW_LOST.inc("handback_exhausted")
        log.error("Re-purge window %s of an org stayed claimed past its life; %d ids keep only their first hop", window, read.seen)
        return
    if read.state in (SEAL_LOST_ABSENT, SEAL_LOST_EPOCH):
        WINDOW_LOST.inc("absent" if read.state == SEAL_LOST_ABSENT else "epoch")
        log.error(
            "Re-purge window %s of an org was lost or replaced before its seal ran (%s); %d ids keep only their first hop",
            window, read.state, read.seen,
        )
        return
    if read.expected > len(read.ids):
        # The window accepted more ids than its list holds (Redis evicted or lost part of it): counted, and the ids that
        # remain are still paged. The ones lost keep only their first hop.
        WINDOW_LOST.inc("ids")
        log.error(
            "Re-purge window %s of an org lost part of its id list: %d accepted, %d left; the rest keep only their first hop",
            window, read.expected, len(read.ids),
        )
    valid = [i for i in dict.fromkeys(read.ids) if valid_record_id(i)]
    if len(valid) != len(read.ids):
        INVALID_IDS.inc("seal")
    WINDOW_IDS.observe("window", value=float(len(valid)))
    size = page_size()
    pages = [valid[i:i + size] for i in range(0, len(valid), size)]
    for index, ids in enumerate(pages):
        spread = math.floor(index * SPREAD_S / len(pages))
        hop = page_hop_payload(org_id, window, index, ids, now=now, spread_s=spread)
        await _publish_batch(producer, org_id, hop, f"{org_id}:{window}:{index}", loop, "page_hop")
    PAGES.inc("page", value=float(len(pages)))
    await store.mark_done(org_id, window, epoch)


def tail_delay_s(attempt: int) -> float:
    """The wait before tail attempt ``attempt`` (1-based): 60, 120, 240, 480 s, never past the envelope's age less 300 s."""
    return float(min(TAIL_BASE_S * 2 ** (attempt - 1), max(max_age_seconds() - 300, TAIL_BASE_S)))


async def handle_page_hop(
    producer: Any, org_id: str, payload: dict[str, Any], *,  # noqa: ANN401
    purge: Callable[[str, str], Awaitable[None]], loop: Any = None, now: float | None = None,  # noqa: ANN401
    purge_many: PurgeMany | None = None,
) -> None:
    """A page hop: purge every id (through ``purge_many`` when given: a few storage requests for the page, else one per id), then publish the next hop (all ids again). Partial failure never fails the page:
    the next hop retries the failed ids; at the final hop a tail message carries just them, a few attempts, and then the
    message dead-letters with them. Every id failing, or the successor not publishing, raises (broker redelivery)."""
    ids = _ids_of(payload)
    if not ids:
        return                          # nothing to purge and nothing to keep a chain going for
    now = now_s() if now is None else now
    tail = payload.get("tail") is True
    attempt = payload.get("attempt")
    attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1 else 1
    failed, first_error = await _purge_all(org_id, ids, purge, purge_many)
    if ids and len(failed) == len(ids) and not tail:
        raise first_error or RuntimeError("every id of a re-purge page failed")
    if tail:
        if failed:
            if attempt >= MAX_TAIL_ATTEMPTS:
                FAILED_IDS.inc("tail")
                # Terminal, so it dead-letters at once with the failed ids in its body (replayable), not after a redelivery budget.
                raise ProcessingError(
                    f"{len(failed)} paid-result purges still failing after {attempt} tail attempts",
                    details={"failed": len(failed)},
                )
            await _publish_tail(producer, org_id, payload, failed, attempt + 1, now, loop)
        return
    following = next_hop_payload(org_id, payload, now=now)
    if following is None:
        if failed:
            await _publish_tail(producer, org_id, payload, failed, 1, now, loop)
        return
    from app.services.paid_cache.repurge import hop_key

    following["recordIds"] = ids                     # the valid ids only, so a dropped id is dropped for good
    await publish_event(
        producer, following, hop_key(org_id, following), loop, event_type=EventTypes.PURGE_PAID_RESULTS_BATCH.value,
    )
    MESSAGES.inc("page_hop")


async def _purge_all(
    org_id: str, ids: list[str], purge: Callable[[str, str], Awaitable[None]], purge_many: PurgeMany | None = None,
) -> tuple[list[str], BaseException | None]:
    if purge_many is not None:
        outcomes = await purge_many(org_id, ids)
        # An id the answer leaves out is not purged: it is repeated like any failure.
        failed_ids = [rid for rid in ids if rid not in outcomes or outcomes[rid] is not None]
        first_failure = next((outcomes[rid] for rid in failed_ids if outcomes.get(rid) is not None), None)
        return failed_ids, first_failure
    gate = asyncio.Semaphore(PURGE_CONCURRENCY)
    errors: list[BaseException | None] = []

    async def one(rid: str) -> None:
        async with gate:
            try:
                await purge(org_id, rid)
                errors.append(None)
            except Exception as exc:
                errors.append(exc)
                failed.append(rid)

    failed: list[str] = []
    await asyncio.gather(*(one(rid) for rid in ids))
    first = next((e for e in errors if e is not None), None)
    return failed, first


async def _publish_tail(
    producer: Any, org_id: str, payload: dict[str, Any], failed: list[str], attempt: int, now: float, loop: Any,  # noqa: ANN401
) -> None:
    body = {
        "orgId": org_id, "phase": PHASE_HOP, "window": payload.get("window"), "page": payload.get("page"),
        "recordIds": failed, "tail": True, "attempt": attempt, "hop": payload.get("hop"),
        "deletedAt": payload.get("deletedAt"), "_retry_not_before": now + tail_delay_s(attempt),
    }
    await _publish_batch(
        producer, org_id, body, f"{org_id}:{payload.get('window')}:{payload.get('page')}", loop, "tail",
    )
