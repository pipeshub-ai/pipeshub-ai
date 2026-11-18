"""The ops exporter's port: quiet rebind, single-flight scrapes, a non-blocking acceptor, and the
bounds each guard is there for (one test per guard, each failing when its guard is removed).

Needs no service; every port is asked of the kernel (`tests/support/free_port.py`).
"""
from __future__ import annotations

import contextlib
import http.client
import json
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.telemetry import ops_exporter as ox
from tests.support.free_port import bind_on_free_port

if TYPE_CHECKING:
    from pathlib import Path

HOST = "127.0.0.1"
PER_SCRAPER = 3  # requests each of the 32 flood scrapers makes; the whole flood must fit the 30 s hang guard on a busy host


def _exporter(tmp_path: Path, **kw) -> ox.OpsExporter:
    return bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST, **kw))


def _close(exporter: ox.OpsExporter) -> None:
    server, exporter._server = exporter._server, None
    if server is not None:
        server.shutdown()
        server.server_close()


def _handler_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.name == "ops-exporter-conn")


def _wait_until(predicate, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _get(port: int, timeout: float = 15.0) -> int:
    conn = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        conn.request("GET", "/metrics")
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


# -- the rebind of a process that does not hold the port ----------------------


def test_a_non_holders_bind_retry_fails_quietly_three_times(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    holder = _exporter(tmp_path)
    port = holder._port
    other = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST)
    try:
        with caplog.at_level(logging.DEBUG, logger="app.telemetry.ops_exporter"):
            results = [other.try_bind() for _ in range(3)]
        assert results == [False, False, False]
        loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert not loud, loud
    finally:
        _close(holder)


def test_the_loop_of_a_non_holder_logs_nothing_above_debug(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    holder = _exporter(tmp_path)
    port = holder._port
    other = ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST,
                           interval=0.02, rebind_interval=0.02)
    tries = []
    real = other.try_bind

    def counted() -> bool:
        tries.append(1)
        return real()

    other.try_bind = counted  # type: ignore[method-assign]
    try:
        with caplog.at_level(logging.DEBUG, logger="app.telemetry.ops_exporter"):
            other.start()
            assert _wait_until(lambda: len(tries) >= 3, 10), "the loop did not retry the bind"
        other.stop()
        loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert not loud, loud
    finally:
        other.stop()  # its loop must not outlive the test and bind the released port
        _close(holder)


# -- single-flight scrapes ----------------------------------------------------


def test_concurrent_renders_share_one_and_the_next_starts_after_it() -> None:
    flight = ox.SingleFlight()
    gate = threading.Event()
    calls: list[int] = []

    def render() -> bytes:
        calls.append(1)
        gate.wait(10)
        return b"body-%d" % len(calls)

    out: list[bytes] = []
    threads = [threading.Thread(target=lambda: out.append(flight.run(render))) for _ in range(16)]
    for t in threads:
        t.start()
    assert _wait_until(lambda: flight.waiting == 15 and len(calls) == 1, 10)
    gate.set()
    for t in threads:
        t.join(10)
    assert calls == [1] and out == [b"body-1"] * 16  # one render served sixteen scrapes
    assert flight.run(render) == b"body-2"  # no reuse beyond the flight that finished
    assert len(calls) == 2


def test_a_failed_render_fails_its_followers_and_does_not_poison_the_next() -> None:
    flight = ox.SingleFlight()
    gate = threading.Event()
    errors: list[BaseException] = []

    def bad() -> bytes:
        gate.wait(10)
        raise RuntimeError("boom")

    def run() -> None:
        try:
            flight.run(bad)
        except RuntimeError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    assert _wait_until(lambda: flight.waiting == 3, 10)
    gate.set()
    for t in threads:
        t.join(10)
    assert len(errors) == 4
    assert flight.run(lambda: b"ok") == b"ok"


def test_sixteen_scrapes_over_the_port_make_one_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = _exporter(tmp_path)
    port = exporter._port
    server = exporter._server
    assert server is not None
    gate = threading.Event()
    calls: list[int] = []

    def slow_render(registry: CollectorRegistry) -> bytes:
        calls.append(1)
        gate.wait(10)
        return generate_latest(registry)

    monkeypatch.setattr(ox, "generate_latest", slow_render)
    statuses: list[int] = []
    threads = [threading.Thread(target=lambda: statuses.append(_get(port))) for _ in range(ox.MAX_CONNECTIONS)]
    try:
        for t in threads:
            t.start()
        assert _wait_until(lambda: server._flight.waiting == ox.MAX_CONNECTIONS - 1, 10), "the scrapes did not meet in one flight"
        gate.set()
        for t in threads:
            t.join(15)
        assert statuses == [200] * ox.MAX_CONNECTIONS
        assert calls == [1]
    finally:
        gate.set()
        _close(exporter)


# -- the high-water mark belongs to its memory domain -------------------------

OOM = 'pipeshub_cgroup_oom_kills_total{service="indexing"}'


def _write_snapshot(run: Path, pid: int, start: str, oom: float) -> None:
    path = run / "signals" / f"{pid}-{start}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "nonce": f"n{pid}", "start": start, "at": 1000.0, "service": "indexing", "signal": {},
                                "metrics": {"pipeshub_cgroup_oom_kills_total": [[["indexing"], oom]]}}))


def _served(collector: ox.SignalCollector) -> dict[str, float]:
    registry = CollectorRegistry(auto_describe=False)
    registry.register(collector)  # type: ignore[arg-type]
    out: dict[str, float] = {}
    for line in generate_latest(registry).decode().splitlines():
        if line and not line.startswith("#"):
            key, value = line.rsplit(" ", 1)
            out[key] = float(value)
    return out


def test_a_new_cgroup_after_a_restart_counts_its_own_oom_kills(tmp_path: Path) -> None:
    alive = {(1, "a")}
    domain = ["cgroup-old"]
    collector = ox.SignalCollector(tmp_path, clock=lambda: 1000.0, cache_ttl=0, is_alive=lambda p, s: (p, s) in alive,
                                   domain=lambda: domain[0])
    _write_snapshot(tmp_path, 1, "a", 3)  # the old container: 3 kills, then it is restarted with its run dir kept
    alive.clear()
    assert _served(collector)[OOM] == 3.0
    domain[0] = "cgroup-new"
    alive.add((9, "z"))
    seen = []
    for value in (0, 1, 2):  # two real kills in the new cgroup
        _write_snapshot(tmp_path, 9, "z", value)
        seen.append(_served(collector)[OOM])
    assert seen == [0.0, 1.0, 2.0]
    assert sum((b - a) if b >= a else b for a, b in zip(seen, seen[1:], strict=False)) == 2  # increase() sees both


def test_the_mark_of_the_same_cgroup_survives_a_holder_change(tmp_path: Path) -> None:
    alive = {(1, "a")}
    make = lambda: ox.SignalCollector(tmp_path, clock=lambda: 1000.0, cache_ttl=0, is_alive=lambda p, s: (p, s) in alive,  # noqa: E731
                                      domain=lambda: "cgroup-1")
    _write_snapshot(tmp_path, 1, "a", 3)
    first = make()
    alive.clear()
    assert _served(first)[OOM] == 3.0
    assert _served(make())[OOM] == 3.0  # a new holder, the same cgroup: the mark stays
    carry = json.loads((tmp_path / ox.CARRY_FILE).read_text())
    assert carry["domain"] == "cgroup-1"


# -- each bound has a test that fails without it ------------------------------


def test_the_shipped_connection_cap_is_sixteen() -> None:
    assert ox.MAX_CONNECTIONS == 16
    assert ox.ADMIT_QUEUE_MAX >= ox.MAX_CONNECTIONS


def test_live_handler_threads_never_exceed_the_cap_while_handlers_linger_after_their_socket_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slot is a live thread, not a tracked socket: here every handler drops its socket from the books and
    then lingers, as a thread tearing down does."""
    real = ox.BoundedServer.shutdown_request

    def lingering(self: ox.BoundedServer, request: socket.socket) -> None:
        real(self, request)
        time.sleep(0.15)

    monkeypatch.setattr(ox.BoundedServer, "shutdown_request", lingering)
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.3)
    exporter = _exporter(tmp_path)
    port = exporter._port
    socks = [socket.create_connection((HOST, port)) for _ in range(64)]
    try:
        peak = 0
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            peak = max(peak, _handler_threads())
        assert peak <= ox.MAX_CONNECTIONS, f"{peak} live handler threads"
        assert peak > 0
    finally:
        for s in socks:
            s.close()
        _close(exporter)


def test_a_trickling_client_is_cut_at_the_request_deadline_not_at_the_read_timeout(tmp_path: Path) -> None:
    """The shipped deadline, with free slots (nothing to evict): only the reaper can cut a client whose every
    byte restarts the per-read timer."""
    assert ox.REQUEST_DEADLINE_S + 0.5 < ox._Handler.timeout
    exporter = _exporter(tmp_path)
    port = exporter._port
    try:
        with socket.create_connection((HOST, port)) as s:
            started = time.monotonic()
            cut_after = None
            while time.monotonic() - started < ox._Handler.timeout + 2:
                try:
                    s.send(b"G")
                    s.settimeout(0.1)
                    if s.recv(1) == b"":
                        cut_after = time.monotonic() - started
                        break
                except TimeoutError:
                    pass
                except OSError:
                    cut_after = time.monotonic() - started
                    break
                time.sleep(0.1)
        assert cut_after is not None, "the trickling client was never cut"
        assert cut_after <= ox.REQUEST_DEADLINE_S + 0.5, cut_after
    finally:
        _close(exporter)


def test_a_scrape_is_answered_when_every_slot_is_held_by_idle_clients_and_the_deadline_is_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eviction, not the reaper: with the deadline far away, only cutting the oldest incomplete request
    frees a slot inside the admit wait."""
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 60.0)
    exporter = _exporter(tmp_path)
    port = exporter._port
    server = exporter._server
    assert server is not None
    idle = [socket.create_connection((HOST, port)) for _ in range(ox.MAX_CONNECTIONS)]
    try:
        assert _wait_until(lambda: len(server._live) == ox.MAX_CONNECTIONS, 5)
        started = time.monotonic()
        assert _get(port) == 200
        assert time.monotonic() - started < ox.ADMIT_WAIT_S + 2
    finally:
        for s in idle:
            s.close()
        _close(exporter)


# -- the acceptor never blocks ------------------------------------------------


def test_the_acceptor_never_waits_for_a_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With every slot answering, process_request returns at once; the newcomer is queued, then gets a 503."""
    monkeypatch.setattr(ox, "ADMIT_WAIT_S", 0.5)
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    hold = threading.Event()
    try:
        for _ in range(ox.MAX_CONNECTIONS):  # sixteen slots, all mid-answer and never evictable
            a, b = socket.socketpair()
            server._conns[a] = [time.monotonic(), True]
            t = threading.Thread(target=hold.wait, daemon=True)
            t.start()
            server._live.add(t)
            del b
        c, d = socket.socketpair()
        started = time.monotonic()
        server.process_request(c, ("x", 0))
        assert time.monotonic() - started < 0.1, "the acceptor waited"
        assert len(server._queue) == 1
        d.settimeout(5)
        assert d.recv(100).startswith(b"HTTP/1.1 503")
        d.close()
    finally:
        hold.set()
        server._conns.clear()
        _close(exporter)


def test_a_full_admission_queue_refuses_at_once(tmp_path: Path) -> None:
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    hold = threading.Event()
    try:
        for _ in range(ox.MAX_CONNECTIONS):
            t = threading.Thread(target=hold.wait, daemon=True)
            t.start()
            server._live.add(t)
        server._closing.set()  # park the admitter so that the queue only fills
        with server._wake:
            server._wake.notify_all()
        assert _wait_until(lambda: not server._admitter.is_alive(), 5)
        queued = []
        for _ in range(ox.ADMIT_QUEUE_MAX):
            c, d = socket.socketpair()
            queued.append(d)
            server.process_request(c, ("x", 0))
        assert len(server._queue) == ox.ADMIT_QUEUE_MAX
        c, d = socket.socketpair()
        started = time.monotonic()
        server.process_request(c, ("x", 0))
        assert time.monotonic() - started < 0.1
        d.settimeout(2)
        assert d.recv(100).startswith(b"HTTP/1.1 503")
        assert len(server._queue) == ox.ADMIT_QUEUE_MAX
    finally:
        hold.set()
        _close(exporter)


def test_a_flood_of_well_formed_scrapers_under_gil_load_is_answered_or_cleanly_refused(tmp_path: Path) -> None:
    """32 scrapers, 4 threads burning the GIL: every reply is a 200 or a 503, no connect is refused or reset,
    and live handler threads stay within the cap at every sample."""
    exporter = _exporter(tmp_path)
    port = exporter._port
    stop = threading.Event()
    results: list[int | str] = []
    peak = [0]

    def burn() -> None:
        while not stop.is_set():
            sum(i * i for i in range(3000))

    def scraper() -> None:
        for _ in range(PER_SCRAPER):
            try:
                results.append(_get(port, timeout=20))
            except OSError as exc:  # a refused or reset connection, a timeout
                results.append(type(exc).__name__)

    def sample() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], _handler_threads())
            time.sleep(0.0005)

    burners = [threading.Thread(target=burn, daemon=True) for _ in range(4)]
    sampler = threading.Thread(target=sample, daemon=True)
    scrapers = [threading.Thread(target=scraper) for _ in range(32)]
    try:
        for t in [sampler, *burners, *scrapers]:
            t.start()
        for t in scrapers:
            t.join(120)
        stop.set()
        sampler.join(5)
        assert len(results) == 32 * PER_SCRAPER
        bad = [r for r in results if r not in (200, 503)]
        assert not bad, f"{len(bad)} of {len(results)} scrapes neither answered nor cleanly refused: {sorted(set(map(str, bad)))}"
        assert results.count(200) > 0
        assert peak[0] <= ox.MAX_CONNECTIONS, f"{peak[0]} live handler threads"
    finally:
        stop.set()
        _close(exporter)


# -- the accept queue is as deep as the cap, and a client that left takes nothing ---


def test_the_listen_backlog_follows_the_handler_cap(tmp_path: Path) -> None:
    assert ox.BoundedServer.request_queue_size == ox.MAX_CONNECTIONS
    server = ox.BoundedServer((HOST, 0), ox._Handler, max_connections=5)
    try:
        assert server.request_queue_size == 5
    finally:
        server.server_close()


def test_a_connection_whose_client_already_left_takes_no_slot_and_no_queue_place(tmp_path: Path) -> None:
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    try:
        c, d = socket.socketpair()
        d.close()  # the client gave up before it was accepted
        server.process_request(c, ("x", 0))
        assert c.fileno() == -1  # closed at once
        assert not server._queue and not server._conns and not server._live
    finally:
        _close(exporter)


def test_a_queued_connection_whose_client_left_while_it_waited_never_takes_the_freed_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ox, "ADMIT_WAIT_S", 30.0)
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    hold = threading.Event()
    try:
        held = []
        for _ in range(ox.MAX_CONNECTIONS):
            t = threading.Thread(target=hold.wait, daemon=True)
            t.start()
            server._live.add(t)
            held.append(t)
        c, d = socket.socketpair()
        server.process_request(c, ("x", 0))
        assert len(server._queue) == 1
        d.close()  # it gives up while queued
        took = []
        real_take = server._take_slot_locked
        server._take_slot_locked = lambda sock, addr: took.append(sock) or real_take(sock, addr)  # type: ignore[method-assign]
        hold.set()  # every slot frees
        assert _wait_until(lambda: not server._queue and c.fileno() == -1, 5)  # off the queue, then closed
        assert took == [], "a connection that had left was given a slot"
    finally:
        hold.set()
        _close(exporter)


# -- a request that has arrived is never cut; the peer check never waits --------


def test_a_scrape_whose_whole_request_is_buffered_is_not_evicted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two slots; the scrape's handler is starved before it parses the request it already has; an idle client
    takes the second slot; a third connection queues and evicts. The scrape must still be answered."""
    started = threading.Event()
    original = ox._Handler.parse_request

    def slow_parse(self: ox._Handler) -> bool:
        started.set()
        time.sleep(0.6)
        return original(self)

    monkeypatch.setattr(ox._Handler, "parse_request", slow_parse)
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind=HOST))
    server = exporter._server
    assert server is not None
    server._max = 2
    port = exporter._port
    socks = []
    try:
        scrape = socket.create_connection((HOST, port), timeout=10)
        scrape.sendall(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")  # the whole request, at once
        assert started.wait(5)
        socks.append(socket.create_connection((HOST, port)))  # an idle client: the second slot
        assert _wait_until(lambda: len(server._live) == 2, 5)
        socks.append(socket.create_connection((HOST, port)))  # queues, and evicts the oldest unanswered connection
        data = b""
        while chunk := scrape.recv(65536):
            data += chunk
        assert data.startswith(b"HTTP/1.0 200"), f"the scrape got {data[:40]!r} (evicted with its request in the buffer)"
    finally:
        for s in socks:
            s.close()
        _close(exporter)


def test_the_peer_check_never_waits_even_with_a_default_socket_timeout() -> None:
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2.0)
    try:
        a, b = socket.socketpair()  # inherits the default timeout, as an accepted socket does
        started = time.monotonic()
        assert ox.BoundedServer._peer_gone(a) is False  # open and silent
        b.send(b"x")
        assert ox.BoundedServer._peer_gone(a) is False  # open with data
        assert time.monotonic() - started < 0.5, "the peer check waited for the default timeout"
        a.recv(1)
        b.close()
        assert ox.BoundedServer._peer_gone(a) is True
        a.close()
    finally:
        socket.setdefaulttimeout(old)


def test_a_scrape_whose_request_arrived_is_answered_even_when_its_handler_runs_past_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reaper cuts what has not sent a complete request by the deadline; a handler starved past it, with the
    whole request already received, still answers."""
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.2)
    started = threading.Event()
    original = ox._Handler.parse_request

    def slow_parse(self: ox._Handler) -> bool:
        started.set()
        time.sleep(0.8)  # past the deadline and a few reaper ticks
        return original(self)

    monkeypatch.setattr(ox._Handler, "parse_request", slow_parse)
    exporter = _exporter(tmp_path)
    try:
        with socket.create_connection((HOST, exporter._port), timeout=10) as scrape:
            scrape.sendall(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")
            assert started.wait(5)
            data = b""
            while chunk := scrape.recv(65536):
                data += chunk
        assert data.startswith(b"HTTP/1.0 200"), f"the scrape got {data[:40]!r}"
    finally:
        _close(exporter)


def test_an_evicted_client_that_had_sent_nothing_is_told_503_not_just_closed_on(tmp_path: Path) -> None:
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    server._max = 2
    socks = []
    try:
        silent = socket.create_connection((HOST, exporter._port), timeout=10)
        socks.append(silent)
        socks.append(socket.create_connection((HOST, exporter._port)))
        assert _wait_until(lambda: len(server._live) == 2, 5)
        socks.append(socket.create_connection((HOST, exporter._port)))  # queues; the oldest silent client is evicted
        assert silent.recv(100).startswith(b"HTTP/1.1 503"), "an evicted silent client was closed on without an answer"
    finally:
        for s in socks:
            s.close()
        _close(exporter)


def test_a_client_that_sent_nothing_by_the_deadline_is_told_503_before_it_is_closed_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.3)
    exporter = _exporter(tmp_path)
    try:
        with socket.create_connection((HOST, exporter._port), timeout=10) as silent:
            assert silent.recv(100).startswith(b"HTTP/1.1 503")
    finally:
        _close(exporter)


@pytest.mark.parametrize("payload", [b"GET /metrics HTTP/1.1\r\n", b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n"], ids=["request-line", "whole-request"])
def test_a_refusal_is_never_followed_by_an_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    """The client's bytes are in the socket but its handler has not read them yet (starved). Evicting it must not
    send a 503 and then let the handler answer: one response, the answer."""
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 60.0)
    entered = threading.Event()
    original = ox._Handler.handle

    def late_handle(self: ox._Handler) -> None:
        entered.set()
        time.sleep(0.5)  # the handler thread is starved before it reads what is already in the socket
        original(self)

    monkeypatch.setattr(ox._Handler, "handle", late_handle)
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    server._max = 2
    socks = []
    try:
        waiting = socket.create_connection((HOST, exporter._port), timeout=10)
        socks.append(waiting)
        waiting.sendall(payload)  # in the socket, not yet read by the starved handler
        assert entered.wait(5)
        socks.append(socket.create_connection((HOST, exporter._port)))
        assert _wait_until(lambda: len(server._live) == 2, 5)
        socks.append(socket.create_connection((HOST, exporter._port)))  # queues and evicts the oldest: this one
        data = b""
        while chunk := waiting.recv(65536):
            data += chunk
        assert data.count(b"HTTP/1.") == 1, f"two responses on one connection: {data[:200]!r}"
        assert data.startswith(b"HTTP/1.0 200"), data[:60]
    finally:
        for s in socks:
            s.close()
        _close(exporter)


def test_a_silent_client_is_told_503_exactly_once_by_the_reaper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.2)
    sent = []
    real = ox.BoundedServer._send_refusal
    monkeypatch.setattr(ox.BoundedServer, "_send_refusal", staticmethod(lambda sock: sent.append(1) or real(sock)))
    handle = ox._Handler.handle
    monkeypatch.setattr(ox._Handler, "handle", lambda self: (time.sleep(0.8), handle(self))[1])  # slow to leave: several reaper ticks pass
    exporter = _exporter(tmp_path)
    try:
        with socket.create_connection((HOST, exporter._port), timeout=10) as silent:
            data = b""
            while chunk := silent.recv(65536):
                data += chunk
        assert data.count(b"HTTP/1.1 503") == 1 and data.count(b"HTTP/1.") == 1, data
        assert len(sent) == 1, f"the refusal was attempted {len(sent)} times"
    finally:
        _close(exporter)


# -- a legitimate scrape under a flood of idle connections ---------------------


def test_a_legitimate_scrape_is_answered_under_a_flood_of_idle_connections_that_stay_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An idle flood that keeps every connection open must not blind the port: a scrape, which sends at once, is
    answered, not refused. Known limit: such a flood can get a client that connects and waits an early 503."""
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 2.0)
    exporter = _exporter(tmp_path)
    server = exporter._server
    assert server is not None
    server._max = 4
    stop = threading.Event()
    held: list[socket.socket] = []

    def flood() -> None:
        while not stop.is_set():
            with contextlib.suppress(OSError):
                held.append(socket.create_connection((HOST, exporter._port), timeout=2))
            stop.wait(0.02)

    thread = threading.Thread(target=flood, daemon=True)
    try:
        thread.start()
        time.sleep(1.2)  # every slot is full of idle clients, the queue is busy
        heads = []
        for _ in range(5):
            # A 503 from a full admission queue is permitted overload behaviour, so each scrape waits for headroom in
            # it; a flood that is never evicted from (the regression this guards) keeps the queue full and fails here.
            assert _wait_until(lambda: len(server._queue) <= ox.ADMIT_QUEUE_MAX - 4, 5), "the idle flood kept the admission queue full"
            with socket.create_connection((HOST, exporter._port), timeout=10) as client:
                client.sendall(b"GET /metrics HTTP/1.1\r\nHost: x\r\n\r\n")
                data = b""
                while chunk := client.recv(65536):
                    data += chunk
            heads.append(data[:12])
        assert all(h.startswith(b"HTTP/1.0 200") for h in heads), heads
    finally:
        stop.set()
        thread.join(5)
        for s in held:
            s.close()
        _close(exporter)
