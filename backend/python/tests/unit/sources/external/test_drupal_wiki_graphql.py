"""Tests for the Drupal Wiki internal GraphQL client and data source.

The endpoint is undocumented, so these pin the exact shapes the connector relies on:
members with their role machine names, the page tree, share targets and the trash.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.drupal_wiki.drupal_wiki import DrupalWikiRESTClientViaToken
from app.sources.client.drupal_wiki.graphql import (
    DrupalWikiGraphQLClient,
    DrupalWikiGraphQLClientViaToken,
)
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.external.drupal_wiki.graphql import (
    DrupalWikiGraphQLDataSource,
    DrupalWikiGraphQLError,
)

BASE_URL = "https://wiki.example.com"


def ok(data: dict) -> GraphQLResponse:
    return GraphQLResponse(success=True, data=data, status_code=200)


def failed(message: str = "Forbidden", status_code: int = 403) -> GraphQLResponse:
    return GraphQLResponse(success=False, data=None, message=message, status_code=status_code)


def data_source(*responses: GraphQLResponse) -> tuple[DrupalWikiGraphQLDataSource, MagicMock]:
    inner = MagicMock()
    inner.execute = AsyncMock(side_effect=list(responses))
    client = MagicMock()
    client.get_client.return_value = inner
    return DrupalWikiGraphQLDataSource(client), inner


class TestClient:
    def test_endpoint_and_headers_come_from_the_rest_client(self) -> None:
        rest = DrupalWikiRESTClientViaToken(f"{BASE_URL}/api/rest", "secret")
        client = DrupalWikiGraphQLClient.build_from_rest_client(rest).get_client()

        assert client.endpoint == f"{BASE_URL}/graphql"
        assert client.headers["Authorization"] == "Bearer secret"
        assert client.headers["X-API-Version"] == "1"
        assert client.get_auth_header() == "Bearer secret"

    def test_set_token_updates_the_header(self) -> None:
        client = DrupalWikiGraphQLClientViaToken(BASE_URL, "secret")
        client.set_token("pat:secret")
        assert client.headers["Authorization"] == "Bearer pat:secret"

class TestSpaceMembers:
    @pytest.mark.asyncio
    async def test_members_are_returned(self) -> None:
        source, inner = data_source(ok({
            "spaceRoleUserMembers": [{"id": 1, "email": "alice@example.com"}],
            "spaceRoleGroupMembers": [{"id": 5}],
        }))

        members = await source.get_space_members(12)

        assert members["users"][0]["email"] == "alice@example.com"
        assert members["groups"][0]["id"] == 5
        assert inner.execute.await_args.kwargs["variables"] == {"spaceId": "12"}

    @pytest.mark.asyncio
    async def test_missing_sections_default_to_empty(self) -> None:
        source, _ = data_source(ok({}))
        members = await source.get_space_members(12)
        assert members == {"users": [], "groups": []}

    @pytest.mark.asyncio
    async def test_failure_raises_so_the_caller_can_fail_closed(self) -> None:
        source, _ = data_source(failed())
        with pytest.raises(DrupalWikiGraphQLError, match="RoleMemberManagerData"):
            await source.get_space_members(12)


class TestTree:
    @pytest.mark.asyncio
    async def test_tree_nodes_are_returned(self) -> None:
        source, _ = data_source(ok({"spaceContentStructureFlatTree": [
            {"id": "n1", "pageId": 100, "parentId": None, "position": 0},
        ]}))
        assert (await source.get_space_tree(12))[0]["pageId"] == 100
