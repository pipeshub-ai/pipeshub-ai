"""End-to-end tests for the graph navigation agent tools.

Exercises ``lookup_record`` and ``navigate`` against a seeded in-memory graph
(``FakeGraphProvider``) that mirrors the real shape returned by
``IGraphDBProvider`` / ``KnowledgeHubService`` — no provider internals are
mocked out, so these tests catch integration bugs the per-tool unit tests
(mocked provider) cannot: real permission propagation through
``KnowledgeHubService.get_nodes``, real breadcrumb/pagination math, and the
actual SSE event shape produced by ``streaming.execute_tool_calls``.

Seeded graph (org "org-1", permissions granted to "user-a" only):

    Jira (app) -> Payments Project (recordGroup) -> PA-1787 (record/TICKET)
        |-- comment-1 (record/COMMENT, PARENT_CHILD)
        |-- attachment-1 (record/FILE, ATTACHMENT)
        `-- LINKED_TO -> Agent Loop Implementation (record/CONFLUENCE_PAGE)
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.utils.lookup_record import create_lookup_record_tool
from app.utils.navigate_tool import create_navigate_tool
from app.utils.record_tool_helpers import NOT_FOUND_ERROR
from app.utils.streaming import execute_tool_calls
from app.utils.url_resolver import normalize_weburl

ORG_ID = "org-1"
TICKET_URL = "https://pipeshub.atlassian.net/browse/PA-1787"

APP_ID = "app-jira"
GROUP_ID = "group-proj"
TICKET_ID = "rec-ticket-1"
COMMENT_ID = "rec-comment-1"
ATTACHMENT_ID = "rec-attachment-1"
CONFLUENCE_ID = "rec-confluence-1"

ALLOWED_USER_ID = "user-a"
DENIED_USER_ID = "user-b"


class FakeGraphProvider:
    """Minimal in-memory ``IGraphDBProvider`` stand-in seeded with a realistic
    app -> recordGroup -> record (+children +LINKED_TO) graph.

    Every node is accessible to ``ALLOWED_USER_ID`` only — this is enough to
    exercise real permission propagation end to end without needing a live
    Arango/Neo4j instance. Method signatures/return shapes mirror the
    production contract exactly (see ``IGraphDBProvider`` + contract tests).
    """

    NODES: dict[str, dict[str, Any]] = {
        APP_ID: {"name": "Jira", "nodeType": "app", "subType": "JIRA", "connector": "Jira"},
        GROUP_ID: {
            "name": "Payments Project", "nodeType": "recordGroup", "subType": "PROJECT",
            "recordGroupType": "PROJECT", "connector": "Jira",
        },
        TICKET_ID: {
            "name": "PA-1787 Payment service outage", "nodeType": "record", "subType": "TICKET",
            "recordType": "TICKET", "connector": "Jira", "webUrl": TICKET_URL,
            "indexingStatus": "COMPLETED", "externalRecordId": "10001",
        },
        COMMENT_ID: {
            "name": "Root cause identified", "nodeType": "record", "subType": "COMMENT",
            "recordType": "COMMENT", "connector": "Jira", "indexingStatus": "COMPLETED",
        },
        ATTACHMENT_ID: {
            "name": "stacktrace.log", "nodeType": "record", "subType": "FILE",
            "recordType": "FILE", "connector": "Jira", "indexingStatus": "COMPLETED",
        },
        CONFLUENCE_ID: {
            "name": "Agent Loop Implementation", "nodeType": "record", "subType": "CONFLUENCE_PAGE",
            "recordType": "CONFLUENCE_PAGE", "connector": "Confluence", "indexingStatus": "COMPLETED",
        },
    }

    PARENT_OF: dict[str, str] = {
        GROUP_ID: APP_ID,
        TICKET_ID: GROUP_ID,
        COMMENT_ID: TICKET_ID,
        ATTACHMENT_ID: TICKET_ID,
    }

    CHILDREN_OF: dict[str, list[str]] = {
        APP_ID: [GROUP_ID],
        GROUP_ID: [TICKET_ID],
        TICKET_ID: [COMMENT_ID, ATTACHMENT_ID],
    }

    LINKED_TO: dict[str, list[str]] = {
        TICKET_ID: [CONFLUENCE_ID],
    }

    def _has_access(self, user_key_or_id: str | None) -> bool:
        return user_key_or_id == ALLOWED_USER_ID

    def _node_doc(self, node_id: str) -> dict[str, Any]:
        info = self.NODES[node_id]
        return {
            "id": node_id,
            "name": info["name"],
            "nodeType": info["nodeType"],
            "parentId": self.PARENT_OF.get(node_id),
            "origin": "CONNECTOR",
            "connector": info.get("connector"),
            "recordType": info.get("recordType"),
            "recordGroupType": info.get("recordGroupType"),
            "indexingStatus": info.get("indexingStatus"),
            "createdAt": 0,
            "updatedAt": 0,
            "webUrl": info.get("webUrl"),
            "hasChildren": bool(self.CHILDREN_OF.get(node_id)),
        }

    # -- users -----------------------------------------------------------
    async def get_user_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        if user_id in (ALLOWED_USER_ID, DENIED_USER_ID):
            return {"_key": user_id, "email": f"{user_id}@example.com"}
        return None

    async def get_user_app_ids(self, user_key: str, transaction: str | None = None) -> list[str]:
        return [APP_ID] if self._has_access(user_key) else []

    async def get_user_permission_app_ids(self, user_key: str, org_id: str, transaction: str | None = None) -> list[str]:
        return []

    # -- org / connectors --------------------------------------------------
    async def get_org_apps(self, org_id: str) -> list[dict[str, Any]]:
        return [{"_key": APP_ID, "id": APP_ID, "type": "JIRA", "name": "Jira"}]

    # -- record resolution (lookup_record) --------------------------------
    async def get_record_by_issue_key(self, connector_id: str, issue_key: str, transaction: str | None = None) -> Any:
        if connector_id == APP_ID and issue_key.upper() == "PA-1787":
            return SimpleNamespace(id=TICKET_ID)
        return None

    async def get_record_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None) -> Any:
        if connector_id == APP_ID and external_id == "10001":
            return SimpleNamespace(id=TICKET_ID)
        return None

    async def get_record_by_weburl(self, weburl: str, org_id: str | None = None, transaction: str | None = None) -> Any:
        if weburl in (TICKET_URL, normalize_weburl(TICKET_URL)):
            return SimpleNamespace(id=TICKET_ID)
        return None

    async def find_slack_burst_record_by_ts(self, connector_id: str, channel_id: str, ts: str, transaction: str | None = None) -> Any:
        return None

    # -- permission gate + document fetch ---------------------------------
    async def check_record_access_with_details(self, user_id: str, org_id: str, record_id: str) -> dict[str, Any] | None:
        if record_id not in self.NODES or not self._has_access(user_id):
            return None
        return {"allowed": True}

    async def get_document(self, document_key: str, collection: str, transaction: str | None = None) -> dict[str, Any] | None:
        info = self.NODES.get(document_key)
        if not info or info["nodeType"] != "record":
            return None
        return {
            "id": document_key,
            "recordName": info["name"],
            "recordType": info.get("recordType"),
            "connectorName": info.get("connector"),
            "webUrl": info.get("webUrl"),
            "indexingStatus": info.get("indexingStatus"),
            "hideWeburl": False,
            "virtualRecordId": None,
        }

    async def get_related_records_by_relation_type(
        self, record_id: str, relation_type: str, edge_collection: str, transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        return [{"id": rid} for rid in self.LINKED_TO.get(record_id, [])]

    # -- knowledge hub browse ----------------------------------------------
    async def get_knowledge_hub_root_nodes(
        self, user_key: str, org_id: str, user_app_ids: list[str], skip: int, limit: int,
        sort_field: str, sort_dir: str, *, only_containers: bool = False,
        origins: list[str] | None = None, node_types: list[str] | None = None,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        apps = [aid for aid in user_app_ids if aid in self.NODES]
        return {"nodes": [self._node_doc(aid) for aid in apps[skip:skip + limit]], "total": len(apps)}

    async def get_knowledge_hub_children(
        self, parent_id: str, parent_type: str, org_id: str, user_key: str, skip: int, limit: int,
        sort_field: str, sort_dir: str, *, only_containers: bool = False,
        record_group_ids: list[str] | None = None, transaction: str | None = None,
    ) -> dict[str, Any]:
        if not self._has_access(user_key):
            return {"nodes": [], "total": 0}
        child_ids = self.CHILDREN_OF.get(parent_id, [])
        total = len(child_ids)
        page = child_ids[skip:skip + limit]
        return {"nodes": [self._node_doc(cid) for cid in page], "total": total}

    async def get_knowledge_hub_node_info(
        self, node_id: str, folder_mime_types: list[str], transaction: str | None = None,
    ) -> dict[str, Any] | None:
        info = self.NODES.get(node_id)
        if not info:
            return None
        return {"id": node_id, "name": info["name"], "nodeType": info["nodeType"], "subType": info.get("subType")}

    async def get_knowledge_hub_parent_node(
        self, node_id: str, folder_mime_types: list[str], transaction: str | None = None,
    ) -> dict[str, Any] | None:
        parent_id = self.PARENT_OF.get(node_id)
        if not parent_id:
            return None
        return await self.get_knowledge_hub_node_info(parent_id, folder_mime_types)

    async def get_knowledge_hub_breadcrumbs(self, node_id: str, transaction: str | None = None) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        current = node_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            info = self.NODES.get(current)
            if not info:
                break
            chain.append({"id": current, "name": info["name"], "nodeType": info["nodeType"], "subType": info.get("subType")})
            current = self.PARENT_OF.get(current)
        chain.reverse()
        return chain


@pytest.fixture
def graph_provider() -> FakeGraphProvider:
    return FakeGraphProvider()


class _FakeLLM:
    """Duck-typed chat model: ``bind_tools`` is a no-op; ``ainvoke`` replays a
    canned sequence of ``AIMessage`` responses, one per hop."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def bind_tools(self, tools: list) -> "_FakeLLM":
        return self

    async def ainvoke(self, messages: list, config: dict | None = None) -> AIMessage:
        response = self._responses[self._call_count]
        self._call_count += 1
        return response


class TestLookupRecordE2E:
    """Lookup by URL and by issue key resolves for the permitted user;
    identical not-found for a user without access."""

    async def test_resolves_by_url_for_permitted_user(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)
        result = await tool.ainvoke({"identifiers": [TICKET_URL]})

        assert result["ok"] is True
        assert result["record_info"]["id"] == TICKET_ID
        assert result["navigation"]["node"]["id"] == TICKET_ID

    async def test_resolves_by_bare_issue_key_for_permitted_user(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)
        result = await tool.ainvoke({"identifiers": ["PA-1787"]})

        assert result["ok"] is True
        assert result["record_info"]["id"] == TICKET_ID

    async def test_resolves_by_bare_external_id_for_permitted_user(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)
        result = await tool.ainvoke({"identifiers": ["10001"]})

        assert result["ok"] is True
        assert result["record_info"]["id"] == TICKET_ID

    async def test_denied_user_gets_not_found_for_same_url(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=DENIED_USER_ID)
        result = await tool.ainvoke({"identifiers": [TICKET_URL]})

        assert result["ok"] is False
        assert result["error"] == NOT_FOUND_ERROR

    async def test_denied_and_missing_identifiers_are_byte_identical(self, graph_provider: FakeGraphProvider) -> None:
        """A record the caller can't see must be indistinguishable from one
        that doesn't exist at all — no existence leak."""
        tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=DENIED_USER_ID)

        denied_result = await tool.ainvoke({"identifiers": ["PA-1787"]})
        missing_result = await tool.ainvoke({"identifiers": ["PA-9999"]})

        assert denied_result["ok"] is False
        assert missing_result["ok"] is False
        assert denied_result["error"] == missing_result["error"] == NOT_FOUND_ERROR


class TestNavigateE2E:
    """navigate() walk: root -> app -> group -> ticket, with correct
    breadcrumbs and pagination."""

    async def test_walk_root_to_app_to_group_to_ticket(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_navigate_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)

        root = await tool.ainvoke({})
        assert root["ok"] is True
        assert "Jira" in root["content"][0]["text"]

        app_view = await tool.ainvoke({"node_id": APP_ID})
        assert app_view["ok"] is True
        assert "Payments Project" in app_view["content"][0]["text"]

        group_view = await tool.ainvoke({"node_id": GROUP_ID})
        assert group_view["ok"] is True
        group_text = group_view["content"][0]["text"]
        assert "PA-1787 Payment service outage" in group_text
        assert "Path: Jira" in group_text

        ticket_view = await tool.ainvoke({"node_id": TICKET_ID})
        assert ticket_view["ok"] is True
        ticket_text = ticket_view["content"][0]["text"]
        assert "Root cause identified" in ticket_text
        assert "stacktrace.log" in ticket_text
        assert "Agent Loop Implementation" in ticket_text  # Related, via LINKED_TO

    async def test_breadcrumbs_are_root_to_leaf_ordered(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_navigate_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)

        ticket_view = await tool.ainvoke({"node_id": TICKET_ID})
        names = [b["name"] for b in ticket_view["navigation"]["breadcrumbs"]]

        assert names == ["Jira", "Payments Project", "PA-1787 Payment service outage"]

    async def test_pagination_limit_one_has_next_then_false(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_navigate_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=ALLOWED_USER_ID)

        page1 = await tool.ainvoke({"node_id": TICKET_ID, "limit": 1, "page": 1})
        assert page1["ok"] is True
        assert page1["navigation"]["totalItems"] == 2
        text1 = page1["content"][0]["text"]
        assert "page 1/2" in text1
        assert 'More: navigate(node_id="' in text1  # hasNext True

        page2 = await tool.ainvoke({"node_id": TICKET_ID, "limit": 1, "page": 2})
        assert page2["ok"] is True
        text2 = page2["content"][0]["text"]
        assert "page 2/2" in text2
        assert 'More: navigate(node_id="' not in text2  # hasNext False

    async def test_denied_user_cannot_open_the_ticket(self, graph_provider: FakeGraphProvider) -> None:
        tool = create_navigate_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=DENIED_USER_ID)

        result = await tool.ainvoke({"node_id": TICKET_ID})

        assert result["ok"] is False
        assert "Payment service outage" not in str(result)


class TestChatStreamE2E:
    """A chat message containing a Jira URL drives lookup_record through the
    real SSE event pipeline (``execute_tool_calls``): tool_call carries a
    human-readable description, tool_success carries the ``navigation``
    payload, and the final answer streams through."""

    async def _run(self, graph_provider: FakeGraphProvider, user_id: str, responses: list[AIMessage]) -> list[dict[str, Any]]:
        lookup_tool = create_lookup_record_tool(graph_provider=graph_provider, org_id=ORG_ID, user_id=user_id)
        llm = _FakeLLM(responses)
        messages = [HumanMessage(content=f"Please check {TICKET_URL} for me.")]

        events: list[dict[str, Any]] = []
        async for event in execute_tool_calls(
            llm=llm,
            messages=messages,
            tools=[lookup_tool],
            tool_runtime_kwargs={},
            final_results=[],
            virtual_record_id_to_result={},
            blob_store=None,
            all_queries=["Jira ticket lookup"],
            retrieval_service=None,
            user_id=user_id,
            org_id=ORG_ID,
            context_length=100_000,
            mode="simple",
        ):
            events.append(event)
        return events

    async def test_lookup_triggered_with_navigation_payload_and_final_answer(self, graph_provider: FakeGraphProvider) -> None:
        tool_call_message = AIMessage(
            content="",
            tool_calls=[{"name": "lookup_record", "args": {"identifiers": [TICKET_URL]}, "id": "call_1"}],
        )
        final_message = AIMessage(content="Found the ticket, it's about a payment outage.")

        events = await self._run(graph_provider, ALLOWED_USER_ID, [tool_call_message, final_message])

        tool_calls = [e for e in events if e["event"] == "tool_call"]
        tool_successes = [e for e in events if e["event"] == "tool_success"]
        completes = [e for e in events if e["event"] == "complete"]

        assert len(tool_calls) == 1
        assert tool_calls[0]["data"]["tool_name"] == "lookup_record"
        assert "PA-1787" in tool_calls[0]["data"]["description"] or TICKET_URL in tool_calls[0]["data"]["description"]

        assert len(tool_successes) == 1
        navigation = tool_successes[0]["data"]["navigation"]
        assert navigation["node"]["id"] == TICKET_ID

        assert len(completes) == 1
        assert "Found the ticket" in completes[0]["data"]["answer"]

    async def test_lookup_permission_denied_emits_tool_error_not_leak(self, graph_provider: FakeGraphProvider) -> None:
        tool_call_message = AIMessage(
            content="",
            tool_calls=[{"name": "lookup_record", "args": {"identifiers": [TICKET_URL]}, "id": "call_1"}],
        )
        final_message = AIMessage(content="I could not find that record.")

        events = await self._run(graph_provider, DENIED_USER_ID, [tool_call_message, final_message])

        tool_errors = [e for e in events if e["event"] == "tool_error"]
        tool_successes = [e for e in events if e["event"] == "tool_success"]

        assert len(tool_successes) == 0
        assert len(tool_errors) == 1
        assert tool_errors[0]["data"]["error"] == NOT_FOUND_ERROR
