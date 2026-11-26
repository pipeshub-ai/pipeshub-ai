"""K-L DD-1..DD-9, CA-S1..S3, CI-1: deep mode's spawn_agent decided as legacy's phase gate
and spawn scheduler decide a batch, the sub-agent a child runs as, citations across the join."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.loops import orchestrator as legacy
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.deep import (
    CITATIONS_KEY,
    DEEP_FAN_OUT_PER_TURN,
    DEEP_PHASE_SLOT,
    DETACH_REFUSED,
    DeepDelegation,
    DeepPhase,
    SubAgentResult,
)
from harness_core.journal.children import ChildRequest
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import (
    Delegated,
    DelegationView,
    Refuse,
    Wait,
)
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

APPROVED = {DEEP_PHASE_SLOT: DeepPhase(phase="programmatic", approved=True).slot()}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _spec(**extension: Any) -> Any:  # noqa: ANN401
    agent = w.agent_doc()
    spec = w.pinned_spec(
        tool_ids=["jira@" + w.JIRA + "/search_issues", "jira@" + w.JIRA + "/create_issue"],
        kb=["kb-1"],
    )
    spec = spec.model_copy(update={"budgets": spec.budgets.model_copy(update={"max_children": 16})})
    if extension:
        ext = dict(spec.extensions["x.pipeshub"])
        ext.update(extension)
        spec = spec.model_copy(update={"extensions": {"x.pipeshub": ext}})
    return agent, spec


async def _runtime(chat_mode: str = "deep", **extension: Any) -> Any:  # noqa: ANN401
    agent, spec = _spec(**extension)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query="find the bug", chat_mode=chat_mode)
    return await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(claim)


def _call(call_id: str, **arguments: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id=call_id, name="spawn_agent", arguments={"role": "r", "goal": "g", "reasoning": "x", **arguments})


def _batch(*calls: ToolCall, slots: dict[str, Any] | None = None, earlier: tuple[Delegated, ...] = ()) -> DelegationView:
    batch = tuple(Delegated(f"t2.{i}", call, 2, "pending") for i, call in enumerate(calls))
    return DelegationView(batch, earlier, APPROVED if slots is None else slots)


def _decide(delegation: DeepDelegation, view: DelegationView, index: int = 0) -> Any:  # noqa: ANN401
    return delegation.decide(view.batch[index], view)


async def test_dd1_the_phase_gate_refuses_a_spawn_before_the_plan_is_approved() -> None:
    delegation = (await _runtime()).delegation
    assert _decide(delegation, _batch(_call("c1"), slots={})) == Refuse(legacy.SPAWN_BLOCKED)
    plan_phase = {DEEP_PHASE_SLOT: DeepPhase().slot()}
    assert _decide(delegation, _batch(_call("c1"), slots=plan_phase)) == Refuse(legacy.SPAWN_BLOCKED)


async def test_dd2_legacy_batch_validation_refuses_with_legacys_messages() -> None:
    from app.agent_loop_lib.agent.spawn_scheduler import validate_spawn_batch

    delegation = (await _runtime()).delegation
    cases = [
        (_call("c1", task_id="a"), _call("c2", task_id="a")),
        (_call("c1", task_id="a", depends_on=["missing"]),),
        (_call("c1", task_id="a", depends_on=["a"]),),
        (_call("c1", task_id="a", depends_on=["b"]), _call("c2", task_id="b", depends_on=["a"])),
        (_call("c1", task_id="a", tools=["no_such_tool"]),),
    ]
    for calls in cases:
        view = _batch(*calls)
        expected = validate_spawn_batch(list(calls), set(), delegation._registry)  # pyright: ignore[reportPrivateUsage]
        for index, call in enumerate(calls):
            error = expected.errors_by_call_id.get(call.id)
            if error is not None:
                assert _decide(delegation, view, index) == Refuse(error)
        assert expected.errors_by_call_id


async def test_dd3_a_task_that_succeeded_earlier_is_not_dispatched_again() -> None:
    delegation = (await _runtime()).delegation
    done = Delegated("t1.0", _call("c0", task_id="a"), 1, "joined", {"output": "x", "success": True})
    assert _decide(delegation, _batch(_call("c1", task_id="a"), earlier=(done,))) == Refuse(
        legacy.task_already_completed("a")
    )
    failed = Delegated("t1.0", _call("c0", task_id="a"), 1, "joined", {"output": "", "success": False})
    assert isinstance(_decide(delegation, _batch(_call("c1", task_id="b", depends_on=["a"]), earlier=(failed,))), Refuse)


async def test_dd4_the_grant_is_the_definitions_tools_from_the_parents_grant() -> None:
    delegation = (await _runtime()).delegation
    jira = _decide(delegation, _batch(_call("c1", tools=["jira"])))
    assert isinstance(jira, ChildRequest)
    assert jira.grants.tools == tuple(sorted(f"jira@{w.JIRA}/{n}" for n in ("create_issue", "search_issues")))
    one = _decide(delegation, _batch(_call("c1", tools=["jira__search_issues"])))
    assert one.grants.tools == (f"jira@{w.JIRA}/search_issues",)
    pool = _decide(delegation, _batch(_call("c1")))
    # No `tools`: the whole spawn pool, i.e. every tool the parent holds.
    assert pool.definition["toolNames"] and pool.grants.tools is not None
    assert set(pool.grants.tools) == {f"jira@{w.JIRA}/{n}" for n in ("create_issue", "search_issues")}
    analysis = _decide(delegation, _batch(_call("c1", tools=[])))
    assert analysis.definition["toolNames"] == [] and analysis.grants.tools is None


async def test_dd5_another_model_or_detach_is_refused_never_ignored() -> None:
    delegation = (await _runtime()).delegation
    assert _decide(delegation, _batch(_call("c1", detach=True))) == Refuse(DETACH_REFUSED)
    other = _decide(delegation, _batch(_call("c1", model="other-model")))
    assert isinstance(other, Refuse) and "other-model" in str(other.content)
    same = _decide(delegation, _batch(_call("c1", model="gpt-x")))
    assert isinstance(same, ChildRequest)


async def test_dd6_fan_out_per_turn_is_capped() -> None:
    delegation = (await _runtime()).delegation
    calls = [_call(f"c{i}", task_id=f"t{i}") for i in range(DEEP_FAN_OUT_PER_TURN + 2)]
    view = _batch(*calls)
    decisions = [_decide(delegation, view, i) for i in range(len(calls))]
    assert all(isinstance(d, ChildRequest) for d in decisions[:DEEP_FAN_OUT_PER_TURN])
    assert all(isinstance(d, Refuse) for d in decisions[DEEP_FAN_OUT_PER_TURN:])


async def test_dd7_a_dependent_waits_then_carries_its_prerequisites_output() -> None:
    from app.agent_loop_lib.agent.spawn_scheduler import (  # pyright: ignore[reportPrivateUsage]
        _augment_goal,
        _format_dependency_section,
    )
    from app.agent_loop_lib.core.types import AgentResult, Goal

    delegation = (await _runtime()).delegation
    first, second = _call("c1", task_id="a"), _call("c2", task_id="b", depends_on=["a"], goal="summarize")
    waiting = _batch(first, second)
    assert _decide(delegation, waiting, 1) == Wait(("t2.0",))
    joined = DelegationView(
        (
            Delegated("t2.0", first, 2, "joined", {"output": "rows " * 10, "success": True}),
            Delegated("t2.1", second, 2, "pending"),
        ),
        (),
        APPROVED,
    )
    request = _decide(delegation, joined, 1)
    section = _format_dependency_section("a", AgentResult(goal=Goal(description=""), output="rows " * 10, success=True))
    assert request.goal == _augment_goal(Goal(description="summarize"), [section]).description
    failed = DelegationView(
        (
            Delegated("t2.0", first, 2, "joined", {"output": "", "success": False, "error": "worker_failed"}),
            Delegated("t2.1", second, 2, "pending"),
        ),
        (),
        APPROVED,
    )
    refused = _decide(delegation, failed, 1)
    assert isinstance(refused, Refuse) and str(refused.content).startswith("prerequisite task 'a' failed (worker_failed)")


async def test_dd8_what_the_model_reads_of_a_joined_child() -> None:
    delegation = (await _runtime()).delegation
    llm = await delegation.outcome(_call("call-1"), {"confidence": "high", "output": "found", "success": True})
    assert llm.content == json.dumps({"output": "found", "success": True, "confidence": "high"})
    assert not llm.error
    programmatic = await delegation.outcome(
        _call("programmatic_spawn_a_0123abcd"),
        {"output": "found", "success": True, "needs_input": "This sub-agent could not fully complete its task — it needs: x"},
    )
    assert programmatic.content == "found\n\n[ESCALATION] This sub-agent could not fully complete its task — it needs: x"
    failed = await delegation.outcome(_call("programmatic_spawn_a_0123abcd"), {"error": "auth_error", "output": "", "success": False})
    assert (failed.content, failed.error) == ("auth_error", True)
    failed_llm = await delegation.outcome(_call("call-1"), {"error": "cancelled", "output": "", "success": False})
    assert json.loads(failed_llm.content) == {"output": "", "success": False, "error": "cancelled"} and failed_llm.error


async def test_dm1_the_orchestrator_reads_what_merging_the_childs_fork_did() -> None:
    from harness_core.workspace.forks import ChildMergeReport

    delegation = (await _runtime()).delegation
    report = ChildMergeReport(
        child_run_id="c",
        fork_id="ws:s:fork:c",
        into="ws:s",
        status="partial",
        revision=3,
        fork_revision=2,
        merged=("b.md",),
        conflicts=(("a.md", "both_changed"),),
    )
    document = {"output": "found", "success": True}
    llm = await delegation.outcome(_call("call-1"), document, workspace=report)
    shown = json.loads(llm.content)
    assert list(shown) == ["output", "success", "workspace"]
    assert shown["workspace"] == report.as_json()
    programmatic = await delegation.outcome(_call("programmatic_spawn_a_0123abcd"), document, workspace=report)
    assert programmatic.content == f"found\n\n[WORKSPACE] {report.summary()}"
    assert "a.md (both_changed)" in programmatic.content and "ws:s:fork:c revision 2" in programmatic.content
    plain = await delegation.outcome(_call("call-1"), document)
    assert plain.content == json.dumps(document)


async def test_dd9_the_definition_and_the_budget_share() -> None:
    runtime = await _runtime()
    request = _decide(runtime.delegation, _batch(_call("c1", role="tickets", tools=["jira"]), _call("c2")))
    assert request.definition["role"] == "tickets"
    assert request.grants.max_turns == 8 and request.grants.max_children == 0
    assert request.grants.max_tokens == 1_000_000 // 3


async def test_cas1_a_child_runs_as_the_role_sub_agent_it_was_pinned_as() -> None:
    from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt

    runtime = await _runtime(subAgent={"role": "tickets", "toolNames": ["jira__search_issues", "internaltools__ask_user_question"]})
    assert runtime.delegation is None and runtime.director is None
    assert isinstance(runtime.child_result, SubAgentResult)
    spec = runtime.shaper.spec({})
    assert spec.name == "pipeshub-subagent-tickets"
    # UI-only tools never reach a child; fetch_record, which the loop granted once a tool returned records, is in
    # the child's list from the start (R4: the list is fixed at the session's first request).
    assert spec.tool_names == ["jira__search_issues", "knowledgegraph__fetch_record"]
    assert isinstance(spec.system_prompt, str) and "## Execution Rules" in spec.system_prompt
    import re

    clock = re.compile(r"\d{2}:\d{2}:\d{2}\.\d+")
    expected = build_sub_agent_prompt("tickets", runtime.shaper._env.context)  # pyright: ignore[reportPrivateUsage]
    assert clock.sub("<now>", spec.system_prompt) == clock.sub("<now>", expected)
    from app.harness.assembly.loop import SubAgentGoalStep

    assert [type(s) for s in runtime.pre_run][-1] is SubAgentGoalStep


async def test_cas2_a_domain_agent_child_runs_as_that_agent_and_keeps_its_result_note() -> None:
    from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool

    parent = await _runtime()
    registry = parent.delegation._registry  # pyright: ignore[reportPrivateUsage]
    agents = [n for n in registry.names() if isinstance(registry.resolve_by_name(n), AgentTool)]
    assert agents, "the golden agent composes at least one domain agent"
    name = agents[0]
    request = _decide(parent.delegation, _batch(_call("c1", tools=[name])))
    assert request.definition["domainAgent"] == name
    tool = registry.resolve_by_name(name)
    assert request.definition["toolNames"] == list(tool.spec.tool_names)

    child = await _runtime(subAgent=request.definition)
    spec = child.shaper.spec({})
    assert spec.name == tool.spec.name
    shaped = await child.child_result.shape({"output": "found it", "success": True}, "answered")
    expected = child.child_result._domain_tool.apply_result_note("found it")  # pyright: ignore[reportPrivateUsage]
    assert shaped["output"] == expected


async def test_ci1b_a_child_hands_back_every_source_it_cited() -> None:
    from app.utils.chat_helpers import CitationRefMapper

    child = await _runtime(subAgent={"role": "r", "toolNames": []})
    context = child.child_result._context  # pyright: ignore[reportPrivateUsage]
    mapper = context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    _ = mapper.get_or_create_ref("https://records/x/0")
    shaped = await child.child_result.shape({"output": "x [source](ref1)", "success": True}, "answered")
    assert shaped[CITATIONS_KEY]["refs"] == [{"url": "https://records/x/0", "ref": "ref1"}]
    bare = await (await _runtime(subAgent={"role": "r", "toolNames": []})).child_result.shape(
        {"output": "x", "success": True}, "answered"
    )
    assert CITATIONS_KEY not in bare


async def test_cas3_a_child_without_a_definition_assembles_as_before() -> None:
    runtime = await _runtime(chat_mode="react")
    assert runtime.delegation is None and runtime.child_result is None


async def test_ci1_a_childs_citations_are_renumbered_into_the_parents_answer() -> None:
    from app.utils.chat_helpers import CitationRefMapper

    runtime = await _runtime()
    delegation: DeepDelegation = runtime.delegation
    context = delegation._citations._context  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    mapper = context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    _ = mapper.get_or_create_ref("https://records/parent/0")
    child_a = {
        "output": "Leave is 25 days [source](ref1); see ref2.",
        "success": True,
        CITATIONS_KEY: {
            "refs": [{"url": "https://records/a/0", "ref": "ref1"}, {"url": "https://records/a/1", "ref": "ref2"}],
            "knownRecordIds": ["rec-a"],
        },
    }
    child_b = {
        "output": "T-42 is resolved [source](ref1).",
        "success": True,
        CITATIONS_KEY: {"refs": [{"url": "https://records/a/1", "ref": "ref1"}]},
    }
    a = await delegation.outcome(_call("programmatic_spawn_a_00000000"), child_a)
    b = await delegation.outcome(_call("programmatic_spawn_b_00000000"), child_b)
    assert a.content == "Leave is 25 days [source](ref2); see ref3."
    # The same source cited by another child keeps its one parent ref.
    assert b.content == "T-42 is resolved [source](ref3)."
    assert mapper.url_to_ref == {
        "https://records/parent/0": "ref1",
        "https://records/a/0": "ref2",
        "https://records/a/1": "ref3",
    }
    (event,) = a.events
    assert event.type == "x.pipeshub.citations.recorded"
    assert {(r["url"], r["ref"]) for r in event.data["refs"]} >= {("https://records/a/0", "ref2")}
    assert "rec-a" in context.tool_state["known_record_ids"]
    assert b.events == ()  # nothing new: the parent already held that source
