"""Unit tests for app.agents.mcp.dcr."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.mcp.dcr import (
    DCRError,
    DiscoveryBlockedError,
    _authority_without_userinfo,
    _well_known_candidate_urls,
    assert_discovery_target_allowed,
    build_authorization_url,
    discover_oauth_metadata,
    generate_code_challenge,
    generate_code_verifier,
    generate_state,
    register_dynamic_client,
)
from app.utils.url_fetcher import FetchError as UrlFetchError


def _mock_async_client(inner: MagicMock):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.agents.mcp.dcr.httpx.AsyncClient", return_value=cm)


def _allow_all_discovery_targets():
    """Discovery hits real-looking hostnames (example.com, mcp.notion.com, ...); tests must
    not depend on actual DNS resolution, so the SSRF guard is stubbed to allow everything and
    exercised separately in `TestAssertDiscoveryTargetAllowed`."""
    return patch("app.agents.mcp.dcr.assert_discovery_target_allowed", AsyncMock(return_value=None))


def _resp(status_code: int, json_body: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


class TestPkceHelpers:
    def test_code_verifier_is_url_safe_and_unpadded(self) -> None:
        verifier = generate_code_verifier()
        assert "=" not in verifier
        assert len(verifier) > 40

    def test_code_challenge_is_deterministic_sha256(self) -> None:
        verifier = "fixed-test-verifier-value"
        challenge1 = generate_code_challenge(verifier)
        challenge2 = generate_code_challenge(verifier)
        assert challenge1 == challenge2
        assert "=" not in challenge1

    def test_different_verifiers_produce_different_challenges(self) -> None:
        assert generate_code_challenge("a") != generate_code_challenge("b")

    def test_generate_state_is_unique(self) -> None:
        assert generate_state() != generate_state()


class TestDiscoverOAuthMetadata:
    """Exercises the RFC 9728 protected-resource -> RFC 8414/OIDC AS-metadata chain."""

    @pytest.mark.asyncio
    async def test_notion_shaped_server_reports_dcr_supported(self) -> None:
        """No protected-resource document; the MCP host is its own AS and advertises a
        registration_endpoint directly — the common case for Notion/Atlassian-style servers."""
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(404),  # protected-resource root
                _resp(  # AS metadata root
                    200,
                    {
                        "authorization_endpoint": "https://mcp.notion.com/authorize",
                        "token_endpoint": "https://mcp.notion.com/token",
                        "registration_endpoint": "https://mcp.notion.com/register",
                        "scopes_supported": ["default"],
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.notion.com")

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://mcp.notion.com/authorize"
        assert metadata.token_endpoint == "https://mcp.notion.com/token"
        assert metadata.registration_endpoint == "https://mcp.notion.com/register"
        assert metadata.supports_dcr is True
        assert inner.get.await_args_list == [
            (("https://mcp.notion.com/.well-known/oauth-protected-resource",),),
            (("https://mcp.notion.com/.well-known/oauth-authorization-server",),),
        ]

    @pytest.mark.asyncio
    async def test_github_shaped_server_delegates_to_different_host_without_dcr(self) -> None:
        """A protected-resource document on a path-inserted well-known points at a
        completely different authorization-server host (github.com), whose metadata has no
        registration_endpoint — DCR is genuinely unsupported."""
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(  # protected-resource, path-inserted candidate succeeds immediately
                    200, {"authorization_servers": ["https://github.com/login/oauth"]}
                ),
                _resp(404),  # AS metadata, path-inserted candidate for the AS issuer
                _resp(  # AS metadata, root candidate
                    200,
                    {
                        "authorization_endpoint": "https://github.com/login/oauth/authorize",
                        "token_endpoint": "https://github.com/login/oauth/access_token",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://api.githubcopilot.com/mcp")

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://github.com/login/oauth/authorize"
        assert metadata.registration_endpoint is None
        assert metadata.supports_dcr is False
        assert inner.get.await_args_list == [
            (("https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp",),),
            (("https://github.com/.well-known/oauth-authorization-server/login/oauth",),),
            (("https://github.com/.well-known/oauth-authorization-server",),),
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_authority_root_when_path_insertion_fails(self) -> None:
        """Servers like Atlassian's only serve metadata at the bare root even though their MCP
        endpoint lives under a path — the client must fall back to it."""
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(404),  # protected-resource, path-inserted
                _resp(404),  # protected-resource, root
                _resp(401),  # AS metadata, path-inserted
                _resp(  # AS metadata, root
                    200,
                    {
                        "authorization_endpoint": "https://mcp.atlassian.com/v1/authorize",
                        "registration_endpoint": "https://cf.mcp.atlassian.com/v1/register",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.atlassian.com/v1/sse")

        assert metadata is not None
        assert metadata.registration_endpoint == "https://cf.mcp.atlassian.com/v1/register"
        assert inner.get.await_args_list == [
            (("https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/sse",),),
            (("https://mcp.atlassian.com/.well-known/oauth-protected-resource",),),
            (("https://mcp.atlassian.com/.well-known/oauth-authorization-server/v1/sse",),),
            (("https://mcp.atlassian.com/.well-known/oauth-authorization-server",),),
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_oidc_discovery_when_oauth_metadata_missing(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(404),  # protected-resource root
                _resp(404),  # AS metadata (oauth-authorization-server) root
                _resp(  # AS metadata (openid-configuration) root
                    200,
                    {
                        "authorization_endpoint": "https://example.com/authorize",
                        "token_endpoint": "https://example.com/token",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://example.com/authorize"

    @pytest.mark.asyncio
    async def test_returns_none_when_all_candidates_fail(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(return_value=_resp(404))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none_rather_than_raising(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(side_effect=RuntimeError("network down"))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is None

    @pytest.mark.asyncio
    async def test_blocked_discovery_target_returns_none_rather_than_raising(self) -> None:
        """A caller-supplied URL that resolves to a private/internal address must not surface
        as an error to the authorize flow — discovery is best-effort."""
        inner = MagicMock()
        inner.get = AsyncMock(return_value=_resp(200, {}))

        with patch(
            "app.agents.mcp.dcr.assert_discovery_target_allowed",
            AsyncMock(side_effect=DiscoveryBlockedError("blocked")),
        ), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://169.254.169.254")

        assert metadata is None
        inner.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outer_timeout_returns_none_rather_than_raising(self) -> None:
        with patch(
            "app.agents.mcp.dcr._discover_oauth_metadata_inner",
            AsyncMock(side_effect=RuntimeError("discovery exploded")),
        ):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is None

    @pytest.mark.asyncio
    async def test_ignores_non_list_authorization_servers(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(200, {"authorization_servers": "https://not-a-list.example.com"}),
                _resp(
                    200,
                    {
                        "authorization_endpoint": "https://example.com/authorize",
                        "token_endpoint": "https://example.com/token",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://example.com/authorize"
        # Fell back to the MCP host itself as the AS candidate.
        assert inner.get.await_args_list == [
            (("https://example.com/.well-known/oauth-protected-resource",),),
            (("https://example.com/.well-known/oauth-authorization-server",),),
        ]

    @pytest.mark.asyncio
    async def test_ignores_empty_or_non_string_authorization_server_entries(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(
            side_effect=[
                _resp(200, {"authorization_servers": [None, "", 123]}),
                _resp(
                    200,
                    {
                        "authorization_endpoint": "https://example.com/authorize",
                        "token_endpoint": "https://example.com/token",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com")

        assert metadata is not None
        assert metadata.issuer == "https://example.com"


class TestAuthorityHelpers:
    def test_authority_without_userinfo_returns_none_for_invalid_url(self) -> None:
        assert _authority_without_userinfo("not-a-url") is None
        assert _authority_without_userinfo("") is None

    def test_authority_without_userinfo_strips_credentials(self) -> None:
        assert (
            _authority_without_userinfo("https://user:pass@example.com:8443/path")
            == "https://example.com:8443"
        )

    def test_well_known_candidate_urls_empty_when_authority_missing(self) -> None:
        assert _well_known_candidate_urls("not-a-url", "/.well-known/oauth-authorization-server") == []


class TestAssertDiscoveryTargetAllowed:
    @pytest.mark.asyncio
    async def test_allows_public_https_url(self) -> None:
        with patch("app.agents.mcp.dcr.validate_public_http_url", return_value=None):
            await assert_discovery_target_allowed("https://example.com/.well-known/oauth-authorization-server")

    @pytest.mark.asyncio
    async def test_wraps_fetch_error_as_discovery_blocked_error(self) -> None:
        with patch(
            "app.agents.mcp.dcr.validate_public_http_url",
            side_effect=UrlFetchError("Blocked unsafe URL address: 127.0.0.1"),
        ):
            with pytest.raises(DiscoveryBlockedError):
                await assert_discovery_target_allowed("http://127.0.0.1/register")

    @pytest.mark.asyncio
    async def test_rejects_loopback_literal_without_network_access(self) -> None:
        """No mocking needed: a literal loopback IP is rejected by parsing alone, so this
        exercises the real SSRF guard without depending on DNS."""
        with pytest.raises(DiscoveryBlockedError):
            await assert_discovery_target_allowed("http://127.0.0.1:8080/register")

    @pytest.mark.asyncio
    async def test_rejects_link_local_metadata_address_without_network_access(self) -> None:
        with pytest.raises(DiscoveryBlockedError):
            await assert_discovery_target_allowed("http://169.254.169.254/latest/meta-data")


class TestRegisterDynamicClient:
    @pytest.mark.asyncio
    async def test_successful_registration_returns_dcr_client(self) -> None:
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {
            "client_id": "cid-123",
            "client_secret": "csecret",
            "registration_access_token": "rat",
            "registration_client_uri": "https://example.com/register/cid-123",
        }
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            client = await register_dynamic_client(
                registration_endpoint="https://example.com/register",
                redirect_uri="https://app.example.com/callback",
                authorization_url="https://example.com/authorize",
                token_url="https://example.com/token",
            )

        assert client.client_id == "cid-123"
        assert client.client_secret == "csecret"
        assert client.authorization_url == "https://example.com/authorize"
        assert client.token_url == "https://example.com/token"

    @pytest.mark.asyncio
    async def test_error_status_raises_dcr_error(self) -> None:
        resp = MagicMock()
        resp.status_code = 400
        resp.text = "bad request"
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            with pytest.raises(DCRError, match="400"):
                await register_dynamic_client(
                    registration_endpoint="https://example.com/register",
                    redirect_uri="https://app.example.com/callback",
                    authorization_url="https://example.com/authorize",
                    token_url="https://example.com/token",
                )

    @pytest.mark.asyncio
    async def test_missing_client_id_raises_dcr_error(self) -> None:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            with pytest.raises(DCRError, match="client_id"):
                await register_dynamic_client(
                    registration_endpoint="https://example.com/register",
                    redirect_uri="https://app.example.com/callback",
                    authorization_url="https://example.com/authorize",
                    token_url="https://example.com/token",
                )

    @pytest.mark.asyncio
    async def test_network_error_wrapped_as_dcr_error(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock(side_effect=RuntimeError("connection refused"))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            with pytest.raises(DCRError, match="connection refused"):
                await register_dynamic_client(
                    registration_endpoint="https://example.com/register",
                    redirect_uri="https://app.example.com/callback",
                    authorization_url="https://example.com/authorize",
                    token_url="https://example.com/token",
                )

    @pytest.mark.asyncio
    async def test_blocked_registration_target_raises_before_any_request(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock()

        with patch(
            "app.agents.mcp.dcr.assert_discovery_target_allowed",
            AsyncMock(side_effect=DiscoveryBlockedError("blocked")),
        ), _mock_async_client(inner):
            with pytest.raises(DiscoveryBlockedError):
                await register_dynamic_client(
                    registration_endpoint="http://169.254.169.254/register",
                    redirect_uri="https://app.example.com/callback",
                    authorization_url="https://example.com/authorize",
                    token_url="https://example.com/token",
                )
        inner.post.assert_not_awaited()


class TestBuildAuthorizationUrl:
    def test_includes_required_params(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
        )
        assert url.startswith("https://example.com/authorize?")
        assert "client_id=cid" in url
        assert "state=state123" in url
        assert "response_type=code" in url

    def test_includes_scopes_when_provided(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
            scopes=["read", "write"],
        )
        assert "scope=read+write" in url or "scope=read%20write" in url

    def test_omits_scope_when_not_provided(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
        )
        assert "scope=" not in url

    def test_includes_pkce_challenge_when_provided(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
            code_challenge="challenge123",
        )
        assert "code_challenge=challenge123" in url
        assert "code_challenge_method=S256" in url

    def test_omits_pkce_when_not_provided(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
        )
        assert "code_challenge" not in url
