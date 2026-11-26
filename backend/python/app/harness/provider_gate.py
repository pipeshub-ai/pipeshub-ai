"""One limit per upstream for every harness model call in the deployment (G-45), shared over Redis.

The model gateway and each worker process call the same providers: engine runs through the gateway, loop runs from
the worker itself. A limit each process kept for itself would be multiplied by the number of processes (one gateway
plus every worker replica), and a gateway full of background engine calls would never see the turn a worker was
about to send. So the limit, its queue and its order live in Redis, as the vendor rate budget's bucket does
(``app.services.rate_budget``): every process asks the same Lua script for a slot, and the script serves the
highest class first, oldest first within a class, every ``floor_every``-th grant to the oldest waiter of any
class, and keeps ``limit // 4`` slots from background and batch, exactly as the in-process ``PriorityGate``.

A caller that finds no slot is queued in Redis and asks again, sooner when a slot of this process frees; a queued
caller that stops asking (its process died) leaves the queue after ``beat_s``. A slot is held for ``hold_s`` and
renewed while its call runs, up to ``max_hold_s``, so a crashed process gives its slots back within ``hold_s``.

A limit is keyed by the unit an upstream rate-limits (``upstream_key``): the provider, a digest of the account the
call is served on and a digest of its endpoint or region, never the credential or the host itself. Two orgs on their
own accounts never queue behind each other; every run on one account shares its limit, whichever process calls it.
Inside a key the order is as above (class, the floor turn, the reserve), and within a class a slot goes to the
waiting call of the org holding fewest of the key's slots, oldest first among equals (WP56-FAIR): a deployment
that serves many orgs on one account has one limit for all of them, and one org's burst must not queue every other
org's call behind all of it. The org is a digest in the waiter's and the slot's id, never the org id. A configuration
naming neither an account nor an endpoint keeps the provider's key. The keys before (``harness:provider-gate:
{provider}``) are not migrated: a process of this release never touches them, and their ``PEXPIRE`` ttl lets them go.

Redis unreachable fails over to this process's own ``PriorityGate`` with the same limit, and Redis is not asked
again for a while: calls keep their class order inside each process, and only the cross-process sum is lost.
A process that starts while its Redis is not answering yet joins the shared limit once it does. Without Redis at all
(an etcd and Kafka deployment) the process gate is all there is.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from redis.exceptions import NoScriptError

from app.core.org_context import spawn_detached, spawn_in_request
from harness_core.gateway.priority_gate import (
    DEFAULT_FLOOR_EVERY,
    DEFAULT_LIMIT,
    DEFAULT_MAX_HOLD_S,
    PriorityGate,
)
from harness_core.logs import HarnessLog
from harness_core.priority import DEFAULT_CLASS, preemptible, priority_rank

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from app.config.configuration_service import ConfigurationService
    from app.services.redis.connection_provider import IRedisConnectionProvider
    from harness_core.clock import Clock
    from harness_core.gateway.priority_gate import AdmissionSlot, ProviderAdmission
    from harness_core.priority import PriorityClass

ENV_PROVIDER_CONCURRENCY: Final = "MODEL_GATEWAY_PROVIDER_CONCURRENCY"
KEY_PREFIX: Final = "harness:provider-gate"
DEFAULT_HOLD_S: Final = 30.0
DEFAULT_BEAT_S: Final = 5.0
POLL_MIN_S: Final = 0.02
POLL_MAX_S: Final = 0.25
OUTAGE_SKIP_S: Final = 30.0
# How far down the class-ordered queue one ask looks for the fair choice: a waiter past it waits for the queue ahead
# of it to shorten, as it did before fairness.
FAIR_SCAN: Final = 256
_ORG_DOMAIN: Final = b"pipeshub.provider-gate.org.v1"
_LOG = HarnessLog.named("app.harness.provider_gate")

# KEYS: held (slot -> expiry ms), queue (waiter -> rank * 1e13 + seq), since (waiter -> seq), beat (waiter -> expiry
# ms), class (waiter -> "1" if preemptible), state (grants, seq). ARGV: waiter, slot, rank, preemptible, limit,
# reserve, floor_every, hold_ms, beat_ms, now_ms (-1: Redis TIME), ttl_ms, scan. Returns {granted, held after}.
# The free room is shared among the first waiters of the class-ordered queue, so every free slot reaches a waiter at
# its next ask, not one per poll interval behind the head. A floor turn gives the room's first slot to the oldest
# waiter of any class. The rest of the room goes, one slot at a time, to the waiter first by (class, slots its org
# holds and has been given in this pass, age) among the first SCAN of the queue (WP56-FAIR); a waiter's and a slot's
# org is the digest before the "|" of its id. The reserve is judged at each grant: every class a person waits on
# ranks before every preemptible one, so once the reserve blocks a waiter nothing after it may pass either.
_ACQUIRE = """
local held_k, queue_k, since_k, beat_k, cls_k, state_k = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5], KEYS[6]
local me, slot = ARGV[1], ARGV[2]
local rank = tonumber(ARGV[3])
local limit = tonumber(ARGV[5])
local reserve = tonumber(ARGV[6])
local floor_every = tonumber(ARGV[7])
local now = tonumber(ARGV[10])
local ttl_ms = tonumber(ARGV[11])
if now < 0 then
    local t = redis.call("TIME")
    now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
redis.call("ZREMRANGEBYSCORE", held_k, "-inf", now)
local dead = redis.call("ZRANGEBYSCORE", beat_k, "-inf", now)
for _, w in ipairs(dead) do
    redis.call("ZREM", queue_k, w)
    redis.call("ZREM", since_k, w)
    redis.call("ZREM", beat_k, w)
    redis.call("HDEL", cls_k, w)
end
if redis.call("ZSCORE", since_k, me) == false then
    local seq = redis.call("HINCRBY", state_k, "seq", 1)
    redis.call("ZADD", queue_k, rank * 10000000000000 + seq, me)
    redis.call("ZADD", since_k, seq, me)
    redis.call("HSET", cls_k, me, ARGV[4])
end
redis.call("ZADD", beat_k, now + tonumber(ARGV[9]), me)
local held = redis.call("ZCARD", held_k)
local granted = 0
if held < limit then
    local room = limit - held
    local grants = tonumber(redis.call("HGET", state_k, "grants") or "0")
    local floor_head = nil
    if floor_every > 0 and (grants + 1) % floor_every == 0 then
        floor_head = redis.call("ZRANGE", since_k, 0, 0)[1]
    end
    local mine = false
    if floor_head == me then
        mine = true
    else
        local function org_of(m)
            local bar = string.find(m, "|", 1, true)
            if bar then
                return string.sub(m, 1, bar - 1)
            end
            return ""
        end
        local counts = {}
        for _, s in ipairs(redis.call("ZRANGE", held_k, 0, -1)) do
            local o = org_of(s)
            counts[o] = (counts[o] or 0) + 1
        end
        local queued = redis.call("ZRANGE", queue_k, 0, tonumber(ARGV[12]) - 1, "WITHSCORES")
        local cands = {}
        for i = 1, #queued, 2 do
            local m = queued[i]
            if m ~= floor_head then
                local score = tonumber(queued[i + 1])
                local r = math.floor(score / 10000000000000)
                cands[#cands + 1] = {m = m, rank = r, seq = score - r * 10000000000000, org = org_of(m)}
            end
        end
        local taken = 0
        if floor_head ~= nil then
            taken = 1
        end
        while taken < room do
            local best, best_i = nil, 0
            for i, c in ipairs(cands) do
                if not c.done then
                    if best == nil or c.rank < best.rank
                        or (c.rank == best.rank and ((counts[c.org] or 0) < (counts[best.org] or 0)
                            or ((counts[c.org] or 0) == (counts[best.org] or 0) and c.seq < best.seq))) then
                        best, best_i = c, i
                    end
                end
            end
            if best == nil then
                break
            end
            if redis.call("HGET", cls_k, best.m) == "1" and room - taken <= reserve then
                break
            end
            if best.m == me then
                mine = true
                break
            end
            best.done = true
            counts[best.org] = (counts[best.org] or 0) + 1
            taken = taken + 1
        end
    end
    if mine then
        redis.call("ZREM", queue_k, me)
        redis.call("ZREM", since_k, me)
        redis.call("ZREM", beat_k, me)
        redis.call("HDEL", cls_k, me)
        redis.call("ZADD", held_k, now + tonumber(ARGV[8]), slot)
        redis.call("HINCRBY", state_k, "grants", 1)
        granted = 1
    end
end
for _, k in ipairs(KEYS) do
    redis.call("PEXPIRE", k, ttl_ms)
end
return {granted, held + granted}
"""

# KEYS: queue, since, beat, class. ARGV: waiter.
_LEAVE = """
redis.call("ZREM", KEYS[1], ARGV[1])
redis.call("ZREM", KEYS[2], ARGV[1])
redis.call("ZREM", KEYS[3], ARGV[1])
redis.call("HDEL", KEYS[4], ARGV[1])
return 1
"""

# KEYS: held. ARGV: now_ms (-1: Redis TIME), hold_ms, then the slots to renew. Only slots still held are renewed.
_RENEW = """
local now = tonumber(ARGV[1])
if now < 0 then
    local t = redis.call("TIME")
    now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local until_ms = now + tonumber(ARGV[2])
local renewed = 0
for i = 3, #ARGV do
    renewed = renewed + redis.call("ZADD", KEYS[1], "XX", "CH", until_ms, ARGV[i])
end
return renewed
"""

# KEYS: held. ARGV: slot.
_RELEASE = """
return redis.call("ZREM", KEYS[1], ARGV[1])
"""


@dataclass(eq=False)
class SharedSlot:
    """A slot in the deployment's limit; ``release`` is idempotent and gives it back in the background."""

    _gate: RedisProviderGate = field(repr=False)
    key: str
    slot_id: str
    taken_at: float
    waited: bool = False
    released: bool = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self._gate._give_back(self)  # pyright: ignore[reportPrivateUsage]


class RedisProviderGate:
    """``ProviderAdmission`` for the whole deployment, over the Redis every harness process already reaches."""

    def __init__(
        self,
        redis: IRedisConnectionProvider,
        limit: int = DEFAULT_LIMIT,
        *,
        monotonic: Callable[[], float],
        floor_every: int = DEFAULT_FLOOR_EVERY,
        max_hold_s: float = DEFAULT_MAX_HOLD_S,
        hold_s: float = DEFAULT_HOLD_S,
        beat_s: float = DEFAULT_BEAT_S,
        sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
        now_ms: Callable[[], int] | None = None,
        fallback: PriorityGate | None = None,
    ) -> None:
        """``now_ms``: the time the scripts judge expiry by; unset, Redis's own clock, so the processes' clocks
        never disagree about it. ``fallback``: the process's own gate while Redis is unreachable."""
        self._redis = redis
        self._limit = max(0, limit)
        self._reserve = self._limit // 4
        self._floor_every = max(0, floor_every)
        self._max_hold_s = max_hold_s
        self._hold_ms = int(hold_s * 1000)
        self._beat_ms = int(beat_s * 1000)
        self._renew_every_s = hold_s / 3
        self._monotonic = monotonic
        self._sleep = sleep
        self._now_ms = now_ms
        self._fallback = fallback or PriorityGate(self._limit, floor_every=floor_every, max_hold_s=max_hold_s,
                                                  monotonic=monotonic)
        self._shas: dict[str, str] = {}
        self._held: dict[str, SharedSlot] = {}
        self._freed: dict[str, asyncio.Event] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._renewer: asyncio.Task[None] | None = None
        self._skip_until = float("-inf")
        # Whether the provider's connection was set up; a gate built while Redis was down sets it up on its next ask.
        self._ready = True

    @property
    def limit(self) -> int:
        return self._limit

    def held_here(self, key: str) -> int:
        return sum(1 for slot in self._held.values() if slot.key == key)

    def _keys(self, key: str) -> list[str]:
        namespace = self._redis.key_namespace
        prefix = f"{namespace}:" if namespace else ""
        # One hash tag per upstream key: every key a script touches is in one cluster slot.
        base = f"{prefix}{KEY_PREFIX}:{{{key}}}"
        return [f"{base}:{name}" for name in ("held", "queue", "since", "beat", "class", "state")]

    def _now(self) -> int:
        return self._now_ms() if self._now_ms is not None else -1

    async def _eval(self, body: str, keys: list[str], *args: object) -> object:
        if not self._ready:
            await self._redis.prepare()
            self._ready = True
        sha = self._shas.get(body)
        if sha is None:
            sha = self._shas[body] = await self._redis.load_script(body)
        client = self._redis.get_client()
        try:
            return await client.evalsha(sha, len(keys), *keys, *args)
        except NoScriptError:
            sha = self._shas[body] = await self._redis.load_script(body)
            return await client.evalsha(sha, len(keys), *keys, *args)

    def _available(self) -> bool:
        return time.monotonic() >= self._skip_until

    def unreachable(self, what: str, exc: BaseException, *, prepared: bool = True) -> None:
        """Redis did not answer: this process's own gate for a while, then Redis is asked again. ``prepared``
        False: the provider's connection was never set up, and the next ask sets it up first."""
        self._ready = self._ready and prepared
        self._outage(what, exc)

    def _outage(self, what: str, exc: BaseException) -> None:
        self._skip_until = time.monotonic() + OUTAGE_SKIP_S
        _LOG.warning("provider_gate.unavailable", op=what, errorType=type(exc).__name__, localFor=OUTAGE_SKIP_S)

    async def acquire(
        self, key: str, priority_class: PriorityClass | None, *, org: str | None = None
    ) -> AdmissionSlot:
        if self._limit == 0 or not self._available():
            return await self._fallback.acquire(key, priority_class, org=org)
        cls = priority_class or DEFAULT_CLASS
        keys = self._keys(key)
        tag = _org_tag(org)
        waiter, slot_id = f"{tag}|{uuid.uuid4().hex}", f"{tag}|{uuid.uuid4().hex}"
        args = (waiter, slot_id, priority_rank(cls), "1" if preemptible(cls) else "0", self._limit, self._reserve,
                self._floor_every, self._hold_ms, self._beat_ms)
        delay = POLL_MIN_S
        waited = False
        try:
            while True:
                try:
                    answer = await self._eval(_ACQUIRE, keys, *args, self._now(), self._ttl_ms(), FAIR_SCAN)
                except Exception as exc:  # any Redis failure: this process's own gate, see the module docstring
                    self._outage("acquire", exc)
                    return await self._fallback.acquire(key, priority_class, org=org)
                if _granted(answer):
                    slot = SharedSlot(self, key, slot_id, self._monotonic(), waited=waited)
                    self._held[slot_id] = slot
                    self._renew_soon()
                    return slot
                waited = True
                await self._until_freed(key, delay)
                delay = min(POLL_MAX_S, delay * 1.5)
        except asyncio.CancelledError:
            # Queued by the first ask even when it was cut off: leave now rather than when the beat runs out.
            self._spawn(self._leave(keys, waiter))
            raise

    def _ttl_ms(self) -> int:
        return 4 * max(self._hold_ms, self._beat_ms, 60_000)

    async def _until_freed(self, key: str, delay: float) -> None:
        """Until ``delay`` has passed or a slot of this provider was given back here, whichever is first."""
        freed = self._freed.setdefault(key, asyncio.Event())
        waiting = spawn_in_request(freed.wait())
        sleeping = spawn_in_request(self._sleep(delay))
        try:
            _ = await asyncio.wait({waiting, sleeping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiting, sleeping):
                _ = task.cancel()

    async def _leave(self, keys: list[str], waiter: str) -> None:
        with contextlib.suppress(Exception):
            _ = await self._eval(_LEAVE, keys[1:5], waiter)

    def _give_back(self, slot: SharedSlot) -> None:
        if self._held.pop(slot.slot_id, None) is None:
            return
        self._spawn(self._release(slot))

    async def _release(self, slot: SharedSlot) -> None:
        try:
            _ = await self._eval(_RELEASE, self._keys(slot.key)[:1], slot.slot_id)
        except Exception as exc:
            # The slot's hold runs out instead: it is not renewed any more.
            _LOG.warning("provider_gate.release_failed", errorType=type(exc).__name__)
        freed = self._freed.pop(slot.key, None)
        if freed is not None:
            freed.set()

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        # The gate is the process's: a give-back outlives the call that made it and acts for no org.
        task = spawn_detached(work)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _renew_soon(self) -> None:
        if self._renewer is None or self._renewer.done():
            self._renewer = spawn_detached(self._renew())

    async def _renew(self) -> None:
        while self._held:
            await self._sleep(self._renew_every_s)
            now = self._monotonic()
            by_key: dict[str, list[str]] = {}
            for slot in list(self._held.values()):
                if now - slot.taken_at >= self._max_hold_s:
                    # Never given back: its hold runs out in Redis as a crashed process's would.
                    _ = self._held.pop(slot.slot_id, None)
                    continue
                by_key.setdefault(slot.key, []).append(slot.slot_id)
            for key, slots in by_key.items():
                try:
                    _ = await self._eval(_RENEW, self._keys(key)[:1], self._now(), self._hold_ms, *slots)
                except Exception as exc:
                    _LOG.warning("provider_gate.renew_failed", errorType=type(exc).__name__)

    async def aclose(self) -> None:
        if self._renewer is not None:
            _ = self._renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewer
        if self._pending:
            _ = await asyncio.gather(*self._pending, return_exceptions=True)


def _org_tag(org: str | None) -> str:
    """The org as the shared gate's ids carry it: a short digest, so Redis never holds the org id."""
    if not org:
        return "-"
    return hashlib.sha256(_ORG_DOMAIN + b"\0" + org.encode("utf-8")).hexdigest()[:12]


def _granted(answer: object) -> bool:
    return isinstance(answer, (list, tuple)) and len(answer) > 0 and int(answer[0]) == 1  # pyright: ignore[reportUnknownArgumentType]


def provider_limit() -> int:
    """``MODEL_GATEWAY_PROVIDER_CONCURRENCY`` (G-45): calls one upstream (a provider account at one endpoint or
    region) takes from the whole deployment at once, engine and loop runs together; 0 is no limit. Unset or not a whole number: the default. Every harness process
    reads it, so set it alike on the gateway and the workers."""
    raw = os.getenv(ENV_PROVIDER_CONCURRENCY, "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_LIMIT


async def provider_admission(config_service: ConfigurationService, clock: Clock) -> ProviderAdmission:
    """The process's admission: the deployment's shared gate over its Redis, else this process's own."""
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    limit = provider_limit()
    local = PriorityGate(limit, monotonic=clock.monotonic)
    if limit == 0:
        return local
    try:
        redis = get_redis_provider(RedisConnectionConfig.from_redis_config(await config_service.get_redis_config()))
    except Exception as exc:
        _LOG.info("provider_gate.local", reason="no_redis", errorType=type(exc).__name__, limit=limit)
        return local
    gate = RedisProviderGate(redis, limit, monotonic=clock.monotonic, fallback=local)
    try:
        await redis.prepare()
        _ = await redis.get_client().ping()  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        # Configured but not answering yet (a Redis restarting with the stack): the shared gate still, which uses
        # this process's own until Redis answers and then joins the deployment's limit, as after any outage.
        gate.unreachable("startup", exc, prepared=False)
        return gate
    _LOG.info("provider_gate.shared", limit=limit)
    return gate


__all__ = [
    "ENV_PROVIDER_CONCURRENCY",
    "RedisProviderGate",
    "SharedSlot",
    "provider_admission",
    "provider_limit",
]
