"""Single-flight locks for the paid-result cache: ``<ns>:pc:lock:{<org>:<record>}:<digest>``, and the
delete markers ``<ns>:pc:deleted:{<org>:<record>}`` that fence a deleted record's late writers.

``SET NX PX`` with a random token, renewed while the holder works and released only by its token. A holder
that dies simply lapses. Redis holds nothing else for the cache: results live in the blob store.
"""

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


def _budgets() -> tuple[float, float]:
    """(general record budget, longest budget of any record type), from the environment, live."""
    record = env_seconds("RECORD_PROCESSING_TIMEOUT", 1800.0)
    return record, max(record, env_seconds("PDF_RECORD_PROCESSING_TIMEOUT", 5400.0))


def stage_deadline_s() -> float:
    """How long after its start a stage may still store results and write its manifests, and the most a parse may run.

    The longest record budget configured: a parse the client allows (at most 0.8 of its record's budget) is
    never cut by the route, and the index run that began before it, which holds the whole record's budget, can still
    store its own results. Read at every call, so an operator's raised budget is followed.
    """
    return max(float(STAGE_DEADLINE_S), _budgets()[1])


#: The most a record budget a caller sends can be: a request body is not trusted with an unbounded deadline.
MAX_RECORD_BUDGET_S = 7 * 24 * 3600.0


def stage_deadline_for(record_budget_s: float | None) -> float:
    """The stage deadline for a record whose budget the caller sent with its request.

    The parsing client sends the record budget it applies, so the service uses the very figure the client waits
    for, whatever its own environment says. An absent or unusable figure (an older client during a rollout) falls
    back to the service's own settings (``stage_deadline_s()``).
    """
    if (
        isinstance(record_budget_s, int | float) and not isinstance(record_budget_s, bool)
        and 0 < record_budget_s <= MAX_RECORD_BUDGET_S
    ):
        return max(float(STAGE_DEADLINE_S), float(record_budget_s))
    return stage_deadline_s()


def admission_window_s() -> float:
    """The latest a paid stage can begin after its record was deleted.

    The indexing consumer may queue for a parse slot twice (before the download and before the parse), each wait up
    to ``RECORD_PROCESSING_TIMEOUT``, and the record's budget is paused meanwhile. Everything else the record does
    before the request (the download, the retries of a full gate, a fall back to another source) spends the budget, so
    the request is sent within the whole longest budget; the parsing route can then hold it for the admission gate and
    the memory wait before ``parse`` starts.
    """
    record, longest = _budgets()
    window = PARSE_SLOT_WAITS * record + longest + PARSE_GATE_WAIT_S + PARSE_MEMORY_WAIT_S
    return float(-(-window // REPURGE_MARGIN_S) * REPURGE_MARGIN_S)


def repurge_horizon_s() -> float:
    """The chain ends once this long has passed since the delete, computed from the live configuration: the latest a
    stage can begin, the longest it may run, the last write's deadline, a lock's life and a margin."""
    return admission_window_s() + stage_deadline_s() + WRITE_DEADLINE_S + LOCK_TTL_MS // 1000 + REPURGE_MARGIN_S


def lock_name(org_id: str, record_id: str, digest: str) -> str:
    # One hash tag per (org, record), so every key of a record sits on one cluster slot.
    return f"pc:lock:{{{org_id}:{record_id}}}:{digest}"


def deleted_name(org_id: str, record_id: str | None = None) -> str:
    """The marker of a deleted record, or (no record) of a deleted org."""
    return f"pc:deleted:{{{org_id}:{record_id}}}" if record_id is not None else f"pc:deleted:{{{org_id}}}"


def refresh_name(org_id: str, record_id: str, event_id: str) -> str:
    """The marker that a refresh event already purged its record's results (a redelivery must not purge again)."""
    return f"pc:refresh:{{{org_id}:{record_id}}}:{event_id}"


class LockBackend(Protocol):
    #: True when every replica sees this backend's locks and markers (Redis); False for one process's own.
    shared: bool

    async def acquire(self, name: str, ttl_ms: int) -> str | None:
        """A token when this caller now holds the lock, None when another does. Raises when unreachable."""

    async def renew(self, name: str, token: str, ttl_ms: int) -> bool: ...

    async def owns(self, name: str, token: str) -> bool:
        """True while this token still holds the lock (one atomic compare). Raises when unreachable."""

    async def release(self, name: str, token: str) -> None: ...

    async def mark(self, name: str, ttl_ms: int) -> None:
        """Sets a marker that reads as set until it lapses."""

    async def is_marked(self, name: str) -> bool:
        """Raises when unreachable: a caller that cannot tell must not assume the marker is absent."""


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

    async def mark(self, name: str, ttl_ms: int) -> None:
        await self._client.set(self.key_of(name), "1", px=ttl_ms)

    async def is_marked(self, name: str) -> bool:
        return bool(await self._client.exists(self.key_of(name)))


class InProcessLocks:
    """The same contract inside one process: tests, and a deployment whose Redis cannot be reached."""

    shared = False

    def __init__(self) -> None:
        self._held: dict[str, tuple[str, float]] = {}
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

    async def mark(self, name: str, ttl_ms: int) -> None:
        with self._mutex:
            self._held[name] = ("marker", self._clock() + ttl_ms / 1000)

    async def is_marked(self, name: str) -> bool:
        with self._mutex:
            held = self._held.get(name)
            return held is not None and held[1] > self._clock()

    def expire_all(self) -> None:
        """Test hook: every held lock lapses, as a dead holder's does (markers stay)."""
        with self._mutex:
            self._held = {n: h for n, h in self._held.items() if h[0] == "marker"}


