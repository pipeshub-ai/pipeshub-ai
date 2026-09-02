"""ResourceGovernor: owns the probe, registry, gates and the sample loop that
ties them together (plan section 3). This is the class other services
construct at process startup and share across their consumers/routes.
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.services.resource_governor.gate import AdmissionGate, StartRateLimiter
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import (
    EMBEDDING_CPU_RESERVATION,
    EMBEDDING_CPU_RESERVATION_MAX_FRACTION,
    HEAVY_PARSE_SLOTS_PER_CPU,
    HEAVY_PARSE_WORKING_SET_GB,
    INCIDENT_COOLDOWN_SECONDS,
    INDEX_HEADROOM,
    INDEX_HEAVY_WORKING_SET_GB,
    INDEX_LIGHT_WORKING_SET_GB,
    LIGHT_PARSE_SLOTS_PER_CPU,
    SAMPLE_INTERVAL_SECONDS,
    SAMPLE_JITTER_SECONDS,
    floor_for,
    next_limits,
    resolve_ceilings,
    start_rate_limiter_params,
    warm_start_limits,
)
from app.services.resource_governor.probe import ResourceProbe, build_probe
from app.services.resource_governor.registry import LimitRegistry

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / (1024 ** 3):.2f}GiB"


def _fmt_ratio(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.0%}"


class ResourceGovernor:
    """Resource-adaptive admission control shared by parsing, indexing and
    Docling processes."""

    def __init__(
        self,
        *,
        logger: logging.Logger,
        env_parse: int | None = None,
        env_index: int | None = None,
        worker_count: int = 1,
        reserve_embedding_cpus: bool = False,
        probe: ResourceProbe | None = None,
        sample_interval: float = SAMPLE_INTERVAL_SECONDS,
        jitter: float = SAMPLE_JITTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._logger = logger
        self._probe = probe or build_probe()
        self._sample_interval = sample_interval
        self._jitter = jitter
        self._clock = clock
        self._sleep = sleep
        self._rng = rng
        self._worker_count = max(1, worker_count)

        initial_snapshot = self._probe.snapshot()
        if initial_snapshot.source == "error":
            # A transient failure on the very first read (e.g. cgroup files
            # not yet mounted during container startup) would otherwise lock
            # in the fallback cpu_quota=1.0 for the ceilings' entire process
            # lifetime, since they're resolved once here and never redone.
            # One immediate retry is enough to ride out a startup race.
            self._logger.warning(
                "ResourceGovernor: initial probe snapshot failed; retrying once before resolving ceilings"
            )
            initial_snapshot = self._probe.snapshot()
        self._ceilings: Ceilings = resolve_ceilings(
            initial_snapshot,
            env_parse,
            env_index,
            self._worker_count,
            reserve_embedding_cpus=reserve_embedding_cpus,
        )
        if self._ceilings.index_light == 0:
            # Only reachable when an explicit MAX_CONCURRENT_INDEXING is too
            # small to split (a total of 1 cannot be two tiers each floored at
            # 1), so resolve_ceilings collapses light away and every record
            # routes to heavy. Worth saying out loud: on this node light
            # records queue behind heavy ones, which the tier split otherwise
            # prevents.
            self._logger.warning(
                "MAX_CONCURRENT_INDEXING=%s leaves no room to split the "
                "in-flight budget by tier; all records share one pool of %d. "
                "Light records will queue behind heavy ones. Raise it to at "
                "least 2 to restore the split.",
                env_index,
                self._ceilings.index,
            )

        self._state_lock = threading.Lock()
        self._registry = LimitRegistry(warm_start_limits(self._ceilings))
        self._state = ControllerState.initial()

        self._gates_lock = threading.Lock()
        self._gates: dict[Pool, AdmissionGate] = {}
        # HEAVY_PARSE's admission rate scales with the heavy-parse ceiling.
        heavy_rate_interval, heavy_rate_capacity = start_rate_limiter_params(self._ceilings.heavy)
        self._rate_limiters: dict[Pool, StartRateLimiter] = {
            Pool.HEAVY_PARSE: StartRateLimiter(
                heavy_rate_interval, heavy_rate_capacity, clock=clock,
            ),
        }

        self._stats_lock = threading.Lock()
        self._last_snapshot: ResourceSnapshot = initial_snapshot
        self._last_demand: dict[Pool, PoolDemand] = {pool: PoolDemand.empty() for pool in Pool}

        self._running = False

        self._logger.info(
            "ResourceGovernor initialised: probe_source=%s cpu_quota=%.2f mem_limit=%s "
            "ceilings(heavy_parse=%d light_parse=%d index_heavy=%d index_light=%d) "
            "worker_count=%d start_limits(heavy_parse=%d light_parse=%d "
            "index_heavy=%d index_light=%d) "
            "— parse ceilings are %.2f (heavy) / %.2f (light) slots per CPU capped by "
            "MAX_CONCURRENT_PARSING; each index ceiling is %.2fx its own parse tier, "
            "with the two together capped by MAX_CONCURRENT_INDEXING, so a queue of "
            "heavy records can never consume the budget light records need; every "
            "pool ramps from its floor toward its ceiling, and heavy_parse "
            "(~%.2fGiB/slot), index_heavy (~%.2fGiB/slot) and index_light "
            "(~%.2fGiB/slot) are additionally held to what free memory can hold",
            initial_snapshot.source,
            initial_snapshot.cpu_quota,
            _fmt_bytes(initial_snapshot.mem_limit_bytes),
            self._ceilings.heavy,
            self._ceilings.light,
            self._ceilings.index_heavy,
            self._ceilings.index_light,
            self._worker_count,
            self._registry.get(Pool.HEAVY_PARSE),
            self._registry.get(Pool.LIGHT_PARSE),
            self._registry.get(Pool.INDEX_HEAVY),
            self._registry.get(Pool.INDEX_LIGHT),
            HEAVY_PARSE_SLOTS_PER_CPU,
            LIGHT_PARSE_SLOTS_PER_CPU,
            INDEX_HEADROOM,
            HEAVY_PARSE_WORKING_SET_GB,
            INDEX_HEAVY_WORKING_SET_GB,
            INDEX_LIGHT_WORKING_SET_GB,
        )
        if reserve_embedding_cpus:
            # Mirrors resolve_ceilings: the reservation is capped at a share of
            # the quota so it cannot flatten heavy on a small host.
            reservation = min(
                EMBEDDING_CPU_RESERVATION,
                initial_snapshot.cpu_quota * EMBEDDING_CPU_RESERVATION_MAX_FRACTION,
            )
            self._logger.info(
                "ResourceGovernor: local CPU embedding model configured — %.2f of "
                "%.2f CPU held back from the heavy-parse ceiling (derived from the "
                "remaining %.2f) so the co-located embedding server keeps cores to "
                "embed with while Docling is converting; set "
                "GOVERNOR_EMBEDDING_CPU_RESERVATION=0 to size heavy off the full "
                "quota, or raise it if embedding is still starved",
                reservation,
                initial_snapshot.cpu_quota,
                max(0.0, initial_snapshot.cpu_quota - reservation),
            )
        self._logger.info(
            "ResourceGovernor start-rate limiters: heavy_parse=%.1f/s (burst %d) "
            "— sustained admission rate for this pool is capped independently "
            "of its concurrency limit; raise MAX_CONCURRENT_PARSING if this "
            "rate looks like the real bottleneck",
            1.0 / heavy_rate_interval, heavy_rate_capacity,
        )
        if initial_snapshot.mem_baseline_bytes is not None:
            self._logger.info(
                "ResourceGovernor memory baseline: %s subtracted from raw working "
                "set (raw=%s adjusted=%s) before computing mem_pressure — this is "
                "memory the probe attributes to co-located idle services (e.g. "
                "Docling's model weights) rather than this pool's own workload; "
                "override with GOVERNOR_BASELINE_MEMORY_MB if this looks wrong",
                _fmt_bytes(initial_snapshot.mem_baseline_bytes),
                _fmt_bytes(initial_snapshot.mem_working_set_raw_bytes),
                _fmt_bytes(initial_snapshot.mem_working_set_bytes),
            )
        else:
            self._logger.info(
                "ResourceGovernor memory baseline: still calibrating (first few "
                "samples use raw mem_pressure unadjusted); set "
                "GOVERNOR_BASELINE_MEMORY_MB to skip auto-calibration"
            )

    # -- gates ------------------------------------------------------------

    def gate(self, pool: Pool) -> AdmissionGate:
        """Return the (memoised) ``AdmissionGate`` for *pool*.

        Intended to be called once per pool from the single event loop that
        will actually use it (the consumer's worker loop or the service's
        uvicorn loop). A second, different loop calling this for the same
        pool receives the same gate object and will hit the ``RuntimeError``
        documented on ``AdmissionGate`` — the intended safety net, since
        this process only ever runs one active consumer/route loop per pool.
        """
        with self._gates_lock:
            existing = self._gates.get(pool)
            if existing is not None:
                return existing
            created = AdmissionGate(
                pool,
                self._registry,
                rate_limiter=self._rate_limiters.get(pool),
                clock=self._clock,
            )
            self._gates[pool] = created
            return created

    @property
    def ceilings(self) -> Ceilings:
        return self._ceilings

    # -- sampling loop -----------------------------------------------------

    async def run(self) -> None:
        """Sample resources and adjust limits until cancelled.

        Safe to run as a background task from a service's lifespan — a
        cancellation simply stops future sampling; already-issued limits
        are left as-is (never revoked, per plan principle 4).
        """
        self._running = True
        try:
            while self._running:
                delay = self._sample_interval + self._rng() * self._jitter
                await self._sleep(delay)
                if not self._running:
                    break
                try:
                    await self._sample_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception("ResourceGovernor sample failed; keeping previous limits")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        """Unsubscribe every gate created by this governor from the
        registry (see ``AdmissionGate.close``).

        Call once at shutdown, after ``stop()``/awaiting the sample task —
        a gate that outlives the event loop it was bound to would otherwise
        leak a registry callback that bounces onto a closed loop the next
        time a limit changes (harmless for a process that is exiting
        anyway, but avoided here since some lifespans re-run within the
        same process, e.g. under a test harness or a reload).
        """
        with self._gates_lock:
            gates = list(self._gates.values())
            self._gates.clear()
        for gate in gates:
            gate.close()

    async def _sample_once(self) -> None:
        now = self._clock()
        snapshot = await asyncio.to_thread(self._probe.snapshot)
        demand = self._drain_all_demand()

        # Held across the registry snapshot, the limit calculation, and the
        # resulting writes so report_memory_incident() (called from another
        # thread) can't have its emergency halving clobbered by a sample
        # that read the registry before the incident but writes after it.
        changed: list[tuple[Pool, int, int]] = []
        with self._state_lock:
            current = self._registry.snapshot()
            new_limits, new_state = next_limits(
                current=current,
                snap=snapshot,
                ceilings=self._ceilings,
                state=self._state,
                demand=demand,
                now=now,
                interval=self._sample_interval,
            )
            self._state = new_state

            for pool in Pool:
                new_value = new_limits.get(pool)
                old_value = current.get(pool)
                if self._registry.set(pool, new_value):
                    changed.append((pool, old_value, new_value))

        with self._stats_lock:
            self._last_snapshot = snapshot
            self._last_demand = demand

        if changed:
            self._logger.info(
                "ResourceGovernor limits changed: %s (mem_pressure=%s cpu_util=%s source=%s)",
                ", ".join(f"{pool.value}:{old}->{new}" for pool, old, new in changed),
                _fmt_ratio(snapshot.mem_pressure),
                _fmt_ratio(snapshot.cpu_utilisation),
                snapshot.source,
            )
            shrank = any(new_value < old_value for _, old_value, new_value in changed)
            if shrank and snapshot.mem_baseline_bytes:
                # Surface raw and adjusted side by side so an operator can
                # see how much of the cgroup a co-located idle service is
                # holding, and judge whether the shrink reflects real
                # workload growth or a container that is simply too small
                # for its resident footprint.
                self._logger.info(
                    "ResourceGovernor shrink context: raw_mem_pressure=%s "
                    "(working_set=%s of %s) adjusted_mem_pressure=%s "
                    "(baseline=%s subtracted from both sides, usable=%s) — "
                    "raise GOVERNOR_MEM_SOFT if this deployment can safely "
                    "run closer to its cgroup limit",
                    _fmt_ratio(
                        snapshot.mem_working_set_raw_bytes / snapshot.mem_limit_bytes
                        if snapshot.mem_working_set_raw_bytes is not None and snapshot.mem_limit_bytes
                        else None
                    ),
                    _fmt_bytes(snapshot.mem_working_set_raw_bytes),
                    _fmt_bytes(snapshot.mem_limit_bytes),
                    _fmt_ratio(snapshot.mem_pressure),
                    _fmt_bytes(snapshot.mem_baseline_bytes),
                    _fmt_bytes(snapshot.mem_usable_bytes),
                )

        rate_limited = {
            pool: demand[pool].rate_limited_acquires
            for pool in Pool
            if demand[pool].rate_limited_acquires > 0
        }
        if rate_limited:
            with self._gates_lock:
                in_use_by_pool = {
                    pool: self._gates[pool].in_use for pool in rate_limited if pool in self._gates
                }
            self._logger.info(
                "ResourceGovernor start-rate limiter denied admission(s) this interval: %s "
                "(current in_use/limit: %s) — if in_use stays well below limit, throughput is "
                "capped by this burst smoother, not the concurrency limit",
                ", ".join(f"{pool.value}:{count}" for pool, count in rate_limited.items()),
                ", ".join(
                    f"{pool.value}={in_use_by_pool.get(pool, '?')}/{new_limits.get(pool)}"
                    for pool in rate_limited
                ),
            )

    def _drain_all_demand(self) -> dict[Pool, PoolDemand]:
        with self._gates_lock:
            gates = dict(self._gates)
        return {
            pool: gates[pool].drain_demand() if pool in gates else PoolDemand.empty()
            for pool in Pool
        }

    # -- fast-reacting incident path ---------------------------------------

    def report_memory_incident(self, reason: str) -> None:
        """Immediately halve the heavy-parse pool and start an incident
        cooldown, without waiting for the next sample (plan section 4,
        "Memory incident feedback"). Safe to call from any thread — e.g. the
        PDF rasterizer's ``BrokenProcessPool`` handler or a ``MemoryError``
        catch, both of which react faster than the sample loop ever could.
        """
        now = self._clock()
        with self._state_lock:
            floor = floor_for(Pool.HEAVY_PARSE, self._ceilings.heavy)
            current = self._registry.get(Pool.HEAVY_PARSE)
            self._registry.set(Pool.HEAVY_PARSE, max(floor, current // 2))
            self._state = self._state.with_update(Pool.HEAVY_PARSE, replace(
                self._state.get(Pool.HEAVY_PARSE),
                healthy_streak=0,
                cooldown_until=now + INCIDENT_COOLDOWN_SECONDS,
            ))
        self._logger.warning(
            "ResourceGovernor: memory incident reported (%s); heavy_parse halved", reason,
        )

    # -- observability ------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Snapshot for health endpoints / logs. Safe from any thread."""
        limits = self._registry.snapshot()
        with self._stats_lock:
            snapshot = self._last_snapshot
            demand = dict(self._last_demand)
        with self._gates_lock:
            in_use = {pool.value: gate.in_use for pool, gate in self._gates.items()}
        return {
            "probe_source": snapshot.source,
            "cpu_quota": snapshot.cpu_quota,
            "cpu_utilisation": snapshot.cpu_utilisation,
            # Both can trip the CPU brake on their own, so a pool pinned at
            # its floor is unexplainable from cpu_utilisation alone.
            "cpu_pressure": snapshot.cpu_pressure,
            "cpu_throttled_ratio": snapshot.cpu_throttled_ratio,
            "mem_pressure": snapshot.mem_pressure,
            # The value every brake actually acts on. Without it a pool pinned
            # at its floor is unexplainable from the payload: mem_pressure is
            # baseline-credited and can read comfortably low while
            # mem_pressure_raw is over MEM_SOFT.
            "mem_pressure_raw": snapshot.mem_pressure_raw,
            "mem_limit_bytes": snapshot.mem_limit_bytes,
            "mem_usable_bytes": snapshot.mem_usable_bytes,
            "mem_working_set_raw_bytes": snapshot.mem_working_set_raw_bytes,
            "mem_baseline_bytes": snapshot.mem_baseline_bytes,
            "worker_count": self._worker_count,
            "ceilings": {
                "heavy_parse": self._ceilings.heavy,
                "light_parse": self._ceilings.light,
                "index": self._ceilings.index,
                "index_heavy": self._ceilings.index_heavy,
                "index_light": self._ceilings.index_light,
            },
            "limits": {pool.value: limits.get(pool) for pool in Pool},
            "in_use": in_use,
            "demand": {
                pool.value: {
                    "utilisation": demand[pool].utilisation(limits.get(pool), self._sample_interval),
                    "blocked_acquires": demand[pool].blocked_acquires,
                    "completions": demand[pool].completions,
                    "rate_limited_acquires": demand[pool].rate_limited_acquires,
                }
                for pool in Pool
            },
        }
