"""Query-shape tests for Arango artifact gallery listing."""

from unittest.mock import AsyncMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


@pytest.fixture
def provider():
    p = ArangoHTTPProvider(logger=AsyncMock(), config_service=AsyncMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(side_effect=[[{"id": "a1"}], [1]])
    return p


def _captured_queries(provider) -> list[str]:
    return [call.args[0] for call in provider.execute_query.await_args_list]


class TestArangoListAccessibleArtifactsQueryShape:
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
        list_query, count_query = _captured_queries(provider)
        assert "permissionEdge._from == @user_from" in list_query
        assert 'record.recordType == "ARTIFACT"' in list_query
        assert "record.orgId == @org_id" in list_query
        assert "record.isDeleted != true" in list_query
        assert '(artifactDoc.visibility == null OR artifactDoc.visibility == "VISIBLE")' in list_query
        assert "artifactDoc.isTemporary != true" in list_query
        assert 'artifactDoc.artifactType != "TOOL_RESULT"' in list_query
        assert "LIMIT @skip, @limit" in list_query
        assert "record._key" in list_query
        assert "users/user-key" == provider.execute_query.await_args_list[0].kwargs["bind_vars"]["user_from"]
        assert "skip" in provider.execute_query.await_args_list[0].kwargs["bind_vars"]
        assert "limit" in provider.execute_query.await_args_list[0].kwargs["bind_vars"]
        count_bind = provider.execute_query.await_args_list[1].kwargs["bind_vars"]
        assert "skip" not in count_bind
        assert "limit" not in count_bind
        assert "LENGTH(" in count_query
        assert "LIMIT @skip, @limit" not in count_query

    @pytest.mark.asyncio
    async def test_search_and_filters_bound(self, provider):
        await provider.list_accessible_artifacts(
            user_id="user-key",
            org_id="org-1",
            skip=10,
            limit=20,
            search="Report",
            artifact_types=["IMAGE", "CHART"],
            conversation_id="conv-1",
            date_from=1,
            date_to=2,
            sort_by="name",
            sort_order="asc",
        )
        list_query, count_query = _captured_queries(provider)
        list_bind = provider.execute_query.await_args_list[0].kwargs["bind_vars"]
        count_bind = provider.execute_query.await_args_list[1].kwargs["bind_vars"]
        assert "FILTER (LIKE(LOWER(artifactDoc.name), @search) OR LIKE(LOWER(artifactDoc.logicalName), @search))" in list_query
        assert "artifactDoc.artifactType IN @artifact_types" in list_query
        assert "artifactDoc.conversationId == @conversation_id" in list_query
        assert "record.createdAtTimestamp >= @date_from" in list_query
        assert "record.createdAtTimestamp <= @date_to" in list_query
        assert "record.createdAtTimestamp >= @date_from" in count_query
        assert "record.createdAtTimestamp <= @date_to" in count_query
        assert "SORT artifactDoc.name ASC" in list_query
        assert list_bind["search"] == "%report%"
        assert list_bind["artifact_types"] == ["IMAGE", "CHART"]
        assert list_bind["conversation_id"] == "conv-1"
        assert list_bind["date_from"] == 1
        assert list_bind["date_to"] == 2
        assert list_bind["skip"] == 10
        assert list_bind["limit"] == 20
        assert count_bind["date_from"] == 1
        assert count_bind["date_to"] == 2
        assert "skip" not in count_bind
        assert "limit" not in count_bind


class TestArangoGetArtifactDetailQueryShape:
    @pytest.mark.asyncio
    async def test_filters_on_artifact_id(self, provider):
        provider.execute_query = AsyncMock(return_value=[{"id": "art-1"}])
        row = await provider.get_artifact_detail("user-key", "org-1", "art-1")
        assert row["id"] == "art-1"
        query = provider.execute_query.await_args.args[0]
        bind = provider.execute_query.await_args.kwargs["bind_vars"]
        assert "permissionEdge._from == @user_from" in query
        assert 'permissionEdge.type == "USER"' in query
        assert "record.orgId == @org_id" in query
        assert 'record.recordType == "ARTIFACT"' in query
        assert "record._key == @artifact_id" in query
        assert 'artifactDoc.artifactType != "TOOL_RESULT"' in query
        assert "artifactDoc.isTemporary != true" in query
        assert '(artifactDoc.visibility == null OR artifactDoc.visibility == "VISIBLE")' in query
        assert bind["user_from"] == "users/user-key"
        assert bind["org_id"] == "org-1"
        assert bind["artifact_id"] == "art-1"


class TestArangoListAllRecordsExcludesArtifacts:
    @pytest.mark.asyncio
    async def test_kb_subquery_excludes_artifact_type(self, provider):
        provider.execute_query = AsyncMock(side_effect=[[], [0]])
        await provider.list_all_records(
            "user-key", "org-1", 0, 10, None, None, None, None, None, None, None, None,
            "createdAtTimestamp", "desc", "all",
        )
        list_query = provider.execute_query.await_args_list[0].args[0]
        count_query = provider.execute_query.await_args_list[1].args[0]
        assert 'record.recordType != "ARTIFACT"' in list_query
        assert 'record.recordType != "ARTIFACT"' in count_query
