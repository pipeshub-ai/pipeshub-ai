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
    INCIDENT_COOLDOWN_SECONDS,
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
        self._ceilings: Ceilings = resolve_ceilings(initial_snapshot, env_parse, env_index, self._worker_count)

        self._state_lock = threading.Lock()
        self._registry = LimitRegistry(warm_start_limits(self._ceilings, env_parse, env_index))
        self._state = ControllerState.initial()

        self._gates_lock = threading.Lock()
        self._gates: dict[Pool, AdmissionGate] = {}
        # HEAVY_PARSE's admission rate scales with the heavy-parse ceiling;
        # DOWNLOAD_BYTES scales with the *index* ceiling (every record —
        # heavy or light tier — reserves a DOWNLOAD_BYTES permit before any
        # parse slot is even requested, so its admission rate should track
        # overall record throughput, not the derived byte budget).
        heavy_rate_interval, heavy_rate_capacity = start_rate_limiter_params(self._ceilings.heavy)
        download_rate_interval, download_rate_capacity = start_rate_limiter_params(self._ceilings.index)
        self._rate_limiters: dict[Pool, StartRateLimiter] = {
            Pool.HEAVY_PARSE: StartRateLimiter(
                heavy_rate_interval, heavy_rate_capacity, clock=clock,
            ),
            Pool.DOWNLOAD_BYTES: StartRateLimiter(
                download_rate_interval, download_rate_capacity, clock=clock,
            ),
        }

        self._stats_lock = threading.Lock()
        self._last_snapshot: ResourceSnapshot = initial_snapshot
        self._last_demand: dict[Pool, PoolDemand] = {pool: PoolDemand.empty() for pool in Pool}

        self._running = False

        self._logger.info(
            "ResourceGovernor initialised: probe_source=%s cpu_quota=%.2f mem_limit=%s "
            "ceilings(heavy_parse=%d light_parse=%d index=%d download_bytes=%s) worker_count=%d",
            initial_snapshot.source,
            initial_snapshot.cpu_quota,
            _fmt_bytes(initial_snapshot.mem_limit_bytes),
            self._ceilings.heavy,
            self._ceilings.light,
            self._ceilings.index,
            _fmt_bytes(self._ceilings.bytes_max),
            self._worker_count,
        )
        self._logger.info(
            "ResourceGovernor start-rate limiters: heavy_parse=%.1f/s (burst %d) "
            "download_bytes=%.1f/s (burst %d) — sustained admission rate for "
            "these two pools is capped independently of their concurrency "
            "limit; raise MAX_CONCURRENT_PARSING/MAX_CONCURRENT_INDEXING if "
            "this rate looks like the real bottleneck",
            1.0 / heavy_rate_interval, heavy_rate_capacity,
            1.0 / download_rate_interval, download_rate_capacity,
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

    async def _sample_once(self) -> None:
        now = self._clock()
        snapshot = await asyncio.to_thread(self._probe.snapshot)
        demand = self._drain_all_demand()
        current = self._registry.snapshot()

        with self._state_lock:
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

        changed: list[tuple[Pool, int, int]] = []
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
                # Baseline subtraction can be *why* pressure still forced a
                # shrink despite genuinely idle CPU — surface both readings
                # so an operator doesn't have to guess whether the fix in
                # probe.py's BaselineMemoryTracker is under-crediting a
                # co-located idle service.
                self._logger.info(
                    "ResourceGovernor shrink context: raw_mem_pressure=%s "
                    "(working_set=%s) adjusted_mem_pressure=%s (baseline "
                    "subtracted=%s) — raise GOVERNOR_BASELINE_MEMORY_MB or "
                    "GOVERNOR_MEM_SOFT if the baseline looks too small for "
                    "this deployment's idle footprint",
                    _fmt_ratio(
                        snapshot.mem_working_set_raw_bytes / snapshot.mem_limit_bytes
                        if snapshot.mem_working_set_raw_bytes is not None and snapshot.mem_limit_bytes
                        else None
                    ),
                    _fmt_bytes(snapshot.mem_working_set_raw_bytes),
                    _fmt_ratio(snapshot.mem_pressure),
                    _fmt_bytes(snapshot.mem_baseline_bytes),
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
        """Immediately halve the memory-relevant pools and start an incident
        cooldown, without waiting for the next sample (plan section 4,
        "Memory incident feedback"). Safe to call from any thread — e.g. the
        PDF rasterizer's ``BrokenProcessPool`` handler or a ``MemoryError``
        catch, both of which react faster than the sample loop ever could.
        """
        now = self._clock()
        affected = (Pool.HEAVY_PARSE, Pool.DOWNLOAD_BYTES)
        pool_ceiling = {Pool.HEAVY_PARSE: self._ceilings.heavy, Pool.DOWNLOAD_BYTES: self._ceilings.bytes_max}
        with self._state_lock:
            state = self._state
            for pool in affected:
                ceiling = pool_ceiling[pool]
                floor = floor_for(pool, ceiling)
                current = self._registry.get(pool)
                self._registry.set(pool, max(floor, current // 2))
                state = state.with_update(pool, replace(
                    state.get(pool), healthy_streak=0, cooldown_until=now + INCIDENT_COOLDOWN_SECONDS,
                ))
            self._state = state
        self._logger.warning(
            "ResourceGovernor: memory incident reported (%s); heavy_parse/download_bytes halved", reason,
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
            "mem_pressure": snapshot.mem_pressure,
            "mem_limit_bytes": snapshot.mem_limit_bytes,
            "mem_working_set_raw_bytes": snapshot.mem_working_set_raw_bytes,
            "mem_baseline_bytes": snapshot.mem_baseline_bytes,
            "worker_count": self._worker_count,
            "ceilings": {
                "heavy_parse": self._ceilings.heavy,
                "light_parse": self._ceilings.light,
                "index": self._ceilings.index,
                "download_bytes": self._ceilings.bytes_max,
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
