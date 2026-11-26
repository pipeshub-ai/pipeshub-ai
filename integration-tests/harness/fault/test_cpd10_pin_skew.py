"""CPD-10 on real processes: a run's stored pin across the upgrade that changes what a new pin holds, and back.

CPD-10 adds dimension rows (the routing lists, two Labs stages, the capabilities authority), so a pin made by this
build holds rows a pin of the tree before it (``harness/cpd6`` ``e43676fb7``, real code through ``release_tree``)
does not, and digests differently. A stored pin is never re-digested: the worker checks a pin's digest over the pin
as stored and reads the rows it knows.

CPD10-85 a run pinned and claimed by the tree before, its worker killed mid-turn, Node and the worker upgraded: this
build claims it again on its stored pin, byte for byte, and completes it.
CPD10-86 the reverse (a rollback) onto the release before this build (N-1, ``release_tree.previous_ref``, which holds
CPD-10): pinned and claimed by this build, completed by N-1 on the same bytes. R-29 promises rollbacks to N-1 only.
CPD10-86b the same rollback onto the tree before CPD-10, which the skew window does not reach: it is refused, typed
(``WORKER_INCOMPATIBLE``: that worker lacks features the run requires), nothing of the run is touched, and rolled
forward again this build completes it (FUPERF-FU-1).
CPD10-80 Node first: this build's Node under ``on`` with the last release's worker, which reads no pin and an older
journal fold: the run is admitted without the pin requirement, waits (never failed, never refused for its pin), and
completes on the spec's legacy fields when a worker of this build arrives (QA18-2's shape, under ``on``).
CPD10-81 workers first: this build's worker under the last release's Node: no pin, no header, the run completes.
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
from test_upgrades import first_model_call, scenario_file
from test_version_skew import NODE_SWITCH_LEASE_MS, TERMINAL, Upgrade

pytestmark = pytest.mark.fault

# The tree CPD-10 was built on: the last one whose pins hold no CPD-10 row.
BEFORE_CPD10 = release_tree.before("CPD-10")
PIN = "CONTROL_PLANE_PIN"
RESOLVED_CONFIG = "resolved_config"
CPD10_ROW = "rollout.durable.modes"
# Longer than the suite's lease and three sweeps: a refused claim that would be retried into a failure would be by then.
HOLDS_FOR_S = 20


@pytest.fixture(scope="module")
def before() -> release_tree.ReleaseTree:
    return release_tree.extract(BEFORE_CPD10)


@pytest.fixture(scope="module")
def last_release() -> release_tree.ReleaseTree:
    return release_tree.extract(release_tree.release_ref())


@pytest.fixture(scope="module")
def previous() -> release_tree.ReleaseTree:
    return release_tree.extract(release_tree.previous_ref())


def _rig(fault_stack: stack.FaultStack, tree: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    fault_stack.node.kill9()
    rig = Upgrade(fault_stack, tree)
    rig.env[PIN] = "on"
    try:
        yield rig
    finally:
        rig.stop()
        fault_stack.node.start()


@pytest.fixture
def across_cpd10(fault_stack: stack.FaultStack, before: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    yield from _rig(fault_stack, before)


@pytest.fixture
def across_release(fault_stack: stack.FaultStack, last_release: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    yield from _rig(fault_stack, last_release)


@pytest.fixture
def across_previous(fault_stack: stack.FaultStack, previous: release_tree.ReleaseTree) -> Iterator[Upgrade]:
    yield from _rig(fault_stack, previous)


def _admit(name: str) -> tuple[str, str]:
    admitted: list[dict[str, Any]] = []

    def durable() -> bool:
        admission = stack.admit(f"flow SENTINEL-PROMPT-{name}", f"cpd10-{uuid.uuid4()}")
        if admission is not None:
            admitted.append(admission)
        return bool(admitted)

    stack.wait_for(durable, 60, "a durable admission")
    return str(admitted[0]["run"]["runId"]), str(admitted[0]["sessionId"])


def _run(run_id: str) -> dict[str, Any]:
    return stack.run_document(run_id) or {}


def _requires_pin(run: dict[str, Any]) -> bool:
    return RESOLVED_CONFIG in ((run.get("requires") or {}).get("features") or [])


def _calls(run_dir: Path, run_id: str) -> int:
    return sum(1 for c in stack.jsonl(run_dir / "model-calls.jsonl") if c["runId"] == run_id)


def _pinned_mid_turn(rig: Upgrade, run_dir: Path, name: str, *, pinned_by_release: bool, release_has_cpd10: bool
                     ) -> tuple[str, str, str, stack.Process]:
    """One side pins and claims a run; it is mid-turn (a model call made)."""
    scenario_file(run_dir, modelDelayS=3, leaseMs=NODE_SWITCH_LEASE_MS)
    rig.start_node(released=pinned_by_release)
    worker = rig.start_worker(run_dir, released=pinned_by_release)
    # The pin is required only once Node has seen a worker that reads it (the F2 rule).
    stack.wait_for(lambda: bool(stack.http("GET", f"/__fault/liveness/{RESOLVED_CONFIG}")["live"]), 90,
                   "a worker that reads pins seen by Node")
    run_id, session = _admit(name)
    first_model_call(run_dir, run_id)
    stored = _run(run_id)
    assert _requires_pin(stored), "the run was admitted without its pin: nothing to carry across"
    pin = stored["resolved"]
    assert isinstance(pin, str) and pin, "the pin is stored on the run as its canonical bytes"
    assert (CPD10_ROW in pin) is (release_has_cpd10 or not pinned_by_release), "which tree made the pin"
    return run_id, session, pin, worker


def _pinned_mid_turn_then_switched(rig: Upgrade, run_dir: Path, name: str, *, pinned_by_release: bool,
                                   release_has_cpd10: bool) -> tuple[str, str]:
    """One side pins and claims a run and its worker dies mid-turn; the other side's Node and worker take over."""
    run_id, _, pin, worker = _pinned_mid_turn(rig, run_dir, name, pinned_by_release=pinned_by_release,
                                              release_has_cpd10=release_has_cpd10)
    worker.kill9()
    rig.start_node(released=not pinned_by_release)
    rig.start_worker(run_dir, released=not pinned_by_release)
    stack.wait_for(lambda: _run(run_id).get("status") in TERMINAL, RECOVERY_TIMEOUT_S, "the run's end after the switch")
    return run_id, pin


def _completed_on(rig: Upgrade, run_dir: Path, run_id: str, pin: str) -> None:
    document = _run(run_id)
    assert (document["status"], document.get("outcome")) == ("completed", "answer"), document.get("status")
    assert document["resolved"] == pin, "the stored pin changed across the switch"
    assert document["attempts"] >= 2, "the second side never claimed it"
    assert _requires_pin(document), "the requirement was dropped: the run did not run on its pin"
    assert _calls(run_dir, run_id) >= 2, "the second side made no model call"
    logs = rig.node_log.read_text(errors="replace") + (run_dir / "worker.log").read_text(errors="replace")
    assert "resolved_config_invalid" not in logs


def test_cpd10_85_a_run_pinned_before_the_upgrade_completes_after_it_on_its_stored_pin(across_cpd10: Upgrade) -> None:
    run_dir = across_cpd10.fault_stack.out / "cpd10_pin_upgrade"
    run_id, pin = _pinned_mid_turn_then_switched(across_cpd10, run_dir, "pin-upgrade", pinned_by_release=True,
                                                 release_has_cpd10=False)
    _completed_on(across_cpd10, run_dir, run_id, pin)


def test_cpd10_86_a_run_pinned_by_this_build_completes_after_a_rollback_to_n_minus_1_on_the_same_pin(
    across_previous: Upgrade,
) -> None:
    run_dir = across_previous.fault_stack.out / "cpd10_pin_rollback"
    run_id, pin = _pinned_mid_turn_then_switched(across_previous, run_dir, "pin-rollback", pinned_by_release=False,
                                                 release_has_cpd10=True)
    _completed_on(across_previous, run_dir, run_id, pin)


# What a refused rollback must leave as it was. The status and lease may move (the sweeper hands an expired lease
# back to the queue); what the run is, and how often it was claimed, may not.
UNTOUCHED = ("resolved", "requires", "input", "spec", "attempts", "outcome")


def test_cpd10_86b_a_rollback_onto_the_tree_before_cpd10_is_refused_typed_and_touches_nothing(
    across_cpd10: Upgrade,
) -> None:
    rig = across_cpd10
    run_dir = rig.fault_stack.out / "cpd10_pin_rollback_pre_feature"
    run_id, session, pin, worker = _pinned_mid_turn(rig, run_dir, "pin-rollback-pre", pinned_by_release=False,
                                                    release_has_cpd10=False)
    worker.kill9()
    before = {key: _run(run_id).get(key) for key in UNTOUCHED}
    calls = _calls(run_dir, run_id)
    events = len(stack.journal(session))

    rig.start_node(released=True)
    older = rig.start_worker(run_dir, released=True)
    stack.wait_for(lambda: bool(rig.refused(run_id)), 90, "the tree before CPD-10 refusing its worker the run, typed")
    time.sleep(HOLDS_FOR_S)
    after = _run(run_id)
    assert after.get("status") not in TERMINAL, "a refused rollback ended the run"
    assert {key: after.get(key) for key in UNTOUCHED} == before, "a refused rollback changed the run"
    assert len(stack.journal(session)) == events, "a refused rollback wrote to the run's journal"
    assert _calls(run_dir, run_id) == calls, "the tree before CPD-10 ran a turn it cannot read"
    older.kill9()

    rig.start_node(released=False)
    rig.start_worker(run_dir, released=False)
    stack.wait_for(lambda: _run(run_id).get("status") in TERMINAL, RECOVERY_TIMEOUT_S, "the run's end, rolled forward")
    _completed_on(rig, run_dir, run_id, pin)


def test_cpd10_80_node_first_under_on_a_run_waits_for_a_worker_of_this_build_and_runs_on_its_legacy_fields(
    across_release: Upgrade,
) -> None:
    run_dir = across_release.fault_stack.out / "cpd10_node_first"
    scenario_file(run_dir)
    across_release.start_node(released=False)
    older = across_release.start_worker(run_dir, released=True)
    run_id, _ = _admit("node-first")
    stack.wait_for(lambda: "WORKER_INCOMPATIBLE" in across_release.node_log.read_text(errors="replace"), 60,
                   "the last release's worker asked for the run")
    waiting = _run(run_id)
    assert waiting["status"] not in TERMINAL, "a run no worker could read yet was ended"
    assert not _requires_pin(waiting), "a pin was required while no worker that reads one was live"
    assert _calls(run_dir, run_id) == 0
    older.kill9()

    across_release.start_worker(run_dir, released=False)
    stack.wait_for(lambda: _run(run_id).get("status") in TERMINAL, RECOVERY_TIMEOUT_S, "the run's end")
    document = _run(run_id)
    assert (document["status"], document.get("outcome")) == ("completed", "answer"), document.get("status")
    assert not _requires_pin(document), "the requirement was added after admission"
    assert _calls(run_dir, run_id) >= 1
    assert "resolved_config_invalid" not in (run_dir / "worker.log").read_text(errors="replace")


def test_cpd10_81_workers_first_this_worker_runs_a_turn_the_last_release_admitted(across_release: Upgrade) -> None:
    run_dir = across_release.fault_stack.out / "cpd10_workers_first"
    scenario_file(run_dir)
    across_release.start_node(released=True)
    across_release.start_worker(run_dir, released=False)
    run_id, _ = _admit("workers-first")
    stack.wait_for(lambda: _run(run_id).get("status") in TERMINAL, RECOVERY_TIMEOUT_S, "the run's end")
    document = _run(run_id)
    assert (document["status"], document.get("outcome")) == ("completed", "answer"), document.get("status")
    assert "resolved" not in document, "the last release pins nothing"
    assert _calls(run_dir, run_id) >= 1
