"""PERF-B on real processes: a rollback with a follow-up turn queued, onto the release before this build and onto the
tree before history-by-reference.

R-29 promises rollbacks to the release before this build (N-1, ``release_tree.previous_ref``), which reads a stored
input that names its history (``input.historyRef``) as this build does. ``HARNESS_RUN_HISTORY_BY_REFERENCE`` is the
switch that decides which shape this build stores.

PERFB-22 the switch off, then a rollback to N-1: the queued follow-up turn is stored with its history inline, and N-1's
Node and worker claim it and run it to its answer.
PERFB-23 the switch on (the default), then a rollback to N-1: the turn names its history, and N-1 claims it and runs
it to its answer on that history.
PERFB-24 the switch on, then a rollback onto the tree before PERF-B (integration ``7a0bf116b``), which the skew window
does not reach: that tree refuses its worker the turn, typed (``WORKER_INCOMPATIBLE``: the turn requires features it
lacks), never runs it, never ends it and changes nothing of it; rolled forward, this build runs it to its answer
(FUPERF-FU-1). Every case admits its turns once Node has seen a worker of this build, as a deployment does: a turn
admitted before that carries no pin, and that tree's Node fails pinning it at claim on this build's spec (a
``TypeError`` in its own ``grantedKnowledge``), which no change here can reach.

The quarantine cap is three claims, so a deployment left mixed for longer than three leases with the switch on can
lose a turn that way; the rollback runbook (FLAGS.md) turns the switch off first.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import release_tree
import stack
from test_crash_points import RECOVERY_TIMEOUT_S
from test_upgrades import scenario_file
from test_version_skew import DRAIN_S, TERMINAL, Upgrade

pytestmark = pytest.mark.fault

# Integration as PERF-B was merged into: the last build that writes and reads the history inline only.
BEFORE_HISTORY_BY_REFERENCE = release_tree.before("PERF-B")
SWITCH = "HARNESS_RUN_HISTORY_BY_REFERENCE"
# Longer than the suite's lease and three sweeps: a turn that would be failed, quarantined or handed to a worker
# that cannot read its pin would be by then.
HOLDS_FOR_S = 20


@pytest.fixture(scope="module")
def release() -> release_tree.ReleaseTree:
    return release_tree.extract(BEFORE_HISTORY_BY_REFERENCE)


@pytest.fixture(scope="module")
def previous() -> release_tree.ReleaseTree:
    return release_tree.extract(release_tree.previous_ref())


def _rig(fault_stack: stack.FaultStack, tree: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    fault_stack.node.kill9()
    rig = Upgrade(fault_stack, tree)
    try:
        yield rig
    finally:
        rig.stop()
        fault_stack.node.start()


@pytest.fixture
def upgrade(fault_stack: stack.FaultStack, release: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    yield from _rig(fault_stack, release)


@pytest.fixture
def rollback(fault_stack: stack.FaultStack, previous: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    yield from _rig(fault_stack, previous)


def _turn(session_id: str | None, name: str) -> dict[str, Any]:
    """A durable admission of a turn; a follow-up turn when ``session_id`` is given."""
    admitted: list[dict[str, Any]] = []
    body = {"query": f"flow SENTINEL-PROMPT-{name}", "recordIds": [], "filters": {}, "attachments": [],
            "chatMode": "quick"}

    def durable() -> bool:
        path = (f"/api/v1/agents/{stack.AGENT}/runs" if session_id is None
                else f"/api/v1/agents/{stack.AGENT}/conversations/{session_id}/runs")
        admission = stack.http("POST", path, {**body, "idempotencyKey": f"perfb-{uuid.uuid4()}"}).get("admission")
        if admission is not None:
            admitted.append(admission)
        return bool(admitted)

    stack.wait_for(durable, 60, "a durable admission")
    return admitted[0]


def _status(run_id: str) -> str:
    return str((stack.run_document(run_id) or {}).get("status"))


def _calls(run_dir: Path, run_id: str) -> int:
    return sum(1 for c in stack.jsonl(run_dir / "model-calls.jsonl") if c["runId"] == run_id)


def _answered_turn_then_a_queued_follow_up(rig: Upgrade, run_dir: Path, name: str) -> tuple[str, dict[str, Any]]:
    """This build admits and answers a first turn, its worker drains, and a follow-up turn is admitted and waits."""
    scenario_file(run_dir)
    rig.start_node(released=False)
    worker = rig.start_worker(run_dir, released=False)
    # As in a deployment with workers of this build up: Node pins a turn only once it has seen a worker that reads
    # pins (the F2 rule), and caches what it saw; admitting before that makes whether the follow-up is pinned a race.
    stack.wait_for(lambda: bool(stack.http("GET", "/__fault/liveness/resolved_config")["live"]), 90,
                   "a worker that reads pins seen by Node")
    first = _turn(None, f"{name}-first")
    stack.wait_for(lambda: _status(first["run"]["runId"]) in TERMINAL, RECOVERY_TIMEOUT_S, "the first turn's end")
    assert _status(first["run"]["runId"]) == "completed"
    assert worker.term(DRAIN_S) is not None, "this build's worker did not drain"
    follow_up = _turn(first["sessionId"], f"{name}-follow-up")
    run_id = str(follow_up["run"]["runId"])
    assert _status(run_id) == "queued"
    return run_id, (stack.run_document(run_id) or {})["input"]


def _runs_on_the_release(rig: Upgrade, run_dir: Path, run_id: str) -> None:
    rig.start_node(released=True)
    rig.start_worker(run_dir, released=True)
    stack.wait_for(lambda: _status(run_id) in TERMINAL, RECOVERY_TIMEOUT_S, "the queued turn's end on N-1")
    document = stack.run_document(run_id) or {}
    assert (document["status"], document.get("outcome")) == ("completed", "answer"), document.get("status")
    assert document["attempts"] == 1, "claimed once, by N-1"
    assert _calls(run_dir, run_id) >= 1
    assert "run.history_unreadable" not in rig.node_log.read_text(errors="replace")


def test_perfb_22_with_the_switch_off_n_minus_1_claims_and_runs_the_queued_turn(rollback: Upgrade) -> None:
    run_dir = rollback.fault_stack.out / "perfb_history_switch_off"
    rollback.env[SWITCH] = "false"
    run_id, stored_input = _answered_turn_then_a_queued_follow_up(rollback, run_dir, "switch-off")
    assert "historyRef" not in stored_input
    # The history is inline, sealed at rest with the run's other input (GAPS2-1, which N-1 holds): never in plaintext.
    assert "historySealed" in stored_input, "the first turn is the follow-up's history, inline"
    assert "previousConversations" not in stored_input
    _runs_on_the_release(rollback, run_dir, run_id)


def test_perfb_23_with_the_switch_on_n_minus_1_runs_the_turn_on_its_history_by_reference(rollback: Upgrade) -> None:
    run_dir = rollback.fault_stack.out / "perfb_history_switch_on"
    rollback.env.pop(SWITCH, None)
    run_id, stored_input = _answered_turn_then_a_queued_follow_up(rollback, run_dir, "switch-on")
    assert stored_input["historyRef"]["turns"] >= 1
    assert "previousConversations" not in stored_input
    _runs_on_the_release(rollback, run_dir, run_id)


# What a rollback that cannot read the turn must leave as it was (its status and lease may move: a sweep hands an
# expired lease back to the queue).
UNTOUCHED = ("input", "requires", "spec", "resolved", "outcome", "attempts")


def test_perfb_24_a_rollback_onto_the_tree_before_perfb_is_refused_typed_and_touches_nothing(upgrade: Upgrade) -> None:
    run_dir = upgrade.fault_stack.out / "perfb_history_pre_feature"
    upgrade.env.pop(SWITCH, None)
    run_id, stored_input = _answered_turn_then_a_queued_follow_up(upgrade, run_dir, "pre-feature")
    assert stored_input["historyRef"]["turns"] >= 1
    stored = stack.run_document(run_id) or {}
    assert "resolved_config" in (stored.get("requires") or {}).get("features", []), stored.get("requires")
    before = {key: stored.get(key) for key in UNTOUCHED}

    upgrade.start_node(released=True)
    older = upgrade.start_worker(run_dir, released=True)
    stack.wait_for(lambda: bool(upgrade.refused(run_id)), 90, "the tree before PERF-B refusing its worker the turn, typed")
    time.sleep(HOLDS_FOR_S)
    waiting = stack.run_document(run_id) or {}
    assert waiting["status"] not in TERMINAL, "the turn was ended by a tree that cannot read it"
    assert {key: waiting.get(key) for key in UNTOUCHED} == before, "a refused rollback changed the turn"
    assert _calls(run_dir, run_id) == 0, "the tree before PERF-B ran the turn without its history"
    older.kill9()

    upgrade.start_node(released=False)
    upgrade.start_worker(run_dir, released=False)
    stack.wait_for(lambda: _status(run_id) in TERMINAL, RECOVERY_TIMEOUT_S, "the turn's end on this build")
    document = stack.run_document(run_id) or {}
    assert (document["status"], document.get("outcome")) == ("completed", "answer"), document.get("status")
    assert _calls(run_dir, run_id) >= 1

