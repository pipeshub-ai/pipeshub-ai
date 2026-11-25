"""QA-18 on real processes: release N (real code, ``release_tree``) and this build, a durable run in flight.

QA18-1 workers first (DUR3's deploy order): N's Node and worker hold a run; N's worker is drained and this build's
claims the run from N's Node; then N's Node is replaced by this build's, whose startup migrations run over N's data.
QA18-2 Node first: this build's Node replaces N's under N's worker, then the worker is replaced.
QA18-3 rollback: a run this build's worker claimed is left to N's Node and worker; N's worker never claims it (its fold
is older than the run's), the run waits queued and marked, never failed; rolled forward again, it completes.

Each scenario starts from an empty database of its own, so this build meets only data N wrote. The session's own Node
(this build, on the suite's port) is stopped for the module and started again after it.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import release_tree
import stack
from test_crash_points import (
    ONE_CALL_EACH,
    RECOVERY_TIMEOUT_S,
    Scenario,
    k12_problems,
    save_journal,
    wait_for_final_disposition,
)
from test_upgrades import first_model_call, outcome_of, record, scenario_file

pytestmark = pytest.mark.fault

TERMINAL = {"completed", "failed", "cancelled"}
DRAIN_S = 40
# A worker mid-turn holds its attempt across a Node switch only while its lease lasts. A switch takes 7 to 8 s on
# the fault stack (stop, boot, collections, indexes); at the suite's 10 s lease a claim made just before the
# switch ran out about 0.1 s before the new Node answered, the worker fenced itself and the turn's model call
# was made again (correct, but not what these cases are about).
NODE_SWITCH_LEASE_MS = 30_000
# On a loaded host the switch has taken 16 s. A worker renews every third of its lease and fences itself 2 s
# before it ends, so a lease of six Node starts rides out a switch four times slower than the slowest start this
# rig measured on a loaded host.
LEASE_PER_NODE_START = 6


@pytest.fixture(scope="module")
def release() -> release_tree.ReleaseTree:
    return release_tree.extract(release_tree.release_ref())


@pytest.fixture
def upgrade(fault_stack: stack.FaultStack, release: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    fault_stack.node.kill9()
    rig = Upgrade(fault_stack, release)
    try:
        yield rig
    finally:
        rig.stop()
        fault_stack.node.start()


class Upgrade:
    """One scenario's processes: a Node and workers of either release over one fresh database."""

    def __init__(self, fault_stack: stack.FaultStack, release: release_tree.ReleaseTree) -> None:
        self.fault_stack = fault_stack
        self.release = release
        self.env = stack.stack_env(fault_stack.broker, f"harness_qa18_{fault_stack.broker}_{uuid.uuid4().hex[:8]}")
        self.node: stack.Process | None = None
        self.workers: list[stack.Process] = []
        self.node_log = fault_stack.out / f"qa18-node-{uuid.uuid4().hex[:6]}.log"
        self.slowest_node_start_s = 0.0

    def start_node(self, *, released: bool) -> None:
        if self.node is not None:
            assert self.node.term(20) is not None, "Node did not stop"
        self.node = (self.release.node_process(self.env, self.node_log) if released
                     else stack.node_process(self.env, self.node_log))
        started = time.monotonic()
        self.node.start()
        self.slowest_node_start_s = max(self.slowest_node_start_s, time.monotonic() - started)

    def node_switch_lease_ms(self) -> int:
        """The lease of a worker whose claim must outlive a Node switch, sized once a Node has started here."""
        assert self.slowest_node_start_s > 0, "start a Node first: the lease is sized from its start"
        return max(NODE_SWITCH_LEASE_MS, round(LEASE_PER_NODE_START * self.slowest_node_start_s * 1000))

    def start_worker(self, run_dir: Path, *, released: bool) -> stack.Process:
        worker = self.release.worker_process(self.env, run_dir) if released else stack.worker_process(self.env, run_dir)
        self.workers.append(worker)
        worker.start()
        return worker

    def stop(self) -> None:
        for worker in self.workers:
            worker.kill9()
        if self.node is not None:
            self.node.kill9()

    def refused(self, run_id: str, reason: str = "WORKER_INCOMPATIBLE") -> list[str]:
        """Node's typed refusals of a claim of ``run_id`` (``run.claim_refused`` with ``reason``), as logged."""
        return [r.text for r in stack.log_records("node", self.node_log.read_text(errors="replace"))
                if r.event == "run.claim_refused" and run_id in r.text and f'"reason": "{reason}"' in r.text]


def _admit(name: str) -> tuple[str, str]:
    """A durable admission; until Node has seen a live worker it answers on the legacy route (no admission)."""
    admitted: list[dict[str, Any]] = []

    def durable() -> bool:
        admission = stack.admit(f"flow SENTINEL-PROMPT-{name}", f"qa18-{uuid.uuid4()}")
        if admission is not None:
            admitted.append(admission)
        return bool(admitted)

    stack.wait_for(durable, 60, "a durable admission")
    return admitted[0]["run"]["runId"], admitted[0]["sessionId"]


def _attempts_called(run_dir: Path, run_id: str) -> set[str]:
    return {c["attemptId"] for c in stack.jsonl(run_dir / "model-calls.jsonl") if c["runId"] == run_id}


def _wait_terminal(run_dir: Path, run_id: str) -> None:
    stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("status") in TERMINAL, RECOVERY_TIMEOUT_S,
                   f"{run_id} terminal")
    wait_for_final_disposition(run_dir, run_id)


def _settle(rig: Upgrade, scenario: Scenario, run_dir: Path, run_id: str, session_id: str) -> list[str]:
    outcome = outcome_of(run_id, session_id, run_dir)
    save_journal(rig.fault_stack.broker, scenario.name, outcome)
    problems = k12_problems(scenario, outcome)
    record(rig.fault_stack, {"scenario": scenario.name, "release": rig.release.ref, "runId": run_id,
                             "claims": outcome.claims, "problems": problems})
    return problems


def test_qa18_1_workers_first_a_run_in_flight_completes_on_this_build(upgrade: Upgrade) -> None:
    scenario = Scenario("qa18_workers_first", "flow", "upgrade", "completed", ONE_CALL_EACH, knobs={"modelDelayS": 3})
    run_dir = upgrade.fault_stack.out / scenario.name
    upgrade.start_node(released=True)
    scenario_file(run_dir, modelDelayS=3, leaseMs=upgrade.node_switch_lease_ms())
    run_id, session_id = _admit(scenario.name)
    old = upgrade.start_worker(run_dir, released=True)
    first_model_call(run_dir, run_id)

    assert old.term(DRAIN_S) is not None, "N's worker did not drain"
    upgrade.start_worker(run_dir, released=False)
    stack.wait_for(lambda: len(_attempts_called(run_dir, run_id)) >= 2, RECOVERY_TIMEOUT_S, "this build's worker called")
    upgrade.start_node(released=False)
    _wait_terminal(run_dir, run_id)

    assert _settle(upgrade, scenario, run_dir, run_id, session_id) == []


def test_qa18_2_node_first_a_run_in_flight_completes_across_both_switches(upgrade: Upgrade) -> None:
    scenario = Scenario("qa18_node_first", "flow", "upgrade", "completed", ONE_CALL_EACH, knobs={"modelDelayS": 3})
    run_dir = upgrade.fault_stack.out / scenario.name
    upgrade.start_node(released=True)
    scenario_file(run_dir, modelDelayS=3, leaseMs=upgrade.node_switch_lease_ms())
    run_id, session_id = _admit(scenario.name)
    old = upgrade.start_worker(run_dir, released=True)
    first_model_call(run_dir, run_id)

    upgrade.start_node(released=False)
    stack.wait_for(
        lambda: any(c["runId"] == run_id and c["turn"] >= 2 for c in stack.jsonl(run_dir / "model-calls.jsonl")),
        RECOVERY_TIMEOUT_S, "N's worker went on against this build's Node",
    )
    assert old.term(DRAIN_S) is not None, "N's worker did not drain"
    upgrade.start_worker(run_dir, released=False)
    _wait_terminal(run_dir, run_id)

    assert _settle(upgrade, scenario, run_dir, run_id, session_id) == []


def test_qa18_3_rolled_back_an_older_worker_never_claims_a_run_this_build_claimed(upgrade: Upgrade) -> None:
    scenario = Scenario("qa18_rollback", "flow", "upgrade", "completed", ONE_CALL_EACH, knobs={"modelDelayS": 3})
    run_dir = upgrade.fault_stack.out / scenario.name
    scenario_file(run_dir, modelDelayS=3)
    upgrade.start_node(released=False)
    # This build's Node routes a run durably only once it has seen a live worker (`no_live_worker`), so the worker comes first.
    newer = upgrade.start_worker(run_dir, released=False)
    run_id, session_id = _admit(scenario.name)
    first_model_call(run_dir, run_id)
    assert newer.term(DRAIN_S) is not None, "this build's worker did not drain"

    upgrade.start_node(released=True)
    upgrade.start_worker(run_dir, released=True)
    stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("waitingForWorker") is not None, 60, "marked waiting")
    stack.wait_for(lambda: len(_filtered(upgrade, run_id)) >= 1, 60, "N's Node filtered N's worker's claim")
    waited = stack.run_document(run_id) or {}
    assert waited.get("status") == "queued", waited.get("status")
    assert waited["waitingForWorker"]["requires"]["foldVersion"] > _release_fold(upgrade.release)
    assert len(_attempts_called(run_dir, run_id)) == 1, "N's worker ran the run"

    # N's Node cannot read the spec this build pinned (RU-2g), so rolling forward is Node first, then a worker;
    # N's worker keeps polling throughout and still never takes the run.
    upgrade.start_node(released=False)
    upgrade.start_worker(run_dir, released=False)
    _wait_terminal(run_dir, run_id)
    assert _settle(upgrade, scenario, run_dir, run_id, session_id) == []


def _filtered(rig: Upgrade, run_id: str) -> list[str]:
    return [r.text for r in stack.log_records("node", rig.node_log.read_text(errors="replace"))
            if r.event == "claim.filtered" and run_id in r.text]


def _release_fold(release: release_tree.ReleaseTree) -> int:
    source = release.python_root / "harness_core" / "kernel" / "fold.py"
    match = re.search(r"^FOLD_VERSION = (\d+)", source.read_text(), re.M)
    assert match is not None
    return int(match.group(1))

