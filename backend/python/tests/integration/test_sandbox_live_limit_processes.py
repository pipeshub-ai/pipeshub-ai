"""Several query workers share one live limit record.

Each worker is a real process with its own ``LiveLimit`` on one state
directory, reading a fake host from a JSON file the test rewrites. The
container must step at most once per interval whichever worker steps, every
worker must see the same limit, a killed worker must not stop the others, and
the per-run free-memory check must admit exactly as many runs as fit when the
workers ask at the same moment.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import random
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Barrier, Event

pytest.importorskip("fcntl")
pytestmark = pytest.mark.timeout(60)

GiB = 2**30
INTERVAL_S = 1.0


class _FileReader:
    source = "host"

    def __init__(self, path: str) -> None:
        self._path = path

    def _avail(self) -> int | None:
        try:
            return int(json.loads(Path(self._path).read_text())["avail_gib"] * GiB)
        except (OSError, ValueError, KeyError):
            return None

    def read(self) -> Any:  # noqa: ANN401
        from app.agent_loop_lib.sandbox.host_reading import HostSample

        return HostSample(mem_total=16 * GiB, mem_available=self._avail(), source="host")

    def memory(self) -> tuple[int | None, int | None]:
        return self._avail(), 16 * GiB


def _live(directory: str, reader_file: str) -> Any:  # noqa: ANN401
    from app.agent_loop_lib.sandbox.capacity import (
        CapacitySettings,
        MachineSize,
        SandboxCapacity,
    )
    from app.agent_loop_lib.sandbox.host_reading import LiveMode
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings

    capacity = SandboxCapacity("compose", CapacitySettings(), slot_files=256)
    capacity._apply(MachineSize(16 * GiB, 4.0, None, "docker_info"), None)
    mode = LiveMode(mode="on:host", reader=_FileReader(reader_file), profile="host")  # type: ignore[arg-type]
    live = LiveLimit(
        capacity, None, settings=LiveSettings(interval_s=INTERVAL_S), resolver=lambda: mode,
        directory=Path(directory),
    )
    live.apply_mode(mode)
    return live


def _sampler(directory: str, reader_file: str, stop: Event, out: Queue) -> None:
    live = _live(directory, reader_file)
    steps: list[tuple[float, int]] = []
    while not stop.is_set():
        record = live.sample_once()
        if record is not None:
            steps.append((time.monotonic(), record.limit))
            out.put(("step", os.getpid(), steps[-1][0], record.limit))
        time.sleep(0.05)
    out.put(("done", os.getpid(), time.monotonic(), live.view(fresh=True).limit))


def _admitter(directory: str, reader_file: str, rounds: int, barrier: Barrier, out: Queue) -> None:
    import asyncio

    for i in range(rounds):
        live = _live(f"{directory}/r{i}", reader_file)
        barrier.wait()
        reason = asyncio.run(live.admit_and_charge(held=0))
        out.put((i, reason is None))


def _write(path: Path, avail_gib: float) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"avail_gib": avail_gib}))
    tmp.replace(path)


def test_workers_step_once_per_interval_and_agree(tmp_path: Path) -> None:
    reader_file = tmp_path / "host.json"
    _write(reader_file, 7.0)
    ctx = multiprocessing.get_context("spawn")
    stop, out = ctx.Event(), ctx.Queue()
    procs = [
        ctx.Process(target=_sampler, args=(str(tmp_path / "state"), str(reader_file), stop, out))
        for _ in range(4)
    ]
    for proc in procs:
        proc.start()
    events: list[tuple[str, int, float, int]] = []
    try:
        deadline = time.monotonic() + 30
        while not any(e[0] == "step" for e in events) and time.monotonic() < deadline:
            events.append(out.get(timeout=30))
        time.sleep(3.0)
        _write(reader_file, 4.0)
        time.sleep(3.0)
        # The worker that stepped last dies; the others carry on.
        while not out.empty():
            events.append(out.get())
        last_stepper = max((e for e in events if e[0] == "step"), key=lambda e: e[2])[1]
        victim = next(p for p in procs if p.pid == last_stepper)
        os.kill(last_stepper, signal.SIGKILL)
        killed_at = time.monotonic()
        victim.join(10)
        time.sleep(4.0)
        stop.set()
        survivors = [p for p in procs if p is not victim]
        done = 0
        while done < len(survivors):
            event = out.get(timeout=30)
            events.append(event)
            done += event[0] == "done"
    finally:
        stop.set()
        for proc in procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()

    steps = sorted((e for e in events if e[0] == "step"), key=lambda e: e[2])
    gaps = [b[2] - a[2] for a, b in zip(steps, steps[1:])]
    assert gaps and min(gaps) >= 0.85 * INTERVAL_S - 0.05
    assert len({e[1] for e in steps}) >= 1
    assert any(e[2] > killed_at + 0.5 for e in steps), "no step after the stepper was killed"
    assert steps[0][3] == 6
    assert steps[-1][3] == 3
    finals = {e[3] for e in events if e[0] == "done"}
    assert finals == {3}

    from app.agent_loop_lib.sandbox.live_record import LiveLedger

    record = LiveLedger(tmp_path / "state").read()
    assert record is not None and record.limit == 3


def test_simultaneous_admissions_take_only_what_fits(tmp_path: Path) -> None:
    reader_file = tmp_path / "host.json"
    # The 1 GiB floor plus 2.5 runs of 512 MiB: two fit, the third would not.
    _write(reader_file, 2.25)
    rounds = 20
    ctx = multiprocessing.get_context("spawn")
    barrier, out = ctx.Barrier(4), ctx.Queue()
    procs = [
        ctx.Process(target=_admitter, args=(str(tmp_path), str(reader_file), rounds, barrier, out))
        for _ in range(4)
    ]
    for proc in procs:
        proc.start()
    try:
        results = [out.get(timeout=50) for _ in range(rounds * 4)]
    finally:
        for proc in procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()
    admitted: dict[int, int] = {}
    for round_no, ok in results:
        admitted[round_no] = admitted.get(round_no, 0) + ok
    assert admitted == dict.fromkeys(range(rounds), 2)


def _http_get(port: int) -> Any:  # noqa: ANN401
    import urllib.request

    def get(path: str, timeout: float) -> tuple[int, Any]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())

    return get


def _sidecar_sampler(directory: str, port: int, stop: Event, out: Queue) -> None:
    from app.agent_loop_lib.sandbox.capacity import (
        CapacitySettings,
        MachineSize,
        SandboxCapacity,
    )
    from app.agent_loop_lib.sandbox.host_reading import resolve_live_mode
    from app.agent_loop_lib.sandbox.live_limit import LiveLimit
    from app.agent_loop_lib.sandbox.live_settings import LiveSettings

    settings = CapacitySettings(host_memory_bytes=16 * GiB, host_cpu_millis=16000, memory_share=0.9)
    capacity = SandboxCapacity("helm_dind", settings, slot_files=256)
    capacity._apply(MachineSize(16 * GiB, 16.0, None, "helm_env"), None)
    live_settings = LiveSettings(interval_s=INTERVAL_S)
    mode = resolve_live_mode(capacity, live_settings, http_get=_http_get(port))
    live = LiveLimit(capacity, None, settings=live_settings, resolver=lambda: mode, directory=Path(directory))
    live.apply_mode(mode)
    out.put(("mode", os.getpid(), time.monotonic(), mode.mode))
    rng = random.Random(os.getpid())
    while not stop.is_set():
        record = live.sample_once()
        if record is not None:
            out.put(("step", os.getpid(), time.monotonic(), record.limit))
        # Like the real sampler: an interval plus jitter between tries.
        stop.wait(INTERVAL_S * 0.5 + rng.random())
    out.put(("done", os.getpid(), time.monotonic(), live.view(fresh=True).limit))


def test_sidecar_workers_read_the_proxy_once_per_step(tmp_path: Path) -> None:
    import http.server
    import threading

    state = {"used": 2 * GiB, "hits": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state["hits"] += 1
            body = json.dumps({"v": 1, "sidecarError": None, "sidecar": {
                "memLimitBytes": 16 * GiB, "memWorkingSetBytes": state["used"], "memPsiFullAvg10": 0.0,
                "oomEventsDelta": 0, "cpuUtil": 0.1, "cpuThrottledRatio": 0.0,
            }}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ctx = multiprocessing.get_context("spawn")
    stop, out = ctx.Event(), ctx.Queue()
    procs = [
        ctx.Process(target=_sidecar_sampler, args=(str(tmp_path / "state"), server.server_port, stop, out))
        for _ in range(4)
    ]
    for proc in procs:
        proc.start()
    events: list[tuple[str, int, float, Any]] = []
    try:
        while sum(e[0] == "mode" for e in events) < 4:
            events.append(out.get(timeout=30))
        resolve_hits = state["hits"]
        time.sleep(4.0)
        # 96 % used: the hard rule halves the limit.
        state["used"] = int(16 * GiB * 0.96)
        time.sleep(4.0)
        stop.set()
        while sum(e[0] == "done" for e in events) < 4:
            events.append(out.get(timeout=30))
    finally:
        stop.set()
        for proc in procs:
            proc.join(10)
            if proc.is_alive():
                proc.kill()
        server.shutdown()

    assert {e[3] for e in events if e[0] == "mode"} == {"on:sidecar"}
    steps = sorted((e for e in events if e[0] == "step"), key=lambda e: e[2])
    gaps = [b[2] - a[2] for a, b in zip(steps, steps[1:])]
    assert gaps and min(gaps) >= 0.85 * INTERVAL_S - 0.05
    # Only the worker that steps reads the proxy (two workers can rarely both
    # pass the unlocked look and read before one takes the step).
    # The first workers may have stepped before the last one had resolved.
    assert state["hits"] - resolve_hits <= len(steps) + 2
    limits = [e[3] for e in steps]
    assert limits[0] == 24 and limits[-1] < 24
    assert 12 in limits
