"""QA6-R1..R9: the options-matrix runner's pure parts (``integration-tests/harness/e2e``), without a stack."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

E2E = Path(__file__).resolve().parents[5] / "integration-tests" / "harness" / "e2e"
sys.path.insert(0, str(E2E))

import checks  # noqa: E402
import matrix  # noqa: E402
import observe  # noqa: E402
import plan  # noqa: E402
import results  # noqa: E402
import rotation  # noqa: E402

import scripts  # noqa: E402

FACTORS = json.loads((matrix.DATA / "e2e-factors.json").read_text())["factors"]
ROWS = matrix.all_rows()


def _row(**factors: str) -> matrix.Row:
    base = {"mode": "quick", "action": "send", "path": "durable", "engine": "none", "wire": "openai_chat",
            "compiler": "off", "flow_guard": "ask", "write_policy": "allow", "decision": "none", "tool_family": "none",
            "sandbox": "broker_closed", "trigger": "interactive", "graph": "neo4j", "mongo": "replset", "kv": "redis",
            "broker": "redis_streams", "deploy": "compose", "role": "user", "child_model": "none"}
    return matrix.Row(id="PW-999", array="pairwise", stack="ST-1", factors={**base, **factors})


def test_qa6_r1_one_test_per_row_grouped_by_stack() -> None:
    assert len(ROWS) == 132 + 75
    assert len({r.test_id for r in ROWS}) == len(ROWS)
    assert all(r.test_id.startswith(f"{r.stack}-") for r in ROWS)
    st3 = [r for r in ROWS if "ST-3" in r.test_id]
    assert st3 and all(r.stack == "ST-3" for r in st3)
    assert all(matrix.covering.stack_of(dict(r.factors)) == r.stack for r in ROWS)


@pytest.mark.parametrize("factor", [f["id"] for f in FACTORS])
def test_qa6_r2_every_level_has_a_setup_step(factor: str) -> None:
    levels = next(f["levels"] for f in FACTORS if f["id"] == factor)
    assert sorted(plan.STEPS[factor].levels) == sorted(levels)  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="no setup step"):
        plan.STEPS[factor](plan.RowPlan(row=_row()), "not-a-level")


def test_qa6_r2_every_row_plans() -> None:
    planned = [plan.plan(r) for r in ROWS]
    blocked = {p.row.id: {b.finding for b in p.blocked} for p in planned if p.blocked}
    assert blocked == {
        r.id: {f for f, on in (("QA37-F1", not r.expected.durable), ("CPD-11", r["child_model"] == "forbidden")) if on}
        for r in ROWS if r["child_model"] != "none" and (not r.expected.durable or r["child_model"] == "forbidden")
    }, "every other level of every factor can be set up"
    flow_off = plan.plan(_row(flow_guard="off", tool_family="toolset", write_policy="ask", decision="allow_once"))
    assert flow_off.process.env()["HARNESS_FLOW_GUARD"] == "false"
    assert flow_off.org.writes == "ask"
    assert plan.plan(_row(flow_guard="org_deny")).org.flow_deny
    kill = plan.plan(_row(path="kill_switch"))
    assert kill.process.env()["AGENT_HARNESS_KILL_SWITCH"] == "true"
    docker = plan.plan(_row(sandbox="docker", tool_family="run_code"))
    assert docker.process.env()["SANDBOX_MODE"] == "docker"


@pytest.mark.parametrize(("family", "name", "writes"), [
    ("none", "answer", None), ("knowledge", "read-then-answer", None), ("mcp", "read-then-write-mcp", "create_ticket"),
    ("toolset", "read-then-write-toolset", "create_issue"), ("run_code", "code-then-answer", None),
    ("skills", "skill-then-answer", None), ("memory", "remember-then-answer", None),
])
def test_qa6_r3_the_script_by_tool_family(family: str, name: str, writes: str | None) -> None:
    script = scripts.for_row(family, "QA6 PW-001 abc")
    assert (script.name, script.writes) == (name, writes)
    assert script.lines[-1] == "say QA6 PW-001 abc"
    calls = [line for line in script.lines if line.startswith("tool ")]
    if writes is not None:
        names = [c.split(" ")[1] for c in calls]
        assert names.index(writes) == len(names) - 1 and len(names) >= 3, "a sink script reads before it writes"
    for call in calls:
        for option in call.removeprefix("tool ").split(" || "):
            json.loads(option.split(" ", 1)[1])


def test_qa6_r3_a_capability_names_each_modes_tool() -> None:
    read = scripts.for_row("knowledge", "QA6 m").lines[1]
    assert [o.split(" ", 1)[0] for o in read.removeprefix("tool ").split(" || ")] == [
        "search_internal_knowledge", "internal_exploration_agent", "knowledgegraph__search"]
    code = scripts.for_row("run_code", "QA6 m").lines[1]
    delegated = json.loads(code.split(" || ")[1].split(" ", 1)[1])["goal"].splitlines()
    assert delegated[1].startswith("tool run_code ") and delegated[-1].startswith("say ")


def _obs(**kw: object) -> observe.Observation:
    o = observe.Observation(route="durable", terminal="finished", first_frame="conversation_created")
    for k, v in kw.items():
        setattr(o, k, v)
    return o


CARD = {"interactionId": "ix.1", "reason": {"rule": "flow_external_write", "layer": "flow"}}
EXPLAINED = {"type": "interaction.explained", "data": {"interactionId": "ix.1", "rule": "flow_external_write", "layer": "flow"}}


def _card(interaction: str, child: str | None = None, name: str = "approval_requested") -> dict[str, object]:
    value = {"interactionId": interaction, **({"childRunId": child} if child else {})}
    return {"type": "CUSTOM", "name": name, "value": value}


@pytest.mark.parametrize(("obs", "sink", "outcome"), [
    (_obs(), False, "answered"),
    (_obs(terminal="error", error_code="server_error"), False, "failed:server_error"),
    (_obs(effects=1), True, "written"),
    (_obs(effects=1, cards=[CARD], decisions=["approved"]), True, "approved:written"),
    (_obs(effects=1, cards=[CARD], decisions=["approved"], events=[{"type": "interaction.granted"}]), True,
     "approved:written+grant"),
    (_obs(cards=[CARD], decisions=["denied"]), True, "denied:user"),
    (_obs(events=[{"type": "tool.denied", "data": {"rule": "flow_external_write"}}]), True, "denied:flow_external_write"),
    (_obs(route="legacy", write_results=["approval_unavailable: this action would send content outside"]), True,
     "refused:approval_unavailable"),
    (_obs(cards=[CARD], decisions=["expire"], terminal="error", error_code="approval_expired"), True,
     "expired:approval_expired"),
])
def test_qa6_r4_what_happened_in_the_oracles_words(obs: observe.Observation, sink: bool, outcome: str) -> None:
    assert observe.observed(obs, sink=sink).outcome == outcome


def test_qa6_r4_executor_and_route() -> None:
    assert observe.observed(_obs(), sink=False).executor == "loop"
    engine = _obs(events=[{"type": "engine.started", "data": {"engine": "opencode"}}])
    assert observe.observed(engine, sink=False).executor == "engine:opencode"
    pinned = _obs(events=[{"type": "engine.checkpointed", "data": {"engine": {"kind": "codex", "version": "0.160.0"}}}])
    assert observe.observed(pinned, sink=False).executor == "engine:codex"
    assert observe.observed(_obs(route="legacy"), sink=False).executor == "legacy"
    waiting = _obs(events=[{"type": "run.spec_pinned", "data": {"spec": {"executor": {"engine": {"kind": "opencode"}}}}}])
    assert observe.observed(waiting, sink=False).executor == "engine:opencode", "an engine run that never got to its engine"
    loop = _obs(events=[{"type": "run.spec_pinned", "data": {"spec": {"executor": {"kind": "loop"}}}}])
    assert observe.observed(loop, sink=False).executor == "loop"
    # CTL-6: a coder agent's turn, a loop run pinning the engine it may hand off to.
    hand_off = {"kind": "loop"}
    code_task = {"x.pipeshub": {"codeTask": {"profile": {"engine": {"kind": "codex"}}}}}
    turn = _obs(events=[{"type": "run.spec_pinned", "data": {"spec": {"executor": hand_off, "extensions": code_task}}}])
    assert observe.observed(turn, sink=False).executor == "loop+code_task:codex"


def test_qa6_r5_the_expectation_is_the_scripts_oracle() -> None:
    for row in ROWS:
        assert row.expected == matrix.Expected(**matrix.covering.oracle(dict(row.factors)))
    got = observe.Observed("legacy", "kill_switch", "legacy", False, "answered", 0, "inherit")
    want = matrix.Expected("legacy", "chat_mode_not_supported", "legacy", False, "answered", 0, "inherit")
    assert checks.oracle_problems(want, got) == [
        "oracle reason: expected 'chat_mode_not_supported', Node logged 'kill_switch'"]


def _p(**factors: str) -> plan.RowPlan:
    return plan.plan(_row(**factors))


def _check(name: str) -> checks.Check:
    return next(c for c in checks.CHECKS if c.name.startswith(name))


HISTORY = [
    {"orgId": "o", "sessionId": "s", "streamId": "s", "seq": 1, "runId": "r", "type": "run.admitted", "critical": True,
     "ts": "t", "writer": "node", "data": {}},
    {"orgId": "o", "sessionId": "s", "streamId": "s", "seq": 2, "runId": "r", "type": "run.spec_pinned", "critical": True,
     "ts": "t", "writer": "node", "data": {}},
    {"orgId": "o", "sessionId": "s", "streamId": "s", "seq": 3, "runId": "r", "type": "model.request", "critical": True,
     "ts": "t", "writer": "worker", "data": {}},
]


@pytest.mark.parametrize(("check", "good", "bad"), [
    ("always: journal history", _obs(session_events=HISTORY),
     _obs(session_events=[HISTORY[0], {**HISTORY[2], "seq": 3}])),
    ("always: first frame", _obs(), _obs(first_frame="RUN_STARTED")),
    ("always: the script ran", _obs(marker="QA6 m", answer="QA6 m"), _obs(marker="QA6 m", answer="tool unavailable")),
    ("route durable: spec pinned first", _obs(events=HISTORY), _obs(events=[HISTORY[0], HISTORY[2], HISTORY[1]])),
    ("route legacy: nothing admitted", _obs(route="legacy"), _obs(route="legacy", events=HISTORY[:1])),
    ("route legacy: one record says why", _obs(route="legacy", legacy_reasons=["infrastructure_unavailable"]), _obs(route="legacy")),
    ("route legacy: one record says why", _obs(route="legacy", legacy_reasons=["kill_switch"]),
     _obs(route="legacy", legacy_reasons=["kill_switch", "kill_switch"])),
    ("card: rule named", _obs(cards=[CARD], decisions=["approved"], session_events=[EXPLAINED, {"type": "interaction.requested"}],
                              audit={"ok": True}, audit_types=[{"type": "interaction.resolved"}]),
     _obs(cards=[{"reason": {"rule": "made_up"}}], decisions=["approved"], audit={"ok": False, "problems": ["gap@3"]})),
    ("action regenerate", _obs(run_id="r2", previous_run_id="r1", events=[{"type": "run.admitted"}],
                               saved_messages=["user_query", "bot_response"]),
     _obs(run_id="r1", previous_run_id="r1", saved_messages=["user_query", "bot_response", "bot_response"])),
    ("action regenerate", _obs(run_id="r2", previous_run_id="r1", events=[{"type": "run.admitted"}], terminal="error",
                               error_code="approval_expired", saved_messages=["user_query"]),
     _obs(run_id="r2", previous_run_id="r1", events=[{"type": "run.admitted"}], saved_messages=["user_query"])),
    ("action reconnect", _obs(reconnects=[("j:2.1/0.0.0", ["j:3.1/0.0.0"])]), _obs()),
    ("route durable: ids unique", _obs(frame_ids=["j:1.1/0.0.0", "j:2.1/0.0.0", "j:2.1/0.0.0^3.14+1", "j:2.1/0.0.0+2"]),
     _obs(frame_ids=["j:1.1/0.0.0", "j:2.1/0.0.0", "j:2.1/0.0.0"])),
    ("route durable: ids unique", _obs(frame_ids=["j:2.1/0.0.0", None, "d:2.1.1.1/1.1", "d:2.1.2.1/1.1^3.14", "a:d:2.1.2.1/1.1",
                                                  "j:3.1/1.1.2-1.1", "q:run:0:1"]),
     _obs(frame_ids=["j:3.1/0.0.0", "j:2.9/0.0.0"])),
    ("route durable: ids unique", _obs(frame_ids=["j:2.1/0.0.0+1", "j:2.1/0.0.0+2"]),
     _obs(frame_ids=["j:2.1/0.0.0+2", "j:2.1/0.0.0+1"])),
    ("route durable: ids unique", _obs(frames=[_card("ix.1", "c1"), _card("ix.1", "c2"), _card("ix.1"), _card("ix.2", "c1")]),
     _obs(frames=[_card("ix.1", "c1"), {"type": "TEXT_MESSAGE_CONTENT"}, _card("ix.1", "c1")])),
    ("route durable: ids unique", _obs(frames=[_card("ix.u", "c1", "unknown_effect"), _card("ix.a", "c1")]),
     _obs(frames=[_card("ix.u", "c1", "unknown_effect"), _card("ix.u", "c1", "unknown_effect")])),
    ("trigger schedule", _obs(firings=[{"runId": "r1"}, {"runId": None}], events=[{"type": "run.admitted", "data": {"runKind": "scheduled"}}]),
     _obs(firings=[{"runId": "r1"}, {"runId": "r2"}])),
    ("tool_family run_code", _obs(events=[{"type": "x.pipeshub.command.finished"}]), _obs()),
    ("engine set", _obs(run_id="r1", session_events=[{"runId": "r1", "type": "workspace.committed"},
                                                     {"runId": "r1", "type": "engine.checkpointed"}]), _obs()),
    ("role admin", _obs(admin_view={"effectivePolicy": {}}, audit={"ok": True}), _obs(admin_view={"status": 403})),
    ("tool_family memory", _obs(memories=["Prefers answers as short bullet lists."]), _obs(memories=[])),
    ("compiler on", _obs(events=[{"type": "model.request", "data": {"contextDigest": "sha256:ab"}}]),
     _obs(events=[{"type": "model.request", "data": {}}])),
])
def test_qa6_r6_each_extra_check_holds_or_names_the_problem(check: str, good: observe.Observation,
                                                            bad: observe.Observation) -> None:
    c = _check(check)
    p = _p(trigger="schedule_webhook") if check.startswith("trigger") else _p()
    assert c.verify(p, good) == []
    assert c.verify(p, bad) != []


@pytest.mark.parametrize(("frame_id", "position"), [
    ("j:22.1/3.1.4", (22, 1, 0, 0, 0)),
    ("j:22.1/3.1.4~2.1^3.14^9.2+2", (22, 1, 0, 0, 2)),
    ("d:5.1.2.3/3.1^3.14", (5, 1, 2, 3, 0)),
    ("a:d:5.1.2.3/3.1", None),
    ("q:run:0:1", None),
    ("", None),
])
def test_qa6fix_f12_10_a_frame_ids_position(frame_id: str, position: tuple[int, ...] | None) -> None:
    assert checks.stream_position(frame_id) == position


HELD = [EXPLAINED, {"type": "interaction.requested"}]


@pytest.mark.parametrize(("card", "events", "problem"), [
    (CARD, HELD, None),
    ({"interactionId": "ix.1"}, HELD, "names rule None of layer None"),
    ({"interactionId": "ix.1", "reason": {"rule": "flow_external_write"}}, HELD, "of layer None"),
    ({"interactionId": "ix.1", "reason": {"rule": "writes", "layer": "galaxy"}}, HELD, "of layer 'galaxy'"),
    ({"interactionId": "ix.1", "reason": {"rule": "writes", "layer": "org"}}, HELD, "the row's rule is ('flow_external_write', 'flow')"),
    (CARD, [{"type": "interaction.requested"}], "the journal explains the card as (None, None)"),
    (CARD, [{**EXPLAINED, "data": {**EXPLAINED["data"], "layer": "org"}}, {"type": "interaction.requested"}],  # type: ignore[dict-item]
     "the journal explains the card as ('flow_external_write', 'org')"),
])
def test_qa6fix_f6_a_card_names_its_rule_and_layer_on_the_frame_and_in_the_journal(
    card: dict[str, object], events: list[dict[str, object]], problem: str | None,
) -> None:
    found = _check("card: rule named").verify(_p(), _obs(cards=[card], session_events=events))
    assert found == [] if problem is None else any(problem in p for p in found), found


@pytest.mark.parametrize(("factors", "named"), [
    ({"flow_guard": "ask", "write_policy": "ask"}, ("flow_external_write", "flow")),
    ({"flow_guard": "off", "write_policy": "ask"}, ("writes", "deployment")),
    ({"flow_guard": "off", "write_policy": "allow", "engine": "opencode"}, ("engine_writes", "engine")),
])
def test_qa6fix_f6_whose_rule_a_rows_card_names(factors: dict[str, str], named: tuple[str, str]) -> None:
    assert checks.expected_card_rule(_p(**factors)) == named


def test_qa6fix_f12_10_every_durable_row_is_checked_not_only_reconnect_rows() -> None:
    check = _check("route durable: ids unique")
    assert check.applies(_p(), _obs()) and not check.applies(_p(), _obs(route="legacy"))


def test_qa6_r6_the_rows_wire_must_serve_the_turn() -> None:
    c = _check("always: the row's wire")
    assert c.verify(_p(wire="gemini"), _obs(wires={"gemini"})) == []
    assert c.verify(_p(wire="gemini"), _obs(wires={"openai_chat"})) != []
    assert checks.canary_problems(['{"delta": "pat-canary-123"}'], ["pat-canary-123"]) != []
    assert checks.canary_problems(['{"delta": "fine"}'], ["pat-canary-123", ""]) == []


@pytest.mark.parametrize(("when", "outcome", "wasxfail", "longrepr", "line"), [
    ("call", "passed", None, "", "pass"),
    ("call", "failed", None, "boom\nAssertionError: oracle route", "fail AssertionError: oracle route"),
    ("call", "failed", None, "x\nE   AssertionError: PW-001 on ST-6:\nE     oracle card: expected True, observed False\nE     [b] c",
     "fail oracle card: expected True, observed False"),
    ("call", "skipped", "QA6-F4: legacy image mode", "", "red QA6-F4"),
    ("call", "passed", "QA6-F4: legacy image mode", "", "green-but-recorded-red QA6-F4"),
    ("call", "skipped", None, "Skipped: blocked E2E-G9: pin not admitted", "blocked blocked E2E-G9: pin not admitted"),
    ("setup", "failed", None, "StackError: did not come up", "error StackError: did not come up"),
])
def test_qa6_r7_one_result_line_per_row(when: str, outcome: str, wasxfail: str | None, longrepr: str, line: str,
                                        tmp_path: Path) -> None:
    log = results.ResultLog(tmp_path / "rows.txt")
    report = SimpleNamespace(when=when, outcome=outcome, longrepr=longrepr or None)
    if wasxfail is not None:
        report.wasxfail = wasxfail
    log.record(ROWS[0], report)
    assert log.lines() == [f"{ROWS[0].id} {ROWS[0].stack} {line}"]
    assert log.write(tmp_path / "unused.txt").read_text() == f"{ROWS[0].id} {ROWS[0].stack} {line}\n"


def test_qa6_r8_known_red_rows_exist_and_name_a_finding() -> None:
    known = json.loads((E2E / "known_red.json").read_text())["rows"]
    ids = {r.id for r in ROWS}
    for row_id, entry in known.items():
        assert row_id in ids
        assert results.FINDING.search(entry["finding"]), entry
        assert entry["why"]


def test_qa6_r9_nightly_rotation_covers_every_stack_within_a_week() -> None:
    start = date(2026, 10, 12)
    nights = [rotation.nightly(start + timedelta(days=d)) for d in range(7)]
    assert all(n[0] == "ST-1" and len(set(n)) == 3 for n in nights)
    assert set().union(*nights) == set(matrix.STACKS)
    helm = {"ST-2", "ST-6", "ST-7"}
    assert all(len(helm & set(n)) == 1 for n in nights), "one kind cluster a night"
    assert rotation.weekly() == tuple(sorted(matrix.STACKS))


def test_qa6_r2_blocked_rows_never_reach_a_stack() -> None:
    claude = _p(engine="claude_code", wire="anthropic_messages", sandbox="broker_closed")
    assert claude.blocked == () and claude.process.env()["HARNESS_ENGINE_RUNS"] == "true"
    assert replace(claude, blocked=()).block("X", "y").blocked == (plan.Blocked("X", "y"),)
    engine = scripts.for_row("mcp", "QA6 m", engine=True)
    assert engine.lines[-2] == f"write {scripts.ENGINE_NOTES} QA6 m" and engine.lines[-1] == "say QA6 m"
    assert not any("search_tools" in line for line in engine.lines), "an engine has its tools without searching"
    assert scripts.for_row("skills", "QA6 m", engine=True).lines[1] == f"skill {scripts.SKILL_NAME}"
    assert scripts.for_row("run_code", "QA6 m", engine=True).lines[1].startswith("shell printf ")


def test_qa6_r10_the_live_variant_is_capped_and_priced() -> None:
    import live

    prices = {"QA6_LIVE_USD_PER_MTOK_IN": "2.5", "QA6_LIVE_USD_PER_MTOK_OUT": "10"}
    for cap in (0.0, 7.01, 50.0):
        with pytest.raises(live.LiveRefused, match="at most 7"):
            live.spend_from(cap, prices)
    with pytest.raises(live.LiveRefused, match="prices"):
        live.spend_from(7.0, {})
    spend = live.spend_from(7.0, prices)
    response = {"type": "model.response", "data": {"usage": {"inputTokens": 1_000_000, "outputTokens": 200_000}}}
    assert spend.charge("PW-001", [response, {"type": "model.request"}]) == pytest.approx(4.5)
    assert not spend.exhausted
    spend.charge("PW-002", [response])
    assert spend.exhausted and spend.spent_usd == pytest.approx(9.0)
    assert live.LiveRun.takes({"wire": "azure_openai", "engine": "none", "child_model": "none"})
    assert not live.LiveRun.takes({"wire": "azure_openai", "engine": "opencode", "child_model": "none"})
    assert not live.LiveRun.takes({"wire": "azure_openai", "engine": "none", "child_model": "same"})


def test_qa6_r6_the_operator_sees_health_and_no_canary_in_logs() -> None:
    assert checks._operator(_p(role="operator"), _obs(health=200, log_text="ok"), ["pat-canary-1"]) == []
    bad = checks._operator(_p(role="operator"), _obs(health=503, log_text="token pat-canary-1"), ["pat-canary-1"])
    assert len(bad) == 2 and "Node's log" in bad[1]


def test_qa6_r3_a_cron_row_waits_for_one_real_slot() -> None:
    from datetime import UTC, datetime

    import driver

    assert driver._next_slot(datetime(2026, 10, 11, 23, 59, 30, tzinfo=UTC)) == "1 0 * * *"
    assert driver._next_slot(datetime(2026, 10, 11, 9, 5, tzinfo=UTC)) == "7 9 * * *"


DEEP = {"mode": "deep", "tool_family": "knowledge"}


@pytest.mark.parametrize(("factors", "role_wire", "blocked"), [
    ({**DEEP}, None, set()),
    ({**DEEP, "child_model": "same", "wire": "gemini"}, "gemini", set()),
    ({**DEEP, "child_model": "different"}, "anthropic_messages", set()),
    ({**DEEP, "child_model": "different", "wire": "anthropic_messages"}, "openai_chat", set()),
    ({**DEEP, "child_model": "forbidden", "wire": "bedrock"}, "openai_chat", {"CPD-11"}),
    ({**DEEP, "child_model": "different", "path": "kill_switch"}, "anthropic_messages", {"QA37-F1"}),
    ({**DEEP, "child_model": "forbidden", "path": "kill_switch"}, "anthropic_messages", {"QA37-F1", "CPD-11"}),
])
def test_qa37_e2_each_role_model_level_is_planned_or_blocked_with_its_reason(
    factors: dict[str, str], role_wire: str | None, blocked: set[str],
) -> None:
    p = _p(**factors)
    assert (p.agent.child_model, p.role_wire) == (factors.get("child_model", "none"), role_wire)
    assert {b.finding for b in p.blocked} == blocked and all(b.reason for b in p.blocked)


def test_qa37_e2_a_role_model_never_changes_the_process_or_the_org() -> None:
    plain = _p(**DEEP)
    for level in ("same", "different", "forbidden"):
        p = _p(**DEEP, child_model=level)
        assert (p.process, p.org, p.turn) == (plain.process, plain.org, plain.turn)
        assert replace(p.agent, child_model="none") == plain.agent


A, B = "e2e-llm", "claude-sonnet-4-5"
ROLE = {"modelKey": "mk-b", "modelName": B, "provider": "anthropic", "wire": "anthropic_messages"}
GRANT = "sha256:" + "0" * 64


def _pinned(run: str, model: str, key: str) -> dict[str, object]:
    spec = {"model": {"policy": [{"provider": "p", "model": model}]},
            "extensions": {"x.pipeshub": {"model": {"modelKey": key, "modelName": model}}}}
    return {"type": "run.spec_pinned", "runId": run, "data": {"spec": spec}}


def _spawned(model: dict[str, str] | None = None) -> dict[str, object]:
    data: dict[str, object] = {"childRunId": "c1", "invocationId": "i1", "grantDigest": GRANT}
    if model is not None:
        data["model"] = model
    return {"type": "child.spawned", "runId": "r1", "data": data}


def _deep_obs(*events: dict[str, object], child_route: str = A, child_key: str = "mk-a", **kw: object) -> observe.Observation:
    journal = [_pinned("r1", A, "mk-a"), *events, _pinned("c1", child_route, child_key)]
    return _obs(run_id="r1", events=journal, session_events=journal, **kw)


NAMED_B = {"provider": "anthropic", "model": B, "role": "default"}
DENIED = {"type": "tool.denied", "runId": "r1", "data": {"invocationId": "i1", "rule": "child_grant_too_large"}}
CALLS = [{"kind": "delegate", "wire": "openai_chat", "model": A}, {"kind": "loop", "wire": "anthropic_messages", "model": B},
         {"kind": "delegate", "wire": "openai_chat", "model": A}]


@pytest.mark.parametrize(("obs", "child"), [
    (_obs(run_id="r1"), "inherit"),
    (_deep_obs(_spawned()), "inherit"),
    (_deep_obs(_spawned(NAMED_B), child_route=B), "role:different"),
    (_deep_obs(_spawned({**NAMED_B, "model": A})), "role:same"),
    (_deep_obs(DENIED, child_refusals=[{"dimension": "models.roles"}]), "refused:models.roles"),
    (_deep_obs(DENIED), "refused:unnamed"),
    # Another run's child (a regenerate's first turn) is not this turn's.
    (_deep_obs({**_spawned(NAMED_B), "runId": "r0"}), "inherit"),
])
def test_qa37_e2_whose_model_a_child_calls_in_the_oracles_words(obs: observe.Observation, child: str) -> None:
    assert observe.observed(obs, sink=False).child == child


def test_qa37_e2_the_oracle_holds_the_childs_model() -> None:
    row = _row(**DEEP, child_model="different")
    assert row.expected.child == "role:different"
    got = observe.observed(_deep_obs(_spawned()), sink=False)
    assert "oracle child: expected 'role:different', observed 'inherit'" in checks.oracle_problems(row.expected, got)
    refused = _row(mode="deep", tool_family="toolset", child_model="forbidden").expected
    assert (refused.card, refused.outcome, refused.effects, refused.child) == (
        False, "denied:child_grant_too_large", 0, "refused:models.roles")
    assert observe.observed(_deep_obs(DENIED, child_refusals=[{"dimension": "models.roles"}]), sink=True).outcome == refused.outcome


GOOD = _deep_obs(_spawned(NAMED_B), child_route=B, child_key="mk-b", role_model=ROLE, model_calls=CALLS)


@pytest.mark.parametrize(("level", "obs", "problem"), [
    ("different", GOOD, None),
    ("same", _deep_obs(_spawned({**NAMED_B, "model": A}), role_model={**ROLE, "modelKey": "mk-a", "modelName": A, "wire": "openai_chat"},
                       model_calls=[{"kind": "delegate", "wire": "openai_chat", "model": A},
                                    {"kind": "loop", "wire": "openai_chat", "model": A}]), None),
    ("different", _deep_obs(role_model=ROLE, model_calls=CALLS), "spawned no child run"),
    ("different", _deep_obs(_spawned(), child_route=B, child_key="mk-b", role_model=ROLE, model_calls=CALLS), "child.spawned names no model"),
    ("different", _deep_obs(_spawned({**NAMED_B, "role": "researcher"}), child_route=B, child_key="mk-b", role_model=ROLE,
                            model_calls=CALLS), "child.spawned names"),
    ("different", _deep_obs(_spawned(NAMED_B), child_key="mk-b", role_model=ROLE, model_calls=CALLS), "the child's spec routes to"),
    ("different", _deep_obs(_spawned(NAMED_B), child_route=B, role_model=ROLE, model_calls=CALLS), "x.pipeshub.model"),
    ("different", _deep_obs(_spawned(NAMED_B), child_route=B, child_key="mk-b", role_model=ROLE,
                            model_calls=[{**c, "wire": "openai_chat", "model": A} for c in CALLS]), "served no sub-agent call"),
    ("different", _deep_obs(_spawned(NAMED_B), child_route=B, child_key="mk-b", role_model=ROLE,
                            model_calls=[{**c, "wire": "anthropic_messages"} for c in CALLS]), "the coordinator's calls went over"),
    ("none", _deep_obs(_spawned()), None),
    ("none", _deep_obs(_spawned(NAMED_B)), "though the agent sets no role model"),
    ("forbidden", _deep_obs(DENIED), None),
    ("forbidden", _deep_obs(DENIED, _spawned(NAMED_B)), "though the role's model is refused"),
])
def test_qa37_e2_the_role_model_is_pinned_and_called(level: str, obs: observe.Observation, problem: str | None) -> None:
    found = _check("child_model:").verify(_p(**DEEP, child_model=level), obs)
    assert (found == []) if problem is None else any(problem in f for f in found), found


def test_qa37_e2_a_refused_spawn_leaves_no_tool_work_to_assert() -> None:
    for family, name in (("memory", "tool_family memory"), ("run_code", "tool_family run_code")):
        check = _check(name)
        assert check.applies(_p(mode="deep", tool_family=family), _obs())
        assert not check.applies(_p(mode="deep", tool_family=family, child_model="forbidden"), _obs())


def test_qa37_e2_the_stack_reads_a_harness_event_from_nodes_log(tmp_path: Path) -> None:
    import lane

    log = "\n".join([
        "2026-10-12 [req:abc ] warn - child.refused", "metadata: {", '  "event": "child.refused",', '  "runId": "r1",',
        '  "dimension": "models.roles"', "}", "2026-10-12 [req:abc ] info - child.spawned", "metadata: {", '  "runId": "r1"', "}",
    ])
    stack = lane.Stack(run="r", stack_id="ST-1", run_dir=tmp_path, env={}, seed={}, api=None, model=None, saas=None,  # type: ignore[arg-type]
                       runtime=SimpleNamespace(node_log=lambda: log))  # type: ignore[arg-type]
    assert stack.node_events("child.refused") == [
        {"message": "child.refused", "event": "child.refused", "runId": "r1", "dimension": "models.roles"}]
    assert len(stack.node_records("abc")) == 2


class _Api:
    """Node's routes as the fixtures call them: every request is kept."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self.platform: dict[str, object] = {}

    def ok(self, method: str, path: str, body: object = None, **_: object) -> dict[str, object]:
        self.calls.append((method, path, body))
        if path.endswith("/platform/settings"):
            self.platform = dict(body) if method == "POST" else self.platform  # type: ignore[call-overload]
            return self.platform
        if path.endswith("/ai-models/providers"):
            return {"details": {"modelKey": "mk-b"}}
        if path.endswith("/memories/preference"):
            return dict(body)  # type: ignore[call-overload]
        return {"agent": {"_key": f"agent-{sum(1 for c in self.calls if c[1].endswith('/agents/create'))}"}}


def test_qa37_e2_the_agent_is_saved_with_its_role_model_through_the_agent_api(tmp_path: Path) -> None:
    import fixtures

    api = _Api()
    stack = SimpleNamespace(run_dir=tmp_path, api=api, seed={"llmKey": "mk-a"}, env={"E2E_LLM_KEY": "k"})
    fx = fixtures.Fixtures(stack)  # type: ignore[arg-type]
    spec = plan.AgentSpec(family="none")
    plain = fx.agent(spec)
    assert [c[:2] for c in api.calls] == [("POST", "/api/v1/agents/create")], "no role model: the agent is saved as before"
    assert api.calls[0][2] == {"name": "QA6 none none none", "description": "An options-matrix agent.",
                               "systemPrompt": "You are a careful analyst."}
    same = fx.role_model("openai_chat")
    assert same == {"modelKey": "mk-a", "modelName": "e2e-llm", "provider": "openAICompatible", "wire": "openai_chat"}
    other = fx.role_model("anthropic_messages")
    assert other == {"modelKey": "mk-b", "modelName": "claude-sonnet-4-5", "provider": "anthropic", "wire": "anthropic_messages"}
    with_role = fx.agent(spec, other)
    assert with_role != plain and fx.agent(spec, other) == with_role and fx.agent(spec, same) not in (plain, with_role)
    assert ("PUT", f"/api/v1/agents/{with_role}", {"roleModels": {"default": {
        "modelKey": "mk-b", "modelName": "claude-sonnet-4-5", "provider": "anthropic"}}}) in api.calls
    assert len({c[2]["name"] for c in api.calls if c[1].endswith("/agents/create")}) == 3  # type: ignore[index]


def test_admmerge_a_rows_approval_rules_go_to_the_deployments_document_and_memory_to_the_admins_own_switch(
    tmp_path: Path,
) -> None:
    """The org approval policy and memory policy routes moved out of this repository: a row's ``writes`` and
    ``flow_deny`` are the deployment's rule (seed_config.py in the app container), a memory row the admin's switch."""
    import fixtures

    api, ran = _Api(), []

    def exec_app(*argv: str) -> str:
        ran.append(argv)
        return json.dumps({"default": json.loads(argv[-1])}) + "\n"

    stack = SimpleNamespace(run_dir=tmp_path, api=api, seed={}, env={}, exec_app=exec_app)
    fixtures.Fixtures(stack).apply_org(plan.OrgSettings(writes="ask", flow_deny=True, memory=True))  # type: ignore[arg-type]
    assert ran == [(*fixtures.SEED_CONFIG, "rules", json.dumps({"writes": "ask", "flow": {"level": "deny"}}))]
    assert ("PUT", fixtures.OWN_MEMORY, {"remember": True}) in api.calls
    assert not [c for c in api.calls if "/admin/approvals" in c[1] or "/admin/memory-policy" in c[1]]
    ran.clear()
    fixtures.Fixtures(stack).apply_org(plan.OrgSettings())  # type: ignore[arg-type]
    assert ran == [(*fixtures.SEED_CONFIG, "rules", json.dumps({"writes": None, "flow": None}))]


def test_qa6fix_f13_a_memory_row_is_attended_and_every_engine_has_one_on_a_compose_stack() -> None:
    memory = [r for r in ROWS if r["tool_family"] == "memory"]
    assert memory and all(r["trigger"] == "interactive" for r in memory)
    compose = {r["engine"] for r in memory if r.stack in ("ST-1", "ST-3", "ST-4", "ST-5")}
    assert compose >= {"none", "opencode", "codex", "claude_code"}
    scheduled = {**dict(memory[0].factors), "trigger": "schedule_cron"}
    refused = [c.name for c in matrix.covering.CONSTRAINTS if not c.holds(scheduled)]
    assert "memory_needs_a_person" in refused


def test_qa6fix_f5_no_row_sends_a_mode_the_chat_routes_refuse() -> None:
    assert "image" not in plan.MODES
    assert all(r["mode"] in plan.MODES for r in ROWS)
    assert json.loads((E2E / "known_red.json").read_text())["rows"] == {}


def test_qafinal_pw012_a_rows_process_settings_reach_node_which_pins_the_flow_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QAFINAL-FU-PW012: PW-012 (``flow_guard: off``) set ``HARNESS_FLOW_GUARD=false`` on the worker and the query
    service only. Node, which pins the deployment's flow rule into the run it admits (CONTROL_PLANE_PIN), kept the
    container's ``true``, so the pin held ``flow: ask`` and the card named the flow rule. A row's settings restart
    Node with them too, and Node is supervised on the QA-6 lane so it can be."""
    import lane

    (tmp_path / "control").mkdir()
    (tmp_path / "logs").mkdir()
    restarted: list[str] = []

    def restart_done(predicate: object, *, timeout: float, what: str, **_: object) -> None:
        restarted.append(what)

    monkeypatch.setattr(lane, "wait_until", restart_done)
    runtime = lane.ComposeRuntime("r", tmp_path, {"E2E_PROJECT": "pxh-e2e-r"})
    env = plan.ProcessEnv(flow_guard=False).env()
    assert runtime.set_process_env(env)
    assert (tmp_path / "control" / "node.env").read_text() == "".join(f"{k}={v}\n" for k, v in sorted(env.items()))
    assert "HARNESS_FLOW_GUARD=false" in (tmp_path / "control" / "node.env").read_text()
    assert (tmp_path / "control" / "restart-node").exists()
    assert {w for w in restarted if w.startswith("node")} == {"node to stop", "node to start again"}
    assert not runtime.set_process_env(env), "the same settings restart nothing"
    supervisor = (E2E.parents[2] / "deployment" / "e2e" / "app_from_source.sh").read_text()
    assert '"${E2E_HUMAN:-}" == "1" || "${E2E_SUPERVISE_QUERY:-}" == "1" ]]; then\n  start_supervised node' in supervisor


def test_r31journey_fu7_the_default_chats_agent_mode_asks_for_the_harness_and_its_job_goes_to_the_orgs_engine() -> None:
    """R31JOURNEY-FU-7: a `universal` row is the default chat's Agent mode as the web app sends it (`harness: durable`,
    read from the routing table), and its engine is the org's coding engine, not an agent's."""
    planned = plan.plan(_row(mode="universal", engine="codex"))
    assert (planned.turn.kind, planned.turn.chat_mode, planned.turn.harness) == ("assistant", "agent", "durable")
    assert planned.org.coding_engine == "codex" and planned.process.engine_runs
    quick = plan.plan(_row(mode="quick", engine="codex"))
    assert (quick.turn.harness, quick.org.coding_engine, quick.agent.engine) == (None, "none", "codex")
    assert {m: plan.MODES[m] for m in ("universal", "project")} == {
        "universal": ("assistant", "agent", False), "project": ("assistant", "internal_search", True),
    }
