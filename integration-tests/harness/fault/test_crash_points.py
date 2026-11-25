"""L4 crash points on real processes, per broker (TEST-STRATEGY 6.1; P1a gate criterion 4).

Each scenario admits one run over the public session route, crashes the worker
(a failpoint ``kill9`` or an external SIGKILL) or Node at its point, restarts
the process, lets recovery run (lease expiry -> sweeper -> re-claim by broker
hint or poll), and checks what the real stack persisted:

K-12 invariants
  (a) no duplicate external effect (the fsynced SaaS log, per idempotency key
      or invocation);
  (b) no re-sample of a journaled model response: model calls per turn are
      exactly what the crash point allows, and one ``model.response`` per turn;
  (c) the final kernel transcript equals an independent projection of the
      journal read back from Node with sealed content opened;
  (d) no dangling tool call.

Ground rule 9: the run can be followed by its ``runId`` in Node's and the
worker's logs (admission, claims, sweeper expiry, commits, the terminal
transition), and no log line holds content, a token, a credential or the key.
"""

from __future__ import annotations

import collections
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import stack

pytestmark = pytest.mark.fault

LEASE_MS = 10_000
RECOVERY_TIMEOUT_S = 120


@dataclass(frozen=True)
class Scenario:
    name: str
    script: str
    crash: str  # "failpoint", "kill_on_model_call" or "node_restart"
    expected_status: str
    model_calls_per_turn: dict[int, int]
    failpoint: str | None = None
    nth: int = 1
    knobs: dict[str, float] = field(default_factory=dict)
    # The kernel's own record of the last attempt; the settle-ack crash dies before it is written.
    last_attempt_recorded: bool = True
    # "chat": the built-in assistant, admitted on the conversations route; it reads and answers.
    # "mcp", "slack": the same, sent by that surface to its legacy stream route, which streams it back (C-5, C-20).
    route: str = "agent"
    # P1b T2: a message is sent to the run while turn 1 streams, before the crash.
    steers: bool = False

    @property
    def writes(self) -> bool:
        return self.route == "agent" and not self.steers


ONE_CALL_EACH = {1: 1, 2: 1, 3: 1}
# The first model call waits at the worker's gate until the checker opens it, so a kill "while it samples" lands
# while it samples however long the kill takes to arrive (a stalled host once let a timed call finish first).
HELD_FIRST_CALL = {"gateTurn": 1}
SCENARIOS = [
    Scenario("claim_kill", "flow", "kill_on_model_call", "completed", {1: 2, 2: 1, 3: 1}, knobs=HELD_FIRST_CALL),
    Scenario("model_before_commit", "flow", "failpoint", "completed", {1: 2, 2: 1, 3: 1}, "worker.model.before_response_commit"),
    Scenario("model_after_commit", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.model.after_response_commit"),
    Scenario("read_before_outcome", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.tool.before_outcome_commit"),
    Scenario("write_after_intent", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.tool.after_intent"),
    Scenario("write_after_effect_idempotent", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.tool.after_effect"),
    # K-4b: the effect of a non-idempotent write is unknown, so the run suspends for a decision, never a blind retry.
    Scenario("write_after_effect_not_idempotent", "nonidem", "failpoint", "waiting_approval", {1: 1}, "worker.tool.after_effect"),
    Scenario("after_outcome_ack", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.commit.after_ack", nth=5),
    Scenario("after_settle_ack", "flow", "failpoint", "completed", ONE_CALL_EACH, "worker.commit.after_ack", nth=6, last_attempt_recorded=False),
    Scenario("node_restart_mid_run", "slow", "node_restart", "completed", ONE_CALL_EACH, knobs={"slowToolS": 10}),
    # P1b T1a (PC-4): a plain-chat run, killed while it samples, before its answer commits, and before its read commits.
    Scenario("chat_claim_kill", "chat", "kill_on_model_call", "completed", {1: 2, 2: 1}, knobs=HELD_FIRST_CALL, route="chat"),
    Scenario("chat_answer_before_commit", "chat", "failpoint", "completed", {1: 1, 2: 2},
             "worker.model.before_response_commit", nth=2, route="chat"),
    Scenario("chat_read_before_outcome", "chat", "failpoint", "completed", {1: 1, 2: 1},
             "worker.tool.before_outcome_commit", route="chat"),
    # P1b T1b (SF-9): the MCP surface's turn, its response open while the worker is killed and the run recovers.
    Scenario("mcp_surface_claim_kill", "chat", "kill_on_model_call", "completed", {1: 2, 2: 1},
             knobs=HELD_FIRST_CALL, route="mcp"),
    # P1b T1e (C-20): the Slack bot's turn, streamed the slack audience across the same crash.
    Scenario("slack_surface_claim_kill", "chat", "kill_on_model_call", "completed", {1: 2, 2: 1},
             knobs=HELD_FIRST_CALL, route="slack"),
    # P1b T2 (ST-4): the user's message is queued while turn 1 streams; the worker dies just before, or just
    # after, the boundary that takes it. Either way the run takes it once and the next model call sees it once.
    Scenario("steer_before_consume", "steer", "failpoint", "completed", {1: 1, 2: 1},
             "worker.input.before_consume_commit", steers=True),
    Scenario("steer_after_consume", "steer", "failpoint", "completed", {1: 1, 2: 1},
             "worker.input.after_consume_commit", steers=True),
    # P1b T4 (RU-1): SIGTERM, as a rolling deploy stops a worker. The attempt finishes the boundary in progress
    # (the model call, or the slow read), commits it and yields; a new worker resumes without a re-sample.
    Scenario("drain_mid_model", "flow", "drain_on_model_call", "completed", ONE_CALL_EACH, knobs={"modelDelayS": 4}),
    Scenario("drain_mid_tool", "slow", "drain_on_slow_tool", "completed", ONE_CALL_EACH, knobs={"slowToolS": 4}),
    # The first model call outlasts the drain bound: the claim is handed back at once, not held for its
    # lease, and the next worker re-samples that turn (a crash's recovery without the wait).
    Scenario("drain_timeout", "flow", "drain_on_model_call", "completed", {1: 2, 2: 1, 3: 1},
             knobs={"firstCallDelayS": 30, "drainS": 2}),
]
DRAINS = ("drain_on_model_call", "drain_on_slow_tool")
DRAIN_EXIT_MARGIN_S = 10

_ANSWER_FRAMES = {"CUSTOM", "RUN_STARTED", "RUN_ERROR", "RUN_FINISHED", "STATE_DELTA", "STATE_SNAPSHOT"}
# What each surface's audience may receive (REPORT 8; C-5, C-20): MCP the answer, Slack what its bot renders.
SURFACE_FRAMES = {
    "mcp": _ANSWER_FRAMES,
    "slack": _ANSWER_FRAMES | {"TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "REASONING_MESSAGE_CONTENT",
                               "REASONING_END", "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_RESULT"},
}
SURFACE_AUDIENCE = {"mcp": "external", "slack": "slack"}

STEER_TEXT = "also cover SENTINEL-STEER"


@dataclass
class Outcome:
    run_id: str
    session_id: str
    run: dict[str, Any]
    events: list[dict[str, Any]]
    run_dir: Path
    claims: int
    surface: stack.SurfaceTurn | None = None
    # A drain: seconds from SIGTERM to exit, and the run's status once the worker was gone.
    drained_in: float | None = None
    status_after_drain: str | None = None


def run_scenario(fault_stack: stack.FaultStack, scenario: Scenario) -> Outcome:
    run_dir = fault_stack.out / scenario.name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.json").write_text(json.dumps({"leaseMs": LEASE_MS, **scenario.knobs}))
    for gate in run_dir.glob("gate-*"):
        gate.unlink()  # an earlier session's, in a reused artifacts directory
    arms = run_dir / "failpoints.json"
    if scenario.failpoint is not None:
        trigger: object = {"nth": scenario.nth} if scenario.nth > 1 else "once"
        arms.write_text(json.dumps({scenario.failpoint: {"action": "kill9", "trigger": trigger}}))

    query = f"{scenario.script} SENTINEL-PROMPT-{scenario.name}"
    worker = stack.start_live_worker(fault_stack.env, run_dir, may_die=scenario.crash == "failpoint")
    drained_in: float | None = None
    status_after_drain: str | None = None
    surface: stack.SurfaceTurn | None = None
    try:
        if scenario.route in SURFACE_FRAMES:
            surface = stack.SurfaceTurn(query, scenario.route)
            stack.wait_for(lambda: surface.session_id() is not None, 60, "conversation_created on the surface stream")
            session_id = surface.session_id() or ""
            run_id = next(e["runId"] for e in stack.journal(session_id) if e["type"] == "run.admitted")
        else:
            admission = stack.admit(query, f"fault-{uuid.uuid4()}", route=scenario.route)
            run_id, session_id = admission["run"]["runId"], admission["sessionId"]
        if scenario.steers:
            # Sent while the run's read runs (the read waits for it), so the next boundary takes it.
            stack.wait_for(lambda: (run_dir / "steer.ready").exists(), 60, "the read started")
            queued = stack.steer(session_id, STEER_TEXT, f"steer-{uuid.uuid4()}")
            assert queued["run"]["runId"] == run_id and queued["queued"] == {"messageSeq": 1}, queued
            (run_dir / "steer.sent").write_text(run_id)
        if scenario.crash == "failpoint":
            assert worker.wait_dead(60), f"{scenario.failpoint} did not kill the worker"
            arms.unlink()
            worker.start()
        elif scenario.crash == "kill_on_model_call":
            stack.wait_for(
                lambda: any(c["runId"] == run_id for c in stack.jsonl(run_dir / "model-calls.jsonl")), 60, "first model call"
            )
            worker.kill9()
            (run_dir / "gate-1.open").write_text(run_id)
            worker.start()
        elif scenario.crash in DRAINS:
            if scenario.crash == "drain_on_model_call":
                stack.wait_for(
                    lambda: any(c["runId"] == run_id for c in stack.jsonl(run_dir / "model-calls.jsonl")), 60, "first model call"
                )
            else:
                stack.wait_for(lambda: (run_dir / "slow.started").exists(), 60, "slow tool started")
            bound = float(scenario.knobs.get("drainS", 30)) + DRAIN_EXIT_MARGIN_S
            drained_in = worker.term(bound)
            status_after_drain = (stack.run_document(run_id) or {}).get("status")
            worker.start()
        else:
            stack.wait_for(lambda: (run_dir / "slow.started").exists(), 60, "slow tool started")
            fault_stack.node.kill9()
            time.sleep(2)
            fault_stack.node.start()
        terminal = {scenario.expected_status, "failed", "cancelled"}
        stack.wait_for(lambda: (stack.run_document(run_id) or {}).get("status") in terminal, RECOVERY_TIMEOUT_S, f"{run_id} terminal")
        # The last attempt records its disposition just after the terminal commit.
        if scenario.last_attempt_recorded:
            wait_for_final_disposition(run_dir, run_id)
    finally:
        worker.kill9()
    if surface is not None:
        surface.thread.join(60)
    events = [e for e in stack.journal(session_id) if e.get("runId") == run_id]
    claims = sum(1 for e in events if e["type"] == "run.claimed")
    return Outcome(
        run_id, session_id, stack.run_document(run_id) or {}, events, run_dir, claims, surface, drained_in, status_after_drain
    )


HANDED_BACK = stack.HANDED_BACK


def wait_for_final_disposition(run_dir: Path, run_id: str) -> None:
    """The last attempt records its disposition just after the terminal commit."""
    stack.wait_for(
        lambda: any(
            d["runId"] == run_id and d["status"] not in HANDED_BACK for d in stack.jsonl(run_dir / "dispositions.jsonl")
        ),
        30,
        "final disposition",
    )


def k12_problems(scenario: Scenario, outcome: Outcome) -> list[str]:
    problems: list[str] = []
    status = outcome.run.get("status")
    if status != scenario.expected_status:
        problems.append(f"status {status} != {scenario.expected_status}")

    calls = collections.Counter(c["turn"] for c in stack.jsonl(outcome.run_dir / "model-calls.jsonl") if c["runId"] == outcome.run_id)
    if dict(calls) != scenario.model_calls_per_turn:
        problems.append(f"(b) model calls per turn {dict(calls)} != {scenario.model_calls_per_turn}")
    responses = stack.responses_per_turn(outcome.events)
    if any(n != 1 for n in responses.values()):
        problems.append(f"(b) journaled responses per turn {responses}")

    effects = stack.effects(outcome.run_dir, outcome.run_id)
    if scenario.writes and not effects:
        problems.append("the run made no external effect")
    if not scenario.writes and effects:
        problems.append(f"a read-only run made external effects {len(effects)}")
    if duplicates := stack.duplicate_effects(effects):
        problems.append(f"(a) duplicate external effects {duplicates}")

    records = [d for d in stack.jsonl(outcome.run_dir / "dispositions.jsonl") if d["runId"] == outcome.run_id]
    if not records:
        if scenario.last_attempt_recorded:
            problems.append("the last attempt recorded no disposition")
        return problems
    # The run's last attempt, by fence: a drained attempt that was superseded while it still read the journal
    # writes its record after the attempt that finished the run (RU-4a on Redis, once under load).
    last = max(records, key=lambda d: d["fence"])
    if "projectionError" in last:
        problems.append(f"(c) projection failed: {last['projectionError']}")
    elif status == "completed":
        if last["kernel"] != last["projection"]:
            problems.append("(c) kernel transcript != journal projection")
        if not last["paired"] or any(p[0] == "tool" and p[1].endswith(":cancelled") for p in last["projection"]):
            problems.append("(d) a tool call has no outcome")
    elif status == "waiting_approval" and not any(e["type"] == "tool.unknown" for e in outcome.events):
        problems.append("suspended without tool.unknown")
    if scenario.steers:
        problems += steer_problems(outcome)
    if scenario.crash in DRAINS:
        problems += drain_problems(scenario, outcome)
    if scenario.route in ("chat", *SURFACE_FRAMES):
        problems += chat_problems(outcome)
    if outcome.surface is not None:
        problems += surface_problems(scenario.route, outcome)
    return problems


def surface_problems(route: str, outcome: Outcome) -> list[str]:
    """The surface's own response outlived the crash: it ended once, with its audience's frames.

    The scripted runtime journals no presentation, so the run has no answer to show and every
    audience ends it with ``RUN_ERROR``; the surface must end it the way the owner's replay does.
    """
    surface = outcome.surface
    assert surface is not None
    problems: list[str] = []
    if surface.thread.is_alive() or surface.error is not None or surface.status != 200:
        problems.append(f"surface stream status {surface.status} error {type(surface.error).__name__ if surface.error else None}")
    frames = surface.frames()
    kinds = collections.Counter(f.get("type") for f in frames)
    terminals = [f for f in frames if f.get("type") in ("RUN_FINISHED", "RUN_ERROR")]
    owner = [f for f in stack.replayed_frames(outcome.session_id, outcome.run_id) if f.get("type") in ("RUN_FINISHED", "RUN_ERROR")]
    if len(terminals) != 1 or [t.get("type") for t in terminals] != [t.get("type") for t in owner]:
        problems.append(f"surface terminal frames {dict(kinds)}, owner's {[t.get('type') for t in owner]}")
    if set(kinds) - SURFACE_FRAMES[route]:
        problems.append(f"surface received frames beyond its audience {sorted(set(kinds) - SURFACE_FRAMES[route])}")
    return problems + surface_leaks(route, "".join(surface.lines))


# The chat script's read: its argument and its result (worker.py) reach no surface; its name reaches
# Slack only, whose bot shows the running tool (TOOL_CALL_START, snapshot `current_tool`).
_CALL_CONTENT = ("refund policy", '"found"', "x" * 64)
_CALL_NAME = "lookup"


def surface_leaks(route: str, body: str) -> list[str]:
    problems = []
    if any(marker in body for marker in _CALL_CONTENT):
        problems.append("the tool call's arguments or result reached the surface")
    if route != "slack" and _CALL_NAME in body:
        problems.append("the tool call's name reached the surface")
    return problems


def drain_problems(scenario: Scenario, outcome: Outcome) -> list[str]:
    """RU-1: the worker exited within the bound and handed the run back at once (yield, or a release past the bound)."""
    problems: list[str] = []
    bound = float(scenario.knobs.get("drainS", 30)) + DRAIN_EXIT_MARGIN_S
    if outcome.drained_in is None or outcome.drained_in > bound:
        problems.append(f"the worker took {outcome.drained_in} s to exit (bound {bound} s)")
    if outcome.status_after_drain != "queued":
        problems.append(f"after the drain the run was {outcome.status_after_drain}, not queued")
    yields = [e["data"].get("reason") for e in outcome.events if e["type"] == "run.yielded"]
    timed_out = "drainS" in scenario.knobs
    if yields != ([] if timed_out else ["drain"]):
        problems.append(f"run.yielded reasons {yields}")
    drains = [r for r in stack.log_records("worker", (outcome.run_dir / "worker.log").read_text(errors="replace"))
              if r.event == "worker.drain"]
    expected = r"released=1 .*timedOut=1" if timed_out else r"timedOut=0 .*yielded=1"
    if len(drains) != 1 or not re.search(expected, drains[0].text):
        problems.append(f"worker.drain lines {[d.text.strip()[-120:] for d in drains]}")
    return problems


def steer_problems(outcome: Outcome) -> list[str]:
    """ST-4: queued once by Node, consumed once by the worker at the boundary after the read, seen once."""
    problems: list[str] = []
    kinds = [(e["type"], e["writer"]) for e in outcome.events]
    if kinds.count(("input.queued", "node")) != 1 or kinds.count(("input.consumed", "worker")) != 1:
        problems.append(f"steering events {[k for k in kinds if k[0].startswith('input.')]}")
        return problems
    types = [t for t, _ in kinds]
    consumed = types.index("input.consumed")
    if "tool.outcome" not in types[:consumed] or "model.response" not in types[consumed:]:
        problems.append("the input was not taken at the boundary after the read")
    second = [c for c in stack.jsonl(outcome.run_dir / "model-calls.jsonl") if c["runId"] == outcome.run_id and c["turn"] == 2]
    if [c.get("users") for c in second] != [2]:
        problems.append(f"turn 2's model call saw {[c.get('users') for c in second]} user messages (the query and the steer)")
    if outcome.run.get("pendingInputs") not in (None, []):
        problems.append(f"pending inputs left on the run {outcome.run.get('pendingInputs')}")
    return problems


def chat_problems(outcome: Outcome) -> list[str]:
    """The assistant's run was admitted on the conversations route, pinned with its chat grant and no agent.

    The worker here runs a scripted runtime, not PipesHub's presentation, so
    no run saves an answer; the assembled chat run's answer across crashes is
    PC-4's contract test (tests/contracts/test_durable_chat_crash.py).
    """
    problems: list[str] = []
    pinned = next((e for e in outcome.events if e["type"] == "run.spec_pinned"), None)
    extension = ((pinned or {}).get("data", {}).get("spec", {}).get("extensions") or {}).get("x.pipeshub") or {}
    if extension.get("chat") != {"mode": "internal_search", "internalSearch": True} or "agent" in extension:
        problems.append(f"pinned chat grant {extension.get('chat')}")
    session = stack.conversation(outcome.session_id).get("session") or {}
    if (session.get("modelInfo") or {}).get("chatMode") != "internal_search" or session.get("agentKey"):
        problems.append(f"session mode {(session.get('modelInfo') or {}).get('chatMode')} agent {session.get('agentKey')}")
    return problems


LOG_SETTLES_S = 5


def log_problems(scenario: Scenario, outcome: Outcome, node_log: Path, credential: str) -> list[str]:
    """Node writes a transition's log line after the commit that makes the run terminal, so a check made the moment
    the status is read can come before the line (seen once: 0.2 s). A missing line is therefore looked for again
    for a few seconds; anything else is judged at once."""
    deadline = time.monotonic() + LOG_SETTLES_S
    while True:
        problems = _log_problems(scenario, outcome, node_log, credential)
        if time.monotonic() >= deadline or not any(p.endswith("line names the run") for p in problems):
            return problems
        time.sleep(0.25)


def _log_problems(scenario: Scenario, outcome: Outcome, node_log: Path, credential: str) -> list[str]:
    node = stack.log_records("node", node_log.read_text(errors="replace"))
    worker = stack.log_records("worker", (outcome.run_dir / "worker.log").read_text(errors="replace"))
    problems: list[str] = []
    for record in [*node, *worker]:
        leaked = [s for s in (*stack.SENTINELS, credential) if s in record.text]
        if leaked:
            problems.append(f"{record.source} {record.event} logs content or a secret ({len(leaked)} match)")
    ours = [r for r in [*node, *worker] if outcome.run_id in r.text]
    events = collections.Counter((r.source, r.event) for r in ours)
    required = [("node", "admission.accepted"), ("node", "run.transition"), ("worker", "run.claimed"), ("worker", "journal.commit")]
    if scenario.steers:
        required += [("node", "input.queued"), ("worker", "input.consumed")]
    if scenario.route in SURFACE_AUDIENCE:
        accepted = [r for r in ours if r.event == "admission.accepted"]
        if not any(re.search(rf"surface\W+{scenario.route}\b", r.text) for r in accepted):
            problems.append(f"admission.accepted does not name the {scenario.route} surface")
        audience = SURFACE_AUDIENCE[scenario.route]
        subscribed = [r for r in ours if r.event == "projection.subscribe"]
        if not any(re.search(rf"audience\W+{audience}\b", r.text) for r in subscribed):
            problems.append(f"projection.subscribe does not name the {audience} audience")
    # A crashed worker's run is recovered either by the sweeper re-queuing it or by the
    # next claim taking over its expired lease first (e.g. a redelivered broker hint);
    # run.claimed says which, so the log must show one of the two.
    took_over = any(
        r.source == "node" and r.event == "run.claimed" and re.search(r"from\W+expired_lease\b", r.text)
        for r in ours
    )
    if scenario.crash in ("failpoint", "kill_on_model_call") and not took_over:
        required.append(("node", "sweeper.expired"))
    if scenario.crash in DRAINS:
        # Handed back by the drain, never left for the sweeper.
        required.append(("worker", "run.release" if "drainS" in scenario.knobs else "run.yield"))
        if events[("node", "sweeper.expired")]:
            problems.append("the sweeper expired a drained run")
    problems += [f"no {source} {event} line names the run" for source, event in required if events[(source, event)] == 0]
    if events[("node", "run.claimed")] != outcome.claims:
        problems.append(f"Node logged {events[('node', 'run.claimed')]} claims of {outcome.claims}")
    problems += [f"{r.source} {r.event} is logged at ERROR" for r in ours if r.level == "ERROR"]
    return problems


def save_journal(broker: str, scenario: str, outcome: Outcome) -> None:
    """The run's session journal as Node stored it, for a release's replay corpus (RU-3a: record_replay_corpus.py --l4)."""
    doc = {"broker": broker, "scenario": scenario, "runId": outcome.run_id, "sessionId": outcome.session_id,
           "status": outcome.run.get("status"), "events": stack.journal(outcome.session_id)}
    (outcome.run_dir / "journal.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_crash_point(fault_stack: stack.FaultStack, credential: str, scenario: Scenario) -> None:
    outcome = run_scenario(fault_stack, scenario)
    save_journal(fault_stack.broker, scenario.name, outcome)
    result = {
        "scenario": scenario.name,
        "broker": fault_stack.broker,
        "runId": outcome.run_id,
        "status": outcome.run.get("status"),
        "claims": outcome.claims,
        "problems": k12_problems(scenario, outcome),
        "logProblems": log_problems(scenario, outcome, fault_stack.node_log, credential),
    }
    with (fault_stack.out / "results.jsonl").open("a") as f:
        f.write(json.dumps(result) + "\n")
    assert result["problems"] == [], result
    assert result["logProblems"] == [], result
    if scenario.crash != "node_restart":
        assert outcome.claims == 2, "recovered by a second claim"
