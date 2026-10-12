"""The model's configured contextLength reaches the agent's context budget.

The chat route fills an unset contextLength with a 128k default before it
reaches `context_length`, so the configured value travels on its own field:
reading the default as configured would shrink models the built-in table
knows to be larger.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.domain_agents import (
    DOMAIN_AGENT_DEFINITIONS,
    compose_domain_agents,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.loops.orchestrator import domain_spec_factory
from app.modules.agents.context.source_catalog import SourceCatalog
from tests.unit.agents.actions.knowledge_graph.test_wiring import (
    _registry_with_nav_tools,
)
from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.tools.registry import ToolRegistry


def _state() -> dict[str, Any]:
    return {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}


class TestConfiguredWindow:
    @pytest.mark.parametrize(("configured", "expected"), [
        (400_000, 400_000), ("400000", 400_000), (" 32768 ", 32_768), (128_000.0, 128_000),
        (None, None), (0, None), (-5, None), ("", None), ("lots", None), ("-5", None),
        # Not a 1-token window, and not a truncated one.
        (True, None), (False, None), (1.5, None), ("4.5", None), ("1e5", None), ("²", None),
    ])
    def test_admin_entered_values_become_a_window_or_none(self, configured: object, expected: int | None) -> None:
        context = AgentContext.from_chat_state(_state(), context_length=128_000, configured_context_window=configured)

        assert context.configured_context_window == expected

    def test_a_route_default_in_context_length_is_not_a_configured_window(self) -> None:
        context = AgentContext.from_chat_state(_state(), context_length=128_000)

        assert context.configured_context_window is None


def _domain_context() -> MagicMock:
    context = MagicMock()
    context.agent_knowledge = []
    context.connector_configs = {}
    context.get_source_catalog.return_value = SourceCatalog.from_state({})
    return context


def _built_domain_specs(registry: ToolRegistry) -> list[AgentSpec]:
    return [registry.resolve_by_name(d.name)._spec for d in DOMAIN_AGENT_DEFINITIONS if registry.has(d.name)]


class TestSubAgents:
    def test_domain_agents_on_the_requests_model_get_the_window(self) -> None:
        registry = _registry_with_nav_tools()

        compose_domain_agents(
            registry, AgentRuntime(tool_registry=registry), _domain_context(),
            provider="scripted", model_name="scripted-model", context_window=400_000,
        )

        specs = _built_domain_specs(registry)
        assert specs
        assert {spec.model.context_window for spec in specs} == {400_000}

    def test_a_domain_on_its_own_model_falls_back_to_that_models_table_entry(self) -> None:
        registry = _registry_with_nav_tools()
        own_model = tuple(replace(d, model="gpt-5.4-mini") for d in DOMAIN_AGENT_DEFINITIONS)

        compose_domain_agents(
            registry, AgentRuntime(tool_registry=registry), _domain_context(),
            provider="scripted", model_name="scripted-model", context_window=400_000, definitions=own_model,
        )

        specs = _built_domain_specs(registry)
        assert specs
        assert {(spec.model.model, spec.model.context_window) for spec in specs} == {("gpt-5.4-mini", None)}

    def test_the_spawn_pool_sizes_children_on_the_requests_model(self) -> None:
        make_spec = domain_spec_factory(
            provider="scripted", model_name="scripted-model", default_tool_names=[], context_window=400_000,
        )

        assert make_spec("research").model.context_window == 400_000
        assert make_spec("research", model="gpt-5.4-mini").model.context_window is None


@pytest.fixture(autouse=True)
def _skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


@pytest.mark.asyncio
class TestFactory:
    async def test_the_root_agent_is_sized_by_the_configured_window(self) -> None:
        context = make_context(llm=FakeChatModel(), configured_context_window=400_000)

        with patch("app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", return_value=None):
            agent, _runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello", model_name="claude-opus-5-5",
            )

        assert agent.spec.model.model == "claude-opus-5-5"
        assert agent.spec.model.context_window == 400_000

    async def test_without_one_the_root_agent_uses_the_table(self) -> None:
        context = make_context(llm=FakeChatModel())

        with patch("app.agents.agent_loop.tool_loader.ClientFactoryRegistry.get_factory", return_value=None):
            agent, _runtime, _goal, _clarifying = await PipesHubAgentFactory().create(
                context, context.llm, "quick", query="hello", model_name="claude-opus-5-5",
            )

        assert agent.spec.model.context_window is None
