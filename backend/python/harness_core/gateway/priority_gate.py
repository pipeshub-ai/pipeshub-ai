"""Each provider's model calls in flight, served by priority class (G-45).

A burst of scheduled or batch runs could hold every call a provider takes while a person's turn waits behind them.
Every harness model call is admitted here first: an engine's through the model gateway, a loop run's own in-process
calls through ``AdmittedTransport``, both with the run's class. ``ProviderAdmission`` is the one seam; this
module's ``PriorityGate`` holds it within one process, and the deployment's shared gate (``app.harness.provider_gate``)
holds one limit across the gateway and every worker process over Redis, so a process count never multiplies it.

Each provider has ``limit`` calls in flight at once. A call that finds none free waits; a freed slot goes to the waiting call of the highest class; within a
class, to the call of the org holding fewest of the key's slots, oldest first among equals (WP56-FAIR: many orgs can
share one upstream account, and one org's burst must not queue every other org's call behind all of it); except that every ``floor_every``-th grant goes to the oldest waiting call of any class, so lower
classes keep moving. Background and batch calls leave ``limit // 4`` slots free for the classes a person waits on,
so a turn that arrives finds one at once; the floor grant ignores that reserve, or it could never reach them.

A slot is held from before the provider is asked until the answer settles. One that is never given back (a stream
its client dropped before reading it) is taken back after ``max_hold_s``, so a leak costs a slot for a while, never
for good.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harness_core.priority import (
    DEFAULT_CLASS,
    PriorityClass,
    preemptible,
    priority_rank,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

DEFAULT_LIMIT = 64
DEFAULT_FLOOR_EVERY = 5
DEFAULT_MAX_HOLD_S = 900.0


def provider_key(provider: str) -> str:
    """One provider's name as every admission writes it: the gateway's route names ``azureopenai`` where a loop
    run's configured model says ``azureOpenAI``, and both must count against the same limit."""
    return provider.strip().lower() or "unknown"


# What names the upstream account a call is billed and rate-limited on, and where it is served: a configured model's
# or a model account's configuration fields. Anything else (the model, a deployment) does not split a limit.
ACCOUNT_FIELDS: tuple[str, ...] = (
    "apiKey", "accessKeyId", "awsAccessKeyId", "serviceAccountJson", "projectId", "project", "organizationId",
)
ENDPOINT_FIELDS: tuple[str, ...] = ("endpoint", "baseUrl", "region", "location")
_DOMAIN = b"pipeshub.provider-gate.v1"


def upstream_key(provider: str, configuration: Mapping[str, object] | None) -> str:
    """The limit a call counts against (G-45): the provider, the account it is served on and its endpoint or region,
    the unit an upstream rate-limits. Both halves are digests, so a key never carries a credential or a host; the
    gateway and the workers derive it from the same configuration, so one account's engine and loop calls share one
    limit and two accounts never do. With neither half present it is ``provider_key(provider)``, the key before."""
    fields = configuration or {}

    def present(names: tuple[str, ...], *, normalise: bool) -> list[str]:
        values: list[str] = []
        for name in names:
            value = fields.get(name)
            text = value.strip() if isinstance(value, str) else ""
            if text:
                values.append(f"{name}={text.lower().rstrip('/') if normalise else text}")
        return values

    account = present(ACCOUNT_FIELDS, normalise=False)
    endpoint = present(ENDPOINT_FIELDS, normalise=True)
    base = provider_key(provider)
    if not account and not endpoint:
        return base
    return f"{base}:a{_digest(b'account', account, 16)}:e{_digest(b'endpoint', endpoint, 12)}"


def _digest(part: bytes, values: list[str], size: int) -> str:
    if not values:
        return "-"
    data = b"\0".join([_DOMAIN, part, *(v.encode("utf-8") for v in values)])
    return hashlib.sha256(data).hexdigest()[:size]


class AdmissionSlot(Protocol):
    """One call's place in its provider's limit. ``release`` is idempotent; ``waited`` says the call queued."""

    @property
    def waited(self) -> bool: ...

    def release(self) -> None: ...


class ProviderAdmission(Protocol):
    """Where every harness model call takes its provider slot before the provider is asked (G-45)."""

    @property
    def limit(self) -> int: ...

    async def acquire(
        self, key: str, priority_class: PriorityClass | None, *, org: str | None = None
    ) -> AdmissionSlot:
        """``org``: the org the call is made for. Orgs sharing one upstream key share its limit fairly (WP56-FAIR);
        unset, the call counts as one org with every other unnamed call."""
        ...


@dataclass(eq=False)
class GateSlot:
    """One call's place in a provider's limit; ``release`` is idempotent."""

    _gate: PriorityGate | None
    key: str
    taken_at: float
    released: bool = False
    waited: bool = False
    org: str = ""

    def release(self) -> None:
        if not self.released:
            self.released = True
            if self._gate is not None:
                self._gate._give_back(self)


@dataclass(order=True)
class _Waiter:
    rank: int
    seq: int
    priority_class: PriorityClass = field(compare=False)
    future: asyncio.Future[GateSlot] = field(compare=False)
    org: str = field(default="", compare=False)


@dataclass
class _Provider:
    held: list[GateSlot] = field(default_factory=list[GateSlot])
    waiting: list[_Waiter] = field(default_factory=list[_Waiter])
    grants: int = 0


class PriorityGate:
    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        *,
        floor_every: int = DEFAULT_FLOOR_EVERY,
        max_hold_s: float = DEFAULT_MAX_HOLD_S,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """``limit`` 0: no limit, every call goes at once (``acquire`` still returns a slot to release). ``sleep``:
        on ``monotonic``'s clock, how the gate waits for a never-returned slot's hold to run out."""
        self._limit = max(0, limit)
        self._reserve = self._limit // 4
        self._floor_every = max(0, floor_every)
        self._max_hold_s = max_hold_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._providers: dict[str, _Provider] = {}
        self._seq = itertools.count()
        self._reapers: dict[str, asyncio.Task[None]] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def in_flight(self, key: str) -> int:
        provider = self._providers.get(key)
        return len(provider.held) if provider is not None else 0

    def waiting(self, key: str) -> int:
        provider = self._providers.get(key)
        return len(provider.waiting) if provider is not None else 0

    async def acquire(self, key: str, priority_class: PriorityClass | None, *, org: str | None = None) -> GateSlot:
        if self._limit == 0:
            return GateSlot(None, key, self._monotonic())
        cls = priority_class or DEFAULT_CLASS
        tenant = org or ""
        provider = self._providers.setdefault(key, _Provider())
        self._reap(provider)
        if not provider.waiting and self._fits(provider, cls, floor=False):
            return self._take(provider, key, tenant)
        waiter = _Waiter(
            priority_rank(cls), next(self._seq), cls, asyncio.get_running_loop().create_future(), tenant
        )
        provider.waiting.append(waiter)
        # A call already waiting may fit now (its reserve no longer binds); serve the queue in order first.
        self._serve(provider, key)
        self._reap_later(provider, key)
        try:
            return await waiter.future
        except asyncio.CancelledError:
            if waiter in provider.waiting:
                provider.waiting.remove(waiter)
            elif waiter.future.done() and not waiter.future.cancelled():
                # Granted as it was cancelled: the slot goes on to the next call.
                waiter.future.result().release()
            raise

    def _fits(self, provider: _Provider, cls: PriorityClass, *, floor: bool) -> bool:
        room = self._limit - len(provider.held)
        if room <= 0:
            return False
        return floor or not preemptible(cls) or room > self._reserve

    def _take(self, provider: _Provider, key: str, org: str, *, waited: bool = False) -> GateSlot:
        slot = GateSlot(self, key, self._monotonic(), waited=waited, org=org)
        provider.held.append(slot)
        provider.grants += 1
        return slot

    def _give_back(self, slot: GateSlot) -> None:
        provider = self._providers.get(slot.key)
        if provider is None:
            return
        if slot in provider.held:
            provider.held.remove(slot)
        self._serve(provider, slot.key)

    def _reap(self, provider: _Provider) -> None:
        now = self._monotonic()
        stale = [s for s in provider.held if now - s.taken_at >= self._max_hold_s]
        for slot in stale:
            slot.released = True
            provider.held.remove(slot)

    def _reap_later(self, provider: _Provider, key: str) -> None:
        """While calls wait, a slot never given back is taken back when its hold runs out and serves them: waiting
        for the next call to arrive could strand them for good."""
        running = self._reapers.get(key)
        if running is not None and not running.done():
            return
        self._reapers[key] = asyncio.ensure_future(self._reap_while_waiting(provider, key))

    async def _reap_while_waiting(self, provider: _Provider, key: str) -> None:
        while provider.waiting and provider.held:
            oldest = min(slot.taken_at for slot in provider.held)
            await self._sleep(max(0.0, oldest + self._max_hold_s - self._monotonic()))
            self._reap(provider)
            self._serve(provider, key)

    def _serve(self, provider: _Provider, key: str) -> None:
        while provider.waiting and len(provider.held) < self._limit:
            floor = self._floor_every > 0 and (provider.grants + 1) % self._floor_every == 0
            if floor:
                ordered = sorted(provider.waiting, key=lambda w: w.seq)
            else:
                held: dict[str, int] = {}
                for slot in provider.held:
                    held[slot.org] = held.get(slot.org, 0) + 1
                ordered = sorted(provider.waiting, key=lambda w: (w.rank, held.get(w.org, 0), w.seq))
            chosen = next((w for w in ordered if self._fits(provider, w.priority_class, floor=floor)), None)
            if chosen is None:
                return
            provider.waiting.remove(chosen)
            if chosen.future.done():
                continue
            chosen.future.set_result(self._take(provider, key, chosen.org, waited=True))


__all__ = [
    "DEFAULT_FLOOR_EVERY",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_HOLD_S",
    "AdmissionSlot",
    "GateSlot",
    "PriorityGate",
    "ProviderAdmission",
    "provider_key",
    "upstream_key",
]
