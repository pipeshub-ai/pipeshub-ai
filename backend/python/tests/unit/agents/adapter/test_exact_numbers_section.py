"""The "Exact Numbers" rule: the agent is told the calculator exists and when
to use it, and under lazy disclosure how to load it, without pinning its
schemas into every request."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.lazy_toolsets import FetchToolsTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.actions.calculator.calculator import Calculator
from app.agents.agent_loop.lazy_tools_wiring import group_connector_toolsets
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.section_order import PIPESHUB_SECTION_ORDER, Volatility
from app.agents.agent_loop.tool_loader import AgentLoopToolsetBuilder
from tests.unit.agent_loop_lib.tools.test_lazy_toolsets import (
    _FakeAgent,
    _FakeRouteContext,
)
from tests.unit.agents.adapter.conftest import make_context

_EVALUATE = "calculator__evaluate_expression"
_DATES = "calculator__date_difference"
_COUNT = "calculator__count_text"
_EXACT = [_EVALUATE, _DATES, _COUNT]
_HEADING = "## Exact Numbers"


def _registry() -> ToolRegistry:
    # The way `PipesHubToolLoader.load()` registers an internal toolset.
    registry = ToolRegistry()
    toolset = AgentLoopToolsetBuilder(
        Calculator(), name="calculator",
        description=Calculator._toolset_metadata["description"], path_prefix="/tools/calculator",
    )
    for tool in toolset.tools:
        registry.register_tool(tool)
    registry.register_toolset("calculator", toolset.description, [tool.name for tool in toolset.tools])
    return registry


def _blocks(
    tool_names: list[str],
    *,
    disclosure: str = "eager",
    pinned: list[str] | None = None,
) -> tuple[str, str]:
    registry = _registry()
    if disclosure == "lazy":
        group_connector_toolsets(registry, tool_names)
    spec = AgentSpec(
        name="pipeshub-agent",
        system_prompt="BASE",
        tool_names=tool_names,
        tool_disclosure=disclosure,
        pinned_toolsets=pinned or [],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )
    with patch.multiple(
        "app.agents.agent_loop.prompt_builder",
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=""),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
    ):
        return PipesHubPromptBuilder(make_context()).build_blocks(
            spec, AgentRuntime(tool_registry=registry), Goal(description="hello"), [], {},
        )


def _section(prompt: str) -> str:
    assert _HEADING in prompt
    return prompt.split(_HEADING, 1)[1].split("\n## ", 1)[0]


class TestEagerDisclosure:
    def test_names_the_granted_tools(self) -> None:
        stable, _ = _blocks(_EXACT)
        section = _section(stable)
        for name in _EXACT:
            assert f"`{name}`" in section
        assert "fetch_tools" not in section
        assert "more than one step" in section
        assert "two dates" in section
        assert "letters or words" in section

    def test_only_what_is_granted(self) -> None:
        section = _section(_blocks([_EVALUATE])[0])
        assert f"`{_EVALUATE}`" in section
        assert _DATES not in section and _COUNT not in section
        assert "two dates" not in section
        assert "letters or words" not in section

    @pytest.mark.parametrize(
        "tool_names",
        [[], ["calculator__calculate_two_operands"], ["knowledgegraph__search"]],
    )
    def test_absent_without_an_exact_tool(self, tool_names: list[str]) -> None:
        assert _HEADING not in "".join(_blocks(tool_names))


class TestLazyDisclosure:
    def test_names_the_toolset_and_how_to_load_it(self) -> None:
        stable, volatile = _blocks(_EXACT, disclosure="lazy")
        section = _section(stable)
        assert "`calculator` toolset" in section
        assert 'fetch_tools("calculator")' in section
        # Individual schemas are not loaded yet; naming them would read as callable.
        prompt = stable + volatile
        for name in _EXACT:
            assert name not in prompt

    def test_the_toolset_line_says_what_it_can_do(self) -> None:
        stable, _ = _blocks(_EXACT, disclosure="lazy")
        load_first = stable.split("Tools you must load before calling", 1)[1]
        line = next(line for line in load_first.splitlines() if line.startswith("- `calculator`"))
        assert "date differences" in line and "word counts" in line

    async def test_fetching_the_named_toolset_makes_the_tools_callable(self) -> None:
        registry = _registry()
        group_connector_toolsets(registry, _EXACT)
        agent = _FakeAgent(AgentSpec(name="pipeshub-agent", tool_names=_EXACT, tool_disclosure="lazy"))

        result = await FetchToolsTool(registry).handle(
            ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "calculator"}),
            _FakeRouteContext(agent=agent, spec=agent.spec),
        )

        assert result.is_error is False
        # The grant bounds the fetch: the two legacy tools stay out.
        assert agent.visible_tools == set(_EXACT)

    def test_pinned_toolset_is_named_by_its_tools(self) -> None:
        section = _section(_blocks(_EXACT, disclosure="lazy", pinned=["calculator"])[0])
        assert f"`{_EVALUATE}`" in section
        assert "fetch_tools" not in section


class TestCacheStability:
    @pytest.mark.parametrize("disclosure", ["eager", "lazy"])
    def test_same_request_same_bytes(self, disclosure: str) -> None:
        assert _blocks(_EXACT, disclosure=disclosure) == _blocks(_EXACT, disclosure=disclosure)

    @pytest.mark.parametrize("disclosure", ["eager", "lazy"])
    def test_lives_in_the_stable_block(self, disclosure: str) -> None:
        stable, volatile = _blocks(_EXACT, disclosure=disclosure)
        assert _HEADING in stable
        assert _HEADING not in volatile

    def test_declared_static_next_to_finding_information(self) -> None:
        names = [name for name, _ in PIPESHUB_SECTION_ORDER]
        assert names.index("exact_numbers") == names.index("finding_information") + 1
        assert dict(PIPESHUB_SECTION_ORDER)["exact_numbers"] is Volatility.STATIC
