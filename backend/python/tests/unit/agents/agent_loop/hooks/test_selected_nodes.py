"""The selection is named once, before the run, on the lifecycle context.

PRE_AGENT receives an AgentLifecycleContext. That context has a scope and
no turn index. Reading a turn index there used to deny every run.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
from app.agents.agent_loop.hooks.selected_nodes import selected_nodes_prompt


def _context(**overrides: object) -> SimpleNamespace:
    values = {"graph_provider": object(), "filters": {"records": ["r1"]}, "user_id": "u", "org_id": "o"}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_the_selection_is_written_onto_the_run_scope(monkeypatch) -> None:
    named = AsyncMock(return_value="This conversation is limited to these items:\n- Drive [app] id=app-1")
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.ops.scope.list_selected_nodes", named,
    )
    scope = SimpleNamespace(extra_prompt_sections={})
    ctx = AgentLifecycleContext(scope=scope)
    seen = False

    async def _next() -> None:
        nonlocal seen
        seen = True

    await selected_nodes_prompt(_context())(ctx, _next)
    assert seen
    assert scope.extra_prompt_sections["selected_nodes"].startswith(
        "This conversation is limited to these items:",
    )
    assert named.await_args.args[2:] == ("u", "o")


@pytest.mark.asyncio
async def test_a_graph_failure_does_not_block_the_run(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.ops.scope.list_selected_nodes",
        AsyncMock(side_effect=RuntimeError("graph down")),
    )
    scope = SimpleNamespace(extra_prompt_sections={})
    seen = False

    async def _next() -> None:
        nonlocal seen
        seen = True

    await selected_nodes_prompt(_context())(AgentLifecycleContext(scope=scope), _next)
    assert seen
    assert "selected_nodes" not in scope.extra_prompt_sections


@pytest.mark.asyncio
async def test_without_a_graph_the_prompt_is_left_alone(monkeypatch) -> None:
    named = AsyncMock()
    monkeypatch.setattr(
        "app.agents.actions.knowledge_graph.ops.scope.list_selected_nodes", named,
    )
    scope = SimpleNamespace(extra_prompt_sections={})

    async def _next() -> None:
        return None

    await selected_nodes_prompt(_context(graph_provider=None))(
        AgentLifecycleContext(scope=scope), _next,
    )
    named.assert_not_awaited()
    assert scope.extra_prompt_sections == {}
