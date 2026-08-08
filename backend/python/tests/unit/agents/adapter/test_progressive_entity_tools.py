"""`progressive_entity_tools` (`app/agents/agent_loop/hooks/progressive_tools.py`)
— the POST_TOOL_USE middleware that grants `resolve_entity_filters`,
`find_records_by_entity`, `expand_neighbors`, and `get_relationships` once
the model has made its first knowledge-graph call — content tools
(search/navigate/list_files/lookup_record) or the essential
entity-discovery tool (search_entities)
(Bug 4 / entity tools tiering: progressive tool injection). Mirrors
`test_citation_tracking.py`'s scope-construction helpers for
`_FetchFullRecordTool`, the established pattern this hook follows.
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
    PROGRESSIVE_ENTITY_TOOL_NAME,
    PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME,
    PROGRESSIVE_FIND_RECORDS_TOOL_NAME,
    PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME,
    PROGRESSIVE_TOOL_NAMES,
    progressive_entity_tools,
)


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
    return ToolScope(turn=turn_scope, call=None, tool_path="/tools/knowledgegraph/search", messages=[])


def _agent_context() -> AgentContext:
    return AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())


def _result_ctx(scope: ToolScope, *, tool_path: str) -> ToolResultContext:
    return ToolResultContext(
        tool_path=tool_path, tool_use_id=uuid4(),
        tool_response=ToolOutput(success=True, data="ok"), scope=scope,
    )


@pytest.mark.asyncio
class TestProgressiveEntityToolsGrantsAfterKnowledgeUse:
    async def test_search_call_grants_resolve_entity_filters(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__search"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in spec.tool_names
        assert PROGRESSIVE_FIND_RECORDS_TOOL_NAME in spec.tool_names
        assert PROGRESSIVE_EXPAND_NEIGHBORS_TOOL_NAME in spec.tool_names
        assert PROGRESSIVE_GET_RELATIONSHIPS_TOOL_NAME in spec.tool_names

    async def test_search_entities_call_grants_both_progressive_tools(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__search_entities"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search_entities")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_TOOL_NAMES.issubset(set(spec.tool_names))

    async def test_navigate_call_also_grants(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__navigate"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/navigate")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in spec.tool_names

    async def test_list_files_call_also_grants(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__list_files"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/list_files")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in spec.tool_names

    async def test_lookup_record_call_also_grants(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__lookup_record"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/lookup_record")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in spec.tool_names

    async def test_grants_to_both_caller_and_root_spec(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        context.root_agent_spec = _spec("root", tool_names=["knowledgegraph__search"])
        caller_spec = _spec("child", tool_names=["knowledgegraph__search"])
        scope = _tool_scope(caller_spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in caller_spec.tool_names
        assert PROGRESSIVE_ENTITY_TOOL_NAME in context.root_agent_spec.tool_names

    async def test_adds_to_visible_tools_when_already_computed(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__search"])
        scope = _tool_scope(spec, registry)
        scope.turn.run.visible_tools = {"knowledgegraph__search"}
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME in scope.turn.run.visible_tools

    async def test_unrelated_tool_call_is_a_noop(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["jira__create_issue"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/jira/create_issue")

        await middleware(ctx, _noop_next)

        assert PROGRESSIVE_ENTITY_TOOL_NAME not in spec.tool_names

    async def test_already_granted_is_idempotent(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec(
            "caller",
            tool_names=["knowledgegraph__search", PROGRESSIVE_ENTITY_TOOL_NAME],
        )
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search")

        await middleware(ctx, _noop_next)

        assert spec.tool_names.count(PROGRESSIVE_ENTITY_TOOL_NAME) == 1

    async def test_calls_next_fn_even_when_granting(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        spec = _spec("caller", tool_names=["knowledgegraph__search"])
        scope = _tool_scope(spec, registry)
        middleware = progressive_entity_tools(context)
        ctx = _result_ctx(scope, tool_path="/tools/knowledgegraph/search")
        called = False

        async def _next() -> None:
            nonlocal called
            called = True

        await middleware(ctx, _next)
        assert called is True
