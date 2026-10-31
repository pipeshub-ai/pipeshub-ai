"""lookup_record in a chat over knowledge bases only.

The connector catalog never lists a knowledge base: the filter options it reads
leave KB apps out, so it is empty for a user whose sources are all KBs.
lookup_record answered "Not found or no access" before any strategy ran, though
a Record ID and a web URL need no catalog, and a KB file is known only by the
key or title in its name (``2019-04-02_HELP-3307-printer-jam.md``).

The tool runs over the real resolver and KnowledgeHubService; the graph is an
autospec of ArangoHTTPProvider whose knowledge hub listing is stubbed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, create_autospec

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import FileRecord, Record, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

from ..kh_listing_stub import connector_pages, node, stub_listing

USER_KEY = "user-key-1"
ORG_ID = "org-1"
KB_ID = "kb-support"


def _kb_file(record_id: str, name: str) -> FileRecord:
    return FileRecord(
        id=record_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=record_id.replace("-", "")[:24],
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id=KB_ID,
        weburl=f"https://pipeshub.example.com/record/{record_id}",
        indexing_status="COMPLETED",
        is_file=True,
        extension="md",
    )


HELP_3307 = _kb_file(
    "62636daf-bfeb-4052-8222-71e72993810e",
    "2019-04-02_HELP-3307-printer-jam.md",
)
HELP_33071 = _kb_file(
    "0b9e0f4e-5d47-4a3c-9a43-1f0f8f4e2a10",
    "2019-04-02_HELP-33071-toner-order.md",
)
OPS_2019_0420 = _kb_file(
    "5a7c2e90-3b1d-4f6e-8c2a-9d0e1f2a3b4c",
    "2019-04-02_OPS-2019-0420-badge-reader-offline.md",
)
FILES = [HELP_3307, HELP_33071, OPS_2019_0420]

JIRA_TICKET = Record(
    id="jira-1",
    record_name="PA-1787 Login fails after password reset",
    record_type=RecordType.TICKET,
    external_record_id="PA-1787",
    version=1,
    origin=OriginTypes.CONNECTOR,
    connector_name=Connectors.JIRA,
    connector_id="conn-jira",
    weburl="https://acme.atlassian.net/browse/PA-1787",
    indexing_status="COMPLETED",
)


def _graph(readable: set[str]) -> MagicMock:
    """A user whose only source is one knowledge base holding FILES, and who
    may read the records in ``readable``."""
    graph = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(graph, USER_KEY, {KB_ID: [node(f.id, f.record_name, origin="COLLECTION") for f in FILES]})
    by_id = {r.id: r for r in [*FILES, JIRA_TICKET]}

    def node_access(node_id: str, **_: object) -> dict[str, Any] | None:
        return {"id": node_id, "nodeType": "record"} if node_id in readable else None

    def issue_key(connector_id: str, key: str, *_: object, **__: object) -> Record | None:
        return JIRA_TICKET if (connector_id, key) == ("conn-jira", "PA-1787") else None

    # Both providers leave KB apps out of the filter options; the gate has them.
    graph.get_knowledge_hub_filter_options.return_value = {"apps": []}
    graph.get_gated_apps.return_value = [{"_key": KB_ID, "name": "Support", "type": "KB"}]
    graph.get_record_by_id.side_effect = lambda record_id, *_, **__: by_id.get(record_id)
    graph.get_record_by_weburl.side_effect = lambda url, *_, **__: next((f for f in FILES if f.weburl == url), None)
    graph.get_record_by_external_id.return_value = None
    graph.get_record_by_issue_key.side_effect = issue_key
    graph.get_knowledge_hub_node_access.side_effect = node_access
    return graph


def _kb_only_chat(graph: MagicMock) -> dict[str, Any]:
    # What the chat bridge prefetches for a user with no connector.
    return {
        "org_id": ORG_ID,
        "user_id": "user-1",
        "graph_provider": graph,
        "has_knowledge": True,
        "available_connectors": [],
    }


async def _lookup(state: dict[str, Any], *identifiers: str) -> tuple[bool, str]:
    return await KnowledgeGraph(state=state).lookup_record(identifiers=list(identifiers))


class TestKbOnlyChatResolves:
    async def test_a_record_id(self) -> None:
        graph = _graph(readable={HELP_3307.id})

        ok, text = await _lookup(_kb_only_chat(graph), HELP_3307.id)

        assert ok
        assert HELP_3307.record_name in text
        graph.get_record_by_id.assert_awaited_with(HELP_3307.id)

    async def test_a_web_url(self) -> None:
        graph = _graph(readable={HELP_3307.id})

        ok, text = await _lookup(_kb_only_chat(graph), HELP_3307.weburl)

        assert ok
        assert HELP_3307.record_name in text

    async def test_a_ticket_key_in_a_kb_file_name(self) -> None:
        graph = _graph(readable={f.id for f in FILES})

        ok, text = await _lookup(_kb_only_chat(graph), "HELP-3307")

        assert ok
        assert HELP_3307.record_name in text
        # The name search matches HELP-33071 too; only the whole key is a match.
        assert HELP_33071.record_name not in text

    async def test_an_external_id_in_a_kb_file_name(self) -> None:
        graph = _graph(readable={f.id for f in FILES})

        ok, text = await _lookup(_kb_only_chat(graph), "OPS-2019-0420")

        assert ok
        assert OPS_2019_0420.record_name in text

    async def test_a_title(self) -> None:
        graph = _graph(readable={f.id for f in FILES})

        ok, text = await _lookup(_kb_only_chat(graph), "HELP-3307-printer-jam")

        assert ok
        assert HELP_3307.record_name in text
        assert HELP_33071.record_name not in text


class TestKbOnlyChatAccess:
    async def test_a_record_the_user_cannot_read_stays_not_found(self) -> None:
        graph = _graph(readable=set())

        for identifier in (HELP_3307.id, HELP_3307.weburl, "HELP-3307", "HELP-3307-printer-jam"):
            ok, text = await _lookup(_kb_only_chat(graph), identifier)

            assert ok
            assert "Not found" in text
            assert HELP_3307.record_name not in text

    async def test_it_searches_the_session_users_knowledge_bases_as_that_user(self) -> None:
        graph = _graph(readable={HELP_3307.id})

        await _lookup(_kb_only_chat(graph), "HELP-3307")

        graph.get_gated_apps.assert_awaited_once_with(USER_KEY, ORG_ID)
        assert [page["app_id"] for page in connector_pages(graph)] == [KB_ID]
        for call in graph.get_knowledge_hub_node_access.await_args_list:
            assert (call.kwargs["user_key"], call.kwargs["org_id"]) == (USER_KEY, ORG_ID)


class TestConnectorCatalogUnchanged:
    @staticmethod
    def _chat_with_jira(graph: MagicMock) -> dict[str, Any]:
        return {
            **_kb_only_chat(graph),
            "available_connectors": [{"id": "conn-jira", "name": "Jira Cloud", "type": "JIRA"}],
        }

    async def test_a_connector_hit_resolves_as_before(self) -> None:
        graph = _graph(readable={JIRA_TICKET.id})

        ok, text = await _lookup(self._chat_with_jira(graph), "PA-1787")

        assert ok
        assert JIRA_TICKET.record_name in text
        graph.get_gated_apps.assert_not_awaited()
        assert connector_pages(graph) == []

    async def test_a_connector_miss_searches_no_knowledge_base(self) -> None:
        graph = _graph(readable={f.id for f in FILES})

        ok, text = await _lookup(self._chat_with_jira(graph), "PA-9999")

        assert ok
        assert "Not found (or no access): PA-9999  [searched: JIRA, all]" in text
        graph.get_gated_apps.assert_not_awaited()
        assert connector_pages(graph) == []


class TestKbAgent:
    async def test_a_ticket_key_in_its_kb_file_name(self) -> None:
        graph = _graph(readable={f.id for f in FILES})
        state = {
            "org_id": ORG_ID,
            "user_id": "user-1",
            "graph_provider": graph,
            "has_knowledge": True,
            "agent_knowledge": [{"connectorId": KB_ID, "type": "KB", "name": "Support"}],
        }

        ok, text = await _lookup(state, "HELP-3307")

        assert ok
        assert HELP_3307.record_name in text
        graph.get_gated_apps.assert_not_awaited()
