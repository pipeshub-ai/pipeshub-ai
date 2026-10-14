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
        provider.execute_query = AsyncMock(return_value=[{"_key": "own-1"}, {"_key": "jira-1"}])

        apps = await provider.get_user_apps(CREATOR_KEY)

        assert [a["_key"] for a in apps] == ["own-1", "jira-1"]
        provider.execute_query.assert_awaited_once()
        query = provider.execute_query.await_args.args[0]
        assert "FOR linked IN authenticatedAs" in query
        assert "FILTER linked._from == @user_from" in query
        assert 'DOCUMENT("apps", linked.connectorId)' in query

    async def test_neo4j_lists_linked_connectors_in_the_same_query(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[{"app_id": "own-1"}, {"app_id": "jira-1"}])

        assert await provider.get_user_app_ids(CREATOR_KEY) == ["own-1", "jira-1"]

        provider.client.execute_query.assert_awaited_once()
        query = provider.client.execute_query.await_args.args[0]
        assert "OPTIONAL MATCH (u)-[linked:AUTHENTICATED_AS]->(:User)" in query
        assert "OPTIONAL MATCH (app3:App {id: linked.connectorId})" in query

    async def test_neo4j_app_documents_come_from_the_same_query(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        assert await provider.get_user_apps(CREATOR_KEY) == []

        provider.client.execute_query.assert_awaited_once()
        assert "collect(DISTINCT app3)" in provider.client.execute_query.await_args.args[0]


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
        assert query.count("FOR principal_id IN principal_ids") == 7
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


class TestRecordAccessCountsTheLinkedAccount:
    async def test_arango_access_query_iterates_the_principals(self) -> None:
        provider = _arango()
        provider.get_document = AsyncMock(return_value={"_key": "rec-1", "connectorId": "jira-1"})
        provider.http_client.execute_aql = AsyncMock(return_value=[None])

        assert await provider.check_record_access_with_details(CREATOR_ID, ORG, "rec-1") is None

        queries = [c.args[0] for c in provider.http_client.execute_aql.await_args_list]
        access_queries = [q for q in queries if "LET allAccess" in q]
        assert len(access_queries) == 1
        query = access_queries[0]
        assert "FILTER linked.connectorId == recordDoc.connectorId" in query
        assert "FOR userDoc IN principals" in query
        # both accounts' access paths are merged, so the highest role wins whichever holds it
        assert "LET mergedAccess = FLATTEN(accessByPrincipal)" in query
        assert query.count("user.userId == @userId") == 1

    async def test_neo4j_access_query_iterates_the_principals(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(side_effect=[
            [{"u": {"id": CREATOR_KEY, "userId": CREATOR_ID}}],
            [],
        ])
        provider._get_user_app_ids = AsyncMock(return_value=["jira-1"])
        provider.get_document = AsyncMock(return_value={"id": "rec-1", "connectorId": "jira-1"})

        assert await provider.check_record_access_with_details(CREATOR_ID, ORG, "rec-1") is None

        assert provider.client.execute_query.await_count == 2
        access_query = provider.client.execute_query.await_args_list[-1].args[0]
        assert "WHERE linked.connectorId = rec.connectorId" in access_query
        assert "UNWIND [caller] + source_accounts AS u" in access_query


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
            assert query.count("OPTIONAL MATCH (principal)-") == 5, name
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

    async def test_node_access(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        assert await provider.get_knowledge_hub_node_access("rec-1", CREATOR_KEY, ORG, []) is None

        provider.client.execute_query.assert_awaited_once()

    async def test_reindex_listings(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_records_by_record_group("rg-1", "jira-1", ORG, 100, CREATOR_KEY)
        await provider.get_records_by_parent_record("rec-1", "jira-1", ORG, 0, CREATOR_KEY)

        keys = [c.kwargs["parameters"]["user_key"] for c in provider.client.execute_query.await_args_list]
        assert keys == [CREATOR_KEY, CREATOR_KEY]


class TestNeo4jLinkedRecords:
    async def test_the_link_counts_only_for_the_linked_records_own_connector(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider.get_linked_records("rec-1", ORG, CREATOR_KEY, ["LINKED_TO"])

        provider.client.execute_query.assert_awaited_once()
        query = provider.client.execute_query.await_args.args[0]
        assert "WHERE linked.connectorId = v.connectorId" in query
        assert "EXISTS { (u)-" not in query


# ---------------------------------------------------------------------------
# Knowledge Hub search — principals inside the permission-first traversal
# ---------------------------------------------------------------------------


class TestSearchCountsTheLinkedAccount:
    def test_neo4j_paths_run_per_principal_scoped_to_the_links_connector(self) -> None:
        query = _neo4j()._build_permission_paths_cypher("", "")

        assert "OPTIONAL MATCH (u)-[linked:AUTHENTICATED_AS]->(source_account:User)" in query
        # four record-group paths and three direct record paths
        assert query.count("UNWIND principals AS principal") == 7
        assert query.count("linked_connector IS NULL OR rg.connectorId = linked_connector") == 4
        assert query.count("linked_connector IS NULL OR record.connectorId = linked_connector") == 3

    def test_neo4j_kb_app_paths_stay_the_callers_own(self) -> None:
        query = _neo4j()._build_permission_paths_cypher("", "")
        kb_paths = [block for block in query.split("CALL {") if "MATCH (u)-" in block and "kb_app" in block]

        assert len(kb_paths) == 2
        assert all("UNWIND principals" not in block.split("RETURN")[0] for block in kb_paths)

    def test_arango_paths_run_per_principal_scoped_to_the_links_connector(self) -> None:
        query = _arango()._build_knowledge_hub_permission_expansion_aql(
            "", "", "true", "true", "", rg_seed_prefilter="", record_prefilter="", inherited_document_prefilter="",
        )

        assert query.count("FOR principal IN principals") == 7
        assert query.count("principal.connectorId == null OR rg.connectorId == principal.connectorId") == 4
        assert query.count("principal.connectorId == null OR record.connectorId == principal.connectorId") == 3
        # only the two KB app seeds still start from the caller alone
        assert query.count("user_from") == 2


# ---------------------------------------------------------------------------
# Reindex permission checkers — hand-written queries, best principal wins
# ---------------------------------------------------------------------------


class TestReindexPermissionChecks:
    async def test_arango_record_check_iterates_the_principals_in_one_query(self) -> None:
        provider = _arango()
        provider.execute_query = AsyncMock(return_value=[{"permission": "READER", "source": "DIRECT"}])

        result = await provider._check_record_permissions("rec-1", CREATOR_KEY)

        assert result["permission"] == "READER"
        provider.execute_query.assert_awaited_once()
        query = provider.execute_query.await_args.args[0]
        assert "linked.connectorId == DOCUMENT(record_from).connectorId" in query
        assert "FOR user_from IN principal_ids" in query
        # the strongest principal wins, not just any principal with access
        assert '"OWNER": 6' in query and "[final_permission] DESC" in query
        assert provider.execute_query.await_args.kwargs["bind_vars"]["user_from"] == f"users/{CREATOR_KEY}"

    async def test_arango_record_group_check_iterates_the_principals_in_one_query(self) -> None:
        provider = _arango()
        provider.execute_query = AsyncMock(return_value=[{"allowed": True, "role": "READER"}])

        result = await provider._check_record_group_permissions("rg-1", CREATOR_KEY, ORG)

        assert result["allowed"] is True
        provider.execute_query.assert_awaited_once()
        query = provider.execute_query.await_args.args[0]
        assert "linked.connectorId == recordGroup.connectorId" in query
        assert "FOR userDoc IN principals" in query

    async def test_neo4j_checks_iterate_the_principals_in_one_query(self) -> None:
        provider = _neo4j()
        provider.client.execute_query = AsyncMock(return_value=[])

        await provider._check_record_permissions("rec-1", CREATOR_KEY)
        await provider._check_record_group_permissions("rg-1", CREATOR_KEY, ORG)

        record_query, group_query = (c.args[0] for c in provider.client.execute_query.await_args_list)
        assert "WHERE linked.connectorId = record.connectorId" in record_query
        assert "UNWIND [caller] + source_accounts AS user" in record_query
        assert "ORDER BY CASE permission WHEN 'OWNER' THEN 6" in record_query
        assert "WHERE linked.connectorId = recordGroup.connectorId" in group_query
        assert "ORDER BY result.allowed DESC, CASE result.role WHEN 'OWNER' THEN 6" in group_query

    async def test_a_denied_result_stays_denied(self) -> None:
        provider = _arango()
        provider.execute_query = AsyncMock(return_value=[{"permission": None, "source": "NONE"}])

        result = await provider._check_record_permissions("rec-1", CREATOR_KEY)

        assert result == {"permission": None, "source": "NONE"}
        provider.execute_query.assert_awaited_once()
