"""Stub workers, a /proc scan and a sampler for the lane pool tests.

A stub worker speaks the worker protocol the pool uses (``send``,
``reply_ready``, ``receive``, ``alive``, ``stop``, ``process.poll()``) and runs
the job in a helper thread, so a test can hold a job, kill a worker, or make a
process survive ``stop``. Stubs make the reap and checkout interleavings
deterministic and fast; the tests that matter for admission use real workers.
"""
from __future__ import annotations

import itertools
import os
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class StubProcess:
    _pids = itertools.count(900_000)

    def __init__(self) -> None:
        self.pid = next(self._pids)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class StubWorker:
    """A worker that runs jobs in a thread of this process."""

    def __init__(self, hub: StubHub) -> None:
        self.hub = hub
        self.process = StubProcess()
        self.started = False
        self.running = False
        self.stopped = False
        self.survives = False  # stop() leaves the process alive until ``release_survivor``
        self.alive_checks_left: int | None = None  # dies after this many alive() answers of True
        self._job: tuple[Any, tuple] | None = None
        self._done = threading.Event()
        self._result: tuple[bool, Any, str] | None = None
        hub.workers.append(self)

    # -- the worker protocol ---------------------------------------------------
    def alive(self) -> bool:
        if self.alive_checks_left is not None and self.process.poll() is None:
            if self.alive_checks_left <= 0:
                self.process.returncode = -9
            self.alive_checks_left -= 1
        return self.process.poll() is None

    def send(self, obj: tuple) -> None:
        if self.stopped or not self.alive():
            self.hub.violations.append(f"send to a stopped worker {self.process.pid}")
            raise OSError("worker is gone")
        self._job = obj
        self._done.clear()
        self.running = True
        threading.Thread(target=self._execute, daemon=True).start()

    def _execute(self) -> None:
        fn, args = self._job  # type: ignore[misc]
        try:
            self._result = (True, fn(*args), "")
        except BaseException as exc:
            self._result = (False, exc, "stub")
        finally:
            self.running = False
            self._done.set()

    def reply_ready(self, timeout: float) -> bool:
        if not self.started:
            self.hub.start_gate.wait(timeout)
            return self.hub.start_gate.is_set()
        return self._done.wait(timeout) or not self.alive()

    def receive(self) -> object:
        if not self.started:
            self.started = True
            return self.process.pid
        if self._result is None:
            raise EOFError
        return self._result

    def stop(self) -> None:
        if self.running:
            self.hub.violations.append(f"stopped while running {self.process.pid}")
        self.stopped = True
        self.hub.stop_calls += 1
        if not self.survives:
            self.process.returncode = -9
            self._done.set()

    # -- test controls -----------------------------------------------------------
    def kill(self) -> None:
        """The process dies by itself (OOM, crash)."""
        self.process.returncode = -9
        self._done.set()

    def release_survivor(self) -> None:
        self.survives = False
        self.process.returncode = -9

    def group_gone(self) -> bool:
        return self.process.poll() is not None


class StubHub:
    def __init__(self) -> None:
        self.workers: list[StubWorker] = []
        self.violations: list[str] = []
        self.stop_calls = 0
        self.start_gate = threading.Event()
        self.start_gate.set()
        self.fail_start: Exception | None = None

    def factory(self) -> StubWorker:
        if self.fail_start is not None:
            raise self.fail_start
        return StubWorker(self)

    def alive_workers(self) -> list[StubWorker]:
        return [w for w in self.workers if w.alive()]


def resident_workers(parents: set[int] | None = None) -> list[int]:
    """Live parse workers whose parent is one of *parents* (this process by default), by a /proc scan."""
    mine = {str(p) for p in (parents or {os.getpid()})}
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[-1].split()
            if fields[0] == b"Z" or fields[1].decode() not in mine:
                continue
            with open(f"/proc/{name}/cmdline", "rb") as handle:
                if b"parse_worker" in handle.read():
                    found.append(int(name))
        except OSError:
            continue
    return found


class ProcSampler:
    """Counts resident parse workers every 20 ms until stopped."""

    def __init__(self, interval: float = 0.02, parents: set[int] | None = None) -> None:
        self.interval = interval
        self.parents = parents
        self.peak = 0
        self.seen: set[int] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            pids = resident_workers(self.parents)
            self.peak = max(self.peak, len(pids))
            self.seen.update(pids)
            time.sleep(self.interval)

    def __enter__(self) -> ProcSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def wait_for(predicate: Callable[[], object], seconds: float = 10.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def pid_after(seconds: float) -> int:
    """A job for a real worker: sleep, then report the worker's pid."""
    time.sleep(seconds)
    return os.getpid()


def double(x: int) -> int:
    return x * 2


def weighted_job(path: str, weight: int, seconds: float) -> int:
    """A job body for the weighted gauge: it records its own start and end, with its weight,
    in a file every process appends to (``O_APPEND``), and never reads the lease table."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, f"start {weight} {time.monotonic():.6f}\n".encode())
        time.sleep(seconds)
        os.write(fd, f"end {weight} {time.monotonic():.6f}\n".encode())
    finally:
        os.close(fd)
    return os.getpid()


def peak_weight(path: str) -> int:
    """The most weight that was running at one instant, from the gauge file."""
    events = []
    for line in open(path).read().splitlines():
        kind, weight, stamp = line.split()
        # An end sorts before a start at the same instant.
        events.append((float(stamp), 0 if kind == "end" else 1, int(weight)))
    events.sort()
    active = peak = 0
    for _, order, weight in events:
        active += weight if order else -weight
        peak = max(peak, active)
    return peak
