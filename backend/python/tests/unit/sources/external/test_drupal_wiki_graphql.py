"""Tests for the Drupal Wiki internal GraphQL client and data source.

The endpoint is undocumented, so these pin the exact shapes the connector relies on:
members with their role machine names, the page tree, share targets and the trash.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.sources.client.drupal_wiki.drupal_wiki import DrupalWikiRESTClientViaToken
from app.sources.client.drupal_wiki.graphql import (
    DrupalWikiGraphQLClient,
    DrupalWikiGraphQLClientViaToken,
)
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.external.drupal_wiki.graphql import (
    DrupalWikiGraphQLAuthError,
    DrupalWikiGraphQLDataSource,
    DrupalWikiGraphQLError,
    DrupalWikiGraphQLRefusedError,
    DrupalWikiGraphQLTransientError,
)

BASE_URL = "https://wiki.example.com"


def ok(data: dict) -> GraphQLResponse:
    return GraphQLResponse(success=True, data=data, status_code=200)


def failed(message: str = "Forbidden", status_code: int = 403) -> GraphQLResponse:
    return GraphQLResponse(success=False, data=None, message=message, status_code=status_code)


def http_response(body: object, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    return resp


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

    @pytest.mark.asyncio
    async def test_operations_go_through_the_rest_client(self) -> None:
        # The REST client carries the connector's rate limit, retries and Retry-After
        # handling, so a GraphQL call must not go around it.
        rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
        rest.execute = AsyncMock(return_value=http_response({"data": {"x": 1}}))
        client = DrupalWikiGraphQLClientViaToken(rest)

        result = await client.execute("query Q { x }", {"spaceId": "3"}, "Q")

        request = rest.execute.await_args.args[0]
        assert (request.method, request.url) == ("POST", f"{BASE_URL}/graphql")
        assert request.body == {
            "query": "query Q { x }", "variables": {"spaceId": "3"}, "operationName": "Q",
        }
        assert result.success is True
        assert result.data == {"x": 1}

    @pytest.mark.asyncio
    async def test_a_non_json_reply_is_a_failed_response(self) -> None:
        rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
        rest.execute = AsyncMock(return_value=http_response(ValueError("not json")))

        result = await DrupalWikiGraphQLClientViaToken(rest).execute("query Q { x }")

        assert result.success is False
        assert "JSON" in (result.message or "")

    @pytest.mark.asyncio
    async def test_an_error_status_is_a_failed_response(self) -> None:
        rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
        rest.execute = AsyncMock(
            return_value=http_response({"errors": [{"message": "slow down"}]}, status=429)
        )

        result = await DrupalWikiGraphQLClientViaToken(rest).execute("query Q { x }")

        assert (result.success, result.status_code) == (False, 429)

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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [TimeoutError(), httpx.ReadTimeout(""), ValueError("bad json")]
    )
    async def test_a_transport_error_is_a_query_failure_naming_its_cause(
        self, error: Exception
    ) -> None:
        # Anything else escapes the caller's per-space handling and takes the whole
        # phase down. A timeout's own message is empty, so its type is named instead.
        source, inner = data_source()
        inner.execute = AsyncMock(side_effect=error)

        with pytest.raises(DrupalWikiGraphQLError) as caught:
            await source.get_space_members(12)

        assert str(caught.value).split("failed: ", 1)[1]


class TestTree:
    @pytest.mark.asyncio
    async def test_tree_nodes_are_returned(self) -> None:
        source, _ = data_source(ok({"spaceContentStructureFlatTree": [
            {"id": "n1", "pageId": 100, "parentId": None, "position": 0},
        ]}))
        assert (await source.get_space_tree(12))[0]["pageId"] == 100


class TestFailuresCarryTheWikisReason:
    @pytest.mark.asyncio
    async def test_a_rest_style_problem_from_the_graphql_endpoint_is_described(self) -> None:
        # The gateway in front of /graphql answers a bad token with a ProblemResponse,
        # which has no GraphQL errors array; that used to read "status 401: None".
        rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
        rest.execute = AsyncMock(return_value=http_response(
            {"status": 401, "title": "Unauthorized", "detail": "Token expired"}, status=401,
        ))
        source = DrupalWikiGraphQLDataSource(DrupalWikiGraphQLClient(DrupalWikiGraphQLClientViaToken(rest)))

        with pytest.raises(DrupalWikiGraphQLError) as caught:
            await source.get_space_members(12)

        assert str(caught.value) == (
            "RoleMemberManagerData failed: Unauthorized: Token expired (HTTP 401)"
        )

    @pytest.mark.asyncio
    async def test_a_graphql_error_keeps_its_code(self) -> None:
        rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
        rest.execute = AsyncMock(return_value=http_response({"errors": [
            {"message": "Operation not allowed", "extensions": {"classification": "QueryNotAllowed"}},
        ]}))
        source = DrupalWikiGraphQLDataSource(DrupalWikiGraphQLClient(DrupalWikiGraphQLClientViaToken(rest)))

        with pytest.raises(DrupalWikiGraphQLError, match="QueryNotAllowed: Operation not allowed \\(HTTP 200\\)"):
            await source.get_space_tree(12)

    @pytest.mark.asyncio
    async def test_a_timeout_names_its_type(self) -> None:
        source, inner = data_source()
        inner.execute = AsyncMock(side_effect=httpx.ReadTimeout(""))

        with pytest.raises(DrupalWikiGraphQLError, match="RoleMemberManagerData failed: ReadTimeout$"):
            await source.get_space_members(12)


def wiki_source(body: object, status: int) -> DrupalWikiGraphQLDataSource:
    """A data source whose wiki answers every operation with ``body`` and ``status``."""
    rest = DrupalWikiRESTClientViaToken(BASE_URL, "secret")
    rest.execute = AsyncMock(return_value=http_response(body, status=status))
    return DrupalWikiGraphQLDataSource(DrupalWikiGraphQLClient(DrupalWikiGraphQLClientViaToken(rest)))


# Drupal Wiki 8.44 refuses both operations for a personal access token with exactly this.
REFUSED_844 = {"errors": [{"message": "QueryNotAllowed", "extensions": {"code": "QUERY_NOT_ALLOWED"}}]}
REFUSED_CLASSIFIED = {"errors": [{"message": "QueryNotAllowed", "extensions": {"classification": "QueryNotAllowed"}}]}


def refusal(code: str) -> dict:
    return {"errors": [{"message": code, "extensions": {"code": code}}]}


class TestARefusalIsTypedApart:
    """T1: a refusal is told apart from a token the wiki rejects and from a failure that may pass."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "status"),
        [(REFUSED_844, 400), (REFUSED_844, 200), (REFUSED_844, 403), (REFUSED_CLASSIFIED, 400)],
    )
    @pytest.mark.parametrize("operation", ["get_space_members", "get_space_tree"])
    async def test_query_not_allowed_is_a_refusal_whatever_the_status(
        self, body: dict, status: int, operation: str
    ) -> None:
        with pytest.raises(DrupalWikiGraphQLRefusedError, match="QueryNotAllowed") as caught:
            await getattr(wiki_source(body, status), operation)(12)

        assert caught.value.kind == "QUERY_NOT_ALLOWED"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ["PERSISTED_QUERY_NOT_FOUND", "PERSISTED_QUERY_NOT_SUPPORTED"])
    async def test_a_persisted_query_the_wiki_lacks_is_a_refusal_of_its_own_kind(self, code: str) -> None:
        # Drupal Wiki 8.44 serves only the persisted queries its web UI ships.
        with pytest.raises(DrupalWikiGraphQLRefusedError) as caught:
            await wiki_source(refusal(code), 400).get_space_members(12)

        assert caught.value.kind == code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    async def test_a_server_error_or_rate_limit_is_transient(self, status: int) -> None:
        with pytest.raises(DrupalWikiGraphQLTransientError):
            await wiki_source({"errors": [{"message": "upstream down"}]}, status).get_space_members(12)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [TimeoutError(), httpx.ReadTimeout(""), httpx.ConnectError("refused")])
    async def test_a_timeout_or_dropped_connection_is_transient(self, error: Exception) -> None:
        source, inner = data_source()
        inner.execute = AsyncMock(side_effect=error)

        with pytest.raises(DrupalWikiGraphQLTransientError):
            await source.get_space_tree(12)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_rejected_token_is_an_auth_error(self, status: int) -> None:
        body = {"status": status, "title": "Unauthorized", "detail": "Token expired"}

        with pytest.raises(DrupalWikiGraphQLAuthError) as caught:
            await wiki_source(body, status).get_space_members(12)

        assert not isinstance(caught.value, DrupalWikiGraphQLRefusedError)

    @pytest.mark.asyncio
    async def test_an_unknown_field_is_neither_refused_nor_transient(self) -> None:
        body = {"errors": [{"message": "Field 'x' is undefined", "extensions": {"classification": "ValidationError"}}]}

        with pytest.raises(DrupalWikiGraphQLError) as caught:
            await wiki_source(body, 400).get_space_tree(12)

        assert type(caught.value) is DrupalWikiGraphQLError


class TestAnExceptionCarryingAStatusIsTyped:
    """QA-P1-4: an error raised with the wiki's 401/403 response attached is an auth error."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_raised_401_or_403_is_auth_not_transient(self, status: int) -> None:
        request = httpx.Request("POST", f"{BASE_URL}/graphql")
        error = httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(status, request=request))
        source, inner = data_source()
        inner.execute = AsyncMock(side_effect=error)

        with pytest.raises(DrupalWikiGraphQLAuthError):
            await source.get_space_members(12)
