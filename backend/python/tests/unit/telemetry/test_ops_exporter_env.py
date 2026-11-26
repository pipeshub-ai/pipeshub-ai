"""The exporter's on/off switch, its config, the lifespan wiring, and the bounded port.

Needs no service; every port is asked of the kernel (`tests/support/free_port.py`).
"""
from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from app.telemetry import ops_exporter as ox
from tests.support.free_port import bind_on_free_port, free_port

PY = Path(__file__).resolve().parents[3]


class _Gov:
    def stats(self) -> dict:
        return {"worker_count": 1, "cpu_utilisation": 0.2, "mem_pressure_raw": 0.3}


def _listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PIPESHUB_METRICS_ENABLED", "PIPESHUB_METRICS_PORT", "PIPESHUB_METRICS_BIND", "PIPESHUB_RUN_DIR"):
        monkeypatch.delenv(name, raising=False)


# -- the switch ---------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "False ", "maybe"])
def test_off_values_build_nothing_start_nothing_and_write_nothing(value: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    port = free_port()
    if value is not None:
        monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", value)
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("PIPESHUB_METRICS_PORT", str(port))
    before = {t.name for t in threading.enumerate()}
    assert ox.exporter_from_env(_Gov(), "indexing") is None
    assert {t.name for t in threading.enumerate()} == before
    assert not (tmp_path / "run").exists() and not _listening(port)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
def test_on_values_build_an_exporter_that_has_not_started(value: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", value)
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path / "run"))
    exporter = ox.exporter_from_env(_Gov(), "indexing")
    assert isinstance(exporter, ox.OpsExporter) and exporter._thread is None and not (tmp_path / "run").exists()


def test_the_off_path_in_a_fresh_interpreter_reads_no_stats_and_makes_no_thread_file_or_port(tmp_path: Path) -> None:
    code = textwrap.dedent(f"""
        import os, threading
        os.environ.pop("PIPESHUB_METRICS_ENABLED", None)
        os.environ["PIPESHUB_RUN_DIR"] = {str(tmp_path / 'run')!r}
        from app.telemetry.ops_exporter import exporter_from_env
        class G:
            def stats(self): raise AssertionError("stats read on the off path")
        assert exporter_from_env(G(), "indexing") is None
        print(sorted(t.name for t in threading.enumerate()))
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=PY, capture_output=True, text=True, timeout=90, check=False)
    assert out.returncode == 0, out.stderr
    assert "ops-exporter" not in out.stdout and not (tmp_path / "run").exists()


# -- config -------------------------------------------------------------------


@pytest.mark.parametrize("port", ["70000", "-1", "0", "abc", "", "65536"])
def test_an_invalid_port_is_refused_at_config_time(port: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", "1")
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("PIPESHUB_METRICS_PORT", port)
    with caplog.at_level("WARNING"):
        exporter = ox.exporter_from_env(_Gov(), "indexing")
    assert exporter is not None and exporter._port == ox.DEFAULT_PORT
    assert [r for r in caplog.records if "PIPESHUB_METRICS_PORT" in r.message]


@pytest.mark.parametrize("port", [0, -1, 70000, 65536])
def test_the_exporter_itself_refuses_a_bad_port(port: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="port"):
        ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}))


@pytest.mark.parametrize("port", ["70000", "-1"])
def test_a_bad_port_never_kills_the_snapshot_thread(port: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", "1")
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("PIPESHUB_METRICS_PORT", port)
    errors: list[BaseException] = []
    old = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)  # type: ignore[assignment]
    exporter = None
    try:
        exporter = ox.exporter_from_env(_Gov(), "indexing")
        assert exporter is not None
        exporter._interval = 0.05
        exporter.start()
        time.sleep(0.4)
        alive = exporter._thread is not None and exporter._thread.is_alive()
    finally:
        if exporter is not None:
            exporter.stop()  # the snapshot loop, and the HTTP server the port fallback may have bound
        threading.excepthook = old
    assert alive and not errors


def test_the_bind_address_is_configurable_and_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", "1")
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path))
    assert ox.exporter_from_env(_Gov(), "indexing")._bind == "0.0.0.0"
    monkeypatch.setenv("PIPESHUB_METRICS_BIND", "127.0.0.1")
    assert ox.exporter_from_env(_Gov(), "indexing")._bind == "127.0.0.1"
    monkeypatch.setenv("PIPESHUB_METRICS_BIND", "not-an-address")
    with caplog.at_level("WARNING"):
        assert ox.exporter_from_env(_Gov(), "indexing")._bind == "0.0.0.0"
    assert [r for r in caplog.records if "PIPESHUB_METRICS_BIND" in r.message]
    with pytest.raises(ValueError, match="bind"):
        ox.OpsExporter(service="indexing", run_dir=tmp_path, port=free_port(), source=lambda: ({}, {}), bind="host.example")


def test_it_listens_only_on_the_configured_address(tmp_path: Path) -> None:
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="127.0.0.1"))
    port = exporter._port
    try:
        assert exporter._server.server_address == ("127.0.0.1", port)
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).status == 200
    finally:
        exporter._server.shutdown()
        exporter._server.server_close()


# -- the lifespan -------------------------------------------------------------


def _lifespan_calls() -> list[str]:
    tree = ast.parse((PY / "app" / "indexing_main.py").read_text())
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    return [ast.unparse(n) for n in ast.walk(func) if isinstance(n, ast.Call)]


def test_the_lifespan_builds_the_exporter_from_the_env_and_starts_and_stops_it() -> None:
    calls = _lifespan_calls()
    assert "exporter_from_env(governor, 'indexing')" in calls
    assert "ops_exporter.start()" in calls and "ops_exporter.stop()" in calls


def test_the_lifespan_does_not_read_the_switch_itself() -> None:
    """The one gate is exporter_from_env: no second, drifting check in the entry module."""
    assert "PIPESHUB_METRICS_ENABLED" not in (PY / "app" / "indexing_main.py").read_text()


@pytest.mark.parametrize("enabled", ["", "true"])
def test_importing_the_indexing_entry_module_starts_nothing(enabled: str, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    env["PIPESHUB_METRICS_ENABLED"] = enabled
    env["PIPESHUB_RUN_DIR"] = str(tmp_path / "run")
    code = "import threading\nimport app.indexing_main as m\nassert callable(m.lifespan)\nprint(sorted(t.name for t in threading.enumerate()))\n"
    out = subprocess.run([sys.executable, "-c", code], cwd=PY, env=env, capture_output=True, text=True, timeout=150, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "ops-exporter" not in out.stdout and not (tmp_path / "run").exists()


def test_the_exporter_on_serves_then_stop_removes_its_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    port = free_port()
    monkeypatch.setenv("PIPESHUB_METRICS_ENABLED", "true")
    monkeypatch.setenv("PIPESHUB_RUN_DIR", str(tmp_path))
    monkeypatch.setenv("PIPESHUB_METRICS_PORT", str(port))
    monkeypatch.setenv("PIPESHUB_METRICS_BIND", "127.0.0.1")
    exporter = ox.exporter_from_env(_Gov(), "indexing")
    assert exporter is not None
    exporter.start()
    deadline = time.time() + 10
    while time.time() < deadline and not _listening(port):
        time.sleep(0.1)
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3).read().decode()
    assert 'pipeshub_cgroup_memory_pressure_ratio{service="indexing"} 0.3' in body
    path = exporter.writer.path
    assert path.exists()
    exporter.stop()
    assert not _listening(port) and not path.exists()


# -- the port is bounded ------------------------------------------------------


def _wait_until(predicate, timeout_s: float) -> bool:
    """Polls observable state up to a bound; no fixed sleep decides the outcome."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_idle_connections_do_not_grow_threads_without_bound_and_time_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.5)  # the shipped value is pinned in the bounds test
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="127.0.0.1"))
    port = exporter._port
    socks: list[socket.socket] = []
    try:
        base = threading.active_count()
        socks.extend(socket.create_connection(("127.0.0.1", port)) for _ in range(200))
        # A slot is held until its handler thread has exited, so the live count is bounded at every sample.
        peak_conns = peak_threads = 0
        window_end = time.monotonic() + 0.3
        while time.monotonic() < window_end:
            peak_conns = max(peak_conns, len(exporter._server._conns))
            peak_threads = max(peak_threads, threading.active_count() - base)
            time.sleep(0.001)
        assert peak_conns <= 16, f"{peak_conns} tracked connections for 200 idle connections"
        assert peak_threads <= 16, f"{peak_threads} threads for 200 idle connections"
        # every idle handler passes its request deadline and is closed: poll the observable state, bounded
        assert _wait_until(lambda: threading.active_count() - base <= 1, 30), "idle handlers were not closed"
        assert urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).status == 200
    finally:
        for s in socks:
            s.close()
        exporter._server.shutdown()
        exporter._server.server_close()


def test_live_handler_threads_never_exceed_the_cap_under_trickling_clients_and_cpu_stress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ox, "REQUEST_DEADLINE_S", 0.4)
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="127.0.0.1"))
    port = exporter._port
    stop = threading.Event()
    socks: list[socket.socket] = []

    def burn() -> None:
        while not stop.is_set():
            sum(i * i for i in range(2000))

    def trickle() -> None:
        while not stop.is_set():
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=2)
            except OSError:
                continue
            socks.append(c)
            try:
                for _ in range(3):
                    c.send(b"G")  # one byte at a time: never a complete request
                    time.sleep(0.05)
            except OSError:
                pass

    workers = [threading.Thread(target=burn, daemon=True) for _ in range(4)]
    workers += [threading.Thread(target=trickle, daemon=True) for _ in range(6)]
    try:
        base = threading.active_count()  # the acceptor and the reaper are already in it
        for w in workers:
            w.start()
        base += len(workers)
        peak_conns = peak_threads = samples = 0
        end = time.monotonic() + 3
        while time.monotonic() < end:
            peak_conns = max(peak_conns, len(exporter._server._conns))
            peak_threads = max(peak_threads, threading.active_count() - base)
            samples += 1
            if samples % 300 == 0:
                _scrape_status(port)  # a refused connection is allowed; the scrape below must be answered
            time.sleep(0.0005)
        assert peak_conns <= 16, f"{peak_conns} tracked connections"
        assert peak_threads <= 16, f"{peak_threads} live handler threads over {samples} samples"
        stop.set()
        for w in workers:
            w.join(5)
        assert _wait_until(lambda: _scrape_status(port) == 200, 10), "a scrape was not answered while slots were held"
    finally:
        stop.set()
        for s in socks:
            s.close()
        exporter._server.shutdown()
        exporter._server.server_close()


def _scrape_status(port: int) -> int:
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).status
    except OSError:
        return 0


def test_the_shipped_bounds_are_small_literals() -> None:
    assert 4 <= ox.MAX_CONNECTIONS <= 32
    assert 0 < ox.REQUEST_DEADLINE_S <= ox.CONNECTION_TIMEOUT_S
    assert 0 < ox._Handler.timeout <= 10  # a silent client is dropped
    assert 0 < ox.COLLECT_CACHE_TTL_S <= 5  # a scrape flood costs one scan per window
    assert ox.OpsExporter(service="indexing", run_dir="/nonexistent-run", port=free_port(), source=lambda: ({}, {})).collector._cache_ttl == ox.COLLECT_CACHE_TTL_S


def test_a_client_that_sends_nothing_is_dropped_after_the_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ox._Handler, "timeout", 0.5)
    exporter = bind_on_free_port(lambda port: ox.OpsExporter(service="indexing", run_dir=tmp_path, port=port, source=lambda: ({}, {}), bind="127.0.0.1"))
    port = exporter._port
    try:
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.settimeout(5)
            started = time.time()
            assert s.recv(1) == b""  # the server closed it
            # wall-clock-ok: the server closes the connection at once; the failure is the socket's own 5 s timeout
            assert time.time() - started < 4
    finally:
        exporter._server.shutdown()
        exporter._server.server_close()
