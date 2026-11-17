"""The exporter's counters, labels and port under hostile or unlucky conditions.

Covers: the container-wide OOM counter never falling when a holder dies, carried counters passing the
same label checks as live ones, no snapshot written after ``stop()``, the closed ``service`` label, no
double count without ``/proc``, the process-path refusal of fleet series, ``retire()`` against a scan,
and a scrape that stays answerable while idle or trickling clients sit on the port.
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import socket
import threading
import time
import urllib.request
from typing import TYPE_CHECKING

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.telemetry import ops_exporter as ox
from tests.support.free_port import bind_on_free_port, free_port

if TYPE_CHECKING:
    from pathlib import Path

OOM = 'pipeshub_cgroup_oom_kills_total{service="indexing"}'
BRAKES = "pipeshub_governor_brakes_total"


def _write(run: Path, pid: int, metrics: dict, *, at: float = 1000.0, start: str | None = "s", service: str = "indexing",
           nonce: str | None = None) -> Path:
    path = run / "signals" / f"{pid}-{start}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "nonce": nonce or f"n{pid}", "start": start, "at": at,
                                "service": service, "signal": {}, "metrics": metrics}))
    return path


def _text(collector: ox.SignalCollector) -> str:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(collector)  # type: ignore[arg-type]
    return generate_latest(registry).decode()


def _served(collector: ox.SignalCollector) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in _text(collector).splitlines():
        if line and not line.startswith("#"):
            key, value = line.rsplit(" ", 1)
            out[key] = float(value)
    return out


def _oom(value: float) -> dict:
    return {"pipeshub_cgroup_oom_kills_total": [[["indexing"], value]]}


# -- the OOM counter is the container's: it never falls --------------------------


def test_oom_counter_does_not_fall_when_the_process_that_read_the_newest_value_dies(tmp_path: Path) -> None:
    alive = {1, 2}
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda pid, start: pid in alive)
    _write(tmp_path, 1, _oom(3))
    _write(tmp_path, 2, _oom(2))
    seen = [_served(collector)[OOM]]
    alive.discard(1)  # the process holding 3 is OOM-killed; the other still reads the older 2
    seen.append(_served(collector)[OOM])
    _write(tmp_path, 2, _oom(4))
    seen.append(_served(collector)[OOM])
    assert seen == [3.0, 3.0, 4.0]  # one kill since the start of the series: increase() is 1


def test_oom_counter_keeps_its_mark_when_a_late_live_process_is_the_only_one_left(tmp_path: Path) -> None:
    alive = {1, 2}
    clock = [1000.0]
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: clock[0], is_alive=lambda pid, start: pid in alive)
    _write(tmp_path, 1, _oom(6), at=1000.0)
    _write(tmp_path, 2, _oom(5), at=1000.0)
    assert _served(collector)[OOM] == 6.0
    alive.discard(1)
    clock[0] = 1100.0  # the survivor is late (stale) and older
    assert _served(collector)[OOM] == 6.0


def test_oom_mark_survives_a_new_port_holder_and_a_dead_process_never_seen_live(tmp_path: Path) -> None:
    _write(tmp_path, 1, _oom(7))
    # process 1 dies before any scrape saw it live: its reading is folded into the mark, not lost
    first = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: False)
    assert _served(first)[OOM] == 7.0
    _write(tmp_path, 2, _oom(5))
    second = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: True)
    assert _served(second)[OOM] == 7.0


@pytest.mark.parametrize("seed", [0x8A2E1116, 914233, 0x5EED0B])
def test_generated_holders_never_make_the_oom_series_fall(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    cgroup = 0.0
    live: dict[int, str] = {}
    next_pid = [1]
    clock = [1000.0]
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: clock[0], is_alive=lambda pid, start: pid in live)
    served: list[float] = []
    for _ in range(300):
        clock[0] += rng.choice((1, 5, 5, 30))
        op = rng.random()
        if op < 0.25:
            cgroup += 1
        if op < 0.15 or not live:
            live[next_pid[0]] = "s"
            next_pid[0] += 1
        elif op < 0.30:
            live.pop(rng.choice(list(live)))  # killed: its last file stays
        elif op < 0.35:
            collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: clock[0], is_alive=lambda pid, start: pid in live)
        for pid in live:
            if rng.random() < 0.7:  # the others are late and keep an older reading
                _write(tmp_path, pid, _oom(cgroup), at=clock[0])
        got = _served(collector).get(OOM)
        if got is not None:
            served.append(got)
    assert served and all(b >= a for a, b in zip(served, served[1:], strict=False)), served
    assert served[-1] <= cgroup


# -- carried counters pass the same closed-label check as live ones -------------


def test_a_dead_process_cannot_smuggle_a_label_value_through_the_carry(tmp_path: Path) -> None:
    rows = {BRAKES: [[["indexing", "heavy_parse", "org-6f1c2a"], 3], [["indexing", "heavy_parse", "memory_soft"], 1]],
            "pipeshub_downstream_events_total": [[["indexing", "Acme Corp", "timeout"], 2]]}
    _write(tmp_path, 7, rows)
    alive = {7}
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda pid, start: pid in alive)
    live = _text(collector)
    assert "org-6f1c2a" not in live and "Acme Corp" not in live
    alive.clear()
    dead = _text(collector)
    assert "org-6f1c2a" not in dead and "Acme Corp" not in dead
    assert 'reason="memory_soft"' in dead  # the valid row is still carried
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert all("org-6f1c2a" not in key and "Acme Corp" not in key for key in carry["values"])


def test_a_carry_file_with_a_foreign_label_is_not_served(tmp_path: Path) -> None:
    (tmp_path / ox.CARRY_FILE).write_text(json.dumps({"folded": [], "hw": {}, "values": {
        json.dumps([BRAKES, "indexing", "heavy_parse", "org-6f1c2a"]): 4.0,
        json.dumps([BRAKES, "org-6f1c2a", "heavy_parse", "memory_soft"]): 4.0}}))
    assert "org-6f1c2a" not in _text(ox.SignalCollector(tmp_path, cache_ttl=0))


# -- stop() retires the file for good ----------------------------------------------


def test_a_slow_last_round_does_not_write_the_file_again_after_stop(tmp_path: Path) -> None:
    calls = [0]
    in_round = threading.Event()

    def source() -> tuple[dict, dict]:
        calls[0] += 1
        if calls[0] == 2:
            in_round.set()
            time.sleep(2.6)  # a slow stats() under load, longer than stop()'s join
        return {}, {BRAKES: [[["indexing", "heavy_parse", "memory_soft"], 5]]}

    exporter = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=free_port(), source=source, bind="127.0.0.1",
                              interval=0.05, rebind_interval=3600)
    exporter.start()
    assert in_round.wait(5)
    exporter.stop()
    time.sleep(1.5)  # the slow round ends now
    assert not exporter.writer.path.exists()
    line = [ln for ln in _text(ox.SignalCollector(tmp_path, cache_ttl=0)).splitlines() if ln.startswith(BRAKES + "{")]
    assert line and line[0].endswith(" 5.0"), line


def test_a_retired_writer_refuses_to_write(tmp_path: Path) -> None:
    writer = ox.SnapshotWriter(tmp_path, "indexing", lambda: ({}, {}))
    assert writer.write() and writer.path.exists()
    writer.retire()
    writer.remove()
    assert writer.write() is False and not writer.path.exists()


# -- the service label is a closed set, and the process's own ------------------------


def test_a_snapshot_service_name_shaped_like_an_org_id_never_becomes_a_label(tmp_path: Path) -> None:
    _write(tmp_path, 1, {"pipeshub_draining": [[["org-6f1c2a"], 1]]}, service="org-6f1c2a")
    text = _text(ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: True))
    assert "org-6f1c2a" not in text


def test_a_row_cannot_name_another_service_than_its_process(tmp_path: Path) -> None:
    _write(tmp_path, 1, {"pipeshub_draining": [[["docling"], 1]]}, service="indexing")
    text = _text(ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: True))
    assert "pipeshub_draining" not in text


def test_every_known_service_is_accepted_and_free_text_is_not() -> None:
    spec = ox.SERIES["pipeshub_draining"]
    for name in ("indexing", "docling", "embedding", "query", "parsing", "parse-worker", "connectors"):
        assert ox._label_ok(spec, "service", name), name
    for name in ("svc-1", "org-6f1c2a", "Acme Corp", "", "x" * 40):
        assert not ox._label_ok(spec, "service", name), name


# -- without /proc a late live writer is hidden, not folded ---------------------------


def test_without_proc_a_late_live_writer_is_not_counted_twice(tmp_path: Path) -> None:
    pid = os.getpid()  # a pid that exists
    clock = [1000.0]
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: clock[0])
    metrics = {BRAKES: [[["indexing", "heavy_parse", "memory_soft"], 40]]}
    key = BRAKES + '{pool="heavy_parse",reason="memory_soft",service="indexing"}'
    _write(tmp_path, pid, metrics, at=1000.0, start=None, nonce="n")
    seen = [_served(collector)[key]]
    clock[0] = 1100.0  # late: written 100 s ago, but the pid exists
    seen.append(_served(collector)[key])
    _write(tmp_path, pid, metrics, at=1100.0, start=None, nonce="n")  # it writes again
    seen.append(_served(collector)[key])
    assert seen == [40.0, 40.0, 40.0]


def test_without_proc_a_late_file_of_a_vanished_pid_is_folded_once(tmp_path: Path) -> None:
    clock = [1000.0]
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: clock[0])
    key = BRAKES + '{pool="heavy_parse",reason="memory_soft",service="indexing"}'
    _write(tmp_path, 4_000_000, {BRAKES: [[["indexing", "heavy_parse", "memory_soft"], 9]]}, at=1000.0, start=None)
    clock[0] = 1100.0
    assert _served(collector)[key] == 9.0
    assert _served(collector)[key] == 9.0


# -- fleet series from a process are refused (alone, not hidden by the budget) ----------


def test_a_fleet_series_in_a_process_snapshot_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, 1, {"pipeshub_queue_pending": [[["parse", "small"], 9]]})
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: True)
    assert "pipeshub_queue_pending" not in _text(collector)
    fleet = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: True,
                               fleet_source=lambda: [("pipeshub_queue_pending", ("parse", "small"), 2.0)])
    lines = [ln for ln in _text(fleet).splitlines() if ln.startswith("pipeshub_queue_pending")]
    assert len(lines) == 1 and lines[0].endswith(" 2.0")  # the fleet source's value; the process's 9 never shows


def test_a_fleet_series_from_a_dead_process_is_not_carried(tmp_path: Path) -> None:
    _write(tmp_path, 1, {"pipeshub_queue_pending": [[["parse", "small"], 9]]})
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda *_: False)
    assert "pipeshub_queue_pending" not in _text(collector)


# -- retire() against a scan: a process is folded once -------------------------------------


def test_retire_racing_a_scan_loses_no_fold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = ox.SignalCollector._load_carry

    def slow_load(self: ox.SignalCollector) -> dict:
        carry = real(self)
        time.sleep(0.15)  # widen the window between reading the carry and writing it back
        return carry

    monkeypatch.setattr(ox.SignalCollector, "_load_carry", slow_load)
    key = json.dumps([BRAKES, "indexing", "heavy_parse", "memory_soft"], separators=(",", ":"))
    path = _write(tmp_path, 7, {BRAKES: [[["indexing", "heavy_parse", "memory_soft"], 5]]})
    _write(tmp_path, 8, {BRAKES: [[["indexing", "heavy_parse", "memory_soft"], 3]]}, nonce="other")  # a scan folds this one
    alive = {7}
    collector = ox.SignalCollector(tmp_path, cache_ttl=0, clock=lambda: 1000.0, is_alive=lambda pid, start: pid in alive)
    retiring = threading.Thread(target=collector.retire, args=(path,))
    scanning = threading.Thread(target=collector.scan)
    retiring.start()
    time.sleep(0.05)  # retire holds the carry; the scan arrives while it works
    scanning.start()
    retiring.join(10)
    scanning.join(10)
    monkeypatch.undo()
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert carry["values"][key] == 8.0, "a fold was lost or doubled (retire and a scan at once)"


# -- the port: a scrape is always answerable -------------------------------------------------


def _scrape_ok(port: int) -> bool:
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).status == 200
    except Exception:
        return False


def _exporter(tmp_path: Path) -> ox.OpsExporter:
    return bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="127.0.0.1"))


def _close(exporter: ox.OpsExporter) -> None:
    assert exporter._server is not None
    exporter._server.shutdown()
    exporter._server.server_close()


def test_reconnecting_idle_clients_do_not_keep_a_scrape_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 1.0)
    exporter = _exporter(tmp_path)
    port = exporter._port
    stop = threading.Event()

    def attacker() -> None:
        while not stop.is_set():
            socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(ox.MAX_CONNECTIONS + 4)]
            stop.wait(0.8)  # re-opened just under the deadline
            for s in socks:
                s.close()

    t = threading.Thread(target=attacker, daemon=True)
    t.start()
    time.sleep(0.3)
    try:
        answered = 0
        for _ in range(8):
            answered += _scrape_ok(port)
            time.sleep(0.3)
    finally:
        stop.set()
        t.join(5)
        _close(exporter)
    assert answered == 8


def test_trickling_clients_are_cut_at_the_request_deadline_and_never_block_a_scrape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.8)
    exporter = _exporter(tmp_path)
    port = exporter._port
    socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(ox.MAX_CONNECTIONS + 4)]
    stop = threading.Event()

    def trickle() -> None:
        msg = b"GET /metrics HTTP/1.1"  # never ends the request line; each byte restarts a per-recv timer
        for i in range(len(msg)):
            if stop.is_set():
                return
            for s in socks:
                with_suppress_send(s, msg[i : i + 1])
            stop.wait(0.3)

    def with_suppress_send(s: socket.socket, data: bytes) -> None:
        with contextlib.suppress(OSError):
            s.send(data)

    t = threading.Thread(target=trickle, daemon=True)
    t.start()
    try:
        assert _scrape_ok(port)  # right away, while every slot is held
        # past the deadline the trickling sockets are closed by the server: poll, bounded, not one fixed sleep
        open_socks = list(socks)
        end = time.monotonic() + 20
        while open_socks and time.monotonic() < end:
            for s in list(open_socks):
                s.settimeout(0.2)
                try:
                    if s.recv(1) == b"":
                        open_socks.remove(s)
                except TimeoutError:
                    pass  # still open: the server has not cut it yet
                except OSError:
                    open_socks.remove(s)
        closed = len(socks) - len(open_socks)
        assert closed == len(socks)
        assert _scrape_ok(port)
    finally:
        stop.set()
        t.join(5)
        for s in socks:
            s.close()
        _close(exporter)


def test_a_connection_that_is_being_answered_is_not_evicted(tmp_path: Path) -> None:
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    try:
        a, b = socket.socketpair()
        server._conns[a] = [time.monotonic() - 100, True]  # answering for a long time
        hold = threading.Event()
        handler = threading.Thread(target=hold.wait, daemon=True)  # its handler thread, still alive
        handler.start()
        server._live.add(handler)
        server._max = 1
        c, d = socket.socketpair()
        server.process_request(c, ("x", 0))  # nothing evictable: the newcomer waits its turn, then gets a 503
        d.settimeout(ox.ADMIT_WAIT_S + 5)
        assert d.recv(100).startswith(b"HTTP/1.1 503")  # a short reply, then the close
        assert a in server._conns and server._conns[a][1] is True  # the answer went on, uncut
        end = time.monotonic() + 5
        while c.fileno() != -1 and time.monotonic() < end:  # the 503 is sent first, the close follows it
            time.sleep(0.005)
        assert c.fileno() == -1
        a.close()
        b.close()
        d.close()
        hold.set()
    finally:
        server._conns.clear()
        _close(exporter)
