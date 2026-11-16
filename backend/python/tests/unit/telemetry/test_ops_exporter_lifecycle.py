"""The ops exporter's persistence and shutdown edges: a failed carry write keeps the counters, a reading from
another memory cgroup does not seed this one's mark, IPv6 bind addresses, a close that cuts active clients,
and a stop that cannot be outrun by a bind.

Needs no service; every port is asked of the kernel (`tests/support/free_port.py`).
"""
from __future__ import annotations

import errno
import json
import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.telemetry import ops_exporter as ox
from tests.support.free_port import bind_on_free_port, free_port

if TYPE_CHECKING:
    from pathlib import Path

HOST = "127.0.0.1"
OOM = 'pipeshub_cgroup_oom_kills_total{service="indexing"}'
BRAKES = 'pipeshub_governor_brakes_total{pool="heavy_parse",reason="memory_soft",service="indexing"}'


def _wait_until(predicate, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _snapshot(run: Path, pid: int, *, oom: float = 0.0, brakes: float = 0.0, **extra: object) -> Path:
    path = run / "signals" / f"{pid}-s.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics: dict = {"pipeshub_cgroup_oom_kills_total": [[["indexing"], oom]]}
    if brakes:
        metrics["pipeshub_governor_brakes_total"] = [[["indexing", "heavy_parse", "memory_soft"], brakes]]
    path.write_text(json.dumps({"pid": pid, "nonce": f"n{pid}", "start": "s", "at": 1000.0, "service": "indexing",
                                "signal": {}, "metrics": metrics, **extra}))
    return path


def _served(collector: ox.SignalCollector) -> dict[str, float]:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(collector)  # type: ignore[arg-type]
    out: dict[str, float] = {}
    for line in generate_latest(registry).decode().splitlines():
        if line and not line.startswith("#"):
            key, value = line.rsplit(" ", 1)
            out[key] = float(value)
    return out


def _collector(run: Path, alive: set[int], domain: str = "cg-1") -> ox.SignalCollector:
    return ox.SignalCollector(run, clock=lambda: 1000.0, cache_ttl=0, is_alive=lambda p, _s: p in alive, domain=lambda: domain)


def _disk_full(monkeypatch: pytest.MonkeyPatch) -> None:
    def full(*_a: object, **_k: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ox, "_write_json", full)


# -- a failed carry write keeps the counters -----------------------------------


def test_a_vanished_process_whose_fold_fails_keeps_its_file_and_its_counters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alive = {1}
    collector = _collector(tmp_path, alive)
    path = _snapshot(tmp_path, 1, brakes=4)
    assert _served(collector)[BRAKES] == 4.0
    alive.clear()
    with monkeypatch.context() as m:
        _disk_full(m)
        assert _served(collector)[BRAKES] == 4.0  # the carry cannot be written: the series does not dip
        assert path.exists()
    assert _served(collector)[BRAKES] == 4.0  # a later scan folds it
    assert not path.exists()
    assert _served(collector)[BRAKES] == 4.0  # exactly once


def test_a_clean_exit_whose_fold_fails_keeps_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collector = _collector(tmp_path, set())
    path = _snapshot(tmp_path, 1, brakes=4)
    with monkeypatch.context() as m:
        _disk_full(m)
        collector.retire(path)
    assert path.exists()  # the counters are in this file until the carry has them
    assert _served(collector)[BRAKES] == 4.0  # the next scan folds the vanished process


# -- the memory domain of a snapshot -----------------------------------------


def test_an_old_cgroups_snapshot_does_not_seed_the_new_cgroups_mark_but_its_counters_carry(tmp_path: Path) -> None:
    _snapshot(tmp_path, 1, oom=12, brakes=3, domain="cg-old")  # left behind by an unclean restart
    collector = _collector(tmp_path, set(), domain="cg-new")
    served = _served(collector)  # the old process is gone: folded
    assert served.get(OOM, 0.0) == 0.0
    assert served[BRAKES] == 3.0  # the summed counters keep their carry behaviour
    _snapshot(tmp_path, 9, oom=1, domain="cg-new")
    assert _served(_collector(tmp_path, {9}, domain="cg-new"))[OOM] == 1.0  # the new cgroup's first kill is visible


def test_a_snapshot_of_the_same_cgroup_still_seeds_the_mark(tmp_path: Path) -> None:
    _snapshot(tmp_path, 1, oom=12, domain="cg-1")
    assert _served(_collector(tmp_path, set(), domain="cg-1"))[OOM] == 12.0


def test_the_writer_records_the_memory_domain(tmp_path: Path) -> None:
    writer = ox.SnapshotWriter(tmp_path, "indexing", lambda: ({}, {}), domain=lambda: "cg-7")
    assert writer.write()
    assert json.loads(writer.path.read_text())["domain"] == "cg-7"


# -- IPv6 bind addresses -------------------------------------------------------


def test_the_socket_family_follows_the_bind_address() -> None:
    assert ox.family_for("::1") == socket.AF_INET6
    assert ox.family_for("::") == socket.AF_INET6
    assert ox.family_for("127.0.0.1") == socket.AF_INET
    assert ox.family_for("0.0.0.0") == socket.AF_INET


def _ipv6_loopback() -> bool:
    try:
        with socket.socket(socket.AF_INET6) as s:
            s.bind(("::1", 0))
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_loopback(), reason="this host has no IPv6 loopback")
def test_an_ipv6_bind_address_is_served(tmp_path: Path) -> None:
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="::1"))
    port = exporter._port
    try:
        with socket.create_connection(("::1", port), timeout=5) as c:
            c.sendall(b"GET /metrics HTTP/1.0\r\n\r\n")
            assert c.recv(32).startswith(b"HTTP/1.0 200")
    finally:
        exporter.stop()


# -- a close cuts active clients ----------------------------------------------


def test_closing_the_server_ends_a_client_that_trickles(tmp_path: Path) -> None:
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST))
    port = exporter._port
    server = exporter._server
    assert server is not None
    c = socket.create_connection((HOST, port))
    try:
        c.send(b"G")
        assert _wait_until(lambda: len(server._conns) == 1, 5)
        server._closing.set()  # the reaper is stopped first, as server_close() does
        server.shutdown()
        server.server_close()
        assert ox._Handler.timeout > 2  # the read timeout is longer than the wait below
        c.settimeout(2)
        assert c.recv(1) == b""  # the handler left at once, without waiting for a read timeout
    finally:
        c.close()


# -- stop cannot be outrun by a bind ------------------------------------------


def test_a_stopped_exporter_never_binds(tmp_path: Path) -> None:
    port = free_port()
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST)
    exporter.stop()
    assert exporter.try_bind() is False
    assert not exporter.holds_port


def test_a_bind_in_progress_when_stop_runs_is_closed_before_stop_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = free_port()
    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST)
    real = ox.BoundedServer
    inside, release = threading.Event(), threading.Event()

    def slow(*args: object, **kwargs: object) -> ox.BoundedServer:
        inside.set()
        assert release.wait(10)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ox, "BoundedServer", slow)
    binder = threading.Thread(target=exporter.try_bind)
    binder.start()
    assert inside.wait(5)
    stopper = threading.Thread(target=exporter.stop)
    stopper.start()
    time.sleep(0.1)  # stop is waiting for the bind: it must not return with the server still to come
    release.set()
    binder.join(10)
    stopper.join(10)
    assert not stopper.is_alive()
    assert exporter._server is None
    assert _wait_until(lambda: socket.socket().connect_ex((HOST, port)) != 0, 5), "the port is still served after stop()"


# -- an unknown memory domain ---------------------------------------------------


def test_a_fold_works_when_the_memory_domain_is_unknown(tmp_path: Path) -> None:
    """No readable cgroup (domain None): the carry still has its domain key, so a vanished process folds."""
    path = _snapshot(tmp_path, 1, brakes=4, domain=None)
    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, cache_ttl=0, is_alive=lambda *_: False, domain=lambda: None)
    assert _served(collector)[BRAKES] == 4.0
    assert not path.exists()  # folded, not left behind
    assert json.loads((tmp_path / ox.CARRY_FILE).read_text())["values"]
    assert _served(collector)[BRAKES] == 4.0


# -- stop never calls the source beside a round still inside it -----------------


def test_stop_does_not_call_the_source_while_a_round_is_still_in_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "STOP_JOIN_S", 0.1)
    inside, release = threading.Event(), threading.Event()
    running = [0]
    peak = [0]
    lock = threading.Lock()

    def source() -> tuple[dict, dict]:
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        inside.set()
        release.wait(10)
        with lock:
            running[0] -= 1
        return {}, {}

    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=free_port(), source=source, bind=HOST, interval=0.01)
    exporter.start()
    assert inside.wait(5)
    try:
        exporter.stop()  # the round is stuck in the source: stop leaves it alone
        assert peak[0] == 1, "the source was called concurrently"
    finally:
        release.set()


def test_a_write_between_the_scan_read_and_the_exit_check_is_folded(tmp_path: Path) -> None:
    """The process replaces its snapshot (7 to 8) after the scan read it and exits before the exit check:
    the 8 is folded, not deleted with the file."""
    path = _snapshot(tmp_path, 1, brakes=7)

    def dies_after_one_more_write(_pid: int, _start: str | None) -> bool:
        _snapshot(tmp_path, 1, brakes=8)  # same pid and nonce: its last write
        return False

    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, cache_ttl=0, is_alive=dies_after_one_more_write, domain=lambda: "cg-1")
    assert _served(collector)[BRAKES] == 8.0
    assert not path.exists()
    assert _served(collector)[BRAKES] == 8.0
