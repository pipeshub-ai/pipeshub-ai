"""Regression coverage for the fix-lazy-skills-gap bug in
`PipesHubAgentFactory.create()`: skill tools (`skills_list`/`load_skill`/
`load_skill_resource`/`skill_search`/`skill_manage`) are registered into
their own `"skills"` toolset by `register_skill_tools()`
(`skills_wiring.py`) — indistinguishable, as far as `ToolRegistry.
grouped_tool_names()` is concerned, from any other grouped toolset. Once
top-level disclosure flips to `"lazy"` (see `lazy_tools_wiring.py`), a
grouped toolset is hidden behind a `fetch_tools`/`search_tools` round-trip
unless it's in `AgentSpec.pinned_toolsets` — exactly the gap
`control_plane.py`'s analogous "skills" auto-pin already closes on that
path. These tests assert the PipesHub factory closes the same gap.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.agent.tool_loop import initial_visible_tools
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

_SKILL_TOOL_NAMES = {"skills_list", "load_skill", "load_skill_resource", "skill_search", "skill_manage"}


class _FakeConnectorTool(Tool):
    """Stands in for an attached connector action. `group_connector_
    toolsets` — the thing that actually flips top-level disclosure to
    `"lazy"` — only groups tools carrying an `app_name`; `make_context()`'s
    minimal registry has none of its own, so one is added here."""

    @property
    def app_name(self) -> str:
        return "jira"

    @property
    def name(self) -> str:
        return "jira_search_issues"

    @property
    def short_description(self) -> str:
        return "Search Jira issues"

    @property
    def description(self) -> str:
        return "Search Jira issues"

    @property
    def path(self) -> str:
        return "/connectors/jira/jira_search_issues"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


async def _fake_load(
    self: PipesHubToolLoader, context: Any, *, skip_apps: set[str] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(_FakeConnectorTool())
    return registry


class _FakeSkillManager:
    """Just enough surface for `register_skill_tools()` and the factory's
    own log line — the five skill-tool classes only stash this reference
    at construction time (see `skills.py`), never call into it until
    `execute()`, which no test here reaches."""

    def catalog_snapshot(self) -> list[dict[str, str]]:
        return [{"name": "pptx"}]


async def _fake_build_skill_manager(context: Any, transport_registry: Any) -> _FakeSkillManager:
    return _FakeSkillManager()


@pytest.fixture(autouse=True)
def _force_lazy_top_level_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_SCOPE", "top_level")
    monkeypatch.setattr(PipesHubToolLoader, "load", _fake_load)


@pytest.fixture(autouse=True)
def _no_code_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the fake registry's tool count/shape fully deterministic —
    `code_execution_enabled()` re-derives from env/Labs flags this suite
    doesn't otherwise care about."""
    monkeypatch.setattr("app.agents.agent_loop.factory.code_execution_enabled", lambda _state: False)


class TestSkillsToolsetPinnedUnderLazyDisclosure:
    async def test_skills_pinned_when_disclosure_goes_lazy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        monkeypatch.setattr("app.agents.agent_loop.factory.build_skill_manager", _fake_build_skill_manager)
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "quick", query="make me a pptx",
        )

        assert agent.spec.tool_disclosure == "lazy"
        assert "skills" in agent.spec.pinned_toolsets

        visible = initial_visible_tools(agent.spec, runtime)
        assert _SKILL_TOOL_NAMES <= visible

    async def test_skills_not_pinned_when_disclosure_stays_eager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sanity check for the guard added alongside the pin: an eager
        request (lazy tools off) must not gain a `pinned_toolsets` entry
        that means nothing there — `AgentSpec.pinned_toolsets` is only
        consulted by `initial_visible_tools()` under lazy disclosure."""
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        monkeypatch.setattr("app.agents.agent_loop.factory.build_skill_manager", _fake_build_skill_manager)
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "quick", query="make me a pptx",
        )

        assert agent.spec.tool_disclosure == "eager"
        assert agent.spec.pinned_toolsets == []

    async def test_no_pin_when_skills_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a skill manager (feature off, or no graph_provider on
        this request — see `build_skill_manager()`), there is no `"skills"`
        toolset to pin at all; disclosure can still go lazy on the
        connector tool alone."""
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
        context = make_context(llm=FakeChatModel())
        factory = PipesHubAgentFactory()

        agent, _runtime, _goal, _clarifying = await factory.create(
            context, context.llm, "quick", query="make me a pptx",
        )

        assert agent.spec.tool_disclosure == "lazy"
        assert agent.spec.pinned_toolsets == []
