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


def start_role(
    role: str, args: dict, *, cwd: str | None = None, commands: bool = False
) -> subprocess.Popen:
    """Start *role* in a new interpreter (spawn, not fork) and return it.

    With ``commands`` the child reads JSON lines from its stdin (see ``send``).
    """
    root = cwd or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = {**os.environ, "PYTHONPATH": root}
    return subprocess.Popen(
        [sys.executable, "-m", "tests.unit.lane_gauge", role, json.dumps(args)],
        cwd=root, env=env, stdout=subprocess.PIPE,
        stdin=subprocess.PIPE if commands else subprocess.DEVNULL, text=True,
    )


def send(proc: subprocess.Popen, **command: object) -> dict:
    """Send one command to a role started with ``commands=True`` and return its reply."""
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(command) + "\n")
    proc.stdin.flush()
    return first_line(proc)


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
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()


def now() -> float:
    return time.clock_gettime(time.CLOCK_MONOTONIC)


def start_together(
    role: str, args: dict, count: int, *, lead: float = 0.3, blip: dict | None = None
) -> tuple[list[subprocess.Popen], subprocess.Popen | None]:
    """Start *count* children of *role* that begin their work at the same instant.

    A fixed start time chosen before the children exist fails when a busy host
    takes longer than that to start an interpreter. Instead each child prints
    ``{"ready": true}`` once it is set up and waits for the ``go`` file, which holds
    the start time, written here only after every child has reported ready. A child
    reads the start time with ``barrier(args)``. The optional *blip* role gets the
    same file and starts ``blip["before"]`` seconds ahead of the others.
    """
    go = f"{args['dir']}.go"
    shared = {**args, "go": go}
    procs = [start_role(role, shared) for _ in range(count)]
    side = None
    try:
        if blip is not None:
            side = start_role("lock_blip", {**blip, "go": go})
            first_line(side)
        for proc in procs:
            first_line(proc)
    except BaseException:
        # The caller gets no handles back, so children already waiting on the go
        # file would spin forever on a shared host.
        reap(*procs, *([side] if side is not None else []))
        raise
    tmp = f"{go}.tmp"
    with open(tmp, "w") as handle:
        handle.write(repr(now() + lead))
    os.replace(tmp, go)
    return procs, side


def barrier(args: dict) -> float:
    """The start time for a child, once the parent says go (see ``start_together``)."""
    print(json.dumps({"ready": True}), flush=True)
    # A parent that died or gave up never writes the go file.
    deadline = now() + args.get("go_timeout", 120.0)
    while not os.path.exists(args["go"]):
        if now() > deadline:
            sys.exit("no go file before the deadline")
        time.sleep(0.005)
    with open(args["go"]) as handle:
        return float(handle.read()) - args.get("before", 0.0)


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


def pid_job(path: str | Path, seconds: float) -> int:
    """A gauge job that returns the pid of the process it ran in (a parse worker, in a pool)."""
    gauge_job(path, seconds)
    return os.getpid()


def env_job(names: list[str]) -> dict[str, str | None]:
    """What a parse worker sees in its environment."""
    return {name: os.environ.get(name) for name in names}


def spawn_sleeper_job(path: str) -> None:
    """Leave a child in the worker's process group, as the PDF rasteriser's pool does, and keep parsing.

    The child's pid goes to *path*. A worker blocked in a parse is the case the
    exit-with-parent watcher exists for: an idle one sees its pipe close.
    """
    import subprocess as sp

    child = sp.Popen(
        [sys.executable, "-c", "import os, time; os.closerange(3, 4096); time.sleep(600)"],
        stdin=sp.DEVNULL,
    )
    with open(path, "w") as handle:
        handle.write(str(child.pid))
    time.sleep(600)


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
    if "warmup_seconds" in args:
        options["warmup_seconds"] = args["warmup_seconds"]
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

    start = barrier(args)
    wait_until(start)
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
    start = barrier(args)
    wait_until(start)
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
    wait_until(start + args["stop_after"])
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
    wait_until(barrier(args))
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
    """Open a budget, count this process as a pool owner, and wait."""
    budget = _budget(args)
    budget.register_presence()
    print(json.dumps({"pid": os.getpid()}), flush=True)
    time.sleep(60)
    budget.close()
    return {}


def role_pool(args: dict) -> dict:
    """A budgeted parse pool with real workers, driven by JSON commands on stdin.

    ``{"op": "job", "gauge": path, "seconds": s}`` replies with the worker pid;
    ``{"op": "stats"}`` with the pool gauges; ``{"op": "quit"}`` ends the role.
    """
    from app.modules.parsers.parse_pool import _Job
    from app.modules.parsers.parse_pool_lanes import LanePool, worker_thread_env

    # Under ``-m`` this module is ``__main__``, which a worker cannot import by name.
    from tests.unit.lane_gauge import pid_job

    budget = _budget(args)
    budget.register_presence()
    pool = LanePool(
        kind="budgeted", threads=args.get("threads", 3), budget=budget, admit=True,
        worker_env=worker_thread_env(1), reap_tick=args.get("tick", 1.0),
        idle_seconds=args.get("idle_seconds"),
    )
    print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
    out = threading.Lock()

    def answer(reply: dict) -> None:
        with out:
            print(json.dumps(reply), flush=True)

    def run(command: dict) -> None:
        job = _Job(pid_job, (command["gauge"], command["seconds"]), "role", 0)
        pool.enqueue(job)
        try:
            reply = {"pid": job.future.result(timeout=command.get("timeout", 120))}
        except Exception as exc:
            reply = {"error": type(exc).__name__}
        answer(reply | {"id": command.get("id"), "done_at": now()})

    for line in sys.stdin:
        command = json.loads(line)
        if command["op"] == "quit":
            break
        if command["op"] == "stats":
            answer(pool.stats() | {"cap": budget.cap()})
        else:
            threading.Thread(target=run, args=(command,), daemon=True).start()
    pool.shutdown()
    budget.close()
    return {}


def role_pool_orphans(args: dict) -> dict:
    """Start a worker that leaves a child behind, report both pids, then wait to be killed."""
    from app.modules.parsers.parse_pool import _Job
    from app.modules.parsers.parse_pool_lanes import LanePool
    from tests.unit.lane_gauge import spawn_sleeper_job

    budget = _budget(args)
    budget.register_presence()
    pool = LanePool(kind="budgeted", threads=1, budget=budget, admit=True, reap_tick=60.0)
    marker = os.path.join(args["dir"], "child.pid")
    pool.enqueue(_Job(spawn_sleeper_job, (marker,), "orphans", 0))
    while not os.path.exists(marker) or not open(marker).read():
        time.sleep(0.05)
    worker = next(iter(pool._out))
    print(json.dumps({"worker": worker.worker.process.pid, "child": int(open(marker).read())}), flush=True)
    time.sleep(60)
    return {}


ROLES = {
    "pool": role_pool,
    "pool_orphans": role_pool_orphans,
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
