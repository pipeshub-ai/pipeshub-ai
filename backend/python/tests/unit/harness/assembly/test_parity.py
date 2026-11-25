"""D-1: the assembler builds the tool set PipesHubAgentFactory builds for the same agent.

Each golden agent is pinned with its whole grant (no client narrowing), and
both sides get the same trusted environment (``build_tool_environment``).
The durable set differs from the factory's only by what the pin withholds by
design: ``run_code`` and its companions while ``grants.sandboxProfile`` is
``none``. ``search_tools`` runs as a durable route (PA-2).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.plan import PLAN_TOOL
from app.harness.assembly.tool_environment import (
    build_tool_environment,
    toolset_tool_id,
)
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.agents.adapter.conftest import FakeChatModel

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.contracts.generated.run_spec import RunSpec

SANDBOX = frozenset({"run_code", "install_packages", "read_sandbox_file"})


def _full_grant(agent: dict[str, Any] | None) -> dict[str, Any]:
    if agent is None:
        return {"tool_ids": [], "agent": False, "kb": []}
    tool_ids = [
        toolset_tool_id(toolset, tool["name"])
        for toolset in agent.get("toolsets") or []
        for tool in toolset["tools"]
    ]
    tool_ids += [
        f"mcp@{server['instanceId']}/{tool['name']}"
        for server in agent.get("mcpServers") or []
        for tool in server["tools"]
    ]
    kb = [k["connectorId"] for k in agent.get("knowledge") or [] if k.get("type") == "KB"]
    return {
        "tool_ids": tool_ids,
        "mcp": [s["instanceId"] for s in agent.get("mcpServers") or []],
        "kb": kb,
    }


GOLDEN_AGENTS: dict[str, dict[str, Any] | None] = {
    "connectors_mcp_knowledge": w.agent_doc(),
    "connector_only": w.agent_doc(mcpServers=[], knowledge=[]),
    "knowledge_only": w.agent_doc(toolsets=[], mcpServers=[]),
    "assistant_empty_grant": None,
}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


@pytest.mark.parametrize("name", sorted(GOLDEN_AGENTS))
async def test_d1_same_tool_set_as_the_legacy_factory(name: str) -> None:
    """TD-1: what the model is offered on turn 1, meta tools included."""
    agent = GOLDEN_AGENTS[name]
    services = w.Services(agent=agent or {})
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, _slots, shaped = await _durable_turn_one(services, spec, "quick", "find the bug")
    durable = {schema.name for schema in shaped.tools or []}
    legacy_agent, legacy_runtime, _goal = await _legacy(w.Services(agent=agent or {}), spec, "quick", "find the bug")
    legacy = set(legacy_agent.spec.tool_names)

    assert legacy_runtime.tool_registry is not None
    # UXR-B1, R49-PLAN (ADR-OWD-07 D-PLAN-1): the durable quick loop offers its plan tool, which legacy never had.
    assert durable == legacy - SANDBOX | {PLAN_TOOL}
    assert "search_tools" in durable
    # Every listed tool can run (fetch_record is registered with the run's first request and described then, R4).
    assert all(runtime.tools.describe(n) is not None for n in durable)
    assert durable, "a golden agent always has at least its platform tools"


async def _durable_turn_one(
    services: w.Services, spec: RunSpec, chat_mode: str, query: str
) -> tuple[Any, dict[str, Any], Any]:
    """Assemble, run the pre-run steps the kernel would run once, shape turn 1."""
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
    from harness_core.kernel.fold import RunState

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query=query, chat_mode=chat_mode)
    runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(claim)
    slots: dict[str, Any] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(query)
    state = RunState(run_id="run-1", slots=slots)
    shaped = await runtime.shaper.shape(ModelCall([], None, None, slots), 1, state)  # type: ignore[union-attr]
    return runtime, slots, shaped


async def _legacy(services: w.Services, spec: RunSpec, chat_mode: str, query: str) -> tuple[Any, Any, Any]:
    run = RunContext.from_claim(w.claim(spec, query=query, chat_mode=chat_mode))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    model_factory = w.model_factory(port)
    env = await build_tool_environment(run, services, await model_factory(run, services))
    agent, runtime, goal, _questions = await PipesHubAgentFactory().create(
        env.context, FakeChatModel(), chat_mode, query=query, model_name="gpt-x", model_key="model-1"
    )
    return agent, runtime, goal


@pytest.mark.parametrize("chat_mode", ["quick", "react"])
async def test_pr2_the_journaled_goal_is_the_legacy_goal(chat_mode: str) -> None:
    """The goal step runs `select_loop_and_goal` itself (quick: the query and
    inferred criteria; react: the intent call's rewrite)."""
    query = "Compare the Q3 and Q4 revenue and list all open deals"
    agent = w.agent_doc()
    services = w.Services(agent=agent)
    spec = w.pinned_spec(**_full_grant(agent))
    _, slots, _ = await _durable_turn_one(services, spec, chat_mode, query)
    _, _, legacy_goal = await _legacy(w.Services(agent=agent), spec, chat_mode, query)
    goal = slots["pre_run.goal"]
    assert goal["description"] == legacy_goal.description
    assert goal["requirements"] == legacy_goal.requirements
    assert goal["successCriteria"] == legacy_goal.success_criteria
    assert goal["constraints"] == legacy_goal.constraints
    assert goal["mode"] == chat_mode
    assert goal["composeDomainAgents"] is (chat_mode == "react")


async def test_pr3_react_composes_the_legacy_domain_agents() -> None:
    """react: the model sees the factory's composed grant (delegates plus the
    residual tools), and the composition is journaled as a pre-run slot."""
    agent = w.agent_doc()
    services = w.Services(agent=agent)
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, shaped = await _durable_turn_one(services, spec, "react", "find the bug")
    legacy_agent, _legacy_runtime, _goal = await _legacy(w.Services(agent=agent), spec, "react", "find the bug")

    composition = slots["pre_run.composition"]
    assert composition["delegates"] == ["internal_exploration_agent"]
    assert "knowledgegraph__search" in composition["claims"]["internal_exploration_agent"]
    durable = {schema.name for schema in shaped.tools or []}
    # UXR-B1: the durable react loop offers its plan tool, which legacy never had.
    assert durable == set(legacy_agent.spec.tool_names) - SANDBOX | {PLAN_TOOL}
    assert "internal_exploration_agent" in durable
    assert "knowledgegraph__search" not in durable
    # The child's tools are all read, so the delegate is re-run after a crash.
    assert runtime.tools.describe("internal_exploration_agent").effect == "read"  # type: ignore[attr-defined]


async def test_pr3_quick_never_composes() -> None:
    agent = w.agent_doc()
    services = w.Services(agent=agent)
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, shaped = await _durable_turn_one(services, spec, "quick", "find the bug")
    assert "pre_run.composition" not in slots
    assert "internal_exploration_agent" not in {schema.name for schema in shaped.tools or []}


@pytest.mark.parametrize("chat_mode", ["quick", "react"])
async def test_pr2_the_system_prompt_is_the_legacy_prompt(chat_mode: str) -> None:
    """The turn shaper builds the prompt the legacy agent's first turn sends:
    same builder, same spec (grant, disclosure, pinned toolsets), same goal."""
    from app.agent_loop_lib.agent.prompt import build_system_prompt

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    query = "What is the status of ticket T-42?"
    _, _, shaped = await _durable_turn_one(w.Services(agent=agent), spec, chat_mode, query)
    legacy_agent, legacy_runtime, goal = await _legacy(w.Services(agent=agent), spec, chat_mode, query)
    legacy = build_system_prompt(legacy_agent.spec, legacy_runtime, goal, [], {})
    # Both render the wall clock, as every legacy turn does.
    clock = re.compile(r"\d{2}:\d{2}:\d{2}\.\d+Z")
    durable = shaped.system or ""
    names = [t.name for t in shaped.tools or []]
    # UXR-B1, R49-PLAN (ADR-OWD-07 D-PLAN-1): quick and react offer the plan tool legacy never had; nothing else moves.
    plan_line = f"- **{PLAN_TOOL}**: Show the user your plan for multi-step work.\n"
    assert (plan_line in durable, PLAN_TOOL in names) == (True, True)
    durable = durable.replace(plan_line, "")
    assert clock.sub("<now>", durable) == clock.sub("<now>", legacy)
    assert [n for n in names if n != PLAN_TOOL] == [
        t.name for t in legacy_runtime.tool_registry.schemas(legacy_agent.spec.tool_names) if t.name not in SANDBOX
    ]


async def test_as1_a_deep_run_is_the_orchestrator_with_its_sub_agents_delegated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """K-L AS-1: Node admits deep (K-L); the run is the orchestrator legacy runs:
    the coordination tools, spawn_agent delegated to child runs, the planning tools
    routed, the phases directed. Nothing is downgraded. R36-COVER (ADR-RUN-04 D1
    amended): beside them the person's question, which today's loop has no use for
    (it has no ask-back) and a sub-agent cannot hold, so a deep run can ask."""
    from app.harness.assembly.deep import DeepDelegation, DeepDirector

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent), max_children=w.DEEP_CHILDREN)
    with caplog.at_level("WARNING"):
        runtime, slots, shaped = await _durable_turn_one(w.Services(agent=agent), spec, "deep", "find the bug")
    assert slots["pre_run.goal"]["loopKind"] == "orchestrator"
    assert "mode.downgraded" not in caplog.text
    legacy_agent, _runtime, _goal = await _legacy(w.Services(agent=agent), spec, "deep", "find the bug")
    assert [t.name for t in shaped.tools or []] == [*legacy_agent.spec.tool_names, "internaltools__ask_user_question"]
    assert [t.name for t in shaped.tools or []] == [
        "create_plan", "critique_plan", "spawn_agent", "verify_result", "internaltools__ask_user_question"]
    assert isinstance(runtime.delegation, DeepDelegation)
    assert isinstance(runtime.director, DeepDirector)
    assert runtime.tools.describe("spawn_agent") is None  # type: ignore[attr-defined]
    for name in ("create_plan", "critique_plan", "verify_result"):
        effect = runtime.tools.describe(name)  # type: ignore[attr-defined]
        assert effect is not None and effect.effect == "read"


async def test_as3_auto_picking_deep_still_runs_as_react_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    """K-L AS-3: auto's tier is decided after the run is assembled, so a deep
    verdict runs as react (logged), as before K-L."""
    from app.harness.assembly.loop import GoalStep

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    claim = w.claim(spec, query="find the bug", chat_mode="auto")
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    runtime = await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(
        claim
    )
    assert runtime.delegation is None and runtime.director is None
    goal = next(step for step in runtime.pre_run if isinstance(step, GoalStep))
    from app.agents.agent_loop import router

    async def deep_verdict(**kwargs: Any) -> Any:  # noqa: ANN401
        from app.agents.agent_loop.modes import resolve_mode

        loop, goal_value, questions, _mode = await original(**{**kwargs, "chat_mode": "react"})
        return loop, goal_value, questions, resolve_mode("deep")

    original = router.select_loop_and_goal
    with caplog.at_level("WARNING"), pytest.MonkeyPatch.context() as patch:
        patch.setattr(router, "select_loop_and_goal", deep_verdict)
        slot = await goal.run("find the bug")
    assert isinstance(slot, dict) and slot["loopKind"] == "orchestrator"
    assert "mode.downgraded" in caplog.text


async def test_pr6_react_modes_log_no_downgrade(caplog: pytest.LogCaptureFixture) -> None:
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    with caplog.at_level("WARNING"):
        await _durable_turn_one(w.Services(agent=agent), spec, "react", "find the bug")
    assert "mode.downgraded" not in caplog.text
