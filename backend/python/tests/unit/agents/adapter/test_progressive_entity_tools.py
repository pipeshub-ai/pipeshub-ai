"""`progressive_entity_tools` (`app/agents/agent_loop/hooks/progressive_tools.py`)
— the POST_TOOL_USE middleware that grants `find_records_by_entity` once
`search_entities` has run. Mirrors `test_citation_tracking.py`'s
scope-construction helpers for `_FetchFullRecordTool`, the pattern this hook
follows.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.progressive_tools import (
    PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
    SEARCH_ENTITIES_TOOL_NAME,
    entity_tools_used_in_history,
    progressive_entity_tools,
)

_SEARCH_ENTITIES_PATH = "/tools/knowledgegraph/search_entities"


async def _noop_next() -> None:
    return None


def _spec(name: str, tool_names: list[str]) -> AgentSpec:
    return AgentSpec(
        name=name, system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )


def _tool_scope(spec: AgentSpec, registry: ToolRegistry) -> ToolScope:
    run_scope = RunScope(
        identity=RunContext(role_name=spec.name, model="m"),
        spec=spec, runtime=AgentRuntime(tool_registry=registry), goal=Goal(description="g"),
    )
    turn_scope = TurnScope(run=run_scope, turn_index=0)
    return ToolScope(turn=turn_scope, call=None, tool_path=_SEARCH_ENTITIES_PATH, messages=[])


def _agent_context() -> AgentContext:
    return AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())


def _result_ctx(scope: ToolScope, *, tool_path: str) -> ToolResultContext:
    return ToolResultContext(
        tool_path=tool_path, tool_use_id=uuid4(),
        tool_response=ToolOutput(success=True, data="ok"), scope=scope,
    )


@pytest.mark.asyncio
class TestProgressiveEntityTools:
    async def test_search_entities_call_grants_find_records(self) -> None:
        spec = _spec("caller", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        ctx = _result_ctx(_tool_scope(spec, ToolRegistry()), tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(_agent_context())(ctx, _noop_next)

        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in spec.tool_names

    @pytest.mark.parametrize("tool_path", [
        "/tools/knowledgegraph/search",
        "/tools/knowledgegraph/navigate",
        "/tools/knowledgegraph/list_files",
        "/tools/knowledgegraph/lookup_record",
    ])
    async def test_other_knowledge_tools_do_not_grant(self, tool_path: str) -> None:
        spec = _spec("caller", tool_names=["knowledgegraph__search"])
        ctx = _result_ctx(_tool_scope(spec, ToolRegistry()), tool_path=tool_path)

        await progressive_entity_tools(_agent_context())(ctx, _noop_next)

        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME not in spec.tool_names

    async def test_grants_to_root_that_has_search_entities(self) -> None:
        context = _agent_context()
        root = _spec("root", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        context.root_agent_spec = root
        caller = _spec("child", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        ctx = _result_ctx(_tool_scope(caller, ToolRegistry()), tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(context)(ctx, _noop_next)

        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in caller.tool_names
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in root.tool_names

    async def test_never_grants_to_root_without_search_entities(self) -> None:
        """Deep mode: hooks are shared with spawned sub-agents, and the
        orchestrator root must keep only its coordination tools."""
        context = _agent_context()
        orchestrator = _spec("orchestrator", tool_names=["spawn_agent", "wait_agents"])
        context.root_agent_spec = orchestrator
        child = _spec("child", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        ctx = _result_ctx(_tool_scope(child, ToolRegistry()), tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(context)(ctx, _noop_next)

        assert orchestrator.tool_names == ["spawn_agent", "wait_agents"]
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in child.tool_names

    async def test_adds_to_visible_tools_when_already_computed(self) -> None:
        spec = _spec("caller", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        scope = _tool_scope(spec, ToolRegistry())
        scope.turn.run.visible_tools = {SEARCH_ENTITIES_TOOL_NAME}
        ctx = _result_ctx(scope, tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(_agent_context())(ctx, _noop_next)

        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in scope.turn.run.visible_tools

    async def test_already_granted_is_idempotent(self) -> None:
        spec = _spec("caller", tool_names=[SEARCH_ENTITIES_TOOL_NAME, PROGRESSIVE_FIND_RECORDS_TOOL_NAME])
        ctx = _result_ctx(_tool_scope(spec, ToolRegistry()), tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(_agent_context())(ctx, _noop_next)

        assert spec.tool_names.count(PROGRESSIVE_FIND_RECORDS_TOOL_NAME) == 1

    async def test_calls_next_fn(self) -> None:
        called: list[bool] = []

        async def _next() -> None:
            called.append(True)

        spec = _spec("caller", tool_names=[SEARCH_ENTITIES_TOOL_NAME])
        ctx = _result_ctx(_tool_scope(spec, ToolRegistry()), tool_path=_SEARCH_ENTITIES_PATH)

        await progressive_entity_tools(_agent_context())(ctx, _next)

        assert called == [True]


class TestEntityToolsUsedInHistory:
    def test_detects_entity_tool_in_bot_turn(self) -> None:
        history = [
            {"role": "user_query", "content": "q"},
            {"role": "bot_response", "tool_results": [{"tool_name": "knowledgegraph__search_entities"}]},
        ]
        assert entity_tools_used_in_history(history) is True

    def test_ignores_other_tools_and_empty_history(self) -> None:
        history = [{"role": "bot_response", "tool_results": [{"tool_name": "knowledgegraph__search"}]}]
        assert entity_tools_used_in_history(history) is False
        assert entity_tools_used_in_history(None) is False
