"""End-to-end scenario tests for GitLab Cloud vs GitLab EE OAuth flow.

These tests stitch together the layers involved in the GitLab EE instance-URL
regression (see ``app/utils/oauth_config.py::resolve_instance_url`` and
``TokenRefreshService._build_oauth_flow_from_shared_config``) and assert the
effective URLs that callers would dial out to. Each scenario is exercised for
both Cloud (``gitlab.com``) and EE (self-managed host) so the contract is
locked in for both deployment modes.

Layers covered:
    1. ``resolve_instance_url`` — host resolution used by API client + connector
    2. ``TokenRefreshService._build_complete_oauth_config`` — OAuth flow config
       handed to ``get_oauth_config`` during a refresh
    3. ``get_oauth_config`` — host-swap on standard OAuth paths

Each scenario verifies:
    - The resolved ``instance_url`` for the API client matches the deployment.
    - The token-refresh OAuth config carries the right ``instanceUrl``.
    - The effective ``token_url`` / ``authorize_url`` from ``get_oauth_config``
      target the right host (gitlab.com for Cloud, EE host for EE).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.utils.oauth_config import get_oauth_config, resolve_instance_url


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_token_refresh_service():
    """Build a ``TokenRefreshService`` with mocked dependencies."""
    from app.connectors.core.base.token_service.token_refresh_service import (
        TokenRefreshService,
    )

    config_service = AsyncMock()
    graph_provider = AsyncMock()
    return TokenRefreshService(config_service, graph_provider), config_service


def _shared_oauth_config(
    *,
    instance_url: str | None,
    client_id: str = "cid",
    client_secret: str = "csecret",
) -> dict:
    """Build a shared OAuth-app config payload as it would be stored in etcd.

    For GitLab the registry-default ``authorizeUrl`` / ``tokenUrl`` are SaaS
    URLs (``https://gitlab.com/oauth/...``) regardless of the deployment mode
    — the host swap is what redirects them to the EE instance.
    """
    config: dict = {"clientId": client_id, "clientSecret": client_secret}
    if instance_url is not None:
        config["instanceUrl"] = instance_url
    return {
        "_id": "oauth-shared-1",
        "authorizeUrl": "https://gitlab.com/oauth/authorize",
        "tokenUrl": "https://gitlab.com/oauth/token",
        "config": config,
        "scopes": {"team_sync": ["read_api"]},
    }


# Cloud vs EE matrix. ``instance_url_on_auth`` mirrors what the connector-instance
# auth config holds; ``instance_url_on_shared`` mirrors what's on the shared
# OAuth-app config. The expected_host is the effective host the OAuth flow
# should target after resolution + host-swap.
SCENARIOS: list[tuple[str, str | None, str | None, str]] = [
    # Fresh installs — instanceUrl persisted on the connector-instance auth.
    ("cloud_fresh", None, None, "https://gitlab.com"),
    ("ee_fresh", "https://git.example.com", "https://git.example.com", "https://git.example.com"),
    # Legacy installs — instanceUrl was stripped from the connector-instance auth
    # by the old strip rule; only the shared OAuth-app config still has it.
    ("cloud_legacy", None, None, "https://gitlab.com"),
    ("ee_legacy", None, "https://git.example.com", "https://git.example.com"),
    # Mixed / explicit override — per-instance value wins over shared.
    (
        "ee_instance_overrides_shared",
        "https://gitlab.team-a.example",
        "https://gitlab.team-b.example",
        "https://gitlab.team-a.example",
    ),
]


# ---------------------------------------------------------------------------
# resolve_instance_url
# ---------------------------------------------------------------------------


class TestResolveInstanceUrlAcrossDeployments:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,instance_url_on_auth,instance_url_on_shared,expected_host",
        SCENARIOS,
    )
    async def test_resolves_correct_host(
        self,
        name: str,
        instance_url_on_auth: str | None,
        instance_url_on_shared: str | None,
        expected_host: str,
    ):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value=[_shared_oauth_config(instance_url=instance_url_on_shared)]
        )

        auth_config: dict = {
            "oauthConfigId": "oauth-shared-1",
            "connectorType": "GITLAB",
        }
        if instance_url_on_auth is not None:
            auth_config["instanceUrl"] = instance_url_on_auth

        result = await resolve_instance_url(
            auth_config, config_service, default="https://gitlab.com"
        )
        assert result == expected_host, f"scenario={name}"


# ---------------------------------------------------------------------------
# TokenRefreshService._build_complete_oauth_config
# ---------------------------------------------------------------------------


class TestTokenRefreshOAuthFlowAcrossDeployments:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,instance_url_on_auth,instance_url_on_shared,expected_host",
        SCENARIOS,
    )
    async def test_refresh_targets_correct_host(
        self,
        name: str,
        instance_url_on_auth: str | None,
        instance_url_on_shared: str | None,
        expected_host: str,
    ):
        """The refresh ``oauth_flow_config`` plus ``get_oauth_config`` together
        must produce a ``token_url`` on the expected host for every scenario."""
        svc, _ = _make_token_refresh_service()
        shared = _shared_oauth_config(instance_url=instance_url_on_shared)
        svc._fetch_shared_oauth_config = AsyncMock(return_value=shared)

        auth_config: dict = {
            "oauthConfigId": "oauth-shared-1",
            "connectorScope": "team",
        }
        if instance_url_on_auth is not None:
            auth_config["instanceUrl"] = instance_url_on_auth

        oauth_flow_config = await svc._build_complete_oauth_config(
            "conn1", "GITLAB", auth_config
        )

        # For Cloud (no instanceUrl anywhere) we don't set the key — that's
        # fine because the unmodified gitlab.com URLs already target Cloud.
        # For every other scenario the resolved instanceUrl must be present.
        if expected_host == "https://gitlab.com" and not (
            instance_url_on_auth or instance_url_on_shared
        ):
            assert "instanceUrl" not in oauth_flow_config, f"scenario={name}"
        else:
            assert oauth_flow_config["instanceUrl"] == expected_host, f"scenario={name}"

        # End-to-end: feed the flow config to ``get_oauth_config`` and verify
        # the effective token/authorize URLs hit the right host.
        oauth_config = get_oauth_config(oauth_flow_config)
        assert oauth_config.token_url == f"{expected_host}/oauth/token", (
            f"scenario={name}"
        )
        assert oauth_config.authorize_url == f"{expected_host}/oauth/authorize", (
            f"scenario={name}"
        )


# ---------------------------------------------------------------------------
# get_oauth_config host swap — direct unit-level sanity for Cloud/EE
# ---------------------------------------------------------------------------


class TestGetOAuthConfigHostSwapAcrossDeployments:
    def test_cloud_no_swap(self):
        """Cloud: instanceUrl absent → SaaS URLs are kept as-is."""
        oauth = get_oauth_config(
            {
                "clientId": "c",
                "clientSecret": "s",
                "authorizeUrl": "https://gitlab.com/oauth/authorize",
                "tokenUrl": "https://gitlab.com/oauth/token",
            }
        )
        assert oauth.authorize_url == "https://gitlab.com/oauth/authorize"
        assert oauth.token_url == "https://gitlab.com/oauth/token"

    def test_cloud_explicit_gitlab_com_no_swap(self):
        """Cloud: instanceUrl explicitly ``https://gitlab.com`` — no-op swap."""
        oauth = get_oauth_config(
            {
                "clientId": "c",
                "clientSecret": "s",
                "instanceUrl": "https://gitlab.com",
                "authorizeUrl": "https://gitlab.com/oauth/authorize",
                "tokenUrl": "https://gitlab.com/oauth/token",
            }
        )
        assert oauth.authorize_url == "https://gitlab.com/oauth/authorize"
        assert oauth.token_url == "https://gitlab.com/oauth/token"

    def test_ee_swaps_host_for_standard_oauth_paths(self):
        """EE: SaaS-default URLs in storage, but instanceUrl points to EE host.
        Both ``/oauth/authorize`` and ``/oauth/token`` must be redirected."""
        oauth = get_oauth_config(
            {
                "clientId": "c",
                "clientSecret": "s",
                "instanceUrl": "https://git.example.com",
                "authorizeUrl": "https://gitlab.com/oauth/authorize",
                "tokenUrl": "https://gitlab.com/oauth/token",
            }
        )
        assert oauth.authorize_url == "https://git.example.com/oauth/authorize"
        assert oauth.token_url == "https://git.example.com/oauth/token"

    def test_ee_derives_oauth_endpoints_when_urls_absent(self):
        """EE: instanceUrl alone is enough to derive the OAuth endpoints."""
        oauth = get_oauth_config(
            {
                "clientId": "c",
                "clientSecret": "s",
                "instanceUrl": "https://git.example.com",
            }
        )
        assert oauth.authorize_url == "https://git.example.com/oauth/authorize"
        assert oauth.token_url == "https://git.example.com/oauth/token"


# ---------------------------------------------------------------------------
# GitLab API client URL across deployments
# ---------------------------------------------------------------------------


class TestGitLabClientBuilderAcrossDeployments:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name,instance_url_on_auth,instance_url_on_shared,expected_host",
        SCENARIOS,
    )
    async def test_client_targets_correct_host(
        self,
        name: str,
        instance_url_on_auth: str | None,
        instance_url_on_shared: str | None,
        expected_host: str,
    ):
        """``GitLabClient.build_from_services`` resolves to the EE host for EE
        deployments (fresh or legacy) and to gitlab.com for Cloud."""
        from unittest.mock import patch

        from app.sources.client.gitlab.gitlab import GitLabClient

        connector_config: dict = {
            "auth": {
                "authType": "OAUTH",
                "oauthConfigId": "oauth-shared-1",
                "connectorType": "GITLAB",
            },
            "credentials": {"access_token": "tok"},
        }
        if instance_url_on_auth is not None:
            connector_config["auth"]["instanceUrl"] = instance_url_on_auth

        shared = _shared_oauth_config(instance_url=instance_url_on_shared)

        async def _get_config(path, *_args, **_kwargs):
            if path.endswith("/config") and "connectors" in path:
                return connector_config
            if path == "/services/oauth/gitlab":
                return [shared]
            return None

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=_get_config)

        with patch("app.sources.client.gitlab.gitlab.gitlab"):
            client = await GitLabClient.build_from_services(
                logger=AsyncMock(), config_service=config_service, connector_instance_id="inst-1"
            )

        assert client.get_client().url == expected_host, f"scenario={name}"
