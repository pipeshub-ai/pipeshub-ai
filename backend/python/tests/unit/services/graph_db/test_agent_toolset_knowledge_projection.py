"""
Unit tests for the agent toolsets/knowledge graph projection shared by the
agent detail endpoint (``get_agent``) and the agent list endpoint
(``get_all_agents``).

Covers:
  Neo4j (behavioral, client mocked):
    - ``_get_documents_by_ids``: empty-input short-circuit, id→doc mapping, and
      resilience (a lookup failure returns {} instead of propagating).
    - ``_project_agents_toolsets_and_knowledge``: toolset grouping per agent,
      KB vs. app knowledge resolution, UNKNOWN fallback when a referenced doc is
      missing, every requested id present with empty defaults, and the batching
      guarantee (toolsets/knowledge queried once for the whole page, not per
      agent).
  Arango (string + post-processing, execute_query mocked):
    - ``get_all_agents`` builds AQL that enriches the returned ``page`` with
      toolsets/knowledge and keeps the shape consistent on the flat-list path,
      and the Python post-step parses ``filtersParsed`` for each agent.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def neo4j_provider():
    provider = Neo4jProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.client = AsyncMock()
    return provider


@pytest.fixture
def arango_provider():
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    return provider


# ---------------------------------------------------------------------------
# Neo4j — _get_documents_by_ids
# ---------------------------------------------------------------------------


class TestNeo4jGetDocumentsByIds:
    @pytest.mark.asyncio
    async def test_empty_ids_short_circuits_without_query(self, neo4j_provider):
        result = await neo4j_provider._get_documents_by_ids([], CollectionNames.APPS.value)
        assert result == {}
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_maps_documents_by_key(self, neo4j_provider):
        neo4j_provider.client.execute_query = AsyncMock(
            return_value=[
                {"n": {"id": "app1", "name": "Slack", "type": "SLACK"}},
                {"n": {"id": "app2", "name": "Jira", "type": "JIRA"}},
            ]
        )
        result = await neo4j_provider._get_documents_by_ids(
            ["app1", "app2"], CollectionNames.APPS.value
        )
        assert set(result.keys()) == {"app1", "app2"}
        assert result["app1"]["name"] == "Slack"
        assert result["app1"]["_key"] == "app1"  # id → _key conversion

    @pytest.mark.asyncio
    async def test_resilient_on_query_error(self, neo4j_provider):
        # A transient DB failure must degrade to {} (callers fall back to UNKNOWN),
        # never propagate and abort the whole projection.
        neo4j_provider.client.execute_query = AsyncMock(side_effect=RuntimeError("db down"))
        result = await neo4j_provider._get_documents_by_ids(
            ["x"], CollectionNames.RECORD_GROUPS.value
        )
        assert result == {}
        neo4j_provider.logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Neo4j — _project_agents_toolsets_and_knowledge
# ---------------------------------------------------------------------------


def _make_projection_client(toolset_rows, knowledge_rows, doc_store):
    """Build an execute_query side effect that routes by query/params.

    - ``agent_ids`` param + 'AS toolset' in query → toolset rows
    - ``agent_ids`` param otherwise               → knowledge rows
    - ``ids`` param                               → docs from doc_store
    Records call counts so tests can assert batching.
    """
    calls = {"toolsets": 0, "knowledge": 0, "docs": 0}

    async def _execute(query, parameters=None, txn_id=None):
        parameters = parameters or {}
        if "agent_ids" in parameters:
            if "AS toolset" in query:
                calls["toolsets"] += 1
                return toolset_rows
            calls["knowledge"] += 1
            return knowledge_rows
        if "ids" in parameters:
            calls["docs"] += 1
            return [
                {"n": doc_store[i]} for i in parameters["ids"] if i in doc_store
            ]
        return []

    return AsyncMock(side_effect=_execute), calls


class TestNeo4jProjectAgents:
    @pytest.mark.asyncio
    async def test_empty_ids_returns_empty_map(self, neo4j_provider):
        assert await neo4j_provider._project_agents_toolsets_and_knowledge([]) == {}
        neo4j_provider.client.execute_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_groups_and_resolves_with_fixed_query_count(self, neo4j_provider):
        toolset_rows = [
            {"agent_id": "a1", "toolset": {"_key": "ts1", "name": "jira", "tools": []}},
            {"agent_id": "a2", "toolset": {"_key": "ts2", "name": "slack", "tools": []}},
        ]
        knowledge_rows = [
            # KB-backed knowledge (recordGroups → resolve to a KB group doc)
            {"agent_id": "a1", "_key": "k1", "connectorId": "kbconn",
             "filters": json.dumps({"recordGroups": ["kb1"]})},
            # App-backed knowledge (no recordGroups → resolve via Apps doc)
            {"agent_id": "a2", "_key": "k2", "connectorId": "app1",
             "filters": json.dumps({})},
        ]
        doc_store = {
            "kb1": {"id": "kb1", "groupType": Connectors.KNOWLEDGE_BASE.value,
                    "groupName": "My KB", "connectorId": "kbconn"},
            "app1": {"id": "app1", "name": "Slack", "type": "SLACK"},
        }
        client, calls = _make_projection_client(toolset_rows, knowledge_rows, doc_store)
        neo4j_provider.client.execute_query = client

        # a3 is requested but has neither toolsets nor knowledge.
        result = await neo4j_provider._project_agents_toolsets_and_knowledge(["a1", "a2", "a3"])

        # Every requested id present with the consistent default shape.
        assert set(result.keys()) == {"a1", "a2", "a3"}
        assert result["a3"] == {"toolsets": [], "knowledge": []}

        # Toolsets grouped to the right agent.
        assert result["a1"]["toolsets"] == [{"_key": "ts1", "name": "jira", "tools": []}]
        assert result["a2"]["toolsets"] == [{"_key": "ts2", "name": "slack", "tools": []}]

        # KB knowledge resolved.
        k1 = result["a1"]["knowledge"][0]
        assert k1["type"] == "KB"
        assert k1["name"] == "My KB"
        assert k1["displayName"] == "My KB"
        assert k1["connectorId"] == "kbconn"
        assert k1["filtersParsed"] == {"recordGroups": ["kb1"]}

        # App knowledge resolved.
        k2 = result["a2"]["knowledge"][0]
        assert k2["type"] == "SLACK"
        assert k2["name"] == "Slack"
        assert k2["filtersParsed"] == {}

        # Batching: toolsets + knowledge each queried exactly once for the whole
        # page regardless of agent count (no per-agent N+1).
        assert calls["toolsets"] == 1
        assert calls["knowledge"] == 1
        # KB + app doc lookups are batched into at most one query per collection.
        assert calls["docs"] <= 2

    @pytest.mark.asyncio
    async def test_missing_doc_falls_back_to_unknown(self, neo4j_provider):
        knowledge_rows = [
            {"agent_id": "a1", "_key": "k1", "connectorId": "ghost",
             "filters": json.dumps({})},
        ]
        client, _calls = _make_projection_client([], knowledge_rows, doc_store={})
        neo4j_provider.client.execute_query = client

        result = await neo4j_provider._project_agents_toolsets_and_knowledge(["a1"])
        k1 = result["a1"]["knowledge"][0]
        assert k1["type"] == "UNKNOWN"
        assert k1["name"] == "ghost"
        assert k1["displayName"] == "ghost"

    @pytest.mark.asyncio
    async def test_null_filters_yields_empty_parsed_object(self, neo4j_provider):
        # Graph node stored filters: null → filtersParsed must be {} (an object),
        # never None, to satisfy the OpenAPI schema.
        knowledge_rows = [
            {"agent_id": "a1", "_key": "k1", "connectorId": "ghost", "filters": None},
        ]
        client, _calls = _make_projection_client([], knowledge_rows, doc_store={})
        neo4j_provider.client.execute_query = client

        result = await neo4j_provider._project_agents_toolsets_and_knowledge(["a1"])
        assert result["a1"]["knowledge"][0]["filtersParsed"] == {}


# ---------------------------------------------------------------------------
# Arango — get_all_agents AQL enrichment + filtersParsed post-processing
# ---------------------------------------------------------------------------


class TestArangoGetAllAgentsEnrichment:
    @pytest.mark.asyncio
    async def test_aql_enriches_page_and_parses_filters(self, arango_provider):
        captured = {}

        async def capture(query, bind_vars=None, transaction=None):
            captured["query"] = query
            captured["bind_vars"] = bind_vars
            return [{
                "agents": [{
                    "_key": "a1",
                    "toolsets": [],
                    "knowledge": [{
                        "_key": "k1",
                        "connectorId": "c1",
                        "filters": json.dumps({"recordGroups": ["kb1"]}),
                    }],
                }],
                "totalItems": 1,
            }]

        arango_provider.execute_query = AsyncMock(side_effect=capture)

        out = await arango_provider.get_all_agents("u1", "o1", page=1, limit=20)

        q = captured["query"]
        # Enrichment is scoped to the returned page and merged onto each agent.
        assert "LET page = @do_page ? SLICE(sorted, @offset, @limit) : sorted" in q
        assert "LET enriched = (" in q
        assert "FOR a IN page" in q
        assert CollectionNames.AGENT_HAS_TOOLSET.value in q
        assert CollectionNames.AGENT_HAS_KNOWLEDGE.value in q
        assert "toolsets: linked_toolsets" in q
        assert "knowledge: linked_knowledge" in q
        assert "agents: enriched" in q
        # kb_type bind var is supplied for the KB-vs-app branch.
        assert captured["bind_vars"]["kb_type"] == Connectors.KNOWLEDGE_BASE.value

        # Python post-step parses the JSON filters into filtersParsed.
        agent = out["agents"][0]
        assert agent["knowledge"][0]["filtersParsed"] == {"recordGroups": ["kb1"]}

    @pytest.mark.asyncio
    async def test_invalid_filters_json_yields_empty_parsed(self, arango_provider):
        async def capture(query, bind_vars=None, transaction=None):
            return [{
                "agents": [{
                    "_key": "a1",
                    "toolsets": [],
                    "knowledge": [{"_key": "k1", "connectorId": "c1", "filters": "{not-json"}],
                }],
                "totalItems": 1,
            }]

        arango_provider.execute_query = AsyncMock(side_effect=capture)
        out = await arango_provider.get_all_agents("u1", "o1", page=1, limit=20)
        assert out["agents"][0]["knowledge"][0]["filtersParsed"] == {}

    @pytest.mark.asyncio
    async def test_null_filters_yields_empty_parsed_object(self, arango_provider):
        # filters: null on the graph edge must parse to {} (object), not None.
        async def capture(query, bind_vars=None, transaction=None):
            return [{
                "agents": [{
                    "_key": "a1",
                    "toolsets": [],
                    "knowledge": [{"_key": "k1", "connectorId": "c1", "filters": None}],
                }],
                "totalItems": 1,
            }]

        arango_provider.execute_query = AsyncMock(side_effect=capture)
        out = await arango_provider.get_all_agents("u1", "o1", page=1, limit=20)
        assert out["agents"][0]["knowledge"][0]["filtersParsed"] == {}
