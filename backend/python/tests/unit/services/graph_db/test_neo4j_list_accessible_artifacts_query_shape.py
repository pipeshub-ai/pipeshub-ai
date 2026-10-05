"""Query-shape tests for Neo4j artifact gallery listing."""

import re
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


class TestNeo4jGalleryReadFailuresPropagate:
    @pytest.mark.asyncio
    async def test_list_failure_is_raised_not_returned_as_an_empty_page(self, provider):
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("bolt down"))
        with pytest.raises(RuntimeError, match="bolt down"):
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

    @pytest.mark.asyncio
    async def test_count_query_failure_is_raised(self, provider):
        provider.client.execute_query = AsyncMock(
            side_effect=[[{"result": {"id": "a1"}}], RuntimeError("count failed")]
        )
        with pytest.raises(RuntimeError, match="count failed"):
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

    @pytest.mark.asyncio
    async def test_detail_failure_is_raised_not_returned_as_not_found(self, provider):
        provider.client.execute_query = AsyncMock(side_effect=RuntimeError("bolt down"))
        with pytest.raises(RuntimeError, match="bolt down"):
            await provider.get_artifact_detail("user-key", "org-1", "art-1")

    @pytest.mark.asyncio
    async def test_detail_with_no_visible_row_is_still_none(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])
        assert await provider.get_artifact_detail("user-key", "org-1", "art-1") is None


class TestNeo4jAllRecordsExcludesArtifacts:
    """An artifact is a record with a direct grant and the connector id of no App
    (``coding_sandbox_<org>``). The All Records list is the Knowledge Hub listing,
    one page per App the user may enter, so an artifact stays out by never
    reaching a page: it is in no bucket of the user's grants, its connector id
    lists nothing, and a page picks up a granted node only of its own App."""

    ARTIFACT_CONNECTOR = "coding_sandbox_org-1"

    @pytest.mark.asyncio
    async def test_a_granted_artifact_is_in_no_connector_bucket(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[{
            "grantees": ["user-key"],
            "gatedApps": ["app-1"],
            "grantSets": [
                {"connectorId": "app-1", "ids": ["rec-1"]},
                {"connectorId": self.ARTIFACT_CONNECTOR, "ids": ["art-1"]},
            ],
        }])
        access = await provider.get_knowledge_hub_access_v3("user-key", "org-1")
        assert access["by_connector"] == {"app-1": ["rec-1"]}

    @pytest.mark.asyncio
    async def test_the_connector_id_of_an_artifact_lists_nothing(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])
        page = await provider.get_knowledge_hub_connector_page_v3(
            self.ARTIFACT_CONNECTOR, "org-1", ["user-key"], ["app-1"], ["art-1"],
        )
        assert page["rows"] == [] and page["total"] == 0
        provider.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_takes_a_granted_node_only_of_its_own_app(self, provider):
        provider.client.execute_query = AsyncMock(return_value=[])
        await provider.get_knowledge_hub_connector_page_v3(
            "app-1", "org-1", ["user-key"], ["app-1"], ["rec-1", "art-1"],
        )
        listing = _queries(provider)[-1]
        assert listing.lstrip().startswith("MATCH (app:App {id: $app_id})")
        # The two arms that read the grant list rather than walk from the App.
        assert re.search(r"MATCH \(sd:Record\|RecordGroup\)\s+WHERE sd\.connectorId = app\.id", listing)
        assert re.search(r"MATCH \(dg:RecordGroup\)\s+WHERE dg\.connectorId = app\.id", listing)
        assert listing.count("IN $kh_lists.grantedIds") == listing.count("sd.id IN $kh_lists.grantedIds") + listing.count(
            "dg.id IN $kh_lists.grantedIds"
        ) + listing.count("ca.id IN $kh_lists.grantedIds") + listing.count("cc.id IN $kh_lists.grantedIds")
