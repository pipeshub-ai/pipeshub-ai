"""FU4: a durable run shows main's MCP sign-in card where today's loop does, the web app, and only there.

The run's surface is pinned (``x.pipeshub.surface``); a run started in the web app gets main's card and the model
main's "sign in with the button" hint, any other surface main's message alone. The servers are journaled with the
call that found them, so a resumed attempt's answer keeps the card.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_loop.mcp_sign_in import BUTTON_HINT, sign_in_part
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.errors import MCPInsufficientScopeError
from app.harness.assembly.context import RunContext
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.presentation import PipesHubPresenter
from harness_core.journal import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.kernel.durable.ports import RunEnding
from harness_core.tools.registry import ToolRegistry
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_mcp_tool_adapter import (
    _FakeSessionManager,
    _server,
    _tool_info,
)
from tests.unit.harness.assembly import world as w

REFUSED = "The Jira MCP server needs more permission for this (scopes: write). Ask the user to reconnect it."
FINALIZED = "x.pipeshub.answer.finalized"


def _run(surface: str | None, via: str = "session") -> RunContext:
    spec = w.pinned_spec(tool_ids=[], kb=[])
    extension = dict(spec.extensions["x.pipeshub"])  # type: ignore[index]
    extension["via"] = via
    if surface is not None:
        extension["surface"] = surface
    spec = spec.model_copy(update={"extensions": {**spec.extensions, "x.pipeshub": extension}})  # type: ignore[dict-item]
    return RunContext.from_claim(w.claim(spec))  # type: ignore[arg-type]


def _durable(run: RunContext) -> Any:  # noqa: ANN401 - the AgentContext the durable worker builds
    """What the assembler leaves on the run's context before its tools load."""
    context = make_context(conversation_id=run.session_id, protocol="agui", run_id="run-1")
    context.durable_web_app = run.web_app
    return context


async def _refused_call(context: Any) -> Any:  # noqa: ANN401
    manager = _FakeSessionManager(exc=MCPInsufficientScopeError(REFUSED, scopes=["write"]))
    return await MCPToolAdapter(_server(), _tool_info(), manager, context=context).execute(query="x")


async def _finalized(context: Any, presenter: PipesHubPresenter) -> dict[str, Any]:  # noqa: ANN401
    events = await presenter.finalize(RunEnding(outcome="answered", texts=("Done.",)))
    [event] = [e for e in events if e.type == FINALIZED]
    return dict(event.data)


def _journaled(events: list[Any]) -> list[JournalEvent]:
    return [
        JournalEvent(
            org_id="org-1", session_id="s-1", stream_id="s-1", seq=seq, run_id="run-1", attempt_id="run-1.a1",
            fence=1, type=e.type, schema_version=1, critical=False, ts="2026-10-18T12:00:00Z", writer="worker",
            visibility="owner", data=dict(e.data),
        )
        for seq, e in enumerate(events, start=1)
    ]


async def _presented_call(context: Any, presenter: PipesHubPresenter) -> list[Any]:  # noqa: ANN401
    out = await _refused_call(context)
    call = ToolCall(id="c1", name="jira__search", arguments={})
    return list(await presenter.result(call, ToolResult(tool_call_id="c1", name="jira__search", content=out.error)))


class TestWhereTheRunWasStarted:
    @pytest.mark.parametrize(("surface", "via", "web"), [
        ("web", "session", True),
        ("web", "pat", False),
        ("web", "oauth_app", False),
        ("slack", "session", False),
        ("mcp", "session", False),
        (None, "session", False),
    ], ids=["web-app", "api-pat", "api-oauth", "slack", "mcp", "not-pinned"])
    def test_only_the_web_app_draws_the_card(self, surface: str | None, via: str, web: bool) -> None:
        run = _run(surface, via)
        assert (run.surface, run.web_app) == (surface, web)


class TestAWebRun:
    async def test_the_model_hears_of_the_button_and_the_answer_lists_the_server(self) -> None:
        context = _durable(_run("web"))
        out = await _refused_call(context)
        assert out.error == f"{REFUSED} {BUTTON_HINT}"

        data = await _finalized(context, PipesHubPresenter(context, ToolRegistry()))
        assert data["mcpSignIn"] == [{"instanceId": "inst-1", "serverName": "Jira MCP", "scopes": ["write"]}]

    async def test_c24_parity_the_model_and_the_reply_see_what_todays_web_chat_does(self) -> None:
        """Today's loop in the web chat (its client name) and the durable web run: the same tool error, the same part."""
        today = make_context(client_name="pipeshub-ai", conversation_id="conv-1", protocol="agui")
        durable = _durable(_run("web"))
        assert (await _refused_call(durable)).error == (await _refused_call(today)).error
        assert sign_in_part(durable) == sign_in_part(today)


class TestASlackRun:
    async def test_no_card_and_mains_non_web_wording(self) -> None:
        context = _durable(_run("slack"))
        out = await _refused_call(context)
        assert out.error == REFUSED
        # What today's loop tells the model off the web (no client name), word for word.
        assert out.error == (await _refused_call(make_context())).error

        data = await _finalized(context, PipesHubPresenter(context, ToolRegistry()))
        assert "mcpSignIn" not in data


class TestAResumedRun:
    async def test_the_card_survives_another_worker(self) -> None:
        first = _durable(_run("web"))
        journaled = await _presented_call(first, PipesHubPresenter(first, ToolRegistry(), citations=CitationJournal(first)))
        [recorded] = [e for e in journaled if e.type == CITATIONS_EVENT]
        assert recorded.data["mcpSignIn"] == [{"instanceId": "inst-1", "serverName": "Jira MCP", "scopes": ["write"]}]

        second = _durable(_run("web"))
        citations = CitationJournal(second)
        resumed = PipesHubPresenter(second, ToolRegistry(), citations=citations)
        await citations.restore(_journaled(journaled))
        data = await _finalized(second, resumed)
        assert data["mcpSignIn"] == [{"instanceId": "inst-1", "serverName": "Jira MCP", "scopes": ["write"]}]

    async def test_a_server_noted_again_on_resume_is_listed_once(self) -> None:
        first = _durable(_run("web"))
        journaled = await _presented_call(first, PipesHubPresenter(first, ToolRegistry(), citations=CitationJournal(first)))

        second = _durable(_run("web"))
        citations = CitationJournal(second)
        resumed = PipesHubPresenter(second, ToolRegistry(), citations=citations)
        await _refused_call(second)  # discovery on the new worker found it refused too
        await citations.restore(_journaled(journaled))
        assert [s["instanceId"] for s in (await _finalized(second, resumed))["mcpSignIn"]] == ["inst-1"]
        # Restored and unchanged: nothing new to journal.
        assert citations.delta() is None
