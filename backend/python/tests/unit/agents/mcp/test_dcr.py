"""Unit tests for app.agents.mcp.dcr.

Covers all six public functions:
  generate_pkce_pair, _derive_code_challenge, build_dcr_authorization_url,
  discover_auth_metadata, register_dcr_client, exchange_dcr_code,
  refresh_dcr_token.

All httpx network calls are mocked via unittest.mock so no real network
traffic is generated.
"""

import base64
import hashlib
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _make_http_client(response: MagicMock) -> AsyncMock:
    """Build a mock httpx.AsyncClient context manager that returns *response*."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _make_network_error_client(method: str = "post") -> AsyncMock:
    """Build a mock httpx.AsyncClient that raises ConnectError on *method*."""
    client = AsyncMock()
    err = httpx.ConnectError("connection failed")
    if method == "get":
        client.get = AsyncMock(side_effect=err)
    else:
        client.post = AsyncMock(side_effect=err)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# generate_pkce_pair
# ---------------------------------------------------------------------------


class TestGeneratePkcePair:
    def test_returns_two_non_empty_strings(self):
        from app.agents.mcp.dcr import generate_pkce_pair

        verifier, challenge = generate_pkce_pair()

        assert isinstance(verifier, str) and len(verifier) > 0
        assert isinstance(challenge, str) and len(challenge) > 0

    def test_verifier_and_challenge_differ(self):
        from app.agents.mcp.dcr import generate_pkce_pair

        verifier, challenge = generate_pkce_pair()

        assert verifier != challenge

    def test_challenge_is_s256_of_verifier(self):
        from app.agents.mcp.dcr import generate_pkce_pair

        verifier, challenge = generate_pkce_pair()
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        assert challenge == expected

    def test_challenge_has_no_padding(self):
        from app.agents.mcp.dcr import generate_pkce_pair

        _, challenge = generate_pkce_pair()

        assert "=" not in challenge

    def test_generates_unique_pairs(self):
        from app.agents.mcp.dcr import generate_pkce_pair

        verifiers = {generate_pkce_pair()[0] for _ in range(5)}

        assert len(verifiers) == 5  # all unique


# ---------------------------------------------------------------------------
# _derive_code_challenge
# ---------------------------------------------------------------------------


class TestDeriveCodeChallenge:
    def test_known_input(self):
        from app.agents.mcp.dcr import _derive_code_challenge

        verifier = "test_verifier_string"
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

        assert _derive_code_challenge(verifier) == expected

    def test_no_padding_in_result(self):
        from app.agents.mcp.dcr import _derive_code_challenge

        result = _derive_code_challenge("some_arbitrary_verifier_value")

        assert "=" not in result

    def test_is_url_safe_base64(self):
        from app.agents.mcp.dcr import _derive_code_challenge

        result = _derive_code_challenge("verifier")

        # Must only contain URL-safe base64 chars (no '+' or '/')
        assert "+" not in result
        assert "/" not in result


# ---------------------------------------------------------------------------
# build_dcr_authorization_url
# ---------------------------------------------------------------------------


class TestBuildDcrAuthorizationUrl:
    def _parse_params(self, url: str) -> dict:
        return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))

    def test_contains_required_params(self):
        from app.agents.mcp.dcr import build_dcr_authorization_url

        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="client-abc",
            redirect_uri="https://app.example.com/callback",
            scopes=["read", "write"],
            state="csrf-state-xyz",
            code_verifier="verifier-abc",
        )
        params = self._parse_params(url)

        assert params["response_type"] == "code"
        assert params["client_id"] == "client-abc"
        assert params["redirect_uri"] == "https://app.example.com/callback"
        assert params["state"] == "csrf-state-xyz"
        assert params["code_challenge_method"] == "S256"
        assert "code_challenge" in params
        assert "scope" in params

    def test_scope_is_space_joined(self):
        from app.agents.mcp.dcr import build_dcr_authorization_url

        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="c",
            redirect_uri="https://app/cb",
            scopes=["read", "write", "admin"],
            state="s",
            code_verifier="v",
        )
        params = self._parse_params(url)

        assert params["scope"] == "read write admin"

    def test_adds_prompt_consent_for_offline_access(self):
        from app.agents.mcp.dcr import build_dcr_authorization_url

        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="c",
            redirect_uri="https://app/cb",
            scopes=["read", "offline_access"],
            state="s",
            code_verifier="v",
        )
        params = self._parse_params(url)

        assert params.get("prompt") == "consent"

    def test_no_prompt_without_offline_access(self):
        from app.agents.mcp.dcr import build_dcr_authorization_url

        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="c",
            redirect_uri="https://app/cb",
            scopes=["read", "write"],
            state="s",
            code_verifier="v",
        )
        params = self._parse_params(url)

        assert "prompt" not in params

    def test_code_challenge_matches_verifier(self):
        from app.agents.mcp.dcr import _derive_code_challenge, build_dcr_authorization_url

        verifier = "my_test_verifier_string"
        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="c",
            redirect_uri="https://app/cb",
            scopes=["read"],
            state="s",
            code_verifier=verifier,
        )
        params = self._parse_params(url)

        assert params["code_challenge"] == _derive_code_challenge(verifier)

    def test_base_url_preserved(self):
        from app.agents.mcp.dcr import build_dcr_authorization_url

        url = build_dcr_authorization_url(
            authorization_endpoint="https://auth.example.com/oauth/authorize",
            client_id="c",
            redirect_uri="https://app/cb",
            scopes=["read"],
            state="s",
            code_verifier="v",
        )

        assert url.startswith("https://auth.example.com/oauth/authorize?")


# ---------------------------------------------------------------------------
# discover_auth_metadata
# ---------------------------------------------------------------------------


class TestDiscoverAuthMetadata:
    async def test_returns_metadata_when_registration_endpoint_present(self):
        from app.agents.mcp.dcr import discover_auth_metadata

        data = {"registration_endpoint": "https://auth.example.com/register", "issuer": "x"}
        mock_client = _make_http_client(_make_response(200, data))

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await discover_auth_metadata("https://mcp.example.com/v1/mcp")

        assert result is not None
        assert result["registration_endpoint"] == "https://auth.example.com/register"

    async def test_returns_none_when_no_registration_endpoint(self):
        from app.agents.mcp.dcr import discover_auth_metadata

        mock_client = _make_http_client(_make_response(200, {"issuer": "x"}))

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await discover_auth_metadata("https://mcp.example.com/mcp")

        assert result is None

    async def test_returns_none_on_404(self):
        from app.agents.mcp.dcr import discover_auth_metadata

        mock_client = _make_http_client(_make_response(404))

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await discover_auth_metadata("https://mcp.example.com/mcp")

        assert result is None

    async def test_returns_none_on_network_error(self):
        from app.agents.mcp.dcr import discover_auth_metadata

        mock_client = _make_network_error_client("get")

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await discover_auth_metadata("https://mcp.example.com/mcp")

        assert result is None

    async def test_probes_server_root_not_full_path(self):
        """The well-known URL must be constructed from the server root, not the full path."""
        from app.agents.mcp.dcr import _WELL_KNOWN_PATH, discover_auth_metadata

        data = {"registration_endpoint": "https://mcp.atlassian.com/register"}
        mock_client = _make_http_client(_make_response(200, data))

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            await discover_auth_metadata("https://mcp.atlassian.com/v1/jira")

        call_url = mock_client.get.call_args[0][0]
        assert call_url == f"https://mcp.atlassian.com{_WELL_KNOWN_PATH}"

    async def test_returns_none_on_non_dict_response(self):
        from app.agents.mcp.dcr import discover_auth_metadata

        # json() returns a list — not a dict → treated as no endpoint
        mock_resp = _make_response(200)
        mock_resp.json = MagicMock(return_value=[])
        mock_client = _make_http_client(mock_resp)

        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await discover_auth_metadata("https://mcp.example.com/mcp")

        assert result is None


# ---------------------------------------------------------------------------
# register_dcr_client
# ---------------------------------------------------------------------------


class TestRegisterDcrClient:
    async def test_success_201(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_http_client(
            _make_response(201, {"client_id": "cid-123", "client_secret": "sec-abc"})
        )
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await register_dcr_client(
                registration_endpoint="https://auth.example.com/register",
                redirect_uri="https://app.example.com/callback",
                scopes=["read", "write"],
            )

        assert result["client_id"] == "cid-123"
        assert result["client_secret"] == "sec-abc"

    async def test_success_200(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_http_client(_make_response(200, {"client_id": "cid-200"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await register_dcr_client(
                registration_endpoint="https://auth.example.com/register",
                redirect_uri="https://app/cb",
                scopes=["read"],
            )

        assert result["client_id"] == "cid-200"

    async def test_http_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_http_client(_make_response(400, text="Bad request"))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="DCR registration failed"):
                await register_dcr_client(
                    registration_endpoint="https://auth.example.com/register",
                    redirect_uri="https://app/cb",
                    scopes=["read"],
                )

    async def test_missing_client_id_raises_runtime_error(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_http_client(_make_response(200, {"other_field": "value"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="missing client_id"):
                await register_dcr_client(
                    registration_endpoint="https://auth.example.com/register",
                    redirect_uri="https://app/cb",
                    scopes=["read"],
                )

    async def test_network_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_network_error_client("post")
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="network error"):
                await register_dcr_client(
                    registration_endpoint="https://auth.example.com/register",
                    redirect_uri="https://app/cb",
                    scopes=["read"],
                )

    async def test_payload_includes_scopes_and_redirect(self):
        from app.agents.mcp.dcr import register_dcr_client

        mock_client = _make_http_client(_make_response(201, {"client_id": "c"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            await register_dcr_client(
                registration_endpoint="https://auth.example.com/register",
                redirect_uri="https://app/cb",
                scopes=["read", "write"],
                client_name="TestApp",
            )

        posted_json = mock_client.post.call_args.kwargs["json"]
        assert posted_json["scope"] == "read write"
        assert posted_json["redirect_uris"] == ["https://app/cb"]
        assert posted_json["client_name"] == "TestApp"


# ---------------------------------------------------------------------------
# exchange_dcr_code
# ---------------------------------------------------------------------------


class TestExchangeDcrCode:
    async def test_success(self):
        from app.agents.mcp.dcr import exchange_dcr_code

        mock_client = _make_http_client(
            _make_response(200, {"access_token": "at-xyz", "refresh_token": "rt-abc"})
        )
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await exchange_dcr_code(
                token_endpoint="https://auth.example.com/token",
                code="auth-code-123",
                redirect_uri="https://app/cb",
                client_id="cid",
                client_secret="sec",
                code_verifier="verifier",
            )

        assert result["access_token"] == "at-xyz"
        assert result["refresh_token"] == "rt-abc"

    async def test_http_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import exchange_dcr_code

        mock_client = _make_http_client(_make_response(400, text="invalid_grant"))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="DCR token exchange failed"):
                await exchange_dcr_code(
                    token_endpoint="https://auth.example.com/token",
                    code="code",
                    redirect_uri="https://app/cb",
                    client_id="cid",
                    client_secret="sec",
                    code_verifier="v",
                )

    async def test_missing_access_token_raises(self):
        from app.agents.mcp.dcr import exchange_dcr_code

        mock_client = _make_http_client(_make_response(200, {"token_type": "Bearer"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="no access_token"):
                await exchange_dcr_code(
                    token_endpoint="https://auth.example.com/token",
                    code="code",
                    redirect_uri="https://app/cb",
                    client_id="cid",
                    client_secret="sec",
                    code_verifier="v",
                )

    async def test_network_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import exchange_dcr_code

        mock_client = _make_network_error_client("post")
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="network error"):
                await exchange_dcr_code(
                    token_endpoint="https://auth.example.com/token",
                    code="code",
                    redirect_uri="https://app/cb",
                    client_id="cid",
                    client_secret="sec",
                    code_verifier="v",
                )

    async def test_code_verifier_included_in_payload(self):
        from app.agents.mcp.dcr import exchange_dcr_code

        mock_client = _make_http_client(_make_response(200, {"access_token": "at"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            await exchange_dcr_code(
                token_endpoint="https://auth.example.com/token",
                code="auth-code",
                redirect_uri="https://app/cb",
                client_id="cid",
                client_secret="",
                code_verifier="my-verifier",
            )

        posted_data = mock_client.post.call_args.kwargs["data"]
        assert posted_data["code_verifier"] == "my-verifier"
        assert posted_data["grant_type"] == "authorization_code"


# ---------------------------------------------------------------------------
# refresh_dcr_token
# ---------------------------------------------------------------------------


class TestRefreshDcrToken:
    async def test_success(self):
        from app.agents.mcp.dcr import refresh_dcr_token

        mock_client = _make_http_client(
            _make_response(200, {"access_token": "new-at", "refresh_token": "new-rt"})
        )
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await refresh_dcr_token(
                token_endpoint="https://auth.example.com/token",
                refresh_token="old-rt",
                client_id="cid",
                client_secret="sec",
            )

        assert result["access_token"] == "new-at"

    async def test_http_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import refresh_dcr_token

        mock_client = _make_http_client(_make_response(401, text="token expired"))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="DCR token refresh failed"):
                await refresh_dcr_token(
                    token_endpoint="https://auth.example.com/token",
                    refresh_token="rt",
                    client_id="cid",
                    client_secret="sec",
                )

    async def test_missing_access_token_raises(self):
        from app.agents.mcp.dcr import refresh_dcr_token

        mock_client = _make_http_client(_make_response(200, {"token_type": "Bearer"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="no access_token"):
                await refresh_dcr_token(
                    token_endpoint="https://auth.example.com/token",
                    refresh_token="rt",
                    client_id="cid",
                    client_secret="sec",
                )

    async def test_empty_client_secret_not_sent(self):
        """When client_secret is empty it must be omitted from the POST payload."""
        from app.agents.mcp.dcr import refresh_dcr_token

        mock_client = _make_http_client(_make_response(200, {"access_token": "at"}))
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            result = await refresh_dcr_token(
                token_endpoint="https://auth.example.com/token",
                refresh_token="rt",
                client_id="cid",
                client_secret="",
            )

        assert result["access_token"] == "at"
        posted_data = mock_client.post.call_args.kwargs["data"]
        assert "client_secret" not in posted_data

    async def test_network_error_raises_runtime_error(self):
        from app.agents.mcp.dcr import refresh_dcr_token

        mock_client = _make_network_error_client("post")
        with patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="network error"):
                await refresh_dcr_token(
                    token_endpoint="https://auth.example.com/token",
                    refresh_token="rt",
                    client_id="cid",
                    client_secret="sec",
                )
