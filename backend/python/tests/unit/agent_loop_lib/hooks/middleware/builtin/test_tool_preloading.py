"""`tool_preloading` (PRE_AGENT middleware) — relevance-band thresholds
(toolset unlock vs. pointer vs. nothing), grant-ceiling enforcement, and
its no-op guards (no scope, no goal description, no toolsets, eager
grant) — mirrors `test_skill_preloading.py`'s structure for the tools
side of progressive disclosure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.scope import RunScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.builtin.tool_preloading import tool_preloading
from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry


class _SimpleTool(Tool):
    def __init__(self, name: str, description: str = "") -> None:
        self._name = name
        self._description = description or f"{name} description"

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

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=self._name)


def _registry_with_jira_and_slack() -> ToolRegistry:
    # Descriptions deliberately avoid any shared token (including common
    # stopwords like "a"/"to") between the jira and slack tools, so a
    # query about one never accidentally scores a nonzero match against
    # the other via `KeywordToolIndex`'s plain token-overlap scoring.
    registry = ToolRegistry()
    registry.register_tool(_SimpleTool("jira_create_issue", "Create issue in Jira for bug tracking"))
    registry.register_tool(_SimpleTool("slack_send_message", "Post updates in Slack channel"))
    registry.register_tool(_SimpleTool("run_code", "Execute code snippet"))
    registry.register_toolset("jira", "Jira issue tracking.", ["jira_create_issue"])
    registry.register_toolset("slack", "Slack messaging.", ["slack_send_message"])
    return registry


@dataclass
class _FakeRuntime:
    tool_registry: ToolRegistry


def _scope(registry: ToolRegistry, spec: AgentSpec, goal: Goal) -> RunScope:
    return RunScope(
        identity=RunContext(role_name=spec.name, model=spec.model.model),
        spec=spec, runtime=_FakeRuntime(tool_registry=registry), goal=goal,
    )


async def _run(
    registry: ToolRegistry, *, tool_names: list[str] | None = None,
    tool_disclosure: str = "lazy", goal_description: str | None = "jira bug report", scope: Any = "__default__",
    **kwargs: Any,
) -> AgentLifecycleContext:
    middleware = tool_preloading(**kwargs)
    goal = Goal(description=goal_description) if goal_description is not None else None
    spec = AgentSpec(name="test-agent", tool_names=tool_names or [], tool_disclosure=tool_disclosure)
    if scope == "__default__":
        scope = _scope(registry, spec, goal or Goal(description=""))
    ctx = AgentLifecycleContext(goal=goal, scope=scope)

    called = {"next": False}

    async def next_fn() -> None:
        called["next"] = True

    await middleware(ctx, next_fn)
    assert called["next"], "middleware must always call next_fn(), even on no-op paths"
    return ctx


class TestRelevanceBands:
    async def test_above_preload_threshold_unlocks_toolset_into_visible_tools(self) -> None:
        registry = _registry_with_jira_and_slack()
        ctx = await _run(registry, preload_threshold=0.5, mention_threshold=0.1)

        assert "jira_create_issue" in ctx.scope.visible_tools
        section = ctx.scope.extra_prompt_sections["preloaded_tools"]
        assert "jira: Jira issue tracking." in section
        assert "already been loaded" in section

    async def test_between_thresholds_injects_pointer_without_unlocking(self) -> None:
        registry = _registry_with_jira_and_slack()
        ctx = await _run(registry, preload_threshold=0.99, mention_threshold=0.1)

        assert "jira_create_issue" not in ctx.scope.visible_tools
        section = ctx.scope.extra_prompt_sections["preloaded_tools"]
        assert "jira: Jira issue tracking." in section
        assert "may also be relevant" in section

    async def test_below_mention_threshold_injects_nothing(self) -> None:
        registry = _registry_with_jira_and_slack()
        ctx = await _run(registry, goal_description="something totally unrelated to any tool", preload_threshold=0.99, mention_threshold=0.99)

        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections

    async def test_grant_ceiling_drops_toolset_with_nothing_left_after_intersection(self) -> None:
        """A toolset that scores above threshold but has NO tools inside
        the grant ceiling must be dropped entirely — never unlocked, never
        even mentioned via a pointer."""
        registry = _registry_with_jira_and_slack()
        ctx = await _run(
            registry, tool_names=["slack_send_message"], preload_threshold=0.5, mention_threshold=0.1,
        )

        # "jira" scores well against the goal, but every one of its tools
        # falls outside the grant ceiling ({"slack_send_message"}) -> the
        # whole toolset is dropped, not partially unlocked; "slack" itself
        # never matches this jira-specific goal at all.
        assert ctx.scope.visible_tools == set()
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections

    async def test_grant_ceiling_still_unlocks_toolsets_within_the_grant(self) -> None:
        registry = _registry_with_jira_and_slack()
        ctx = await _run(
            registry, tool_names=["jira_create_issue", "run_code"],
            preload_threshold=0.5, mention_threshold=0.1,
        )

        assert ctx.scope.visible_tools == {"run_code", "jira_create_issue"}


class TestNoOpGuards:
    async def test_no_op_when_scope_is_none(self) -> None:
        registry = _registry_with_jira_and_slack()
        await _run(registry, scope=None)  # must not raise

    async def test_no_op_when_goal_has_no_description(self) -> None:
        registry = _registry_with_jira_and_slack()
        ctx = await _run(registry, goal_description="")
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections

    async def test_no_op_when_registry_has_no_toolsets(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_SimpleTool("run_code"))
        ctx = await _run(registry)
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections
        assert ctx.scope.visible_tools is None

    async def test_no_op_when_grant_is_eager(self) -> None:
        """Eager disclosure means every named tool is already fully visible
        from turn 0 (see `tool_loop.py`) — nothing for preloading to do,
        and no pointer should be injected either."""
        registry = _registry_with_jira_and_slack()
        ctx = await _run(registry, tool_names=["jira_create_issue"], tool_disclosure="eager")
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections
        assert ctx.scope.visible_tools is None

    async def test_search_failure_is_swallowed(self) -> None:
        registry = _registry_with_jira_and_slack()

        class _RaisingIndex:
            async def search(self, *args: Any, **kwargs: Any) -> list:
                raise RuntimeError("search backend unavailable")

        ctx = await _run(registry, index=_RaisingIndex())  # must not raise
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections

    async def test_stale_preloaded_section_is_cleared_when_nothing_matches(self) -> None:
        registry = _registry_with_jira_and_slack()
        spec = AgentSpec(name="test-agent", tool_disclosure="lazy")
        goal = Goal(description="something totally unrelated to any tool")
        scope = _scope(registry, spec, goal)
        scope.extra_prompt_sections["preloaded_tools"] = "stale from a prior turn"
        ctx = await _run(registry, goal_description=goal.description, scope=scope, preload_threshold=0.99, mention_threshold=0.99)
        assert "preloaded_tools" not in ctx.scope.extra_prompt_sections
