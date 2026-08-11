"""`tool_schemas_for_turn` (prompt-caching Phase 6 — prefix stability).

The "toolsets, no `spec.tool_names`" branch used to return
`registry.schemas(list(agent.visible_tools))`, and `visible_tools` is a
`set[str]`. Set iteration order depends on Python's per-process string
hash seed, so the SAME logical tool grant could serialize onto the wire in
a DIFFERENT order depending only on which worker process handled the
request — silently breaking a byte-identical cache prefix for no reason a
user or admin could see or control. These tests pin the fix: the returned
schema order is always `sorted(agent.visible_tools)`, regardless of the
set's internal iteration order.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from tests.unit.agents.adapter.test_prompt_builder import FakeTool


def _make_registry_with_toolset(names: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register_tool(FakeTool(name, description=f"Fake {name} tool."))
    # Any toolset registration at all puts the registry on the
    # `has_toolsets()` branch `tool_schemas_for_turn` takes.
    registry.register_toolset("grp", "grp toolset", names)
    return registry


def _spec() -> AgentSpec:
    return AgentSpec(
        name="test-agent",
        system_prompt="",
        tool_names=[],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )


class TestToolSchemasForTurnOrderingIsDeterministic:
    def test_schema_order_matches_sorted_visible_tools_regardless_of_set_order(self) -> None:
        names = ["zulu_tool", "alpha_tool", "mike_tool", "bravo_tool"]
        registry = _make_registry_with_toolset(names)
        runtime = AgentRuntime(tool_registry=registry)
        spec = _spec()

        # Two sets that are logically equal but were built via different
        # insertion sequences — real Python `set`s make no order guarantee
        # here even for identical contents in different literals.
        agent_a = SimpleNamespace(visible_tools={"zulu_tool", "alpha_tool", "mike_tool", "bravo_tool"})
        agent_b = SimpleNamespace(visible_tools=set(reversed(names)))

        schemas_a = tool_schemas_for_turn(agent_a, spec, runtime)
        schemas_b = tool_schemas_for_turn(agent_b, spec, runtime)

        expected_order = sorted(names)
        assert [s.name for s in schemas_a] == expected_order
        assert [s.name for s in schemas_b] == expected_order

    def test_repeated_calls_with_the_same_logical_set_are_byte_stable(self) -> None:
        """Guards the actual cache-visible symptom: serializing the same
        tool grant twice must produce identical `ToolSchema.input_schema`
        wire content, not just identically-NAMED tools in some order."""
        names = ["c_tool", "a_tool", "b_tool"]
        registry = _make_registry_with_toolset(names)
        runtime = AgentRuntime(tool_registry=registry)
        spec = _spec()

        agent = SimpleNamespace(visible_tools=set(names))
        first = tool_schemas_for_turn(agent, spec, runtime)
        agent.visible_tools = set(names)  # same content, fresh set object
        second = tool_schemas_for_turn(agent, spec, runtime)

        assert [s.name for s in first] == [s.name for s in second] == sorted(names)
        assert [ToolSchema.model_dump(s) for s in first] == [
            ToolSchema.model_dump(s) for s in second
        ]

    def test_initializes_visible_tools_before_sorting(self) -> None:
        """`agent.visible_tools is None` on the first call — must be seeded
        via `initial_visible_tools` (a `set`) and still sort cleanly rather
        than raising on `None`."""
        names = ["only_tool"]
        registry = _make_registry_with_toolset(names)
        runtime = AgentRuntime(tool_registry=registry)
        spec = _spec()
        agent = SimpleNamespace(visible_tools=None)

        schemas = tool_schemas_for_turn(agent, spec, runtime)
        assert [s.name for s in schemas] == ["only_tool"]
        assert agent.visible_tools is not None
