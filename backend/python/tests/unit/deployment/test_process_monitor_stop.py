"""process_monitor.sh starts the drain as the stop signal arrives (R44-STOP).

The app container's stop grace (60 s) covers the durable worker's drain (30 s bound plus release, DRAIN_WAIT_S 40 s)
and Node's (8 s). Bash runs a trap only once its foreground command returns, so a SIGTERM that landed in the
monitoring loop's ``sleep "$CHECK_INTERVAL"`` began the drain up to 20 s late, past the grace for a full drain.

The script runs here as the image runs it, with /app pointed at a temporary tree and the services replaced by stubs
that log the signal they get; no Docker.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

from tests.support.settle import SETTLE_S

SCRIPT = Path(__file__).resolve().parents[5] / "deployment" / "process_monitor.sh"

_STUB = """#!/bin/bash
# Stands in for node and python: logs its start and the SIGTERM it gets, then exits as the service would.
# The monitor asks `python -m app.capacity facts` for the container's facts before it starts anything.
case "$*" in *app.capacity*) exit 0 ;; esac
name="$(basename "$0") $*"
log=__LOG__
on_term() { echo "TERM $name" >>"$log"; sleep "${STUB_DRAIN_S:-0}"; echo "EXIT $name" >>"$log"; exit 0; }
trap on_term TERM
echo "START $name" >>"$log"
sleep 600 &
wait
"""


def _tree(tmp: Path) -> tuple[Path, Path, Path]:
    for sub in ("app/backend/dist", "app/python", "bin"):
        (tmp / sub).mkdir(parents=True)
    log = tmp / "services.log"
    for name in ("node", "python"):
        stub = tmp / "bin" / name
        stub.write_text(_STUB.replace("__LOG__", str(log)))
        stub.chmod(0o755)
    curl = tmp / "bin" / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    script = tmp / "process_monitor.sh"
    script.write_text(SCRIPT.read_text().replace("/app/", f"{tmp}/app/"))
    return script, log, tmp / "bin"


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_a_stop_in_the_monitoring_loop_drains_the_worker_at_once_then_stops_node(tmp_path: Path) -> None:
    """The monitor idles 10 minutes between checks here: a stop it handled only after its idle would hit the hang
    guard. Order, not duration, says the rest: the worker drains first and Node stops only after it has exited."""
    script, log, stubs = _tree(tmp_path)
    out = tmp_path / "monitor.out"
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "CHECK_INTERVAL": "600",
           "AGENT_HARNESS_WORKER_ENABLED": "true", "MODEL_GATEWAY_ENABLED": "false", "STUB_DRAIN_S": "1"}
    with out.open("w") as sink:
        monitor = subprocess.Popen(["bash", str(script)], env=env, stdout=sink, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    try:
        assert _wait_for(lambda: "Beginning monitoring cycle" in out.read_text(), SETTLE_S), out.read_text()
        assert "app.harness_main" in log.read_text()
        monitor.send_signal(signal.SIGTERM)
        assert monitor.wait(timeout=SETTLE_S) == 0, out.read_text()
    finally:
        # The stubs' own sleeps outlive them; all of it is the monitor's session.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(monitor.pid, signal.SIGKILL)
    events = [line for line in log.read_text().splitlines() if not line.startswith("START ")]
    worker = "python -m app.harness_main"
    node = next(e.split(" ", 1)[1] for e in events if e.startswith("TERM node ") and "index.js" in e)
    # Durable runs commit through Node, so Node is signalled only once the worker has drained and exited.
    assert events.index(f"TERM {worker}") < events.index(f"EXIT {worker}") < events.index(f"TERM {node}")
    assert "All services stopped." in out.read_text()
