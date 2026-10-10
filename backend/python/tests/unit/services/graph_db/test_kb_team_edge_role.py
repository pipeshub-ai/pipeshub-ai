"""KB role derived from the team->KB share edge, with a capped fallback for legacy edges.

Query-shape checks run against the generated Cypher/AQL text; behaviour against
real Neo4j/Arango lives in tests/integration/graph_db/test_kb_team_role_real_backends.py.
"""

import inspect
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango import arango_http_provider
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.kb_team_role import (
    aql_team_kb_role,
    cypher_team_kb_role,
)
from app.services.graph_db.neo4j import neo4j_provider as neo4j_provider_module
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def neo4j_provider() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[{"role": "READER"}])
    return provider


@pytest.fixture
def arango_provider() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=["READER"])
    return provider


class TestSharedHelper:
    def test_cypher_prefers_edge_role_then_capped_member_role(self) -> None:
        expr = cypher_team_kb_role("e", "m")
        assert expr == (
            "CASE WHEN e.role IS NOT NULL AND e.role <> '' THEN e.role "
            "WHEN m.role = 'OWNER' THEN 'WRITER' ELSE m.role END"
        )

    def test_aql_prefers_edge_role_then_capped_member_role(self) -> None:
        expr = aql_team_kb_role("e", "m")
        assert expr == '((e.role != null AND e.role != "") ? e.role : (m.role == "OWNER" ? "WRITER" : m.role))'

    @pytest.mark.parametrize("module", [neo4j_provider_module, arango_http_provider])
    def test_providers_do_not_reimplement_the_cap(self, module) -> None:
        source = inspect.getsource(module)
        assert "role = 'OWNER' THEN 'WRITER'" not in source
        assert 'role == "OWNER" ? "WRITER"' not in source


class TestEffectiveRoleQueries:
    @pytest.mark.asyncio
    async def test_neo4j_get_user_kb_permission(self, neo4j_provider) -> None:
        role = await neo4j_provider.get_user_kb_permission("kb1", "u1")

        assert role == "READER"
        assert cypher_team_kb_role("tb", "ut") in neo4j_provider.client.execute_query.call_args.args[0]

    @pytest.mark.asyncio
    async def test_arango_get_user_kb_permission(self, arango_provider) -> None:
        role = await arango_provider.get_user_kb_permission("kb1", "u1")

        assert role == "READER"
        assert aql_team_kb_role("kb_team_perm", "team_info") in arango_provider.http_client.execute_aql.call_args.args[0]

    @pytest.mark.asyncio
    async def test_neo4j_list_user_knowledge_bases(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])
        await neo4j_provider.list_user_knowledge_bases("u1", "org1", 0, 10)

        queries = [c.args[0] for c in neo4j_provider.client.execute_query.call_args_list]
        assert len(queries) >= 3
        expr = cypher_team_kb_role("r2", "r1")
        for q in queries:
            assert expr in q

    @pytest.mark.asyncio
    async def test_arango_list_user_knowledge_bases(self, arango_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])
        await arango_provider.list_user_knowledge_bases("u1", "org1", 0, 10)

        queries = [c.args[0] for c in arango_provider.http_client.execute_aql.call_args_list]
        assert len(queries) >= 3
        expr = aql_team_kb_role("kb_team_perm", "team_info")
        for q in queries:
            assert expr in q

    def test_app_permission_snippets(self, neo4j_provider, arango_provider) -> None:
        assert cypher_team_kb_role("tb", "ut") in neo4j_provider._get_app_permission_role_cypher("app", "u", "{}")
        assert aql_team_kb_role("team_app_perm", "user_team_perm") in arango_provider._get_app_permission_role_aql(
            "app", "u", "{}"
        )

    def test_neo4j_record_path7_uses_edge_role_for_kb_app_only(self, neo4j_provider) -> None:
        q = neo4j_provider._get_record_permission_role_cypher("record", "u", "{}")
        kb_branch = (
            "CASE WHEN target:App AND target.type = 'KB' THEN "
            + cypher_team_kb_role("p7", "ut")
            + " ELSE ut.role END"
        )
        assert kb_branch in q
        assert "collect(DISTINCT ut.role)" not in q

    def test_arango_record_path7_uses_edge_role_for_kb_app_only(self, arango_provider) -> None:
        q = arango_provider._get_record_permission_role_aql("record", "u", "{}")
        assert 'STARTS_WITH(target_id, "apps/") AND DOCUMENT(target_id).type == "KB"' in q
        assert aql_team_kb_role("team_target_perm", "user_team_perm") in q
        assert ": user_team_perm.role" in q

    @pytest.mark.asyncio
    async def test_neo4j_record_access_details_kb_team_branch(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(
            side_effect=[[{"u": {"id": "u1", "userId": "ext1"}}], []]
        )
        neo4j_provider._get_user_app_ids = AsyncMock(return_value=[])
        neo4j_provider.get_document = AsyncMock(return_value={"id": "r1"})

        await neo4j_provider.check_record_access_with_details("ext1", "org1", "r1")

        access_query = neo4j_provider.client.execute_query.call_args_list[1].args[0]
        assert 'role: ' + cypher_team_kb_role("teamKbPerm", "userTeamPerm") in access_query
        assert "role: userTeamPerm.role" not in access_query

    @pytest.mark.asyncio
    async def test_arango_record_access_details_kb_team_branch(self, arango_provider) -> None:
        arango_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
        arango_provider._get_user_app_ids = AsyncMock(return_value=[])
        arango_provider.get_document = AsyncMock(return_value={"_key": "r1"})
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])

        await arango_provider.check_record_access_with_details("ext1", "org1", "r1")

        access_query = arango_provider.http_client.execute_aql.call_args_list[0].args[0]
        assert aql_team_kb_role("kb_team_perm", "user_team_perm") in access_query
        assert "role: user_team_perm.role" not in access_query

    @pytest.mark.asyncio
    async def test_neo4j_list_all_records_team_branches(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(return_value=[])

        await neo4j_provider.list_all_records("u1", "org1", 0, 10)

        queries = [c.args[0] for c in neo4j_provider.client.execute_query.call_args_list]
        expr = cypher_team_kb_role("teamKbPerm", "userTeamPerm")
        with_role = [q for q in queries if "COLLECT({kb: kb2, role:" in q]
        assert len(with_role) >= 2
        for q in with_role:
            assert expr in q
            assert "role: userTeamPerm.role" not in q

    @pytest.mark.asyncio
    async def test_arango_list_all_records_team_branch(self, arango_provider) -> None:
        arango_provider.execute_query = AsyncMock(return_value=[])
        arango_provider.http_client.execute_aql = AsyncMock(return_value=[])

        await arango_provider.list_all_records(
            "u1", "org1", 0, 10, None, None, None, None, None, None, None, None,
            "createdAtTimestamp", "desc", "all",
        )

        queries = [c.args[0] for c in arango_provider.execute_query.call_args_list]
        main = next(q for q in queries if "LET teamKbAccess" in q and "kb_doc: kb, role:" in q)
        assert aql_team_kb_role("teamKbPerm", "user_team_perm") in main
        assert "role: user_team_perm }" not in main


class TestBackfill:
    @pytest.mark.asyncio
    async def test_neo4j_backfill_stamps_only_uniform_reader_of_active_members(self, neo4j_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"stamped": 3}], [{"remaining": 1}]])

        assert await neo4j_provider.backfill_kb_team_edge_roles() == {"stamped": 3, "remaining_role_less": 1}

        call = neo4j_provider.client.execute_query.call_args_list[0]
        assert "roles = [$stamp_role]" in call.args[0]
        assert "m.isActive = true" in call.args[0]
        assert call.args[1] == {"stamp_role": "READER"}

    @pytest.mark.asyncio
    async def test_arango_backfill_stamps_only_uniform_reader_of_active_members(self, arango_provider) -> None:
        arango_provider.http_client.execute_aql = AsyncMock(side_effect=[[1, 1], [2]])

        assert await arango_provider.backfill_kb_team_edge_roles() == {"stamped": 2, "remaining_role_less": 2}

        call = arango_provider.http_client.execute_aql.call_args_list[0]
        assert "FOR t IN @@teams_collection" in call.args[0]
        assert "roles[0] == @stamp_role" in call.args[0]
        assert "member.isActive == true" in call.args[0]
        assert call.kwargs["bind_vars"]["stamp_role"] == "READER"

    @pytest.mark.asyncio
    async def test_remaining_count_failure_does_not_fail_backfill(self, neo4j_provider, arango_provider) -> None:
        neo4j_provider.client.execute_query = AsyncMock(side_effect=[[{"stamped": 1}], RuntimeError("boom")])
        assert await neo4j_provider.backfill_kb_team_edge_roles() == {"stamped": 1, "remaining_role_less": -1}

        arango_provider.http_client.execute_aql = AsyncMock(side_effect=[[1], RuntimeError("boom")])
        assert await arango_provider.backfill_kb_team_edge_roles() == {"stamped": 1, "remaining_role_less": -1}
