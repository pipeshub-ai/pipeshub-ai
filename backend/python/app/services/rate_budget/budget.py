"""One rate budget per (org, vendor family), shared by connector sync, indexing fetches and agent tool calls (G-31).

The budget is a token bucket in Redis, so every process of the connectors and query services draws from the same
one; a local limiter per process would multiply the vendor's limit by the number of processes. Priority is a
reserve each class must leave in the bucket (an interactive call can take the last token, a sync cannot take the
last quarter, a background agent the last half) and a floor each lower class is owed while it waits
(``BudgetLimits``), so interactive calls go first and nothing starves. A throttled call anywhere arms one pause for
the whole account, so a single 429 is not followed by one 429 per caller.

Redis being unreachable fails open: each side's own limiter (``ResiliencePolicy``, Slack's tier limiter) still
paces it, as before the budget existed, and the budget is skipped for a while rather than retried on every call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from redis.exceptions import NoScriptError

from app.core.org_context import spawn_in_request
from app.services.rate_budget.families import (
    BudgetLimits,
    RatePriority,
    limits_for,
)
from app.services.redis.connection_provider_factory import get_redis_provider
from harness_core.effects.failures import ToolFailure, retry_after_s, tool_error_code

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from app.services.redis.connection_provider import IRedisConnectionProvider

logger = logging.getLogger(__name__)

ENABLED_ENV = "CONNECTOR_RATE_BUDGET_ENABLED"
KEY_PREFIX = "pipeshub:ratebudget"
# A pause longer than this is the vendor asking for a quota reset, not a throttle; the callers' own retry handles it.
MAX_PAUSE_S = 300.0
# When a throttled call names no Retry-After.
DEFAULT_PAUSE_S = 2.0
_STATE_TTL_MS = 600_000
# Tokens of floor a waiting class may bank: enough to be served next, too few to burst past the classes above it.
_CREDIT_CAP = 2.0
_OUTAGE_SKIP_S = 30.0

# Returns 0 when a token was taken, else the milliseconds until one could be. ``cs``/``cb`` are the sync and
# background classes' credit, earned at their floor rate (capped at _CREDIT_CAP); ``ws``/``wb`` say until when a
# refused caller of that class is known to be waiting. A caller with credit takes as an interactive one would, and
# while a lower class waits with credit, every class above it leaves it one token.
_TAKE_SCRIPT = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local me = ARGV[3]
local reserve = tonumber(ARGV[4])
local floor_s = tonumber(ARGV[5])
local floor_b = tonumber(ARGV[6])
local cap = tonumber(ARGV[7])
local ttl_ms = tonumber(ARGV[8])
local t = redis.call("TIME")
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local st = redis.call("HMGET", key, "tokens", "ts", "pause", "cs", "cb", "ws", "wb")
local pause = tonumber(st[3]) or 0
if pause > now then
    return pause - now
end
local tokens = tonumber(st[1])
local ts = tonumber(st[2])
if tokens == nil or ts == nil then
    tokens = burst
    ts = now
end
local dt = math.max(0, now - ts) / 1000
tokens = math.min(burst, tokens + dt * rate)
local cs = math.min(cap, (tonumber(st[4]) or 0) + dt * floor_s)
local cb = math.min(cap, (tonumber(st[5]) or 0) + dt * floor_b)
local ws = tonumber(st[6]) or 0
local wb = tonumber(st[7]) or 0
local held = 0
local credit = 0
local floor = 0
if me == "interactive" then
    if ws > now and cs >= 1 then held = held + 1 end
    if wb > now and cb >= 1 then held = held + 1 end
elseif me == "sync" then
    credit = cs
    floor = floor_s
    if wb > now and cb >= 1 then held = held + 1 end
else
    credit = cb
    floor = floor_b
end
local need = reserve
if credit >= 1 then need = 0 end
if held > need then need = held end
local wait_ms = 0
if tokens - 1 >= need then
    tokens = tokens - 1
    if me == "sync" and cs >= 1 then cs = cs - 1 end
    if me == "background" and cb >= 1 then cb = cb - 1 end
else
    local eta = (need + 1 - tokens) / rate
    if credit < 1 and floor > 0 then
        local with_credit = math.max((1 - credit) / floor, (held + 1 - tokens) / rate)
        eta = math.min(eta, with_credit)
    end
    wait_ms = math.max(1, math.ceil(eta * 1000))
    if me == "sync" then ws = now + wait_ms + 250 end
    if me == "background" then wb = now + wait_ms + 250 end
end
redis.call("HSET", key, "tokens", tostring(tokens), "ts", tostring(now), "cs", tostring(cs), "cb", tostring(cb),
    "ws", tostring(ws), "wb", tostring(wb))
redis.call("PEXPIRE", key, ttl_ms)
return wait_ms
"""

_PAUSE_SCRIPT = """
local key = KEYS[1]
local pause_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local t = redis.call("TIME")
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local current = tonumber(redis.call("HGET", key, "pause")) or 0
local resume = now + pause_ms
if resume > current then
    redis.call("HSET", key, "pause", tostring(resume))
end
redis.call("PEXPIRE", key, math.max(ttl_ms, pause_ms + 1000))
return 1
"""


@dataclass(frozen=True, slots=True)
class RateAccount:
    org_id: str
    family: str


class RateBudgetWait(ToolFailure):
    """An agent call that would wait longer than its share allows: answered ``rate_limited`` with the wait, so the
    tool executor or the engine decides whether to come back."""

    def __init__(self, retry_after_s: float, family: str) -> None:
        super().__init__(
            "rate_limited",
            f"The {family} account is at its shared rate limit; try again in {retry_after_s:.0f}s.",
        )
        self.retry_after_s = retry_after_s


class IRateBudget(ABC):
    @abstractmethod
    async def take(self, account: RateAccount, limits: BudgetLimits, priority: RatePriority) -> float:
        """Take one token at ``priority``: 0 when taken, else the seconds until one could be."""

    @abstractmethod
    async def pause(self, account: RateAccount, seconds: float) -> None:
        """Hold every caller of the account back for ``seconds``; never shortens a pause already armed."""


class RedisRateBudget(IRateBudget):
    def __init__(self, provider: IRedisConnectionProvider | None = None) -> None:
        self._provider = provider
        self._shas: dict[str, str] = {}
        self._skip_until = 0.0

    def _redis(self) -> IRedisConnectionProvider:
        if self._provider is None:
            self._provider = get_redis_provider()
        return self._provider

    def _key(self, account: RateAccount) -> str:
        namespace = self._redis().key_namespace
        prefix = f"{namespace}:" if namespace else ""
        return f"{prefix}{KEY_PREFIX}:{account.org_id}:{account.family}"

    async def _eval(self, body: str, key: str, *args: object) -> int:
        provider = self._redis()
        sha = self._shas.get(body)
        if sha is None:
            sha = self._shas[body] = await provider.load_script(body)
        client = provider.get_client()
        try:
            result = await client.evalsha(sha, 1, key, *args)
        except NoScriptError:
            sha = self._shas[body] = await provider.load_script(body)
            result = await client.evalsha(sha, 1, key, *args)
        return int(result)

    def _available(self) -> bool:
        return time.monotonic() >= self._skip_until

    def _outage(self, what: str, exc: BaseException) -> None:
        self._skip_until = time.monotonic() + _OUTAGE_SKIP_S
        logger.warning(
            "rate_budget.unavailable op=%s error=%s; each caller's own limiter paces it for %ss",
            what, type(exc).__name__, _OUTAGE_SKIP_S,
        )

    @override
    async def take(self, account: RateAccount, limits: BudgetLimits, priority: RatePriority) -> float:
        if not self._available():
            return 0.0
        try:
            wait_ms = await self._eval(
                _TAKE_SCRIPT,
                self._key(account),
                limits.rate,
                limits.burst,
                priority.value,
                limits.reserve(priority),
                limits.floor(RatePriority.SYNC),
                limits.floor(RatePriority.BACKGROUND),
                _CREDIT_CAP,
                _STATE_TTL_MS,
            )
        except Exception as exc:  # any Redis failure fails open, see the module docstring
            self._outage("take", exc)
            return 0.0
        return wait_ms / 1000.0

    @override
    async def pause(self, account: RateAccount, seconds: float) -> None:
        if seconds <= 0 or not self._available():
            return
        pause_ms = int(min(seconds, MAX_PAUSE_S) * 1000)
        try:
            _ = await self._eval(_PAUSE_SCRIPT, self._key(account), pause_ms, _STATE_TTL_MS)
        except Exception as exc:  # as take()
            self._outage("pause", exc)


_priority: ContextVar[RatePriority | None] = ContextVar("rate_priority", default=None)


def current_priority(default: RatePriority = RatePriority.INTERACTIVE) -> RatePriority:
    return _priority.get() or default


@contextmanager
def rate_priority(priority: RatePriority) -> Generator[None]:
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


class AccountBudget:
    """One account's budget, as a caller holds it."""

    def __init__(self, budget: IRateBudget, account: RateAccount, limits: BudgetLimits) -> None:
        self.budget = budget
        self.account = account
        self.limits = limits
        self._pending: set[asyncio.Task[None]] = set()

    async def acquire(self, priority: RatePriority | None = None, *, max_wait_s: float | None = None) -> float:
        """Wait for one token; returns the seconds waited. With ``max_wait_s``, a wait beyond it raises
        ``RateBudgetWait`` instead of being waited."""
        chosen = priority or current_priority()
        started = time.monotonic()
        waited = 0.0
        while True:
            wait = await self.budget.take(self.account, self.limits, chosen)
            if wait <= 0:
                return waited
            if max_wait_s is not None and waited + wait > max_wait_s:
                raise RateBudgetWait(wait, self.account.family)
            # Callers released by the same refill would otherwise all ask again on the same tick.
            await asyncio.sleep(wait + random.uniform(0, min(0.05, wait * 0.1)))
            waited = time.monotonic() - started

    async def pause(self, seconds: float | None) -> None:
        await self.budget.pause(self.account, DEFAULT_PAUSE_S if seconds is None else seconds)

    def pause_soon(self, seconds: float | None) -> None:
        """``pause`` for synchronous callers on the event loop (``ResiliencePolicy.pause``); a no-op off it."""
        try:
            _ = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = spawn_in_request(self.pause(seconds))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)


_account: ContextVar[AccountBudget | None] = ContextVar("rate_account", default=None)
# The loop the scope was entered on: a blocking client (googleapiclient) sees its 429 on a worker thread, which
# carries the context but cannot reach Redis itself.
_account_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar("rate_account_loop", default=None)


@contextmanager
def account_scope(budget: AccountBudget | None) -> Generator[None]:
    """Name the account the calls in this block spend, for code below that sees a 429 but not its caller."""
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    token = _account.set(budget if isinstance(budget, AccountBudget) else None)
    loop_token = _account_loop.set(loop)
    try:
        yield
    finally:
        _account_loop.reset(loop_token)
        _account.reset(token)


@contextmanager
def budget_scope(budget: AccountBudget | None, priority: RatePriority) -> Generator[None]:
    """Calls in this block spend ``budget`` at ``priority``: a connector's sync, an indexing fetch."""
    with account_scope(budget), rate_priority(priority):
        yield


async def note_throttled(retry_after_s: float | None) -> None:
    """A call in scope was throttled by the vendor: pause its whole account."""
    budget = _account.get()
    if budget is not None:
        await budget.pause(retry_after_s)


def note_throttled_threadsafe(retry_after_s: float | None) -> None:
    """``note_throttled`` for a blocking client, from a worker thread or from the loop itself: the pause is
    armed on the loop the account's scope was entered on."""
    budget, loop = _account.get(), _account_loop.get()
    if budget is None or loop is None or loop.is_closed():
        return
    try:
        running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        budget.pause_soon(retry_after_s)
    else:
        _ = loop.call_soon_threadsafe(budget.pause_soon, retry_after_s)


def is_throttle(status: int, retry_after_s: float | None) -> bool:
    """A vendor answer that means "slow down": any 429, or a 503 that names when to come back (SharePoint and
    Graph answer an overloaded tenant so). A bare 503 is an outage, which a pause would not help."""
    return status == 429 or (status == 503 and retry_after_s is not None)


@asynccontextmanager
async def budgeted_call(
    budget: AccountBudget, priority: RatePriority, *, max_wait_s: float | None
) -> AsyncGenerator[None]:
    """One agent tool call: a token first, and a vendor throttle it meets pauses the account."""
    _ = await budget.acquire(priority, max_wait_s=max_wait_s)
    with account_scope(budget), rate_priority(priority):
        try:
            yield
        except Exception as exc:
            if not isinstance(exc, RateBudgetWait) and tool_error_code(exc) == "rate_limited":
                await budget.pause(retry_after_s(exc))
            raise


def budget_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "true").strip().lower() not in ("0", "false", "no", "off")


class _Process:
    budget: IRateBudget | None = None


def shared_budget() -> IRateBudget:
    if _Process.budget is None:
        _Process.budget = RedisRateBudget()
    return _Process.budget


def set_shared_budget(budget: IRateBudget | None) -> None:
    """Tests and alternative stores replace the process's budget here."""
    _Process.budget = budget


def account_budget(org_id: str | None, family: str | None) -> AccountBudget | None:
    """The org's budget for a vendor family, or None when the family has none or budgets are off."""
    if not org_id or not family or not budget_enabled():
        return None
    limits = limits_for(family)
    if limits is None:
        return None
    return AccountBudget(shared_budget(), RateAccount(org_id, family), limits)


__all__ = [
    "DEFAULT_PAUSE_S",
    "ENABLED_ENV",
    "MAX_PAUSE_S",
    "AccountBudget",
    "IRateBudget",
    "RateAccount",
    "RateBudgetWait",
    "RedisRateBudget",
    "account_budget",
    "account_scope",
    "budget_enabled",
    "budget_scope",
    "budgeted_call",
    "current_priority",
    "is_throttle",
    "note_throttled",
    "note_throttled_threadsafe",
    "rate_priority",
    "set_shared_budget",
    "shared_budget",
]
