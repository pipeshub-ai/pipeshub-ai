"""Unit tests for GraphQL client and response modules."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.graphql.response import GraphQLError, GraphQLResponse


# ---------------------------------------------------------------------------
# GraphQLError
# ---------------------------------------------------------------------------


class TestGraphQLError:
    def test_basic(self):
        err = GraphQLError(message="something broke")
        assert err.message == "something broke"
        assert err.locations is None
        assert err.path is None
        assert err.extensions is None

    def test_with_all_fields(self):
        err = GraphQLError(
            message="bad query",
            locations=[{"line": 1, "column": 5}],
            path=["viewer", "repositories"],
            extensions={"code": "BAD_REQUEST"},
        )
        assert err.locations == [{"line": 1, "column": 5}]
        assert err.path == ["viewer", "repositories"]
        assert err.extensions["code"] == "BAD_REQUEST"

    def test_path_with_int(self):
        err = GraphQLError(message="err", path=["items", 0, "name"])
        assert err.path[1] == 0


# ---------------------------------------------------------------------------
# GraphQLResponse
# ---------------------------------------------------------------------------


class TestGraphQLResponse:
    def test_success_response(self):
        resp = GraphQLResponse(success=True, data={"viewer": {"login": "user"}})
        assert resp.success is True
        assert resp.data["viewer"]["login"] == "user"
        assert resp.errors is None
        assert resp.message is None

    def test_error_response(self):
        err = GraphQLError(message="bad")
        resp = GraphQLResponse(success=False, errors=[err], message="bad")
        assert resp.success is False
        assert len(resp.errors) == 1

    def test_to_json(self):
        resp = GraphQLResponse(success=True, data={"key": "val"})
        j = resp.to_json()
        assert '"success":true' in j.lower() or '"success": true' in j.lower()

    def test_from_response_success(self):
        raw = {"data": {"viewer": {"login": "user"}}}
        resp = GraphQLResponse.from_response(raw)
        assert resp.success is True
        assert resp.data == {"viewer": {"login": "user"}}
        assert resp.errors is None
        assert resp.message is None

    def test_from_response_with_errors(self):
        raw = {
            "data": None,
            "errors": [
                {
                    "message": "Not found",
                    "locations": [{"line": 1, "column": 2}],
                    "path": ["viewer"],
                    "extensions": {"code": "NOT_FOUND"},
                }
            ],
        }
        resp = GraphQLResponse.from_response(raw)
        assert resp.success is False
        assert len(resp.errors) == 1
        assert resp.errors[0].message == "Not found"
        assert resp.message == "Not found"

    def test_from_response_with_extensions(self):
        raw = {"data": {"x": 1}, "extensions": {"cost": 42}}
        resp = GraphQLResponse.from_response(raw)
        assert resp.extensions == {"cost": 42}
        assert resp.success is True

    def test_from_response_empty_errors(self):
        raw = {"data": {"x": 1}, "errors": []}
        resp = GraphQLResponse.from_response(raw)
        assert resp.success is True

    def test_from_response_error_missing_fields(self):
        raw = {"errors": [{"message": "oops"}]}
        resp = GraphQLResponse.from_response(raw)
        assert resp.success is False
        assert resp.errors[0].locations is None
        assert resp.errors[0].path is None

    def test_from_response_multiple_errors(self):
        raw = {
            "errors": [
                {"message": "err1"},
                {"message": "err2"},
            ]
        }
        resp = GraphQLResponse.from_response(raw)
        assert len(resp.errors) == 2
        # message should be first error's message
        assert resp.message == "err1"


# ---------------------------------------------------------------------------
# GraphQLClient (ABC - test via concrete subclass)
# ---------------------------------------------------------------------------


class ConcreteGraphQLClient:
    """Minimal concrete subclass for testing abstract GraphQLClient."""

    def __init__(self, endpoint, headers=None, timeout=30):
        self.endpoint = endpoint
        self.headers = headers or {}
        self.timeout = timeout

    def get_auth_header(self):
        return self.headers.get("Authorization")

    async def execute(self, query, variables=None, operation_name=None):
        # Import here to use real implementation
        from app.sources.client.graphql.client import GraphQLClient

        # We'll test the actual execute method through mocking aiohttp
        pass

    async def close(self):
        return None


class TestGraphQLClientInit:
    def test_init_default(self):
        from app.sources.client.graphql.client import GraphQLClient

        # Can't instantiate ABC directly; test via subclass
        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")
        assert client.endpoint == "http://graphql.local/graphql"
        assert client.headers == {}
        assert client.timeout == 30

    def test_init_with_headers(self):
        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return self.headers.get("Authorization")

        client = TestImpl(
            endpoint="http://graphql.local/graphql",
            headers={"Authorization": "Bearer tok"},
            timeout=60,
        )
        assert client.headers["Authorization"] == "Bearer tok"
        assert client.timeout == 60


def _make_aiohttp_mocks(response_data):
    """Create properly nested aiohttp mock objects for ClientSession + post context managers."""
    mock_response = MagicMock()
    mock_response.json = AsyncMock(return_value=response_data)

    # session.post(...) returns an async context manager
    mock_post_cm = MagicMock()
    mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post_cm.__aexit__ = AsyncMock(return_value=False)

    # The session itself is an async context manager returned by ClientSession(...)
    mock_session = MagicMock()
    mock_session.post.return_value = mock_post_cm
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


class TestGraphQLClientExecute:
    @pytest.mark.asyncio
    async def test_execute_success(self):
        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")
        mock_session = _make_aiohttp_mocks({"data": {"viewer": {"login": "user"}}})

        with patch("app.sources.client.graphql.client.aiohttp.ClientSession", return_value=mock_session), \
             patch("app.sources.client.graphql.client.aiohttp.ClientTimeout"):
            result = await client.execute("{ viewer { login } }")
            assert isinstance(result, GraphQLResponse)
            assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_variables_and_operation(self):
        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")
        mock_session = _make_aiohttp_mocks({"data": {"result": True}})

        with patch("app.sources.client.graphql.client.aiohttp.ClientSession", return_value=mock_session), \
             patch("app.sources.client.graphql.client.aiohttp.ClientTimeout"):
            result = await client.execute(
                "mutation M($id: ID!) { delete(id: $id) }",
                variables={"id": "123"},
                operation_name="M",
            )
            assert isinstance(result, GraphQLResponse)

    @pytest.mark.asyncio
    async def test_execute_client_error(self):
        import aiohttp

        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")

        with patch(
            "app.sources.client.graphql.client.aiohttp.ClientSession",
            side_effect=aiohttp.ClientError("connection refused"),
        ), patch("app.sources.client.graphql.client.aiohttp.ClientTimeout"):
            result = await client.execute("{ viewer { login } }")
            assert result.success is False
            assert "Request failed" in result.message


class TestGraphQLClientContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")
        async with client as c:
            assert c is client

    @pytest.mark.asyncio
    async def test_close_noop(self):
        from app.sources.client.graphql.client import GraphQLClient

        class TestImpl(GraphQLClient):
            def get_auth_header(self):
                return None

        client = TestImpl(endpoint="http://graphql.local/graphql")
        result = await client.close()
        assert result is None
