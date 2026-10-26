"""`RepeatedReadGuard` (`app/agents/agent_loop/hooks/repeated_reads.py`) through the factory's real
hooks: one MCP read with the same arguments stops running after it already ran twice in the last
few turns (bug bash: one call 14 times in a loop)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall, ToolMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks import RepeatedReadGuard
from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import TOOLS_BY_PATH
from app.agents.mcp.models import MCPToolInfo
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


@pytest.fixture(autouse=True)
def _no_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    # The writes here would otherwise stop on a card; approvals have their own tests.
    monkeypatch.setenv("MCP_TOOL_APPROVALS", "false")


class _Server:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, server: Any, tool_name: str, arguments: dict[str, Any], **_kwargs: Any) -> CallToolResult:  # noqa: ANN401
        self.calls.append((tool_name, dict(arguments)))
        return CallToolResult(content=[TextContent(type="text", text=f"{tool_name} #{len(self.calls)}")])


def _adapter(context: Any, mcp: _Server, name: str, *, read_only: bool) -> MCPToolAdapter:  # noqa: ANN401
    server = ResolvedMCPServer(
        instance_id="inst-1", name="Linear", display_name="Linear", instance={"_id": "inst-1", "authMode": "none"},
        auth={}, owner_id="user-1", attached_tools=None,
    )
    info = MCPToolInfo(
        name=name, namespaced_name=f"mcp_linear_{name}",
        input_schema={"type": "object", "properties": {"team": {"type": "string"}, "title": {"type": "string"}}},
        annotations={"readOnlyHint": read_only},
    )
    adapter = MCPToolAdapter(server, info, SimpleNamespace(call=mcp.call), context=context)  # type: ignore[arg-type]
    context.tool_state.setdefault(TOOLS_BY_PATH, {})[adapter.path] = adapter
    return adapter


def _call(n: int, name: str = "mcp_linear_list_issues", **arguments: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id=f"c{n}", name=name, arguments=arguments or {"team": "ENG"})


async def _run(*calls: ToolCall) -> tuple[_Server, list[ToolMessage]]:
    """One reply in which the model makes `calls`, one per turn, then answers."""
    context = make_context()
    mcp = _Server()
    registry = ToolRegistry()
    registry.register_tool(_adapter(context, mcp, "list_issues", read_only=True))
    registry.register_tool(_adapter(context, mcp, "save_issue", read_only=False))
    transport = ScriptedTransport()
    for call in calls:
        transport.add_tool_call(call)
    transport.add_text("done")
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, hooks=PipesHubAgentFactory._build_hooks(context))
    spec = AgentSpec(
        name="a", system_prompt="s", tool_names=registry.names(), model=ModelSpec(provider="scripted", model="m"),
        loop=ReActLoop(), max_turns=len(calls) + 2,
    )
    agent = Agent(spec, runtime)
    await agent.run(Goal(description="list my issues"))
    results = [m for m in await agent.context.messages() if isinstance(m, ToolMessage)]
    return mcp, results


def _text(message: ToolMessage) -> str:
    return str(message.content)


class TestTheSameReadOverAndOver:
    async def test_it_stops_after_running_twice(self) -> None:
        mcp, results = await _run(*[_call(n) for n in range(1, 9)])

        assert len(mcp.calls) == 2
        assert [_text(r) for r in results[:2]] == ["list_issues #1", "list_issues #2"]
        assert _text(results[2]).startswith("Not run: Linear · list_issues already ran 2 times")
        # A model that keeps calling is refused every time, not let through every other turn.
        assert all("Stop calling it and answer the user now" in _text(r) for r in results[3:])

    async def test_other_arguments_still_run(self) -> None:
        mcp, _ = await _run(_call(1), _call(2), _call(3, team="OPS"), _call(4, team="OPS"))
        assert [c[1]["team"] for c in mcp.calls] == ["ENG", "ENG", "OPS", "OPS"]

    async def test_a_write_that_succeeded_starts_the_count_again(self) -> None:
        write = _call(3, "mcp_linear_save_issue", title="Bug")
        mcp, _ = await _run(_call(1), _call(2), write, _call(4))
        assert [c[0] for c in mcp.calls] == ["list_issues", "list_issues", "save_issue", "list_issues"]

    async def test_an_older_result_can_be_read_again(self) -> None:
        """Results a few turns back can be compacted or cleared, and the model is told to call again."""
        others = [_call(n, team=f"T{n}") for n in (3, 4, 5)]
        mcp, _ = await _run(_call(1), _call(2), *others, _call(6))
        assert [c[1]["team"] for c in mcp.calls].count("ENG") == 3

    async def test_writes_arent_guarded(self) -> None:
        writes = [_call(n, "mcp_linear_save_issue", title="Bug") for n in (1, 2, 3)]
        mcp, _ = await _run(*writes)
        assert len(mcp.calls) == 3


class _Run:
    """`RunScope`'s slot storage, for a guard driven directly."""

    def __init__(self) -> None:
        self.slots: dict[Any, Any] = {}

    def get(self, slot: Any) -> Any:  # noqa: ANN401
        return self.slots.setdefault(slot, slot.default_factory())

    def set(self, slot: Any, value: Any) -> None:  # noqa: ANN401
        self.slots[slot] = value


class TestWhereItCounts:
    def _ctx(self, adapter: MCPToolAdapter, run: _Run | None) -> ToolCallContext:
        scope = SimpleNamespace(turn=SimpleNamespace(run=run, turn_index=0)) if run is not None else None
        return ToolCallContext(tool_path=adapter.path, tool_input={"team": "ENG"}, scope=scope)  # type: ignore[arg-type]

    async def _pre(self, guard: RepeatedReadGuard, ctx: ToolCallContext) -> PreDecision:
        async def _next() -> None:
            return None

        await guard.pre_tool_use(ctx, _next)
        return ctx.decision

    async def test_each_run_counts_its_own_calls(self) -> None:
        """A child agent starts empty and may repeat a lookup its sibling made."""
        context = make_context()
        adapter = _adapter(context, _Server(), "list_issues", read_only=True)
        guard = RepeatedReadGuard(context)
        first, second = _Run(), _Run()
        from app.agents.agent_loop.hooks import repeated_reads

        first.get(repeated_reads._RUNS).turns[repeated_reads._call_key(adapter.path, {"team": "ENG"})] = [0, 0]

        assert await self._pre(guard, self._ctx(adapter, first)) == PreDecision.DENY
        assert await self._pre(guard, self._ctx(adapter, second)) == PreDecision.ALLOW

    async def test_the_approved_call_an_answer_runs_isnt_counted(self) -> None:
        context = make_context()
        adapter = _adapter(context, _Server(), "list_issues", read_only=True)
        assert await self._pre(RepeatedReadGuard(context), self._ctx(adapter, None)) == PreDecision.ALLOW
