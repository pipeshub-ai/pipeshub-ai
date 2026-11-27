"""WS-1..WS-5 (P5.9a): shapes, capacity, sizing and single use, pure; WS-6 sizing by memory over mixed shapes
(R23FU-FU-1)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.pool import (
    CapacityProbe,
    HostCapacity,
    WarmPool,
    WarmPoolConfig,
    WarmSandbox,
    WarmShape,
    capacity_slots,
    may_warm,
    next_shape,
    over_budget,
    shape_of,
    warm_budget_mb,
)
from harness_core.sandbox.types import (
    EgressDefault,
    EgressDestination,
    EgressPolicy,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock


def _spec(**overrides: object) -> SandboxSpec:
    base = SandboxSpec(
        image_digest="pxh-opencode-tools:1.18.34",
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
        egress=EgressPolicy(
            destinations=(
                EgressDestination("model-gateway", (8097,)),
                EgressDestination("tool-gateway", (8097,), upstream="http://worker:8098/a"),
            ),
            token_routes=(
                TokenRoute("pxh-model-aaaaaaaaaaaaaaaa", "model-gateway", "gateway:model"),
                TokenRoute("pxh-tools-aaaaaaaaaaaaaaaa", "tool-gateway", "gateway:tools"),
            ),
        ),
        mounts=(MountSpec("/workspace"), MountSpec("/state"), MountSpec("/skills", read_only=True)),
        labels={"orgId": "o", "runId": "r1", "attemptId": "a1"},
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


OTHER_RUN = _spec(
    labels={"orgId": "o2", "runId": "r2", "attemptId": "a9"},
    egress=EgressPolicy(
        destinations=(
            EgressDestination("tool-gateway", (8097,), upstream="http://worker-2:8098/b"),
            EgressDestination("MODEL-gateway.", (8097,)),
        ),
        token_routes=(
            TokenRoute("pxh-model-bbbbbbbbbbbbbbbb", "model-gateway", "gateway:model"),
            TokenRoute("pxh-tools-bbbbbbbbbbbbbbbb", "tool-gateway", "gateway:tools"),
        ),
    ),
)


def test_ws1_specs_of_two_runs_share_a_shape() -> None:
    shape = shape_of(_spec())
    assert shape is not None
    assert shape == shape_of(OTHER_RUN)
    assert shape.proxied_hosts == ("model-gateway", "tool-gateway")
    assert WarmShape.from_wire(shape.to_wire()) == shape


@pytest.mark.parametrize(
    ("name", "spec"),
    [
        ("image", _spec(image_digest="pxh-codex:0.160.0")),
        ("mode", _spec(mode=RuntimeMode.GVISOR)),
        ("resources", _spec(resources=Resources(cpus=1.0, memory_mb=2048, pids=256, scratch_mb=256))),
        ("mount read-only", _spec(mounts=(MountSpec("/workspace"), MountSpec("/state"), MountSpec("/skills")))),
        ("mount target", _spec(mounts=(MountSpec("/work"), MountSpec("/state"), MountSpec("/skills", read_only=True)))),
        ("hosts", _spec(egress=EgressPolicy(destinations=(EgressDestination("model-gateway", (8097,)),)))),
        ("no network", _spec(egress=EgressPolicy())),
    ],
)
def test_ws1_what_a_container_fixes_at_create_makes_another_shape(name: str, spec: SandboxSpec) -> None:
    assert shape_of(spec) is not None
    assert shape_of(spec) != shape_of(_spec()), name


def test_ws1_ip_destinations_need_no_host_entry_and_deny_has_no_network() -> None:
    ip_only = shape_of(_spec(egress=EgressPolicy(destinations=(EgressDestination("10.0.0.5", (443,)),))))
    assert ip_only is not None and ip_only.proxied_hosts == ()
    deny = shape_of(_spec(egress=EgressPolicy()))
    assert deny is not None and deny.proxied_hosts is None
    assert not deny.template().egress.needs_proxy
    assert shape_of(deny.template()) == deny


@pytest.mark.parametrize(
    "spec",
    [
        _spec(mounts=(MountSpec("/workspace", source="org/ses/workspace"), MountSpec("/state"))),
        _spec(egress=EgressPolicy(default=EgressDefault.ALLOW)),
    ],
)
def test_ws1_a_persistent_mount_or_the_legacy_bridge_is_never_warm(spec: SandboxSpec) -> None:
    assert shape_of(spec) is None


def test_ws1_the_template_is_the_shape_with_no_run_in_it() -> None:
    shape = shape_of(_spec())
    assert shape is not None
    template = shape.template({"pipeshub.harness.owner": "x-lead"})
    assert shape_of(template) == shape
    assert dict(template.labels) == {"pipeshub.harness.owner": "x-lead"}
    assert template.egress.token_routes == ()
    assert all(d.upstream is None for d in template.egress.destinations)


@pytest.mark.parametrize(
    ("memory_mb", "slot_mb", "reserve", "slots"),
    [
        (128_000, 1024, 0.25, 93),
        (8192, 1024, 0.25, 6),
        (8192, 1024, 0.0, 8),
        (1000, 1024, 0.0, 0),
        (0, 1024, 0.25, 0),
        (8192, 0, 0.25, 0),
    ],
)
def test_ws2_slots_from_memory(memory_mb: int, slot_mb: int, reserve: float, slots: int) -> None:
    assert capacity_slots(memory_mb, slot_mb, reserve) == slots


def _cap(memory_mb: int, held_mb: int = 0, warm_mb: int = 0, *, held: int | None = None, warm: int | None = None) -> HostCapacity:
    """A host with ``memory_mb`` for sandboxes; counts default to 1 GiB sandboxes."""
    return HostCapacity(
        memory_mb=memory_mb,
        held=held_mb // 1024 if held is None else held,
        warm=warm_mb // 1024 if warm is None else warm,
        held_mb=held_mb,
        warm_mb=warm_mb,
    )


@pytest.mark.parametrize(
    ("capacity", "fraction", "budget"),
    [
        (_cap(96_000), 0.25, 24_000),
        (_cap(6144), 0.25, 1536),
        (_cap(6144, held_mb=5120), 0.25, 1024),
        (_cap(6144, held_mb=6144), 0.25, 0),
        (_cap(6144, held_mb=9216), 0.25, 0),
        (_cap(0), 0.25, 0),
        (_cap(3072), 1.0, 3072),
    ],
)
def test_ws3_the_warm_budget_is_a_share_of_memory_and_never_what_runs_hold(
    capacity: HostCapacity, fraction: float, budget: int
) -> None:
    assert warm_budget_mb(capacity, fraction=fraction) == budget


@pytest.mark.parametrize(
    ("capacity", "memory_mb", "ceiling", "fraction", "allowed"),
    [
        (_cap(96_000), 1024, 0, 0.25, False),
        (_cap(96_000, warm_mb=8192), 1024, 8, 0.25, False),
        (_cap(96_000, warm_mb=7168), 1024, 8, 0.25, True),
        (_cap(6144, warm_mb=1024), 1024, 8, 0.25, False),
        # An empty pool keeps one sandbox past its budget while it fits (as `max(1, ...)` did) ...
        (_cap(2048), 1024, 8, 0.25, True),
        (_cap(6144), 3072, 8, 0.25, True),
        # ... and never one that does not fit beside what runs hold.
        (_cap(6144, held_mb=5120), 3072, 8, 0.25, False),
        (_cap(6144, held_mb=6144), 1024, 8, 0.25, False),
        (_cap(0), 1024, 8, 0.25, False),
        (_cap(3072, warm_mb=2048), 1024, 3, 1.0, True),
    ],
)
def test_ws3_may_warm(capacity: HostCapacity, memory_mb: int, ceiling: int, fraction: float, allowed: bool) -> None:
    assert may_warm(capacity, memory_mb, ceiling=ceiling, fraction=fraction) is allowed


def _shape(name: str, memory_mb: int = 1024) -> WarmShape:
    return WarmShape(f"img-{name}", RuntimeMode.DOCKER_HARDENED, Resources(memory_mb=memory_mb), ())


def test_ws4_the_shape_with_fewest_warm_goes_next_earlier_first() -> None:
    a, b, c = _shape("a"), _shape("b"), _shape("c")
    host = _cap(96_000)
    assert next_shape(host, [a, b, c], {}, ceiling=8, fraction=0.25) == a
    assert next_shape(host, [a, b, c], {a: 1}, ceiling=8, fraction=0.25) == b
    assert next_shape(host, [a, b, c], {a: 1, b: 1, c: 1}, ceiling=8, fraction=0.25) == a
    assert next_shape(host, [], {}, ceiling=8, fraction=0.25) is None


def test_ws6_r23fu_fu_1_a_shape_too_large_for_the_budget_never_stops_smaller_ones() -> None:
    small, big = _shape("small"), _shape("qwen", 3072)
    # 16 GiB host, a quarter kept back: 12 GiB for sandboxes, a 3 GiB warm budget; one small one warm already.
    host = _cap(12_288, warm_mb=1024)
    assert next_shape(host, [big, small], {small: 1}, ceiling=8, fraction=0.25) == small
    full = _cap(12_288, warm_mb=3072)
    assert next_shape(full, [big, small], {small: 3}, ceiling=8, fraction=0.25) is None


@pytest.mark.parametrize(
    ("capacity", "ceiling", "fraction", "over"),
    [
        (_cap(12_288), 8, 0.25, False),
        (_cap(12_288, warm_mb=3072), 8, 0.25, False),
        (_cap(12_288, warm_mb=4096), 8, 0.25, True),
        (_cap(12_288, warm_mb=4096), 3, 1.0, True),
        # One sandbox past the budget is what an empty pool may make; trim keeps it while it fits.
        (_cap(2048, warm_mb=1024), 8, 0.25, False),
        (_cap(2048, held_mb=2048, warm_mb=1024), 8, 0.25, True),
    ],
)
def test_ws6_trim_removes_exactly_what_refill_could_not_have_made(
    capacity: HostCapacity, ceiling: int, fraction: float, over: bool
) -> None:
    assert over_budget(capacity, ceiling=ceiling, fraction=fraction) is over


@pytest.mark.parametrize("fraction", [0.25, 0.5, 1.0])
@pytest.mark.parametrize("held_mb", [0, 2048, 5120, 9216, 12_288])
def test_ws6_refill_never_makes_what_trim_removes(fraction: float, held_mb: int) -> None:
    shapes = [_shape("a"), _shape("qwen", 3072), _shape("b", 512)]
    counts: dict[WarmShape, int] = {}
    warm_mb = warm = 0
    for _ in range(64):
        host = _cap(12_288, held_mb, warm_mb, held=held_mb // 1024, warm=warm)
        shape = next_shape(host, shapes, counts, ceiling=6, fraction=fraction)
        if shape is None:
            break
        counts[shape] = counts.get(shape, 0) + 1
        warm, warm_mb = warm + 1, warm_mb + shape.resources.memory_mb
        assert not over_budget(_cap(12_288, held_mb, warm_mb, held=held_mb // 1024, warm=warm), ceiling=6, fraction=fraction)
    assert held_mb + warm_mb <= 12_288


class _Backend:
    def __init__(self, memory_mb: int = 8 * 1024) -> None:
        self.memory_mb = memory_mb
        self.warm: dict[str, WarmSandbox] = {}
        self.claimed: dict[str, int] = {}
        self._n = 0

    async def create_warm(self, shape: WarmShape, labels: object) -> WarmSandbox:
        self._n += 1
        sandbox = WarmSandbox(f"w{self._n}", shape, float(self._n))
        self.warm[sandbox.sandbox_id] = sandbox
        return sandbox

    async def claim_warm(self, sandbox: WarmSandbox) -> bool:
        if self.warm.pop(sandbox.sandbox_id, None) is None:
            return False
        self.claimed[sandbox.sandbox_id] = sandbox.shape.resources.memory_mb
        return True

    async def discard_warm(self, sandbox: WarmSandbox, *, reason: str) -> None:
        _ = self.claimed.pop(sandbox.sandbox_id, None)

    def hold_cold(self, memory_mb: int) -> None:
        self._n += 1
        self.claimed[f"c{self._n}"] = memory_mb

    async def list_warm(self) -> list[WarmSandbox]:
        return list(self.warm.values())

    def holds(self, sandbox_id: str) -> bool:
        return sandbox_id in self.claimed

    async def probe_capacity(self) -> CapacityProbe:
        return CapacityProbe(
            memory_mb=self.memory_mb,
            held=len(self.claimed),
            warm=len(self.warm),
            held_mb=sum(self.claimed.values()),
            warm_mb=sum(s.shape.resources.memory_mb for s in self.warm.values()),
        )


async def test_ws5_a_handed_out_sandbox_never_reenters_the_pool() -> None:
    shape = shape_of(_spec())
    assert shape is not None
    backend = _Backend()
    pool = WarmPool(backend, WarmPoolConfig(ceiling=2, shapes=(shape,), reserve_fraction=0.0), clock=ManualClock())
    assert await pool.refill() == 2
    taken = await pool.take(_spec())
    assert taken is not None
    assert pool.retired(taken.sandbox_id)
    with pytest.raises(SandboxError):
        pool.add(taken)
    # Even if the backend listed it again (a broken adapter), loading skips it.
    backend.warm[taken.sandbox_id] = taken
    _ = await pool.load()
    assert taken.sandbox_id not in pool.warm_ids()


async def test_ws5_a_claim_lost_to_another_taker_moves_on() -> None:
    shape = shape_of(_spec())
    assert shape is not None
    backend = _Backend()
    pool = WarmPool(backend, WarmPoolConfig(ceiling=2, shapes=(shape,), reserve_fraction=0.0), clock=ManualClock())
    _ = await pool.refill()
    first = min(pool.warm_ids())
    _ = await backend.claim_warm(backend.warm[first])
    taken = await pool.take(_spec())
    assert taken is not None and taken.sandbox_id != first


async def test_ws5_unknown_images_are_not_learned_and_cost_no_lookup() -> None:
    backend = _Backend()
    pool = WarmPool(backend, WarmPoolConfig(ceiling=2, learn_images=frozenset({"pxh-codex:0.160.0"})), clock=ManualClock())
    pool.learn(_spec())
    assert pool.shapes == ()
    pool.learn(_spec(image_digest="pxh-codex:0.160.0"))
    assert len(pool.shapes) == 1
    assert await pool.take(_spec()) is None


async def test_ws6_r23fu_fu_1_qwens_3_gib_shape_leaves_the_small_engines_their_capacity() -> None:
    """16 GiB host, three 1 GiB engine shapes and Qwen Code's 3 GiB: the old slot math sized every slot at 3 GiB
    (4 slots), so the pool stopped warming after four runs; by memory it keeps warming beside eight."""
    small = shape_of(_spec())
    assert small is not None
    qwen = replace(small, image_digest="pxh-qwen-code:0.1", resources=replace(small.resources, memory_mb=3072))
    others = [replace(small, image_digest=f"pxh-engine-{n}:1") for n in "ab"]
    backend = _Backend(memory_mb=16 * 1024)
    pool = WarmPool(
        backend, WarmPoolConfig(ceiling=8, shapes=(small, *others, qwen), reserve_fraction=0.25), clock=ManualClock()
    )
    assert await pool.refill() == 3
    assert {s.shape for s in backend.warm.values()} == {small, *others}, "Qwen's 3 GiB never fits beside three warm"
    for _ in range(8):
        spec = _spec(image_digest="pxh-codex:0.160.0")
        _ = await pool.make_room(spec)
        backend.hold_cold(spec.resources.memory_mb)
    capacity = await pool.capacity()
    assert (capacity.memory_mb, capacity.held_mb) == (12_288, 8192)
    assert len(backend.warm) == 3, "eight 1 GiB runs and three warm fit in 12 GiB: nothing was given back"
    assert await pool.refill() == 0
    detail = pool.detail(capacity)
    assert (detail["capacitySlots"], detail["capacityMb"], detail["heldMb"], detail["warmMb"]) == ("12", "12288", "8192", "3072")
    assert not await pool.make_room(_spec(image_digest="pxh-codex:0.160.0")), "a ninth run still fits"
    backend.hold_cold(1024)
    assert await pool.make_room(_spec(image_digest="pxh-codex:0.160.0")), "the tenth run's memory comes from the pool"
    assert len(backend.warm) == 2


async def test_ws6_make_room_gives_back_as_much_memory_as_the_cold_create_needs() -> None:
    small = shape_of(_spec())
    assert small is not None
    backend = _Backend(memory_mb=4096)
    pool = WarmPool(backend, WarmPoolConfig(ceiling=4, shapes=(small,), fraction=1.0, reserve_fraction=0.0), clock=ManualClock())
    assert await pool.refill() == 4
    assert await pool.make_room(_spec(resources=Resources(cpus=1.0, memory_mb=3072, pids=256, scratch_mb=256)))
    assert len(backend.warm) == 1, "three oldest 1 GiB warm sandboxes make room for one 3 GiB run"
    assert not await pool.make_room(_spec(resources=Resources(cpus=1.0, memory_mb=3072, pids=256, scratch_mb=256)))
