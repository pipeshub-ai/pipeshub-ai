"""ADR-SCH-03 D8: the schedules toolset is loaded when it is needed, not before.

``schedules`` is on demand: whatever the grant's size it is one line in the overview, its tools load through
discovery, and it never counts toward a lazy threshold, so the person's own tools are disclosed as before. Preloading
unlocks it for a request that clearly asks for later or repeated work, on the classic loop, the durable loop and
engines alike; ``call_tool`` unwraps to the tool's own call, so it still asks every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.agent.tool_loop import initial_visible_tools
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.scope import RunScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.builtin.tool_preloading import tool_preloading
from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.lazy_toolsets import CallToolTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import schedule_tool
from app.agents.agent_loop.lazy_tools_wiring import (
    CONNECTORS_PARENT,
    META_TOOL_NAMES,
    make_lazy_tools_planner,
)
from app.agents.agent_loop.schedule_tool import (
    ClassicScheduleCreateTool,
    SchedulesApi,
    register_schedule_tool,
)
from app.harness.assembly.disclosure import Disclosure
from app.harness.engines.durable import engine_disclosure
from app.harness.schedules import SCHEDULE_TOOL_ID, ScheduleAsks
from app.schedules.arguments import SCHEDULE_CREATE_TOOL
from app.schedules.toolset import (
    SCHEDULES_DESCRIPTION,
    SCHEDULES_TOOLSET,
    register_schedules_toolset,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolEffect, allow_all
from harness_core.kernel.agent.prompt import _render_toolset_overview  # pyright: ignore[reportPrivateUsage]
from harness_core.logs import HarnessLog
from harness_core.tools.call_tool import unwrapped
from tests.unit.agents.agent_loop.test_sch02_schedules_without_harness import (
    _chat,
    _once,
)
from tests.unit.agents.agent_loop.test_tool_approvals import _Store, run_request


class _Plain(Tool):
    def __init__(self, name: str, description: str) -> None:
        self._name, self._description = name, description

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/toolsets/test/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data=self._name)


def _registry(*, person_tools: int = 2) -> ToolRegistry:
    """The person's Jira and Slack tools (``person_tools`` each, at least one), and the schedules toolset."""
    registry = ToolRegistry()
    jira = [f"jira_tool_{i}" for i in range(person_tools)]
    slack = [f"slack_tool_{i}" for i in range(person_tools)]
    for name in jira:
        registry.register_tool(_Plain(name, "Create issue in Jira for bug tracking"))
    for name in slack:
        registry.register_tool(_Plain(name, "Post updates in Slack channel"))
    registry.register_toolset("jira", "Jira issue tracking.", jira)
    registry.register_toolset("slack", "Slack messaging.", slack)
    registry.register_tool(ClassicScheduleCreateTool(_chat(), SchedulesApi(None)))
    register_schedules_toolset(registry, [SCHEDULE_CREATE_TOOL])
    return registry


# --- preloading ---------------------------------------------------------------------------------------------------


@dataclass
class _Runtime:
    tool_registry: ToolRegistry


async def _preload(registry: ToolRegistry, goal: str) -> RunScope:
    spec = AgentSpec(name="a", tool_names=[*registry.names()], tool_disclosure="lazy")
    scope = RunScope(
        identity=RunContext(role_name="a", model=spec.model.model), spec=spec,
        runtime=_Runtime(tool_registry=registry), goal=Goal(description=goal),
    )

    async def _next() -> None:
        return None

    await tool_preloading()(AgentLifecycleContext(goal=Goal(description=goal), scope=scope), _next)
    return scope


@pytest.mark.parametrize(
    "goal",
    [
        "remind me in 5 minutes",
        "tell me a joke after 2 mins",
        "every Monday summarise my open Jira issues",
        "Remind me to call Sam tomorrow",
        "post the standup notes in Slack every weekday morning",
    ],
)
async def test_sch03_a_clear_later_or_repeated_request_unlocks_schedules(goal: str) -> None:
    scope = await _preload(_registry(), goal)
    assert SCHEDULE_CREATE_TOOL in (scope.visible_tools or set())
    assert f"{SCHEDULES_TOOLSET}: {SCHEDULES_DESCRIPTION}" in scope.extra_prompt_sections["preloaded_tools"]


@pytest.mark.parametrize(
    "goal",
    [
        "summarise my open Jira issues",
        "what is our refund policy?",
        "write a python function that sorts a list",
        "find the report every team shared last quarter",
        "summarise the last 5 minutes of the call transcript",
    ],
)
async def test_sch03_an_unrelated_request_does_not_unlock_or_point_at_schedules(goal: str) -> None:
    scope = await _preload(_registry(), goal)
    assert SCHEDULE_CREATE_TOOL not in (scope.visible_tools or set())
    assert SCHEDULES_TOOLSET not in scope.extra_prompt_sections.get("preloaded_tools", "")


async def test_sch03_a_weak_sign_only_points_at_schedules() -> None:
    scope = await _preload(_registry(), "what is on my calendar tomorrow?")
    assert SCHEDULE_CREATE_TOOL not in (scope.visible_tools or set())
    section = scope.extra_prompt_sections["preloaded_tools"]
    assert "may also be relevant" in section and SCHEDULES_TOOLSET in section


# --- the classic loop ---------------------------------------------------------------------------------------------


def _plan(registry: ToolRegistry, grant: list[str] | None = None) -> tuple[list[str], str, list[str]]:
    return make_lazy_tools_planner(apply=True)(registry, grant if grant is not None else list(registry.names()))


def test_sch03_a_small_grant_lists_the_persons_tools_and_only_schedules_waits() -> None:
    registry = _registry()
    grant = list(registry.names())
    names, mode, pins = _plan(registry, grant)

    assert mode == "lazy" and set(META_TOOL_NAMES) <= set(names)
    assert set(pins) == {"jira", "slack"}
    # Not swept under "connectors": the person's groups stay as they were.
    assert registry.children_of(CONNECTORS_PARENT) == []
    spec = AgentSpec(name="a", tool_names=names, tool_disclosure=mode, pinned_toolsets=pins)
    visible = initial_visible_tools(spec, _Runtime(tool_registry=registry))  # type: ignore[arg-type]
    assert {"jira_tool_0", "jira_tool_1", "slack_tool_0", "slack_tool_1"} <= visible
    assert SCHEDULE_CREATE_TOOL not in visible
    # The same grant, decided again (the next turn), is the same list: the prompt prefix holds.
    assert _plan(registry, grant) == (names, mode, pins)


def test_sch03_the_overview_shows_schedules_as_one_line() -> None:
    registry = _registry()
    names, mode, pins = _plan(registry)
    spec = AgentSpec(name="a", tool_names=names, tool_disclosure=mode, pinned_toolsets=pins)
    overview = _render_toolset_overview(_Runtime(tool_registry=registry), spec)  # type: ignore[arg-type]
    assert f"- {SCHEDULES_TOOLSET}: {SCHEDULES_DESCRIPTION} (1 tool(s))" in overview


def test_sch03_schedules_is_not_counted_toward_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    # Four of the person's tools and the threshold at four: without schedules counted, still under it.
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "4")
    registry = _registry(person_tools=2)
    _names, _mode, pins = _plan(registry)
    assert set(pins) == {"jira", "slack"} and registry.children_of(CONNECTORS_PARENT) == []


def test_sch03_past_the_threshold_schedules_keeps_its_own_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "3")
    registry = _registry(person_tools=2)
    _names, mode, pins = _plan(registry)
    assert mode == "lazy" and pins == []
    assert {g.name for g in registry.children_of(CONNECTORS_PARENT)} == {"jira", "slack"}
    (schedules,) = [g for g in registry.toolsets() if g.name == SCHEDULES_TOOLSET]
    assert schedules.parent is None and schedules.on_demand


@pytest.mark.parametrize("kwargs", [{"apply": False}, {"apply": True, "mode": "eager"}])
def test_sch03_listing_everything_by_choice_still_lists_everything(kwargs: dict[str, Any]) -> None:
    registry = _registry()
    names, mode, pins = make_lazy_tools_planner(**kwargs)(registry, list(registry.names()))
    assert (mode, pins) == ("eager", []) and SCHEDULE_CREATE_TOOL in names


def test_sch03_a_grant_without_schedules_is_disclosed_as_before() -> None:
    registry = _registry()
    names = [n for n in registry.names() if n != SCHEDULE_CREATE_TOOL]
    assert make_lazy_tools_planner(apply=True)(registry, names) == (names, "eager", [])


async def test_sch03_the_classic_tool_is_registered_as_the_on_demand_group() -> None:
    registry = ToolRegistry()
    context = _chat()

    async def on(_config: object) -> bool:
        return True

    original = schedule_tool.is_scheduled_runs_on
    schedule_tool.is_scheduled_runs_on = on  # type: ignore[assignment]
    try:
        assert await register_schedule_tool(registry, context)
    finally:
        schedule_tool.is_scheduled_runs_on = original
    (group,) = [g for g in registry.toolsets() if g.name == SCHEDULES_TOOLSET]
    assert group.on_demand and group.cues
    assert group.tool_names == ["schedule_create", "schedule_find", "schedule_update", "schedule_delete"]


async def test_sch03_a_call_through_call_tool_still_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def on(_config: object) -> bool:
        return True

    monkeypatch.setattr(schedule_tool, "is_scheduled_runs_on", on)
    store = _Store()
    context = _chat(store)
    registry = ToolRegistry()
    assert await register_schedule_tool(registry, context)
    tool = registry.resolve_by_name(SCHEDULE_CREATE_TOOL)
    via = ToolCall(id="c1", name="call_tool", arguments={"name": SCHEDULE_CREATE_TOOL, "arguments": _once()})
    done = ToolCall(id="c2", name="task_complete", arguments={"output": "done"})
    asked = await run_request(context, [CallToolTool(), tool], via, done)
    (blocked,) = asked.events.of(EventType.TOOL_BLOCKED)
    assert blocked["status"] == ToolCallStatus.AWAITING_APPROVAL
    (saved,) = store.pending()
    assert (saved["source"], saved["toolName"]) == ("platform", SCHEDULE_CREATE_TOOL)


# --- the durable loop ---------------------------------------------------------------------------------------------


@dataclass
class _Env:
    context: Any = None


def _durable(registry: ToolRegistry) -> Disclosure:
    return Disclosure(_Env(), registry, essential=[], log=HarnessLog.named("test"))  # type: ignore[arg-type]


def test_sch03_the_durable_loop_defers_only_schedules_in_a_small_grant() -> None:
    registry = _registry()
    disclosure = _durable(registry)
    grant = list(registry.names())
    names, mode, pinned = disclosure.decide(grant, "react")
    assert mode == "lazy" and {"jira", "slack"} <= set(pinned) and SCHEDULES_TOOLSET not in pinned
    assert "search_tools" in names and "call_tool" in names
    assert disclosure.decide(grant, "react") == (names, mode, pinned)


def test_sch03_the_durable_loop_unwraps_call_tool_before_the_ask() -> None:
    call = unwrapped(ToolCall(id="c1", name="call_tool", arguments={"name": SCHEDULE_CREATE_TOOL, "arguments": _once()}))
    assert call.name == SCHEDULE_CREATE_TOOL
    effect = ToolEffect(SCHEDULE_TOOL_ID, "write", False, SinkDescriptor(egress="self", args_leave_boundary=False))
    assert ScheduleAsks(allow_all)(call, effect) == "ask"


# --- engines ------------------------------------------------------------------------------------------------------


def test_sch03_an_engine_finds_schedules_through_the_search_whatever_the_grant() -> None:
    registry = _registry()
    disclosure = engine_disclosure(registry, registry.names(), essential=[])
    assert disclosure is not None
    assert disclosure.deferred == frozenset({SCHEDULE_CREATE_TOOL})
    assert any(line.startswith(f"{SCHEDULES_TOOLSET}: ") for line in disclosure.sources)


def test_sch03_an_engine_grant_without_schedules_is_listed_as_before() -> None:
    registry = _registry()
    names = [n for n in registry.names() if n != SCHEDULE_CREATE_TOOL]
    assert engine_disclosure(registry, names, essential=[]) is None


def test_sch03_schedules_is_not_counted_toward_the_engine_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD", "4")
    registry = _registry(person_tools=2)
    disclosure = engine_disclosure(registry, registry.names(), essential=[])
    assert disclosure is not None and disclosure.deferred == frozenset({SCHEDULE_CREATE_TOOL})
