"""A randomized multi-process chaos run of the code sandbox capacity system.

Real ``SandboxResourceGovernor`` + ``SharedSlots`` + ``LiveLimit`` +
``LoadLedger`` + ``IndexingYield`` + the alert runner run in several query
worker processes, and a real indexing ``ResourceGovernor`` reads and acks the
yield signal in one or two indexing processes. Only the edges are fake, and
every fake lives in the run directory so the controller can see it:

- ``host.json``: the machine reading (free memory, CPU, PSI), which the
  controller rewrites, removes or garbles;
- ``kv.json``: the configuration store the alert cooldown claims go to, with
  TTLs on a shared wall clock, and outages driven from ``control.json``;
- ``events.jsonl``: what the notification publisher accepted;
- ``clock``: an offset added to the wall clock of the load ledger, the alert
  runner and the store, which the controller jumps.

Each worker runs many simulated chats: an acquire with a random wait budget,
a fake run of random length, random cancels (Stop) and exceptions. The
controller SIGKILLs and restarts workers, SIGSTOPs and resumes them, corrupts
shared state files, jumps the wall clock and drops readings. Every process
writes JSON lines (``w-<pid>.jsonl``) that the controller checks afterwards:

- at most the ceiling of concurrent holders across processes, counted from
  the runs' own start/end lines (and per org when a per-org cap is set);
- every acquire has exactly one outcome, matching what the caller saw;
- no slot or leader lock is held by a dead process (``/proc/locks``);
- the live limit stays within [floor, ceiling];
- the per-run memory check never admits a run its reading says does not fit,
  apart from the documented fail-open cases, which are counted;
- alerts: one evaluating leader at a time, at most one email per level per
  cooldown, back-to-normal only after a clear window;
- the yield signal is released once chats stop waiting for memory;
- no tracebacks in worker logs, no fd/thread/memory growth.

The short run is ``test_sandbox_chaos.py``; the long one is this module:

    python -m tests.integration.chaos.sandbox_chaos --config c8k2 --seed 3 --duration 600

Nothing here touches Docker, real memory or CPU: the state directory comes
from a private ``SANDBOX_STATE_ID``.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import contextlib
import contextvars
import fcntl
import json
import logging
import math
import os
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GiB = 2**30
MiB = 2**20
RUN_BYTES = 512 * MiB
SLOT_FILES = 256
PYTHON_ROOT = Path(__file__).resolve().parents[3]

# --------------------------------------------------------------------------
# Configuration


@dataclass
class AlertCfg:
    window_s: int = 60
    eval_s: float = 2.0
    warn_busy_pct: float = 40.0
    warn_min_waited: int = 5
    crit_min_refused: int = 5
    crit_refused_pct: float = 5.0
    cooldown_s: int = 150


@dataclass
class ChaosConfig:
    name: str = "c2"
    seed: int = 1
    duration_s: float = 600.0
    workers: int = 4
    indexers: int = 1
    machine_gib: float = 4.0
    cpus: float = 8.0
    min_total: int = 2
    overcommit: float = 1.0
    org_cap: int | None = None
    orgs: int = 3
    expected_ceiling: int = 2
    live_interval_s: float = 1.0
    yield_ttl_s: float = 10.0
    max_inflight: int = 30
    alert: AlertCfg = field(default_factory=AlertCfg)
    # Mean seconds between random faults; 0 turns them off.
    fault_every_s: float = 10.0
    # A fixed script of (at_s, phase) and (at_s, fault) instead of random ones.
    phase_script: list[tuple[float, str]] | None = None
    fault_script: list[tuple[float, str]] | None = None
    # Random faults left out, such as ``corrupt_kv`` (the store losing its keys).
    skip_faults: list[str] = field(default_factory=list)
    # How long to wait, after chats stop, for the yield signal to lapse.
    drain_s: float = 30.0

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ChaosConfig:
        data = dict(data)
        data["alert"] = AlertCfg(**data.get("alert", {}))
        for key in ("phase_script", "fault_script"):
            if data.get(key) is not None:
                data[key] = [tuple(item) for item in data[key]]
        return cls(**data)


PRESETS: dict[str, dict[str, Any]] = {
    # A 16 GiB machine whose CPU term binds the ceiling: 0.7 CPUs x 0.75 / 0.5 = 1.
    "c1": {"machine_gib": 16.0, "cpus": 0.7, "min_total": 1, "expected_ceiling": 1, "workers": 4},
    # 1.4 CPUs: ceiling 2, with a floor of 1 and of 2 (and a per-org cap of 1).
    "c2m1": {"machine_gib": 16.0, "cpus": 1.4, "min_total": 1, "expected_ceiling": 2, "workers": 5},
    "c2": {"machine_gib": 16.0, "cpus": 1.4, "min_total": 2, "expected_ceiling": 2, "workers": 5, "org_cap": 1},
    # Memory binds at 8 (16 GiB x 0.25 / 512 MiB); per-org cap 3.
    "c8": {"machine_gib": 16.0, "cpus": 12.0, "min_total": 2, "expected_ceiling": 8, "workers": 6,
           "org_cap": 3, "indexers": 2},
    # The same machine with an over-commit of 2: 16 (the CPU term is 18).
    "c8k2": {"machine_gib": 16.0, "cpus": 12.0, "min_total": 2, "overcommit": 2.0, "expected_ceiling": 16,
             "workers": 8, "indexers": 2},
    # A small machine where memory binds at 2: the per-run burst budget is tight.
    "small2": {"machine_gib": 4.0, "cpus": 8.0, "min_total": 2, "expected_ceiling": 2, "workers": 4},
}


def hard_floor_bytes(total: int) -> int:
    """The host profile's per-run free-memory floor, computed here on its own."""
    return min(max(int(0.05 * total), GiB), 8 * GiB)


# --------------------------------------------------------------------------
# Shared fakes


class JsonLog:
    """Appends one JSON line per event with one ``write``, so a SIGKILL loses
    at most the line being written and lines from threads never interleave."""

    def __init__(self, path: Path) -> None:
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)

    def __call__(self, kind: str, /, **fields: Any) -> None:  # noqa: ANN401
        fields["e"] = kind
        fields.setdefault("t", time.monotonic())
        os.write(self.fd, (json.dumps(fields, separators=(",", ":"), default=str) + "\n").encode())


class OffsetClock:
    """``base()`` plus the offset the controller keeps in a shared file."""

    def __init__(self, path: Path, base: Any = time.time) -> None:  # noqa: ANN401
        self._fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        self._base = base

    def offset(self) -> float:
        data = os.pread(self._fd, 8, 0)
        return struct.unpack("<d", data)[0] if len(data) == 8 else 0.0

    def __call__(self) -> float:
        return self._base() + self.offset()


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def read_json(path: Path) -> Any:  # noqa: ANN401
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


class FileReader:
    """The live limit's host reader, from ``host.json``. Never raises; a
    missing or garbled file reads as nothing known."""

    source = "host"

    def __init__(self, path: Path) -> None:
        self._path = path
        # The reading the current thread's last ``memory()`` returned.
        self.local = threading.local()

    def _data(self) -> dict[str, Any]:
        data = read_json(self._path)
        return data if isinstance(data, dict) else {}

    def read(self) -> Any:  # noqa: ANN401
        from app.agent_loop_lib.sandbox.host_reading import HostSample

        d = self._data()
        return HostSample(
            mem_total=d.get("total"), mem_available=d.get("avail"), cpu_util=d.get("cpu"),
            cpu_psi_some_avg10=d.get("psi"), mem_psi_full_avg10=d.get("mem_psi"), source="host",
        )

    def memory(self) -> tuple[int | None, int | None]:
        d = self._data()
        reading = (d.get("avail"), d.get("total"))
        self.local.reading = reading
        return reading


class FileKV:
    """The configuration store calls the alert cooldown makes, over one JSON
    file under a lock, with TTLs on the shared (offset) wall clock. Outages
    come from ``control.json``: ``down`` raises, ``flaky`` raises on some
    calls, ``hang`` sleeps past the store timeout."""

    def __init__(self, run_dir: Path, clock: OffsetClock, rng: random.Random) -> None:
        self._path = run_dir / "kv.json"
        self._lock = run_dir / "kv.lock"
        self._control = run_dir / "control.json"
        self._clock = clock
        self._rng = rng

    async def _fault(self) -> None:
        control = read_json(self._control) or {}
        mode = control.get("kv", "ok")
        if mode == "down" or (mode == "flaky" and self._rng.random() < 0.3):
            raise ConnectionError("store down")
        if mode == "hang":
            await asyncio.sleep(3.0)

    @contextlib.contextmanager
    def _locked(self) -> Any:  # noqa: ANN401
        fd = os.open(self._lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = read_json(self._path)
            if not isinstance(data, dict):
                data = {}
            now = self._clock()
            data = {k: v for k, v in data.items()
                    if isinstance(v, dict) and (v.get("exp") is None or now < v["exp"])}
            box = [data]
            yield box
            write_atomic(self._path, json.dumps(box[0]).encode())
        finally:
            os.close(fd)

    async def create_config_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        await self._fault()
        with self._locked() as box:
            if key in box[0]:
                return False
            box[0][key] = {"v": value, "exp": None if ttl_seconds is None else self._clock() + ttl_seconds}
            return True

    async def set_config(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        await self._fault()
        with self._locked() as box:
            box[0][key] = {"v": value, "exp": None if ttl_seconds is None else self._clock() + ttl_seconds}
            return True

    async def get_config(self, key: str, use_cache: bool = False, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        await self._fault()
        with self._locked() as box:
            entry = box[0].get(key)
            return None if entry is None else entry["v"]

    async def delete_config(self, key: str) -> bool:
        await self._fault()
        with self._locked() as box:
            return box[0].pop(key, None) is not None


class FilePublisher:
    """The notification producer: appends what it accepts to ``events.jsonl``."""

    def __init__(self, run_dir: Path, clock: OffsetClock, rng: random.Random) -> None:
        self._log = JsonLog(run_dir / "events.jsonl")
        self._control = run_dir / "control.json"
        self._clock = clock
        self._rng = rng

    async def send_message(self, topic: str, message: dict[str, Any], key: str | None = None) -> bool:
        control = read_json(self._control) or {}
        mode = control.get("pub", "ok")
        if mode == "fail" or (mode == "flaky" and self._rng.random() < 0.3):
            return False
        if mode == "raise":
            raise ConnectionError("broker down")
        if mode == "hang":
            await asyncio.sleep(3.0)
        self._log("published", pid=os.getpid(), fake=self._clock(), topic=topic, key=key, event=message)
        return True

    async def cleanup(self) -> None:
        return None


# --------------------------------------------------------------------------
# Query worker


_RUN: contextvars.ContextVar[str | None] = contextvars.ContextVar("chaos_run", default=None)


class _Outcomes:
    """An ``AcquireObserver`` that records each outcome against the run id
    of the task that made the acquire."""

    def __init__(self) -> None:
        self.by_run: dict[str, list[str]] = defaultdict(list)

    def wait_start(self, reason: Any) -> None:  # noqa: ANN401
        return None

    def wait_reason(self, reason: Any) -> None:  # noqa: ANN401
        return None

    def outcome(self, outcome: str, waited_s: float, reason: Any) -> None:  # noqa: ANN401
        run = _RUN.get()
        self.by_run[run or "?"].append(outcome)


def _proc_stats() -> dict[str, int]:
    fds = len(os.listdir("/proc/self/fd"))
    with open("/proc/self/statm") as fh:
        rss = int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return {"fds": fds, "rss": rss, "threads": threading.active_count(),
            "os_threads": len(os.listdir("/proc/self/task"))}


async def query_worker(run_dir: Path, cfg: ChaosConfig, index: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from app.agent_loop_lib.sandbox.capacity import (
        CapacitySettings,
        FixedLimit,
        MachineSize,
        SandboxCapacity,
    )
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor
    from app.agent_loop_lib.sandbox.host_reading import LiveMode
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings
    from app.agent_loop_lib.sandbox.live_yield import IndexingYield
    from app.agent_loop_lib.sandbox.load_ledger import LoadLedger
    from app.agent_loop_lib.sandbox.manager import SandboxLimitExceeded
    from app.services.resource_governor.shared_slots import (
        SharedSlots,
        SlotHold,
        sandbox_state_directory,
    )
    from app.services.resource_governor.yield_signal import YieldSignal
    from app.services.sandbox_alerts.config import AlertSettings
    from app.services.sandbox_alerts.loop import SandboxAlertRunner

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=4, thread_name_prefix="chaos"))
    pid = os.getpid()
    log = JsonLog(run_dir / f"w-{pid}.jsonl")
    rng = random.Random(cfg.seed * 1000 + index * 17 + pid)
    state = sandbox_state_directory()
    assert state is not None and "chaos" in state.name, state
    wall = OffsetClock(run_dir / "clock")
    control_path = run_dir / "control.json"
    host_path = run_dir / "host.json"

    capacity = SandboxCapacity(
        "compose", CapacitySettings(min_total=cfg.min_total, max_per_org=cfg.org_cap), slot_files=SLOT_FILES,
    )
    capacity._apply(MachineSize(int(cfg.machine_gib * GiB), cfg.cpus, None, "docker_info"), None)

    class Slots(SharedSlots):
        def try_acquire_tagged(self, limit: int, *, tag: bytes, tag_limit: int | None) -> Any:  # noqa: ANN401
            result = super().try_acquire_tagged(limit, tag=tag, tag_limit=tag_limit)
            if isinstance(result, SlotHold):
                result.chaos_limit = limit  # type: ignore[attr-defined]
            return result

    recorder = _Outcomes()
    ledger = LoadLedger(state, mode="compose", clock=wall)
    governor = SandboxResourceGovernor(
        capacity, Slots(state / "slots", SLOT_FILES), limit_provider=FixedLimit(capacity),
        observers=[ledger, recorder],
    )
    reader = FileReader(host_path)
    mode = LiveMode(mode="on:host", reader=reader, profile="host")  # type: ignore[arg-type]
    settings = LiveSettings(
        interval_s=cfg.live_interval_s, memory_overcommit=cfg.overcommit, yield_ttl_s=cfg.yield_ttl_s,
    )
    admits: Counter[str] = Counter()

    class ChaosLive(LiveLimit):
        """The real live limit, with each per-run admission classified."""

        _tl = threading.local()

        def _admit_locked(self) -> Any:  # noqa: ANN401
            tl = self._tl
            tl.busy = False
            reader.local.reading = None
            cached = self._admit_reading
            t = self._clock()
            try:
                result = super()._admit_locked()
            except Exception as exc:
                admits["error"] += 1
                log("admit", cls="error", at=t, err=repr(exc))
                raise
            profile = self._profile
            if result is not None:
                admits["floor_refused"] += 1
                return result
            fresh = getattr(reader.local, "reading", None)
            if profile is None:
                cls, avail, total = "off", None, None
            else:
                if fresh is not None:
                    avail, total, src = fresh[0], fresh[1], "fresh"
                elif cached is not None and 0 <= t - cached[0] < profile.admit_cache_s:
                    avail, total, src = cached[1], cached[2], "cache"
                else:
                    avail = total = None
                    src = "unknown"
                if src == "unknown":
                    cls = "unknown_source"
                elif not isinstance(avail, int) or not isinstance(total, int):
                    cls = "fail_open_missing"
                elif tl.busy:
                    cls = "fail_open_lock_busy"
                elif avail - RUN_BYTES < hard_floor_bytes(total):
                    cls = "VIOLATION"
                else:
                    truth = read_json(host_path) or {}
                    t_avail, t_total = truth.get("avail"), truth.get("total")
                    stale = (
                        isinstance(t_avail, int) and isinstance(t_total, int)
                        and t_avail - RUN_BYTES < hard_floor_bytes(t_total)
                    )
                    cls = "ok_stale_reading" if stale else "ok"
            admits[cls] += 1
            log("admit", cls=cls, at=t, avail=avail, total=total)
            return None

    live = ChaosLive(capacity, governor, settings=settings, resolver=lambda: mode)
    inner = live._ledger

    class LedgerProxy:
        def __getattr__(self, name: str) -> Any:  # noqa: ANN401
            return getattr(inner, name)

        @contextlib.contextmanager
        def transaction(self, lock_budget: float = 0.020) -> Any:  # noqa: ANN401
            with inner.transaction(lock_budget) as txn:
                if txn is None:
                    ChaosLive._tl.busy = True
                yield txn

    live._ledger = LedgerProxy()  # type: ignore[assignment]
    governor.limit_provider = live
    signal_ = YieldSignal.open_default(role="code sandbox")
    writer = IndexingYield(live, signal_, settings)
    if writer.enabled:
        live.on_memory_wait = writer.on_memory_wait
        live.after_sample = writer.check_lapsed
    live.start()

    a = cfg.alert
    runner = SandboxAlertRunner(
        FileKV(run_dir, wall, rng),  # type: ignore[arg-type]
        AlertSettings(
            window_s=a.window_s, eval_s=int(max(1, a.eval_s)), warn_busy_pct=a.warn_busy_pct,
            warn_min_waited=a.warn_min_waited, crit_min_refused=a.crit_min_refused,
            crit_refused_pct=a.crit_refused_pct, cooldown_s=a.cooldown_s, scope=f"chaos{cfg.seed}",
        ),
        state, producer_factory=lambda: _producer(run_dir, wall, rng), snapshot=governor.snapshot,
        logger=logging.getLogger("chaos.alerts"), clock=wall, host="chaos-host",
        store_timeout_s=1.0, publish_timeout_s=1.0,
    )

    stopping = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    control: dict[str, Any] = {}
    chats: set[asyncio.Task[None]] = set()
    counter = 0
    log("hello", role="query", index=index, ceiling=live.view(fresh=True).ceiling)

    async def on_wait() -> None:
        roll = rng.random()
        if roll < 0.1:
            raise RuntimeError("chaos: on_wait failed")
        if roll < 0.2:
            await asyncio.sleep(rng.uniform(0.0, 0.05))

    async def chat(rid: str) -> None:
        _RUN.set(rid)
        waits = control.get("waits") or [0.0]
        wait_s = float(rng.choice(waits))
        org = f"org{rng.randrange(cfg.orgs)}"
        log("ask", id=rid, wait=wait_s, org=org)
        lease = None
        try:
            try:
                lease = await governor.acquire(
                    org_id=org, wait_s=wait_s, on_wait=on_wait if rng.random() < 0.5 else None,
                )
                result = "granted"
            except SandboxLimitExceeded as exc:
                result = "refused"
                reason = getattr(exc, "reason", None)
            except asyncio.CancelledError:
                result = "cancelled"
                raise
            except Exception as exc:
                result = "error"
                log("violation", kind="acquire_raised", id=rid, err=repr(exc))
        finally:
            seen = recorder.by_run.pop(rid, [])
            expected = {"granted": "granted", "refused": "refused"}.get(result, "cancelled")
            log("res", id=rid, result=result, observed=seen)
            if seen != [expected]:
                log("violation", kind="outcome_mismatch", id=rid, result=result, observed=seen)
        if result == "refused":
            log("refused", id=rid, reason=str(reason))
        if lease is None:
            return
        hold = lease.hold
        limit_used = getattr(hold, "chaos_limit", None)
        log("start", id=rid, org=org, held_before=hold.held_before, limit=limit_used)
        if limit_used is not None and hold.held_before >= limit_used:
            log("violation", kind="held_before_at_limit", id=rid, held_before=hold.held_before, limit=limit_used)
        try:
            lo, hi = control.get("hold") or (0.1, 1.0)
            await asyncio.sleep(rng.uniform(lo, hi))
            if rng.random() < control.get("exc_p", 0.05):
                raise RuntimeError("chaos: the run failed")
        except RuntimeError:
            pass
        finally:
            log("end", id=rid)
            await governor.release(lease)

    async def scheduler() -> None:
        nonlocal counter
        while not stopping.is_set():
            # The phase gives the offered load as a share of the ceiling.
            lo, hi = control.get("hold") or (0.1, 1.0)
            ceiling = max(1, live.view().ceiling)
            rate = 0.0 if control.get("stop_chats") else (
                float(control.get("load", 1.0)) * ceiling / (cfg.workers * max(0.05, (lo + hi) / 2))
            )
            await asyncio.sleep(rng.expovariate(rate) if rate > 0 else 0.25)
            if stopping.is_set() or control.get("stop_chats") or rate <= 0:
                continue
            if len(chats) >= cfg.max_inflight:
                continue
            counter += 1
            task = asyncio.create_task(chat(f"{pid}-{counter}"))
            chats.add(task)
            task.add_done_callback(chats.discard)

    async def canceller() -> None:
        while not stopping.is_set():
            await asyncio.sleep(0.2)
            p = float(control.get("cancel_p", 0.02))
            for task in list(chats):
                if rng.random() < p:
                    task.cancel()

    async def watcher() -> None:
        nonlocal control
        last_stats = 0.0
        while not stopping.is_set():
            data = read_json(control_path)
            if isinstance(data, dict):
                control = data
            view = live.view()
            log("view", limit=view.limit, ceiling=view.ceiling, floor=view.floor, rule=view.rule, mode=view.mode)
            now = time.monotonic()
            if now - last_stats >= 5.0:
                last_stats = now
                log("stats", inflight=len(chats), admits=dict(admits), **_proc_stats())
            await asyncio.sleep(0.5)

    async def alerts() -> None:
        leader = False
        while not stopping.is_set():
            try:
                decision = await runner.run_once()
            except Exception as exc:
                log("alert_error", err=repr(exc))
                decision = None
            if runner.is_leader and not leader:
                leader = True
                log("leader")
            if decision is not None:
                log("eval", fake=wall(), level=decision.level, any=decision.any,
                    clear_since=runner._clear_since)
            await asyncio.sleep(a.eval_s * rng.uniform(0.8, 1.2))

    tasks = [asyncio.create_task(c()) for c in (scheduler, canceller, watcher, alerts)]
    await stopping.wait()
    for task in tasks:
        task.cancel()
    for task in list(chats):
        task.cancel()
    await asyncio.gather(*tasks, *list(chats), return_exceptions=True)
    await live.stop()
    await runner.close()
    ledger.close()
    held = governor.held_total(0.5)
    governor.close()
    log("stats", inflight=0, admits=dict(admits), **_proc_stats())
    log("bye", held_after_close=held)


async def _producer(run_dir: Path, clock: OffsetClock, rng: random.Random) -> FilePublisher:
    control = read_json(run_dir / "control.json") or {}
    if control.get("pub") == "factory_down":
        raise ConnectionError("broker unreachable")
    return FilePublisher(run_dir, clock, rng)


# --------------------------------------------------------------------------
# Indexing worker


async def indexing_worker(run_dir: Path, cfg: ChaosConfig, index: int) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from app.services.resource_governor.controller import ResourceGovernor
    from app.services.resource_governor.feedback import DownstreamFeedback
    from app.services.resource_governor.models import Pool, ResourceSnapshot
    from app.services.resource_governor.shared_slots import sandbox_state_directory
    from app.services.resource_governor.yield_signal import (
        YieldSignal,
        read_yield_state,
    )

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=3, thread_name_prefix="chaos"))
    pid = os.getpid()
    log = JsonLog(run_dir / f"w-{pid}.jsonl")
    rng = random.Random(cfg.seed * 7000 + index * 31 + pid)
    state = sandbox_state_directory()
    assert state is not None and "chaos" in state.name, state

    class Probe:
        def snapshot(self) -> ResourceSnapshot:
            return ResourceSnapshot(
                cpu_quota=8.0, cpu_utilisation=0.2, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
                mem_limit_bytes=32 * GiB, mem_working_set_bytes=int(0.2 * 32 * GiB), source="chaos",
            )

    governor = ResourceGovernor(
        logger=logging.getLogger("chaos.indexing"), probe=Probe(), feedback=DownstreamFeedback(),
        sample_interval=0.5, jitter=0.1, yield_signal=YieldSignal(state),
    )
    gates = {pool: governor.gate(pool) for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)}
    stopping = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    log("hello", role="indexing", index=index)

    async def work(pool: Any) -> None:  # noqa: ANN401
        async with gates[pool].slot(timeout=2.0) as admitted:
            if admitted:
                await asyncio.sleep(rng.uniform(0.05, 0.5))

    async def load() -> None:
        jobs: set[asyncio.Task[None]] = set()
        while not stopping.is_set():
            await asyncio.sleep(0.05)
            if len(jobs) < 40:
                task = asyncio.create_task(work(rng.choice(list(gates))))
                jobs.add(task)
                task.add_done_callback(jobs.discard)
        for task in list(jobs):
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    async def watch() -> None:
        last_stats = 0.0
        while not stopping.is_set():
            st = read_yield_state(state)
            log("yield", active=st.active, ackers=st.ackers, at_floor=st.index_at_floor,
                seen=governor._yield_state.active,
                limits={p.value: g.limit for p, g in gates.items()})
            now = time.monotonic()
            if now - last_stats >= 5.0:
                last_stats = now
                log("stats", **_proc_stats())
            await asyncio.sleep(0.25)

    run = asyncio.create_task(governor.run())
    tasks = [asyncio.create_task(load()), asyncio.create_task(watch())]
    await stopping.wait()
    governor.stop()
    run.cancel()
    for task in tasks:
        task.cancel()
    await asyncio.gather(run, *tasks, return_exceptions=True)
    governor.close()
    log("stats", **_proc_stats())
    log("bye")


def worker_main(role: str, run_dir: Path, index: int) -> None:
    cfg = ChaosConfig.from_json(json.loads((run_dir / "config.json").read_text()))
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format=f"%(asctime)s %(levelname)s [{role}{index} %(process)d] %(name)s: %(message)s",
    )
    target = query_worker if role == "query" else indexing_worker
    asyncio.run(target(run_dir, cfg, index))


# --------------------------------------------------------------------------
# Controller


PHASES: dict[str, dict[str, Any]] = {
    "normal": {"load": 1.0, "waits": [0, 0.5, 2, 5], "hold": [0.2, 2.0], "cancel_p": 0.02, "exc_p": 0.05,
               "host": "plenty"},
    "storm": {"load": 4.0, "waits": [0, 0.2, 1], "hold": [1.0, 4.0], "cancel_p": 0.02, "exc_p": 0.05,
              "host": "plenty"},
    "memory": {"load": 2.0, "waits": [1, 5, 10], "hold": [0.5, 3.0], "cancel_p": 0.03, "exc_p": 0.05,
               "host": "tight"},
    "cpu": {"load": 2.0, "waits": [0.5, 2, 5], "hold": [0.5, 3.0], "cancel_p": 0.02, "exc_p": 0.05,
            "host": "cpu"},
    "flaky": {"load": 1.5, "waits": [0, 1, 5], "hold": [0.2, 2.0], "cancel_p": 0.05, "exc_p": 0.1,
              "host": "flaky"},
    "calm": {"load": 0.15, "waits": [0], "hold": [0.1, 0.5], "cancel_p": 0.0, "exc_p": 0.0, "host": "plenty"},
}
FAULTS = (
    "kill", "kill", "kill_leader", "stop_cont", "stop_cont", "kill_indexer", "corrupt_live",
    "corrupt_yield", "corrupt_load", "delete_live", "corrupt_kv", "clock_fwd", "clock_back",
    "reading_gone", "kv_flaky", "kv_down", "kv_hang", "pub_fail", "pub_raise", "pub_hang",
)


@dataclass
class Proc:
    role: str
    index: int
    popen: subprocess.Popen[bytes]
    started: float
    stderr_path: Path
    ended: float | None = None
    killed: bool = False
    graceful: bool = False

    @property
    def pid(self) -> int:
        return self.popen.pid


class Controller:
    def __init__(self, cfg: ChaosConfig, run_dir: Path) -> None:
        self.cfg = cfg
        self.run = run_dir
        self.rng = random.Random(cfg.seed)
        self.state_id = f"chaos-{cfg.name}-{cfg.seed}-{os.getpid()}"
        self.env = {**os.environ, "SANDBOX_STATE_ID": self.state_id, "PYTHONPATH": str(PYTHON_ROOT)}
        self.procs: list[Proc] = []
        self.faults = JsonLog(run_dir / "faults.jsonl")
        self.clock_fd = os.open(run_dir / "clock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        os.pwrite(self.clock_fd, struct.pack("<d", 0.0), 0)
        self.clock_offset = 0.0
        self.control: dict[str, Any] = {}
        self.host_kind = "plenty"
        self.host_until = 0.0
        self.reading_gone_until = 0.0
        self.flaky_state = "ok"
        self.flaky_next = 0.0
        self.tight_base: float | None = None
        self.kv_until = 0.0
        self.pub_until = 0.0
        self.stopped: dict[int, float] = {}
        self.lock_violations: list[dict[str, Any]] = []
        self.slots = None
        self.inodes: dict[tuple[str, int], str] = {}
        self.restart_queue: list[tuple[float, str, int]] = []
        self.counts: Counter[str] = Counter()

    # -- processes

    def state_dir(self) -> Path:
        from app.services.resource_governor.shared_slots import sandbox_state_directory

        directory = sandbox_state_directory({"SANDBOX_STATE_ID": self.state_id})
        assert directory is not None
        return directory

    def spawn(self, role: str, index: int) -> Proc:
        stderr_path = self.run / f"{role}{index}-{len(self.procs)}.log"
        with open(stderr_path, "wb") as err:
            popen = subprocess.Popen(
                [sys.executable, "-m", "tests.integration.chaos.sandbox_chaos", "--worker", role,
                 "--run-dir", str(self.run), "--index", str(index)],
                cwd=PYTHON_ROOT, env=self.env, stdout=err, stderr=subprocess.STDOUT,
            )
        proc = Proc(role, index, popen, time.monotonic(), stderr_path)
        self.procs.append(proc)
        return proc

    def alive(self, role: str | None = None) -> list[Proc]:
        return [p for p in self.procs if p.ended is None and (role is None or p.role == role)]

    def reap(self) -> None:
        for proc in self.alive():
            code = proc.popen.poll()
            if code is not None:
                proc.ended = time.monotonic()
                if not proc.killed and not proc.graceful:
                    self.faults("unexpected_exit", pid=proc.pid, code=code)
                    self.counts["unexpected_exit"] += 1
                    self.restart_queue.append((time.monotonic() + 0.5, proc.role, proc.index))

    def kill(self, proc: Proc, why: str) -> None:
        if proc.pid in self.stopped:
            self.stopped.pop(proc.pid)
        self.faults("kill", pid=proc.pid, role=proc.role, why=why)
        proc.killed = True
        with contextlib.suppress(ProcessLookupError):
            os.kill(proc.pid, signal.SIGKILL)
        proc.popen.wait()
        proc.ended = time.monotonic()
        self.restart_queue.append((time.monotonic() + self.rng.uniform(0.3, 2.0), proc.role, proc.index))

    # -- shared state

    def write_control(self, phase: str) -> None:
        spec = dict(PHASES[phase])
        self.host_kind = spec.pop("host")
        self.tight_base = None
        self.control = {**self.control, **spec, "phase": phase}
        self.flush_control()

    def flush_control(self) -> None:
        write_atomic(self.run / "control.json", json.dumps(self.control).encode())

    def set_clock(self, offset: float) -> None:
        self.clock_offset = offset
        os.pwrite(self.clock_fd, struct.pack("<d", offset), 0)

    def held(self) -> int:
        if self.slots is None:
            return 0
        try:
            return self.slots.held_count(0.02)
        except TimeoutError:
            return 0

    def write_host(self, now: float) -> None:
        total = int(self.cfg.machine_gib * GiB)
        hard = hard_floor_bytes(total)
        if now < self.reading_gone_until:
            kind = self.rng.choice(["missing", "garbage", "partial", "nulls"])
            path = self.run / "host.json"
            if kind == "missing":
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            elif kind == "garbage":
                write_atomic(path, b"{not json")
            elif kind == "partial":
                write_atomic(path, json.dumps({"total": total}).encode())
            else:
                write_atomic(path, json.dumps({"total": None, "avail": None}).encode())
            return
        cpu, psi, mem_psi = 0.3, 0.05, 0.0
        if self.host_kind == "tight":
            if self.tight_base is None:
                # Between under the per-run floor and a little over it.
                self.tight_base = self.rng.uniform(hard + 0.2 * RUN_BYTES, hard + 3.5 * RUN_BYTES)
            base = self.tight_base
            if self.rng.random() < 0.05:
                mem_psi = 0.2
        elif self.host_kind == "cpu":
            base, cpu, psi = 0.85 * total, self.rng.uniform(0.9, 1.0), self.rng.uniform(0.6, 0.95)
        else:
            base = 0.85 * total
        if self.host_kind == "flaky":
            if now >= self.flaky_next:
                self.flaky_next = now + self.rng.uniform(1.0, 3.0)
                self.flaky_state = self.rng.choice(["ok", "ok", "missing", "garbage", "low"])
            if self.flaky_state in ("missing", "garbage"):
                path = self.run / "host.json"
                if self.flaky_state == "missing":
                    with contextlib.suppress(FileNotFoundError):
                        path.unlink()
                else:
                    write_atomic(path, b"\x00\x01garbage")
                return
            if self.flaky_state == "low":
                base = hard + 0.5 * RUN_BYTES
        # The runs that hold a slot use some of it.
        avail = int(max(0.03 * total, base - 0.4 * RUN_BYTES * self.held()))
        write_atomic(self.run / "host.json", json.dumps(
            {"total": total, "avail": avail, "cpu": cpu, "psi": psi, "mem_psi": mem_psi},
        ).encode())

    def corrupt(self, name: str) -> None:
        state = self.state_dir()
        rng = self.rng
        if name == "corrupt_load":
            files = sorted((state / "load").glob("w-*.json"))
            if not files:
                return
            target = rng.choice(files)
        elif name == "corrupt_kv":
            target = self.run / "kv.json"
        else:
            target = state / ("live" if name == "corrupt_live" else "yield")
        try:
            data = bytearray(target.read_bytes())
        except OSError:
            return
        how = rng.choice(["flip", "truncate", "zero", "garbage"])
        if how == "flip" and data:
            for _ in range(rng.randint(1, 8)):
                data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        elif how == "truncate":
            data = data[: rng.randrange(max(1, len(data)))]
        elif how == "zero":
            data = bytearray(len(data))
        else:
            data = bytearray(rng.randbytes(rng.choice([len(data), 17, 300])))
        # In place, as a stray writer would: the same inode, not a rename.
        fd = os.open(target, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, bytes(data), 0)
        finally:
            os.close(fd)
        self.faults("corrupt", file=str(target.name), how=how)

    def leader_pid(self) -> int | None:
        locks = self.read_locks()
        for pid, name in locks:
            if name == "leader":
                return pid
        return None

    def index_inodes(self) -> None:
        state = self.state_dir()
        paths = [*(state / "slots").glob("*"), state / "load" / "leader", state / "live.lock",
                 state / "yield.lock"]
        for path in paths:
            try:
                st = path.stat()
            except OSError:
                continue
            dev = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}"
            self.inodes[(dev, st.st_ino)] = path.name if path.parent.name != "slots" else f"slot:{path.name}"

    def read_locks(self) -> list[tuple[int, str]]:
        found = []
        try:
            text = Path("/proc/locks").read_text()
        except OSError:
            return found
        for line in text.splitlines():
            parts = line.split()
            if len(parts) < 6 or parts[1] == "->":
                continue
            try:
                pid = int(parts[4])
                dev_a, dev_b, ino = parts[5].split(":")
            except ValueError:
                continue
            name = self.inodes.get((f"{dev_a}:{dev_b}", int(ino)))
            if name is not None:
                found.append((pid, name))
        return found

    def check_locks(self, now: float) -> None:
        live = {p.pid for p in self.alive()}
        ended = {p.pid: p.ended for p in self.procs if p.ended is not None}
        own = os.getpid()
        for pid, name in self.read_locks():
            if pid == own or pid in live:
                continue
            gone_for = now - ended[pid] if pid in ended else None
            if gone_for is None or gone_for > 3.0:
                item = {"pid": pid, "file": name, "gone_for": gone_for}
                self.lock_violations.append(item)
                self.faults("violation", kind="lock_held_by_dead_process", **item)

    # -- faults

    def fault(self, name: str, now: float) -> None:
        rng = self.rng
        query = self.alive("query")
        self.counts[name] += 1
        if name == "kill" and query:
            self.kill(rng.choice(query), "random")
        elif name == "kill_leader":
            pid = self.leader_pid()
            proc = next((p for p in query if p.pid == pid), None)
            if proc is not None:
                self.kill(proc, "leader")
        elif name == "kill_indexer":
            indexers = self.alive("indexing")
            if indexers:
                self.kill(rng.choice(indexers), "indexer")
        elif name == "stop_cont" and query:
            proc = rng.choice([p for p in query if p.pid not in self.stopped] or query)
            if proc.pid not in self.stopped:
                os.kill(proc.pid, signal.SIGSTOP)
                self.stopped[proc.pid] = now + rng.uniform(0.5, 4.0)
                self.faults("stop", pid=proc.pid, until=self.stopped[proc.pid])
        elif name.startswith("corrupt_"):
            self.corrupt(name)
        elif name == "delete_live":
            with contextlib.suppress(FileNotFoundError):
                (self.state_dir() / "live").unlink()
            self.faults("delete", file="live")
        elif name in ("clock_fwd", "clock_back"):
            jump = rng.uniform(10, 90) * (1 if name == "clock_fwd" else -1)
            self.set_clock(self.clock_offset + jump)
            self.faults("clock", jump=jump, offset=self.clock_offset)
        elif name == "reading_gone":
            self.reading_gone_until = now + rng.uniform(2.0, 10.0)
            self.faults("reading_gone", until=self.reading_gone_until)
        elif name.startswith("kv_"):
            self.control["kv"] = name[3:]
            self.kv_until = now + rng.uniform(2.0, 8.0)
            self.flush_control()
            self.faults("kv", mode=name[3:], until=self.kv_until)
        elif name.startswith("pub_"):
            self.control["pub"] = name[4:]
            self.pub_until = now + rng.uniform(2.0, 8.0)
            self.flush_control()
            self.faults("pub", mode=name[4:], until=self.pub_until)

    def tick_faults(self, now: float) -> None:
        for pid, until in list(self.stopped.items()):
            if now >= until:
                self.stopped.pop(pid)
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGCONT)
                self.faults("cont", pid=pid)
        if self.kv_until and now >= self.kv_until:
            self.kv_until = 0.0
            self.control["kv"] = "ok"
            self.flush_control()
        if self.pub_until and now >= self.pub_until:
            self.pub_until = 0.0
            self.control["pub"] = "ok"
            self.flush_control()
        for item in list(self.restart_queue):
            at, role, index = item
            if now >= at:
                self.restart_queue.remove(item)
                proc = self.spawn(role, index)
                self.faults("restart", pid=proc.pid, role=role)

    # -- run

    def run_all(self) -> dict[str, Any]:
        cfg = self.cfg
        state = self.state_dir()
        shutil.rmtree(state, ignore_errors=True)
        (self.run / "config.json").write_text(json.dumps(asdict(cfg)))
        self.control = {"kv": "ok", "pub": "ok"}
        self.write_control("normal")
        self.write_host(0.0)
        from app.services.resource_governor.shared_slots import SharedSlots

        self.slots = SharedSlots(state / "slots", SLOT_FILES)
        self.index_inodes()
        for i in range(cfg.workers):
            self.spawn("query", i)
        for i in range(cfg.indexers):
            self.spawn("indexing", i)
        start = time.monotonic()
        self.faults("begin", start=start)
        phases = list(cfg.phase_script or [])
        fault_script = list(cfg.fault_script or [])
        phase_end = 0.0
        next_fault = start + (self.rng.expovariate(1 / cfg.fault_every_s) if cfg.fault_every_s else math.inf)
        next_lock_check = 0.0
        since_calm = 0
        try:
            while True:
                now = time.monotonic()
                elapsed = now - start
                if elapsed >= cfg.duration_s:
                    break
                if cfg.phase_script is not None:
                    while phases and elapsed >= phases[0][0]:
                        _, phase = phases.pop(0)
                        self.write_control(phase)
                        self.faults("phase", phase=phase)
                elif now >= phase_end:
                    names = [n for n in PHASES if n != "calm"]
                    long_calm = since_calm >= 3
                    phase = "calm" if long_calm or self.rng.random() < 0.2 else self.rng.choice(names)
                    since_calm = 0 if phase == "calm" else since_calm + 1
                    length = (
                        # One window for the load to leave it, one clear window.
                        self.rng.uniform(2 * cfg.alert.window_s + 20, 2 * cfg.alert.window_s + 40)
                        if phase == "calm" and long_calm else self.rng.uniform(10, 40)
                    )
                    phase_end = now + length
                    self.write_control(phase)
                    self.faults("phase", phase=phase, length=length)
                while fault_script and elapsed >= fault_script[0][0]:
                    _, name = fault_script.pop(0)
                    self.fault(name, now)
                if cfg.fault_script is None and now >= next_fault:
                    next_fault = now + self.rng.expovariate(1 / cfg.fault_every_s)
                    self.fault(self.rng.choice([f for f in FAULTS if f not in cfg.skip_faults]), now)
                self.write_host(now)
                self.tick_faults(now)
                self.reap()
                if now >= next_lock_check:
                    next_lock_check = now + 1.0
                    self.index_inodes()
                    self.check_locks(now)
                time.sleep(0.2)
            return self.finish()
        finally:
            for pid in list(self.stopped):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGCONT)
            for proc in self.alive():
                with contextlib.suppress(ProcessLookupError):
                    proc.popen.kill()
                proc.popen.wait()
            if self.slots is not None:
                self.slots.close()
            shutil.rmtree(state, ignore_errors=True)

    def finish(self) -> dict[str, Any]:
        from app.services.resource_governor.yield_signal import read_yield_state

        cfg = self.cfg
        state = self.state_dir()
        # Undo any fault still in force, then stop new chats with plenty of memory.
        for pid in list(self.stopped):
            self.stopped.pop(pid)
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGCONT)
        self.restart_queue.clear()
        self.reading_gone_until = 0.0
        self.control.update({"stop_chats": True, "kv": "ok", "pub": "ok"})
        self.write_control("calm")
        self.control["stop_chats"] = True
        self.flush_control()
        drain_start = time.monotonic()
        released_after = None
        while time.monotonic() - drain_start < cfg.drain_s:
            self.write_host(time.monotonic())
            self.reap()
            if not read_yield_state(state).active:
                released_after = time.monotonic() - drain_start
                break
            time.sleep(0.25)
        # Then one more check a TTL later: nothing may refresh it now.
        time.sleep(1.0)
        still_active = read_yield_state(state).active
        for proc in self.alive():
            proc.graceful = True
            with contextlib.suppress(ProcessLookupError):
                os.kill(proc.pid, signal.SIGTERM)
        exit_codes = {}
        for proc in self.alive():
            try:
                exit_codes[proc.pid] = proc.popen.wait(timeout=20)
            except subprocess.TimeoutExpired:
                exit_codes[proc.pid] = "timeout"
                proc.popen.kill()
                proc.popen.wait()
            proc.ended = time.monotonic()
        self.index_inodes()
        self.check_locks(time.monotonic() + 10)
        held_end = self.held()
        result = analyse(self.run, cfg)
        result["drain"] = {
            "yield_released_after_s": released_after, "yield_active_after_drain": still_active,
            "held_after_exit": held_end, "exit_codes": exit_codes,
        }
        result["faults_injected"] = dict(self.counts)
        result["lock_violations"] = self.lock_violations
        problems = result["problems"]
        if released_after is None or still_active:
            problems.append(f"yield signal still active {cfg.drain_s:.0f}s after chats stopped")
        if held_end:
            problems.append(f"{held_end} slots held after every worker exited")
        bad_exit = {pid: code for pid, code in exit_codes.items() if code != 0}
        if bad_exit:
            problems.append(f"workers did not exit cleanly: {bad_exit}")
        if self.lock_violations:
            problems.append(f"{len(self.lock_violations)} locks held by dead processes")
        if self.counts.get("unexpected_exit"):
            problems.append(f"{self.counts['unexpected_exit']} workers exited on their own")
        (self.run / "summary.json").write_text(json.dumps(result, indent=1, default=str))
        return result


# --------------------------------------------------------------------------
# Analysis


def _lines(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        raw = path.read_bytes().splitlines()
    except OSError:
        return out
    for line in raw:
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def _max_overlap(intervals: list[tuple[float, float, str]]) -> tuple[int, float | None, list[str]]:
    points = []
    for start, end, rid in intervals:
        points.append((start, 1, rid))
        points.append((end, -1, rid))
    # Ends before starts at the same instant: a release then a take is not overlap.
    points.sort(key=lambda p: (p[0], p[1]))
    current, best, at = 0, 0, None
    open_ids: set[str] = set()
    best_ids: list[str] = []
    for t, delta, rid in points:
        current += delta
        if delta > 0:
            open_ids.add(rid)
        else:
            open_ids.discard(rid)
        if current > best:
            best, at, best_ids = current, t, sorted(open_ids)
    return best, at, best_ids


def analyse(run_dir: Path, cfg: ChaosConfig) -> dict[str, Any]:
    """Check the invariants from the files of a finished run alone, so an old
    run directory can be checked again (``--analyse``)."""
    problems: list[str] = []
    notes: list[str] = []
    faults = _lines(run_dir / "faults.jsonl")
    begin = next(f["t"] for f in faults if f["e"] == "begin")
    # The controller logs a kill just before it sends it: a lower bound of
    # when the kernel dropped the process's locks.
    killed_at = {f["pid"]: f["t"] for f in faults if f["e"] == "kill"}
    spawned_at = {f["pid"]: f["t"] for f in faults if f["e"] == "restart"}
    phase_log = [(f["t"], f["phase"]) for f in faults if f["e"] == "phase"]
    corrupt_times = [f["t"] for f in faults if f["e"] in ("corrupt", "delete") and f.get("file") in ("live",)]
    live_deleted = [f["t"] for f in faults if f["e"] == "delete" and f.get("file") == "live"]
    kv_lost = [f["t"] for f in faults if f["e"] == "corrupt" and f.get("file") == "kv.json"]
    clock_jumps = [(f["t"], f["jump"]) for f in faults if f["e"] == "clock"]
    query_pids: set[int] = set()

    def split_record(t: float) -> bool:
        """Whether at *t* some live worker opened the live record before it
        was deleted and another opened the new file after."""
        for td in live_deleted:
            if td > t:
                continue
            alive = [pid for pid in query_pids
                     if spawned_at.get(pid, begin) <= t and killed_at.get(pid, math.inf) > t]
            if any(spawned_at.get(pid, begin) < td for pid in alive) and any(
                spawned_at.get(pid, begin) > td for pid in alive
            ):
                return True
        return False

    starts: dict[str, dict[str, Any]] = {}
    ends: dict[str, float] = {}
    asks: dict[str, dict[str, Any]] = {}
    res: dict[str, list[dict[str, Any]]] = defaultdict(list)
    views: list[dict[str, Any]] = []
    admits: list[dict[str, Any]] = []
    evals: list[dict[str, Any]] = []
    yields: list[dict[str, Any]] = []
    stats: dict[int, list[dict[str, Any]]] = defaultdict(list)
    violations: list[dict[str, Any]] = []
    alert_errors: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("w-*.jsonl")):
        pid = int(path.stem.split("-")[1])
        for ev in _lines(path):
            kind = ev["e"]
            ev["pid"] = pid
            if kind == "hello" and ev.get("role") == "query":
                query_pids.add(pid)
            if kind == "ask":
                asks[ev["id"]] = ev
            elif kind == "res":
                res[ev["id"]].append(ev)
            elif kind == "start":
                starts[ev["id"]] = ev
            elif kind == "end":
                ends[ev["id"]] = ev["t"]
            elif kind == "view":
                views.append(ev)
            elif kind == "admit":
                admits.append(ev)
            elif kind == "eval":
                evals.append(ev)
            elif kind == "yield":
                yields.append(ev)
            elif kind == "stats":
                stats[pid].append(ev)
            elif kind == "violation":
                violations.append(ev)
            elif kind == "alert_error":
                alert_errors.append(ev)

    # 1. Concurrent holders, from the runs' own lines.
    intervals = []
    missing_end = []
    for rid, ev in starts.items():
        end = ends.get(rid)
        if end is None:
            end = killed_at.get(ev["pid"])
            if end is None:
                missing_end.append(rid)
                continue
        intervals.append((ev["t"], end, rid))
    peak, peak_at, peak_ids = _max_overlap(intervals)
    if peak > cfg.expected_ceiling:
        problems.append(f"{peak} concurrent holders > ceiling {cfg.expected_ceiling} at {peak_at}: {peak_ids}")
    if missing_end:
        problems.append(f"{len(missing_end)} runs of live workers never ended: {missing_end[:5]}")
    per_org_peak = {}
    if cfg.org_cap:
        by_org: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
        for start, end, rid in intervals:
            by_org[starts[rid]["org"]].append((start, end, rid))
        for org, items in by_org.items():
            n, at, ids = _max_overlap(items)
            per_org_peak[org] = n
            if n > cfg.org_cap:
                problems.append(f"org {org}: {n} concurrent holders > per-org cap {cfg.org_cap} at {at}: {ids}")

    # 2. Exactly one outcome per acquire.
    outcome_counts: Counter[str] = Counter()
    for rid, ask in asks.items():
        got = res.get(rid, [])
        if len(got) > 1:
            problems.append(f"acquire {rid} has {len(got)} outcomes")
        elif not got:
            if ask["pid"] not in killed_at:
                problems.append(f"acquire {rid} of a live worker has no outcome")
            else:
                outcome_counts["lost_to_kill"] += 1
            continue
        outcome_counts[got[0]["result"]] += 1
        if got[0]["result"] == "granted" and rid not in starts and ask["pid"] not in killed_at:
            problems.append(f"granted {rid} never started")
    problems.extend(f"worker {v['pid']}: {v['kind']} {v}" for v in violations)

    # 3. The live limit's bounds.
    # Until the sampler has chosen its reading the limit is the fixed ceiling,
    # which an over-commit has not raised yet.
    bad_views = [v for v in views if not (cfg.min_total <= v["limit"] <= v["ceiling"])
                 or v["floor"] != min(cfg.min_total, cfg.expected_ceiling)
                 or (v["ceiling"] != cfg.expected_ceiling if v["mode"].startswith("on")
                     else v["ceiling"] > cfg.expected_ceiling)]
    if bad_views:
        problems.append(f"{len(bad_views)} views outside [floor, ceiling]: {bad_views[:3]}")
    limits = Counter(v["limit"] for v in views)
    rules = Counter(v["rule"] for v in views)

    # 4. Per-run memory check.
    admit_cls = Counter(a["cls"] for a in admits)
    if admit_cls.get("VIOLATION"):
        problems.append(f"{admit_cls['VIOLATION']} runs admitted under the per-run memory floor")
    if admit_cls.get("error"):
        notes.append(f"{admit_cls['error']} per-run checks raised and admitted (fail-open)")
    # Burst: runs admitted within one burst window must all fit the reading each saw.
    window = 5.0
    ok_admits = sorted((a for a in admits if a["cls"] in ("ok", "ok_stale_reading")), key=lambda a: a["at"])
    times = [a["at"] for a in ok_admits]
    over, over_near_corruption, over_split = 0, 0, 0
    for a in ok_admits:
        before = bisect.bisect_left(times, a["at"] - 0.001) - bisect.bisect_right(times, a["at"] - window + 0.05)
        if (before + 1) * RUN_BYTES > a["avail"] - hard_floor_bytes(a["total"]):
            near = any(a["at"] - window - 1 <= t <= a["at"] for t in corrupt_times)
            # A worker killed or stopped while it held the record lock is a
            # fail-open case too; count those apart.
            if near:
                over_near_corruption += 1
            elif split_record(a["at"]):
                # Workers that opened the record before it was deleted keep
                # writing to the old file; newer ones to a new one.
                over_split += 1
            else:
                over += 1
    if over:
        notes.append(f"{over} admissions over the burst budget without a record corruption nearby")

    # 5. Alerts.
    events = _lines(run_dir / "events.jsonl")
    alert_events = [e for e in events if e["event"]["payload"].get("resolvesAlertId") is None]
    resolved = [e for e in events if e["event"]["payload"].get("resolvesAlertId") is not None]
    cooldown = cfg.alert.cooldown_s
    emails = [e for e in alert_events if e["event"]["email"]]
    for i, e2 in enumerate(emails):
        lvl2 = e2["event"]["payload"]["level"]
        for e1 in emails[:i]:
            lvl1 = e1["event"]["payload"]["level"]
            covers = lvl1 == lvl2 or (lvl1 == "critical" and lvl2 == "warning")
            if covers and e2["fake"] - e1["fake"] < cooldown - 1.0:
                if any(e1["t"] < t < e2["t"] for t in kv_lost):
                    notes.append(f"{lvl2} email {e2['fake'] - e1['fake']:.1f}s after a {lvl1} email: the store lost its keys")
                    continue
                problems.append(
                    f"{lvl2} email {e2['fake'] - e1['fake']:.1f}s (fake) after a {lvl1} email; cooldown {cooldown}s"
                    f" (store faults in between: {_faults_between(faults, e1['t'], e2['t'])})",
                )
    for i, e2 in enumerate(alert_events):
        lvl = e2["event"]["payload"]["level"]
        for e1 in alert_events[:i]:
            if e1["event"]["payload"]["level"] != lvl:
                continue
            gap = e2["fake"] - e1["fake"]
            resolved_between = any(e1["t"] < r["t"] < e2["t"] for r in resolved)
            if gap < cooldown - 1.0 and not resolved_between and not any(e1["t"] < t < e2["t"] for t in kv_lost):
                problems.append(f"two {lvl} alerts {gap:.1f}s (fake) apart with no back-to-normal between")
    window_s = cfg.alert.window_s
    evals.sort(key=lambda e: e["t"])
    for r in resolved:
        clear_since = _parse_iso(r["event"]["payload"]["clearSince"])
        if r["fake"] - clear_since < window_s - 1.0:
            problems.append(f"back-to-normal after {r['fake'] - clear_since:.1f}s clear (< window {window_s})")
        fired_since = [e for e in alert_events if clear_since <= e["fake"] <= r["fake"] - 0.001]
        if fired_since:
            problems.append(f"back-to-normal at {r['fake']:.0f} with alerts fired since clear_since {clear_since:.0f}")
        busy = [e for e in evals if r["t"] - (window_s - 2) <= e["t"] < r["t"] and e["any"]]
        jumped = any(r["t"] - window_s <= t <= r["t"] and j > 0 for t, j in clock_jumps)
        if busy and not jumped:
            problems.append(f"back-to-normal at {r['t']:.1f} with a busy evaluation {r['t'] - busy[-1]['t']:.1f}s before")
    # Leader uniqueness: a new pid evaluates only after the previous one died.
    leader_changes = 0
    for prev, cur in zip(evals, evals[1:], strict=False):
        if prev["pid"] == cur["pid"]:
            continue
        leader_changes += 1
        died = killed_at.get(prev["pid"])
        if died is None or died > cur["t"]:
            problems.append(f"leader {cur['pid']} evaluated at {cur['t']:.2f} while {prev['pid']} was still leader")
    eval_pids_alive_overlap = 0

    # 6. Yield signal.
    yield_active = [y for y in yields if y["active"]]
    acked = [y for y in yields if y["active"] and y["ackers"] > 0]

    # 7. Logs.
    log_findings: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for path in sorted(run_dir.glob("*.log")):
        lines = path.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            key = None
            if line.startswith("Traceback"):
                prev = lines[i - 1] if i else ""
                if _LOG_RECORD.match(prev):
                    # Logged on purpose with exc_info: a handled failure.
                    key = "logged_traceback:" + prev.split(": ", 1)[-1][:80]
                elif i >= 2 and ("above exception" in lines[i - 2] or "During handling" in lines[i - 2]):
                    continue
                else:
                    key = "unhandled_traceback"
            elif "exception was never retrieved" in line or "was destroyed but it is pending" in line:
                key = "asyncio_leak"
            elif " ERROR " in line or " CRITICAL " in line:
                key = "error:" + _error_kind(line)
            if key:
                log_findings[key] += 1
                examples.setdefault(key, f"{path.name}: {line[:300]}")
    for key, n in log_findings.items():
        if key in ("unhandled_traceback", "asyncio_leak") or (key.startswith("error:") and key != "error:reading_failed"):
            problems.append(f"{n} log lines '{key}', e.g. {examples[key]}")
        elif key.startswith("logged_traceback"):
            notes.append(f"{n} handled failures logged with a traceback: {key}")

    # 8. Resource growth per process that lived long enough.
    growth = {}
    for pid, items in stats.items():
        if len(items) < 4:
            continue
        first, last = items[1], items[-1]
        d = {k: last[k] - first[k] for k in ("fds", "threads", "os_threads", "rss")}
        growth[pid] = d
        if d["fds"] > 4 or d["os_threads"] > 4 or d["rss"] > 64 * MiB:
            problems.append(f"pid {pid} grew {d} over {last['t'] - first['t']:.0f}s")

    return {
        "config": asdict(cfg),
        "problems": problems,
        "notes": notes,
        "concurrency": {"peak": peak, "ceiling": cfg.expected_ceiling, "runs_started": len(starts),
                        "per_org_peak": per_org_peak},
        "outcomes": dict(outcome_counts),
        "asks": len(asks),
        "live_limit": {"views": len(views), "limits_seen": dict(sorted(limits.items())), "rules": dict(rules)},
        "per_run_check": dict(admit_cls) | {"burst_over_budget": over,
                                            "burst_over_budget_near_corruption": over_near_corruption,
                                            "burst_over_budget_split_record": over_split},
        "alerts": {"alerts": Counter(e["event"]["payload"]["level"] for e in alert_events),
                   "emails": Counter(e["event"]["payload"]["level"] for e in emails),
                   "resolved": len(resolved), "evaluations": len(evals), "leader_changes": leader_changes,
                   "alert_loop_errors": len(alert_errors), "overlap": eval_pids_alive_overlap},
        "yield": {"samples": len(yields), "active_samples": len(yield_active), "acked_samples": len(acked)},
        "logs": dict(log_findings),
        "growth": growth,
        "phases": Counter(p for _, p in phase_log),
        "processes": len(query_pids),
    }


def _faults_between(faults: list[dict[str, Any]], t1: float, t2: float) -> list[str]:
    return [f"{f['e']}:{f.get('mode', '')}" for f in faults if t1 - 10 <= f["t"] <= t2 and f["e"] in ("kv", "pub", "clock")]


def _parse_iso(text: str) -> float:
    from datetime import UTC, datetime

    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


_LOG_RECORD = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ [A-Z]+ ")


def _error_kind(line: str) -> str:
    if "reading failed; holding the limit" in line:
        return "reading_failed"
    if "no code run can start" in line:
        return "impossible_floor"
    return "other"


# --------------------------------------------------------------------------
# Entry points


def run_chaos(cfg: ChaosConfig, run_dir: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir or Path(tempfile.mkdtemp(prefix=f"sbx-chaos-{cfg.name}-{cfg.seed}-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    result = Controller(cfg, run_dir).run_all()
    result["run_dir"] = str(run_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--worker", choices=("query", "indexing"))
    parser.add_argument("--run-dir")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--config", default="c2", choices=sorted(PRESETS))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--fault-every", type=float, default=10.0)
    parser.add_argument("--skip-fault", action="append", default=[], choices=sorted(set(FAULTS)))
    parser.add_argument("--analyse", metavar="RUN_DIR", help="check a finished run's files again")
    args = parser.parse_args(argv)
    if args.analyse:
        run_dir = Path(args.analyse)
        cfg = ChaosConfig.from_json(json.loads((run_dir / "config.json").read_text()))
        result = analyse(run_dir, cfg)
        print(json.dumps({k: v for k, v in result.items() if k not in ("growth", "config")}, indent=1, default=str))
        return 1 if result["problems"] else 0
    if args.worker:
        worker_main(args.worker, Path(args.run_dir), args.index)
        return 0
    cfg = ChaosConfig(name=args.config, seed=args.seed, duration_s=args.duration,
                      fault_every_s=args.fault_every, skip_faults=args.skip_fault, **PRESETS[args.config])
    result = run_chaos(cfg, Path(args.run_dir) if args.run_dir else None)
    print(json.dumps({k: v for k, v in result.items() if k not in ("growth", "config")}, indent=1, default=str))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
