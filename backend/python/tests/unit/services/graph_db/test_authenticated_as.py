"""authenticatedAs links inside the permission queries.

A connector creator linked to the source account the connector is authenticated as must
hold both accounts' permissions for that connector only. Every permission query counts the
linked account as a second principal, reached through the link edge and scoped to the
link's connector; nothing runs a second time as another user, and nothing looks the
source account up by its userId.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG = "org-1"
CREATOR_ID = "creator-user-id"
CREATOR_KEY = "creator-key"


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"_key": CREATOR_KEY, "userId": CREATOR_ID})
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock())
    provider.client = AsyncMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": CREATOR_KEY, "userId": CREATOR_ID})
    return provider


# ---------------------------------------------------------------------------
# App lists — linked connectors are listed by the query itself
# ---------------------------------------------------------------------------


class TestAppListsIncludeLinkedConnectors:

    async def test_arango_lists_linked_connectors_in_the_same_query(self) -> None:
        provider = _arango()
        provider.execute_query = AsyncMock(return_value=["own-1", "jira-1"])

        assert await provider.get_user_app_ids(CREATOR_KEY) == ["own-1", "jira-1"]

        provider.execute_query.assert_awaited_once()
        query = provider.execute_query.await_args.args[0]
        assert "FOR link IN authenticatedAs" in query
        assert "FILTER link._from == @user_from" in query
        assert 'DOCUMENT("apps", link.connectorId)' in query
        assert "UNION_DISTINCT(direct, via_team, linked)" in query

    async def test_neo4j_lists_linked_connectors_in_the_same_query(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[{"app_id": "own-1"}, {"app_id": "jira-1"}])

        assert await provider.get_user_app_ids(CREATOR_KEY) == ["own-1", "jira-1"]

        provider.client.execute_query.assert_awaited_once()
        query = provider.client.execute_query.await_args.args[0]
        assert "OPTIONAL MATCH (u)-[linked:AUTHENTICATED_AS]->(:User)" in query
        assert "OPTIONAL MATCH (app3:App {id: linked.connectorId})" in query



# ---------------------------------------------------------------------------
# Chat retrieval — one query per connector, linked account reached through the edge
# ---------------------------------------------------------------------------


class TestRetrievalCountsTheLinkedAccount:
    async def test_arango_query_iterates_the_principals_of_that_connector(self) -> None:
        provider = _arango()
        provider.execute_query = AsyncMock(return_value=[{"virtualRecordId": "v1", "recordId": "r1"}])

        result = await provider._get_virtual_ids_for_connector(CREATOR_ID, ORG, "jira-1", None)

        assert result == {"v1": "r1"}
        provider.execute_query.assert_awaited_once()
        query = provider.execute_query.await_args.args[0]
        assert "linked._from == userDoc._id AND linked.connectorId == @connectorId" in query
        assert query.count("FOR principal_id IN principal_ids") == 4
        assert " ANY userDoc._id " not in query
        # the only userId lookup is the caller's own
        assert query.count("user.userId == @userId") == 1
        assert provider.execute_query.await_args.kwargs["bind_vars"]["userId"] == CREATOR_ID

    async def test_neo4j_query_iterates_the_principals_of_that_connector(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[{"virtualId": "v1", "recordId": "r1"}])

        result = await provider._get_virtual_ids_for_connector(CREATOR_ID, ORG, "jira-1", None)

        assert result == {"v1": "r1"}
        provider.client.execute_query.assert_awaited_once()
        query = provider.client.execute_query.await_args.args[0]
        assert "(caller)-[:AUTHENTICATED_AS {connectorId: $connectorId}]->(source_account:User)" in query
        assert "UNWIND [caller] + source_accounts AS userDoc" in query
        assert query.count("{userId: $userId}") == 1

    async def test_the_bulk_gate_runs_one_task_per_connector(self) -> None:
        provider = _arango()
        provider._get_user_app_ids = AsyncMock(return_value=["own-1", "jira-1"])
        provider.http_client.execute_aql = AsyncMock(return_value=[])
        provider._get_virtual_ids_for_connector = AsyncMock(return_value={})
        provider._get_kb_virtual_ids = AsyncMock(return_value={})

        await provider.get_accessible_virtual_record_ids(CREATOR_ID, ORG)

        runs = [(c.args[0], c.args[2]) for c in provider._get_virtual_ids_for_connector.await_args_list]
        assert sorted(runs) == [(CREATOR_ID, "jira-1"), (CREATOR_ID, "own-1")]


# ---------------------------------------------------------------------------
# Opening one record — one access query, first principal with access wins
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Permission-role builders — Knowledge Hub, reindex listings, location filter
# ---------------------------------------------------------------------------


def _role_queries() -> dict[str, str]:
    arango, neo4j = _arango(), _neo4j()
    return {
        "arango record": arango._get_permission_role_aql("record", "record", "u"),
        "arango recordGroup": arango._get_permission_role_aql("recordGroup", "rg", "u"),
        "neo4j record": neo4j._get_permission_role_cypher("record", "record", "u"),
        "neo4j recordGroup": neo4j._get_permission_role_cypher("recordGroup", "rg", "u"),
    }


class TestRoleBuildersCountTheLinkedAccount:
    def test_the_link_is_read_for_the_nodes_own_connector_only(self) -> None:
        queries = _role_queries()
        assert "linked.connectorId == record.connectorId" in queries["arango record"]
        assert "linked.connectorId == rg.connectorId" in queries["arango recordGroup"]
        assert "WHERE linked.connectorId = record.connectorId" in queries["neo4j record"]
        assert "WHERE linked.connectorId = rg.connectorId" in queries["neo4j recordGroup"]

    def test_every_arango_permission_path_checks_all_principals(self) -> None:
        for name in ("arango record", "arango recordGroup"):
            query = _role_queries()[name]
            assert query.count("._from IN principal_ids") == 5, name
            # the only single-user anchor left is the link lookup itself
            assert query.count("._from == u._id") == 1, name

    def test_every_neo4j_permission_path_checks_all_principals(self) -> None:
        for name in ("neo4j record", "neo4j recordGroup"):
            query = _role_queries()[name]
            # Each of the five paths runs once per principal: its subquery takes the
            # principal in, and reads it directly (user, org) or probes its membership.
            assert query.count("WITH principal, target") == 5, name
            assert query.count("MATCH (principal)-") == 3, name
            assert query.count("EXISTS { (principal)-[:PERMISSION {type: 'USER'}]->(") == 2, name
            assert "UNWIND principals AS principal" in query, name
            assert query.count("OPTIONAL MATCH (u)-") == 1, name
            assert "[u] + collect(DISTINCT source_account) AS principals" in query

    def test_a_linked_connector_counts_as_the_users_own_app(self) -> None:
        """Without this the creator lists the connector but cannot open its app node."""
        neo4j_query = _neo4j()._get_permission_role_cypher("app", "app", "u")
        assert "(u)-[linked_app:AUTHENTICATED_AS {connectorId: app.id}]->(:User)" in neo4j_query
        assert "coalesce(own_app_rel, linked_app) AS user_app_rel" in neo4j_query

        arango_query = _arango()._get_permission_role_aql("app", "app", "u")
        assert "linked._from == u._id AND linked.connectorId == app._key" in arango_query
        assert "LET user_app_rel = own_app_rel != null ? own_app_rel : linked_app_rel" in arango_query


class TestBuilderBackedMethodsRunOnceAsTheCaller:
    async def test_context_permissions(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(return_value=[{"role": "WRITER", "canEdit": True}])

        result = await provider.get_knowledge_hub_context_permissions(CREATOR_KEY, ORG, "rg-1", None, "recordGroup")

        assert result["role"] == "WRITER"
        provider.http_client.execute_aql.assert_awaited_once()
        assert provider.http_client.execute_aql.await_args.kwargs["bind_vars"]["user_key"] == CREATOR_KEY


    async def test_reindex_listings(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_records_by_record_group("rg-1", "jira-1", ORG, 100, CREATOR_KEY)
        await provider.get_records_by_parent_record("rec-1", "jira-1", ORG, 0, CREATOR_KEY)

        keys = [c.kwargs["parameters"]["user_key"] for c in provider.client.execute_query.await_args_list]
        assert keys == [CREATOR_KEY, CREATOR_KEY]




# ---------------------------------------------------------------------------
# Knowledge Hub search — principals inside the permission-first traversal
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Reindex permission checkers — hand-written queries, best principal wins
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# New model — the link enters through the access context, which the listing,
# the batch access check and everything built on them read. Behaviour is covered
# in tests/integration/graph_permissions/test_shadow_user.py on both backends.
# ---------------------------------------------------------------------------


class TestAccessContextCountsTheLink:
    def test_the_gate_opens_the_linked_connector(self) -> None:
        cypher = Neo4jProvider._kh_gate_cypher()
        assert "OPTIONAL MATCH (u)-[kh_link:AUTHENTICATED_AS]->(:User)" in cypher
        assert "kh_linkedApp.orgId = $org_id" in cypher
        aql = ArangoHTTPProvider._kh_gate_aql()
        assert "FOR kh_link IN authenticatedAs" in aql
        assert "linked.orgId == @org_id" in aql

    async def test_neo4j_grants_of_the_linked_account_stay_in_its_connector(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])
        await provider.get_knowledge_hub_access_v3(CREATOR_KEY, ORG)
        query = provider.client.execute_query.await_args.args[0]
        assert "OPTIONAL MATCH (u)-[kh_link:AUTHENTICATED_AS]->(kh_src:User)" in query
        assert "kh_lg.connectorId = kh_link.connectorId" in query

    async def test_arango_grants_of_the_linked_account_stay_in_its_connector(self) -> None:
        provider = _arango()
        provider.http_client.execute_aql = AsyncMock(return_value=[])
        await provider.get_knowledge_hub_access_v3(CREATOR_KEY, ORG)
        query = provider.http_client.execute_aql.await_args.args[0]
        assert "LET linked_grants" in query
        assert "granted.connectorId == kh_link.connectorId" in query
