"""Unit tests for app.agents.mcp.oauth_client."""
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.agents.mcp.oauth_client import (
    MCPMalformedTokenError,
    MCPOAuthError,
    MCPRefreshTokenInvalidError,
    _parse_form_encoded,
    exchange_code_for_token,
    parse_tokens,
    refresh_access_token,
    request_client_credentials_token,
)
from app.utils.oauth_errors import MAX_TOKEN_LIFETIME_S
from tests.support.mcp_oauth_fakes import json_response, mock_async_client


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
    async def testjson_response_parsed(self) -> None:
        resp = json_response(200, {"access_token": "tok", "token_type": "Bearer", "expires_in": 3600})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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

        with mock_async_client(inner):
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
        resp = json_response(200, {"token_type": "Bearer"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(400, {"error": "invalid_request"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(200, {"access_token": "tok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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

        with mock_async_client(inner):
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

        with mock_async_client(inner):
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

        with mock_async_client(inner):
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
        resp = json_response(200, {"access_token": "newtok", "refresh_token": "newrefresh", "expires_in": 3600})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(200, {"access_token": "newtok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(400, {"error": "invalid_grant", "error_description": "refresh token is invalid"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(500, {"error": "server_error"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(200, {"token_type": "Bearer"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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
        resp = json_response(200, {"access_token": "newtok"})
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with mock_async_client(inner):
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


class TestATokenEndpointThatNeverAnswers:
    """Refused, timed out or cut off before an answer: the module's own error, marked as an outage."""

    @pytest.mark.parametrize("raised", [
        httpx.ConnectError("All connection attempts failed"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ], ids=["refused", "connect-timeout", "read-timeout", "cut-off"])
    @pytest.mark.parametrize("request_token", [
        lambda: exchange_code_for_token(token_url="https://auth.example.com/token", client_id="cid", client_secret="cs",
                                        code="c", redirect_uri="https://app/cb", allow_private=False),
        lambda: refresh_access_token(token_url="https://auth.example.com/token", client_id="cid", client_secret="cs",
                                     refresh_token="r", allow_private=False),
        lambda: request_client_credentials_token("https://auth.example.com/token", "cid", "cs", allow_private=False),
    ], ids=["code-exchange", "refresh", "client-credentials"])
    async def test_it_is_an_unanswered_oauth_error(self, raised: Exception, request_token) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(side_effect=raised)
        with mock_async_client(inner), pytest.raises(MCPOAuthError) as caught:
            await request_token()

        error = caught.value
        assert type(error) is MCPOAuthError and error.unanswered
        assert (error.status, error.error_code, error.rejected_client) == (None, None, False)
        assert error.__cause__ is raised
        # The type, never the text or the URL.
        assert str(error) == f"OAuth token request failed before an answer ({type(raised).__name__})"

    async def test_an_answer_is_not_unanswered(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=json_response(503, {"error": "temporarily_unavailable"}))
        with mock_async_client(inner), pytest.raises(MCPOAuthError) as caught:
            await refresh_access_token(token_url="https://auth.example.com/token", client_id="cid", client_secret=None,
                                       refresh_token="r", allow_private=False)
        assert caught.value.status == 503 and not caught.value.unanswered

    async def test_a_url_the_guard_refuses_is_not_unanswered(self) -> None:
        with pytest.raises(MCPOAuthError) as caught:
            await refresh_access_token(token_url="https://10.0.0.8/token", client_id="cid", client_secret=None,
                                       refresh_token="r", allow_private=False)
        assert not caught.value.unanswered

    def test_an_error_raised_with_just_a_message_is_not_unanswered(self) -> None:
        assert not MCPOAuthError("OAuth refresh response missing access_token").unanswered

    async def test_our_own_bug_is_not_wrapped_as_a_token_failure(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(side_effect=RuntimeError("ours"))
        with mock_async_client(inner), pytest.raises(RuntimeError, match="^ours$"):
            await refresh_access_token(token_url="https://auth.example.com/token", client_id="cid", client_secret=None,
                                       refresh_token="r", allow_private=False)


class TestErrorsInTheResponseBody:
    """RFC 6749 errors decide, whatever the HTTP status: GitHub sends them with 200."""

    @staticmethod
    async def _refresh(resp: MagicMock) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)
        with mock_async_client(inner):
            await refresh_access_token(
                allow_private=True, token_url="https://example.com/token",
                client_id="cid", client_secret="csec", refresh_token="r",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resp", [
        json_response(200, {"error": "bad_refresh_token", "error_description": "The refresh token passed is incorrect or expired."}),
        _form_response(200, "error=bad_refresh_token&error_description=expired"),
        json_response(400, {"error": "interaction_required", "error_description": "AADSTS50076: use MFA"}),
        json_response(400, {"error": "invalid_scope"}),
        json_response(400, {"error": "invalid_request"}),
    ])
    async def test_a_rejection_a_retry_cannot_fix_is_permanent(self, resp: MagicMock) -> None:
        with pytest.raises(MCPRefreshTokenInvalidError) as exc_info:
            await self._refresh(resp)
        assert exc_info.value.status == resp.status_code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resp", [
        json_response(401, {"error": "invalid_client"}),
        _form_response(200, "error=incorrect_client_credentials"),
        json_response(400, {"error": "unauthorized_client"}),
    ])
    async def test_a_refused_client_is_a_misconfiguration_not_a_rejected_refresh_token(self, resp: MagicMock) -> None:
        with pytest.raises(MCPOAuthError) as exc_info:
            await self._refresh(resp)
        assert type(exc_info.value) is MCPOAuthError
        assert (exc_info.value.status, exc_info.value.deauth_reason) == (resp.status_code, "oauth_client_rejected")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resp", [
        json_response(503, {"error": "temporarily_unavailable"}),
        json_response(200, {"error": "server_error"}),
        json_response(503, {"error": "interaction_required"}),
        json_response(429, {"error": "invalid_grant"}),
        json_response(500, {"error": "login_required"}),
    ])
    async def test_a_provider_outage_can_be_retried(self, resp: MagicMock) -> None:
        with pytest.raises(MCPOAuthError) as exc_info:
            await self._refresh(resp)
        assert not isinstance(exc_info.value, MCPRefreshTokenInvalidError)
        assert exc_info.value.status == resp.status_code

    @pytest.mark.asyncio
    async def test_the_message_names_the_error_not_the_raw_body(self) -> None:
        resp = json_response(400, {"error": "invalid_grant", "error_description": "expired", "trace": "x" * 5000})
        with pytest.raises(MCPRefreshTokenInvalidError) as exc_info:
            await self._refresh(resp)
        assert str(exc_info.value) == "OAuth token request rejected (400): invalid_grant: expired"

    @pytest.mark.asyncio
    async def test_a_code_exchange_error_with_status_200_is_an_error(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=_form_response(200, "error=bad_verification_code&error_description=bad"))
        with mock_async_client(inner):
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
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a", "refresh_token": "r"}))
        with mock_async_client(inner):
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
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a2"}))
        with mock_async_client(inner):
            tokens = await refresh_access_token(
                allow_private=True, token_url="https://auth.example.com/token", client_id="cid",
                client_secret=None, refresh_token="r", resource="https://mcp.example.com/mcp",
            )

        assert inner.post.await_args.kwargs["data"]["resource"] == "https://mcp.example.com/mcp"
        assert tokens.resource == "https://mcp.example.com/mcp"

    @pytest.mark.asyncio
    async def test_without_one_nothing_is_added(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a2"}))
        with mock_async_client(inner):
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
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a", "refresh_token": "r"}))
        with mock_async_client(inner):
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
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a2"}))
        with mock_async_client(inner):
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
        with mock_async_client(inner), pytest.raises(MCPOAuthError) as exc_info:
            await refresh_access_token(
                allow_private=True, token_url="https://example.com/token",
                client_id="cid", client_secret="csec", refresh_token="r",
            )
        return exc_info.value

    @pytest.mark.asyncio
    async def test_an_unknown_client_is_reported_as_such(self) -> None:
        error = await self._refresh_error(json_response(401, {"error": "invalid_client"}))
        assert error.error_code == "invalid_client"
        assert error.rejected_client is True

    @pytest.mark.asyncio
    async def test_the_code_is_matched_whatever_its_case(self) -> None:
        error = await self._refresh_error(_form_response(200, "error=INVALID_CLIENT"))
        assert error.rejected_client is True

    @pytest.mark.asyncio
    async def test_a_rejected_grant_is_not_a_rejected_client(self) -> None:
        error = await self._refresh_error(json_response(400, {"error": "invalid_grant"}))
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


class TestClientCredentialsToken:
    """RFC 6749 §4.4: API Toolsets get tokens for the client itself, with no user."""

    @staticmethod
    async def _request(body: dict, **kwargs: object) -> tuple[object, dict]:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=json_response(200, body))
        with mock_async_client(inner):
            tokens = await request_client_credentials_token(
                "https://auth.example.com/token", "cid", "csec", allow_private=True, **kwargs,
            )
        return tokens, inner.post.await_args.kwargs

    @pytest.mark.asyncio
    async def test_asks_for_the_client_credentials_grant_with_the_scopes(self) -> None:
        tokens, sent = await self._request({"access_token": "a", "expires_in": 60}, scopes=["read", "write"])

        assert sent["data"] == {"grant_type": "client_credentials", "client_id": "cid", "client_secret": "csec", "scope": "read write"}
        assert tokens.access_token == "a" and tokens.expires_in == 60
        assert tokens.refresh_token is None
        assert (tokens.client_id, tokens.token_url) == ("cid", "https://auth.example.com/token")

    @pytest.mark.asyncio
    async def test_without_scopes_none_are_sent(self) -> None:
        _, sent = await self._request({"access_token": "a"})
        assert "scope" not in sent["data"]

    @pytest.mark.asyncio
    async def test_client_secret_basic(self) -> None:
        tokens, sent = await self._request({"access_token": "a"}, auth_method="client_secret_basic")
        assert sent["headers"]["Authorization"].startswith("Basic ")
        assert "client_secret" not in sent["data"]
        assert tokens.token_endpoint_auth_method == "client_secret_basic"

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self) -> None:
        with pytest.raises(MCPOAuthError, match="missing access_token"):
            await self._request({"token_type": "Bearer"})


class TestDeauthReason:
    """Whether an answer counts toward signing the credential out (token_refresh), and why."""

    @pytest.mark.parametrize(("error", "reason"), [
        (MCPRefreshTokenInvalidError("x", error_code="invalid_grant", status=400), "refresh_token_invalid"),
        (MCPRefreshTokenInvalidError("refresh token has expired"), "refresh_token_invalid"),  # a text marker, no code
        (MCPRefreshTokenInvalidError("x", error_code="INVALID_SCOPE", status=400), None),  # our request, refused
        (MCPRefreshTokenInvalidError("x", error_code="unsupported_grant_type", status=400), None),
        (MCPOAuthError("x", error_code="Invalid_Client", status=401), "oauth_client_rejected"),
        (MCPOAuthError("x", error_code="unauthorized_client", status=200), "oauth_client_rejected"),
        (MCPOAuthError("x", error_code="invalid_client", status=503), None),  # an overloaded endpoint
        (MCPOAuthError("x", error_code="invalid_client", status=429), None),
        (MCPOAuthError("x", status=400), None),
        (MCPOAuthError("no answer"), None),
    ])
    def test_the_reason(self, error: MCPOAuthError, reason: str | None) -> None:
        assert error.deauth_reason == reason


MARKER = "FIELD-MARKER-26E"


class TestParseTokens:
    """One parse for a provider's answer (snake_case) and a saved record (camelCase): a field of the wrong shape is
    MCPMalformedTokenError, named as OAuth spells it and never quoted."""

    @pytest.mark.parametrize(("data", "field"), [
        ({"expires_in": f"soon-{MARKER}"}, "expires_in"),
        ({"expires_in": [MARKER]}, "expires_in"),
        ({"expires_in": "3600.5"}, "expires_in"),
        ({"expires_in": float("nan")}, "expires_in"),
        ({"expires_in": float("inf")}, "expires_in"),
        ({"expires_in": 10**20}, "expires_in"),
        ({"expires_in": MAX_TOKEN_LIFETIME_S + 1}, "expires_in"),
        ({"expires_in": -(MAX_TOKEN_LIFETIME_S + 1)}, "expires_in"),
        ({"scope": [MARKER]}, "scope"),
        ({"token_type": {"t": MARKER}}, "token_type"),
        ({"refresh_token": 26}, "refresh_token"),
        ({"access_token": 26}, "access_token"),
        ({"access_token": None}, "access_token"),
        ({"created_at": f"bad-{MARKER}"}, "created_at"),
        ({"created_at": 10**15}, "created_at"),
        ({"expiresIn": f"soon-{MARKER}"}, "expires_in"),
        ({"createdAt": [MARKER]}, "created_at"),
        ({"scope": [MARKER], "tokenUrl": "https://t"}, "scope"),
    ])
    def test_a_field_of_the_wrong_shape_is_malformed_and_named(self, data: dict, field: str) -> None:
        saved_record = any(c.isupper() for key in data for c in key)  # camelCase keys
        base = {"accessToken": "a"} if saved_record else {"access_token": "a"}

        with pytest.raises(MCPMalformedTokenError) as raised:
            parse_tokens({**base, **data})

        e = raised.value
        assert e.field_name == field
        assert str(e) == f"OAuth token field '{field}' is malformed"
        assert MARKER not in str(e)
        assert e.__context__ is None and e.__cause__ is None  # pydantic's text quotes the value
        assert e.refresh_reason == "malformed_token" and isinstance(e, MCPOAuthError)
        assert (e.unanswered, e.rejected_client, e.deauth_reason) == (False, False, None)  # no outage, no strike

    def test_a_saved_record_without_an_access_token_names_it_as_oauth_does(self) -> None:
        with pytest.raises(MCPMalformedTokenError) as raised:
            parse_tokens({"refreshToken": "r"})
        assert raised.value.field_name == "access_token"

    @pytest.mark.parametrize(("expires_in", "kept"), [
        (3600, 3600), ("3600", 3600), (3600.0, 3600), (3599.5, 3599), (None, None), (-5, -5),
        (MAX_TOKEN_LIFETIME_S, MAX_TOKEN_LIFETIME_S), (-MAX_TOKEN_LIFETIME_S, -MAX_TOKEN_LIFETIME_S),
    ], ids=["int", "numeric_text", "whole_float", "fraction", "none", "negative", "the_bound", "minus_the_bound"])
    def test_a_well_formed_lifetime_is_kept(self, expires_in: object, kept: object) -> None:
        tokens = parse_tokens({"access_token": "a", "expires_in": expires_in})

        assert tokens.expires_in == kept
        tokens.is_expired  # noqa: B018 - created_at + the lifetime is inside datetime's range
        tokens.expires_at_epoch  # noqa: B018

    @pytest.mark.parametrize(("expires_in", "kept", "expired", "has_epoch"), [
        (0.5, 1, False, True), (-0.5, -1, True, True), (3599.5, 3599, False, True),
        (0, 0, False, False), (0.0, 0, False, False),  # exact zero: no expiry, as on main
    ], ids=["half_second", "minus_half_second", "fraction", "zero", "zero_float"])
    def test_a_lifetime_under_a_second_keeps_its_expiry(
        self, expires_in: float, kept: int, expired: bool, has_epoch: bool,
    ) -> None:
        # A fraction is dropped toward zero, but never to 0: the expiry properties read 0 as "no expiry".
        for data in ({"access_token": "a", "expires_in": expires_in}, {"accessToken": "a", "expiresIn": expires_in}):
            tokens = parse_tokens(data)

            assert tokens.expires_in == kept
            assert tokens.is_expired is expired
            assert (tokens.expires_at_epoch is not None) is has_epoch

    def test_snake_and_camel_case_give_the_same_tokens(self) -> None:
        snake = {"access_token": "a", "refresh_token": "r", "expires_in": 60, "token_url": "https://t", "created_at": "2026-01-01T00:00:00+00:00"}
        camel = {"accessToken": "a", "refreshToken": "r", "expiresIn": 60, "tokenUrl": "https://t", "createdAt": "2026-01-01T00:00:00+00:00"}
        before = dict(camel)

        assert parse_tokens(snake) == parse_tokens(camel)
        assert camel == before

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["exchange", "client_credentials", "refresh"])
    async def test_each_token_request_refuses_a_malformed_answer(self, call: str) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(return_value=json_response(200, {"access_token": "a", "expires_in": f"soon-{MARKER}"}))
        url = "https://auth.example.com/token"
        with mock_async_client(inner), pytest.raises(MCPMalformedTokenError) as raised:
            if call == "exchange":
                await exchange_code_for_token(url, "cid", "csec", "code", "https://app/cb", allow_private=True)
            elif call == "client_credentials":
                await request_client_credentials_token(url, "cid", "csec", allow_private=True)
            else:
                await refresh_access_token(url, "cid", "csec", "rt", allow_private=True)
        assert raised.value.field_name == "expires_in"
