"""Degraded dependencies while runs are in flight, per broker (QA-17; 03 §5 DD-1..DD-4, DD-8, DD-11..DD-13, DD-16).

Each scenario starts a worker, admits a run and follows it as the user's
browser does, degrades one dependency at a point the run's gate pins (or before
the run, for the model provider), restores it, and checks C-1..C-5:
what the user saw, how the run ended, no duplicate
effect, a valid journal history, clean logs.
"""

from __future__ import annotations

import collections
import contextlib
import json
import os
import time
import urllib.error
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pytest

import stack
from degraded import LEASE_MS, RAW, Expect, Rig, Run, start_rig, stop_rig, wait_port

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

pytestmark = pytest.mark.fault

COMPLETED = Expect("completed")
# SLO #7 with Redis down: a Stop lands within 20 s.
CANCEL_BOUND_S = 20
# LAT1: with its run-ready listener down, the claimer's longest idle back-off is 5 s.
POLL_BACKSTOP_S = 5
# The store's client reconnects by itself: how long an admission may still be refused after the store is back.
RECOVERS_WITHIN_S = 30


@pytest.fixture(scope="session")
def rig(broker: str, infrastructure: None, artifacts: Path) -> Iterator[Rig]:
    out = artifacts / broker / "degraded"
    out.mkdir(parents=True, exist_ok=True)
    started = start_rig(broker, out)
    yield started
    stop_rig(started)


@contextlib.contextmanager
def scenario_run(rig: Rig, name: str, query: str, **knobs: object) -> Iterator[Run]:
    run_dir = rig.scenario(name, **knobs)
    worker = rig.start_worker(run_dir)
    try:
        run = rig.admit(run_dir, query, worker)
    except BaseException:
        worker.kill9()
        raise
    try:
        yield run
    finally:
        rig.finish(run)


def events_of(rig: Rig, run: Run) -> list[dict[str, Any]]:
    return [e for e in stack.journal(run.session_id, base=rig.base) if e.get("runId") == run.run_id]


def outcomes(rig: Rig, run: Run, tool: str) -> list[str]:
    """The run's journaled outcome statuses of ``tool``, in order."""
    events = events_of(rig, run)
    ids = {e["data"]["invocationId"]: str(e["data"].get("toolId", "")) for e in events if e["type"] == "tool.intent"}
    return [e["data"]["status"] for e in events
            if e["type"] == "tool.outcome" and ids.get(e["data"]["invocationId"], "").endswith(tool)]


def worker_events(run: Run, event: str) -> list[stack.LogRecord]:
    return [r for r in stack.log_records("worker", (run.run_dir / "worker.log").read_text(errors="replace"))
            if r.event == event]


def node_events(rig: Rig, event: str) -> list[stack.LogRecord]:
    return [r for r in stack.log_records("node", rig.node_log.read_text(errors="replace")) if r.event == event]


def felt(run: Run, *events: str) -> bool:
    return any(worker_events(run, e) for e in events)


# ---- Mongo ----


def mongo_step_down() -> None:
    """The primary steps down; a single-node set elects it again once its 10 s step-down ends."""
    stack.compose(
        "exec", "-T", "mongodb", "mongosh", "--quiet", "-u", "admin", "-p", "password", "--authenticationDatabase",
        "admin", "--eval", "db.adminCommand({replSetStepDown: 10, secondaryCatchUpPeriodSecs: 0, force: true})",
        check=False,
    )


def test_ix_dd_1_mongo_primary_steps_down_while_the_run_commits(rig: Rig) -> None:
    with scenario_run(rig, "mongo_step_down", "flow", gateTurn=2) as run:
        rig.gate_reached(run, 2)
        mongo_step_down()
        rig.open_gate(run, 2)
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)


def test_ix_dd_2a_mongo_unreachable_mid_run(rig: Rig) -> None:
    with scenario_run(rig, "mongo_down", "flow", gateTurn=2) as run:
        rig.gate_reached(run, 2)
        with rig.toxi.down("mongo"):
            rig.open_gate(run, 2)
            stack.wait_for(lambda: felt(run, "journal.unavailable", "journal.unreachable", "journal.renew_failed"),
                           60, "the worker felt the outage")
            time.sleep(LEASE_MS / 1000 + 2)  # outlast the lease, as a real outage would
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)


def test_ix_dd_2b_a_turn_sent_while_mongo_is_unreachable(rig: Rig) -> None:
    run_dir = rig.scenario("mongo_down_admission")
    worker = rig.start_worker(run_dir)
    try:
        with rig.toxi.down("mongo"):
            started = time.monotonic()
            try:
                answer: Any = stack.admit("answer SENTINEL-PROMPT-mongo_down_admission", "degraded-mongo-down", base=rig.base)
            except stack.urllib.error.HTTPError as refused:
                answer = {"status": refused.code, "body": refused.read().decode(errors="replace")}
            waited = time.monotonic() - started
        assert waited < 30, f"the refusal took {waited:.1f} s"
        assert isinstance(answer, dict) and answer.get("status") in (500, 503), answer
        assert RAW.search(answer["body"]) is None, answer["body"]
        run = rig.admit(run_dir, "answer", worker)
        rig.wait_terminal(run)
    finally:
        worker.kill9()
    rig.assert_clean(run, Expect("completed", writes=False))


def test_ix_dd_16a_mongo_slow(rig: Rig) -> None:
    with rig.toxi.toxic("mongo", "latency", latency=100), scenario_run(rig, "mongo_slow", "flow") as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)
    claims = [e for e in events_of(rig, run) if e["type"] == "run.claimed"]
    assert len(claims) == 1, f"{len(claims)} claims: a slow store must not cost the lease"


# ---- Redis ----


def test_ix_dd_3a_redis_down_mid_run(rig: Rig) -> None:
    with scenario_run(rig, "redis_down", "flow", gateTurn=2) as run:
        rig.gate_reached(run, 2)
        with rig.toxi.down("redis"):
            rig.open_gate(run, 2)
            rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)


def test_ix_dd_3b_run_ready_degrades_to_polling_while_redis_is_down(rig: Rig) -> None:
    run_dir = rig.scenario("redis_down_admission")
    worker = rig.start_worker(run_dir)
    try:
        with rig.toxi.down("redis"):
            stack.wait_for(lambda: bool(worker_events_at(run_dir, "slots.ready_unavailable")), 60,
                           "the worker's run-ready listener saw Redis go")
            run = rig.admit(run_dir, "answer", worker)
            rig.wait_terminal(run)
    finally:
        worker.kill9()
    rig.assert_clean(run, Expect("completed", writes=False))
    failed = [r for r in node_events(rig, "run_ready.publish_failed") if run.run_id in r.text]
    assert failed, "Node did not log the notice it could not publish"
    claimed = [r for r in worker_events(run, "slots.claimed") if run.run_id in r.text]
    assert claimed and "via=poll" in claimed[0].text, [c.text for c in claimed]


def worker_events_at(run_dir: Path, event: str) -> list[stack.LogRecord]:
    log = run_dir / "worker.log"
    return [r for r in stack.log_records("worker", log.read_text(errors="replace")) if r.event == event] if log.exists() else []


def test_ix_dd_3c_stop_lands_within_the_slo_while_redis_is_down(rig: Rig) -> None:
    with scenario_run(rig, "redis_down_cancel", "flow", gateTurn=1) as run:
        rig.gate_reached(run, 1)
        with rig.toxi.down("redis"):
            asked = time.monotonic()
            stack.cancel(run.session_id, run.run_id, base=rig.base)
            stack.wait_for(lambda: rig.status(run) == "cancelled", CANCEL_BOUND_S, "cancelled within SLO #7")
            took = time.monotonic() - asked
            rig.wait_terminal(run)
    assert took <= CANCEL_BOUND_S
    rig.assert_clean(run, Expect("cancelled", writes=False))


# A person no pin was made for yet, so their first admission must read the org's policy documents (the snapshot
# is kept per org, agent and actor).
NEW_ACTOR = "66f6a6a6a6a6a6a6a6a6a6c7"


@pytest.mark.skipif(os.environ.get("CONTROL_PLANE_PIN") != "on", reason="the pin decides admission in `on` only")
def test_cpd6_38_an_admission_whose_pin_cannot_be_made_is_refused_by_its_code_and_recovers(rig: Rig) -> None:
    """``CONTROL_PLANE_PIN=on`` with the configuration store down: the run's policy cannot be resolved, so nothing is
    started (never a run on unresolved policy), the caller is told so by a code the UI words, and the next admission
    after the store returns is admitted with nobody restarting anything."""
    run_dir = rig.scenario("redis_down_pin")
    worker = rig.start_worker(run_dir)
    try:
        with rig.toxi.down("redis"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                stack.admit("answer", f"degraded-{uuid.uuid4()}", base=rig.base, user=NEW_ACTOR)
            assert refused.value.code == 503, refused.value.code
            error = json.loads(refused.value.read())["error"]
            assert error["code"] == "HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE", error
        refusals = [r for r in node_events(rig, "admission.refused") if NEW_ACTOR in r.text]
        assert refusals and '"reason": "unavailable"' in refusals[-1].text, [r.text for r in refusals]
        assert not [r for r in node_events(rig, "admission.accepted") if NEW_ACTOR in r.text], "a run was started"

        admission: dict[str, Any] = {}

        def admitted() -> bool:
            with contextlib.suppress(urllib.error.HTTPError):
                admission.update(
                    stack.admit(
                        f"answer SENTINEL-PROMPT-{run_dir.name}", f"degraded-{uuid.uuid4()}", base=rig.base,
                        user=NEW_ACTOR,
                    )
                )
            return bool(admission.get("run"))

        stack.wait_for(admitted, RECOVERS_WITHIN_S, "an admission once the configuration store is back")
        stream = stack.ResumingStream(
            rig.base, admission["sessionId"], admission["run"]["runId"], deadline_s=300, user=NEW_ACTOR
        )
        run = Run(admission["run"]["runId"], admission["sessionId"], run_dir, stream, worker, [worker])
        rig.wait_terminal(run)
    finally:
        worker.kill9()
    rig.assert_clean(run, Expect("completed", writes=False))


def test_ix_dd_16b_redis_slow(rig: Rig) -> None:
    with rig.toxi.toxic("redis", "latency", latency=200), scenario_run(rig, "redis_slow", "flow") as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)


def test_ix_dd_3d_redis_fails_over_to_a_promoted_replica(rig: Rig) -> None:
    with scenario_run(rig, "redis_failover", "flow", gateTurn=2) as run:
        rig.gate_reached(run, 2)
        stack.compose("exec", "-T", "redis-replica", "redis-cli", "REPLICAOF", "NO", "ONE")
        rig.toxi.upstream("redis", rig.ports.redis_replica)
        rig.open_gate(run, 2)
        rig.wait_terminal(run)
        after = rig.admit(run.run_dir, "answer", run.worker)
        rig.wait_terminal(after)
    rig.assert_clean(run, COMPLETED)
    rig.assert_clean(after, Expect("completed", writes=False))
    assert not [r for r in node_events(rig, "run_ready.publish_failed") if after.run_id in r.text]


# ---- the broker ----


def test_ix_dd_4_broker_down(rig: Rig) -> None:
    if rig.broker == "redis":
        pytest.skip("the Redis Streams broker is the Redis IX-DD-3a/3b take down")
    run_dir = rig.scenario("broker_down")
    worker = rig.start_worker(run_dir)
    runs: list[Run] = []
    try:
        stack.compose("stop", "kafka")
        try:
            runs = [rig.admit(run_dir, "answer", worker) for _ in range(2)]
            for run in runs:
                rig.wait_terminal(run)
            backlog = stack.http("GET", "/__fault/outbox", base=rig.base)["backlog"]
            assert backlog["pending"] + backlog["publishing"] + backlog["failed"] > 0, backlog
        finally:
            stack.compose("--profile", "kafka", "up", "-d", "--wait", "kafka")
        stack.wait_for(lambda: _drained(rig), 120, "the outbox drained")
    finally:
        worker.kill9()
    for run in runs:
        rig.assert_clean(run, Expect("completed", writes=False))
        claims = [e for e in events_of(rig, run) if e["type"] == "run.claimed"]
        assert len(claims) == 1, f"{run.run_id} claimed {len(claims)} times"


def _drained(rig: Rig) -> bool:
    backlog = stack.http("GET", "/__fault/outbox", base=rig.base)["backlog"]
    return backlog["pending"] == backlog["publishing"] == backlog["failed"] == 0


# ---- the model provider ----


def model_run(rig: Rig, name: str, **stream_timeouts: float) -> contextlib.AbstractContextManager[Run]:
    return scenario_run(rig, name, "llm", model=rig.model(**stream_timeouts))


def model_calls(run: Run) -> dict[int, int]:
    calls = stack.jsonl(run.run_dir / "model-calls.jsonl")
    return dict(collections.Counter(c["turn"] for c in calls if c["runId"] == run.run_id))


def test_ix_dd_11a_provider_rate_limits(rig: Rig) -> None:
    """ERR-UX2: the provider's Retry-After (7 s) is the wait, not the default 20 s, and the stream shows it."""
    rig.plan_llm([{"status": 429, "headers": {"Retry-After": "7"}}])
    with model_run(rig, "llm_429") as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)
    yielded = [e for e in events_of(rig, run) if e["type"] == "run.yielded"]
    assert [e["data"]["reason"] for e in yielded] == ["llm_backoff"], yielded
    wait_s = (datetime.fromisoformat(yielded[0]["data"]["notBefore"]) - datetime.fromisoformat(yielded[0]["ts"])).total_seconds()
    assert 5 <= wait_s <= 9, f"the yield waits the provider's 7 s, not the default: {wait_s}"
    waits = [f["snapshot"] for f in run.stream.frames
             if f.get("type") == "STATE_SNAPSHOT" and (f.get("snapshot") or {}).get("status") == "waiting_provider"]
    assert [bool(w.get("retry_at")) for w in waits] == [True], "QA17-O3: the owner's stream shows the provider wait"


def test_ix_dd_11b_provider_5xx_then_recovers(rig: Rig) -> None:
    rig.plan_llm([{"status": 500}, {"status": 503}])
    with model_run(rig, "llm_5xx_transient") as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)
    assert model_calls(run) == {1: 3, 2: 1}, model_calls(run)


def test_ix_dd_11c_provider_5xx_every_time(rig: Rig) -> None:
    rig.plan_llm([], then={"status": 500})
    try:
        with model_run(rig, "llm_5xx") as run:
            rig.wait_terminal(run)
    finally:
        rig.plan_llm([])
    rig.assert_clean(run, Expect("failed", code="server_error", writes=False))


def test_ix_dd_11d_provider_stalls(rig: Rig) -> None:
    with rig.toxi.toxic("llm", "timeout", timeout=0), model_run(rig, "llm_stall", first_event_s=3, idle_s=3) as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, Expect("failed", code="timeout", writes=False))
    assert model_calls(run) == {1: 3}, model_calls(run)


def test_ix_dd_11e_provider_streams_slowly(rig: Rig) -> None:
    with rig.toxi.toxic("llm", "slicer", average_size=16, size_variation=8, delay=40_000), \
            model_run(rig, "llm_slow", first_event_s=10, idle_s=3) as run:
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)
    assert model_calls(run) == {1: 1, 2: 1}, model_calls(run)


# ---- what a run's tools reach ----


@contextlib.contextmanager
def killed(rig: Rig, name: str) -> Iterator[None]:
    """The fake process killed, as a crash would, and started again afterwards."""
    process = rig.processes[name]
    process.kill9()
    try:
        yield
    finally:
        process.start()
        wait_port(getattr(rig.ports, name))


# (case, fault, the code the failed call carries). The embedding client retries a refused connection with back-off,
# which outlasts the rig's call deadline (RIG_CALL_S), so its read ends as the call's timeout.
DEPENDENCY_FAULTS = {
    "graph_lookup": ("IX-DD-13a", lambda rig: rig.toxi.down("graph"), "unavailable"),
    "vector_search": ("IX-DD-13b", lambda rig: rig.toxi.down("vector"), "unavailable"),
    "embed_query": ("IX-DD-13c", lambda rig: rig.toxi.down("embedding"), "timeout"),
    "mcp_echo": ("IX-DD-12", lambda rig: killed(rig, "mcp"), "unavailable"),
    "sandbox_exec": ("IX-DD-8", lambda rig: killed(rig, "sandbox"), "unavailable"),
}


@pytest.mark.parametrize("tool", list(DEPENDENCY_FAULTS), ids=lambda t: DEPENDENCY_FAULTS[t][0])
def test_a_tools_dependency_goes_away_mid_run(rig: Rig, tool: str) -> None:
    """QA17-O1/O4 (ERR-UX): the failed call reaches the user as its code, never its exception text."""
    _, fault, code = DEPENDENCY_FAULTS[tool]
    run = degraded_dependency(rig, tool, fault, f"dep_{tool}")
    assert outcome_codes(rig, run, tool) == [None, code], outcome_codes(rig, run, tool)


# A store that accepts the connection and never answers (toxiproxy's `timeout` toxic with no limit). Upstream: the
# request never reaches the server, which keeps the client's pooled connection open (a downstream toxic let the
# server drop it, and the client read that as a disconnect).
DEPENDENCY_BLACK_HOLES = {
    "graph_lookup": ("IX-DD-13a-bh", "graph"),
    "vector_search": ("IX-DD-13b-bh", "vector"),
    "embed_query": ("IX-DD-13c-bh", "embedding"),
    "mcp_echo": ("IX-DD-12-bh", "mcp"),
    "sandbox_exec": ("IX-DD-8-bh", "sandbox"),
}


@pytest.mark.parametrize("tool", list(DEPENDENCY_BLACK_HOLES), ids=lambda t: DEPENDENCY_BLACK_HOLES[t][0])
def test_a_tools_dependency_black_holes_mid_run(rig: Rig, tool: str) -> None:
    """QA17-O5 (ERR-UX): the read ends at the call's deadline with a typed timeout, not the client's own minutes
    (Qdrant 300 s, the embedding client 600 s x 5, Neo4j unbounded): within the wait for its outcomes."""
    proxy = DEPENDENCY_BLACK_HOLES[tool][1]
    run = degraded_dependency(
        rig, tool, lambda rig: rig.toxi.toxic(proxy, "timeout", stream="upstream", timeout=0), f"bh_{tool}",
    )
    assert outcome_codes(rig, run, tool) == [None, "timeout"], outcome_codes(rig, run, tool)


def degraded_dependency(
    rig: Rig, tool: str, fault: Callable[[Rig], contextlib.AbstractContextManager[None]], name: str,
) -> Run:
    with scenario_run(rig, name, f"dep {tool}", gateTurn=2) as run:
        rig.gate_reached(run, 2)
        with fault(rig):
            rig.open_gate(run, 2)
            stack.wait_for(lambda: len(outcomes(rig, run, tool)) >= 2, 180, f"{tool} ran degraded")
        rig.wait_terminal(run)
    rig.assert_clean(run, COMPLETED)
    assert outcomes(rig, run, tool) == ["ok", "error"], outcomes(rig, run, tool)
    return run


def outcome_codes(rig: Rig, run: Run, tool: str) -> list[str | None]:
    """The ``errorCode`` each of ``tool``'s outcomes carries beside its detail (ERR-UX)."""
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.journal.inline import inline_bytes

    events = events_of(rig, run)
    ids = {e["data"]["invocationId"]: str(e["data"].get("toolId", "")) for e in events if e["type"] == "tool.intent"}
    codes: list[str | None] = []
    for e in events:
        if e["type"] == "tool.outcome" and ids.get(e["data"]["invocationId"], "").endswith(tool):
            data = inline_bytes(ContentRef.model_validate(e["data"]["resultRef"]))
            codes.append(json.loads(data).get("errorCode") if data is not None else "not inline")
    return codes
