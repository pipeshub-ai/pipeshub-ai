"""Unit tests for app.agents.mcp.oauth_client."""
import base64
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
                allow_private=True,
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
                allow_private=True,
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
                    allow_private=True,
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
                    allow_private=True,
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
                allow_private=True,
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
                allow_private=True,
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
                allow_private=True,
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
                allow_private=True,
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
                allow_private=True,
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
                allow_private=True,
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
                    allow_private=True,
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
                    allow_private=True,
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
                    allow_private=True,
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
                allow_private=True,
                token_url="https://example.com/token",
                client_id="cid",
                client_secret=None,
                refresh_token="oldrefresh",
            )

        sent_data = inner.post.await_args.kwargs["data"]
        assert "client_secret" not in sent_data
        assert sent_data["client_id"] == "cid"


class TestTokenRequestUrlGuard:
    """The token POST goes through the same SSRF guard as the MCP connection."""

    @pytest.mark.asyncio
    async def test_loopback_token_url_is_refused_before_sending(self) -> None:
        with pytest.raises(MCPOAuthError, match="not allowed"):
            await exchange_code_for_token(
                token_url="http://127.0.0.1:2379/v3/kv/range",
                client_id="cid", client_secret="csec", code="c", redirect_uri="https://app/cb",
                allow_private=True,
            )

    @pytest.mark.asyncio
    async def test_private_token_url_is_refused_when_private_networks_are_not_allowed(self) -> None:
        with pytest.raises(MCPOAuthError, match="public"):
            await refresh_access_token(
                token_url="https://10.0.0.8/token", client_id="cid", client_secret=None, refresh_token="r",
                allow_private=False,
            )

    @pytest.mark.asyncio
    async def test_public_token_url_is_sent_pinned_and_does_not_follow_redirects(self) -> None:
        import httpx

        sent: list[httpx.Request] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"}, text="moved")

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]),              patch("app.agents.mcp.url_guard.urllib.request.getproxies", return_value={}),              patch("app.agents.mcp.url_guard.httpx.AsyncHTTPTransport", lambda **_kw: httpx.MockTransport(_handler)):
            with pytest.raises(MCPOAuthError):
                await exchange_code_for_token(
                    token_url="https://auth.example.com/token",
                    client_id="cid", client_secret="csec", code="c", redirect_uri="https://app/cb",
                    allow_private=False,
                )

        assert len(sent) == 1
        assert sent[0].url.host == "93.184.216.34"
        assert sent[0].headers["host"] == "auth.example.com"


class TestErrorsInTheResponseBody:
    """RFC 6749 errors decide, whatever the HTTP status: GitHub sends them with 200."""

    @staticmethod
    async def _refresh(resp: MagicMock) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)
        with _mock_async_client(inner):
            await refresh_access_token(
                allow_private=True, token_url="https://example.com/token",
                client_id="cid", client_secret="csec", refresh_token="r",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resp", [
        _json_response(200, {"error": "bad_refresh_token", "error_description": "The refresh token passed is incorrect or expired."}),
        _form_response(200, "error=bad_refresh_token&error_description=expired"),
        _json_response(401, {"error": "invalid_client"}),
        _form_response(200, "error=incorrect_client_credentials"),
        _json_response(400, {"error": "unauthorized_client"}),
    ])
    async def test_a_rejection_a_retry_cannot_fix_is_permanent(self, resp: MagicMock) -> None:
        with pytest.raises(MCPRefreshTokenInvalidError):
            await self._refresh(resp)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resp", [
        _json_response(503, {"error": "temporarily_unavailable"}),
        _json_response(200, {"error": "server_error"}),
    ])
    async def test_a_provider_outage_can_be_retried(self, resp: MagicMock) -> None:
        with pytest.raises(MCPOAuthError) as exc_info:
            await self._refresh(resp)
        assert not isinstance(exc_info.value, MCPRefreshTokenInvalidError)

    @pytest.mark.asyncio
    async def test_the_message_names_the_error_not_the_raw_body(self) -> None:
        resp = _json_response(400, {"error": "invalid_grant", "error_description": "expired", "trace": "x" * 5000})
        with pytest.raises(MCPRefreshTokenInvalidError) as exc_info:
            await self._refresh(resp)
        assert str(exc_info.value) == "OAuth token request rejected (400): invalid_grant: expired"

    @pytest.mark.asyncio
    async def test_a_code_exchange_error_with_status_200_is_an_error(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_form_response(200, "error=bad_verification_code&error_description=bad"))
        with _mock_async_client(inner):
            with pytest.raises(MCPOAuthError, match="bad_verification_code"):
                await exchange_code_for_token(
                    allow_private=True, token_url="https://example.com/token", client_id="cid",
                    client_secret=None, code="c", redirect_uri="https://app.example.com/cb",
                )


class TestResourceIndicator:
    """RFC 8707: the token requests name the server the tokens are for."""

    @pytest.mark.asyncio
    async def test_the_code_exchange_sends_it_and_the_tokens_keep_it(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_json_response(200, {"access_token": "a", "refresh_token": "r"}))
        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                allow_private=True, token_url="https://auth.example.com/token", client_id="cid",
                client_secret=None, code="c", redirect_uri="https://app.example.com/cb",
                resource="https://mcp.example.com/mcp",
            )

        assert inner.post.await_args.kwargs["data"]["resource"] == "https://mcp.example.com/mcp"
        assert tokens.resource == "https://mcp.example.com/mcp"

    @pytest.mark.asyncio
    async def test_a_refresh_sends_it(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_json_response(200, {"access_token": "a2"}))
        with _mock_async_client(inner):
            tokens = await refresh_access_token(
                allow_private=True, token_url="https://auth.example.com/token", client_id="cid",
                client_secret=None, refresh_token="r", resource="https://mcp.example.com/mcp",
            )

        assert inner.post.await_args.kwargs["data"]["resource"] == "https://mcp.example.com/mcp"
        assert tokens.resource == "https://mcp.example.com/mcp"

    @pytest.mark.asyncio
    async def test_without_one_nothing_is_added(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_json_response(200, {"access_token": "a2"}))
        with _mock_async_client(inner):
            await refresh_access_token(
                allow_private=True, token_url="https://auth.example.com/token", client_id="cid",
                client_secret=None, refresh_token="r",
            )

        assert "resource" not in inner.post.await_args.kwargs["data"]


class TestTheClientProvesWhoItIsTheWayItWasRegistered:
    """RFC 6749 §2.3.1: the secret goes where the client was registered to send it."""

    @staticmethod
    async def _exchange(**kwargs: object) -> tuple[object, dict]:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_json_response(200, {"access_token": "a", "refresh_token": "r"}))
        with _mock_async_client(inner):
            tokens = await exchange_code_for_token(
                allow_private=True, token_url="https://auth.example.com/token", code="c",
                redirect_uri="https://app.example.com/cb", **kwargs,
            )
        return tokens, inner.post.await_args.kwargs

    @pytest.mark.asyncio
    async def test_client_secret_basic_sends_the_secret_in_the_header_only(self) -> None:
        tokens, sent = await self._exchange(client_id="id:1", client_secret="s p&c/+=", auth_method="client_secret_basic")

        scheme, encoded = sent["headers"]["Authorization"].split(" ")
        assert scheme == "Basic"
        # Each part is form-encoded before the join, so the ':' in the id can't split it.
        assert base64.b64decode(encoded).decode() == "id%3A1:s+p%26c%2F%2B%3D"
        assert "client_id" not in sent["data"]
        assert "client_secret" not in sent["data"]
        assert tokens.token_endpoint_auth_method == "client_secret_basic"

    @pytest.mark.asyncio
    async def test_by_default_the_secret_is_posted_as_before(self) -> None:
        tokens, sent = await self._exchange(client_id="cid", client_secret="csec")

        assert sent["data"]["client_id"] == "cid"
        assert sent["data"]["client_secret"] == "csec"
        assert "Authorization" not in sent["headers"]
        assert tokens.token_endpoint_auth_method == "client_secret_post"

    @pytest.mark.asyncio
    async def test_without_a_secret_only_the_client_id_is_sent(self) -> None:
        tokens, sent = await self._exchange(client_id="cid", client_secret=None, auth_method="client_secret_basic")

        assert sent["data"]["client_id"] == "cid"
        assert "client_secret" not in sent["data"]
        assert "Authorization" not in sent["headers"]
        assert tokens.token_endpoint_auth_method == "none"

    @pytest.mark.asyncio
    async def test_a_public_client_never_sends_a_secret(self) -> None:
        tokens, sent = await self._exchange(client_id="cid", client_secret="left-over", auth_method="none")

        assert "client_secret" not in sent["data"]
        assert "Authorization" not in sent["headers"]
        assert tokens.token_endpoint_auth_method == "none"

    @pytest.mark.asyncio
    async def test_a_refresh_authenticates_the_same_way_and_says_so(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_json_response(200, {"access_token": "a2"}))
        with _mock_async_client(inner):
            tokens = await refresh_access_token(
                allow_private=True, token_url="https://auth.example.com/token", client_id="cid",
                client_secret="csec", refresh_token="r", auth_method="client_secret_basic",
            )

        sent = inner.post.await_args.kwargs
        assert sent["headers"]["Authorization"] == "Basic " + base64.b64encode(b"cid:csec").decode()
        assert sent["data"] == {"grant_type": "refresh_token", "refresh_token": "r"}
        assert tokens.token_endpoint_auth_method == "client_secret_basic"


class TestTheProvidersErrorCodeIsKept:
    """The callers act on `invalid_client` (the provider forgot the client), so it has to survive."""

    @staticmethod
    async def _refresh_error(resp: MagicMock) -> MCPOAuthError:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)
        with _mock_async_client(inner), pytest.raises(MCPOAuthError) as exc_info:
            await refresh_access_token(
                allow_private=True, token_url="https://example.com/token",
                client_id="cid", client_secret="csec", refresh_token="r",
            )
        return exc_info.value

    @pytest.mark.asyncio
    async def test_an_unknown_client_is_reported_as_such(self) -> None:
        error = await self._refresh_error(_json_response(401, {"error": "invalid_client"}))
        assert error.error_code == "invalid_client"
        assert error.rejected_client is True

    @pytest.mark.asyncio
    async def test_the_code_is_matched_whatever_its_case(self) -> None:
        error = await self._refresh_error(_form_response(200, "error=INVALID_CLIENT"))
        assert error.rejected_client is True

    @pytest.mark.asyncio
    async def test_a_rejected_grant_is_not_a_rejected_client(self) -> None:
        error = await self._refresh_error(_json_response(400, {"error": "invalid_grant"}))
        assert error.error_code == "invalid_grant"
        assert error.rejected_client is False

    @pytest.mark.asyncio
    async def test_an_answer_without_a_code_has_none(self) -> None:
        error = await self._refresh_error(_form_response(502, "Bad Gateway"))
        assert error.error_code is None
        assert error.rejected_client is False

    def test_the_error_can_still_be_raised_with_just_a_message(self) -> None:
        error = MCPOAuthError("boom")
        assert str(error) == "boom"
        assert error.error_code is None
