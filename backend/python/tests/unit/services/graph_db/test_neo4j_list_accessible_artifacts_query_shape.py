"""Query-shape tests for Neo4j artifact gallery listing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


@pytest.fixture
def provider():
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(
        side_effect=[[{"result": {"id": "a1"}}], [{"total": 1}]]
    )
    return p


def _queries(provider) -> list[str]:
    return [call.args[0] for call in provider.client.execute_query.await_args_list]


class TestNeo4jListAccessibleArtifactsQueryShape:
    @pytest.mark.asyncio
    async def test_permission_first_from_graph_user_key(self, provider):
        await provider.list_accessible_artifacts(
            user_id="user-key",
            org_id="org-1",
            skip=0,
            limit=50,
            search=None,
            artifact_types=None,
            conversation_id=None,
            date_from=None,
            date_to=None,
            sort_by="createdAtTimestamp",
            sort_order="desc",
        )
        list_query, count_query = _queries(provider)
        assert 'MATCH (u:User {id: $user_id})-[perm:PERMISSION {type: "USER"}]->(rec:Record)' in list_query
        assert 'rec.recordType = "ARTIFACT"' in list_query
        assert "rec.orgId = $org_id" in list_query
        assert "coalesce(rec.isDeleted, false) = false" in list_query
        assert 'coalesce(art.visibility, "VISIBLE") = "VISIBLE"' in list_query
        assert 'art.artifactType <> "TOOL_RESULT"' in list_query
        assert "coalesce(art.isTemporary, false) = false" in list_query
        assert "SKIP $skip" in list_query
        assert "LIMIT $limit" in list_query
        assert "count(rec) AS total" in count_query
        params = provider.client.execute_query.await_args_list[0].kwargs["parameters"]
        assert params["user_id"] == "user-key"

    @pytest.mark.asyncio
    async def test_search_and_filters(self, provider):
        await provider.list_accessible_artifacts(
            user_id="user-key",
            org_id="org-1",
            skip=5,
            limit=10,
            search="plot",
            artifact_types=["CHART"],
            conversation_id="c1",
            date_from=1,
            date_to=2,
            sort_by="name",
            sort_order="asc",
        )
        list_query, count_query = _queries(provider)
        list_params = provider.client.execute_query.await_args_list[0].kwargs["parameters"]
        count_params = provider.client.execute_query.await_args_list[1].kwargs["parameters"]
        assert "toLower(coalesce(art.name, '')) CONTAINS toLower($search)" in list_query
        assert "art.artifactType IN $artifact_types" in list_query
        assert "rec.createdAtTimestamp >= $date_from" in list_query
        assert "rec.createdAtTimestamp <= $date_to" in list_query
        assert "rec.createdAtTimestamp >= $date_from" in count_query
        assert "rec.createdAtTimestamp <= $date_to" in count_query
        assert "ORDER BY art.name ASC" in list_query
        assert list_params["date_from"] == 1
        assert list_params["date_to"] == 2
        assert count_params["date_from"] == 1
        assert count_params["date_to"] == 2


class TestNeo4jGetArtifactDetailQueryShape:
    @pytest.mark.asyncio
    async def test_filters_on_artifact_id(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[{"result": {"id": "art-1"}}])
        row = await provider.get_artifact_detail("user-key", "org-1", "art-1")
        assert row["id"] == "art-1"
        query = provider.client.execute_query.await_args.args[0]
        params = provider.client.execute_query.await_args.kwargs["parameters"]
        assert 'MATCH (u:User {id: $user_id})-[perm:PERMISSION {type: "USER"}]->(rec:Record)' in query
        assert "rec.orgId = $org_id" in query
        assert "coalesce(rec.isDeleted, false) = false" in query
        assert "rec.id = $artifact_id" in query
        assert 'art.artifactType <> "TOOL_RESULT"' in query
        assert "coalesce(art.isTemporary, false) = false" in query
        assert 'coalesce(art.visibility, "VISIBLE") = "VISIBLE"' in query
        assert params["user_id"] == "user-key"
        assert params["org_id"] == "org-1"
        assert params["artifact_id"] == "art-1"


class TestNeo4jListAllRecordsExcludesArtifacts:
    @pytest.mark.asyncio
    async def test_kb_clause_excludes_artifact_type(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])
        await provider.list_all_records(
            user_id="user-key",
            org_id="org-1",
            skip=0,
            limit=10,
        )
        list_query = provider.client.execute_query.await_args_list[0].args[0]
        assert 'kbRecord.recordType <> "ARTIFACT"' in list_query
