"""A scrape is always answered inside its scrape timeout while the port is under the load that once broke it:
24 clients that connect, wait 0.2 s and close, in a loop, with threads burning the exporter's GIL and processes
burning the CPUs. The exporter runs in its own process, as in the indexing service.

A probabilistic load test (burner processes beside the host's own load), so it is a `stress` test: it runs with
`-m stress` or `RUN_STRESS_TESTS=1`, in a stress job or by hand, and the unit job skips it. The deterministic guards for
the same defects are in test_ops_exporter_acceptor.py (the backlog follows the cap; a client that left takes no slot;
a request that has arrived is never cut; the peer check never waits). Needs no service; three runs, a few seconds each.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from tests.support.free_port import free_port

PY = Path(__file__).resolve().parents[3]
SCRAPE_TIMEOUT_S = 10.0  # the chart's metrics.podMonitor.scrapeTimeout
BURNERS = max(2, min(4, os.cpu_count() or 2))

pytestmark = [pytest.mark.stress, pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a Linux load test")]

EXPORTER = textwrap.dedent(
    """
    import sys, threading, time
    from app.telemetry.ops_exporter import OpsExporter
    run, port, burners = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    def burn():
        while True:
            sum(i * i for i in range(2000))
    for _ in range(burners):
        threading.Thread(target=burn, daemon=True).start()
    exporter = OpsExporter(service="indexing", run_dir=run, port=port, source=lambda: ({}, {}), bind="127.0.0.1")
    assert exporter.try_bind()
    print("READY", flush=True)
    time.sleep(600)
    """
)

SCRAPER = textwrap.dedent(
    """
    import json, socket, sys, time
    port, secs = int(sys.argv[1]), float(sys.argv[2])
    ok, slow, failed, worst = 0, 0, [], 0.0
    end = time.monotonic() + secs
    while time.monotonic() < end:
        t0 = time.monotonic()
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=10)
            s.settimeout(10)
            s.sendall(b"GET /metrics HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n")
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if time.monotonic() - t0 > 10:
                    raise TimeoutError("scrape timeout")
            s.close()
            if data.startswith((b"HTTP/1.0 200", b"HTTP/1.1 200")):
                ok += 1
            else:
                failed.append("status:" + data[:12].decode(errors="replace"))
        except OSError as exc:
            failed.append(type(exc).__name__)
        worst = max(worst, time.monotonic() - t0)
        time.sleep(0.5)
    print(json.dumps({"ok": ok, "failed": failed, "worst": worst}))
    """
)


def _burn(secs: float) -> None:
    end = time.time() + secs
    while time.time() < end:
        sum(i * i for i in range(5000))


def _reconnecting_idle(port: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
        except OSError:
            continue
        stop.wait(0.2)
        s.close()


@pytest.mark.parametrize("run", [1, 2, 3])
def test_scrapes_are_answered_inside_the_timeout_under_idle_reconnecting_clients_and_burners(tmp_path: Path, run: int) -> None:
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
    exporter = subprocess.Popen([sys.executable, "-B", "-c", EXPORTER, str(tmp_path), str(port), str(BURNERS)], cwd=PY, env=env,
                                stdout=subprocess.PIPE, text=True)
    stop = threading.Event()
    burners = []
    try:
        assert exporter.stdout is not None
        assert exporter.stdout.readline().strip() == "READY"
        ctx = mp.get_context("spawn")
        burners = [ctx.Process(target=_burn, args=(9.0,), daemon=True) for _ in range(BURNERS)]
        for p in burners:
            p.start()
        clients = [threading.Thread(target=_reconnecting_idle, args=(port, stop), daemon=True) for _ in range(24)]
        for t in clients:
            t.start()
        time.sleep(0.3)
        out = subprocess.run([sys.executable, "-c", SCRAPER, str(port), "6"], capture_output=True, text=True, timeout=SCRAPE_TIMEOUT_S + 30, check=True)
        result = json.loads(out.stdout.strip().splitlines()[-1])
        assert result["ok"] > 0
        assert not result["failed"], f"run {run}: scrapes that were not answered: {result}"
        assert result["worst"] < SCRAPE_TIMEOUT_S, result
    finally:
        stop.set()
        for p in burners:
            p.join(15)
        exporter.stdin and exporter.stdin.close()
        exporter.terminate()  # the child this test started
        exporter.wait(10)
