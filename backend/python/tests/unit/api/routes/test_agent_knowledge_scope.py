"""``chat_stream`` hands the agent loop a scope no wider than the agent's knowledge.

Drives the real route with the agent loop replaced by a recorder, so these
fail if the route stops applying ``resolve_agent_filters`` — the pure
function's own tests cannot catch that.
"""
from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.selection_scope as selection_scope
from app.services.graph_db.interface.graph_db_provider import AccessCheck

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "o1"


@pytest.fixture(autouse=True)
def _forget_resolved_selections():
    selection_scope._memo.clear()
    yield
    selection_scope._memo.clear()

AGENT_KNOWLEDGE = [
    {"connectorId": "jira-1", "type": "JIRA"},
    {"connectorId": "kb-1", "type": "KB"},
]


def _services(
    agent: dict, *, apps: dict | None = None, roles: dict | None = None,
    readable: tuple[str, ...] = (), selection_nodes: dict | None = None,
) -> dict:
    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value=agent)
    graph.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset(readable)))
    graph.get_selection_nodes = AsyncMock(return_value=selection_nodes or {"groups": [], "records": []})
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})

    async def _get_document(key: str, collection: str) -> dict | None:
        if collection == "users":
            return {"_key": "creator-key", "userId": "creator", "orgId": ORG, "email": "c@x.com"}
        return None

    async def _nodes(collection: str, field: str, values: list, return_fields=None, **_: object) -> list:
        return [{"id": v, **(apps or {})[v]} for v in values if v in (apps or {})]

    users = {"caller": {"_key": "caller-key", "email": "u@x.com"},
             "creator": {"_key": "creator-key", "email": "c@x.com"}}
    graph.get_document = AsyncMock(side_effect=_get_document)
    graph.get_nodes_by_field_in = AsyncMock(side_effect=_nodes)
    graph.get_user_by_user_id = AsyncMock(side_effect=lambda user_id: users.get(user_id))

    async def _role(kb_id: str, user: str) -> str | None:
        return (roles or {}).get((kb_id, user))

    graph.get_user_kb_permission = AsyncMock(side_effect=_role)
    return {
        "graph_provider": graph,
        "retrieval_service": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": AsyncMock(),
        "logger": logging.getLogger("agent-route-test"),
    }


async def _run(
    agent: dict, body: dict, *, graph_out: list | None = None, frames_out: list | None = None,
    **graph_kwargs,
) -> dict:
    """Run chat_stream for a saved agent; return the query_info the loop got."""
    from app.api.routes.agent import chat_stream

    captured: dict = {}

    async def _recording_loop(
        query_info: dict, *_args: object, **_kwargs: object,
    ) -> AsyncIterator[str]:
        captured.update(query_info)
        return
        yield  # pragma: no cover - marks this an async generator

    services = _services(agent, **graph_kwargs)
    if graph_out is not None:
        graph_out.append(services["graph_provider"])
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.headers = {}
    request.app.state.toolset_registry = MagicMock()

    with ExitStack() as stack:
        for target, kwargs in [
            ("get_services", {"new_callable": AsyncMock, "return_value": services}),
            ("_get_user_context", {"return_value": {"userId": "caller", "orgId": ORG}}),
            ("_get_user_document", {"new_callable": AsyncMock,
                                    "return_value": {"email": "u@x.com", "_key": "caller-key"}}),
            ("_get_org_info", {"new_callable": AsyncMock,
                               "return_value": {"orgId": ORG, "accountType": "enterprise"}}),
            ("get_llm_for_chat", {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})}),
            ("load_entity_vector_store", {"new_callable": AsyncMock, "return_value": None}),
            ("run_agent_loop_stream", {"new": _recording_loop}),
        ]:
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        response = await chat_stream(request, "agent-1")
        chunks = [chunk async for chunk in response.body_iterator]
        if frames_out is not None:
            frames_out.extend(c.decode() if isinstance(c, bytes) else c for c in chunks)
    return captured


def _agent(*, service_account: bool) -> dict:
    return {
        "_key": "agent-1",
        "name": "agent",
        "knowledge": AGENT_KNOWLEDGE,
        "toolsets": [],
        "models": [],
        "createdBy": "creator-key",
        "isServiceAccount": service_account,
    }


@pytest.mark.parametrize("service_account", [False, True])
class TestSavedAgentScope:
    async def test_foreign_connector_never_reaches_the_loop(self, service_account) -> None:
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["creator-private-gmail"], "kb": []}},
        )
        assert query_info, "agent loop was not reached"
        assert "creator-private-gmail" not in query_info["filters"]["apps"]
        assert query_info["filters"]["apps"] == []
        assert all(k.get("connectorId") != "creator-private-gmail" for k in query_info["knowledge"])

    async def test_foreign_kb_is_dropped_alongside_own_sources(self, service_account) -> None:
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["kb-1", "someone-elses-kb"]}},
        )
        assert query_info["filters"]["apps"] == ["jira-1"]
        assert query_info["filters"]["kb"] == ["kb-1"]

    async def test_no_filters_uses_the_agents_knowledge(self, service_account) -> None:
        query_info = await _run(_agent(service_account=service_account), {"query": "q"})
        assert query_info["filters"]["apps"] == ["jira-1"]
        assert query_info["filters"]["kb"] == ["kb-1"]

    async def test_callers_project_collection_is_kept(self, service_account) -> None:
        """A project chat adds the project's own hidden collection to ``kb``;
        the caller may search their own project's files."""
        hidden = {"type": "KB", "isHidden": True, "orgId": ORG}
        graphs: list = []
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["proj-kb"]}, "strictScope": True},
            graph_out=graphs,
            apps={"proj-kb": hidden},
            roles={("proj-kb", "caller-key"): "READER"},
        )
        assert query_info["filters"]["kb"] == ["proj-kb"]
        assert query_info["filters"]["strictScope"] is True
        # Admission is checked for the caller, never the run-as creator.
        roles_checked = [c.args for c in graphs[0].get_user_kb_permission.await_args_list]
        assert roles_checked == [("proj-kb", "caller-key")]

    async def test_project_collection_after_the_projects_other_collections(self, service_account) -> None:
        """Node appends the hidden project collection after the project's
        other collections, none of which the agent has."""
        visible = {f"proj-visible-{i}": {"type": "KB", "isHidden": False, "orgId": ORG} for i in range(6)}
        hidden = {"proj-kb": {"type": "KB", "isHidden": True, "orgId": ORG}}
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": [*visible, "proj-kb"]}, "strictScope": True},
            apps=visible | hidden,
            roles={("proj-kb", "caller-key"): "READER"},
        )
        assert query_info["filters"]["kb"] == ["proj-kb"]

    async def test_hidden_collection_the_caller_cannot_read_is_dropped(self, service_account) -> None:
        hidden = {"type": "KB", "isHidden": True, "orgId": ORG}
        query_info = await _run(
            _agent(service_account=service_account),
            {"query": "q", "filters": {"apps": ["jira-1"], "kb": ["proj-kb"]}},
            apps={"proj-kb": hidden},
            roles={},
        )
        assert "proj-kb" not in query_info["filters"]["kb"]


LIMITED_KNOWLEDGE = [
    {"connectorId": "jira-1", "type": "JIRA", "filtersParsed": {"recordGroups": ["proj-a"]}},
    {"connectorId": "kb-1", "type": "KB"},
]
PROJ_A = {"groups": [{"id": "proj-a", "connectorId": "jira-1"}], "records": []}


@pytest.mark.parametrize("service_account", [False, True])
class TestSourceLimitedToSomeNodes:
    """A source saved with record groups or records is searched as those nodes only."""

    async def test_the_turn_searches_the_nodes_and_is_bounded_by_them(self, service_account) -> None:
        graphs: list = []
        query_info = await _run(
            {**_agent(service_account=service_account), "knowledge": LIMITED_KNOWLEDGE},
            {"query": "q"},
            graph_out=graphs, readable=("proj-a",), selection_nodes=PROJ_A,
            apps={"jira-1": {"vectorMembershipBackfilled": True}},
        )
        filters = query_info["filters"]
        assert (filters["apps"], filters["kb"], filters["recordGroups"]) == ([], ["kb-1"], ["proj-a"])
        assert (filters["allowedApps"], filters["allowedRecordGroups"], filters["allowedRecords"]) == (
            ["kb-1"], ["proj-a"], [],
        )
        assert filters["strictScope"] is True and filters["selectionApps"] == ["jira-1"]
        # The source stays enabled for the tools that work per app.
        assert [k["connectorId"] for k in query_info["knowledge"]] == ["jira-1", "kb-1"]
        # Resolved as the identity the search runs as.
        searcher = "creator-key" if service_account else "caller-key"
        assert graphs[0].check_access.await_args.args[0] == searcher

    async def test_a_callers_selection_is_bounded_by_the_agent_and_by_the_project(self, service_account) -> None:
        query_info = await _run(
            {**_agent(service_account=service_account), "knowledge": LIMITED_KNOWLEDGE},
            {
                "query": "q",
                "filters": {"apps": [], "kb": [], "records": ["picked"], "projectApps": ["anything"]},
                "allowedFilters": {"apps": ["kb-1"], "records": ["project-folder"]},
                "strictScope": True,
            },
        )
        filters = query_info["filters"]
        assert (filters["apps"], filters["records"]) == ([], ["picked"])
        assert "recordGroups" not in filters
        assert (filters["allowedApps"], filters["allowedRecordGroups"]) == (["kb-1"], ["proj-a"])
        assert (filters["projectApps"], filters["projectRecordGroups"], filters["projectRecords"]) == (
            ["kb-1"], [], ["project-folder"],
        )

    async def test_a_turn_that_leaves_the_limited_source_out_is_not_a_selection(self, service_account) -> None:
        query_info = await _run(
            {**_agent(service_account=service_account), "knowledge": LIMITED_KNOWLEDGE},
            {"query": "q", "filters": {"apps": [], "kb": ["kb-1"]}},
        )
        filters = query_info["filters"]
        assert (filters["apps"], filters["kb"]) == ([], ["kb-1"])
        assert not any(key in filters for key in ("recordGroups", "selectionApps"))
        # Still bounded by the agent's knowledge, for the tools that reach a record by id.
        assert (filters["allowedApps"], filters["allowedRecordGroups"]) == (["kb-1"], ["proj-a"])


class TestLimitThatCannotBeSearched:
    """A saved limit larger than one turn may search, or one that cannot be read."""

    async def _stream(self, body: dict, monkeypatch, **graph_kwargs) -> tuple[dict, str]:
        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "1")
        frames: list[str] = []
        query_info = await _run(
            {**_agent(service_account=False), "knowledge": LIMITED_KNOWLEDGE}, body,
            selection_nodes={"groups": [{"id": "proj-a", "connectorId": "jira-1"},
                                        {"id": "proj-a-nested", "connectorId": "jira-1"}], "records": []},
            readable=("proj-a",), frames_out=frames, **graph_kwargs,
        )
        return query_info, "".join(frames)

    async def test_the_turn_fails_with_a_message_about_the_agent_not_the_user(self, monkeypatch) -> None:
        query_info, stream = await self._stream({"query": "q"}, monkeypatch)
        assert not query_info, "the model must not run"
        assert "selection_too_large" in stream and "This agent is limited" in stream

    async def test_a_turn_limited_only_by_the_agents_bound_fails_with_its_reason(self, monkeypatch) -> None:
        """R1-23: a turn that leaves the limited source out is no selection, so the
        bound was resolved lazily inside each tool and failed as a generic error."""
        query_info, stream = await self._stream({"query": "q", "filters": {"apps": [], "kb": ["kb-1"]}}, monkeypatch)
        assert not query_info, "the model must not run"
        assert "selection_too_large" in stream and "This agent is limited" in stream

    async def test_a_selection_the_user_made_is_named_as_theirs(self, monkeypatch) -> None:
        _, stream = await self._stream(
            {"query": "q", "filters": {"apps": [], "kb": [], "recordGroups": ["proj-a"]}}, monkeypatch,
        )
        assert "selection_too_large" in stream and "you selected" in stream

    async def test_a_turn_that_does_not_search_is_not_held_up(self, monkeypatch) -> None:
        query_info, stream = await self._stream(
            {"query": "q", "agentCapabilities": {"internalSearch": False}}, monkeypatch,
        )
        assert query_info and "selection_too_large" not in stream
        assert query_info["filters"]["apps"] == [] and query_info["knowledge"] == []

    async def test_a_source_whose_stored_limit_cannot_be_read_is_not_searched(self, caplog) -> None:
        knowledge = [
            {"connectorId": "jira-1", "type": "JIRA", "filters": "{bad", "filtersParsed": {}},
            {"connectorId": "kb-1", "type": "KB"},
        ]
        with caplog.at_level(logging.WARNING, logger="agent-route-test"):
            query_info = await _run({**_agent(service_account=True), "knowledge": knowledge}, {"query": "q"})
        assert (query_info["filters"]["apps"], query_info["filters"]["kb"]) == ([], ["kb-1"])
        assert [k["connectorId"] for k in query_info["knowledge"]] == ["kb-1"]
        assert "cannot be read" in caplog.text and "jira-1" in caplog.text


async def test_dropped_ids_are_logged_with_the_agent(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="agent-route-test"):
        await _run(
            _agent(service_account=True),
            {"query": "q", "filters": {"apps": ["creator-private-gmail"], "kb": []}},
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("agent-1" in m and "creator-private-gmail" in m for m in messages)


async def test_nothing_logged_when_nothing_is_dropped(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="agent-route-test"):
        await _run(_agent(service_account=False), {"query": "q", "filters": {"apps": ["jira-1"], "kb": []}})
    assert not any("outside the agent's knowledge" in r.getMessage() for r in caplog.records)


class TestSavingALimit:
    """`_refuse_limit_over_the_cap`: an agent no turn could search is not saved."""

    @staticmethod
    def _graph(groups: int) -> AsyncMock:
        graph = AsyncMock()
        graph.get_selection_nodes = AsyncMock(return_value={
            "groups": [{"id": f"g{i}", "connectorId": "jira-1"} for i in range(groups)], "records": [],
        })
        return graph

    async def test_a_limit_over_the_cap_is_refused_with_the_number(self, monkeypatch) -> None:
        from app.api.routes.agent import InvalidRequestError, _refuse_limit_over_the_cap

        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "2")
        sources = {"jira-1": {"connectorId": "jira-1", "filters": {"recordGroups": ["proj-a"]}}}
        with pytest.raises(InvalidRequestError, match="more than 2 records"):
            await _refuse_limit_over_the_cap(sources, self._graph(3), ORG, logging.getLogger("t"))

    async def test_a_limit_at_the_cap_is_saved(self, monkeypatch) -> None:
        from app.api.routes.agent import _refuse_limit_over_the_cap

        monkeypatch.setenv("SEARCH_SELECTION_MAX_NODES", "2")
        sources = {"jira-1": {"connectorId": "jira-1", "filters": {"recordGroups": ["proj-a"]}}}
        await _refuse_limit_over_the_cap(sources, self._graph(2), ORG, logging.getLogger("t"))

    async def test_whole_sources_are_not_counted(self) -> None:
        from app.api.routes.agent import _refuse_limit_over_the_cap

        graph = self._graph(0)
        await _refuse_limit_over_the_cap(
            {"jira-1": {"connectorId": "jira-1", "filters": {}}}, graph, ORG, logging.getLogger("t"),
        )
        graph.get_selection_nodes.assert_not_awaited()

    async def test_a_count_that_fails_does_not_block_the_save(self, caplog) -> None:
        from app.api.routes.agent import _refuse_limit_over_the_cap

        graph = AsyncMock()
        graph.get_selection_nodes = AsyncMock(side_effect=RuntimeError("graph down"))
        sources = {"jira-1": {"connectorId": "jira-1", "filters": {"records": ["f1"]}}}
        await _refuse_limit_over_the_cap(sources, graph, ORG, logging.getLogger("t"))
        assert "Could not size" in caplog.text
