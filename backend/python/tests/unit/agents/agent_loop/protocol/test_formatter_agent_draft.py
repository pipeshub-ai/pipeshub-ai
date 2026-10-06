"""PH11-08: both formatters carry the agent draft, with its draftId."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.agent_builder.models import (
    AgentDraft,
    DraftKnowledge,
    DraftTool,
    DraftToolset,
    DraftUnresolved,
    DraftWebSearch,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.agent_draft import agent_draft_sse
from app.agents.agent_loop.protocol.formatter import (
    AGUI_FORMATTER,
    LEGACY_FORMATTER,
    ProtocolFormatter,
)


def _draft() -> AgentDraft:
    return AgentDraft(
        name="Offer drafter", handleSuggestion="offer-drafter", description="d", instructions="i",
        knowledge=["kb-1"], suggestedTools=["jira__create_issue"], requestedBy="u1",
    )


def _rich_draft() -> AgentDraft:
    return AgentDraft(
        name="HR helper", handleSuggestion="hr-helper", description="d", instructions="i", knowledge=["kb-1"],
        knowledgeSources=[DraftKnowledge(id="kb-1", name="HR Policies", kind="collection")],
        actions=[DraftToolset(
            instanceId="i-1", name="jira", displayName="Jira",
            tools=[DraftTool(name="create_issue", fullName="jira.create_issue")],
        )],
        webSearch=DraftWebSearch(provider="duckduckgo", providerLabel="DuckDuckGo"),
        unresolved=[DraftUnresolved(kind="knowledge", query="Payroll", reason="not_found")],
        revisesDraftId="d-0", requestedBy="u1",
    )


def _context(**kw) -> AgentContext:
    return AgentContext(org_id="o", user_id="u1", user_email="u@x", run_id="run-1", **kw)


def test_agui_emits_a_custom_agent_draft_frame() -> None:
    draft = _draft()
    [frame] = AGUI_FORMATTER.agent_draft(_context(), draft=draft)
    assert frame["event"] == "CUSTOM"
    body = json.loads(frame["data"]) if isinstance(frame["data"], str) else frame["data"]
    assert body["name"] == "agent_draft"
    assert body["value"]["draftId"] == draft.draftId
    assert body["value"]["toolsets"] == []


def test_legacy_emits_an_agent_draft_event() -> None:
    draft = _draft()
    assert LEGACY_FORMATTER.agent_draft(_context(), draft=draft) == [
        {"event": "agent_draft", "data": draft.model_dump()}
    ]


def test_the_protocol_requires_every_formatter_to_implement_it() -> None:
    assert "agent_draft" in ProtocolFormatter.__abstractmethods__


def _tool_ctx(name_data: str, success: bool = True) -> MagicMock:
    ctx = MagicMock()
    ctx.tool_path = "/tools/agent_builder/draft_agent"
    ctx.tool_response.success = success
    ctx.tool_response.data = name_data
    registry = ctx.scope.turn.run.runtime.tool_registry
    registry.has_path.return_value = True
    registry.resolve.return_value.name = "agent_builder__draft_agent"
    return ctx


async def _run_hook(context: AgentContext, ctx: MagicMock) -> list:
    sink = MagicMock()
    sink.write = AsyncMock()
    context.event_sink = sink
    next_fn = AsyncMock()
    await agent_draft_sse(context)(ctx, next_fn)
    return [c.args[0] for c in sink.write.call_args_list]


@pytest.mark.parametrize("protocol", ["legacy", "agui"])
async def test_hook_writes_the_draft_once_for_the_assistant(protocol: str) -> None:
    draft = _draft()
    payload = json.dumps({"status": "drafted", "draft": draft.model_dump()})
    written = await _run_hook(
        _context(invocation="assistant", has_ui_client=True, protocol=protocol), _tool_ctx(payload),
    )
    assert len(written) == 1


async def test_hook_ignores_errors_and_other_invocations() -> None:
    err = json.dumps({"status": "error", "code": "RATE_LIMITED"})
    assert await _run_hook(_context(invocation="assistant", has_ui_client=True), _tool_ctx(err)) == []
    ok = json.dumps({"status": "drafted", "draft": _draft().model_dump()})
    assert await _run_hook(_context(invocation="saved_agent", has_ui_client=True), _tool_ctx(ok)) == []
    assert await _run_hook(_context(invocation="assistant", has_ui_client=False), _tool_ctx(ok)) == []


@pytest.mark.parametrize("protocol", ["legacy", "agui"])
async def test_hook_carries_knowledge_actions_web_search_and_unresolved(protocol: str) -> None:
    draft = _rich_draft()
    payload = json.dumps({"status": "drafted", "draft": draft.model_dump(), "message": "m"})
    [frame] = await _run_hook(
        _context(invocation="assistant", has_ui_client=True, protocol=protocol), _tool_ctx(payload),
    )
    body = frame["data"]
    body = json.loads(body) if isinstance(body, str) else body
    value = body["value"] if protocol == "agui" else body
    assert AgentDraft.model_validate(value) == draft


async def test_list_agent_options_does_not_produce_a_draft_card() -> None:
    ctx = _tool_ctx(json.dumps({"status": "ok", "knowledge": [], "actionToolsets": []}))
    ctx.tool_path = "/tools/agent_builder/list_agent_options"
    ctx.scope.turn.run.runtime.tool_registry.resolve.return_value.name = "agent_builder__list_agent_options"
    assert await _run_hook(_context(invocation="assistant", has_ui_client=True), ctx) == []
