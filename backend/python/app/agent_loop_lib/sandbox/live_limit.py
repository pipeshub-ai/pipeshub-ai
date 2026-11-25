"""The live limit: how many code sandboxes may start now, between the floor
and the ceiling, from a reading of the machine they run on.

Every query worker runs a sampler. There is no leader: the limit lives in one
shared record (``live_record.py``), and whichever worker samples first after
the spacing (85 % of ``SANDBOX_LIVE_INTERVAL_S``) takes the step, so the
container steps once per interval whatever the number of workers.

Each step (``step()``, pure) moves the limit toward a memory target: the runs
that fit in free memory above a soft line, counting each at its full memory
limit. It halves the limit below a hard floor or on memory pressure, takes
one off while the CPU is busy, and grows only while runs wait for it, by slow
start (1, 2, 4, ...) capped at the target, after a cooldown.

A 15 s sample cannot see a burst, so each run also checks free memory itself
once it has a slot (``admit_and_charge()``): a run that would leave less than
the hard floor gives its slot back and waits. Each run admitted is charged in
the record for the burst window after its own start, since its memory is not
yet in use. That check is the only thing that holds back the floor's runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import random
import threading
import time
import weakref
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from app.agent_loop_lib.sandbox.capacity import HoldReason, LiveView
from app.agent_loop_lib.sandbox.host_reading import (
    HostSample,
    LiveMode,
    SidecarReader,
    resolve_live_mode,
)
from app.agent_loop_lib.sandbox.live_record import (
    BURST_SLOTS,
    LiveLedger,
    LiveRecord,
    LiveTxn,
)
from app.agent_loop_lib.sandbox.live_settings import MAX_MEMORY_OVERCOMMIT, LiveSettings
from app.core.org_context import spawn_detached
from app.services.resource_governor.lane_ledger import pid_alive
from app.services.resource_governor.shared_slots import sandbox_state_directory

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.sandbox.capacity import SandboxCapacity
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor

__all__ = [
    "LiveLimit",
    "LiveProfile",
    "cgroup_profile",
    "get_live_limit",
    "host_profile",
    "initial_record",
    "sidecar_profile",
    "step",
]

logger = logging.getLogger(__name__)

MiB = 2**20
GiB = 2**30

# A step is taken at most once per this share of the interval, by any worker.
STEP_SPACING = 0.85
JITTER_S = 1.0
SHRINK_COOLDOWN_S = 30.0
HARD_COOLDOWN_S = 60.0
# A record nobody stepped for this long, or whose last stepper died this long
# ago, is started afresh, so a restarted stack does not inherit an old shrink.
STALE_S = 120.0
DEAD_OWNER_S = 30.0
READING_LOST_S = 300.0
RERESOLVE_S = 300.0
# While the machine has not been read yet, resolve again sooner: after 15 s,
# doubling up to RERESOLVE_S.
UNREAD_RERESOLVE_S = 15.0
FIRST_READING_WAIT_S = 30.0
STEP_LOCK_BUDGET = 0.020
ADMIT_LOCK_BUDGET = 0.050
HELD_BUDGET = 0.050
STAMP_EVERY_S = 1.0
# How far ahead of a reader's clock another worker's time may be; further
# ahead, it was written before the clock restarted.
CLOCK_SLACK_S = 1.0
VIEW_TTL_S = 1.0
UNKNOWN_LOG_EVERY_S = 300.0
# Grow only well below the CPU brakes, so the limit does not saw-tooth.
GROW_UTIL_MARGIN = 0.10
GROW_PSI_MARGIN = 0.25
GROW_THROTTLE_DIVISOR = 5.0
# Over-commit needs a live reading of the whole machine to catch heavy runs.
HELM_MAX_OVERCOMMIT = 2.0


@dataclass(frozen=True)
class LiveProfile:
    """How one kind of reading is turned into a limit.

    The memory lines are either fixed sizes (``floor_bytes`` / ``soft_bytes``,
    or the defaults of a share of the total within bounds), or shares of the
    total (``floor_share`` / ``soft_share``).
    """

    name: Literal["host", "cgroup", "sidecar"]
    # hard = avail < floor + one run, or avail < floor.
    hard_adds_run: bool = True
    mem_psi_hard: float = 0.05
    # The target counts held runs as already placed: what they use is in the
    # reading, so only runs not yet started need a full run's memory.
    credit_running: bool = False
    cpu_brake: float = 0.90
    cpu_psi_brake: float | None = 0.75
    cpu_throttle_brake: float | None = None
    cpu_sustain: int = 2
    burst_window_s: float = 5.0
    # After READING_LOST_S without a valid sample: hold the limit, or go back
    # to the fixed ceiling.
    on_reading_lost: Literal["hold", "static_ceiling"] = "hold"
    # Whether a run waiting for memory may ask indexing to slow down: only
    # where sandboxes and indexing share the memory.
    yields_to_indexing: bool = True
    admit_cache_s: float = 0.25
    floor_bytes: int | None = None
    soft_bytes: int | None = None
    floor_share: float | None = None
    soft_share: float | None = None

    def memory_lines(self, total: int, m: float) -> tuple[int, int]:
        """``(hard_floor, soft)`` in bytes for a machine of *total* bytes
        and runs of *m* bytes."""
        if self.floor_share is not None and self.soft_share is not None:
            return int(self.floor_share * total), int(self.soft_share * total)
        hard = self.floor_bytes or _clamp(int(0.05 * total), GiB, 8 * GiB)
        if self.soft_bytes is not None:
            soft = _clamp(self.soft_bytes, int(hard + m), max(int(hard + m), total))
        else:
            soft = max(int(hard + 2 * m), _clamp(int(0.15 * total), 2 * GiB, 24 * GiB))
        return hard, soft


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def host_profile(settings: LiveSettings, name: Literal["host", "cgroup"] = "host") -> LiveProfile:
    return LiveProfile(
        name=name,
        cpu_brake=settings.host_cpu_brake,
        cpu_psi_brake=settings.host_cpu_psi_brake,
        floor_bytes=settings.host_mem_floor_mb * MiB if settings.host_mem_floor_mb else None,
        soft_bytes=settings.host_mem_soft_mb * MiB if settings.host_mem_soft_mb else None,
    )


def cgroup_profile(settings: LiveSettings) -> LiveProfile:
    return host_profile(settings, "cgroup")


def sidecar_profile(settings: LiveSettings) -> LiveProfile:
    """A Docker-in-Docker sidecar holds only the sandboxes and is sized for
    the ceiling, so its lines sit near its limit; a lost reading falls back
    to the fixed ceiling, which is safe there on its own."""
    return LiveProfile(
        name="sidecar",
        hard_adds_run=False,
        mem_psi_hard=0.10,
        credit_running=True,
        cpu_brake=settings.sidecar_cpu_brake,
        # The sidecar's cpu.pressure mixes in each sandbox's own CPU quota.
        cpu_psi_brake=None,
        cpu_throttle_brake=settings.sidecar_cpu_throttle,
        # Its CPU quota throttles in short bursts: one busy sample must not move it.
        cpu_sustain=3,
        # The exporter writes every 5 s and the reading is cached for 2 s.
        burst_window_s=10.0,
        on_reading_lost="static_ceiling",
        # Indexing cannot free the sidecar's memory.
        yields_to_indexing=False,
        admit_cache_s=2.0,
        floor_share=1.0 - settings.sidecar_mem_hard,
        soft_share=1.0 - settings.sidecar_mem_high,
    )


def _usable(sample: HostSample | None) -> bool:
    return sample is not None and sample.mem_total is not None and sample.mem_available is not None


def _target(
    sample: HostSample, profile: LiveProfile, m: float, soft: int, ceiling: int, floor: int, held: int,
) -> int:
    assert sample.mem_available is not None
    spare = math.floor((sample.mem_available - soft) / m)
    return _clamp(held + spare if profile.credit_running else spare, floor, ceiling)


def _previous_target_after_growth(rec: LiveRecord) -> int | None:
    """The previous sample's target when the limit last moved up, else None.

    The runs a grow lets in take memory, so the next sample's target dips
    under the limit they were let in at; shrinking on that one sample, then
    growing again for the next waiter, turned the limit around about twice a
    minute. Reversing a grow therefore needs the target below the limit on
    two samples in a row. A hard floor or memory pressure still halves at
    once, and each run still checks free memory before it starts.
    """
    # Only a grow leaves the slow-start step above 1; every shrink resets it.
    if rec.slow_start_step <= 1 or rec.target is None or rec.sample_at < rec.last_step_at:
        return None
    return rec.target


def live_charges(
    rec: LiveRecord, now: float, window_s: float, max_bytes: int | None = None,
) -> tuple[tuple[float, int], ...]:
    """The burst charges still in their window. One ending well over a
    window from now was written before the clock restarted (a host reboot);
    the slack covers a worker that read the clock just before another one
    charged. With *max_bytes* (one run's memory times the slots), no charge
    counts for more: a burst cannot admit more runs than there are slots, so
    a larger one is damaged and must not refuse every run."""
    return tuple(
        (until, size if max_bytes is None else min(size, max_bytes))
        for until, size in rec.charges if now < until <= now + window_s + CLOCK_SLACK_S
    )


def _add_charge(
    charges: tuple[tuple[float, int], ...], until: float, size: int,
) -> tuple[tuple[float, int], ...]:
    """*charges* plus one, merging the two oldest while there are too many:
    the merged charge ends with the later of them, so none ends early and
    none outlives the newest."""
    merged = sorted([*charges, (until, size)])
    while len(merged) > BURST_SLOTS:
        (_, first), (end, second) = merged[0], merged[1]
        merged[:2] = [(end, first + second)]
    return tuple(merged)


def _shared_cpu(sample: HostSample | None, rec: LiveRecord | None) -> HostSample | None:
    """*sample* with its CPU use measured from the counters of the last
    step, whichever worker took it, rather than from this worker's own
    previous reading, which is several steps old when workers take turns."""
    if sample is None or sample.cpu_counters is None or rec is None or rec.cpu_total <= 0:
        return sample
    d_busy = sample.cpu_counters[0] - rec.cpu_busy
    d_total = sample.cpu_counters[1] - rec.cpu_total
    if d_total <= 0 or d_busy < 0:
        # The counters restarted (a reboot): keep this worker's own reading.
        return sample
    return replace(sample, cpu_util=min(1.0, d_busy / d_total))


def _sample_fields(sample: HostSample, now: float) -> dict[str, Any]:
    return {
        "sample_at": now, "last_valid_at": now,
        "mem_total": sample.mem_total, "mem_available": sample.mem_available,
    }


def initial_record(
    previous: LiveRecord | None, sample: HostSample | None, *, profile: LiveProfile, m: float,
    ceiling: int, floor: int, held: int, now: float, pid: int,
) -> LiveRecord:
    """A fresh record, warm-started at the memory target (at the ceiling when
    nothing could be read). Keeps the burst charge of *previous*."""
    kept = previous is not None and previous.initialised
    base = LiveRecord(
        ceiling=ceiling, min=floor, stamp=now, owner_pid=pid, last_step_at=now,
        # Without a reading, how long it has been missing carries over.
        last_valid_at=previous.last_valid_at if kept and not _usable(sample) else now,
        # Growth from a fresh start doubles too, as after a shrink: at +1 a
        # large host would take many minutes to reach a freed target.
        in_slow_start=True, slow_start_step=1,
        charges=previous.charges if previous else (),
    )
    if not _usable(sample):
        return replace(base, limit=ceiling, rule="start")
    assert sample is not None and sample.mem_total is not None
    _, soft = profile.memory_lines(sample.mem_total, m)
    target = _target(sample, profile, m, soft, ceiling, floor, held)
    return replace(
        base, limit=target, target=target, rule="start",
        shrink_cause="memory" if target < ceiling else None, **_sample_fields(sample, now),
    )


def step(
    rec: LiveRecord, sample: HostSample | None, *, profile: LiveProfile, m: float, ceiling: int,
    floor: int, held: int, now: float,
) -> LiveRecord:
    """One sample's step from *rec*. Pure."""
    limit = _clamp(rec.limit, floor, ceiling)
    # A run waited at the limit since the previous step: only then can
    # growth help anyone. A stamp from the future is not a wait.
    waiting = 0 < rec.last_wait_limit_at <= now + CLOCK_SLACK_S and rec.last_wait_limit_at >= rec.last_step_at
    settled_floor = _previous_target_after_growth(rec)
    rec = replace(
        rec, last_step_at=now, stamp=now, ceiling=ceiling, min=floor,
        # No step sets a cooldown longer than HARD_COOLDOWN_S: one ending
        # later (a damaged or forged record) must not hold the limit down.
        cooldown_until=min(rec.cooldown_until, now + HARD_COOLDOWN_S),
    )
    if not _usable(sample):
        return replace(rec, limit=limit, rule="unknown", cpu_high_streak=0)
    assert sample is not None and sample.mem_total is not None and sample.mem_available is not None
    hard_floor, soft = profile.memory_lines(sample.mem_total, m)
    target = _target(sample, profile, m, soft, ceiling, floor, held)
    hard = (
        sample.mem_available < hard_floor + (m if profile.hard_adds_run else 0)
        or (sample.mem_psi_full_avg10 or 0.0) > profile.mem_psi_hard
        or (sample.mem_oom_events or 0) > 0
    )
    util = sample.cpu_util or 0.0
    psi = sample.cpu_psi_some_avg10 or 0.0
    throttled = sample.cpu_throttled_ratio or 0.0
    cpu_high = (
        util > profile.cpu_brake
        or (profile.cpu_psi_brake is not None and psi > profile.cpu_psi_brake)
        or (profile.cpu_throttle_brake is not None and throttled > profile.cpu_throttle_brake)
    )
    streak = rec.cpu_high_streak + 1 if cpu_high else 0
    rec = replace(rec, target=target, cpu_high_streak=streak, **_sample_fields(sample, now))

    # (limit, priority, rule): the tightest wins, memory before CPU on a tie.
    candidates: list[tuple[int, int, str]] = []
    if hard:
        candidates.append((max(floor, limit // 2), 0, "mem_hard"))
    settled = target if settled_floor is None else max(target, settled_floor)
    if limit > settled:
        candidates.append((max(settled, limit - math.ceil((limit - settled) / 2)), 1, "mem_target"))
    if streak >= profile.cpu_sustain:
        candidates.append((max(floor, limit - 1), 2, "cpu"))
    if candidates:
        new, _, rule = min(candidates)
        cause = "cpu" if rule == "cpu" else "memory"
        return replace(
            rec, limit=new, rule=rule, in_slow_start=True, slow_start_step=1,
            cooldown_until=now + (HARD_COOLDOWN_S if rule == "mem_hard" else SHRINK_COOLDOWN_S),
            shrink_cause=cause if new < ceiling else None,
        )

    grow_band = (
        # The margins shrink with a low brake, so an idle CPU can still grow.
        util < profile.cpu_brake - min(GROW_UTIL_MARGIN, profile.cpu_brake / 2)
        and (
            profile.cpu_psi_brake is None
            or psi < profile.cpu_psi_brake - min(GROW_PSI_MARGIN, profile.cpu_psi_brake / 2)
        )
        and (profile.cpu_throttle_brake is None or throttled < profile.cpu_throttle_brake / GROW_THROTTLE_DIVISOR)
    )
    new, slow_step = limit, rec.slow_start_step
    if not waiting:
        rule = "no_demand"
    elif now < rec.cooldown_until:
        rule = "cooldown"
    elif not grow_band:
        rule = "cpu_hold"
    elif limit >= min(target, ceiling):
        rule = "at_ceiling" if limit >= ceiling else "at_target"
    else:
        increment = rec.slow_start_step if rec.in_slow_start else 1
        new = min(target, ceiling, limit + increment)
        slow_step = min(2 * increment, ceiling)
        rule = "grow"
    return replace(
        rec, limit=new, rule=rule, slow_start_step=slow_step,
        shrink_cause=None if new >= ceiling else (rec.shrink_cause or "memory"),
    )


def _gib(value: float | None) -> str:
    return "?" if value is None else f"{value / GiB:.1f}GiB"


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"


_DEFAULT = object()


class LiveLimit:
    """The ``LimitProvider`` that follows the machine's reading.

    Built by ``get_default_governor()`` for docker and local mode; the query
    service starts and stops its sampler. Until the sampler has decided what
    to read, and whenever there is nothing trustworthy to read, the limit is
    the ceiling and no per-run check is made.
    """

    def __init__(
        self,
        capacity: SandboxCapacity,
        governor: SandboxResourceGovernor | None,
        *,
        settings: LiveSettings | None = None,
        resolver: Callable[[], LiveMode] | None = None,
        directory: Any = _DEFAULT,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._capacity = capacity
        self._governor = weakref.ref(governor) if governor is not None else None
        self._settings = settings or LiveSettings.from_env()
        self._resolver = resolver or (lambda: resolve_live_mode(capacity, self._settings))
        self._clock = clock
        self._rng = rng or random.Random()
        self._pid = os.getpid()
        self._ledger = self._open_ledger(sandbox_state_directory() if directory is _DEFAULT else directory)
        self._mode = LiveMode.off("starting", "the sampler has not started")
        self._profile: LiveProfile | None = None
        self._overcommit = 1.0
        self._resolved_at: float | None = None
        self._unread_tries = 0
        self._lock = threading.Lock()
        self._view: tuple[float, LiveView] | None = None
        self._last_limit: int | None = None
        self._stamped: dict[str, float] = {}
        self._admit_reading: tuple[float, int | None, int | None] | None = None
        self._last_held = 0
        self._unknown_logged_at: float | None = None
        self._admit_error_logged_at: float | None = None
        self._corrupt_logged = False
        self._floor_logged = False
        self._task: asyncio.Task[None] | None = None
        # Called (on the event loop, never blocking) while a run waits for
        # memory where indexing shares it; see ``waiting()``.
        self.on_memory_wait: Callable[[HoldReason, float], None] | None = None
        # Called off the event loop after each sampler pass.
        self.after_sample: Callable[[], None] | None = None

    @staticmethod
    def _open_ledger(directory: Any) -> LiveLedger:
        if directory is not None:
            try:
                return LiveLedger(directory)
            except OSError as exc:
                logger.warning(
                    "sandbox live limit: the state directory cannot be used (%s); each worker "
                    "keeps its own live limit", exc,
                )
        return LiveLedger(None)

    # -- mode ----------------------------------------------------------------

    @property
    def mode(self) -> LiveMode:
        return self._mode

    @property
    def profile(self) -> LiveProfile | None:
        return self._profile

    @property
    def settings(self) -> LiveSettings:
        return self._settings

    def _profile_for(self, mode: LiveMode, *, log: bool) -> LiveProfile | None:
        if mode.profile == "host":
            return host_profile(self._settings)
        if mode.profile == "cgroup":
            return cgroup_profile(self._settings)
        if mode.profile == "sidecar":
            if log and self._settings.host_tuned:
                logger.warning(
                    "sandbox live limit: SANDBOX_HOST_MEM_* / SANDBOX_HOST_CPU_* are ignored for the "
                    "Docker-in-Docker sidecar; use SANDBOX_SIDECAR_*",
                )
            return sidecar_profile(self._settings)
        return None

    def apply_mode(self, mode: LiveMode) -> None:
        """Use *mode* from now on, logging a change. Never reads the machine:
        it runs on the event loop."""
        changed = mode.mode != self._mode.mode
        self._unread_tries = self._unread_tries + 1 if mode.mode == "off:unread" else 0
        self._mode = mode
        self._profile = self._profile_for(mode, log=changed) if mode.on else None
        self._overcommit = self._effective_overcommit(mode, log=changed)
        self._resolved_at = self._clock()
        with self._lock:
            self._view = None
            self._admit_reading = None
        if not changed:
            return
        if mode.on and mode.reason:
            logger.warning("sandbox live limit: %s", mode.reason)
        elif mode.on:
            logger.info(
                "sandbox live limit: on (source=%s, same machine: %s) ceiling=%d floor=%d",
                mode.reader.source if mode.reader else "?", mode.note or "-",
                self._ceiling(self._overcommit), self._capacity.floor(),
            )
        else:
            level = (
                logging.INFO if mode.mode in ("off:disabled", "off:remote", "off:unread")
                else logging.WARNING
            )
            logger.log(
                level, "sandbox live limit: off (%s); runs are capped at the fixed ceiling C=%d",
                mode.reason, self._capacity.ceiling(),
            )

    def _effective_overcommit(self, mode: LiveMode, *, log: bool = True) -> float:
        wanted = self._settings.memory_overcommit
        if wanted <= 1.0:
            return 1.0
        if mode.mode == "on:host" and self._capacity.mode == "compose":
            k = min(wanted, MAX_MEMORY_OVERCOMMIT)
        elif mode.mode == "on:sidecar" and self._capacity.mode == "helm_dind":
            k = min(wanted, HELM_MAX_OVERCOMMIT)
            if log and k < wanted:
                logger.warning(
                    "sandbox memory over-commit %.1fx is capped at %.1fx for the Docker-in-Docker sidecar",
                    wanted, k,
                )
        else:
            if log:
                logger.warning(
                    "sandbox memory over-commit %.1fx forced to 1.0 (%s): it needs a live reading "
                    "of the whole machine", wanted, mode.mode,
                )
            return 1.0
        before, after = self._capacity.ceiling(), self._ceiling(k)
        if log:
            logger.info(
                "sandbox memory over-commit %.1fx (%s, live limit on): ceiling %d->%d%s",
                k, self._capacity.mode, before, after,
                "" if after > before else " (memory does not bind)",
            )
        return k

    def _ceiling(self, k: float) -> int:
        """The ceiling with runs counted at 1/*k* of their memory. An
        explicit or unknown memory term leaves it unchanged."""
        capacity = self._capacity
        mem_term = capacity.mem_term()
        if k <= 1.0 or mem_term is None:
            return capacity.ceiling()
        raised = min(capacity.other_term(), math.floor(mem_term * k), capacity.slot_files)
        return max(capacity.floor(), raised)

    def _run_bytes(self, k: float) -> float:
        run = self._capacity.plan.run
        return (run.memory_bytes if run is not None else 512 * MiB) / k

    def _lost(self, rec: LiveRecord | None, now: float) -> bool:
        return (
            self._profile is not None and self._profile.on_reading_lost == "static_ceiling"
            and rec is not None and rec.initialised and now - rec.last_valid_at >= READING_LOST_S
        )

    def _k(self, rec: LiveRecord | None, now: float) -> float:
        return 1.0 if self._lost(rec, now) else self._overcommit

    # -- the sampler ---------------------------------------------------------

    def start(self) -> None:
        """Start the sampler on the running loop. Safe to call more than once."""
        if self._task is not None and not self._task.done():
            return
        self._task = spawn_detached(self._run(), name="sandbox-live-limit")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def resolve(self) -> None:
        try:
            mode = await asyncio.to_thread(self._resolver)
        except Exception:
            logger.warning("sandbox live limit: choosing what to read failed", exc_info=True)
            mode = LiveMode.off("error", "choosing what to read failed", retry=True)
        self.apply_mode(mode)

    def reresolve_after_s(self) -> float:
        """How long after the last resolve a retryable mode is resolved again."""
        if self._mode.mode == "off:unread" and self._unread_tries > 0:
            # The exponent is bounded: after about a thousand tries the float
            # product would overflow and end the sampler.
            return min(RERESOLVE_S, UNREAD_RERESOLVE_S * 2 ** min(self._unread_tries - 1, 16))
        return RERESOLVE_S

    async def _run(self) -> None:
        # Wait for the first reading of the machine even when the ceiling is
        # explicit: the same-machine check needs the daemon's /info.
        await self._capacity.wait_measured(timeout=FIRST_READING_WAIT_S)
        await self.resolve()
        while True:
            if self._mode.on:
                try:
                    await asyncio.to_thread(self.sample_once)
                except Exception:
                    logger.warning("sandbox live limit: sample failed", exc_info=True)
            self.view(fresh=True)
            if self.after_sample is not None:
                try:
                    await asyncio.to_thread(self.after_sample)
                except Exception:
                    logger.debug("sandbox live limit: after_sample failed", exc_info=True)
            await asyncio.sleep(self._settings.interval_s + self._rng.uniform(0.0, JITTER_S))
            again = self._mode.retry or self._lost(self._ledger.read(), self._clock())
            if again and self._resolved_at is not None and self._clock() - self._resolved_at >= self.reresolve_after_s():
                await self.resolve()

    def _stale(self, rec: LiveRecord, now: float) -> bool:
        age = now - rec.stamp
        if age > STALE_S or age < -CLOCK_SLACK_S:
            return True
        return age > DEAD_OWNER_S and not pid_alive(rec.owner_pid)

    def _fresh_step(self, rec: LiveRecord | None, now: float) -> bool:
        # A step dated in the future is not fresh: it would stop every
        # sampler from stepping until the record went stale.
        return (
            rec is not None and rec.initialised and not self._stale(rec, now)
            and -CLOCK_SLACK_S <= now - rec.last_step_at < STEP_SPACING * self._settings.interval_s
        )

    def _held(self) -> int:
        governor = self._governor() if self._governor is not None else None
        if governor is None:
            return self._last_held
        held = governor.held_total(budget=HELD_BUDGET)
        if held is not None:
            self._last_held = held
        return self._last_held

    def sample_once(self) -> LiveRecord | None:
        """Take this interval's step unless another worker has. Blocking: run
        it off the event loop. Returns the record written, if any."""
        mode, profile = self._mode, self._profile
        if not mode.on or mode.reader is None or profile is None:
            return None
        self._ledger.reopen_if_replaced()
        now = self._clock()
        if self._fresh_step(self._ledger.read(), now):
            return None
        sample = mode.reader.read()
        held = self._held()
        with self._ledger.transaction(STEP_LOCK_BUDGET) as txn:
            if txn is None:
                return None
            # Read under the lock, so no record holds a stamp later than a
            # later stepper's clock: one further ahead predates a reboot.
            now = self._clock()
            rec = txn.read()
            if self._fresh_step(rec, now):
                return None
            sample = _shared_cpu(sample, rec)
            lost = self._lost(rec, now)
            usable = _usable(sample)
            k = 1.0 if lost and not usable else self._overcommit
            ceiling = self._ceiling(k)
            floor, m = min(self._capacity.floor(), ceiling), self._run_bytes(k)
            kwargs = {"profile": profile, "m": m, "ceiling": ceiling, "floor": floor, "held": held, "now": now}
            if rec is None or not rec.initialised or self._stale(rec, now) or (lost and usable):
                new = initial_record(rec, sample, pid=self._pid, **kwargs)
            elif lost:
                new = replace(
                    rec, limit=ceiling, ceiling=ceiling, min=floor, rule="unknown", shrink_cause=None,
                    target=None, last_step_at=now, stamp=now, cpu_high_streak=0,
                )
            elif rec.ceiling != ceiling or rec.min != floor:
                new = initial_record(rec, sample, pid=self._pid, **kwargs)
            else:
                new = step(rec, sample, **kwargs)
            new = replace(new, owner_pid=self._pid)
            if sample is not None and sample.cpu_counters is not None:
                new = replace(new, cpu_busy=sample.cpu_counters[0], cpu_total=sample.cpu_counters[1])
            txn.write(new)
        self._log_step(rec, new, sample, m, now)
        return new

    def _log_step(
        self, old: LiveRecord | None, new: LiveRecord, sample: HostSample | None, m: float, now: float,
    ) -> None:
        if not _usable(sample):
            if self._unknown_logged_at is None or now - self._unknown_logged_at >= UNKNOWN_LOG_EVERY_S:
                self._unknown_logged_at = now
                logger.error(
                    "sandbox live limit: %s reading failed; holding the limit at %d",
                    self._mode.mode, new.limit,
                )
            return
        self._unknown_logged_at = None
        assert sample is not None and sample.mem_total is not None and self._profile is not None
        old_limit = old.limit if old is not None and old.initialised else None
        hard_floor, soft = self._profile.memory_lines(sample.mem_total, m)
        self._check_floor(sample.mem_total, self._profile.memory_lines(sample.mem_total, self._run_bytes(1.0))[0])
        waiting = old is not None and old.last_wait_limit_at > 0 and old.last_wait_limit_at >= old.last_step_at
        level = logging.INFO if old_limit != new.limit else logging.DEBUG
        logger.log(
            level,
            "sandbox live limit %s->%d rule=%s target=%s avail=%s total=%s soft=%s floor=%s cpu=%s "
            "psi=%s throttled=%s held=%d waiting=%s source=%s worker=%d",
            "-" if old_limit is None else old_limit, new.limit, new.rule, new.target,
            _gib(sample.mem_available), _gib(sample.mem_total), _gib(soft), _gib(hard_floor),
            _pct(sample.cpu_util), "?" if sample.cpu_psi_some_avg10 is None else f"{sample.cpu_psi_some_avg10:.2f}",
            _pct(sample.cpu_throttled_ratio), self._last_held, "yes" if waiting else "no",
            sample.source, self._pid,
        )

    # -- LimitProvider -------------------------------------------------------

    def view(self, *, fresh: bool = False) -> LiveView:
        now = self._clock()
        with self._lock:
            cached = self._view
        if not fresh and cached is not None and 0 <= now - cached[0] < VIEW_TTL_S:
            return cached[1]
        view = self._build_view(now)
        with self._lock:
            self._view = (now, view)
            previous, self._last_limit = self._last_limit, view.limit
        if previous is not None and view.limit > previous:
            governor = self._governor() if self._governor is not None else None
            if governor is not None:
                governor.capacity_changed()
        return view

    def _build_view(self, now: float) -> LiveView:
        capacity = self._capacity
        mode = self._mode
        if not mode.on or mode.reader is None:
            ceiling = capacity.ceiling()
            return LiveView(
                limit=ceiling, ceiling=ceiling, floor=capacity.floor(), rule="off",
                shrink_cause=None, target=None, mode=mode.mode, source=None,
                sampled_age_s=None, mem_total_bytes=None, mem_available_bytes=None,
            )
        rec = self._ledger.read()
        lost = self._lost(rec, now)
        ceiling = self._ceiling(1.0 if lost else self._overcommit)
        floor = min(capacity.floor(), ceiling)
        if rec is None or not rec.initialised:
            return LiveView(
                limit=ceiling, ceiling=ceiling, floor=floor, rule="start", shrink_cause=None,
                target=None, mode=mode.mode, source=mode.reader.source, sampled_age_s=None,
                mem_total_bytes=None, mem_available_bytes=None,
            )
        # A record at its own ceiling follows the current one (sizing may have
        # finished since); a shrunk one keeps its limit.
        limit = ceiling if rec.limit >= rec.ceiling else _clamp(rec.limit, floor, ceiling)
        return LiveView(
            limit=limit, ceiling=ceiling, floor=floor, rule=rec.rule or "start",
            shrink_cause=rec.shrink_cause if limit < ceiling else None,  # type: ignore[arg-type]
            target=rec.target, mode="off:reading-lost" if lost else mode.mode,
            source=None if lost else mode.reader.source,
            sampled_age_s=max(0.0, now - rec.sample_at) if rec.sample_at else None,
            mem_total_bytes=None if lost else rec.mem_total,
            mem_available_bytes=None if lost else rec.mem_available,
        )

    async def admit_and_charge(self, *, held: int) -> HoldReason | None:
        if not self._mode.on:
            return None
        try:
            return await asyncio.to_thread(self._admit_locked)
        except Exception:
            # Like an unreadable floor: admit, or a failing reading or record
            # write would fail every run instead of only skipping the check.
            now = self._clock()
            if self._admit_error_logged_at is None or now - self._admit_error_logged_at >= UNKNOWN_LOG_EVERY_S:
                self._admit_error_logged_at = now
                logger.warning("sandbox live limit: the per-run memory check failed; admitting", exc_info=True)
            return None

    def _memory_now(self, now: float) -> tuple[int | None, int | None]:
        mode, profile = self._mode, self._profile
        if mode.reader is None or profile is None:
            return None, None
        with self._lock:
            cached = self._admit_reading
        if cached is not None and 0 <= now - cached[0] < profile.admit_cache_s:
            return cached[1], cached[2]
        available, total = mode.reader.memory()
        if available is not None and total is not None:
            # A missing reading is asked again by the next run rather than
            # kept: the reader may have it a moment later.
            with self._lock:
                self._admit_reading = (now, available, total)
        return available, total

    def _admit_locked(self) -> HoldReason | None:
        profile = self._profile
        if profile is None:
            return None
        now = self._clock()
        available, total = self._memory_now(now)
        if available is None or total is None:
            # The floor cannot be read: admit, or chat would stop whenever the
            # reading misbehaves.
            return None
        # Over-commit only raises the ceiling: each run is checked and charged
        # at its full memory limit, since a run just started may use all of it.
        m = self._run_bytes(1.0)
        hard_floor, _ = profile.memory_lines(total, m)
        with self._ledger.transaction(ADMIT_LOCK_BUDGET) as txn:
            if txn is None:
                # Only a stopped lock holder keeps the lock this long; the next
                # sample still catches a real shortage.
                return None
            # The clock again: the reading and the lock may have taken long
            # enough that a charge another worker made meanwhile would look
            # like one from before a clock restart, and be dropped.
            now = self._clock()
            rec = txn.read()
            if rec is None:
                rec = self._replacement(txn, now)
            # A burst cannot admit more runs than there are slots: charges
            # summing past that are damaged and must not refuse every run.
            most = int(m) * max(1, self._ceiling(self._k(rec, now)))
            charges = live_charges(rec, now, profile.burst_window_s, max_bytes=most)
            if available - min(most, sum(size for _, size in charges)) - m < hard_floor:
                return HoldReason.HOST_MEMORY_FLOOR
            txn.write(replace(rec, charges=_add_charge(charges, now + profile.burst_window_s, int(m))))
        return None

    def _replacement(self, txn: LiveTxn, now: float) -> LiveRecord:
        """What an admission writes its charge into when the record is not
        valid. Missing, or left by an older version: an uninitialised one.
        Damaged: never one that lifts the limit, so this worker's last good
        record, or failing that one at the floor stamped long ago, which the
        next sample starts afresh."""
        if not txn.corrupt:
            return LiveRecord()
        if not self._corrupt_logged:
            self._corrupt_logged = True
            logger.warning("sandbox live limit: the shared record is damaged; holding the limit until the next sample")
        good = txn.last_good
        if good is not None and good.initialised:
            return good
        ceiling = self._ceiling(self._k(None, now))
        floor = min(self._capacity.floor(), ceiling)
        return LiveRecord(
            limit=floor, ceiling=ceiling, min=floor, rule="unknown",
            shrink_cause="memory" if floor < ceiling else None, owner_pid=self._pid,
        )

    def _check_floor(self, total: int, hard_floor: int) -> None:
        """One ERROR when the hard floor leaves no room for a single run."""
        run = self._run_bytes(1.0)
        if self._floor_logged or hard_floor + run < total:
            return
        self._floor_logged = True
        floor_mb = self._settings.host_mem_floor_mb
        source = (
            f"SANDBOX_HOST_MEM_FLOOR_MB={floor_mb}" if floor_mb is not None and self._profile is not None
            and self._profile.floor_bytes is not None else "the hard free-memory floor"
        )
        logger.error(
            "sandbox live limit: %s (%s) plus one run (%s) is not below the %s memory size (%s); "
            "no code run can start. Lower it below the memory size minus one run.",
            source, _gib(hard_floor), _gib(run), self._mode.mode, _gib(total),
        )

    def waiting(self, reason: HoldReason, waited_s: float) -> None:
        """Stamp the record so the sampler knows runs wait: a wait at the
        limit lets it grow; a wait for the per-run floor cannot be helped by
        growing, so it is stamped apart. One non-blocking lock try, at most
        once a second per stamp."""
        if not self._mode.on or reason is HoldReason.ORG_SHARE:
            return
        field = "last_wait_mem_at" if reason is HoldReason.HOST_MEMORY_FLOOR else "last_wait_limit_at"
        now = self._clock()
        if now - self._stamped.get(field, -math.inf) >= STAMP_EVERY_S:
            with self._ledger.transaction(0.0) as txn:
                rec = txn.read() if txn is not None else None
                if txn is not None and rec is not None and rec.initialised:
                    # A failed write only loses this stamp; it must not fail the waiting run.
                    with contextlib.suppress(OSError):
                        txn.write(replace(rec, wait_events=rec.wait_events + 1, **{field: now}))
                        self._stamped[field] = now
        callback = self.on_memory_wait
        if callback is not None and self.waits_for_memory(reason):
            try:
                callback(reason, waited_s)
            except Exception:
                logger.debug("sandbox live limit: on_memory_wait failed", exc_info=True)

    def waits_for_memory(self, reason: HoldReason) -> bool:
        """Whether a run waiting for *reason* waits for memory that indexing
        shares: the per-run floor failed, or the limit is down for memory and
        memory still binds (its target is not above the limit)."""
        profile = self._profile
        if not self._mode.on or profile is None or not profile.yields_to_indexing:
            return False
        if reason is HoldReason.HOST_MEMORY_FLOOR:
            return True
        if reason is not HoldReason.LIVE_MEMORY:
            return False
        view = self.view()
        return view.target is not None and view.target <= view.limit

    def hard_floor_bytes(self) -> int | None:
        """The per-run free-memory floor at the last reading, if any."""
        view, profile = self.view(), self._profile
        if profile is None or view.mem_total_bytes is None:
            return None
        return profile.memory_lines(view.mem_total_bytes, self._run_bytes(1.0))[0]

    def wait_ended(self, *, granted: bool) -> None:
        return None

    def live_block(self, view: LiveView) -> dict[str, Any]:
        """The governor snapshot's ``live`` entry: the view, plus the
        sidecar's last reading in this worker on a Docker-in-Docker sidecar."""
        block: dict[str, Any] = asdict(view)
        reader = self._mode.reader
        if isinstance(reader, SidecarReader):
            last = reader.last_block or {}
            limit, used = last.get("memLimitBytes"), last.get("memWorkingSetBytes")
            block["sidecar"] = {
                "memUsedPct": round(100 * used / limit, 1) if limit and used is not None else None,
                "memLimitBytes": limit, "cpuUtil": last.get("cpuUtil"),
                "cpuThrottledRatio": last.get("cpuThrottledRatio"),
                "dockerdRssBytes": last.get("dockerdRssBytes"), "ageS": last.get("ageS"),
            }
        return block


_live: LiveLimit | None = None


def get_live_limit() -> LiveLimit | None:
    """The live limit ``get_default_governor()`` built, if it built one."""
    return _live


def set_live_limit(live: LiveLimit | None) -> None:
    global _live
    _live = live
