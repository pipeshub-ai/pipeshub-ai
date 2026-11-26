"""Recommendation 4 (recs audit) on the native loop: is the tool list a model request carries the same for a session?

* An eager grant (at or under ``PIPESHUB_LAZY_TOOLS_THRESHOLD``, 20 by default): yes, the same schemas in the same
  order on every request and in a fresh process state (a resumed attempt rebuilds it from the spec).
* A lazy grant (over the threshold; lazy disclosure is on by default): yes, since recs2. ``fetch_tools`` and
  ``search_tools`` answer schemas and never grow the list; a tool they answer is called through ``call_tool``. (The
  audit found the gap as a strict xfail; recs2 turned it into this pass.) The durable kernel fixes the list for the
  whole chat session: ``tests/unit/harness_core/durable/test_r4_session_tool_list.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.tools.builtin.lazy_toolsets import FetchToolsTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from tests.unit.agent_loop_lib.tools.test_lazy_toolsets import (
    _FakeAgent,  # pyright: ignore[reportPrivateUsage]
    _FakeRouteContext,  # pyright: ignore[reportPrivateUsage]
    _SimpleTool,  # pyright: ignore[reportPrivateUsage]
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for group in ("slack", "jira", "confluence"):
        names = [f"{group}_tool_{n:02d}" for n in range(12)]
        for name in names:
            registry.register_tool(_SimpleTool(name, description=f"{group} tool {name}"))
        registry.register_toolset(group, f"{group} tools.", names)
    registry.register_tool(_SimpleTool("task_complete", description="Finish the task"))
    return registry


def _wire(agent: object, spec: AgentSpec, registry: ToolRegistry) -> str:
    schemas = tool_schemas_for_turn(agent, spec, SimpleNamespace(tool_registry=registry))
    return json.dumps([[s.name, s.description, s.input_schema] for s in schemas], sort_keys=True)


def test_r4_an_eager_grant_sends_the_same_tool_list_on_every_request_and_after_a_resume() -> None:
    registry = _registry()
    spec = AgentSpec(name="a", tool_names=["jira_tool_03", "slack_tool_01", "task_complete"], tool_disclosure="eager")
    agent = _FakeAgent(spec)
    first = _wire(agent, spec, registry)
    assert [_wire(agent, spec, registry) for _ in range(3)] == [first] * 3
    assert _wire(_FakeAgent(spec), spec, registry) == first


async def test_r4_a_lazy_grant_keeps_the_same_tool_list_after_fetch_tools() -> None:
    registry = _registry()
    spec = AgentSpec(name="a", tool_names=[], tool_disclosure="lazy")
    agent = _FakeAgent(spec)
    before = _wire(agent, spec, registry)
    call = ToolCall(id="fetch-1", name="fetch_tools", arguments={"toolset": "jira"})
    result = await FetchToolsTool(registry).handle(call, _FakeRouteContext(agent=agent, spec=spec))
    assert result.is_error is False
    assert _wire(agent, spec, registry) == before
