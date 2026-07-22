"""`citation_tracking` (`app/agents/agent_loop/hooks/citations.py`) — the
POST_TOOL_USE middleware that dynamically registers `_FetchFullRecordTool`
and grants it to the calling spec(s) once retrieval produces virtual
records.

Regression coverage for the fix-registry-races todo: concurrent invocations
(two retrieval-bearing tool calls in the same gathered turn) must never let
one caller's grant be skipped just because a sibling call already won the
registration race — `register_tool_if_absent` (see `tools/registry.py`)
replaces the check-then-`register_tool` pattern that used to raise
`DuplicateToolNameError` and abort the loser's own `_grant` calls.
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
from app.agents.agent_loop.hooks.citations import (
    _FETCH_FULL_RECORD_TOOL_NAME,
    CitationCollector,
    citation_tracking,
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
    return ToolScope(turn=turn_scope, call=None, tool_path="/toolsets/builtin/search", messages=[])


def _agent_context() -> AgentContext:
    context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())
    context.tool_state["virtual_record_id_to_result"] = {"rec1": {"content": "hi"}}
    return context


class TestCitationTrackingRegistersOnce:
    async def test_registers_tool_and_grants_to_caller_spec(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)
        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names

    async def test_second_call_after_already_registered_still_grants(self) -> None:
        """Simulates the losing side of a registration race: the tool is
        already registered (by a sibling call) by the time this middleware
        invocation reaches the register line — it must still reach its own
        `_grant` calls instead of raising."""
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)

        from app.agents.agent_loop.hooks.citations import _FetchFullRecordTool

        registry.register_tool(_FetchFullRecordTool(collector, context))

        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names

    async def test_grants_to_both_caller_and_root_spec_when_reference_present(self) -> None:
        registry = ToolRegistry()
        context = _agent_context()
        collector = CitationCollector(context)
        context.root_agent_spec = _spec("root", tool_names=["retrieval_search_internal_knowledge"])
        caller_spec = _spec("child", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(caller_spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert _FETCH_FULL_RECORD_TOOL_NAME in caller_spec.tool_names
        assert _FETCH_FULL_RECORD_TOOL_NAME in context.root_agent_spec.tool_names

    async def test_no_virtual_records_is_a_noop(self) -> None:
        registry = ToolRegistry()
        context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock())
        collector = CitationCollector(context)
        spec = _spec("caller", tool_names=["retrieval_search_internal_knowledge"])
        scope = _tool_scope(spec, registry)
        middleware = citation_tracking(context, collector)
        ctx = ToolResultContext(
            tool_path="/toolsets/builtin/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data="ok"), scope=scope,
        )

        await middleware(ctx, _noop_next)

        assert not registry.has(_FETCH_FULL_RECORD_TOOL_NAME)
        assert _FETCH_FULL_RECORD_TOOL_NAME not in spec.tool_names
