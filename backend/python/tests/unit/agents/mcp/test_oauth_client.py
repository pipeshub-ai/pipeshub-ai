"""Unit tests for app.agents.mcp.oauth_client."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.oauth_client import (
    MCPOAuthError,
    MCPRefreshTokenInvalidError,
    _parse_form_encoded,
    exchange_code_for_token,
    refresh_access_token,
)


def _mock_async_client(inner: MagicMock):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.agents.mcp.oauth_client.httpx.AsyncClient", return_value=cm)


def _json_response(status_code: int, body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": "application/json"}
    resp.text = str(body)
    resp.json.return_value = body
    return resp


def _form_response(status_code: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"content-type": "application/x-www-form-urlencoded"}
    resp.text = text
    return resp


class TestParseFormEncoded:
    def test_parses_basic_fields(self) -> None:
        result = _parse_form_encoded("access_token=abc&token_type=bearer&expires_in=3600")
        assert result["access_token"] == "abc"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] == 3600

    def test_non_numeric_expires_in_left_unparsed(self) -> None:
        result = _parse_form_encoded("access_token=abc&expires_in=not-a-number")
        assert result["expires_in"] == "not-a-number"

    def test_missing_expires_in_omitted(self) -> None:
        result = _parse_form_encoded("access_token=abc")
        assert "expires_in" not in result


class TestExchangeCodeForToken:
    @pytest.mark.asyncio
    async def test_json_response_parsed(self) -> None:
        resp = _json_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 3600})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                code="authcode",
                redirect_uri="https://app.example.com/callback",
            )

        assert tokens.access_token == "tok"
        assert tokens.expires_in == 3600
        assert tokens.token_url == "https://example.com/token"

    @pytest.mark.asyncio
    async def test_form_encoded_response_parsed(self) -> None:
        resp = _form_response(200, "access_token=tok2&token_type=bearer&expires_in=7200")
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                token_url="https://github.com/login/oauth/access_token",
                client_id="cid",
                client_secret=None,
                code="authcode",
                redirect_uri="https://app.example.com/callback",
            )

        assert tokens.access_token == "tok2"
        assert tokens.expires_in == 7200

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self) -> None:
        resp = _json_response(200, {"token_type": "Bearer"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            with pytest.raises(MCPOAuthError, match="access_token"):
                await exchange_code_for_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    code="authcode",
                    redirect_uri="https://app.example.com/callback",
                )

    @pytest.mark.asyncio
    async def test_error_status_raises_oauth_error(self) -> None:
        resp = _json_response(400, {"error": "invalid_request"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            with pytest.raises(MCPOAuthError):
                await exchange_code_for_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    code="authcode",
                    redirect_uri="https://app.example.com/callback",
                )

    @pytest.mark.asyncio
    async def test_includes_code_verifier_when_provided(self) -> None:
        resp = _json_response(200, {"access_token": "tok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            await exchange_code_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret=None,
                code="authcode",
                redirect_uri="https://app.example.com/callback",
                code_verifier="verifier123",
            )

        sent_data = inner.post.await_args.kwargs["data"]
        assert sent_data["code_verifier"] == "verifier123"
        assert "client_secret" not in sent_data

    @pytest.mark.asyncio
    async def test_unknown_content_type_falls_back_to_json_body(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/octet-stream"}
        resp.text = '{"access_token": "tok-json", "token_type": "Bearer"}'
        resp.json.side_effect = Exception("not json via .json()")
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                code="authcode",
                redirect_uri="https://app.example.com/callback",
            )

        assert tokens.access_token == "tok-json"

    @pytest.mark.asyncio
    async def test_unknown_content_type_falls_back_to_form_encoded(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/octet-stream"}
        resp.text = "access_token=tok-form&token_type=bearer"
        resp.json.side_effect = Exception("not json")
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                code="authcode",
                redirect_uri="https://app.example.com/callback",
            )

        assert tokens.access_token == "tok-form"

    @pytest.mark.asyncio
    async def test_json_content_type_with_unparseable_body_falls_back_to_form(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = "access_token=tok-form&token_type=bearer"
        resp.json.side_effect = ValueError("not json")
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                code="authcode",
                redirect_uri="https://app.example.com/callback",
            )

        assert tokens.access_token == "tok-form"


class TestRefreshAccessToken:
    @pytest.mark.asyncio
    async def test_successful_refresh_returns_new_tokens(self) -> None:
        resp = _json_response(200, {"access_token": "newtok", "refresh_token": "newrefresh", "expires_in": 3600})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await refresh_access_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                refresh_token="oldrefresh",
            )

        assert tokens.access_token == "newtok"
        assert tokens.refresh_token == "newrefresh"

    @pytest.mark.asyncio
    async def test_refresh_token_preserved_when_provider_omits_new_one(self) -> None:
        resp = _json_response(200, {"access_token": "newtok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            tokens = await refresh_access_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                refresh_token="oldrefresh",
            )

        assert tokens.refresh_token == "oldrefresh"

    @pytest.mark.asyncio
    async def test_invalid_grant_raises_permanent_error(self) -> None:
        resp = _json_response(400, {"error": "invalid_grant", "error_description": "refresh token is invalid"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            with pytest.raises(MCPRefreshTokenInvalidError):
                await refresh_access_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    refresh_token="badrefresh",
                )

    @pytest.mark.asyncio
    async def test_generic_error_raises_recoverable_oauth_error(self) -> None:
        resp = _json_response(500, {"error": "server_error"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            with pytest.raises(MCPOAuthError) as exc_info:
                await refresh_access_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    refresh_token="sometoken",
                )
            assert not isinstance(exc_info.value, MCPRefreshTokenInvalidError)

    @pytest.mark.asyncio
    async def test_missing_access_token_in_refresh_response_raises(self) -> None:
        resp = _json_response(200, {"token_type": "Bearer"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            with pytest.raises(MCPOAuthError, match="access_token"):
                await refresh_access_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    refresh_token="sometoken",
                )

    @pytest.mark.asyncio
    async def test_refresh_omits_client_secret_when_none(self) -> None:
        resp = _json_response(200, {"access_token": "newtok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _mock_async_client(inner):
            await refresh_access_token(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret=None,
                refresh_token="oldrefresh",
            )

        sent_data = inner.post.await_args.kwargs["data"]
        assert "client_secret" not in sent_data
        assert sent_data["client_id"] == "cid"
