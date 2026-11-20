"""Single-flight locks for the paid-result cache: ``<ns>:pc:lock:{<org>:<record>}:<digest>``, and the
delete markers ``<ns>:pc:deleted:{<org>:<record>}`` that fence a deleted record's late writers.

``SET NX PX`` with a random token, renewed while the holder works and released only by its token. A holder
that dies simply lapses. Redis holds nothing else for the cache: results live in the blob store.
"""

import logging
import math
import secrets
import threading
import time
from typing import TYPE_CHECKING, Protocol

from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from redis.asyncio import Redis

LOCK_TTL_MS = 120_000

_RENEW_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE', KEYS[1], ARGV[2]) end return 0"
_OWNS_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return 1 end return 0"
_MARK_IF_OWNED_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('SET', KEYS[2], '1', 'PX', ARGV[2]) return 1 end return 0"
)
_RELEASE_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


#: A delete marker outlives the longest parse a delete can race (an orphaned request keeps running after the
#: indexing run that sent it gave up).
DELETED_TTL_MS = 24 * 3600 * 1000


#: What bounds a write that lands after its record was deleted. A lock lives at most ``LOCK_TTL_MS`` past its
#: last renewal, a signed upload that has started ends within ``WRITE_DEADLINE_S`` (enforced: the store sequence
#: runs under that timeout), and a result is stored only if its stage began less than ``stage_deadline_s()`` ago:
#: the record's own processing budget, read from the settings the indexing client reads, never less than
#: ``STAGE_DEADLINE_S`` (a floor for the smallest budgets).
WRITE_DEADLINE_S = 120
STAGE_DEADLINE_S = 2400
REPURGE_MARGIN_S = 60
#: The delayed second purge of a deleted record is a chain of hops, each due this long after the one before
#: (lock TTL + write deadline + margin), so no queued message waits long enough to outlive its signature.
REPURGE_HOP_S = LOCK_TTL_MS // 1000 + WRITE_DEADLINE_S + REPURGE_MARGIN_S
REPURGE_DELAY_S = REPURGE_HOP_S
#: The figures below mirror the consumer's, the client's and the parsing route's (a test ties each to its source).
PARSE_SLOT_WAITS = 2
PARSE_GATE_WAIT_S = 120
PARSE_MEMORY_WAIT_S = 30


_budget_clamp_warned: set[str] = set()


def _clamped(name: str, default: float) -> float:
    """A record budget from the environment, never above ``MAX_RECORD_BUDGET_S``: a run longer than the open-manifest
    protection window could still be writing after it ends. A setting above it is clamped with one warning."""
    value = env_seconds(name, default)
    if value > MAX_RECORD_BUDGET_S:
        if name not in _budget_clamp_warned:
            _budget_clamp_warned.add(name)
            logging.getLogger(__name__).warning(
                "%s=%.0f s is above the %.0f s the paid-result cache allows (its open-manifest protection window less a "
                "margin); the stage deadline and the re-purge horizon use %.0f s", name, value, MAX_RECORD_BUDGET_S,
                MAX_RECORD_BUDGET_S,
            )
        return MAX_RECORD_BUDGET_S
    return value


def _raw_budgets() -> tuple[float, float]:
    """(general record budget, longest budget of any record type), from the environment, live, as configured: the
    indexing consumer admits under these, whatever the stage deadline is clamped to."""
    record = env_seconds("RECORD_PROCESSING_TIMEOUT", 1800.0)
    return record, max(record, env_seconds("PDF_RECORD_PROCESSING_TIMEOUT", 5400.0))


def _budgets() -> tuple[float, float]:
    """The same, clamped to the protection window (for the stage deadline); warns once per variable when it clamps."""
    record = _clamped("RECORD_PROCESSING_TIMEOUT", 1800.0)
    return record, max(record, _clamped("PDF_RECORD_PROCESSING_TIMEOUT", 5400.0))


def stage_deadline_s() -> float:
    """How long after its start a stage may still store results and write its manifests, and the most a parse may run.

    The longest record budget configured: a parse the client allows (at most 0.8 of its record's budget) is
    never cut by the route, and the index run that began before it, which holds the whole record's budget, can still
    store its own results. Read at every call, so an operator's raised budget is followed.
    """
    return max(float(STAGE_DEADLINE_S), _budgets()[1])


#: The most a record budget a caller sends can be: a request body is not trusted with an unbounded deadline.
#: How long an open manifest keeps its objects out of the retention sweep (a run still writing is protected this long).
OPEN_MANIFEST_PROTECTS_S = 24 * 3600
#: Kept inside the window: a run whose budget is longer than the protection could still be writing after it ends.
OPEN_MANIFEST_MARGIN_S = 4 * 3600
MAX_RECORD_BUDGET_S = float(OPEN_MANIFEST_PROTECTS_S - OPEN_MANIFEST_MARGIN_S)


def valid_record_budget(record_budget_s: object) -> float | None:
    """The record budget a caller sent, when it is a usable duration (finite, positive, below the open-manifest
    protection window by its margin), else None."""
    if isinstance(record_budget_s, bool) or not isinstance(record_budget_s, int | float):
        return None
    try:
        budget = float(record_budget_s)
    except OverflowError:
        return None
    return budget if math.isfinite(budget) and 0 < budget <= MAX_RECORD_BUDGET_S else None


def stage_deadline_for(record_budget_s: float | None) -> float:
    """The stage deadline for a record whose budget the caller sent with its request.

    The parsing client sends the record budget it applies, so the service uses the very figure the client waits
    for, whatever its own environment says. An absent or unusable figure (an older client during a rollout) falls
    back to the service's own settings (``stage_deadline_s()``).
    """
    budget = valid_record_budget(record_budget_s)
    return max(float(STAGE_DEADLINE_S), budget) if budget is not None else stage_deadline_s()


def admission_window_s() -> float:
    """The latest a paid stage can begin after its record was deleted.

    The indexing consumer may queue for a parse slot twice (before the download and before the parse), each wait up
    to ``RECORD_PROCESSING_TIMEOUT``, and the record's budget is paused meanwhile. Everything else the record does
    before the request (the download, the retries of a full gate, a fall back to another source) spends the budget, so
    the request is sent within the whole longest budget; the parsing route can then hold it for the admission gate and
    the memory wait before ``parse`` starts.
    """
    record, longest = _raw_budgets()
    window = PARSE_SLOT_WAITS * record + longest + PARSE_GATE_WAIT_S + PARSE_MEMORY_WAIT_S
    return float(-(-window // REPURGE_MARGIN_S) * REPURGE_MARGIN_S)


def repurge_horizon_s() -> float:
    """The chain ends once this long has passed since the delete, computed from the live configuration: the latest a
    stage can begin, the longest it may run, the last write's deadline, a lock's life and a margin."""
    # The unclamped budgets: the consumer admits (and a write can land) under what is configured, so the horizon
    # covers that even where the stage deadline is clamped.
    longest = max(float(STAGE_DEADLINE_S), _raw_budgets()[1])
    return admission_window_s() + longest + WRITE_DEADLINE_S + LOCK_TTL_MS // 1000 + REPURGE_MARGIN_S


def lock_name(org_id: str, record_id: str, digest: str) -> str:
    # One hash tag per (org, record), so every key of a record sits on one cluster slot.
    return f"pc:lock:{{{org_id}:{record_id}}}:{digest}"


def deleted_name(org_id: str, record_id: str | None = None) -> str:
    """The marker of a deleted record, or (no record) of a deleted org."""
    return f"pc:deleted:{{{org_id}:{record_id}}}" if record_id is not None else f"pc:deleted:{{{org_id}}}"


def manifest_lock_name(org_id: str, record_id: str, path: str) -> str:
    """The lock one replica holds while it reads, merges and replaces a manifest another replica may also write."""
    return f"pc:mlock:{{{org_id}:{record_id}}}:{path}"


def refresh_name(org_id: str, record_id: str, event_id: str) -> str:
    """The marker that a refresh event already purged its record's results (a redelivery must not purge again)."""
    return f"pc:refresh:{{{org_id}:{record_id}}}:{event_id}"


def refresh_done_name(org_id: str, record_id: str, event_id: str) -> str:
    """The marker that a refresh run under ``event_id`` has ended (a second event of the same refresh is a no-op)."""
    return f"pc:refreshdone:{{{org_id}:{record_id}}}:{event_id}"


def refresh_last_name(org_id: str, record_id: str) -> str:
    """The marker holding the request time of the newest refresh that ran on the record (an older request's late event is refused)."""
    return f"pc:refreshlast:{{{org_id}:{record_id}}}"


class LockBackend(Protocol):
    #: True when every replica sees this backend's locks and markers (Redis); False for one process's own.
    shared: bool

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        """A token when this caller now holds the lock, None when another does. Raises when unreachable."""

    async def renew(self, name: str, token: str, ttl_ms: int) -> bool: ...

    async def owns(self, name: str, token: str) -> bool:
        """True while this token still holds the lock (one atomic compare). Raises when unreachable."""

    async def release(self, name: str, token: str) -> None: ...

    async def mark(self, name: str, ttl_ms: int, value: str = "1") -> None:
        """Sets a marker that reads as set until it lapses. ``value`` is what ``marker_value`` reads back (a delete
        stores its time in ms; a marker of an older release holds ``1``)."""

    async def mark_if_owned(self, name: str, ttl_ms: int, lock_name: str, token: str) -> bool:
        """Sets the marker only while ``token`` still holds ``lock_name``, in one atomic step; False when it does not."""

    async def is_marked(self, name: str) -> bool:
        """Raises when unreachable: a caller that cannot tell must not assume the marker is absent."""

    async def marker_value(self, name: str) -> str | None:
        """The marker's value, None when it is not set. One single-key read. Raises when unreachable."""


class RedisLocks:
    """Keys are ``<ns>:pc:...``, the namespace of the messaging client (``REDIS_KEY_NAMESPACE``), when it is set."""

    shared = True

    def __init__(self, client: "Redis", key_namespace: str = "") -> None:
        self._client = client
        self._prefix = f"{key_namespace}:" if key_namespace else ""

    def key_of(self, name: str) -> str:
        return f"{self._prefix}{name}"

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        token = secrets.token_hex(16)
        return token if await self._client.set(self.key_of(name), token, nx=True, px=ttl_ms) else None

    async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
        return bool(await self._client.eval(_RENEW_LUA, 1, self.key_of(name), token, ttl_ms))  # type: ignore[misc]

    async def owns(self, name: str, token: str) -> bool:
        return bool(await self._client.eval(_OWNS_LUA, 1, self.key_of(name), token))  # type: ignore[misc]

    async def release(self, name: str, token: str) -> None:
        await self._client.eval(_RELEASE_LUA, 1, self.key_of(name), token)  # type: ignore[misc]

    async def mark(self, name: str, ttl_ms: int, value: str = "1") -> None:
        await self._client.set(self.key_of(name), value, px=ttl_ms)

    async def mark_if_owned(self, name: str, ttl_ms: int, lock_name: str, token: str) -> bool:
        return bool(await self._client.eval(  # type: ignore[misc]
            _MARK_IF_OWNED_LUA, 2, self.key_of(lock_name), self.key_of(name), token, ttl_ms,
        ))

    async def is_marked(self, name: str) -> bool:
        return bool(await self._client.exists(self.key_of(name)))

    async def marker_value(self, name: str) -> str | None:
        value = await self._client.get(self.key_of(name))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)


class InProcessLocks:
    """The same contract inside one process: tests, and a deployment whose Redis cannot be reached."""

    shared = False
    #: True only for the stand-in a deployment uses when its Redis cannot be reached: what a refresh with paid-result
    #: writes on refuses, because no other worker can see its markers or leases. Tests use the plain object.
    degraded = False

    def __init__(self) -> None:
        self._held: dict[str, tuple[str, float]] = {}
        self._values: dict[str, str] = {}
        self._clock = time.monotonic
        #: Event loops on several threads share one instance (the fallback of a Redis that cannot be reached).
        self._mutex = threading.Lock()

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        with self._mutex:
            now = self._clock()
            held = self._held.get(name)
            if held is not None and held[1] > now:
                return None
            token = secrets.token_hex(16)
            self._held[name] = (token, now + ttl_ms / 1000)
            return token

    async def renew(self, name: str, token: str, ttl_ms: int) -> bool:
        with self._mutex:
            held = self._held.get(name)
            if held is None or held[0] != token:
                return False
            self._held[name] = (token, self._clock() + ttl_ms / 1000)
            return True

    async def owns(self, name: str, token: str) -> bool:
        with self._mutex:
            held = self._held.get(name)
            return held is not None and held[0] == token and held[1] > self._clock()

    async def release(self, name: str, token: str) -> None:
        with self._mutex:
            held = self._held.get(name)
            if held is not None and held[0] == token:
                del self._held[name]

    async def mark(self, name: str, ttl_ms: int, value: str = "1") -> None:
        with self._mutex:
            self._held[name] = ("marker", self._clock() + ttl_ms / 1000)
            self._values[name] = value

    async def mark_if_owned(self, name: str, ttl_ms: int, lock_name: str, token: str) -> bool:
        with self._mutex:
            held = self._held.get(lock_name)
            if held is None or held[0] != token or held[1] <= self._clock():
                return False
            self._held[name] = ("marker", self._clock() + ttl_ms / 1000)
            return True

    async def is_marked(self, name: str) -> bool:
        with self._mutex:
            held = self._held.get(name)
            return held is not None and held[1] > self._clock()

    async def marker_value(self, name: str) -> str | None:
        with self._mutex:
            held = self._held.get(name)
            if held is None or held[0] != "marker" or held[1] <= self._clock():
                return None
            return self._values.get(name, "1")

    def expire_all(self) -> None:
        """Test hook: every held lock lapses, as a dead holder's does (markers stay)."""
        with self._mutex:
            self._held = {n: h for n, h in self._held.items() if h[0] == "marker"}


