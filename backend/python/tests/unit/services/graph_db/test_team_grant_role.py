"""Every query that reads a role through a team reads it from the team's grant.

Both providers took a member's role inside the team as their role on what the team was
granted, so a team OWNER owned every knowledge base shared with the team. The behaviour
is proven on real Neo4j and ArangoDB in
tests/integration/graph_db/test_kb_role_through_team_real_backends.py; these tests pin
that each query path builds its team role from the one shared rule.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.team_grant_role import (
    TEAM_GRANT_ROLE_CAP,
    aql_team_grant_role,
    cypher_team_grant_role,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

CYPHER_RULE = cypher_team_grant_role("team_grant", "team_member")
AQL_RULE = aql_team_grant_role("team_grant", "team_member")
RPM = {"OWNER": 4, "WRITER": 3, "READER": 2, "COMMENTER": 1}


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    return provider


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=[])
    return provider


class TestTheRule:
    def test_a_team_is_never_an_owner(self) -> None:
        assert TEAM_GRANT_ROLE_CAP == "WRITER"
        assert f"= 'OWNER' THEN '{TEAM_GRANT_ROLE_CAP}'" in CYPHER_RULE
        assert f'== "OWNER" ? "{TEAM_GRANT_ROLE_CAP}"' in AQL_RULE

    def test_the_grant_role_comes_before_the_member_role(self) -> None:
        assert re.search(r"team_grant\.role IS NOT NULL AND team_grant\.role <> '' THEN team_grant\.role "
                         r"ELSE team_member\.role", CYPHER_RULE)
        assert re.search(r'team_grant\.role != null AND team_grant\.role != ""\) \? team_grant\.role '
                         r": team_member\.role", AQL_RULE)

    def test_any_other_role_reads_as_no_role(self) -> None:
        assert CYPHER_RULE.endswith("IN ['OWNER', 'WRITER', 'READER'] THEN (CASE WHEN team_grant.role IS NOT NULL "
                                    "AND team_grant.role <> '' THEN team_grant.role ELSE team_member.role END) "
                                    "ELSE null END)")
        assert AQL_RULE.endswith('IN ["OWNER", "WRITER", "READER"] ? ((team_grant.role != null AND '
                                 'team_grant.role != "") ? team_grant.role : team_member.role) : null)))')

    def test_a_retired_role_reads_as_reader(self) -> None:
        assert "'COMMENTER'" in CYPHER_RULE and "THEN 'READER'" in CYPHER_RULE
        assert '"COMMENTER"' in AQL_RULE and '? "READER"' in AQL_RULE


class TestNeo4jPaths:
    @pytest.mark.asyncio
    async def test_kb_role(self, neo4j: Neo4jProvider) -> None:
        await neo4j.get_user_kb_permission("kb1", "u1")

        query = neo4j.client.execute_query.await_args.args[0]
        assert f"collect(DISTINCT {CYPHER_RULE}) AS team_roles" in query
        assert "ut.role" not in query

    @pytest.mark.asyncio
    async def test_kb_list(self, neo4j: Neo4jProvider) -> None:
        await neo4j.list_user_knowledge_bases("u1", "o1", 0, 10)

        for call in neo4j.client.execute_query.await_args_list:
            assert f"RETURN kb, {CYPHER_RULE} AS stored_role, false AS is_direct" in call.args[0]

    @pytest.mark.parametrize("node_type", ["record", "recordGroup", "app"])
    def test_hub_roles(self, neo4j: Neo4jProvider, node_type: str) -> None:
        cypher = neo4j._get_permission_role_cypher(node_type, "n", "u")

        assert CYPHER_RULE in cypher
        assert "ut.role" not in cypher


class TestArangoPaths:
    @pytest.mark.asyncio
    async def test_kb_role(self, arango: ArangoHTTPProvider) -> None:
        await arango.get_user_kb_permission("kb1", "u1")

        query = arango.http_client.execute_aql.await_args.args[0]
        assert f"LET team_role = {AQL_RULE}" in query
        assert "role: team_info.role" not in query

    @pytest.mark.asyncio
    async def test_kb_list_page_count_and_filters(self, arango: ArangoHTTPProvider) -> None:
        await arango.list_user_knowledge_bases("u1", "o1", 0, 10, permissions=["WRITER"])

        queries = [call.args[0] for call in arango.http_client.execute_aql.await_args_list]
        assert len(queries) == 3
        for query in queries:
            assert f"LET team_role = {AQL_RULE}" in query
            assert "team_info" not in query

    @pytest.mark.parametrize("node_type", ["record", "recordGroup", "app"])
    def test_hub_roles(self, arango: ArangoHTTPProvider, node_type: str) -> None:
        aql = arango._get_permission_role_aql(node_type, "n", "u")

        assert AQL_RULE in aql
        assert "RETURN user_team_perm.role" not in aql
