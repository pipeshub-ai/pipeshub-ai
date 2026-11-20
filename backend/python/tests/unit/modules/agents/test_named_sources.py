"""The sources a turn's model calls may name, the tools of the person's own a run offers, and how the prompts render
what they may not name.

Without an edition that says others read a run, every source and every tool is offered and named as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.agent_loop_lib.core.messages import TextPart
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.hooks.unnamed_sources import keep_unnamed_sources_out
from app.modules.agents.chat_extension import ChatExtension
from app.modules.agents.context.named_sources import (
    NAMED_SOURCES_KEY,
    NamedSources,
    nameable_source_ids,
    named_sources_in,
    picked_source_ids,
    runs_as_assistant,
    without_unnamed_sources,
)
from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.agents.qna.router import build_capability_context, build_tier_rubric
from app.services.graph_db.interface.graph_db_provider import SELECTION_APPS_FILTER_KEY
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

PRIVATE = {"connectorId": "kb-private", "name": "My Private Notes", "type": "KB"}
TRACKER = {"connectorId": "app-tracker", "name": "Team Tracker", "type": "JIRA"}
MAIL = {
    "instanceId": "ts-mail", "name": "mail", "displayName": "My Mail", "scope": "personal",
    "tools": [{"name": "send", "fullName": "mail.send", "description": "send mail"}],
}
SERVER = {"instanceId": "mcp-own", "name": "own_crm", "displayName": "Own CRM"}


class _OthersRead(ChatExtension):
    def read_by_others(self, context: AgentContext) -> bool:
        return True


def _context(extension: ChatExtension | None = None, **fields: Any) -> AgentContext:  # noqa: ANN401
    target = extension or ChatExtension()
    with (
        patch("app.modules.agents.chat_extension.get_chat_extension", return_value=target),
        patch("app.agents.agent_loop.context.get_chat_extension", return_value=target),
    ):
        context = make_context(invocation="assistant", has_knowledge=True, **fields)
        # The decision is asked once; read it while the extension is patched in.
        assert context.read_by_others is isinstance(target, _OthersRead)
        context.named_sources  # noqa: B018
        return context


class TestPickedSourceIds:
    @pytest.mark.parametrize(
        ("requested", "resolved", "picked"),
        [
            (None, None, None),
            ({}, None, None),
            ({"apps": [], "kb": []}, None, None),
            ({"apps": ["app-1"], "kb": []}, None, ["app-1"]),
            ({"kb": ["kb-1", "NO_KB_SELECTED", " ", 7]}, None, ["kb-1"]),
            ({"apps": ["app-1", "app-1"], "kb": ["kb-1"]}, None, ["app-1", "kb-1"]),
            ({"records": ["rec-1"]}, {SELECTION_APPS_FILTER_KEY: ["app-2"]}, ["app-2"]),
            ({"records": ["rec-1"]}, None, []),
        ],
        ids=["no filters", "empty", "empty buckets", "an app", "a collection", "both", "a selection", "a selection on nothing"],
    )
    def test_what_the_request_picked(self, requested: object, resolved: object, picked: list[str] | None) -> None:
        assert picked_source_ids(requested, resolved) == picked


class TestWithoutTheEdition:
    def test_nobody_else_reads_and_every_source_is_named(self) -> None:
        context = _context(agent_knowledge=[dict(PRIVATE), dict(TRACKER)])

        assert context.named_sources == NamedSources(knowledge=(PRIVATE, TRACKER))
        assert context.tool_state[NAMED_SOURCES_KEY] is context.named_sources

    def test_every_tool_of_the_persons_own_is_offered(self) -> None:
        context = _context(agent_toolsets=[dict(MAIL)], mcp_servers=[dict(SERVER)])

        assert [t["instanceId"] for t in context.agent_toolsets] == ["ts-mail"]
        assert [s["instanceId"] for s in context.mcp_servers] == ["mcp-own"]

    def test_a_state_built_outside_a_run_names_what_it_holds(self) -> None:
        state = {"agent_knowledge": [PRIVATE], "available_connectors": [{"id": "c", "type": "JIRA"}]}

        assert named_sources_in(state) == NamedSources(knowledge=(PRIVATE,), connectors=({"id": "c", "type": "JIRA"},))


class TestWhenOthersRead:
    def test_only_the_picked_sources_are_named(self) -> None:
        context = _context(
            _OthersRead(), agent_knowledge=[dict(PRIVATE), dict(TRACKER)], tool_state={"picked_source_ids": ["app-tracker"]},
        )

        assert context.named_sources == NamedSources(knowledge=(TRACKER,), unnamed=True, picked=True, limited=True)

    def test_with_nothing_picked_none_is_named_and_the_run_still_searches(self) -> None:
        context = _context(_OthersRead(), agent_knowledge=[dict(PRIVATE), dict(TRACKER)])

        assert context.named_sources == NamedSources(unnamed=True, limited=True)
        assert context.tool_state["agent_knowledge"] == [PRIVATE, TRACKER]

    def test_the_persons_own_tools_are_left_out(self) -> None:
        own_server = {"mcp-own": {"instance": {"_id": "mcp-own", "scope": "personal"}, "auth": {}}}
        context = _context(
            _OthersRead(), agent_toolsets=[dict(MAIL)], mcp_servers=[dict(SERVER)], mcp_server_configs=own_server,
        )

        assert context.agent_toolsets == [] and context.tool_state["tools"] == []
        assert context.mcp_servers == [] and context.mcp_server_configs == {}

    def test_a_pick_the_chat_route_cannot_name_is_said_to_be_the_pick(self) -> None:
        """The chat route lists connectors by type and never a collection; with a pick, what it searches is the pick."""
        context = _context(
            _OthersRead(), tool_state={"available_connectors": [{"id": "c1", "type": "JIRA"}], "picked_source_ids": ["kb-9"]},
        )

        assert context.named_sources == NamedSources(unnamed=True, picked=True, limited=True)
        text = SourceCatalog.from_state(context.tool_state, named=context.named_sources).render(["knowledgegraph__search"])
        assert "The sources picked for this question" in text
        assert "Everything the person asking can read" not in text and "JIRA" not in text

    def test_a_saved_agent_is_left_as_configured(self) -> None:
        context = _context(_OthersRead(), agent_key="agent-7", agent_knowledge=[dict(PRIVATE)], agent_toolsets=[dict(MAIL)])

        assert not runs_as_assistant(context)
        assert context.named_sources == NamedSources(knowledge=(PRIVATE,))
        assert [t["instanceId"] for t in context.agent_toolsets] == ["ts-mail"]


class TestTheUnnamedEntry:
    def test_it_names_nothing_and_says_search_covers_everything(self) -> None:
        catalog = SourceCatalog.from_state({}, named=NamedSources(unnamed=True))

        text = catalog.render(["knowledgegraph__search"])
        assert not catalog.is_empty()
        assert "Everything the person asking can read" in text
        assert "`knowledgegraph__search` without `source_ids`" in text
        assert "source_ids: [\"<id>\"]" not in text

    def test_beside_a_picked_source_it_says_more_is_searched(self) -> None:
        catalog = SourceCatalog.from_state({}, named=NamedSources(knowledge=(TRACKER,), unnamed=True))

        text = catalog.render(["knowledgegraph__search"])
        assert "Team Tracker" in text and "`app-tracker`" in text
        assert "searched too" in text

    def test_it_cannot_tell_which_live_apps_are_indexed(self) -> None:
        catalog = SourceCatalog.from_state({}, named=NamedSources(unnamed=True))

        assert catalog.live_only_apps(frozenset({"jira", "slack"})) == ()


class TestTheRouter:
    def test_sources_it_may_not_name_are_neither_named_nor_counted(self) -> None:
        block, count, sources, _ = build_capability_context({
            "knowledge": [], "knowledgeUnnamed": True, "filters": {"apps": ["a", "b", "c"], "kb": ["k"]},
        })

        assert count is None and sources == []
        assert "every source the person asking can read" in block
        assert "(4 total)" not in block and "connector(s)" not in block

    def test_a_pick_it_may_not_name_is_called_the_pick(self) -> None:
        block, count, _, _ = build_capability_context({"knowledge": [], "knowledgeUnnamed": True, "knowledgePicked": True})

        assert count is None
        assert "the sources picked for this question" in block and "every source" not in block

    def test_a_count_it_may_not_give_keeps_deep_viable(self) -> None:
        rubric = build_tier_rubric("", "", None)

        assert "sources are not listed here — deep is viable" in rubric
        assert "(not listed)" in rubric
        assert "source(s) configured" not in rubric

    def test_a_count_it_may_give_is_unchanged(self) -> None:
        rubric = build_tier_rubric("", "", 1)

        assert "1 source(s) configured — deep is NOT viable (need ≥2)" in rubric
        assert "(1 configured)" in rubric


class TestToolResults:
    """In a turn others read, a tool's result names only the sources the prompts name."""

    HEADER = (
        "Record ID: r1\nConnector: KB\nConnector ID: kb-private\n"
        "Location: My Private Notes (App ID: kb-private) -> Ideas (Record Group ID: rg-1) -> note.txt (Record ID: r1)\n"
        "Connector ID: app-tracker | Location: Team Tracker (App ID: app-tracker)\nConnector ID: N/A"
    )

    def test_a_source_it_may_not_name_is_another_source(self) -> None:
        out = without_unnamed_sources(self.HEADER, frozenset({"app-tracker"}))

        assert "My Private Notes" not in out and "kb-private" not in out
        assert "Location: another source -> Ideas (Record Group ID: rg-1) -> note.txt (Record ID: r1)" in out
        assert "Connector ID: app-tracker" in out and "Location: Team Tracker (App ID: app-tracker)" in out
        assert "Connector ID: N/A" in out and out.startswith("Record ID: r1\nConnector: KB\n")

    def test_the_sources_it_may_name_are_the_named_ones_only_when_names_are_limited(self) -> None:
        assert nameable_source_ids({}) is None
        assert nameable_source_ids({NAMED_SOURCES_KEY: NamedSources(knowledge=(TRACKER,))}) is None
        limited = NamedSources(knowledge=(TRACKER,), unnamed=True, limited=True)
        assert nameable_source_ids({NAMED_SOURCES_KEY: limited}) == frozenset({"app-tracker"})

    async def test_every_text_of_a_result_is_rewritten_before_the_rest_of_the_pipeline(self) -> None:
        context = _context(
            _OthersRead(), agent_knowledge=[dict(PRIVATE), dict(TRACKER)], tool_state={"picked_source_ids": ["app-tracker"]},
        )
        middleware = keep_unnamed_sources_out(context)
        assert middleware is not None
        other = {"type": "image", "url": "kept"}
        ctx = ToolResultContext(
            tool_path="/tools/knowledgegraph/search", tool_use_id=uuid4(),
            tool_response=ToolOutput(success=True, data=[TextPart(text=self.HEADER), self.HEADER, other]),
        )
        seen: list[Any] = []
        nxt = AsyncMock(side_effect=lambda: seen.append(ctx.tool_response.data))

        await middleware(ctx, nxt)

        assert seen, "the rest of the pipeline did not run"
        first, second, third = seen[0]
        assert "My Private Notes" not in first.text and "kb-private" not in first.text
        assert "kb-private" not in second
        assert third is other

    def test_a_run_nobody_else_reads_gets_no_rewrite(self) -> None:
        assert keep_unnamed_sources_out(_context(agent_knowledge=[dict(PRIVATE)])) is None
