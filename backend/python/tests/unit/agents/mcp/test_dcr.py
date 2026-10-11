"""Unit tests for app.agents.mcp.dcr."""
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.agents.mcp.dcr import (
    DCRError,
    DiscoveryBlockedError,
    InvalidOAuthEndpointError,
    _authority_without_userinfo,
    _authorization_server_metadata_urls,
    _discovery_client,
    _well_known_candidate_urls,
    assert_discovery_target_allowed,
    build_authorization_url,
    canonical_resource_uri,
    discover_oauth_metadata,
    generate_code_challenge,
    generate_code_verifier,
    generate_state,
    preferred_token_auth_method,
    register_dynamic_client,
)
from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.models import DiscoveredOAuthMetadata
from app.agents.mcp.url_guard import MCPUrlPolicy, PerOriginGuardedTransport
from app.agents.mcp.www_authenticate import bearer_challenge, challenge_scopes

PUBLIC_ONLY = MCPUrlPolicy(allow_private=False)


def _challenge(status_code: int, *values: str) -> MagicMock:
    """What the probe's unauthenticated request gets back (only status and headers are read)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers.get_list.return_value = list(values)
    resp.aclose = AsyncMock()
    return resp


def _mock_async_client(inner: MagicMock):
    # The 401 probe goes through `send`; unless a test says otherwise the server sends no challenge.
    if not isinstance(inner.send, AsyncMock):
        inner.send = AsyncMock(return_value=_challenge(404))
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
            metadata = await discover_oauth_metadata("https://mcp.notion.com", policy=PUBLIC_ONLY)

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
                _resp(404),  # RFC 8414, path inserted
                _resp(404),  # OpenID, path inserted
                _resp(404),  # OpenID, path appended
                _resp(404),  # RFC 8414, path appended
                _resp(  # RFC 8414 at the bare host
                    200,
                    {
                        "authorization_endpoint": "https://github.com/login/oauth/authorize",
                        "token_endpoint": "https://github.com/login/oauth/access_token",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://api.githubcopilot.com/mcp", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://github.com/login/oauth/authorize"
        assert metadata.registration_endpoint is None
        assert metadata.supports_dcr is False
        assert inner.get.await_args_list == [
            (("https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp",),),
            (("https://github.com/.well-known/oauth-authorization-server/login/oauth",),),
            (("https://github.com/.well-known/openid-configuration/login/oauth",),),
            (("https://github.com/login/oauth/.well-known/openid-configuration",),),
            (("https://github.com/login/oauth/.well-known/oauth-authorization-server",),),
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
                _resp(401),  # RFC 8414, path inserted
                _resp(404),  # OpenID, path inserted
                _resp(404),  # OpenID, path appended
                _resp(404),  # RFC 8414, path appended
                _resp(  # RFC 8414 at the bare host
                    200,
                    {
                        "authorization_endpoint": "https://mcp.atlassian.com/v1/authorize",
                        "registration_endpoint": "https://cf.mcp.atlassian.com/v1/register",
                    },
                ),
            ]
        )

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.atlassian.com/v1/sse", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.registration_endpoint == "https://cf.mcp.atlassian.com/v1/register"
        assert inner.get.await_args_list == [
            (("https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/sse",),),
            (("https://mcp.atlassian.com/.well-known/oauth-protected-resource",),),
            (("https://mcp.atlassian.com/.well-known/oauth-authorization-server/v1/sse",),),
            (("https://mcp.atlassian.com/.well-known/openid-configuration/v1/sse",),),
            (("https://mcp.atlassian.com/v1/sse/.well-known/openid-configuration",),),
            (("https://mcp.atlassian.com/v1/sse/.well-known/oauth-authorization-server",),),
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
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://example.com/authorize"

    @pytest.mark.asyncio
    async def test_returns_none_when_all_candidates_fail(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(return_value=_resp(404))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

        assert metadata is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none_rather_than_raising(self) -> None:
        inner = MagicMock()
        inner.get = AsyncMock(side_effect=RuntimeError("network down"))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

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
            metadata = await discover_oauth_metadata("https://169.254.169.254", policy=PUBLIC_ONLY)

        assert metadata is None
        inner.get.assert_not_awaited()
        inner.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_outer_timeout_returns_none_rather_than_raising(self) -> None:
        with patch(
            "app.agents.mcp.dcr._discover_oauth_metadata_inner",
            AsyncMock(side_effect=RuntimeError("discovery exploded")),
        ):
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

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
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

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
            metadata = await discover_oauth_metadata("https://example.com", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.issuer == "https://example.com"


class TestDiscoveryIgnoresEndpointsThatAreNotWebUrls:
    """A server whose metadata names a `javascript:` endpoint is treated as publishing nothing:
    that URL would otherwise be opened in the user's browser as PipesHub."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["authorization_endpoint", "token_endpoint", "registration_endpoint"])
    async def test_metadata_with_a_script_endpoint_is_not_used(self, field: str) -> None:
        document = {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "registration_endpoint": "https://auth.example.com/register",
            field: "javascript:alert(document.domain)//",
        }
        inner = MagicMock()
        inner.get = AsyncMock(side_effect=lambda url: _resp(200, document) if "oauth-authorization-server" in url else _resp(404))

        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com", policy=PUBLIC_ONLY)

        assert metadata is None

    @pytest.mark.asyncio
    async def test_a_later_valid_authorization_server_still_wins(self) -> None:
        bad = {"authorization_endpoint": "javascript:alert(1)", "token_endpoint": "https://bad.example.com/token"}
        good = {"authorization_endpoint": "https://good.example.com/authorize", "token_endpoint": "https://good.example.com/token"}

        def _get(url: str) -> MagicMock:
            if url.endswith("oauth-protected-resource"):
                return _resp(200, {"authorization_servers": ["https://bad.example.com", "https://good.example.com"]})
            if url == "https://bad.example.com/.well-known/oauth-authorization-server":
                return _resp(200, bad)
            if url == "https://good.example.com/.well-known/oauth-authorization-server":
                return _resp(200, good)
            return _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=_get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.authorization_endpoint == "https://good.example.com/authorize"


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
    """Same policy as the MCP connection (`url_guard`); IP literals need no DNS."""

    @pytest.mark.asyncio
    async def test_delegates_to_the_mcp_url_policy(self) -> None:
        with patch("app.agents.mcp.dcr.check_mcp_url", AsyncMock()) as mock_assert:
            await assert_discovery_target_allowed("https://example.com/x", allow_private=True)
        mock_assert.assert_awaited_once_with("https://example.com/x", allow_private=True)

    @pytest.mark.asyncio
    async def test_wraps_a_blocked_url_as_discovery_blocked_error(self) -> None:
        with patch(
            "app.agents.mcp.dcr.check_mcp_url", AsyncMock(side_effect=MCPUrlBlockedError("nope")),
        ):
            with pytest.raises(DiscoveryBlockedError):
                await assert_discovery_target_allowed("https://example.com/x", allow_private=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("allow_private", [True, False])
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/register",
            "http://169.254.169.254/latest/meta-data",
            "http://100.100.100.200/latest/meta-data",
            "http://168.63.129.16/machine?comp=goalstate",
        ],
    )
    async def test_loopback_and_metadata_are_refused_under_either_policy(self, url: str, allow_private: bool) -> None:
        with pytest.raises(DiscoveryBlockedError):
            await assert_discovery_target_allowed(url, allow_private=allow_private)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", ["http://10.0.0.5/register", "http://100.64.1.1:8080/register"])
    async def test_private_networks_follow_the_instance_policy(self, url: str) -> None:
        await assert_discovery_target_allowed(url, allow_private=True)
        with pytest.raises(DiscoveryBlockedError):
            await assert_discovery_target_allowed(url, allow_private=False)


class TestDiscoveryClientIsPinned:
    def test_requests_go_through_the_per_origin_guard(self) -> None:
        client = _discovery_client(5.0, PUBLIC_ONLY)
        assert isinstance(client._transport, PerOriginGuardedTransport)
        assert client._transport._policy is PUBLIC_ONLY
        assert client.follow_redirects is False
        assert client.trust_env is False


class TestServerSuppliedUrlsOnPrivateNetworks:
    """URLs the server's metadata names reach private addresses only on hosts the instance
    was configured with."""

    POLICY = MCPUrlPolicy(allow_private=True, trusted_hosts=frozenset({"mcp.internal"}))

    @pytest.mark.asyncio
    async def test_an_authorization_server_on_another_private_host_is_not_fetched(self) -> None:
        resource = {"authorization_servers": ["http://10.0.3.7:8091"]}
        checked: list[tuple[str, bool]] = []

        async def _check(url: str, *, allow_private: bool) -> None:
            checked.append((url, allow_private))
            if url.startswith("http://10.0.3.7") and not allow_private:
                raise DiscoveryBlockedError("private")

        inner = MagicMock()
        inner.get = AsyncMock(return_value=_resp(200, resource))
        with patch("app.agents.mcp.dcr.assert_discovery_target_allowed", _check), _mock_async_client(inner):
            await discover_oauth_metadata("http://mcp.internal/mcp", policy=self.POLICY)

        assert all(allowed for url, allowed in checked if url.startswith("http://mcp.internal"))
        private_checks = [allowed for url, allowed in checked if url.startswith("http://10.0.3.7")]
        assert private_checks and not any(private_checks)
        fetched = [call.args[0] for call in inner.get.await_args_list]
        assert not any(url.startswith("http://10.0.3.7") for url in fetched)

    @pytest.mark.asyncio
    async def test_registration_on_another_private_host_is_refused_before_any_request(self) -> None:
        inner = MagicMock()
        inner.post = AsyncMock()
        with _mock_async_client(inner), pytest.raises(DiscoveryBlockedError):
            await register_dynamic_client(
                registration_endpoint="http://10.0.3.7/register",
                redirect_uri="https://app.example.com/callback",
                authorization_url="http://mcp.internal/authorize",
                token_url="http://mcp.internal/token",
                policy=self.POLICY,
            )
        inner.post.assert_not_awaited()


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
                policy=PUBLIC_ONLY,
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
                    policy=PUBLIC_ONLY,
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
                    policy=PUBLIC_ONLY,
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
                    policy=PUBLIC_ONLY,
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
                    policy=PUBLIC_ONLY,
                )
        inner.post.assert_not_awaited()


class TestHowTheClientAuthenticatesAtTheTokenEndpoint:
    """client_secret_post as before; client_secret_basic where that is all the provider takes."""

    @pytest.mark.parametrize("supported,expected", [
        (None, "client_secret_post"),
        ([], "client_secret_post"),
        (["client_secret_basic", "client_secret_post"], "client_secret_post"),
        (["client_secret_basic", "private_key_jwt"], "client_secret_basic"),
        (["none"], "none"),
        (["private_key_jwt"], "client_secret_post"),
    ])
    def test_the_method_asked_for(self, supported: list[str] | None, expected: str) -> None:
        assert preferred_token_auth_method(supported) == expected

    @staticmethod
    async def _register(json_body: object, supported: list[str] | None = None) -> tuple[object, MagicMock]:
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = json_body
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            client = await register_dynamic_client(
                registration_endpoint="https://example.com/register",
                redirect_uri="https://app.example.com/mcp-servers/oauth/callback/",
                authorization_url="https://example.com/authorize",
                token_url="https://example.com/token",
                policy=PUBLIC_ONLY,
                auth_methods_supported=supported,
            )
        return client, inner.post

    @pytest.mark.asyncio
    async def test_registration_asks_for_basic_where_the_server_takes_only_that(self) -> None:
        client, post = await self._register({"client_id": "cid", "client_secret": "s"}, ["client_secret_basic"])

        assert post.await_args.kwargs["json"]["token_endpoint_auth_method"] == "client_secret_basic"
        assert client.token_endpoint_auth_method == "client_secret_basic"

    @pytest.mark.asyncio
    async def test_the_method_the_provider_registered_is_the_one_kept(self) -> None:
        client, post = await self._register(
            {"client_id": "cid", "client_secret": "s", "token_endpoint_auth_method": "client_secret_basic"},
        )

        assert post.await_args.kwargs["json"]["token_endpoint_auth_method"] == "client_secret_post"
        assert client.token_endpoint_auth_method == "client_secret_basic"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["private_key_jwt", ["client_secret_post"]])
    async def test_a_method_pipeshub_cannot_use_fails_the_registration(self, method: object) -> None:
        with pytest.raises(DCRError, match="doesn't support"):
            await self._register({"client_id": "cid", "token_endpoint_auth_method": method})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listed,kept", [
        (["client_secret_basic", 7, ""], ["client_secret_basic"]),
        (None, None),
        ("client_secret_basic", None),
    ])
    async def test_discovery_keeps_the_methods_the_server_lists(self, listed: object, kept: list[str] | None) -> None:
        as_metadata = {"authorization_endpoint": "https://mcp.example.com/a", "token_endpoint": "https://mcp.example.com/t"}
        if listed is not None:
            as_metadata["token_endpoint_auth_methods_supported"] = listed

        async def get(url: str) -> MagicMock:
            if url == "https://mcp.example.com/.well-known/oauth-authorization-server":
                return _resp(200, as_metadata)
            return _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com/mcp", policy=PUBLIC_ONLY)

        assert metadata.token_endpoint_auth_methods_supported == kept


class TestPkceSupportIsChecked:
    """A server that lists its PKCE methods without S256 is refused; one that lists none isn't."""

    @pytest.mark.parametrize("listed,refused", [
        (["S256"], False),
        (["plain", "S256"], False),
        (["plain"], True),
        ([], True),
        (None, False),
    ])
    def test_only_a_list_without_s256_refuses(self, listed: list[str] | None, refused: bool) -> None:
        assert DiscoveredOAuthMetadata(code_challenge_methods_supported=listed).refuses_s256_pkce is refused

    @pytest.mark.asyncio
    @pytest.mark.parametrize("listed,kept", [(["plain", "S256", 3], ["plain", "S256"]), (None, None)])
    async def test_discovery_keeps_the_methods_the_server_lists(self, listed: object, kept: list[str] | None) -> None:
        as_metadata = {"authorization_endpoint": "https://mcp.example.com/a", "token_endpoint": "https://mcp.example.com/t"}
        if listed is not None:
            as_metadata["code_challenge_methods_supported"] = listed

        async def get(url: str) -> MagicMock:
            if url == "https://mcp.example.com/.well-known/oauth-authorization-server":
                return _resp(200, as_metadata)
            return _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com/mcp", policy=PUBLIC_ONLY)

        assert metadata.code_challenge_methods_supported == kept


class TestTheRegistrationIsRecorded:
    """What the client was registered for is kept, so one that no longer fits can be replaced."""

    @staticmethod
    async def _register(json_body: object) -> tuple[object, MagicMock]:
        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = json_body
        inner = MagicMock()
        inner.post = AsyncMock(return_value=resp)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            client = await register_dynamic_client(
                registration_endpoint="https://example.com/register",
                redirect_uri="https://app.example.com/mcp-servers/oauth/callback/",
                authorization_url="https://example.com/authorize",
                token_url="https://example.com/token",
                policy=PUBLIC_ONLY,
            )
        return client, inner.post

    @pytest.mark.asyncio
    async def test_registers_as_a_web_application(self) -> None:
        _client, post = await self._register({"client_id": "cid"})
        payload = post.await_args.kwargs["json"]
        assert payload["application_type"] == "web"
        assert payload["redirect_uris"] == ["https://app.example.com/mcp-servers/oauth/callback/"]

    @pytest.mark.asyncio
    async def test_keeps_the_redirect_uri_and_when_the_secret_expires(self) -> None:
        client, _post = await self._register({"client_id": "cid", "client_secret": "s", "client_secret_expires_at": 1_900_000_000})
        assert client.redirect_uri == "https://app.example.com/mcp-servers/oauth/callback/"
        assert client.client_secret_expires_at == 1_900_000_000
        stored = client.model_dump(by_alias=True)
        assert stored["redirectUri"] == "https://app.example.com/mcp-servers/oauth/callback/"
        assert stored["clientSecretExpiresAt"] == 1_900_000_000

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [{"client_id": "cid"}, {"client_id": "cid", "client_secret_expires_at": "soon"}])
    async def test_an_absent_or_malformed_expiry_is_none(self, body: dict) -> None:
        client, _post = await self._register(body)
        assert client.client_secret_expires_at is None

    @pytest.mark.asyncio
    async def test_an_answer_that_is_not_an_object_is_a_registration_error(self) -> None:
        with pytest.raises(DCRError, match="client_id"):
            await self._register(["cid"])


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

    def test_includes_extra_params(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
            extra_params={"access_type": "offline", "prompt": "consent"},
        )
        params = parse_qs(urlparse(url).query)
        assert params["access_type"] == ["offline"]
        assert params["prompt"] == ["consent"]

    def test_omits_extra_params_when_not_provided(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
        )
        assert "access_type" not in parse_qs(urlparse(url).query)

    def test_extra_params_cannot_override_protocol_params(self) -> None:
        """Catalog metadata must not be able to redirect the flow or replay a state."""
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
            scopes=["read"],
            extra_params={
                "client_id": "attacker",
                "redirect_uri": "https://evil.example.com/steal",
                "response_type": "token",
                "state": "replayed",
                "scope": "admin",
                "prompt": "consent",
            },
        )
        params = parse_qs(urlparse(url).query)
        assert params["client_id"] == ["cid"]
        assert params["redirect_uri"] == ["https://app.example.com/callback"]
        assert params["response_type"] == ["code"]
        assert params["state"] == ["state123"]
        assert params["scope"] == ["read"]
        assert "evil.example.com" not in url
        # Non-reserved extras still pass through alongside the rejected ones.
        assert params["prompt"] == ["consent"]

    def test_extra_params_cannot_downgrade_pkce(self) -> None:
        url = build_authorization_url(
            authorization_url="https://example.com/authorize",
            client_id="cid",
            redirect_uri="https://app.example.com/callback",
            state="state123",
            code_challenge="challenge123",
            extra_params={"code_challenge": "attacker", "code_challenge_method": "plain"},
        )
        params = parse_qs(urlparse(url).query)
        assert params["code_challenge"] == ["challenge123"]
        assert params["code_challenge_method"] == ["S256"]


class TestBuildAuthorizationUrlSafety:
    @pytest.mark.parametrize("endpoint", ["javascript:alert(1)//", "data:text/html,x", "/authorize", ""])
    def test_an_endpoint_that_is_not_a_web_url_is_refused(self, endpoint: str) -> None:
        with pytest.raises(InvalidOAuthEndpointError):
            build_authorization_url(
                authorization_url=endpoint, client_id="cid", redirect_uri="https://app.example.com/cb", state="s",
            )

    def test_an_existing_query_is_kept_with_a_single_question_mark(self) -> None:
        url = build_authorization_url(
            authorization_url="https://login.example.com/oauth2/authorize?tenant=acme&p=B2C_1_signin",
            client_id="cid", redirect_uri="https://app.example.com/cb", state="s",
        )
        assert url.count("?") == 1
        params = parse_qs(urlparse(url).query)
        assert params["tenant"] == ["acme"]
        assert params["p"] == ["B2C_1_signin"]
        assert params["client_id"] == ["cid"]

    def test_a_fragment_on_the_endpoint_is_dropped(self) -> None:
        url = build_authorization_url(
            authorization_url="https://login.example.com/authorize#section",
            client_id="cid", redirect_uri="https://app.example.com/cb", state="s",
        )
        assert "#" not in url

    def test_protocol_params_already_on_the_endpoint_are_replaced_not_duplicated(self) -> None:
        url = build_authorization_url(
            authorization_url="https://login.example.com/authorize?redirect_uri=https://evil.example.com&state=x&client_id=evil",
            client_id="cid", redirect_uri="https://app.example.com/cb", state="s",
        )
        params = parse_qs(urlparse(url).query)
        assert params["redirect_uri"] == ["https://app.example.com/cb"]
        assert params["state"] == ["s"]
        assert params["client_id"] == ["cid"]
        assert "evil.example.com" not in url


class TestIdentityProviderIssuers:
    """Entra ID, Okta and Keycloak serve OpenID metadata appended after the issuer's path."""

    def test_the_spec_order_for_an_issuer_with_a_path(self) -> None:
        assert _authorization_server_metadata_urls("https://login.microsoftonline.com/tenant-1/v2.0") == [
            "https://login.microsoftonline.com/.well-known/oauth-authorization-server/tenant-1/v2.0",
            "https://login.microsoftonline.com/.well-known/openid-configuration/tenant-1/v2.0",
            "https://login.microsoftonline.com/tenant-1/v2.0/.well-known/openid-configuration",
            "https://login.microsoftonline.com/tenant-1/v2.0/.well-known/oauth-authorization-server",
            "https://login.microsoftonline.com/.well-known/oauth-authorization-server",
            "https://login.microsoftonline.com/.well-known/openid-configuration",
        ]

    def test_an_issuer_without_a_path_tries_the_host(self) -> None:
        assert _authorization_server_metadata_urls("https://auth.example.com/") == [
            "https://auth.example.com/.well-known/oauth-authorization-server",
            "https://auth.example.com/.well-known/openid-configuration",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("issuer", [
        "https://login.microsoftonline.com/tenant-1/v2.0",
        "https://acme.okta.com/oauth2/default",
        "https://sso.acme.com/realms/acme",
    ])
    async def test_appended_openid_metadata_is_found(self, issuer: str) -> None:
        appended = f"{issuer}/.well-known/openid-configuration"
        document = {"authorization_endpoint": f"{issuer}/authorize", "token_endpoint": f"{issuer}/token", "issuer": issuer}

        async def get(url: str) -> MagicMock:
            if url.endswith("/oauth-protected-resource") or "oauth-protected-resource/" in url:
                return _resp(200, {"authorization_servers": [issuer]})
            return _resp(200, document) if url == appended else _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.acme.com/mcp", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.token_endpoint == f"{issuer}/token"
        assert metadata.issuer == issuer


class TestTheServersOwnMetadata:
    """RFC 9728 metadata gives the `resource` to send (RFC 8707) and the scopes to request."""

    @pytest.mark.parametrize(("url", "canonical"), [
        ("HTTPS://MCP.Example.COM/mcp/", "https://mcp.example.com/mcp"),
        ("https://mcp.example.com/v1/sse?x=1#frag", "https://mcp.example.com/v1/sse"),
        # A URL carrying credentials is never sent anywhere (`is_web_url`).
        ("https://user:pw@mcp.example.com/v1/sse", None),
        ("https://mcp.example.com", "https://mcp.example.com"),
        ("javascript:alert(1)", None),
    ])
    def test_canonical_resource_uri(self, url: str, canonical: str | None) -> None:
        assert canonical_resource_uri(url) == canonical

    @pytest.mark.asyncio
    async def test_resource_and_scopes_come_from_the_protected_resource_metadata(self) -> None:
        async def get(url: str) -> MagicMock:
            if "oauth-protected-resource" in url:
                return _resp(200, {
                    "resource": "https://MCP.example.com/mcp",
                    "authorization_servers": ["https://auth.example.com"],
                    "scopes_supported": ["mcp:read", "mcp:write"],
                })
            if url == "https://auth.example.com/.well-known/oauth-authorization-server":
                return _resp(200, {
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "scopes_supported": ["openid", "mail.read", "files.readwrite.all"],
                })
            return _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com/mcp", policy=PUBLIC_ONLY)

        assert metadata.resource == "https://mcp.example.com/mcp"
        assert metadata.resource_scopes == ["mcp:read", "mcp:write"]
        assert metadata.scopes_supported == ["openid", "mail.read", "files.readwrite.all"]

    @pytest.mark.asyncio
    async def test_a_server_without_that_metadata_gets_no_resource(self) -> None:
        async def get(url: str) -> MagicMock:
            if url == "https://mcp.example.com/.well-known/oauth-authorization-server":
                return _resp(200, {"authorization_endpoint": "https://mcp.example.com/a", "token_endpoint": "https://mcp.example.com/t"})
            return _resp(404)

        inner = MagicMock()
        inner.get = AsyncMock(side_effect=get)
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example.com/mcp", policy=PUBLIC_ONLY)

        assert metadata.resource is None
        assert metadata.resource_scopes == []

    def test_the_authorize_url_carries_the_resource_and_a_template_cannot_change_it(self) -> None:
        url = build_authorization_url(
            "https://auth.example.com/authorize", "cid", "https://app.example.com/cb", "st",
            extra_params={"resource": "https://evil.example.com", "prompt": "consent"},
            resource="https://mcp.example.com/mcp",
        )
        query = parse_qs(urlparse(url).query)
        assert query["resource"] == ["https://mcp.example.com/mcp"]
        assert query["prompt"] == ["consent"]


class TestTheBearerChallenge:
    @pytest.mark.parametrize("values,expected", [
        (['Bearer resource_metadata="https://a.example/prm", scope="files:read"'],
         {"resource_metadata": "https://a.example/prm", "scope": "files:read"}),
        (['bearer Resource_Metadata=https://a.example/prm'], {"resource_metadata": "https://a.example/prm"}),
        (['Basic realm="x", Bearer scope="a b"'], {"scope": "a b"}),
        (['Negotiate abc==, Bearer scope=a'], {"scope": "a"}),
        (['Basic realm="x"', 'Bearer error="invalid_token", scope="s"'], {"error": "invalid_token", "scope": "s"}),
        (['Bearer error_description="bad, \\"very\\" bad", scope="s"'], {"error_description": 'bad, "very" bad', "scope": "s"}),
        (['Bearer scope="first", scope="second"'], {"scope": "first"}),
        (['Bearer xscope="no", scope="yes"'], {"xscope": "no", "scope": "yes"}),
        (['Basic realm="x"'], {}),
        ([], {}),
    ])
    def test_the_first_bearer_challenges_parameters(self, values: list[str], expected: dict[str, str]) -> None:
        assert bearer_challenge(values) == expected

    def test_its_scopes_are_valid_tokens_each_once_and_capped(self) -> None:
        assert challenge_scopes({"scope": 'read  write read b\\ad "q" files:all'}) == ["read", "write", "files:all"]
        assert challenge_scopes({}) == []
        assert len(challenge_scopes({"scope": " ".join(f"s{i}" for i in range(80))})) == 50
        assert challenge_scopes({"scope": f"read {'x' * 201}"}) == ["read"]

    def test_a_huge_header_is_read_only_so_far(self) -> None:
        value = "Bearer scope=s, " + "x=" + "y" * 20000 + ', resource_metadata="https://late.example"'
        assert "resource_metadata" not in bearer_challenge([value])


_AS = {"authorization_endpoint": "https://auth.example/authorize", "token_endpoint": "https://auth.example/token"}


class TestTheChallengeLeadsDiscovery:
    @pytest.mark.asyncio
    async def test_the_metadata_a_401_names_comes_first_and_its_scopes_are_kept(self) -> None:
        inner = MagicMock()
        inner.send = AsyncMock(return_value=_challenge(
            401, 'Bearer resource_metadata="https://mcp.example/meta/prm.json", scope="read write read bád"',
        ))
        inner.get = AsyncMock(side_effect=[
            _resp(200, {"resource": "https://mcp.example/mcp", "authorization_servers": ["https://auth.example"]}),
            _resp(200, _AS),
        ])
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://user:pw@mcp.example/mcp", policy=PUBLIC_ONLY)

        assert metadata is not None
        assert metadata.resource == "https://mcp.example/mcp"
        assert metadata.challenge_scopes == ["read", "write"]
        assert inner.get.await_args_list[0] == (("https://mcp.example/meta/prm.json",),)
        method, url = inner.build_request.call_args.args[:2]
        headers = inner.build_request.call_args.kwargs["headers"]
        assert (method, url) == ("POST", "https://mcp.example/mcp"), "no userinfo on the probe"
        assert "authorization" not in {k.lower() for k in headers}
        assert headers["MCP-Protocol-Version"] and "text/event-stream" in headers["Accept"]
        inner.send.assert_awaited_once()
        assert inner.send.call_args.kwargs == {"stream": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("prm", [
        {"resource": "https://other.example/mcp", "authorization_servers": ["https://evil.example"]},
        {"authorization_servers": ["https://evil.example"]},
    ], ids=["another-resource", "no-resource"])
    async def test_named_metadata_for_another_resource_is_ignored(self, prm: dict) -> None:
        """Otherwise a server could name a genuine third party's metadata and get its token."""
        inner = MagicMock()
        inner.send = AsyncMock(return_value=_challenge(401, 'Bearer resource_metadata="https://other.example/prm"'))
        inner.get = AsyncMock(side_effect=[
            _resp(200, prm),  # the named document: refused
            _resp(404),  # well-known, path-inserted
            _resp(404),  # well-known, root
            _resp(200, _AS),  # the MCP host as its own authorization server
        ])
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example/mcp", policy=PUBLIC_ONLY)

        assert metadata is not None and metadata.resource is None
        assert "evil.example" not in str(inner.get.await_args_list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("named", ["javascript:alert(1)", "/relative/prm", "https://x.example/" + "a" * 3000])
    async def test_a_named_address_that_isnt_a_usable_web_url_isnt_fetched(self, named: str) -> None:
        inner = MagicMock()
        inner.send = AsyncMock(return_value=_challenge(401, f'Bearer resource_metadata="{named}"'))
        inner.get = AsyncMock(return_value=_resp(404))
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            await discover_oauth_metadata("https://mcp.example/mcp", policy=PUBLIC_ONLY)
        assert named not in str(inner.get.await_args_list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [200, 403])
    async def test_anything_but_a_401_gives_no_hints(self, status: int) -> None:
        inner = MagicMock()
        inner.send = AsyncMock(return_value=_challenge(status, 'Bearer resource_metadata="https://mcp.example/prm", scope="s"'))
        inner.get = AsyncMock(side_effect=[_resp(404), _resp(404), _resp(200, _AS)])
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example/mcp", policy=PUBLIC_ONLY)
        assert metadata is not None and metadata.challenge_scopes == []
        assert inner.get.await_args_list[0] != (("https://mcp.example/prm",),)

    @pytest.mark.asyncio
    async def test_a_probe_that_fails_leaves_discovery_as_before(self) -> None:
        inner = MagicMock()
        inner.send = AsyncMock(side_effect=TimeoutError())
        inner.get = AsyncMock(side_effect=[_resp(404), _resp(200, _AS)])
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            metadata = await discover_oauth_metadata("https://mcp.example", policy=PUBLIC_ONLY)
        assert metadata is not None and metadata.authorization_endpoint == _AS["authorization_endpoint"]

    @pytest.mark.asyncio
    async def test_a_legacy_sse_server_is_probed_with_get(self) -> None:
        inner = MagicMock()
        inner.send = AsyncMock(return_value=_challenge(401, "Bearer"))
        inner.get = AsyncMock(return_value=_resp(404))
        with _allow_all_discovery_targets(), _mock_async_client(inner):
            await discover_oauth_metadata("https://mcp.example/sse", policy=PUBLIC_ONLY, sse=True)
        method, url = inner.build_request.call_args.args[:2]
        assert (method, url) == ("GET", "https://mcp.example/sse")
        assert inner.build_request.call_args.kwargs["headers"] == {"Accept": "text/event-stream"}
