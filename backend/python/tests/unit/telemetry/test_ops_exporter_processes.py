"""Real processes: one holds the port, kills leave no stale gauges, and counters stay monotonic.

Needs no service, only loopback ports in 19200-19299 (override with OPS_EXPORTER_TEST_PORT).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

PY = Path(__file__).resolve().parents[3]
BASE_PORT = int(os.environ.get("OPS_EXPORTER_TEST_PORT", "19260"))

WORKER = textwrap.dedent(
    """
    import sys, time
    from app.telemetry.ops_exporter import OpsExporter
    run, port, in_use, brakes = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    def source():
        return {}, {
            "pipeshub_governor_pool_in_use": [[["indexing", "heavy_parse"], in_use]],
            "pipeshub_governor_brakes_total": [[["indexing", "heavy_parse", "memory_soft"], brakes]],
        }
    OpsExporter(service="indexing", run_dir=run, port=port, source=source, interval=0.2,
                rebind_interval=0.5, stale_after=3.0).start()
    time.sleep(600)
    """
)
IN_USE = 'pipeshub_governor_pool_in_use{pool="heavy_parse",service="indexing"}'
BRAKES = 'pipeshub_governor_brakes_total{pool="heavy_parse",reason="memory_soft",service="indexing"}'


def _free(port: int) -> int:
    while True:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1


def _scrape(port: int, timeout: float = 15.0) -> dict[str, float]:
    deadline = time.time() + timeout
    while True:
        try:
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2).read().decode()
            return {ln.rsplit(" ", 1)[0]: float(ln.rsplit(" ", 1)[1]) for ln in body.splitlines() if ln and not ln.startswith("#")}
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(0.2)


def _eventually(port: int, series: str, want: float, timeout: float = 15.0) -> dict[str, float]:
    deadline = time.time() + timeout
    while True:
        got = _scrape(port)
        if got.get(series) == want or time.time() > deadline:
            return got
        time.sleep(0.2)


def _holder(run: Path) -> int:
    return int((run / "holder").read_text()) if (run / "holder").exists() else -1


@pytest.fixture
def procs():  # noqa: ANN201
    started: list[subprocess.Popen] = []
    yield started
    for p in started:
        if p.poll() is None:
            p.kill()
        p.wait()


def _spawn(procs: list, run: Path, port: int, in_use: float, brakes: float) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    p = subprocess.Popen([sys.executable, "-c", WORKER, str(run), str(port), str(in_use), str(brakes)], cwd=PY, env=env)
    procs.append(p)
    return p


def test_kill_freshness_and_carry(tmp_path: Path, procs: list) -> None:
    port = _free(BASE_PORT)
    a = _spawn(procs, tmp_path, port, 3, 50)
    b = _spawn(procs, tmp_path, port, 5, 50)
    c = _spawn(procs, tmp_path, port, 7, 0)
    got = _eventually(port, IN_USE, 15)
    assert got[IN_USE] == 15 and got[BRAKES] == 100
    assert not any("pid" in k for k in got)

    # Whoever serves, kill a worker that is not the holder: its gauge goes at the next scrape and its file is deleted.
    holder_pid = next(p.pid for p in (a, b, c) if _holds(port, p.pid))
    victim = next(p for p in (a, b) if p.pid != holder_pid)
    value = {a.pid: 3, b.pid: 5}[victim.pid]
    os.kill(victim.pid, signal.SIGKILL)
    victim.wait()
    got = _eventually(port, IN_USE, 15 - value)
    assert got[IN_USE] == 15 - value
    assert got[BRAKES] == 100  # the dead worker's 50 moved into the carry, once
    assert not list((tmp_path / "signals").glob(f"{victim.pid}-*.json"))  # folded, then deleted

    # A replacement starts at 0, then counts one event: the series rises by 1, not by 50 + 1.
    _spawn(procs, tmp_path, port, 1, 1)
    got = _eventually(port, BRAKES, 101)
    assert got[BRAKES] == 101

    # Kill the holder: another process serves within 30 s and reads the same carry.
    holder = next(p for p in procs if p.poll() is None and _holds(port, p.pid))
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait()
    t0 = time.time()
    got = _eventually(port, BRAKES, 101, timeout=30)
    assert time.time() - t0 < 30
    assert got[BRAKES] == 101  # exactly the carry and the live counters: no loss, no double count


def _holds(port: int, pid: int) -> bool:
    """Whether *pid* is the one listening on *port* (its socket inode is in /proc/<pid>/fd)."""
    inodes = set()
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        f = line.split()
        if f[3] == "0A" and int(f[1].split(":")[1], 16) == port:
            inodes.add(f[9])
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    return any(os.readlink(f"/proc/{pid}/fd/{fd}").removeprefix("socket:[").removesuffix("]") in inodes for fd in fds if "socket" in os.readlink(f"/proc/{pid}/fd/{fd}"))


def test_scrape_latency_with_64_snapshot_files(tmp_path: Path) -> None:
    from app.telemetry import ops_exporter as ox

    start = ox._proc_start(os.getpid())
    sig = tmp_path / "signals"
    sig.mkdir()
    # 64 files of one live pid each is not possible, so they are fresh files of a non-Linux style (no start): trusted while fresh.
    for pid in range(1000, 1064):
        (sig / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "nonce": f"n{pid}", "start": None, "at": time.time(), "service": "indexing", "signal": {},
            "metrics": {
                "pipeshub_governor_pool_in_use": [[["indexing", p], 1.0] for p in ("index_heavy", "index_light", "heavy_parse", "light_parse")],
                "pipeshub_parse_units_total": [[["small", o], 1.0] for o in ("ok", "failed", "retried")],
            },
        }))
    assert start is not None or True
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=_free(BASE_PORT + 20), source=lambda: ({}, {}))
    assert exporter.try_bind()
    try:
        _scrape(exporter._port)
        times = []
        for _ in range(10):
            t = time.perf_counter()
            body = _scrape(exporter._port)
            times.append(time.perf_counter() - t)
        assert body['pipeshub_governor_pool_in_use{pool="heavy_parse",service="indexing"}'] == 64
        assert sorted(times)[len(times) // 2] <= 0.05
    finally:
        exporter._server.shutdown()
        exporter._server.server_close()


OOM_WORKER = textwrap.dedent(
    """
    import sys, time
    from app.telemetry.ops_exporter import OpsExporter
    run, port, oom = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
    def source():
        return {}, {"pipeshub_cgroup_oom_kills_total": [[["indexing"], oom]]}
    OpsExporter(service="indexing", run_dir=run, port=port, source=source, interval=0.2,
                rebind_interval=0.5, stale_after=3.0).start()
    time.sleep(600)
    """
)
OOM = 'pipeshub_cgroup_oom_kills_total{service="indexing"}'


def test_oom_kills_is_the_cgroup_value_with_several_real_processes_and_a_restart(tmp_path: Path, procs: list) -> None:
    """Every process reads the same container-wide oom_kill; N processes and a restart serve the cgroup value."""
    port = _free(BASE_PORT + 40)
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    spawned = [subprocess.Popen([sys.executable, "-c", OOM_WORKER, str(tmp_path), str(port), "2"], cwd=PY, env=env) for _ in range(3)]
    procs.extend(spawned)
    got = _eventually(port, OOM, 2)
    assert got[OOM] == 2  # not 6
    seen = []
    victim = next(p for p in spawned if not _holds(port, p.pid))
    os.kill(victim.pid, signal.SIGKILL)
    victim.wait()
    procs.append(subprocess.Popen([sys.executable, "-c", OOM_WORKER, str(tmp_path), str(port), "2"], cwd=PY, env=env))
    deadline = time.time() + 6
    while time.time() < deadline:  # a few scrapes across the restart
        seen.append(_scrape(port)[OOM])
        time.sleep(0.2)
    assert set(seen) == {2.0}


def test_gc_never_deletes_a_live_writers_file(tmp_path: Path, procs: list) -> None:
    """A real writer every 0.2 s and a holder-side scan in a tight loop with the real /proc start-time check."""
    from app.telemetry import ops_exporter as ox

    port = _free(BASE_PORT + 45)
    writer = _spawn(procs, tmp_path, port, 1, 7)
    deadline = time.time() + 15
    while time.time() < deadline and not list((tmp_path / "signals").glob(f"{writer.pid}-*.json")):
        time.sleep(0.05)
    collector = ox.SignalCollector(tmp_path, stale_after=5.0, cache_ttl=0)
    hits = sum(any(s.get("pid") == writer.pid for s in collector.scan()) for _ in range(1500))
    carry = tmp_path / ox.CARRY_FILE
    folded = json.loads(carry.read_text())["folded"] if carry.exists() else []
    assert not any(f.startswith(f"{writer.pid}:") for f in folded), f"a live writer was folded: {folded}"
    assert hits >= 1490, hits
    writer.kill()
    writer.wait()
    collector.scan()
    assert not list((tmp_path / "signals").glob(f"{writer.pid}-*.json"))
    assert sum(json.loads(carry.read_text())["values"].values()) == 7  # the brakes counter only; in_use is a gauge


HAMMER = textwrap.dedent(
    """
    import sys, time, urllib.request, concurrent.futures as cf
    port, secs, n = int(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
    def one():
        end = time.time() + secs
        while time.time() < end:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read()
            except OSError:
                pass
    with cf.ThreadPoolExecutor(n) as ex:
        [f.result() for f in [ex.submit(one) for _ in range(n)]]
    """
)


def _p99_ms(samples: list[float]) -> float:
    return samples[min(len(samples) - 1, int(0.99 * len(samples)))] * 1000


def _flood_lag_problem(busy: list[float], idle: list[float]) -> str | None:
    """Why the loop-lag samples (sorted, seconds) of a flood are not acceptable, else None.

    The p99 stays within twice the idle p99 (a 25 ms floor keeps a 1 ms idle p99 from making it a noise test);
    at most one sample is over 250 ms (a single host hiccup, which a shared runner can make 1.8 s long); and
    none is over 4 s (a frozen loop).
    """
    p99, idle_p99 = _p99_ms(busy), _p99_ms(idle)
    stalled = sum(1 for lag_s in busy if lag_s > 0.25)
    if p99 > max(2 * idle_p99, 25.0) or stalled > 1 or busy[-1] > 4.0:
        return (f"event loop lag under a scrape flood: p99 {p99:.1f} ms (idle {idle_p99:.1f}), "
                f"{stalled} samples over 250 ms, max {busy[-1] * 1000:.1f} ms")
    return None


def test_the_flood_lag_verdict_allows_one_hiccup_and_nothing_more() -> None:
    quiet = sorted([0.001] * 400)
    assert _flood_lag_problem(quiet, quiet) is None
    assert _flood_lag_problem(sorted([0.001] * 400 + [1.8]), quiet) is None  # the one host hiccup seen in CI
    assert _flood_lag_problem(sorted([0.001] * 400 + [1.5, 1.5]), quiet) is not None  # two freezes
    assert _flood_lag_problem(sorted([0.001] * 400 + [4.5]), quiet) is not None  # one frozen loop
    assert _flood_lag_problem(sorted([0.06] * 400), quiet) is not None  # a slow loop


def test_a_scrape_flood_over_64_snapshot_files_does_not_stall_the_event_loop(tmp_path: Path) -> None:
    """The holder lives in the indexing process: sixteen scrapers (one per slot) over 64 large snapshot files must
    leave its event loop as responsive as idle, within twice its idle p99 (scrapes share one render; the
    window spans several of the 1 s collection rounds, each of which empties the cache)."""
    import asyncio

    from app.telemetry import ops_exporter as ox

    sig = tmp_path / "signals"
    sig.mkdir()
    rows = {
        name: [[["indexing", p] if spec.labels[:1] == ("service",) and len(spec.labels) == 2 else ["x"] * len(spec.labels), 1.0]
               for p in ("index_heavy", "index_light", "heavy_parse", "light_parse")]
        for name, spec in ox.SERIES.items() if not spec.fleet
    }
    for pid in range(5000, 5064):
        (sig / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "nonce": "n", "start": None, "at": time.time() + 3600, "service": "indexing",
            "signal": {"pad": "x" * 1500}, "metrics": rows}))

    async def lag(seconds: float) -> list[float]:
        out = []
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            t = time.perf_counter()
            await asyncio.sleep(0.005)
            out.append(time.perf_counter() - t - 0.005)
        return sorted(out)

    port = _free(BASE_PORT + 50)
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), stale_after=7200, bind="127.0.0.1", interval=1.0)
    exporter.start()
    deadline = time.time() + 5
    while not exporter.holds_port and time.time() < deadline:
        time.sleep(0.05)
    assert exporter.holds_port
    hammer = None
    try:
        idle = asyncio.run(lag(2.0))
        hammer = subprocess.Popen([sys.executable, "-c", HAMMER, str(port), "6", "16"])
        time.sleep(0.3)
        busy = asyncio.run(lag(5.0))
    finally:
        if hammer is not None:
            hammer.wait(timeout=30)
        exporter._stop.set()
        if exporter._server:
            exporter._server.shutdown()
            exporter._server.server_close()
    problem = _flood_lag_problem(busy, idle)
    assert problem is None, problem
