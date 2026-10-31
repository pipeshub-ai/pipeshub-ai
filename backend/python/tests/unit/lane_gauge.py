"""An independent gauge for the lane budget, and the child-process roles the
multi-process tests run.

The gauge never reads the lease table. A job body appends ``start <pid> <t>``
and ``end <pid> <t>`` to a shared file opened ``O_APPEND``; the test sweeps the
intervals afterwards. A budget that over-admits shows up here even though its
own table agrees with itself.

Run a role with ``python -m tests.unit.lane_gauge <role> <json-args>``. Children
are real OS processes started with ``exec`` (never ``fork`` alone), so the file
locks they take are the kernel's.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from app.services.resource_governor.lanes import LaneBudget


class FakeClock:
    """A settable monotonic clock for the pure and ledger tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Mem:
    """A memory reader the test sets: ``(limit, raw working set)``."""

    def __init__(self, limit: int | None = None, raw: int | None = None) -> None:
        self.limit, self.raw, self.calls = limit, raw, 0

    def __call__(self) -> tuple[int | None, int | None]:
        self.calls += 1
        return self.limit, self.raw

    def at(self, fraction: float) -> None:
        assert self.limit is not None
        self.raw = int(self.limit * fraction)


def start_role(role: str, args: dict, *, cwd: str | None = None) -> subprocess.Popen:
    """Start *role* in a new interpreter (spawn, not fork) and return it."""
    root = cwd or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root}
    return subprocess.Popen(
        [sys.executable, "-m", "tests.unit.lane_gauge", role, json.dumps(args)],
        cwd=root, env=env, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
    )


def first_line(proc: subprocess.Popen) -> dict:
    """The first JSON line the child prints, which says it is ready."""
    assert proc.stdout is not None
    return json.loads(proc.stdout.readline())


def reap(*procs: subprocess.Popen) -> None:
    """Kill and wait for children this test started, by handle."""
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def now() -> float:
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def gauge_job(path: str | Path, seconds: float, pid: int | None = None) -> None:
    """A job body: record the interval it ran for."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    pid = os.getpid() if pid is None else pid
    try:
        os.write(fd, f"start {pid} {now():.6f}\n".encode())
        time.sleep(seconds)
        os.write(fd, f"end {pid} {now():.6f}\n".encode())
    finally:
        os.close(fd)


def max_overlap(path: str | Path) -> int:
    """The most jobs that were running at one instant, by sweeping the intervals."""
    events: list[tuple[float, int]] = []
    for line in open(path).read().splitlines():
        kind, _pid, stamp = line.split()
        events.append((float(stamp), 1 if kind == "start" else -1))
    events.sort(key=lambda e: (e[0], e[1]))
    running = peak = 0
    for _t, delta in events:
        running += delta
        peak = max(peak, running)
    return peak


def wait_until(stamp: float) -> None:
    while now() < stamp:
        time.sleep(0.0005)


def _reader(limit: int | None, raw: int | None) -> Callable[[], tuple[int | None, int | None]]:
    return lambda: (limit, raw)


def _budget(args: dict) -> LaneBudget:
    from pathlib import Path as P

    from app.services.resource_governor.lanes import LaneBudget, plan_lanes

    plan = plan_lanes(cpus=args["lanes"] * 2, heavy=args["lanes"] * 2)
    options = {}
    if "mem_limit" in args:
        options["memory_reader"] = _reader(args["mem_limit"], args.get("mem_raw"))
    if "view_ttl" in args:
        options["view_ttl"] = args["view_ttl"]
    return LaneBudget.open(plan, P(args["dir"]), **options)


def role_run(args: dict) -> dict:
    """Run gauge jobs back to back on leases until the deadline."""
    budget = _budget(args)
    stop = threading.Event()
    sampled = {"max_run": 0, "samples": 0}

    def sampler() -> None:
        while not stop.is_set():
            run, _ = budget.held_count()
            sampled["max_run"] = max(sampled["max_run"], run)
            sampled["samples"] += 1
            time.sleep(0.005)

    wait_until(args["start_at"])
    deadline = now() + args["seconds"]
    threading.Thread(target=sampler, daemon=True).start()
    done = 0

    def worker() -> None:
        nonlocal done
        while now() < deadline:
            lease = budget.try_acquire()
            if lease is None:
                time.sleep(0.003)
                continue
            try:
                gauge_job(args["gauge"], args["job_seconds"])
                done += 1
            finally:
                lease.release()

    threads = [threading.Thread(target=worker) for _ in range(args["threads"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    budget.close()
    return {"done": done, **sampled}


def role_admit(args: dict) -> dict:
    """Call ``admit_spawn`` at the barrier, then keep any ticket until told to stop."""
    budget = _budget(args)
    wait_until(args["start_at"])
    tickets = []
    results = []
    for _ in range(args.get("calls", 1)):
        if args.get("calls", 1) > 1:
            time.sleep(0.005)
        ticket = budget.admit_spawn(args.get("ws", 0))
        results.append(ticket is not None)
        if ticket is not None:
            tickets.append(ticket)
    print(json.dumps({"admitted": results}), flush=True)
    wait_until(args["stop_at"])
    for t in tickets:
        t.release(started=True)
        t.worker_exited()
    budget.close()
    return {}


def role_hold_resident(args: dict) -> dict:
    """Take a resident token via a spawn ticket and sit on it until killed."""
    budget = _budget(args)
    ticket = budget.admit_spawn(0)
    lease = budget.try_acquire()
    print(json.dumps({"admitted": ticket is not None, "leased": lease is not None}), flush=True)
    time.sleep(60)
    return {}


def role_hold_lock(args: dict) -> dict:
    """Hold ``ledger.lock`` until killed or stopped."""
    import fcntl

    fd = os.open(os.path.join(args["dir"], "ledger.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    print(json.dumps({"locked": True}), flush=True)
    time.sleep(60)
    return {}


def role_lock_blip(args: dict) -> dict:
    """Hold ``ledger.lock`` for a short, fixed interval so that callers queue up behind it."""
    import fcntl

    fd = os.open(os.path.join(args["dir"], "ledger.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    wait_until(args["start_at"])
    fcntl.flock(fd, fcntl.LOCK_EX)
    time.sleep(args["seconds"])
    fcntl.flock(fd, fcntl.LOCK_UN)
    return {}


def role_hold_mutex(args: dict) -> dict:
    """Hold the resident set's mutex, as a process stalled inside ``try_acquire`` would."""
    import fcntl

    fd = os.open(os.path.join(args["dir"], "resident", "mutex"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    print(json.dumps({"locked": True}), flush=True)
    time.sleep(60)
    return {}


def role_ledger_writer(args: dict) -> dict:
    """Write records whose fields agree with each other, as fast as possible."""
    from pathlib import Path as P

    from app.services.resource_governor.lane_ledger import DomainLedger, LedgerRecord

    ledger = DomainLedger(P(args["dir"]), view_ttl=0.0)
    print(json.dumps({"ready": True}), flush=True)
    i = 0
    while True:
        i += 1
        with ledger.transaction() as txn:
            if txn is not None:
                n = (i % 60) + 1
                txn.write(LedgerRecord(plan_lanes=n, cap=n, cap_stamp=float(n),
                                       cap_owner_pid=os.getpid(), spawns_in_window=n,
                                       window_start=float(n), last_brake_at=float(n)))


def role_incident(args: dict) -> dict:
    """Report one memory incident through this process's budget."""
    budget = _budget(args)
    return {"cap": budget.report_incident()}


def role_live(args: dict) -> dict:
    """Open a budget (which creates a presence file) and wait."""
    budget = _budget(args)
    print(json.dumps({"pid": os.getpid()}), flush=True)
    time.sleep(60)
    budget.close()
    return {}


ROLES = {
    "run": role_run,
    "admit": role_admit,
    "hold_resident": role_hold_resident,
    "hold_lock": role_hold_lock,
    "hold_mutex": role_hold_mutex,
    "lock_blip": role_lock_blip,
    "ledger_writer": role_ledger_writer,
    "live": role_live,
    "incident": role_incident,
}


def main(argv: list[str]) -> None:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    result = ROLES[argv[1]](json.loads(argv[2]))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main(sys.argv)
