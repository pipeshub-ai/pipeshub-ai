"""`DomainAgentDefinition.model` (p4-model-tiering): an optional per-domain
model override on top of the request's own `model_name` — `None` (every
pre-existing catalog entry) must keep inheriting `model_name` unchanged;
setting it must reach that domain's registered `AgentTool.spec` and
nothing else's."""

from __future__ import annotations

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.domain_agents import (
    DomainAgentDefinition,
    plan_domain_agents,
    register_domain_agents,
)
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_domain_agents import FakeTool


def _definitions(*, tiered_model: str | None) -> tuple[DomainAgentDefinition, ...]:
    return (
        DomainAgentDefinition(
            name="calculator_agent",
            domain="calculation",
            description="calc",
            app_names=frozenset({"calculator"}),
            model=tiered_model,
        ),
        DomainAgentDefinition(
            name="web_agent",
            domain="web research",
            description="web",
            app_names=frozenset({"dynamic"}),
        ),
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(FakeTool("calculator_evaluate", app_name="calculator"))
    registry.register_tool(FakeTool("dynamic_web_search", app_name="dynamic"))
    return registry


def _spec_of(registry: ToolRegistry, name: str) -> AgentSpec:
    tool = registry.resolve_by_name(name)
    assert isinstance(tool, AgentTool)
    return tool.spec


class TestDomainAgentModelTiering:
    def test_unset_model_inherits_the_request_model_name(self) -> None:
        registry = _registry()
        runtime = AgentRuntime(tool_registry=registry)
        plan = plan_domain_agents(registry, _definitions(tiered_model=None))
        register_domain_agents(
            plan, registry, runtime, make_context(),
            provider="scripted", model_name="scripted-big-model",
        )
        assert _spec_of(registry, "calculator_agent").model == ModelSpec(
            provider="scripted", model="scripted-big-model",
        )
        # An unrelated domain with no override is unaffected either way.
        assert _spec_of(registry, "web_agent").model == ModelSpec(
            provider="scripted", model="scripted-big-model",
        )

    def test_set_model_overrides_only_that_domain(self) -> None:
        registry = _registry()
        runtime = AgentRuntime(tool_registry=registry)
        plan = plan_domain_agents(registry, _definitions(tiered_model="scripted-cheap-model"))
        register_domain_agents(
            plan, registry, runtime, make_context(),
            provider="scripted", model_name="scripted-big-model",
        )
        assert _spec_of(registry, "calculator_agent").model == ModelSpec(
            provider="scripted", model="scripted-cheap-model",
        )
        # web_agent has no override — stays on the request's own model_name.
        assert _spec_of(registry, "web_agent").model == ModelSpec(
            provider="scripted", model="scripted-big-model",
        )

    def test_override_keeps_the_request_provider(self) -> None:
        """A model tier swap is within one provider's family — it must
        never silently switch providers out from under the request."""
        registry = _registry()
        runtime = AgentRuntime(tool_registry=registry)
        plan = plan_domain_agents(registry, _definitions(tiered_model="scripted-cheap-model"))
        register_domain_agents(
            plan, registry, runtime, make_context(),
            provider="scripted", model_name="scripted-big-model",
        )
        assert _spec_of(registry, "calculator_agent").model.provider == "scripted"
