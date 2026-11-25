"""OPS2: MongoDB goes away under a serving Node and comes back, per broker.

On the human test stack the shared MongoDB died on a full disk and returned about fifty minutes later; Node never
served again. Its process had begun stopping on the first uncaught ENOSPC and re-entered the stop for every later
one. Here the store alone goes away, longer than server selection (5 s) and the sweeper's lease (15 s): the process
keeps running, logs each loop's failure at the loop's own pace, and once the store is back it leads its sweeper
again and completes a run within a bound, without a restart.
"""

from __future__ import annotations

import collections
import time

import pytest

import stack
from test_recovery_bound import SWEEP_INTERVAL_S, SWEEPER_LEASE_S, ensure_running, holder_pid, leases
from test_startup_gate import a_run_completes
from test_upgrades import record

pytestmark = pytest.mark.fault

OUTAGE_S = 30
# mongod's own start, the driver's next heartbeat (10 s), then the sweeper's lease and one tick.
RECOVERY_S = 30 + SWEEPER_LEASE_S + SWEEP_INTERVAL_S


def leads(pid: int) -> bool:
    return holder_pid(leases("none", stack.NODE_URL)["leader"]) == pid


def test_ops2_f1_a_node_serves_again_once_its_store_returns(fault_stack: stack.FaultStack) -> None:
    node, log = fault_stack.node, fault_stack.node_log
    ensure_running(node)
    assert node.proc is not None
    pid = node.proc.pid
    stack.wait_for(lambda: leads(pid), SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "the Node leading before the outage")
    mark = stack.log_mark(log)
    began = time.monotonic()
    try:
        stack.compose("stop", "mongodb")
        time.sleep(OUTAGE_S)
        alive_through_outage = node.proc.poll() is None
    finally:
        stack.compose("start", "mongodb")
    returned = time.monotonic()
    try:
        stack.wait_for(lambda: leads(pid), RECOVERY_S, "the same Node leading its sweeper again")
        recovered_s = round(time.monotonic() - returned, 1)
        problems = a_run_completes(fault_stack, "store_outage")
    finally:
        ensure_running(node)
    window_s = time.monotonic() - began
    failures = collections.Counter(
        r.event for r in stack.log_since("node", log, mark) if r.level in ("WARNING", "ERROR")
    )
    record(fault_stack, {"scenario": "store_outage", "outageS": OUTAGE_S, "recoveredS": recovered_s,
                         "failures": dict(failures), "problems": problems})
    assert alive_through_outage
    assert node.proc.pid == pid, "the Node was restarted"
    # The incident logged one release failure 60 times a second; a loop that fails logs once per tick.
    assert all(count <= window_s for count in failures.values()), failures
    assert not [e for e in failures if e.endswith("lease_release_failed")], failures
    assert problems == [], problems


SHUTDOWN_DEADLINE_S = 20  # SHUTDOWN_DEADLINE_MS, src/libs/services/process-shutdown.ts
STORM = 1000


def test_ops2_f2_a_fault_storm_with_the_store_away_stops_once_and_exits_for_its_supervisor(
    fault_stack: stack.FaultStack,
) -> None:
    """The incident's own sequence: the store away and every write throwing. One stop, one release try per lease,
    a non-zero exit within the shutdown's deadline; started again once the store is back, it leads."""
    node, log = fault_stack.node, fault_stack.node_log
    ensure_running(node)
    assert node.proc is not None
    mark = stack.log_mark(log)
    try:
        stack.compose("stop", "mongodb")
        stack.http("POST", "/__fault/uncaught", {"count": STORM})
        started = time.monotonic()
        exited = node.wait_dead(SHUTDOWN_DEADLINE_S + 10)
        exit_s = round(time.monotonic() - started, 1)
        exit_code = node.proc.returncode
    finally:
        stack.compose("up", "-d", "--wait", "--wait-timeout", "120", "mongodb")
    try:
        node.start()
        assert node.proc is not None
        pid = node.proc.pid
        stack.wait_for(lambda: leads(pid), RECOVERY_S, "the restarted Node leading its sweeper")
    finally:
        ensure_running(node)
    records = stack.log_since("node", log, mark)
    stops = sum(1 for r in records if "Received uncaughtException" in r.text)
    releases = collections.Counter(r.event for r in records if r.event.endswith("lease_release_failed"))
    record(fault_stack, {"scenario": "fault_storm_store_away", "exitCode": exit_code, "exitS": exit_s,
                         "stops": stops, "releaseFailures": dict(releases)})
    assert exited, "the Node kept running"
    assert exit_code not in (0, None), exit_code
    assert stops == 1, stops
    assert all(count == 1 for count in releases.values()), releases
