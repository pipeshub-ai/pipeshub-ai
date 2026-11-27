"""UX3 LG-1..LG-5: the read-then-write flow guard on today's loop.

The durable loop asks the user before an external write that follows a read
(UX1). Today's loop serves the modes the durable one does not (planExecute,
Universal Agent Mode, project chats, regenerate through the API, the kill
switch), and it cannot ask mid-call, so the same rule refuses there: the model
reads one typed refusal and tells the user where they can approve it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.agent_loop.hooks.flow_guard import (
    LEGACY_FLOW_REFUSAL,
    legacy_flow_guard,
    register_legacy_flow_guard,
)
from harness_core.effects.flow import FlowRule
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.tools.base import Tag

SEND = ("/toolsets/slack/send_message", (Tag("category", "write"),))
DRAFT = ("/toolsets/gmail/draft_email", (Tag("category", "write"), Tag("sink", "self")))
SAVE = ("/artifacts/save_artifact", (Tag("category", "write"), Tag("sink", "none")))
SEARCH = ("/toolsets/retrieval/search", (Tag("category", "read"),))
SPAWN = ("/toolsets/builtin/spawn_agent", (Tag("spawn", "true"), Tag("spawn", "batch")))
PLAN = ("/toolsets/builtin/create_plan", (Tag("planning", "create_plan"),))
MCP = ("/mcp/acme/post_note", ())
ASK = FlowRule("ask")


def _context(history: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    return SimpleNamespace(previous_conversations=history or [], tool_state={})


async def _decide(context: SimpleNamespace, tool: tuple[str, tuple[Tag, ...]], rule: FlowRule = ASK) -> ToolCallContext:
    ctx = ToolCallContext(tool_path=tool[0], tool_input={}, tags=list(tool[1]))
    ran: list[bool] = []

    async def next_fn() -> None:
        ran.append(True)

    await legacy_flow_guard(context, (rule,))(ctx, next_fn)
    ctx.metadata["ran"] = bool(ran)
    return ctx


async def test_lg1_an_external_write_after_a_read_is_refused_before_it_runs() -> None:
    context = _context()
    assert (await _decide(context, SEARCH)).metadata["ran"]
    refused = await _decide(context, SEND)
    assert refused.decision == PreDecision.DENY
    assert not refused.metadata["ran"]
    assert refused.decision_reason == LEGACY_FLOW_REFUSAL
    assert LEGACY_FLOW_REFUSAL.startswith("approval_unavailable:")


async def test_lg1_a_conversation_with_history_counts_as_read() -> None:
    refused = await _decide(_context([{"role": "user_query", "content": "Q3 plan?"}]), SEND)
    assert refused.decision == PreDecision.DENY


async def test_lg1_a_deny_level_refuses_too() -> None:
    refused = await _decide(_context([{"role": "user_query", "content": "x"}]), SEND, FlowRule("deny"))
    assert refused.decision == PreDecision.DENY


async def test_lg1_an_untagged_tool_counts_as_an_external_write() -> None:
    # An MCP server's tool carries no tags: it may send anywhere (fail closed, as the durable loop classifies it).
    refused = await _decide(_context([{"role": "user_query", "content": "x"}]), MCP)
    assert refused.decision == PreDecision.DENY


async def test_lg2_nothing_read_yet_runs() -> None:
    sent = await _decide(_context(), SEND)
    assert sent.decision == PreDecision.ALLOW and sent.metadata["ran"]


@pytest.mark.parametrize("tool", [DRAFT, SAVE, SEARCH, SPAWN, PLAN])
async def test_lg4_own_space_internal_writes_reads_and_the_loops_machinery_run(tool: tuple[str, tuple[Tag, ...]]) -> None:
    ctx = await _decide(_context([{"role": "user_query", "content": "x"}]), tool)
    assert ctx.decision == PreDecision.ALLOW and ctx.metadata["ran"]


async def test_lg3_the_operator_off_switch_registers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness_core.kernel.hooks.registry import HookRegistry
    from tests.support.config_service import FakeConfigurationService

    monkeypatch.setenv("HARNESS_FLOW_GUARD", "false")
    hooks = HookRegistry()
    context = SimpleNamespace(**vars(_context()), config_service=FakeConfigurationService(), org_id="org-1")
    assert await register_legacy_flow_guard(hooks, context) is False
    monkeypatch.setenv("HARNESS_FLOW_GUARD", "true")
    assert await register_legacy_flow_guard(hooks, context) is True


async def test_lg5_the_durable_loops_hooks_carry_no_legacy_guard() -> None:
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from harness_core.kernel.hooks.events import HookEvent

    context = SimpleNamespace(
        tool_state={}, previous_conversations=[{"role": "user_query"}], event_sink=None,
        context_length=None, include_retrieval_context=False,
    )
    hooks = PipesHubAgentFactory._build_hooks(context)  # type: ignore[arg-type]
    ctx = ToolCallContext(tool_path=SEND[0], tool_input={}, tags=list(SEND[1]))
    await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
    assert ctx.decision == PreDecision.ALLOW


@pytest.mark.parametrize("mode", ["react", "planExecute"])
async def test_lg1_todays_loop_is_built_with_the_guard(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from harness_core.kernel.hooks.events import HookEvent
    from tests.support.config_service import FakeConfigurationService
    from tests.unit.agents.adapter.conftest import FakeChatModel, make_context

    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    # An org with the harness on (ADR-OPS-08): the fake's platform settings turn it on.
    context = make_context(llm=FakeChatModel(), config_service=FakeConfigurationService())
    context.previous_conversations = [{"role": "user_query", "content": "Q3 plan?"}]
    _agent, runtime, _goal, _clarifying = await PipesHubAgentFactory().create(context, context.llm, mode, query="post it")
    ctx = ToolCallContext(tool_path=SEND[0], tool_input={}, tags=list(SEND[1]))
    await runtime.hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
    assert ctx.decision == PreDecision.DENY and ctx.decision_reason == LEGACY_FLOW_REFUSAL
