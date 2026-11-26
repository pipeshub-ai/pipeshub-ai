"""L4 for rolling upgrades (P1b T4) on real processes, per broker.

RU-5a: a rollback (N+1 -> N) while a run an N+1 worker touched is in flight.
Workers of the two releases differ by their advertisement (``foldVersion``);
the N workers must never claim the run (it waits, marked, never failed) and
an N+1 worker must complete it with the K-12 invariants.

RU-4a: a rolling deploy under load. Two Nodes behind a balancer and two
workers serve streaming runs admitted throughout; the workers, then the Nodes,
are replaced one at a time. Every run completes with the K-12 invariants and
the longest gap between frames any browser stream saw (reconnecting with
Last-Event-ID) stays under the stall bound.

RU-4c (CH-15): the same deploy with 100 sessions admitted and streaming before it starts; every one completes and
its stream finishes.

RU-6a (C-13): recovery after a full control-plane outage, measured from
Node's return to the run's next claim.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import stack
from test_crash_points import (
    LEASE_MS,
    ONE_CALL_EACH,
    RECOVERY_TIMEOUT_S,
    Outcome,
    Scenario,
    k12_problems,
    log_problems,
    save_journal,
    wait_for_final_disposition,
)

pytestmark = pytest.mark.fault

sys.path.insert(0, str(stack.PYTHON_ROOT))
from harness_core.kernel.fold import FOLD_VERSION  # noqa: E402

NEWER = {"foldVersion": FOLD_VERSION + 1}
# Longer than LIVENESS_GAP_MS (the sweeper's lease plus two ticks): Node was gone, not handed over.
OUTAGE_S = 45
# SLO #6 (restated in T4): runnable again within 60 s of the control plane's return.
RECOVERY_SLO_S = 60
# RU-4a: the longest gap between two frames a browser sees during a rolling deploy. Proposed bound: a
# model call (2 s here) plus a Node's replacement behind the balancer and the client's reconnect (SLO #8, 3 s).
ROLL_STALL_SLO_S = 15
ROLL_MODEL_DELAY_S = 2
ROLL_ADMIT_EVERY_S = 1.5


def scenario_file(run_dir: Path, **knobs: Any) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.json").write_text(json.dumps({"leaseMs": LEASE_MS, **knobs}))


def first_model_call(run_dir: Path, run_id: str) -> None:
    stack.wait_for(
        lambda: any(c["runId"] == run_id for c in stack.jsonl(run_dir / "model-calls.jsonl")), 60, "first model call"
    )


def outcome_of(run_id: str, session_id: str, run_dir: Path) -> Outcome:
    events = [e for e in stack.journal(session_id) if e.get("runId") == run_id]
    claims = sum(1 for e in events if e["type"] == "run.claimed")
    return Outcome(run_id, session_id, stack.run_document(run_id) or {}, events, run_dir, claims)


def node_lines(fault_stack: stack.FaultStack, event: str, run_id: str) -> list[stack.LogRecord]:
    return [
        r for r in stack.log_records("node", fault_stack.node_log.read_text(errors="replace"))
        if r.event == event and run_id in r.text
    ]


def roll(
    fault_stack: stack.FaultStack, node_b: stack.Process, workers: list[stack.Process], worker_env: dict[str, str],
    run_dir: Path,
) -> list[float | None]:
    """A rolling deploy (RU-4): the workers replaced one at a time, then the Nodes; each worker's drain time."""
    drains: list[float | None] = []
    for index, worker in enumerate(workers):
        drains.append(worker.term(45))
        workers[index] = stack.worker_process(worker_env, run_dir)
        workers[index].start()
        time.sleep(3)
    for node in (fault_stack.node, node_b):
        node.term(20)
        node.start()
        time.sleep(3)
    return drains


def record(fault_stack: stack.FaultStack, result: dict[str, Any]) -> None:
    with (fault_stack.out / "results.jsonl").open("a") as f:
        f.write(json.dumps({"broker": fault_stack.broker, **result}) + "\n")


def test_ru5a_a_rollback_leaves_a_newer_run_waiting_for_a_newer_worker(
    fault_stack: stack.FaultStack, credential: str
) -> None:
    scenario = Scenario("rollback_waits", "flow", "rollback", "completed", ONE_CALL_EACH,
                        knobs={"modelDelayS": 3})
    run_dir = fault_stack.out / scenario.name
    scenario_file(run_dir, modelDelayS=3, capabilities=NEWER)
    stack.advertise_worker(fault_stack.env, run_dir)
    admission = stack.admit(f"flow SENTINEL-PROMPT-{scenario.name}", f"fault-{uuid.uuid4()}")
    run_id, session_id = admission["run"]["runId"], admission["sessionId"]
    workers: list[stack.Process] = []
    try:
        newer = stack.worker_process(fault_stack.env, run_dir)
        workers.append(newer)
        newer.start()
        first_model_call(run_dir, run_id)
        assert newer.term(40) is not None, "the N+1 worker did not drain"

        # Rolled back: only N workers are left, polling and taking hints.
        scenario_file(run_dir, modelDelayS=3)
        older = stack.worker_process(fault_stack.env, run_dir)
        workers.append(older)
        older.start()
        stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("waitingForWorker") is not None, 30, "marked waiting")
        time.sleep(8)  # several polls of the N worker
        waited = stack.run_document(run_id) or {}
        assert waited.get("status") == "queued", waited.get("status")
        assert waited["waitingForWorker"]["requires"]["foldVersion"] == NEWER["foldVersion"]
        assert sum(1 for e in stack.journal(session_id) if e.get("runId") == run_id and e["type"] == "run.claimed") == 1

        # Rolled forward again: an N+1 worker takes it.
        scenario_file(run_dir, modelDelayS=3, capabilities=NEWER)
        again = stack.worker_process(fault_stack.env, run_dir)
        workers.append(again)
        again.start()
        stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("status") in {"completed", "failed", "cancelled"},
                       RECOVERY_TIMEOUT_S, f"{run_id} terminal")
        wait_for_final_disposition(run_dir, run_id)
    finally:
        for worker in workers:
            worker.kill9()
    outcome = outcome_of(run_id, session_id, run_dir)
    save_journal(fault_stack.broker, scenario.name, outcome)
    problems = k12_problems(scenario, outcome)
    filtered = node_lines(fault_stack, "claim.filtered", run_id)
    if len(filtered) != 1 or not re.search(rf"runFoldVersion\W+{NEWER['foldVersion']}\b", filtered[0].text):
        problems.append(f"claim.filtered lines {len(filtered)}")
    if outcome.run.get("waitingForWorker") is not None:
        problems.append("the mark outlived the claim that took the run")
    log = [p for p in log_problems(scenario, outcome, fault_stack.node_log, credential)
           if "sweeper.expired" not in p]
    record(fault_stack, {"scenario": scenario.name, "runId": run_id, "claims": outcome.claims,
                         "problems": problems, "logProblems": log})
    assert problems == [], problems
    assert log == [], log
    assert outcome.claims == 2


def _ts(event: dict[str, Any]) -> float:
    return datetime.fromisoformat(event["ts"].replace("Z", "+00:00")).timestamp()


def test_c13_recovery_after_a_full_control_plane_outage(fault_stack: stack.FaultStack, credential: str) -> None:
    scenario = Scenario("control_plane_outage", "slow", "node_restart", "completed", ONE_CALL_EACH,
                        knobs={"slowToolS": 5})
    run_dir = fault_stack.out / scenario.name
    scenario_file(run_dir, slowToolS=5)
    stack.advertise_worker(fault_stack.env, run_dir)
    admission = stack.admit(f"slow SENTINEL-PROMPT-{scenario.name}", f"fault-{uuid.uuid4()}")
    run_id, session_id = admission["run"]["runId"], admission["sessionId"]
    worker = stack.worker_process(fault_stack.env, run_dir)
    try:
        worker.start()
        stack.wait_for(lambda: (run_dir / "slow.started").exists(), 60, "slow tool started")
        fault_stack.node.kill9()
        time.sleep(OUTAGE_S)
        fault_stack.node.start()
        back = time.time()
        stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("status") in {"completed", "failed", "cancelled"},
                       400, f"{run_id} terminal")
        wait_for_final_disposition(run_dir, run_id)
    finally:
        worker.kill9()
    outcome = outcome_of(run_id, session_id, run_dir)
    save_journal(fault_stack.broker, scenario.name, outcome)
    claims = [e for e in outcome.events if e["type"] == "run.claimed"]
    recovered_s = _ts(claims[-1]) - back if len(claims) > 1 else None
    holdoffs = [r.text for r in stack.log_records("node", fault_stack.node_log.read_text(errors="replace"))
                if r.event in ("sweeper.holdoff", "sweeper.gap")]
    record(fault_stack, {"scenario": scenario.name, "runId": run_id, "claims": outcome.claims,
                         "recoveredAfterNodeReturnS": recovered_s, "outageS": OUTAGE_S, "gapLogs": len(holdoffs)})
    problems = k12_problems(scenario, outcome)
    assert problems == [], problems
    assert recovered_s is not None and recovered_s <= RECOVERY_SLO_S, recovered_s


def test_ru4a_a_rolling_deploy_under_load_stalls_no_stream_past_the_bound(
    fault_stack: stack.FaultStack, credential: str
) -> None:
    scenario = Scenario("rolling_deploy", "flow", "rolling_deploy", "completed", ONE_CALL_EACH,
                        knobs={"modelDelayS": ROLL_MODEL_DELAY_S})
    run_dir = fault_stack.out / scenario.name
    scenario_file(run_dir, modelDelayS=ROLL_MODEL_DELAY_S)
    node_b = stack.node_process(fault_stack.env, fault_stack.out / "node-b.log", port=stack.extra_port(1))
    balancer = stack.Balancer(stack.extra_port(2), [stack.NODE_PORT, stack.extra_port(1)])
    worker_env = {**fault_stack.env, "AGENT_HARNESS_STATE_URL": balancer.url}
    workers = [stack.worker_process(worker_env, run_dir) for _ in range(2)]
    streams: list[stack.ResumingStream] = []
    admitting = threading.Event()
    admitted_errors: list[str] = []
    on_legacy_route = 0
    node_logs = {log: stack.log_mark(log) for log in (fault_stack.node_log, fault_stack.out / "node-b.log")}

    def admit_while_rolling() -> None:
        nonlocal on_legacy_route
        while admitting.is_set():
            try:
                admission = stack.admit(f"flow SENTINEL-PROMPT-{scenario.name}", f"fault-{uuid.uuid4()}",
                                        base=balancer.url)
            except OSError as error:  # a Node was replaced mid-request: the browser retries
                admitted_errors.append(type(error).__name__)
                continue
            if admission is None:
                # A Node that has seen no live worker yet (it just started) answers on the legacy route (LAT1):
                # the turn runs on today's loop and there is no durable run to follow. Counted, not a failure.
                on_legacy_route += 1
                time.sleep(ROLL_ADMIT_EVERY_S)
                continue
            streams.append(stack.ResumingStream(balancer.url, admission["sessionId"], admission["run"]["runId"],
                                                RECOVERY_TIMEOUT_S * 2))
            time.sleep(ROLL_ADMIT_EVERY_S)

    def sample_leases() -> None:
        # CPD6-F-RU4A: who held the sweeper's leader lease, twice a second, through the deploy.
        while not sampled.wait(0.5):
            with contextlib.suppress(OSError, ValueError, KeyError), (run_dir / "leases.jsonl").open("a") as out:
                out.write(json.dumps(stack.http("GET", "/__fault/leases", base=balancer.url), default=str) + "\n")

    sampled = threading.Event()
    drains: list[float | None] = []
    try:
        node_b.start()
        for worker in workers:
            worker.start()
        admitting.set()
        admitter = threading.Thread(target=admit_while_rolling, daemon=True)
        admitter.start()
        threading.Thread(target=sample_leases, daemon=True).start()
        time.sleep(4)
        drains += roll(fault_stack, node_b, workers, worker_env, run_dir)
        admitting.clear()
        admitter.join(30)
        for stream in streams:
            stream.thread.join(RECOVERY_TIMEOUT_S)
        for stream in streams:
            stack.wait_for(lambda run_id=stream.run_id: (stack.run_document(run_id) or {}).get("status")
                           in {"completed", "failed", "cancelled"}, RECOVERY_TIMEOUT_S, f"{stream.run_id} terminal")
            wait_for_final_disposition(run_dir, stream.run_id)
    finally:
        admitting.clear()
        # What a stream that stalled past the bound looked like in the store, while the stack still answers.
        stalled = [s for s in streams if s.max_gap_s() > ROLL_STALL_SLO_S]
        with contextlib.suppress(OSError, ValueError, KeyError):
            if stalled:
                (run_dir / "stalled.json").write_text(json.dumps([
                    {"runId": s.run_id, "sessionId": s.session_id, "maxGapS": s.max_gap_s(),
                     "arrivals": s.arrivals, "started": s.started, "run": stack.run_document(s.run_id),
                     "events": [e for e in stack.journal(s.session_id) if e.get("runId") == s.run_id]}
                    for s in stalled
                ], indent=1, default=str))
        sampled.set()
        for worker in workers:
            worker.kill9()
        node_b.kill9()
        balancer.close()

    problems: list[str] = []
    for stream in streams:
        outcome = outcome_of(stream.run_id, stream.session_id, run_dir)
        problems += [f"{stream.run_id}: {p}" for p in k12_problems(scenario, outcome)]
        if not stream.finished:
            problems.append(f"{stream.run_id}: the stream never finished ({stream.error})")
    # OPS1: a stopping Node releases its leader lease and answers what it accepted before its store closes.
    for log, mark in node_logs.items():
        for line in stack.log_since("node", log, mark):
            if line.event == "sweeper.lease_release_failed" or "client was closed" in line.text:
                problems.append(f"{log.name}: {line.event}: the store closed under a stopping Node")
    gaps = sorted((round(s.max_gap_s(), 2) for s in streams), reverse=True)
    record(fault_stack, {"scenario": scenario.name, "runs": len(streams), "maxGapsS": gaps[:5],
                         "reconnects": sum(s.reconnects for s in streams), "workerDrainsS": drains,
                         "admissionRetries": len(admitted_errors), "onLegacyRoute": on_legacy_route, "problems": problems})
    assert len(streams) >= 8, len(streams)
    assert all(d is not None for d in drains), drains
    assert problems == [], problems
    assert gaps[0] <= ROLL_STALL_SLO_S, gaps


# CH-15: the deploy the test scenarios name, with 100 sessions active when it starts: admitted together, each run's
# model calls slow enough that none is done when the roll begins, and well inside a worker's 45 s drain.
ROLL_SESSIONS = 100
ROLL_100_MODEL_DELAY_S = 30
# A person holds at most 10 active runs (ADMISSION_QUOTAS.activeRunsPerUser) and an org 200: 100 sessions are ten
# people's, so each is admitted at once rather than waiting for one person's earlier runs to end.
ROLL_PEOPLE = tuple(f"66f6a6a6a6a6a6a6a6a6{index:04x}" for index in range(0xd0, 0xd0 + 10))


def test_ru4c_ch15_a_rolling_deploy_with_100_active_sessions_continues_every_one(
    fault_stack: stack.FaultStack, credential: str
) -> None:
    """CH-15: 100 sessions admitted and streaming before the deploy starts (each run is still working: its model
    calls are slow and the workers' claims queue), then the workers and the Nodes are replaced one at a time. Every
    session continues or resumes: each run completes with the K-12 invariants and each browser stream, reconnecting
    with Last-Event-ID, finishes; nothing worse than a reconnect is seen."""
    scenario = Scenario("rolling_deploy_100", "flow", "rolling_deploy", "completed", ONE_CALL_EACH,
                        knobs={"modelDelayS": ROLL_100_MODEL_DELAY_S})
    run_dir = fault_stack.out / scenario.name
    scenario_file(run_dir, modelDelayS=ROLL_100_MODEL_DELAY_S)
    node_b = stack.node_process(fault_stack.env, fault_stack.out / "node-b-100.log", port=stack.extra_port(1))
    balancer = stack.Balancer(stack.extra_port(2), [stack.NODE_PORT, stack.extra_port(1)])
    worker_env = {**fault_stack.env, "AGENT_HARNESS_STATE_URL": balancer.url}
    workers = [stack.worker_process(worker_env, run_dir) for _ in range(2)]
    streams: list[stack.ResumingStream] = []
    drains: list[float | None] = []
    active, admitted_in = 0, 0.0
    try:
        node_b.start()
        for worker in workers:
            worker.start()
        # Both Nodes have seen a live worker before the sessions open, so every admission is a durable run.
        stack.wait_for(lambda: stack.admit(f"flow SENTINEL-PROMPT-{scenario.name}-probe", f"fault-{uuid.uuid4()}",
                                           base=balancer.url) is not None, 60, "a durable admission")
        def open_session(index: int) -> stack.ResumingStream:
            person = ROLL_PEOPLE[index % len(ROLL_PEOPLE)]
            while True:
                try:
                    admission = stack.admit(f"flow SENTINEL-PROMPT-{scenario.name}", f"fault-{uuid.uuid4()}",
                                            base=balancer.url, user=person)
                except OSError:
                    continue
                if admission is not None:
                    return stack.ResumingStream(balancer.url, admission["sessionId"], admission["run"]["runId"],
                                                RECOVERY_TIMEOUT_S * 4, user=person)

        opening = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            streams += pool.map(open_session, range(ROLL_SESSIONS))
        admitted_in = round(time.monotonic() - opening, 1)
        active = sum(1 for s in streams if (stack.run_document(s.run_id) or {}).get("status")
                     not in {"completed", "failed", "cancelled"})
        drains = roll(fault_stack, node_b, workers, worker_env, run_dir)
        for stream in streams:
            stream.thread.join(RECOVERY_TIMEOUT_S * 4)
        for stream in streams:
            stack.wait_for(lambda run_id=stream.run_id: (stack.run_document(run_id) or {}).get("status")
                           in {"completed", "failed", "cancelled"}, RECOVERY_TIMEOUT_S, f"{stream.run_id} terminal")
            wait_for_final_disposition(run_dir, stream.run_id)
    finally:
        for worker in workers:
            worker.kill9()
        node_b.kill9()
        balancer.close()

    problems: list[str] = []
    for stream in streams:
        outcome = outcome_of(stream.run_id, stream.session_id, run_dir)
        problems += [f"{stream.run_id}: {p}" for p in k12_problems(scenario, outcome)]
        if not stream.finished:
            problems.append(f"{stream.run_id}: the stream never finished ({stream.error})")
    record(fault_stack, {"scenario": scenario.name, "runs": len(streams), "admittedInS": admitted_in, "activeAtDeploy": active,
                         "reconnects": sum(s.reconnects for s in streams), "workerDrainsS": drains,
                         "problems": problems})
    assert len(streams) == ROLL_SESSIONS
    # The deploy met active sessions, not a drained queue.
    assert active >= ROLL_SESSIONS // 2, active
    assert all(d is not None for d in drains), drains
    assert problems == [], problems[:10]
