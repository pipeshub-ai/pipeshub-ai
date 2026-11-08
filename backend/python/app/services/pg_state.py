"""Cross-process state on PostgreSQL (``REDIS_MODE=postgres``): leader keys, capacity leases, retry counters, plain counters.

Everything lives in ``platform.kv_entries`` (bytes + optional expiry), so a second process sees what the first wrote, which an
in-process Redis cannot do. Expiry is the database clock. Each primitive is one statement (or one short transaction), so it is
atomic without advisory locks (but for the capacity pools, below):

* a lock row is taken by ``INSERT ... ON CONFLICT DO UPDATE ... WHERE expired OR mine``;
* a counter is bumped by one upsert, and keeps the expiry it was created with (like INCR);
* a capacity pool serialises on a transaction-scoped advisory lock keyed by its name, then counts live leases.

Caches do not belong here: they stay in the process and are dropped by NOTIFY (see ``ConfigurationService``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.distributed.interface import IDistributedLeaseManager, IRetryTracker
from app.services.pg_runtime import RuntimeDb

if TYPE_CHECKING:
    from collections.abc import Sequence

LOCK_PREFIX = "state:lock:"
COUNTER_PREFIX = "state:counter:"
LEASE_PREFIX = "state:lease:"
POOL_PREFIX = "state:pool:"

_ACQUIRE = """/* pipeshub:state.lock */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, convert_to($2, 'UTF8'), now() + make_interval(secs => $3::double precision), now())
ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at, updated_at = now()
WHERE platform.kv_entries.expires_at <= now() OR platform.kv_entries.value = excluded.value
RETURNING true"""
_RENEW = """/* pipeshub:state.renew */
UPDATE platform.kv_entries SET expires_at = now() + make_interval(secs => $3::double precision), updated_at = now()
WHERE key = $1 AND value = convert_to($2, 'UTF8') AND expires_at > now() RETURNING true"""
_RELEASE = """/* pipeshub:state.release */
DELETE FROM platform.kv_entries WHERE key = $1 AND value = convert_to($2, 'UTF8')"""
_INCR = """/* pipeshub:state.incr */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, convert_to('1', 'UTF8'),
        CASE WHEN $2::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $2::double precision) END, now())
ON CONFLICT (key) DO UPDATE SET
  value = CASE WHEN platform.kv_entries.expires_at <= now() THEN convert_to('1', 'UTF8')
               ELSE convert_to((convert_from(platform.kv_entries.value, 'UTF8')::bigint + 1)::text, 'UTF8') END,
  expires_at = CASE WHEN platform.kv_entries.expires_at <= now() THEN excluded.expires_at ELSE platform.kv_entries.expires_at END,
  updated_at = now()
RETURNING convert_from(value, 'UTF8')::bigint AS n"""
_GET = """/* pipeshub:state.get */
SELECT convert_from(value, 'UTF8') AS v FROM platform.kv_entries WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())"""
_SET = """/* pipeshub:state.set */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, convert_to($2, 'UTF8'),
        CASE WHEN $3::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $3::double precision) END, now())
ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at, updated_at = now()"""
_DELETE = """/* pipeshub:state.delete */
DELETE FROM platform.kv_entries WHERE key = ANY($1::text[])"""
_ANY_LIVE = """/* pipeshub:state.anylive */
SELECT count(*) FROM platform.kv_entries WHERE key = ANY($1::text[]) AND (expires_at IS NULL OR expires_at > now())"""
# capacity leases
# a pool is serialised by a transaction-scoped advisory lock on a hash of its name: no row is left behind per pool, and it works
# behind a transaction pooler because it is released at COMMIT (the two-integer lock classes are the kernel's; this one-key form is a different namespace)
_POOL_LOCK = """/* pipeshub:state.poollock */
SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))"""
_LEASE_RENEW = """/* pipeshub:state.leaserenew */
UPDATE platform.kv_entries SET expires_at = now() + make_interval(secs => $2::double precision), updated_at = now()
WHERE key = $1 AND expires_at > now() RETURNING true"""
_LEASE_COUNT = """/* pipeshub:state.leasecount */
SELECT count(*) FROM platform.kv_entries WHERE key >= $1 AND key < $2 AND expires_at > now()"""
_LEASE_REAP = """/* pipeshub:state.leasereap */
DELETE FROM platform.kv_entries WHERE key >= $1 AND key < $2 AND expires_at <= now()"""
_LEASE_TAKE = """/* pipeshub:state.leasetake */
INSERT INTO platform.kv_entries (key, value, expires_at, updated_at)
VALUES ($1, ''::bytea, now() + make_interval(secs => $2::double precision), now())
ON CONFLICT (key) DO UPDATE SET expires_at = excluded.expires_at, updated_at = now()"""
_LEASE_RENEW_MANY = """/* pipeshub:state.leaserenewmany */
UPDATE platform.kv_entries SET expires_at = now() + make_interval(secs => $2::double precision), updated_at = now()
WHERE key = ANY($1::text[]) AND expires_at > now() RETURNING key"""


def _range(prefix: str) -> tuple[str, str]:
    """[prefix, end) covering every key that starts with ``prefix`` (the prefixes here end in ':', so the end is ';')."""
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)


class PgState:
    def __init__(self, runtime: RuntimeDb | None = None) -> None:
        self.rt = runtime or RuntimeDb()

    async def _tx(self, *, write: bool = True):  # noqa: ANN202
        return (await self.rt.db()).runtime_uow(write=write)

    # leader keys / single-flight locks ----------------------------------------------------------------------
    async def lock_acquire(self, key: str, owner: str, ttl_seconds: float) -> bool:
        """Take the lock, or renew it when ``owner`` already holds it. False while another owner's lock is live."""
        async with await self._tx() as tx:
            return bool(
                await tx.fetchval(
                    _ACQUIRE, LOCK_PREFIX + key, owner, float(max(ttl_seconds, 1))
                )
            )

    async def lock_renew(self, key: str, owner: str, ttl_seconds: float) -> bool:
        async with await self._tx() as tx:
            return bool(
                await tx.fetchval(
                    _RENEW, LOCK_PREFIX + key, owner, float(max(ttl_seconds, 1))
                )
            )

    async def lock_release(self, key: str, owner: str) -> bool:
        """Drop the lock only if ``owner`` still holds it; True when a row went."""
        async with await self._tx() as tx:
            return (await tx.execute(_RELEASE, LOCK_PREFIX + key, owner)).endswith(" 1")

    async def lock_owner(self, key: str) -> str | None:
        """Who holds the lock now (``None`` when nobody, or it has expired)."""
        async with await self._tx(write=False) as tx:
            return await tx.fetchval(_GET, LOCK_PREFIX + key)

    # counters (retry counts, the ACL-version counter, rate windows) ---------------------------------------------
    async def incr(self, key: str, ttl_seconds: float | None = None) -> int:
        """+1, creating the counter at 1. The expiry is fixed when the counter is created; ``None`` keeps it for ever."""
        async with await self._tx() as tx:
            return int(await tx.fetchval(_INCR, COUNTER_PREFIX + key, ttl_seconds))

    async def get_int(self, key: str) -> int:
        async with await self._tx(write=False) as tx:
            v = await tx.fetchval(_GET, COUNTER_PREFIX + key)
        return int(v) if v else 0

    async def delete_counters(self, keys: Sequence[str]) -> int:
        async with await self._tx() as tx:
            n = await tx.fetchval(_ANY_LIVE, [COUNTER_PREFIX + k for k in keys])
            await tx.execute(_DELETE, [COUNTER_PREFIX + k for k in keys])
        return int(n)

    async def any_counter(self, keys: Sequence[str]) -> bool:
        async with await self._tx(write=False) as tx:
            return bool(
                await tx.fetchval(_ANY_LIVE, [COUNTER_PREFIX + k for k in keys])
            )

    # short-lived flags --------------------------------------------------------------------------------------
    async def set_flag(self, key: str, value: str, ttl_seconds: float | None) -> None:
        async with await self._tx() as tx:
            await tx.execute(_SET, "state:flag:" + key, value, ttl_seconds)

    async def get_flag(self, key: str) -> str | None:
        async with await self._tx(write=False) as tx:
            return await tx.fetchval(_GET, "state:flag:" + key)


class PgLeaderLock:
    """The ``LeaderLock`` protocol (try_acquire / refresh / release / close) on :class:`PgState`."""

    def __init__(
        self, owner: str, ttl_seconds: int, key: str, state: PgState | None = None
    ) -> None:
        self.owner, self.ttl_seconds, self.key = owner, max(1, ttl_seconds), key
        self._state = state or PgState()

    async def try_acquire(self) -> bool:
        return await self._state.lock_acquire(self.key, self.owner, self.ttl_seconds)

    async def refresh(self) -> bool:
        return await self._state.lock_renew(self.key, self.owner, self.ttl_seconds)

    async def release(self) -> None:
        await self._state.lock_release(self.key, self.owner)

    async def close(self) -> None:
        return None


class PgLeaseManager(IDistributedLeaseManager):
    """Cluster-wide capacity leases (``record:<id>`` exclusivity is ``limit=1``; a parse or index pool is ``limit=N``) across processes."""

    def __init__(self, state: PgState | None = None) -> None:
        self._state = state or PgState()

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    @staticmethod
    def _key(pool: str, owner: str) -> str:
        return f"{LEASE_PREFIX}{pool}:{owner}"

    async def try_acquire(
        self, pool: str, owner: str, limit: int, lease_seconds: float
    ) -> bool:
        lo, hi = _range(f"{LEASE_PREFIX}{pool}:")
        mark = POOL_PREFIX + pool
        secs = float(max(lease_seconds, 1))
        async with await self._state._tx() as tx:
            await tx.fetchval(_POOL_LOCK, mark)  # one acquirer per pool at a time
            if await tx.fetchval(_LEASE_RENEW, self._key(pool, owner), secs):
                return True  # already ours: a renewal
            await tx.execute(_LEASE_REAP, lo, hi)
            if int(await tx.fetchval(_LEASE_COUNT, lo, hi)) >= limit:
                return False
            await tx.execute(_LEASE_TAKE, self._key(pool, owner), secs)
            return True

    async def renew(self, pool: str, owner: str, lease_seconds: float) -> bool:
        async with await self._state._tx() as tx:
            return bool(
                await tx.fetchval(
                    _LEASE_RENEW, self._key(pool, owner), float(max(lease_seconds, 1))
                )
            )

    async def renew_many(
        self, leases: Sequence[tuple[str, str]], lease_seconds: float
    ) -> dict[tuple[str, str], bool]:
        if not leases:
            return {}
        keys = {self._key(p, o): (p, o) for p, o in leases}
        async with await self._state._tx() as tx:
            rows = await tx.fetch(
                _LEASE_RENEW_MANY, list(keys), float(max(lease_seconds, 1))
            )
        alive = {r["key"] for r in rows}
        return {lease: key in alive for key, lease in keys.items()}

    async def release(self, pool: str, owner: str) -> None:
        async with await self._state._tx() as tx:
            await tx.execute(_DELETE, [self._key(pool, owner)])


class PgRetryTracker(IRetryTracker):
    """Failure, delivery and hand-back counters per message id, shared by every worker, expiring after ``ttl_seconds``."""

    FAIL, DELIVERY, HANDBACK = "retry:fail:", "retry:delivery:", "retry:handback:"
    DEFAULT_TTL_SECONDS = 86400

    def __init__(
        self, state: PgState | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._state = state or PgState()
        self.ttl_seconds = ttl_seconds

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def increment_and_check(
        self, message_id: str, max_attempts: int
    ) -> tuple[int, bool]:
        n = await self._state.incr(self.FAIL + message_id, self.ttl_seconds)
        return n, n >= max_attempts

    async def get_count(self, message_id: str) -> int:
        return await self._state.get_int(self.FAIL + message_id)

    def _all(self, message_id: str) -> list[str]:
        return [
            self.FAIL + message_id,
            self.DELIVERY + message_id,
            self.HANDBACK + message_id,
        ]

    async def clear(self, message_id: str) -> None:
        await self._state.delete_counters(self._all(message_id))

    async def clear_batch(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        failed = await self._state.delete_counters([self.FAIL + m for m in message_ids])
        await self._state.delete_counters(
            [k for m in message_ids for k in (self.DELIVERY + m, self.HANDBACK + m)]
        )
        return failed

    async def has_pending_retries(self, message_ids: list[str]) -> bool:
        return bool(message_ids) and await self._state.any_counter(
            [self.FAIL + m for m in message_ids]
        )

    async def record_delivery(self, message_id: str) -> int:
        return await self._state.incr(self.DELIVERY + message_id, self.ttl_seconds)

    async def record_handback(self, message_id: str) -> int:
        return await self._state.incr(self.HANDBACK + message_id, self.ttl_seconds)

    async def get_handbacks(self, message_id: str) -> int:
        return await self._state.get_int(self.HANDBACK + message_id)
