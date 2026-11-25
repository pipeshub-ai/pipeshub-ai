"""OPS1: how long a run waits after the Node that claimed it for a worker went away, per broker.

A claim's transaction commits in Mongo and the Node dies, or is told to stop,
before the worker has its answer. Nobody holds the attempt, so the run waits
for its lease to lapse and for a sweeper to re-queue it. The bound (SLO #6):

    recovered within  max(L, T) + I  of the claim's commit

``L`` the attempt's lease, ``I`` the sweep interval, ``T`` the takeover of the
sweeper's leadership: nothing when the leader lives or stops gracefully (it
releases its lease, and a stopping Node answers the claims it accepted, so
there is nothing to recover), its leader lease (15 s) when it crashed, and a
single Node's restart when that takes longer.

The failpoint is the fault stack Node's (``/__fault/failpoint``): ``kill`` is a
SIGKILL the moment the claim's transaction has committed; ``term`` a SIGTERM
there, with the answer still a second away. Times are the store's own
(``/__fault/leases``), read by polling.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import stack
from test_crash_points import (
    LEASE_MS,
    ONE_CALL_EACH,
    RECOVERY_TIMEOUT_S,
    Scenario,
    k12_problems,
    save_journal,
    wait_for_final_disposition,
)
from test_upgrades import outcome_of, record, scenario_file

pytestmark = pytest.mark.fault

SWEEPER_LEASE = "agent-harness-run-sweeper"
# SLO #6 as stated (docs/agent-harness-v2/SLO.md); the build's own values are read from Node and must agree.
LEASE_S = LEASE_MS / 1000
SWEEP_INTERVAL_S = 5.0
SWEEPER_LEASE_S = 15.0
# What observing adds to a bound: the poll below, and the tick's own writes between its timer and its commit.
POLL_S = 0.1
OBSERVED_WITHIN_S = 1.5
TERMINAL = {"completed", "failed", "cancelled"}
# The graceful cases: an attempt's lease that outlasts a single Node's restart, so only a lost answer re-claims.
RESTART_LEASE_MS = 30_000


@dataclass
class Nodes:
    """The Node the worker's claim reaches (the sweeper's leader), and where the test reads from while it is away."""

    leader: stack.Process
    leader_port: int
    survivor_port: int | None
    leader_log: Path

    @property
    def leader_url(self) -> str:
        return f"http://127.0.0.1:{self.leader_port}"

    @property
    def survivor_url(self) -> str | None:
        return None if self.survivor_port is None else f"http://127.0.0.1:{self.survivor_port}"


def leases(run_id: str, base: str) -> dict[str, Any]:
    return stack.http("GET", f"/__fault/leases/{run_id}", base=base)


def holder_pid(lease: dict[str, Any] | None) -> int | None:
    """The process that holds a leader lease: holders are ``<host>:<pid>:<uuid>``."""
    return None if lease is None else int(lease["holder"].split(":")[1])


def stored_leader(database: str) -> dict[str, Any] | None:
    """The leader lease as Mongo holds it, read with no Node running."""
    script = (
        f"const l = db.getSiblingDB('{database}').agentHarnessLeases.findOne({{_id: '{SWEEPER_LEASE}'}});"
        "print(JSON.stringify(l === null ? null : {holder: l.holder, expiresAt: l.expiresAt}))"
    )
    done = stack.compose(
        "exec", "-T", "mongodb", "mongosh", "--quiet", "-u", "admin", "-p", "password", "--authenticationDatabase",
        "admin", "--eval", script,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def wait_for_leader(pids: dict[int, int], base: str) -> int:
    """The port of the live Node that leads; a lease a dead process left is waited out."""
    found: list[int] = []

    def led() -> bool:
        state = leases("none", base)
        pid = holder_pid(state["leader"])
        if pid in pids and state["leader"]["expiresAt"] > state["now"]:
            found.append(pids[pid])
            return True
        return False

    stack.wait_for(led, 2 * SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "a live sweeper leader")
    return found[0]


def ensure_running(node: stack.Process) -> None:
    if node.proc is None or node.proc.poll() is not None:
        node.start()


@contextlib.contextmanager
def one_node(fault_stack: stack.FaultStack) -> Iterator[Nodes]:
    try:
        # A lease an earlier case's dead Node left is waited out: the scenario starts with this Node leading.
        assert fault_stack.node.proc is not None
        wait_for_leader({fault_stack.node.proc.pid: stack.NODE_PORT}, stack.NODE_URL)
        yield Nodes(fault_stack.node, stack.NODE_PORT, None, fault_stack.node_log)
    finally:
        ensure_running(fault_stack.node)


@contextlib.contextmanager
def two_nodes(fault_stack: stack.FaultStack) -> Iterator[Nodes]:
    port_b = stack.extra_port(5)
    node_b = stack.node_process(fault_stack.env, fault_stack.out / "node-b-recovery.log", port=port_b)
    node_b.start()
    try:
        assert fault_stack.node.proc is not None and node_b.proc is not None
        pids = {fault_stack.node.proc.pid: stack.NODE_PORT, node_b.proc.pid: port_b}
        leader_port = wait_for_leader(pids, stack.NODE_URL)
        if leader_port == stack.NODE_PORT:
            yield Nodes(fault_stack.node, leader_port, port_b, fault_stack.node_log)
        else:
            yield Nodes(node_b, leader_port, stack.NODE_PORT, fault_stack.out / "node-b-recovery.log")
    finally:
        node_b.kill9()
        ensure_running(fault_stack.node)


TOPOLOGIES = {"one_node": one_node, "two_nodes": two_nodes}


def _ts(event: dict[str, Any]) -> float:
    return datetime.fromisoformat(event["ts"].replace("Z", "+00:00")).timestamp()


def claims_of(run_id: str, session_id: str, base: str) -> list[dict[str, Any]]:
    return [e for e in stack.journal(session_id, base=base) if e.get("runId") == run_id and e["type"] == "run.claimed"]


def poll_until(
    read: Callable[[], dict[str, Any]], done: Callable[[dict[str, Any]], bool], timeout: float, what: str
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = read()
        if done(state):
            return state
        time.sleep(POLL_S)
    raise TimeoutError(what)


@dataclass
class Claimed:
    """A run a worker asked the leader for, with the failpoint armed at the claim's commit."""

    run_id: str
    session_id: str
    worker: stack.Process
    balancer: stack.Balancer
    marks: dict[Path, int]

    def reroute(self, nodes: Nodes) -> None:
        """The worker's next connection goes to the Node that is left; a single Node comes back on its own port."""
        if nodes.survivor_port is not None:
            self.balancer.route_to([nodes.survivor_port])


@contextlib.contextmanager
def claim_at_failpoint(
    fault_stack: stack.FaultStack, nodes: Nodes, name: str, action: str, lease_ms: int = LEASE_MS
) -> Iterator[Claimed]:
    run_dir = fault_stack.out / name
    scenario_file(run_dir, modelDelayS=1, leaseMs=lease_ms)
    stack.advertise_worker(fault_stack.env, run_dir)
    balancer = stack.Balancer(stack.extra_port(6), [nodes.leader_port])
    worker = stack.worker_process({**fault_stack.env, "AGENT_HARNESS_STATE_URL": balancer.url}, run_dir)
    marks = {log: stack.log_mark(log) for log in (fault_stack.node_log, fault_stack.out / "node-b-recovery.log")}
    try:
        stack.http("POST", "/__fault/failpoint", {"atClaimCommit": action}, base=nodes.leader_url)
        admission = stack.admit(f"flow SENTINEL-PROMPT-{name}", f"fault-{uuid.uuid4()}", base=nodes.leader_url)
        worker.start()
        yield Claimed(admission["run"]["runId"], admission["sessionId"], worker, balancer, marks)
    finally:
        worker.kill9()
        balancer.close()


def node_records(fault_stack: stack.FaultStack, claimed: Claimed) -> list[stack.LogRecord]:
    """What both Nodes logged since the scenario began."""
    records: list[stack.LogRecord] = []
    for log, mark in claimed.marks.items():
        if log.exists():
            records += stack.log_since("node", log, mark)
    return records


def finish(fault_stack: stack.FaultStack, name: str, claimed: Claimed, base: str, claims: int) -> list[str]:
    # The checker reads through the session's Node, which a two-Node scenario may have taken away.
    ensure_running(fault_stack.node)
    stack.wait_for(lambda: (stack.run_document(claimed.run_id, base=base) or {}).get("status") in TERMINAL,
                   RECOVERY_TIMEOUT_S, f"{claimed.run_id} terminal")
    run_dir = fault_stack.out / name
    wait_for_final_disposition(run_dir, claimed.run_id)
    outcome = outcome_of(claimed.run_id, claimed.session_id, run_dir)
    save_journal(fault_stack.broker, name, outcome)
    scenario = Scenario(name, "flow", "node_at_claim_commit", "completed", ONE_CALL_EACH)
    problems = k12_problems(scenario, outcome)
    if outcome.claims != claims:
        problems.append(f"claims {outcome.claims} != {claims}")
    return problems


@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_ops1_1_crash_at_claim_commit_is_recovered_within_the_bound(
    fault_stack: stack.FaultStack, topology: str
) -> None:
    name = f"recovery_crash_{topology}"
    with TOPOLOGIES[topology](fault_stack) as nodes, claim_at_failpoint(fault_stack, nodes, name, "kill") as claimed:
        assert nodes.leader.wait_dead(60), "the failpoint did not kill the Node at the claim's commit"
        claimed.reroute(nodes)
        assert nodes.leader.proc is not None
        dead_pid = nodes.leader.proc.pid
        left = stored_leader(fault_stack.env["MONGO_DB_NAME"])
        assert left is not None and holder_pid(left) == dead_pid, left
        lapses_at = left["expiresAt"]

        if nodes.survivor_url is None:
            nodes.leader.start()
            base = nodes.leader_url
        else:
            base = nodes.survivor_url
        ready = leases(claimed.run_id, base)

        # The claim is in Mongo and no Node said so: nobody holds the attempt.
        first = claims_of(claimed.run_id, claimed.session_id, base)
        assert [c["data"]["fence"] for c in first] == [1], first
        committed_at = _ts(first[0]) * 1000
        orphan = ready["run"] if ready["run"]["fence"] == 1 else None
        logged = [r for r in node_records(fault_stack, claimed) if r.event == "run.claimed" and claimed.run_id in r.text]
        assert logged == [], [r.text for r in logged]

        seen_before_lapse: list[int | None] = []

        def read() -> dict[str, Any]:
            state = leases(claimed.run_id, base)
            if state["now"] < lapses_at:
                seen_before_lapse.append(holder_pid(state["leader"]))
            return state

        took = poll_until(read, lambda s: holder_pid(s["leader"]) not in (dead_pid, None),
                          2 * SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "another replica leading")
        requeued = poll_until(read, lambda s: s["run"]["status"] != "running" or s["run"]["fence"] > 1,
                              RECOVERY_TIMEOUT_S, "the orphaned attempt expired")
        problems = finish(fault_stack, name, claimed, base, claims=2)

    attempt_lapses_at = orphan["leaseExpiresAt"] if orphan is not None else committed_at + LEASE_MS
    # A single Node cannot lead before it is back; the first read after its start stands for that.
    back_at = ready["now"] if nodes.survivor_url is None else 0
    takeover_s = (took["now"] - max(lapses_at, back_at)) / 1000
    recovery_s = (requeued["now"] - committed_at) / 1000
    bound_s = max(LEASE_S, SWEEPER_LEASE_S, (back_at - committed_at) / 1000) + SWEEP_INTERVAL_S
    record(fault_stack, {
        "scenario": name, "runId": claimed.run_id, "leaderLeaseLeftAtDeathS": round((lapses_at - committed_at) / 1000, 2),
        "takeoverAfterLeaseLapsedS": round(takeover_s, 2), "requeuedAfterCommitS": round(recovery_s, 2),
        "requeuedAfterAttemptLapsedS": round((requeued["now"] - attempt_lapses_at) / 1000, 2),
        "boundS": round(bound_s, 2), "sweeperLeaseMs": ready["sweeperLeaseMs"], "problems": problems,
    })
    assert ready["sweepIntervalMs"] == SWEEP_INTERVAL_S * 1000, ready["sweepIntervalMs"]
    assert ready["sweeperLeaseMs"] == SWEEPER_LEASE_S * 1000, ready["sweeperLeaseMs"]
    assert all(pid == dead_pid for pid in seen_before_lapse), f"leadership changed before the lease lapsed: {seen_before_lapse}"
    assert takeover_s <= SWEEP_INTERVAL_S + OBSERVED_WITHIN_S, takeover_s
    assert recovery_s <= bound_s + OBSERVED_WITHIN_S, (recovery_s, bound_s)
    assert problems == [], problems


# When the SIGTERM lands: with the claim committed and unanswered, or with the claim yet to reach the store.
SIGTERM_AT = {"committed": "term", "arriving": "term-before"}


@pytest.mark.parametrize("at", list(SIGTERM_AT))
@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_ops1_1_sigterm_at_a_claim_answers_the_claim_and_hands_leadership_over(
    fault_stack: stack.FaultStack, topology: str, at: str
) -> None:
    name = f"recovery_graceful_{at}_{topology}"
    with (
        TOPOLOGIES[topology](fault_stack) as nodes,
        claim_at_failpoint(fault_stack, nodes, name, SIGTERM_AT[at], RESTART_LEASE_MS) as claimed,
    ):
        assert nodes.leader.wait_dead(30), "the Node did not stop on SIGTERM"
        claimed.reroute(nodes)
        assert nodes.leader.proc is not None
        stopped_pid, exit_code = nodes.leader.proc.pid, nodes.leader.proc.returncode

        if nodes.survivor_url is None:
            left = stored_leader(fault_stack.env["MONGO_DB_NAME"])
            nodes.leader.start()
            assert nodes.leader.proc is not None
            base = nodes.leader_url
            # Asked for at start, before the process is ready: no interval passes without a leader.
            at_ready = leases(claimed.run_id, base)
            assert holder_pid(at_ready["leader"]) == nodes.leader.proc.pid, at_ready["leader"]
            handover_s = 0.0
        else:
            base = nodes.survivor_url
            stopped = leases(claimed.run_id, base)
            left = stopped["leader"]
            took = poll_until(lambda: leases(claimed.run_id, base), lambda s: holder_pid(s["leader"]) not in (stopped_pid, None),
                              SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "the survivor leading")
            handover_s = (took["now"] - stopped["now"]) / 1000
        problems = finish(fault_stack, name, claimed, base, claims=1)
        records = node_records(fault_stack, claimed)
        stopping = stack.log_since("node", nodes.leader_log, claimed.marks[nodes.leader_log])

    events = [r.event for r in records]
    expired = [r.text for r in records if r.event == "sweeper.expired" and claimed.run_id in r.text]
    record(fault_stack, {"scenario": name, "runId": claimed.run_id, "exitCode": exit_code, "handoverS": round(handover_s, 2),
                         "leaseLeftBehind": left, "problems": problems})
    assert exit_code == 0, exit_code
    assert holder_pid(left) != stopped_pid, f"the stopped Node still holds the lease: {left}"
    assert "sweeper.leader_released" in events
    assert "sweeper.lease_release_failed" not in events
    # The stopping Node itself answered the claim, through a store that was still open.
    assert any(r.event == "run.claimed" and claimed.run_id in r.text for r in stopping), "the stopping Node did not answer the claim"
    store_closed = [r.event for r in stopping if r.level == "ERROR" and "Mongo" in r.text]
    assert store_closed == [], store_closed
    assert expired == [], expired
    assert handover_s <= SWEEP_INTERVAL_S + OBSERVED_WITHIN_S, handover_s
    assert problems == [], problems
