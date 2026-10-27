"""`seed_visible_tools_from_history` (`app/agents/agent_loop/hooks/memory.py`): a follow-up request
keeps the tools the conversation already used visible, even when tool preloading (PRE_AGENT) has
already set what is visible for the run."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agents.agent_loop.hooks.memory import seed_visible_tools_from_history
from app.agents.agent_loop.parked_turn import VISIBLE_TOOLS
from tests.unit.agents.adapter.conftest import make_context

_HISTORY = [
    {"role": "user_query", "content": "find my pages"},
    {"role": "bot_response", "content": "Found them.", "tool_results": [
        {"tool_name": "mcp_atlassian_getConfluenceSpaces", "result": "4 spaces", "status": "success", "tool_id": "c1"},
        {"tool_name": "gone_tool", "result": "x", "status": "success", "tool_id": "c2"},
    ]},
]


class _Registry:
    def names(self) -> list[str]:
        return ["mcp_atlassian_getConfluenceSpaces", "mcp_atlassian_createConfluencePage", "fetch_tools"]

    def has_toolsets(self) -> bool:
        return True


async def _seed(visible: set[str] | None, parked: list[str] | None = None) -> set[str] | None:
    run = SimpleNamespace(
        visible_tools=visible, runtime=SimpleNamespace(tool_registry=_Registry()), spec=SimpleNamespace(tool_names=[]),
    )
    ctx = SimpleNamespace(turn_index=0, scope=SimpleNamespace(run=run))

    async def _next() -> None:
        return None

    context = make_context(previous_conversations=_HISTORY)
    if parked is not None:
        context.tool_state[VISIBLE_TOOLS] = parked
    await seed_visible_tools_from_history(context)(ctx, _next)  # type: ignore[arg-type]
    return run.visible_tools


async def test_tools_used_earlier_join_what_preloading_already_made_visible() -> None:
    assert await _seed({"fetch_tools", "preloaded_tool"}) == {
        "fetch_tools", "preloaded_tool", "mcp_atlassian_getConfluenceSpaces",
    }


async def test_with_nothing_visible_yet_they_join_the_starting_set(monkeypatch: Any) -> None:  # noqa: ANN401
    import app.agent_loop_lib.agent.tool_loop as tool_loop

    monkeypatch.setattr(tool_loop, "initial_visible_tools", lambda spec, runtime: {"fetch_tools"})
    assert await _seed(None) == {"fetch_tools", "mcp_atlassian_getConfluenceSpaces"}


async def test_the_tools_an_answered_questions_turn_could_see_join_too() -> None:
    assert await _seed({"fetch_tools"}, parked=["mcp_atlassian_createConfluencePage", "gone_tool"]) == {
        "fetch_tools", "mcp_atlassian_getConfluenceSpaces", "mcp_atlassian_createConfluencePage",
    }
