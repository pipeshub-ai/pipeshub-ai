"""Unit tests for app.sources.client.http.http_client.HTTPClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


# ---------------------------------------------------------------------------
# Constructor / defaults
# ---------------------------------------------------------------------------
class TestHTTPClientInit:
    def test_default_token_type(self):
        client = HTTPClient(token="tok123")
        assert client.headers["Authorization"] == "Bearer tok123"

    def test_custom_token_type(self):
        client = HTTPClient(token="tok123", token_type="Basic")
        assert client.headers["Authorization"] == "Basic tok123"

    def test_default_timeout(self):
        client = HTTPClient(token="t")
        assert client.timeout == 30.0

    def test_custom_timeout(self):
        client = HTTPClient(token="t", timeout=60.0)
        assert client.timeout == 60.0

    def test_default_follow_redirects(self):
        client = HTTPClient(token="t")
        assert client.follow_redirects is True

    def test_custom_follow_redirects(self):
        client = HTTPClient(token="t", follow_redirects=False)
        assert client.follow_redirects is False

    def test_client_initially_none(self):
        client = HTTPClient(token="t")
        assert client.client is None


# ---------------------------------------------------------------------------
# get_client
# ---------------------------------------------------------------------------
class TestGetClient:
    def test_returns_self(self):
        client = HTTPClient(token="t")
        assert client.get_client() is client


# ---------------------------------------------------------------------------
# _ensure_client
# ---------------------------------------------------------------------------
class TestEnsureClient:
    @pytest.mark.asyncio
    async def test_creates_client_on_first_call(self):
        client = HTTPClient(token="t")
        assert client.client is None
        with patch("app.sources.client.http.http_client.httpx.AsyncClient") as MockAsyncClient:
            mock_instance = MagicMock()
            MockAsyncClient.return_value = mock_instance
            result = await client._ensure_client()
            MockAsyncClient.assert_called_once_with(timeout=30.0, follow_redirects=True)
            assert result is mock_instance
            assert client.client is mock_instance

    @pytest.mark.asyncio
    async def test_reuses_existing_client(self):
        client = HTTPClient(token="t")
        sentinel = MagicMock()
        client.client = sentinel
        result = await client._ensure_client()
        assert result is sentinel


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------
class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_get_request(self):
        client = HTTPClient(token="mytoken")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(url="https://api.example.com/items", method="GET")
        response = await client.execute(request)

        mock_httpx_client.request.assert_called_once_with(
            "GET",
            "https://api.example.com/items",
            params={},
            headers={"Authorization": "Bearer mytoken"},
        )
        assert isinstance(response, HTTPResponse)

    @pytest.mark.asyncio
    async def test_execute_with_path_params(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/items/{item_id}",
            method="GET",
            path={"item_id": "42"},
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert call_args[0][1] == "https://api.example.com/items/42"

    @pytest.mark.asyncio
    async def test_execute_with_query_params(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/items",
            method="GET",
            query={"page": "1", "limit": "10"},
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["params"] == {"page": "1", "limit": "10"}

    @pytest.mark.asyncio
    async def test_execute_post_json_body(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/items",
            method="POST",
            body={"name": "test"},
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["json"] == {"name": "test"}
        assert "data" not in call_args[1]
        assert "content" not in call_args[1]

    @pytest.mark.asyncio
    async def test_execute_post_form_data(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/login",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body={"username": "user", "password": "pass"},
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["data"] == {"username": "user", "password": "pass"}
        assert "json" not in call_args[1]

    @pytest.mark.asyncio
    async def test_execute_post_bytes_body(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/upload",
            method="POST",
            body=b"raw bytes content",
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["content"] == b"raw bytes content"
        assert "json" not in call_args[1]
        assert "data" not in call_args[1]

    @pytest.mark.asyncio
    async def test_execute_no_body(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(url="https://api.example.com/items", method="DELETE")
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        assert "json" not in call_args[1]
        assert "data" not in call_args[1]
        assert "content" not in call_args[1]

    @pytest.mark.asyncio
    async def test_request_headers_override_client_headers(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(
            url="https://api.example.com/items",
            method="GET",
            headers={"Authorization": "Basic override", "X-Custom": "val"},
        )
        await client.execute(request)

        call_args = mock_httpx_client.request.call_args
        merged = call_args[1]["headers"]
        assert merged["Authorization"] == "Basic override"
        assert merged["X-Custom"] == "val"

    @pytest.mark.asyncio
    async def test_execute_passes_extra_kwargs(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        mock_response = MagicMock()
        mock_httpx_client.request = AsyncMock(return_value=mock_response)
        client.client = mock_httpx_client

        request = HTTPRequest(url="https://api.example.com/items", method="GET")
        await client.execute(request, timeout=60)

        call_args = mock_httpx_client.request.call_args
        assert call_args[1]["timeout"] == 60

    @pytest.mark.asyncio
    async def test_execute_creates_client_if_none(self):
        client = HTTPClient(token="t")
        assert client.client is None

        with patch("app.sources.client.http.http_client.httpx.AsyncClient") as MockAsyncClient:
            mock_httpx = AsyncMock()
            mock_response = MagicMock()
            mock_httpx.request = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value = mock_httpx

            request = HTTPRequest(url="https://api.example.com/items", method="GET")
            await client.execute(request)

            MockAsyncClient.assert_called_once()
            mock_httpx.request.assert_called_once()


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------
class TestClose:
    @pytest.mark.asyncio
    async def test_close_when_client_exists(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        client.client = mock_httpx_client

        await client.close()

        mock_httpx_client.aclose.assert_called_once()
        assert client.client is None

    @pytest.mark.asyncio
    async def test_close_when_no_client(self):
        client = HTTPClient(token="t")
        # Should not raise
        await client.close()
        assert client.client is None


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------
class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_returns_self(self):
        with patch("app.sources.client.http.http_client.httpx.AsyncClient") as MockAsyncClient:
            MockAsyncClient.return_value = MagicMock()
            client = HTTPClient(token="t")
            result = await client.__aenter__()
            assert result is client
            assert client.client is not None

    @pytest.mark.asyncio
    async def test_aexit_closes_client(self):
        client = HTTPClient(token="t")
        mock_httpx_client = AsyncMock()
        client.client = mock_httpx_client

        await client.__aexit__(None, None, None)

        mock_httpx_client.aclose.assert_called_once()
        assert client.client is None

    @pytest.mark.asyncio
    async def test_async_with_block(self):
        with patch("app.sources.client.http.http_client.httpx.AsyncClient") as MockAsyncClient:
            mock_httpx = AsyncMock()
            MockAsyncClient.return_value = mock_httpx
            async with HTTPClient(token="t") as client:
                assert client.client is not None
            mock_httpx.aclose.assert_called_once()
