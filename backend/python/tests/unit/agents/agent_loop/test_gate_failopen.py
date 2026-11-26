"""GATE-FAILOPEN-1: a call to an MCP tool the approval gate cannot resolve is refused, never run.

The gate found an MCP call's server and rules only through ``TOOLS_BY_PATH``, which the loader fills as it registers
each adapter. A call to an MCP path the table did not name (a tool registered without it, a table another context
filled) was treated as no gated tool at all and ran with no rule read: a person's Block on it, or the default Ask
for a write, was skipped. Both deciders (today's loop gate and the durable run's ``ToolRulesPolicy``) now refuse it.
A stand-in that never reaches a server (an unconnected server's masked tool, WS-09) says so, and keeps its answer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop import tool_approvals as approvals
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.models import MCPToolInfo
from app.harness.assembly.needs_authentication import UnconnectedMcpTool
from app.harness.tool_rules import ToolRulesPolicy
from harness_core.kernel.core.messages import ToolCall
from tests.unit.agents.adapter.conftest import make_context


@pytest.fixture(autouse=True)
def _mcp_rules_on() -> Any:  # noqa: ANN401
    with patch.dict("os.environ", {"MCP_TOOL_APPROVALS": "true"}):
        yield


class _Store:
    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return default

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        return True


def _context() -> Any:  # noqa: ANN401
    return make_context(config_service=_Store(), client_name="pipeshub-ai", chat_streaming=True,
                        conversation_id="conv-1", agent_key="agent-1")


def _adapter() -> MCPToolAdapter:
    server = ResolvedMCPServer(
        instance_id="inst-1", name="Jira", display_name="Jira", instance={"_id": "inst-1", "authMode": "none"},
        auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(name="search", namespaced_name="mcp_jira_search", input_schema={"type": "object"},
                       annotations={"readOnlyHint": True})
    return MCPToolAdapter(server, info, session_manager=None)  # type: ignore[arg-type]


async def _gate(context: Any, path: str) -> ToolCallContext:  # noqa: ANN401
    ctx = ToolCallContext(tool_path=path, tool_input={})

    async def _next() -> None:
        return None

    await approvals.approval_gate(context)(ctx, _next)
    return ctx


async def test_the_gate_refuses_an_mcp_call_it_cannot_resolve() -> None:
    ctx = await _gate(_context(), _adapter().path)
    assert ctx.decision == PreDecision.DENY
    assert "wasn't run" in (ctx.decision_reason or "")


async def test_the_gate_still_runs_a_resolved_read_only_mcp_tool() -> None:
    context = _context()
    adapter = _adapter()
    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
    assert (await _gate(context, adapter.path)).decision == PreDecision.ALLOW


async def test_a_stand_in_that_never_reaches_a_server_keeps_its_own_answer() -> None:
    context = _context()
    tool = UnconnectedMcpTool(instance="inst-2", server="Wiki", tool="find", name="mcp_wiki_find")
    approvals.never_reaches_a_server(context, tool.path)
    assert (await _gate(context, tool.path)).decision == PreDecision.ALLOW


async def test_another_kind_of_tool_is_not_touched() -> None:
    assert (await _gate(_context(), "/toolsets/jira/create_issue")).decision == PreDecision.ALLOW


def _allow(_call: ToolCall, _effect: object) -> str:
    return "allow"


async def test_a_durable_run_refuses_an_mcp_tool_its_rules_cannot_resolve() -> None:
    context = _context()
    registry = ToolRegistry()
    adapter = _adapter()
    registry.register_tool(adapter)
    policy = ToolRulesPolicy(base=_allow, context=context, registry=registry)  # type: ignore[arg-type]
    await policy.refresh()
    call = ToolCall(id="c", name=adapter.name, arguments={})
    assert policy(call, object()) == "deny"  # type: ignore[arg-type]
    rule, text = policy.denial(call, object())  # type: ignore[arg-type]
    assert rule == "tool_rules" and "wasn't run" in text

    context.tool_state.setdefault(approvals.TOOLS_BY_PATH, {})[adapter.path] = adapter
    await policy.refresh()
    assert policy(call, object()) == "allow"  # type: ignore[arg-type]
