"""OPS1: a Node that starts while its store cannot be prepared (CPD-6 F-B), per broker.

Connected but not writable (the primary stepped down): the whole application
needs MongoDB, so the process keeps trying, serves nothing meanwhile (its port
refuses connections, which is what every probe of it sees), and becomes ready
by itself when the primary returns. Unreachable: the process exits non-zero for
its supervisor to start again. MongoDB answering while the harness alone cannot
prepare (an index build refused): Node serves, the harness reports itself
unavailable, and turns itself on once the cause is gone.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest

import stack
from test_crash_points import ONE_CALL_EACH, RECOVERY_TIMEOUT_S, Scenario, k12_problems, wait_for_final_disposition
from test_recovery_bound import SWEEP_INTERVAL_S, SWEEPER_LEASE_S, TERMINAL, ensure_running, holder_pid, leases
from test_upgrades import outcome_of, record, scenario_file

pytestmark = pytest.mark.fault

READY = "FAULT NODE READY"
NOT_PREPARED = "Agent harness store unreachable"
# Long enough that only the test ends it: the outage lasts until the Node has been seen trying again.
STEP_DOWN_S = 300
TRIES_SEEN = 3


def mongosh(script: str) -> None:
    stack.compose(
        "exec", "-T", "mongodb", "mongosh", "--quiet", "-u", "admin", "-p", "password", "--authenticationDatabase",
        "admin", "--eval", script, check=False,
    )


def primary_steps_down() -> None:
    mongosh(f"db.adminCommand({{replSetStepDown: {STEP_DOWN_S}, secondaryCatchUpPeriodSecs: 0, force: true}})")


def primary_returns() -> None:
    mongosh("db.adminCommand({replSetFreeze: 0})")


RECEIPTS, RECEIPT_INDEX = "agentCommandReceipts", "orgId_1_runId_1_commandId_1"
DUPLICATE = "{orgId: 'ops1-f3', runId: 'ops1-f3', commandId: 'ops1-f3'}"


def in_node_db(fault_stack: stack.FaultStack, script: str) -> None:
    mongosh(f"const d = db.getSiblingDB('{fault_stack.env['MONGO_DB_NAME']}'); {script}")


def chat_turn_status() -> int:
    request = urllib.request.Request(
        stack.NODE_URL + "/api/v1/conversations/stream", method="POST",
        data=json.dumps({"query": "SENTINEL-PROMPT-f3", "chatMode": "internal_search"}).encode(),
        headers={"authorization": f"Bearer {stack.ORG}.{stack.USER}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def accepts(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return False
    return True


def a_run_completes(fault_stack: stack.FaultStack, name: str) -> list[str]:
    run_dir = fault_stack.out / name
    scenario_file(run_dir)
    worker = stack.start_live_worker(fault_stack.env, run_dir)
    try:
        admission = stack.admit(f"flow SENTINEL-PROMPT-{name}", f"fault-{uuid.uuid4()}")
        run_id, session_id = admission["run"]["runId"], admission["sessionId"]
        stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("status") in TERMINAL, RECOVERY_TIMEOUT_S,
                       f"{run_id} terminal")
        wait_for_final_disposition(run_dir, run_id)
    finally:
        worker.kill9()
    outcome = outcome_of(run_id, session_id, run_dir)
    problems = k12_problems(Scenario(name, "flow", "none", "completed", ONE_CALL_EACH), outcome)
    return [*problems, *([] if outcome.claims == 1 else [f"claims {outcome.claims}"])]


def test_ops1_2_a_node_started_without_a_writable_store_waits_and_recovers_by_itself(fault_stack: stack.FaultStack) -> None:
    node, log = fault_stack.node, fault_stack.node_log
    assert node.term(30) is not None, "the Node did not stop"
    mark = stack.log_mark(log)
    failed: list[BaseException] = []

    def start() -> None:
        try:
            node.start(timeout=300)
        except BaseException as error:  # noqa: BLE001 - reported below
            failed.append(error)

    def logged(text: str) -> int:
        return sum(1 for r in stack.log_since("node", log, mark) if text in r.text)

    def ready() -> bool:
        with log.open("rb") as f:
            f.seek(mark)
            return READY.encode() in f.read()

    served_unprepared = 0
    starting = threading.Thread(target=start, daemon=True)
    try:
        primary_steps_down()
        starting.start()
        while starting.is_alive() and logged(NOT_PREPARED) < TRIES_SEEN:
            served_unprepared += accepts(stack.NODE_PORT)
            time.sleep(0.2)
        tries = logged(NOT_PREPARED)
        was_ready_during_outage = ready()
    finally:
        primary_returns()
    starting.join(300)
    try:
        assert failed == [] and not starting.is_alive(), failed
        assert node.proc is not None and node.proc.poll() is None
        started_pid = node.proc.pid
        # Its background services run: the lease was free (the stop released it), so this process leads.
        stack.wait_for(lambda: holder_pid(leases("none", stack.NODE_URL)["leader"]) == started_pid,
                       SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "the started Node leading the sweeper")
        problems = a_run_completes(fault_stack, "startup_store_not_writable")
    finally:
        ensure_running(node)
    record(fault_stack, {"scenario": "startup_store_not_writable", "triesSeenDuringOutage": tries,
                         "connectionsAcceptedDuringOutage": served_unprepared, "problems": problems})
    assert tries >= TRIES_SEEN, tries
    assert not was_ready_during_outage
    assert served_unprepared == 0, served_unprepared
    assert problems == [], problems


def test_ops1_2_a_node_started_without_a_reachable_store_exits_for_its_supervisor(fault_stack: stack.FaultStack) -> None:
    node = fault_stack.node
    assert node.term(30) is not None, "the Node did not stop"
    try:
        stack.compose("pause", "mongodb")
        with contextlib.ExitStack() as paused:
            paused.callback(stack.compose, "unpause", "mongodb", check=False)
            node.start(may_die=True, timeout=120)
            assert node.wait_dead(60), "the Node kept running without its store"
            assert node.proc is not None
            exit_code = node.proc.returncode
        node.start()
        problems = a_run_completes(fault_stack, "startup_store_unreachable")
    finally:
        ensure_running(node)
    record(fault_stack, {"scenario": "startup_store_unreachable", "exitCode": exit_code, "problems": problems})
    assert exit_code not in (0, None), exit_code
    assert problems == [], problems


def test_ops1_2_a_harness_that_cannot_prepare_leaves_node_serving_and_turns_itself_on(fault_stack: stack.FaultStack) -> None:
    """OPS1-2-F3: the unique receipt index cannot be built over two equal receipts; MongoDB itself is fine."""
    node, log = fault_stack.node, fault_stack.node_log
    assert node.term(30) is not None, "the Node did not stop"
    in_node_db(fault_stack, f"d.{RECEIPTS}.dropIndex('{RECEIPT_INDEX}'); d.{RECEIPTS}.insertMany([{DUPLICATE}, {DUPLICATE}])")
    try:
        mark = stack.log_mark(log)
        node.start()
        assert node.proc is not None
        started_pid = node.proc.pid
        unavailable = stack.http("GET", "/__fault/health")["agentHarness"]
        fell_back = chat_turn_status()

        in_node_db(fault_stack, f"d.{RECEIPTS}.deleteMany({DUPLICATE})")
        stack.wait_for(lambda: stack.http("GET", "/__fault/health")["agentHarness"] == {"status": "available"},
                       60, "the harness turned itself on")
        stack.wait_for(lambda: holder_pid(leases("none", stack.NODE_URL)["leader"]) == started_pid,
                       SWEEPER_LEASE_S + SWEEP_INTERVAL_S, "its background services running")
        turned_on = [r.event for r in stack.log_since("node", log, mark) if "Agent harness on" in r.text]
        problems = a_run_completes(fault_stack, "startup_harness_unavailable")
    finally:
        in_node_db(fault_stack, f"d.{RECEIPTS}.deleteMany({DUPLICATE})")
        ensure_running(node)
    record(fault_stack, {"scenario": "startup_harness_unavailable", "health": unavailable,
                         "chatTurnStatus": fell_back, "problems": problems})
    assert {k: unavailable[k] for k in ("status", "reason")} == {"status": "unavailable", "reason": "preparation_failed"}, unavailable
    assert "prepare the database" in unavailable["detail"], unavailable
    # The chat route's harness middleware steps aside: the turn reaches today's loop (the rig's stand-in answers 501).
    assert fell_back == 501, fell_back
    assert len(turned_on) == 1, turned_on
    assert problems == [], problems
