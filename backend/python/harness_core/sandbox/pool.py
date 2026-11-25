"""Warm pool of generic sandboxes (P5.9a), shared by every broker adapter.

A warm sandbox is created ahead of any run with only what a run cannot change
(its ``WarmShape``: image, mode, resources, scratch mount layout, the proxy's
names) and is bound to one run at acquire. It is single use: the pool hands an
id out once, through the adapter's atomic ``claim_warm``, and never takes one
back; a released sandbox is destroyed like any other.

Sizing comes from the host's probed memory (R23FU-FU-1): every live sandbox
counts with the memory it holds (warm ones too), the pool's warm sandboxes hold
at most ``min(memory x fraction, memory - held)`` of it and are at most
``ceiling`` in number, and an empty pool may hold one sandbox past that budget
when it fits. Counting memory, not slots of one size, keeps a large shape from
shrinking what the host holds of small ones. The pool refills only while no
acquire is in flight, and gives memory back before a cold create needs it, so
warm sandboxes never starve a run.

Claims are admitted per org (SEC-DOS-05): while another org has asked for a sandbox within ``org_window_s``, an org
holds at most ``org_claim_cap`` warm-served sandboxes at once, and its acquires past that go cold, never refused. An
org alone on the broker is never capped, so a one-org install keeps the whole pool.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import ipaddress
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.metering import NOOP_BROKER_METRICS
from harness_core.sandbox.types import (
    EgressDefault,
    EgressDestination,
    EgressPolicy,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping, Sequence

    from harness_core.clock import Clock
    from harness_core.sandbox.metering import BrokerMetrics

# `BrokerHealth.detail` keys a broker with a pool reports.
WARM_POOL = "warmPool"
CAPACITY_SLOTS = "capacitySlots"
CAPACITY_MB = "capacityMb"
HELD = "held"
HELD_MB = "heldMb"
WARM = "warm"
WARM_MB = "warmMb"
# The spec label naming the org a sandbox is for (as the egress bindings and the reaps read it).
ORG_LABEL = "orgId"


@dataclass(frozen=True, slots=True)
class WarmShape:
    """What a sandbox's container fixes at create. ``proxied_hosts`` is ``None`` for a sandbox that never
    gets a network, else the names ``/etc/hosts`` points at its egress proxy."""

    image_digest: str
    mode: RuntimeMode
    resources: Resources
    mounts: tuple[tuple[str, bool], ...]
    proxied_hosts: tuple[str, ...] | None = None
    # A scratch mount's own size (``MountSpec.size_mb``), by target; empty for every shape before WP-63.
    mount_sizes: tuple[tuple[str, int], ...] = ()
    # The digest a pinned spec holds its image to (C2.20): a warm sandbox made before the image was rebuilt is
    # another shape, never handed to a run pinned to the new one.
    image_pin: str | None = None

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_wire().encode()).hexdigest()[:24]

    def to_wire(self) -> str:
        r = self.resources
        return json.dumps(
            {
                "image": self.image_digest,
                "mode": self.mode.value,
                "resources": [r.cpus, r.memory_mb, r.pids, r.scratch_mb],
                "mounts": [[target, ro] for target, ro in self.mounts],
                # Only when set, so a shape without sized mounts keeps its digest.
                **({"sizes": [[target, mb] for target, mb in self.mount_sizes]} if self.mount_sizes else {}),
                "hosts": None if self.proxied_hosts is None else list(self.proxied_hosts),
                # Only when pinned, so an unpinned shape keeps the digest it had before C2.20.
                **({"pin": self.image_pin} if self.image_pin is not None else {}),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_wire(cls, raw: str) -> WarmShape:
        data = json.loads(raw)
        cpus, memory_mb, pids, scratch_mb = data["resources"]
        hosts = data["hosts"]
        return cls(
            image_digest=str(data["image"]),
            mode=RuntimeMode(data["mode"]),
            resources=Resources(cpus=float(cpus), memory_mb=int(memory_mb), pids=int(pids), scratch_mb=int(scratch_mb)),
            mounts=tuple((str(target), bool(ro)) for target, ro in data["mounts"]),
            mount_sizes=tuple((str(target), int(mb)) for target, mb in data.get("sizes", ())),
            proxied_hosts=None if hosts is None else tuple(str(h) for h in hosts),
            image_pin=str(data["pin"]) if data.get("pin") is not None else None,
        )

    def template(self, labels: Mapping[str, str] | None = None) -> SandboxSpec:
        """The spec a warm sandbox is created from: no run's labels, no binding, nothing to reach yet."""
        egress = (
            EgressPolicy()
            if self.proxied_hosts is None
            else EgressPolicy(destinations=tuple(EgressDestination(host) for host in self.proxied_hosts))
        )
        return SandboxSpec(
            image_digest=self.image_digest,
            mode=self.mode,
            resources=self.resources,
            egress=egress,
            mounts=tuple(
                MountSpec(target, read_only=ro, size_mb=dict(self.mount_sizes).get(target)) for target, ro in self.mounts
            ),
            labels=dict(labels or {}),
            image_pin=self.image_pin,
        )


def _is_ip(value: str) -> bool:
    try:
        _ = ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def shape_of(spec: SandboxSpec) -> WarmShape | None:
    """The shape ``spec`` needs, or ``None`` when no warm sandbox can serve it: a persistent mount or the
    legacy egress bridge must be in place when the container is created."""
    if spec.egress.default is EgressDefault.ALLOW or any(m.source is not None for m in spec.mounts):
        return None
    hosts: tuple[str, ...] | None = None
    if spec.egress.needs_proxy:
        names = {d.host.strip().lower().rstrip(".") for d in spec.egress.destinations}
        hosts = tuple(sorted(name for name in names if not _is_ip(name)))
    return WarmShape(
        image_digest=spec.image_digest,
        mode=spec.mode,
        resources=spec.resources,
        mounts=tuple((m.target, m.read_only) for m in spec.mounts),
        mount_sizes=tuple((m.target, m.size_mb) for m in spec.mounts if m.size_mb is not None),
        proxied_hosts=hosts,
        image_pin=spec.image_pin,
    )


@dataclass(frozen=True, slots=True)
class HostCapacity:
    """``memory_mb`` is what sandboxes may hold on the host (its reserve kept back); ``held`` sandboxes are bound
    to runs and hold ``held_mb`` of it, ``warm`` wait in a pool and hold ``warm_mb``."""

    memory_mb: int
    held: int
    warm: int
    held_mb: int
    warm_mb: int

    @property
    def free_mb(self) -> int:
        return max(0, self.memory_mb - self.held_mb - self.warm_mb)

    def fits(self, memory_mb: int) -> bool:
        return memory_mb <= self.free_mb


@dataclass(frozen=True, slots=True)
class CapacityProbe:
    """What an adapter reads from its host: total memory and its live sandboxes, warm ones included, each by count
    and by the memory their limits hold."""

    memory_mb: int
    held: int
    warm: int
    held_mb: int
    warm_mb: int


def capacity_slots(memory_mb: int, slot_mb: int, reserve_fraction: float) -> int:
    """How many sandboxes of ``slot_mb`` fit in ``memory_mb`` with its reserve kept back."""
    if memory_mb <= 0 or slot_mb <= 0:
        return 0
    return max(0, math.floor(sandbox_memory_mb(memory_mb, reserve_fraction) / slot_mb))


def sandbox_memory_mb(memory_mb: int, reserve_fraction: float) -> int:
    return max(0, math.floor(memory_mb * (1.0 - reserve_fraction)))


def warm_budget_mb(capacity: HostCapacity, *, fraction: float) -> int:
    """The memory a pool's warm sandboxes may hold: its share of the host's, never what runs hold."""
    return max(0, min(math.floor(capacity.memory_mb * fraction), capacity.memory_mb - capacity.held_mb))


def may_warm(capacity: HostCapacity, memory_mb: int, *, ceiling: int, fraction: float) -> bool:
    """Whether a pool may create one more warm sandbox holding ``memory_mb``: below the ceiling, in free memory
    and within its budget, except that an empty pool may hold one past the budget (as ``max(1, ...)`` did)."""
    if ceiling <= 0 or capacity.warm >= ceiling or not capacity.fits(memory_mb):
        return False
    return capacity.warm == 0 or capacity.warm_mb + memory_mb <= warm_budget_mb(capacity, fraction=fraction)


def over_budget(capacity: HostCapacity, *, ceiling: int, fraction: float) -> bool:
    """Whether a pool holds more than ``may_warm`` would have let it create, so trim and refill never undo each
    other: past the ceiling, past what runs leave of the host, or past its budget with more than one sandbox."""
    if capacity.warm == 0:
        return False
    if capacity.warm > ceiling or capacity.held_mb + capacity.warm_mb > capacity.memory_mb:
        return True
    return capacity.warm > 1 and capacity.warm_mb > warm_budget_mb(capacity, fraction=fraction)


def org_claim_cap(ceiling: int, share: float) -> int:
    """How many warm-served sandboxes one org may hold at once while another org is active."""
    return max(1, math.ceil(ceiling * share))


def next_shape(
    capacity: HostCapacity, shapes: Sequence[WarmShape], counts: Mapping[WarmShape, int], *, ceiling: int, fraction: float
) -> WarmShape | None:
    """The shape to warm next: the one with the fewest warm sandboxes (earlier shapes first on a tie) that the
    pool may still create, so a shape too large for what is left never stops smaller ones from filling."""
    ranked = sorted(range(len(shapes)), key=lambda index: (counts.get(shapes[index], 0), index))
    for index in ranked:
        if may_warm(capacity, shapes[index].resources.memory_mb, ceiling=ceiling, fraction=fraction):
            return shapes[index]
    return None


@dataclass(frozen=True, slots=True)
class WarmSandbox:
    """A warm sandbox as its adapter knows it; ``ref`` is the adapter's own handle (a container id)."""

    sandbox_id: str
    shape: WarmShape
    created_at: float
    ref: str = ""
    running: bool = True


@dataclass(frozen=True, slots=True)
class WarmPoolConfig:
    """``ceiling`` 0 is off. Shapes are configured, and learned from cold acquires of ``learn_images``."""

    ceiling: int
    shapes: tuple[WarmShape, ...] = ()
    learn_images: frozenset[str] = frozenset()
    max_shapes: int = 8
    fraction: float = 0.25
    # Memory kept for everything that is not a sandbox (the app, stores, the daemon).
    reserve_fraction: float = 0.25
    max_age_s: int = 3600
    # A warm sandbox not running this long after create is a dead create; a younger one may be in flight elsewhere.
    start_grace_s: int = 120
    # Static labels on every warm sandbox (owner, install); never a run's.
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # SEC-DOS-05: one org's share of the ceiling while another org asked within the window; 1.0 is no cap.
    org_share: float = 0.5
    org_window_s: int = 900

    def __post_init__(self) -> None:
        if self.ceiling < 0 or not 0 < self.fraction <= 1 or not 0 <= self.reserve_fraction < 1:
            raise ValueError("warm pool: ceiling >= 0, 0 < fraction <= 1, 0 <= reserve < 1")
        if not 0 < self.org_share <= 1 or self.org_window_s < 0:
            raise ValueError("warm pool: 0 < org share <= 1, org window >= 0")
        object.__setattr__(self, "labels", dict(self.labels))


@dataclass(frozen=True, slots=True)
class WarmStats:
    capacity: HostCapacity
    budget_mb: int
    by_shape: Mapping[str, int]


class WarmBackend(Protocol):
    """What a broker adapter does for its pool. ``claim_warm`` is atomic across broker processes."""

    async def create_warm(self, shape: WarmShape, labels: Mapping[str, str]) -> WarmSandbox: ...

    async def claim_warm(self, sandbox: WarmSandbox) -> bool:
        """Take ``sandbox`` out of every pool for good; ``False`` when another taker or a reap got it first."""
        ...

    async def discard_warm(self, sandbox: WarmSandbox, *, reason: str) -> None:
        """Destroy a sandbox this process claimed."""
        ...

    async def list_warm(self) -> list[WarmSandbox]:
        """Every unclaimed warm sandbox of this broker's namespace, running or not, any process's."""
        ...

    async def probe_capacity(self) -> CapacityProbe: ...

    def holds(self, sandbox_id: str) -> bool:
        """Whether this process holds ``sandbox_id`` bound to a run now (released and reaped ones are not held)."""
        ...


class WarmPool:
    """Bookkeeping and policy; the adapter does the work through ``WarmBackend``."""

    def __init__(
        self,
        backend: WarmBackend,
        config: WarmPoolConfig,
        *,
        clock: Clock,
        log: HarnessLog | None = None,
        metrics: BrokerMetrics = NOOP_BROKER_METRICS,
    ) -> None:
        self._backend = backend
        self._metrics = metrics
        self.config = config
        self._clock = clock
        self._log = log or HarnessLog.named("harness_core.sandbox.pool")
        self._shapes: list[WarmShape] = list(config.shapes)
        self._configured = frozenset(config.shapes)
        self._entries: dict[str, WarmSandbox] = {}
        self._retired: set[str] = set()
        self._inflight = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        # Warm sandboxes this pool handed to acquires, and acquires it sent cold for their org's cap.
        self.served = 0
        self.capped = 0
        # SEC-DOS-05: when each org last asked for a sandbox, which org each served sandbox went to, and the ones an
        # acquire still in flight took (claimed, not bound yet: the backend does not hold them yet).
        self._org_seen: dict[str, float] = {}
        self._served_to: dict[str, str] = {}
        self._binding: set[str] = set()
        self._taken_here: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("warm_taken", default=None)

    @property
    def enabled(self) -> bool:
        return self.config.ceiling > 0

    @property
    def shapes(self) -> tuple[WarmShape, ...]:
        return tuple(self._shapes)

    def warm_ids(self) -> frozenset[str]:
        return frozenset(self._entries)

    def retired(self, sandbox_id: str) -> bool:
        return sandbox_id in self._retired

    def smallest_mb(self) -> int:
        """The memory of the pool's smallest shape: what the health detail counts the host's sandboxes in."""
        return min((shape.resources.memory_mb for shape in self._shapes), default=Resources().memory_mb)

    def _learnable(self, shape: WarmShape) -> bool:
        return shape in self._configured or shape.image_digest in self.config.learn_images

    def learn(self, spec: SandboxSpec) -> None:
        """A cold acquire of a pool image: pre-create its shape from now on (bounded, oldest learned out)."""
        shape = shape_of(spec)
        if shape is not None and self.enabled:
            self._know(shape)

    def _know(self, shape: WarmShape) -> None:
        if shape in self._shapes or not self._learnable(shape):
            return
        self._shapes.append(shape)
        learned = [s for s in self._shapes if s not in self._configured]
        while len(self._shapes) > max(self.config.max_shapes, len(self._configured)) and learned:
            self._shapes.remove(learned.pop(0))
        self._log.info("sandbox.warm_shape_learned", shape=shape.digest, image=shape.image_digest, shapes=len(self._shapes))

    def add(self, sandbox: WarmSandbox) -> None:
        if sandbox.sandbox_id in self._retired:
            raise SandboxError(f"warm sandbox {sandbox.sandbox_id} was handed out once; it is never pooled again")
        self._entries[sandbox.sandbox_id] = sandbox

    def forget(self, sandbox_id: str) -> None:
        """A sandbox the adapter destroyed outside the pool (a reap): retired for good."""
        _ = self._entries.pop(sandbox_id, None)
        self._retired.add(sandbox_id)

    @contextlib.asynccontextmanager
    async def acquiring(self) -> AsyncIterator[None]:
        """Marks an acquire in flight: refill steps aside until none is, and what it takes counts toward its org's
        share before the backend holds it."""
        self._inflight += 1
        taken: list[str] = []
        token = self._taken_here.set(taken)
        try:
            yield
        finally:
            self._taken_here.reset(token)
            self._binding.difference_update(taken)
            self._inflight -= 1

    async def load(self) -> int:
        """Warm sandboxes the backend has that this process does not know yet (an earlier or another broker's)."""
        if not self.enabled:
            return 0
        added = 0
        for sandbox in await self._backend.list_warm():
            if not sandbox.running or sandbox.sandbox_id in self._retired or sandbox.sandbox_id in self._entries:
                continue
            if sandbox.shape not in self._shapes:
                if not self._learnable(sandbox.shape):
                    continue
                self._know(sandbox.shape)
            self._entries[sandbox.sandbox_id] = sandbox
            added += 1
        return added

    async def take(self, spec: SandboxSpec) -> WarmSandbox | None:
        """A warm sandbox of ``spec``'s shape, claimed for this acquire and retired from every pool; ``None`` sends
        the acquire cold (no such sandbox, or its org holds its share while another org is active)."""
        shape = shape_of(spec) if self.enabled else None
        if shape is None or not (shape in self._shapes or self._learnable(shape)):
            return None
        org = spec.labels.get(ORG_LABEL) or None
        if org is not None and self._over_share(org):
            self.capped += 1
            self._metrics.warm_claim("org_capped")
            self._log.info("sandbox.warm_org_capped", orgId=org, cap=org_claim_cap(self.config.ceiling, self.config.org_share))
            return None
        taken = await self._claim(shape, org)
        self._metrics.warm_claim("cold" if taken is None else "warm")
        self._metrics.pool_depth(len(self._entries))
        return taken

    async def _claim(self, shape: WarmShape, org: str | None) -> WarmSandbox | None:
        for attempt in range(2):
            for sandbox in sorted(self._entries.values(), key=lambda s: s.created_at):
                if sandbox.shape != shape:
                    continue
                del self._entries[sandbox.sandbox_id]
                self._retired.add(sandbox.sandbox_id)
                if await self._backend.claim_warm(sandbox):
                    self.served += 1
                    if org is not None:
                        self._served_to[sandbox.sandbox_id] = org
                        taken = self._taken_here.get()
                        if taken is not None:
                            taken.append(sandbox.sandbox_id)
                            self._binding.add(sandbox.sandbox_id)
                    return sandbox
            if attempt == 0 and not await self.load():
                break
        return None

    def _over_share(self, org: str) -> bool:
        """Notes ``org``'s ask; whether it already holds its share while another org asked within the window.

        Counted from what the backend holds now, so every way a sandbox ends (release, reap, sweep) gives the share
        back; one an acquire in flight took counts too, so concurrent acquires of one org cannot all slip under it."""
        now = self._clock.now().timestamp()
        window = self.config.org_window_s
        self._org_seen = {o: t for o, t in self._org_seen.items() if now - t < window}
        contended = any(other != org for other in self._org_seen)
        self._org_seen[org] = now
        held = 0
        for sandbox_id, owner in list(self._served_to.items()):
            if sandbox_id in self._binding or self._backend.holds(sandbox_id):
                held += owner == org
            else:
                del self._served_to[sandbox_id]
        return contended and held >= org_claim_cap(self.config.ceiling, self.config.org_share)

    async def capacity(self) -> HostCapacity:
        probe = await self._backend.probe_capacity()
        return HostCapacity(
            memory_mb=sandbox_memory_mb(probe.memory_mb, self.config.reserve_fraction),
            held=probe.held,
            warm=probe.warm,
            held_mb=probe.held_mb,
            warm_mb=probe.warm_mb,
        )

    def budget_mb(self, capacity: HostCapacity) -> int:
        return warm_budget_mb(capacity, fraction=self.config.fraction)

    async def stats(self) -> WarmStats:
        capacity = await self.capacity()
        counts: dict[str, int] = {}
        for sandbox in self._entries.values():
            counts[sandbox.shape.digest] = counts.get(sandbox.shape.digest, 0) + 1
        return WarmStats(capacity=capacity, budget_mb=self.budget_mb(capacity), by_shape=counts)

    async def refill(self) -> int:
        """Create warm sandboxes up to the budget, one at a time, while no acquire is in flight."""
        if not self.enabled:
            return 0
        created = 0
        async with self._lock:
            _ = await self.load()
            while self._inflight == 0 and not self._closing:
                shape = self._short_shape(await self.capacity())
                if shape is None:
                    break
                sandbox = await self._backend.create_warm(shape, self.config.labels)
                self.add(sandbox)
                created += 1
        if created:
            self._metrics.pool_depth(len(self._entries))
            self._log.info("sandbox.warm_refilled", created=created, warm=len(self._entries))
        return created

    def _short_shape(self, capacity: HostCapacity) -> WarmShape | None:
        counts: dict[WarmShape, int] = {}
        for sandbox in self._entries.values():
            counts[sandbox.shape] = counts.get(sandbox.shape, 0) + 1
        return next_shape(capacity, self._shapes, counts, ceiling=self.config.ceiling, fraction=self.config.fraction)

    async def make_room(self, spec: SandboxSpec) -> bool:
        """Before a cold create of ``spec``: give back the oldest warm sandboxes' memory until it fits."""
        if not self.enabled:
            return False
        capacity = await self.capacity()
        short = spec.resources.memory_mb - capacity.free_mb
        if short <= 0:
            return False
        freed, chosen = 0, list[WarmSandbox]()
        for sandbox in sorted(self._entries.values(), key=lambda s: s.created_at):
            if freed >= short:
                break
            chosen.append(sandbox)
            freed += sandbox.shape.resources.memory_mb
        return await self._discard(chosen, reason="make_room") > 0

    async def trim(self) -> int:
        """Remove stale warm sandboxes (too old, of an unknown shape, dead creates) and the oldest past the budget."""
        if not self.enabled:
            return 0
        async with self._lock:
            now = self._clock.now().timestamp()
            removed = 0
            for sandbox in await self._backend.list_warm():
                if sandbox.running:
                    continue
                # One this pool knew running has died; another's may still be starting (a create in flight).
                if sandbox.sandbox_id in self._entries or now - sandbox.created_at >= self.config.start_grace_s:
                    removed += await self._discard([sandbox], reason="not_running")
            _ = await self.load()
            stale = [
                s
                for s in self._entries.values()
                if now - s.created_at >= self.config.max_age_s or s.shape not in self._shapes
            ]
            removed += await self._discard(stale, reason="stale")
            while self._entries and over_budget(
                await self.capacity(), ceiling=self.config.ceiling, fraction=self.config.fraction
            ):
                # Each round takes one entry out whatever the claim's outcome, so the loop ends.
                removed += await self._discard_oldest(1, reason="over_target")
        return removed

    async def _discard_oldest(self, count: int, *, reason: str) -> int:
        oldest = sorted(self._entries.values(), key=lambda s: s.created_at)[:count]
        return await self._discard(oldest, reason=reason)

    async def _discard(self, sandboxes: Iterable[WarmSandbox], *, reason: str) -> int:
        removed = 0
        for sandbox in list(sandboxes):
            _ = self._entries.pop(sandbox.sandbox_id, None)
            self._retired.add(sandbox.sandbox_id)
            if await self._backend.claim_warm(sandbox):
                await self._backend.discard_warm(sandbox, reason=reason)
                removed += 1
        if removed:
            self._metrics.pool_depth(len(self._entries))
            self._log.info("sandbox.warm_discarded", count=removed, reason=reason)
        return removed

    def kick(self) -> None:
        """Refill in the background (after an acquire took one, or a release freed a slot)."""
        if not self.enabled or self._closing or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.ensure_future(self._refill_quietly())

    async def _refill_quietly(self) -> None:
        try:
            _ = await self.refill()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The next kick or sweep tries again; acquires go cold meanwhile.
            self._log.warning("sandbox.warm_refill_failed", errorType=type(exc).__name__)

    async def aclose(self) -> None:
        """Stop refilling; a create in flight finishes (cancelling it would leave a container the daemon is
        still making, with nobody to remove it)."""
        self._closing = True
        task, self._task = self._task, None
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def detail(self, capacity: HostCapacity) -> dict[str, str]:
        smallest = self.smallest_mb()
        return {
            WARM_POOL: str(self.enabled).lower(),
            # How many of the pool's smallest sandboxes the host holds; the memory figures are the measure.
            CAPACITY_SLOTS: str(capacity.memory_mb // smallest if smallest > 0 else 0),
            CAPACITY_MB: str(capacity.memory_mb),
            HELD: str(capacity.held),
            HELD_MB: str(capacity.held_mb),
            WARM: str(capacity.warm),
            WARM_MB: str(capacity.warm_mb),
        }


__all__ = [
    "CAPACITY_MB",
    "CAPACITY_SLOTS",
    "HELD",
    "HELD_MB",
    "ORG_LABEL",
    "WARM",
    "WARM_MB",
    "WARM_POOL",
    "CapacityProbe",
    "HostCapacity",
    "WarmBackend",
    "WarmPool",
    "WarmPoolConfig",
    "WarmSandbox",
    "WarmShape",
    "WarmStats",
    "capacity_slots",
    "may_warm",
    "next_shape",
    "org_claim_cap",
    "over_budget",
    "sandbox_memory_mb",
    "shape_of",
    "warm_budget_mb",
]
