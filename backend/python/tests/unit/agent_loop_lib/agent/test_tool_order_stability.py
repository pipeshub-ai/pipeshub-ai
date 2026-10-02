"""Tool schemas are emitted in a deterministic, append-only order (P0.14).

Tools precede the system prompt in the provider cache prefix, so the order
must not depend on registration or set iteration order, and lazy disclosure
must append rather than reorder.
"""

from __future__ import annotations

from types import SimpleNamespace

from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import stable_tool_order, tool_schemas_for_turn
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry

_NAMES = [f"tool_{c}" for c in "abcdefghij"]
_JIRA = ["jira_create", "jira_search"]
_SLACK = ["slack_send", "slack_read"]


class _T(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return f"/toolsets/test/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=None)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(visible_tools=None, _scope=SimpleNamespace(tool_order=[]))


def _spec(tool_names: list[str] | None = None) -> AgentSpec:
    return AgentSpec(name="a", system_prompt="s", tool_names=tool_names or [],
                     model=ModelSpec(provider="scripted", model="m"))


def _names(schemas) -> list[str]:
    return [s.name for s in schemas]


@settings(max_examples=50, deadline=None)
@given(order=st.permutations(_NAMES))
def test_eager_order_independent_of_registration_order(order) -> None:
    registry = ToolRegistry()
    for name in order:
        registry.register_tool(_T(name))
    runtime = AgentRuntime(tool_registry=registry)

    assert _names(tool_schemas_for_turn(_agent(), _spec(), runtime)) == sorted(_NAMES)
    granted = list(reversed(order[:5]))
    assert _names(tool_schemas_for_turn(_agent(), _spec(granted), runtime)) == sorted(granted)


@settings(max_examples=50, deadline=None)
@given(order=st.permutations(_NAMES[:4] + _JIRA + _SLACK), first=st.sampled_from(["jira", "slack"]))
def test_lazy_disclosure_appends_without_reordering(order, first) -> None:
    registry = ToolRegistry()
    for name in order:
        registry.register_tool(_T(name))
    registry.register_toolset("jira", "Jira", list(reversed(_JIRA)))
    registry.register_toolset("slack", "Slack", list(reversed(_SLACK)))
    runtime = AgentRuntime(tool_registry=registry)
    agent = _agent()
    spec = _spec()

    turn0 = _names(tool_schemas_for_turn(agent, spec, runtime))
    assert turn0 == sorted(_NAMES[:4])

    groups = {"jira": _JIRA, "slack": _SLACK}
    second = "slack" if first == "jira" else "jira"
    agent.visible_tools |= set(groups[first])
    turn1 = _names(tool_schemas_for_turn(agent, spec, runtime))
    assert turn1 == turn0 + sorted(groups[first])

    agent.visible_tools |= set(groups[second])
    turn2 = _names(tool_schemas_for_turn(agent, spec, runtime))
    assert turn2 == turn1 + sorted(groups[second])

    assert _names(tool_schemas_for_turn(agent, spec, runtime)) == turn2


@given(
    previous=st.lists(st.sampled_from(_NAMES), unique=True),
    current=st.sets(st.sampled_from(_NAMES)),
)
def test_stable_tool_order_properties(previous, current) -> None:
    result = stable_tool_order(previous, current)
    assert sorted(result) == sorted(current)
    kept = [n for n in previous if n in current]
    assert result[: len(kept)] == kept
    assert result[len(kept):] == sorted(set(current) - set(kept))
