"""DEEPROUTE: work bigger than a chat reply goes to the coding engine, up front or once the run outgrows a reply.

The router's table (request kind -> route and reason), the escalation thresholds, the director's hand-off at a turn
boundary (the instruction, the journaled reason, the synthetic fallback, one hand-off per turn, never while waiting
for a person), the fallbacks that keep today's loop, and the engine child's grant (the parent's, narrowed only by the
workspace mount) on the real assembler.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.code_task import CODE_TASK_DESCRIPTION, CODE_TASK_GUIDANCE
from app.harness.assembly.deep_route import (
    ENGINE_FAILED_INSTRUCTION,
    ESCALATION_INSTRUCTION,
    HANDOFF_EVENT,
    HANDOFF_SLOT,
    UPFRONT_INSTRUCTION,
    DeepRouteDirector,
    Handoff,
    Measured,
    RouteDecision,
    Thresholds,
    classify,
    escalation,
    measure,
    routes,
)
from app.harness.engines.instructions import ENGINE_TASK_INSTRUCTIONS
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.common import ContentRef
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.durable.director import SYNTHETIC_PROVIDER, SYNTHETIC_TRANSPORT
from harness_core.kernel.durable.ports import TurnResult
from harness_core.kernel.fold import (
    Consumed,
    Invocation,
    ResponseRecord,
    RunState,
    ToolCallRef,
    UsageRecord,
)
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.durable_turns import LoopDirectorContract
from tests.harness_replay.artifacts import ParityArtifactRegistry

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[6]
PROFILE = json.loads((ROOT / "contracts/harness/vectors/engine-profiles.json").read_text())["release"][0]
REF = ContentRef(ref="blob:x", hash="sha256:" + "0" * 64, size=1)
TRIAGE_REQUEST = (
    "many open pull requests are pending on the pipeshub-ai oss repo, which need fixing and give immediate value. "
    "find them and tell me which to merge first"
)
LOG = HarnessLog(logging.getLogger("test.deeproute"), {})


# ---- the router ----

ROUTER_TABLE: list[tuple[str, dict[str, Any], str, str]] = [
    ("What is our PTO policy?", {}, "loop", "short_request"),
    ("Who owns the billing service?", {}, "loop", "short_request"),
    ("Create a Jira ticket for the login bug", {}, "loop", "chat_sized"),
    ("Send a Slack message to #eng that the deploy is done", {}, "loop", "chat_sized"),
    ("Compare Q3 revenue with Q2 for the sales team", {}, "loop", "chat_sized"),
    ("Find the latest React release notes", {}, "loop", "chat_sized"),
    ("Send an email to all vendors about the new invoice address", {}, "loop", "chat_sized"),
    (TRIAGE_REQUEST, {}, "engine", "analysis"),
    ("Research the top 10 vector databases and write a comparison report with pricing", {}, "engine", "research"),
    ("Plan the migration of our CI from Jenkins to GitHub Actions", {}, "engine", "planning"),
    ("Give me a comprehensive report on all open incidents this quarter", {}, "engine", "long_report"),
    ("Investigate why the nightly build fails and list every failing test", {}, "engine", "research"),
    (
        "Compare https://a.example/pricing and https://b.example/pricing and recommend one",
        {},
        "engine",
        "analysis",
    ),
    ("What changed in the travel policy?", {"deep_research": True}, "engine", "research"),
    ("Tell me about our onboarding", {"tier": "deep"}, "engine", "many_steps"),
    ("Tell me about our onboarding process for engineers", {"requirements": 6}, "loop", "chat_sized"),
    (TRIAGE_REQUEST, {"answering_approval": True}, "loop", "approval_answer"),
    (TRIAGE_REQUEST, {"asked_question": True}, "loop", "clarifying_question"),
    ("hi", {}, "loop", "short_request"),
]


@pytest.mark.parametrize(("query", "context", "route", "reason"), ROUTER_TABLE)
def test_dr1_the_router_table(query: str, context: dict[str, Any], route: str, reason: str) -> None:
    decided = classify(query, **context)
    assert (decided.to, decided.reason) == (route, reason), decided


def test_dr1_a_routed_request_names_its_signals_and_round_trips_through_the_slot() -> None:
    decided = classify(TRIAGE_REQUEST)
    assert {"many_items", "analysis"} <= set(decided.signals)
    assert RouteDecision.from_slot(decided.slot()) == decided
    assert RouteDecision.from_slot({"to": "elsewhere"}) is None


def test_dr1_the_model_s_many_requirements_add_a_signal_not_a_route() -> None:
    assert classify("Summarize the onboarding plan for engineers", requirements=6).to == "engine"
    assert classify("Summarize the onboarding doc for engineers", requirements=6).to == "loop"


# ---- thresholds ----


def test_dr2_thresholds_parse_and_keep_defaults() -> None:
    assert Thresholds.parse(None) == Thresholds()
    parsed = Thresholds.parse("toolCalls=20, contextShare=0.7")
    assert (parsed.tool_calls, parsed.context_share, parsed.turns) == (20, 0.7, 6)
    with pytest.raises(ValueError, match="unknown setting"):
        Thresholds.parse("calls=3")
    with pytest.raises(ValueError, match="negative"):
        Thresholds.parse("turns=-1")


@pytest.mark.parametrize(
    ("measured", "reason"),
    [
        (Measured(tool_calls=11, turns=5, context_share=0.49, spend_share=0.29), None),
        (Measured(tool_calls=12, turns=1, context_share=0.0, spend_share=0.0), "many_steps"),
        (Measured(tool_calls=0, turns=6, context_share=0.0, spend_share=0.0), "many_steps"),
        (Measured(tool_calls=0, turns=1, context_share=0.5, spend_share=0.0), "context"),
        (Measured(tool_calls=0, turns=1, context_share=0.0, spend_share=0.3), "budget"),
        (Measured(tool_calls=30, turns=9, context_share=0.9, spend_share=0.5), "budget"),
    ],
)
def test_dr2_each_threshold_and_none_below(measured: Measured, reason: str | None) -> None:
    assert escalation(measured, Thresholds()) == reason


def test_dr2_a_zero_threshold_is_off() -> None:
    off = Thresholds.parse("toolCalls=0,turns=0,contextShare=0,spendShare=0")
    assert escalation(Measured(tool_calls=99, turns=99, context_share=1.0, spend_share=1.0), off) is None


def test_dr2_recorded_run_crosses_a_threshold_at_turn_six() -> None:
    """A recorded run's journal (query.log): calls per turn 1,3,1,3,6 and request sizes; the boundary before turn 6 escalates."""
    per_turn = [(1, 8_860), (3, 9_607), (1, 11_254), (3, 12_456), (6, 31_913)]
    state = _state(per_turn)
    measured = measure(state, window_tokens=128_000, max_tokens=1_000_000)
    assert measured.tool_calls == 14 and measured.turns == 5
    assert escalation(measured, Thresholds()) == "many_steps"
    assert escalation(measure(_state(per_turn[:4]), window_tokens=128_000, max_tokens=1_000_000), Thresholds()) is None


# ---- the director ----


def _director(**overrides: Any) -> DeepRouteDirector:  # noqa: ANN401
    values: dict[str, Any] = {
        "query": TRIAGE_REQUEST,
        "thresholds": Thresholds(),
        "window_tokens": 128_000,
        "max_tokens": 1_000_000,
        "max_turns": 15,
        "log": LOG,
    }
    values.update(overrides)
    return DeepRouteDirector(**values)


def _state(
    per_turn: list[tuple[int, int]],
    *,
    route: RouteDecision | None = None,
    answered_last: bool = False,
    tool: str = "fetch_url",
    slots: dict[str, Any] | None = None,
    consumed: int = 0,
    waiting: bool = False,
) -> RunState:
    """A run after ``per_turn`` model turns: (tool calls, request input tokens) each; every call completed."""
    responses: list[ResponseRecord] = []
    invocations: dict[str, Invocation] = {}
    for turn, (calls, tokens) in enumerate(per_turn, start=1):
        answered = answered_last and turn == len(per_turn)
        refs = [] if answered else [ToolCallRef(call_id=f"c{turn}.{i}", name=tool, args_digest="d") for i in range(calls)]
        responses.append(
            ResponseRecord(
                seq=turn * 10, turn=turn, message_ref=REF, provider="openai", transport="t", tool_calls=refs,
                usage=UsageRecord(input_tokens=tokens, output_tokens=100),
            )
        )
        for i, ref in enumerate(refs):
            invocations[f"i{turn}.{i}"] = Invocation(
                invocation_id=f"i{turn}.{i}", turn=turn, index=i, call_id=ref.call_id, name=tool, args_digest="d",
                state="completed", status="ok",
            )
    if waiting and invocations:
        last = list(invocations)[-1]
        invocations[last] = invocations[last].model_copy(update={"state": "awaiting_approval"})
    goal: dict[str, Any] = {"description": "Triage the open pull requests of pipeshub-ai."}
    if route is not None:
        goal["route"] = route.slot()
    return RunState(
        run_id="run-1",
        turn=len(per_turn),
        turn_completed=len(per_turn),
        responses=responses,
        invocations=invocations,
        slots={"pre_run.goal": goal, **(slots or {})},
        consumed=Consumed(input_tokens=consumed),
    )


def test_dr3_a_routed_request_is_handed_off_before_the_first_model_turn() -> None:
    plan = _director().before(1, _state([], route=classify(TRIAGE_REQUEST)), [])
    assert plan.synthetic is None
    assert plan.inject == (), "harness control rides the harness channel (WP93-STEER)"
    (instruction,) = (i.text for i in plan.harness)
    assert instruction == UPFRONT_INSTRUCTION.format(why="analysis across many items")
    assert "web search" in instruction and "code_task" in instruction
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(turn=1, trigger="upfront", reason="analysis")
    (event,) = plan.events
    assert (event.type, event.data, event.critical) == (
        HANDOFF_EVENT,
        {"trigger": "upfront", "reason": "analysis"},
        False,
    )


def test_dr3_a_loop_request_is_left_alone() -> None:
    plan = _director().before(1, _state([], route=classify("What is our PTO policy?")), [])
    assert (plan.inject, plan.harness, plan.synthetic, dict(plan.slots), plan.events) == ((), (), None, {}, ())


def test_dr3_a_run_that_outgrew_a_reply_is_escalated_while_still_gathering() -> None:
    state = _state([(1, 8_860), (3, 9_607), (1, 11_254), (3, 12_456), (6, 31_913)])
    plan = _director().before(6, state, [])
    (instruction,) = (i.text for i in plan.harness)
    assert instruction == ESCALATION_INSTRUCTION.format(why="it has taken many steps")
    assert "what you found so far" in instruction and "[source](refN)" in instruction
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(turn=6, trigger="escalation", reason="many_steps")
    assert plan.events[0].data == {"trigger": "escalation", "reason": "many_steps"}


def test_dr3_context_and_spend_name_their_reason() -> None:
    assert _director().before(3, _state([(1, 9_000), (1, 70_000)]), []).slots[HANDOFF_SLOT]["reason"] == "context"
    spent = _state([(1, 9_000), (1, 9_000)], consumed=400_000)
    assert _director().before(3, spent, []).slots[HANDOFF_SLOT]["reason"] == "budget"


@pytest.mark.parametrize(
    ("label", "state", "turn", "director"),
    [
        ("the model answered", _state([(4, 9_000)] * 4 + [(4, 9_000)], answered_last=True), 6, {}),
        ("two turns or fewer left", _state([(4, 9_000)] * 5), 6, {"max_turns": 6}),
        ("waiting for a person", _state([(4, 9_000)] * 5, waiting=True), 6, {}),
        ("a code task already ran", _state([(4, 9_000)] * 5, tool="code_task"), 6, {}),
        (
            "handed off once already",
            _state([(4, 9_000)] * 5, slots={HANDOFF_SLOT: Handoff(2, "escalation", "many_steps", taken=3).slot()}),
            6,
            {},
        ),
        ("thresholds off", _state([(4, 9_000)] * 5), 6, {"thresholds": Thresholds.parse("toolCalls=0,turns=0")}),
    ],
)
def test_dr3_never_escalates(label: str, state: RunState, turn: int, director: dict[str, Any]) -> None:
    plan = _director(**director).before(turn, state, [])
    assert (plan.inject, plan.harness, plan.synthetic) == ((), (), None), label


def test_dr4_a_model_that_ignores_the_instruction_is_handed_off_by_the_director_once() -> None:
    asked = Handoff(turn=6, trigger="escalation", reason="many_steps")
    state = _state([(4, 9_000)] * 6, slots={HANDOFF_SLOT: asked.slot()})
    previous = [
        TurnResult(name="fetch_url", call_id="c6.0", status="ok", content={"url": "https://github.com/x/pull/1"}),
        TurnResult(name="fetch_url", call_id="c6.1", status="ok", content="x" * 20_000),
    ]
    plan = _director().before(7, state, previous)
    assert (plan.inject, plan.harness) == ((), ())
    assert plan.synthetic is not None
    (call,) = plan.synthetic.tool_calls or []
    assert call.name == "code_task" and call.id == "deeproute-7"
    goal = call.arguments["goal"]
    assert goal.startswith("Triage the open pull requests of pipeshub-ai.")
    # HANDOFF-CTX: the results go to the engine with the code_task itself (``Gathered``, every earlier call with its
    # arguments, test_handoffctx_code_task), never copied into the goal a second time.
    assert "https://github.com/x/pull/1" not in goal
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(6, "escalation", "many_steps", taken=7)
    # Once taken, never again; a later boundary is left to the model.
    taken = _state([(4, 9_000)] * 7, slots=dict(plan.slots))
    assert _director().before(8, taken, []).synthetic is None


def test_dr4_a_model_that_answered_instead_ends_the_run_with_its_answer() -> None:
    asked = Handoff(turn=6, trigger="escalation", reason="many_steps")
    state = _state([(4, 9_000)] * 5 + [(0, 9_000)], answered_last=True, slots={HANDOFF_SLOT: asked.slot()})
    plan = _director().before(7, state, [])
    assert (plan.inject, plan.harness, plan.synthetic) == ((), (), None)


def test_dr4_the_engine_s_answer_is_the_answer_of_a_run_it_handed_off() -> None:
    answer = "Merge PR 41 first [PR 41](https://github.com/pipeshub-ai/pipeshub-ai/pull/41); see triage.md."
    asked = Handoff(turn=1, trigger="upfront", reason="analysis")
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    ok = TurnResult(name="code_task", call_id="c1.0", status="ok", content={"success": True, "output": answer})
    plan = _director().before(2, state, [ok])
    assert plan.synthetic is not None and not plan.synthetic.tool_calls
    assert plan.synthetic.content[0].text == answer  # type: ignore[union-attr]
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(1, "upfront", "analysis", answered=2)
    # Once answered, nothing more; an engine that failed or said nothing leaves the turn to the model.
    assert _director().before(3, _state([(1, 9_000)], tool="code_task", slots=dict(plan.slots)), [ok]).synthetic is None
    failed = TurnResult(name="code_task", call_id="c1.0", status="error", content={"success": False, "output": "x"})
    empty = TurnResult(name="code_task", call_id="c1.0", status="ok", content={"success": True, "output": " "})
    for result in (failed, empty):
        assert _director().before(2, state, [result]).synthetic is None
    # A code_task the model chose on its own (no hand-off) is presented by the model, as before.
    assert _director().before(2, _state([(1, 9_000)], tool="code_task"), [ok]).synthetic is None


def test_dr4_childref_a_failed_hand_off_is_told_to_the_person_in_one_plain_line() -> None:
    """CHILDREF: a Codex child was quarantined and the parent answered from its own results without saying
    the engine had failed. The turn that takes it back is asked to say so, as a cut-short answer is (ENGBUDGET)."""
    asked = Handoff(turn=6, trigger="escalation", reason="many_steps")
    state = _state([(4, 9_000)] * 5 + [(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    crashed = {"error": "crash_loop", "output": "", "success": False}
    for failed in (
        TurnResult(name="code_task", call_id="c6.0", status="ok", content=crashed),
        TurnResult(name="code_task", call_id="c6.0", status="error", content="code_task failed"),
    ):
        plan = _director().before(7, state, [failed])
        assert [i.text for i in plan.harness] == [ENGINE_FAILED_INSTRUCTION] and plan.inject == ()
        assert plan.synthetic is None and plan.cut_short is None
        # Not marked answered: a re-driven turn is asked again, and the next boundary has no code_task result.
        assert dict(plan.slots) == {}
    assert "one" in ENGINE_FAILED_INSTRUCTION and "coding engine" in ENGINE_FAILED_INSTRUCTION
    assert "crash_loop" not in ENGINE_FAILED_INSTRUCTION
    # An engine that answered, or one the model called on its own, is not flagged.
    ok = TurnResult(name="code_task", call_id="c6.0", status="ok", content={"success": True, "output": "done"})
    assert _director().before(7, state, [ok]).harness == ()
    unrouted = _state([(4, 9_000)] * 5 + [(1, 9_000)], tool="code_task")
    assert _director().before(7, unrouted, [TurnResult(name="code_task", call_id="c6.0", status="ok", content=crashed)]).harness == ()


def test_dr4_synthetic_turns_do_not_count_as_model_turns() -> None:
    state = _state([(1, 9_000)])
    state.responses.append(
        ResponseRecord(seq=99, turn=2, message_ref=REF, provider=SYNTHETIC_PROVIDER, transport=SYNTHETIC_TRANSPORT)
    )
    assert measure(state, window_tokens=128_000, max_tokens=1_000_000).turns == 1


# ---- prompts ----


def test_dr5_code_task_is_for_deep_work_and_still_answers_simple_questions_directly() -> None:
    for phrase in ("research", "planning", "many tool calls", "web search", "directly"):
        assert phrase in CODE_TASK_DESCRIPTION, phrase
    assert "Answer simple questions" in CODE_TASK_GUIDANCE and "call it early" in CODE_TASK_GUIDANCE
    assert "keeping its citations" in CODE_TASK_GUIDANCE


def test_dr5_the_engine_reaches_the_web_through_its_tools_and_keeps_notes() -> None:
    assert "no direct internet" in ENGINE_TASK_INSTRUCTIONS
    assert "notes/" in ENGINE_TASK_INSTRUCTIONS and "ph call" in ENGINE_TASK_INSTRUCTIONS


# ---- fallbacks and the assembled run ----


@pytest.fixture
def deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1"))
        )
        yield


def _spec(*, code_task: bool = True, auto_route: bool | None = None) -> RunSpec:
    spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    if not code_task:
        return spec
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {
        "profile": PROFILE,
        **({"autoRoute": auto_route} if auto_route is not None else {}),
    }
    raw["budgets"]["maxChildren"] = 1
    return validate_contract(RunSpec, raw)


def _context(spec: RunSpec, **claim: Any) -> Any:  # noqa: ANN401
    from app.harness.assembly.context import RunContext

    return RunContext.from_claim(w.claim(spec, **claim))


@pytest.mark.parametrize(
    ("label", "spec", "claim", "env", "expected"),
    [
        ("an engine is pinned", {}, {}, {}, True),
        ("no engine: code_task not offered", {"code_task": False}, {}, {}, False),
        ("the org turned it off", {"auto_route": False}, {}, {}, False),
        ("the org left it on", {"auto_route": True}, {}, {}, True),
        ("the operator turned it off", {}, {}, {"HARNESS_DEEP_ROUTING": "false"}, False),
        ("deep mode keeps its orchestrator", {}, {"chat_mode": "deep"}, {}, False),
    ],
)
def test_dr6_who_routes(
    label: str, spec: dict[str, Any], claim: dict[str, Any], env: dict[str, str], expected: bool
) -> None:
    with patch.dict(os.environ, env):
        assert routes(_context(_spec(**spec), **claim)) is expected, label


async def test_dr6_the_assembled_loop_run_carries_the_director_and_the_child_inherits_the_grant(
    deployment: None,
) -> None:
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.delegation import Delegated, DelegationView
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    runtime = await assembler.build(w.claim(_spec(), query=TRIAGE_REQUEST))  # type: ignore[arg-type]
    try:
        assert isinstance(runtime.director, DeepRouteDirector)
        call = ToolCall(id="deeproute-6", name="code_task", arguments={"goal": "Triage the PRs."})
        decided = runtime.delegation.decide(
            Delegated(invocation_id="t6.0", call=call, turn=6, state="pending"),  # type: ignore[arg-type]
            DelegationView(batch=()),
        )
        # Only the workspace mount is named: tools (web search and fetch included), skills, knowledge and access are
        # the parent's, which Node narrows the child to (grant ⊆ parent); the kernel adds the inherited label.
        grants = decided.grants  # type: ignore[union-attr]
        assert grants.wire() == {"mounts": ["/workspace"]}
        assert decided.executor is not None and decided.executor.kind == "engine"  # type: ignore[union-attr]
        goal_step = next(s for s in runtime.pre_run if s.key == "goal")
        slot = await goal_step.run(TRIAGE_REQUEST)
        assert slot["route"] == {"to": "engine", "reason": "analysis", "signals": ["analysis", "many_items"]}
    finally:
        await runtime.close()

    off = await assembler.build(w.claim(_spec(auto_route=False), query=TRIAGE_REQUEST))  # type: ignore[arg-type]
    try:
        # SPENDCONT: the director stays to present a cut-short code_task, but never hands off on its own.
        assert isinstance(off.director, DeepRouteDirector) and not off.director.routing
        assert "route" not in await next(s for s in off.pre_run if s.key == "goal").run(TRIAGE_REQUEST)
    finally:
        await off.close()


async def test_dr6_code_task_s_outcome_keeps_the_engine_s_citations(deployment: None) -> None:
    from app.harness.assembly.code_task import CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    answer = (
        "Merge #41 first: it fixes the crash [source](ref3). The docs PR is ready "
        "([PR 52](https://github.com/pipeshub-ai/pipeshub-ai/pull/52))."
    )
    call = ToolCall(id="c1", name="code_task", arguments={"goal": "Triage the PRs."})
    outcome = await CodeTaskDelegation().outcome(call, {"status": "answered", "output": answer})
    shown = outcome.content
    assert isinstance(shown, dict)
    assert "[source](ref3)" in shown["output"]
    assert "(https://github.com/pipeshub-ai/pipeshub-ai/pull/52)" in shown["output"]


# ---- the kernel's port contract ----


class _Directors:
    """DP14's harness: a run the goal step routed to the engine."""

    def fresh(self) -> DeepRouteDirector:
        return _director()

    def state(self) -> RunState:
        return _state([], route=classify(TRIAGE_REQUEST))


class TestDeepRouteDirector(LoopDirectorContract):
    @pytest.fixture
    def directors(self) -> _Directors:  # type: ignore[override]
        return _Directors()
