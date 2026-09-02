"""Route-handler coverage for `app.api.routes.mcp_servers`.

Existing `test_mcp_servers.py` covers helpers and a few OAuth paths. These tests
exercise the FastAPI handlers themselves (admin gates, CRUD, auth, discovery,
agent-key routes) with the same mock-request style used there.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPToolInfo,
    MCPTransport,
    OAuthTokens,
)
from app.agents.mcp.token_refresh import MCPTokenRefreshError
from app.api.routes.mcp_servers import (
    AuthenticateRequest,
    OAuthClientConfigRequest,
    _check_user_is_admin,
    _filter_stdio_env,
    _get_configured_frontend_base_url,
    _sweep_expired_oauth_states,
    authenticate_agent_instance,
    authenticate_instance,
    auto_authenticate_instance,
    create_instance,
    delete_instance,
    get_agent_mcp_servers,
    get_agent_oauth_authorization_url,
    get_catalog_template,
    get_instance,
    get_instance_tools,
    get_my_mcp_servers,
    get_oauth_authorization_url,
    get_oauth_config,
    list_catalog,
    list_instances,
    reauthenticate_agent_instance,
    reauthenticate_instance,
    refresh_oauth_token,
    remove_agent_instance_credentials,
    update_credentials,
    update_instance,
    update_oauth_config,
)


def _mock_request(user=None, headers=None, app_state=None) -> MagicMock:
    request = MagicMock()
    request.state.user = user or {}
    request.headers = headers or {}
    for key, value in (app_state or {}).items():
        setattr(request.app.state, key, value)
    return request


def _api_token_instance(**overrides) -> dict:
    base = {
        "_id": "inst-1",
        "orgId": "org-1",
        "createdBy": "admin-1",
        "name": "Brave",
        "typeId": "brave_search",
        "transport": MCPTransport.STDIO.value,
        "authMode": MCPAuthMode.API_TOKEN.value,
        "useAdminAuth": False,
        "isCustom": False,
        "requiredEnv": ["BRAVE_API_KEY"],
        "optionalEnv": [],
        "createdAt": 1,
        "updatedAt": 2,
    }
    base.update(overrides)
    return base


def _oauth_instance(**overrides) -> dict:
    return _api_token_instance(
        authMode=MCPAuthMode.OAUTH.value,
        transport=MCPTransport.STREAMABLE_HTTP.value,
        url="https://mcp.example.com",
        authorizationUrl="https://auth.example.com/authorize",
        tokenUrl="https://auth.example.com/token",
        scopes=["default"],
        requiredEnv=[],
        **overrides,
    )


def _admin_request(config_service=None, registry=None) -> MagicMock:
    return _mock_request(
        user={"userId": "admin-1", "orgId": "org-1"},
        app_state={
            "config_service": config_service or MagicMock(),
            "mcp_registry": registry or MagicMock(),
        },
    )


# ---------------------------------------------------------------------------
# Admin check + frontend base URL helpers
# ---------------------------------------------------------------------------


class TestCheckUserIsAdmin:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"nodejs": {"endpoint": "http://nodejs:3000"}})
        request = _mock_request(headers={"authorization": "Bearer t", "x-organization-id": "org-1"})

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.api.routes.mcp_servers.httpx.AsyncClient", return_value=mock_client):
            assert await _check_user_is_admin("admin-1", request, config_service) is True

        mock_client.get.assert_awaited_once()
        assert mock_client.get.await_args.args[0].endswith("/api/v1/users/admin-1/adminCheck")

    @pytest.mark.asyncio
    async def test_defaults_false_on_transport_error(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        request = _mock_request()

        with patch("app.api.routes.mcp_servers.httpx.AsyncClient", side_effect=RuntimeError("network")):
            assert await _check_user_is_admin("u1", request, config_service) is False


class TestGetConfiguredFrontendBaseUrl:
    @pytest.mark.asyncio
    async def test_reads_public_endpoint(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "https://app.example.com/"}}
        )
        assert await _get_configured_frontend_base_url(config_service) == "https://app.example.com"

    @pytest.mark.asyncio
    async def test_falls_back_on_error(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        result = await _get_configured_frontend_base_url(config_service)
        assert result.startswith("http")


class TestFilterStdioEnv:
    def test_keeps_only_allowlisted_non_empty(self) -> None:
        instance = {"requiredEnv": ["API_KEY"], "optionalEnv": ["DEBUG"]}
        filtered = _filter_stdio_env(instance, {"API_KEY": " secret ", "DEBUG": "", "OTHER": "x"})
        assert filtered == {"API_KEY": "secret"}


class TestSweepExpiredOauthStates:
    @pytest.mark.asyncio
    async def test_deletes_only_expired(self) -> None:
        config_service = MagicMock()
        config_service.list_keys_in_directory = AsyncMock(return_value=["/s/a", "/s/b"])
        config_service.get_config = AsyncMock(
            side_effect=[
                {"expiresAt": 1},
                {"expiresAt": 9_999_999_999_999},
            ]
        )
        config_service.delete_config = AsyncMock()
        await _sweep_expired_oauth_states(config_service)
        config_service.delete_config.assert_awaited_once_with("/s/a")

    @pytest.mark.asyncio
    async def test_list_failure_is_non_fatal(self) -> None:
        config_service = MagicMock()
        config_service.list_keys_in_directory = AsyncMock(side_effect=RuntimeError("down"))
        await _sweep_expired_oauth_states(config_service)  # no raise


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class TestCatalogHandlers:
    @pytest.mark.asyncio
    async def test_list_catalog_paginates_and_searches(self) -> None:
        template = MagicMock()
        template.model_dump.return_value = {"typeId": "github", "displayName": "GitHub"}
        registry = MagicMock()
        registry.list_templates.return_value = [template]
        request = _admin_request(registry=registry)

        with patch(
            "app.agents.mcp.catalog.search_templates", return_value=[template]
        ) as search, patch(
            "app.agents.mcp.catalog.paginate", return_value=([template], 1)
        ) as paginate:
            result = await list_catalog(request, page=1, limit=10, search="git")

        search.assert_called_once()
        paginate.assert_called_once()
        assert result["total"] == 1
        assert result["templates"] == [{"typeId": "github", "displayName": "GitHub"}]

    @pytest.mark.asyncio
    async def test_get_catalog_template_404(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = None
        request = _admin_request(registry=registry)
        with pytest.raises(HTTPException) as exc:
            await get_catalog_template(request, "missing")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_catalog_template_ok(self) -> None:
        template = MagicMock()
        template.model_dump.return_value = {"typeId": "github"}
        registry = MagicMock()
        registry.get_template.return_value = template
        request = _admin_request(registry=registry)
        assert await get_catalog_template(request, "github") == {"typeId": "github"}


# ---------------------------------------------------------------------------
# Instance CRUD
# ---------------------------------------------------------------------------


class TestInstanceCrud:
    @pytest.mark.asyncio
    async def test_list_instances_requires_admin(self) -> None:
        request = _admin_request()
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await list_instances(request)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_list_instances_annotates_oauth_client_flag(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"clientId": "cid"})
        request = _admin_request(config_service=config_service)
        instances = [_api_token_instance()]

        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance", new=AsyncMock(return_value=instances)),
            patch("app.api.routes.mcp_servers.resolve_instance_owner_config_service", new=AsyncMock(return_value=config_service)),
        ):
            result = await list_instances(request)

        assert result["instances"][0]["hasOAuthClientConfig"] is True

    @pytest.mark.asyncio
    async def test_create_instance_happy_path(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        registry = MagicMock()
        registry.get_template.return_value = MagicMock(
            command="npx",
            args=["-y", "server"],
            required_env=["API_KEY"],
            optional_env=[],
            default_url=None,
            authorization_url=None,
            token_url=None,
            default_scopes=[],
        )
        request = _admin_request(config_service=config_service, registry=registry)
        payload = MCPServerInstanceConfig(
            name="Brave",
            type_id="brave_search",
            transport=MCPTransport.STDIO,
            auth_mode=MCPAuthMode.API_TOKEN,
        )

        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            result = await create_instance(request, payload)

        assert result["name"] == "Brave"
        assert result["_id"]
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_instance_rejects_disallowed_stdio_env(self) -> None:
        registry = MagicMock()
        registry.get_template.return_value = MagicMock(
            command="npx",
            args=[],
            required_env=["API_KEY"],
            optional_env=[],
            default_url=None,
            authorization_url=None,
            token_url=None,
            default_scopes=[],
        )
        request = _admin_request(registry=registry)
        payload = MCPServerInstanceConfig(
            name="Brave",
            type_id="brave_search",
            transport=MCPTransport.STDIO,
            auth_mode=MCPAuthMode.API_TOKEN,
            env={"API_KEY": "x", "EVIL": "y"},
        )

        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await create_instance(request, payload)
        assert exc.value.status_code == 400
        assert "EVIL" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_create_instance_set_config_failure(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=False)
        registry = MagicMock()
        registry.get_template.return_value = None
        request = _admin_request(config_service=config_service, registry=registry)
        payload = MCPServerInstanceConfig(
            name="Custom",
            transport=MCPTransport.SSE,
            auth_mode=MCPAuthMode.NONE,
            url="https://mcp.example.com/sse",
        )

        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await create_instance(request, payload)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_instance_not_found(self) -> None:
        request = _admin_request()
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_instance(request, "missing")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_instance_ok(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _admin_request(config_service=config_service)
        instance = _api_token_instance()
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)),
        ):
            result = await get_instance(request, "inst-1")
        assert result["_id"] == "inst-1"
        assert result["hasOAuthClientConfig"] is False

    @pytest.mark.asyncio
    async def test_update_instance_happy_path(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        registry = MagicMock()
        registry.get_template.return_value = None
        request = _admin_request(config_service=config_service, registry=registry)
        existing = _api_token_instance(isCustom=True, transport=MCPTransport.SSE.value, url="https://old.example.com")
        payload = MCPServerInstanceConfig(
            name="Renamed",
            transport=MCPTransport.SSE,
            auth_mode=MCPAuthMode.NONE,
            url="https://new.example.com",
        )

        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=existing)),
        ):
            result = await update_instance(request, "inst-1", payload)

        assert result["name"] == "Renamed"
        assert result["url"] == "https://new.example.com"
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_instance_cascades_credentials_and_states(self) -> None:
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        config_service.list_keys_in_directory = AsyncMock(
            side_effect=[
                ["/services/mcp/credentials/inst-1/user-1"],
                ["/services/mcp/oauth-states/state-a", "/services/mcp/oauth-states/state-b"],
            ]
        )
        config_service.get_config = AsyncMock(
            side_effect=[
                {"instanceId": "inst-1"},
                {"instanceId": "other"},
            ]
        )
        request = _admin_request(config_service=config_service)
        refresh = MagicMock()
        refresh.cancel_refresh_tasks_for_instance = MagicMock(return_value=1)

        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=refresh,
            ),
        ):
            result = await delete_instance(request, "inst-1")

        assert result == {"success": True, "_id": "inst-1"}
        refresh.cancel_refresh_tasks_for_instance.assert_called_once_with("inst-1")
        deleted = [c.args[0] for c in config_service.delete_config.await_args_list]
        assert "/services/mcp/credentials/inst-1/user-1" in deleted
        assert "/services/mcp/oauth-states/state-a" in deleted
        assert "/services/mcp/oauth-states/state-b" not in deleted


# ---------------------------------------------------------------------------
# Auth handlers
# ---------------------------------------------------------------------------


class TestAuthHandlers:
    @pytest.mark.asyncio
    async def test_authenticate_instance_saves_api_token(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)
        instance = _api_token_instance(requiredEnv=[])
        payload = AuthenticateRequest(apiToken="tok-1")

        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)),
            patch("app.api.routes.mcp_servers._assert_can_write_credentials", new=AsyncMock()),
        ):
            result = await authenticate_instance(request, "inst-1", payload)

        assert result == {"success": True, "isAuthenticated": True}
        saved = config_service.set_config.await_args.args[1]
        assert saved["credentials"]["apiToken"] == "tok-1"

    @pytest.mark.asyncio
    async def test_authenticate_instance_not_found(self) -> None:
        request = _admin_request()
        with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await authenticate_instance(request, "missing", AuthenticateRequest(apiToken="x"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_credentials_delegates_to_authenticate(self) -> None:
        request = _admin_request()
        with patch(
            "app.api.routes.mcp_servers.authenticate_instance",
            new=AsyncMock(return_value={"success": True}),
        ) as auth:
            result = await update_credentials(request, "inst-1", AuthenticateRequest(apiToken="x"))
        assert result == {"success": True}
        auth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_authenticate_requires_admin_auth_flag(self) -> None:
        request = _admin_request()
        with patch(
            "app.api.routes.mcp_servers._get_org_instance",
            new=AsyncMock(return_value=_api_token_instance(useAdminAuth=False)),
        ):
            with pytest.raises(HTTPException) as exc:
                await auto_authenticate_instance(request, "inst-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_auto_authenticate_requires_admin_credentials(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _admin_request(config_service=config_service)
        with patch(
            "app.api.routes.mcp_servers._get_org_instance",
            new=AsyncMock(return_value=_api_token_instance(useAdminAuth=True, createdBy="admin-1")),
        ):
            with pytest.raises(HTTPException) as exc:
                await auto_authenticate_instance(request, "inst-1")
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_auto_authenticate_ok(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True})
        request = _admin_request(config_service=config_service)
        with patch(
            "app.api.routes.mcp_servers._get_org_instance",
            new=AsyncMock(return_value=_api_token_instance(useAdminAuth=True, createdBy="admin-1")),
        ):
            result = await auto_authenticate_instance(request, "inst-1")
        assert result == {"success": True, "isAuthenticated": True}

    @pytest.mark.asyncio
    async def test_reauthenticate_clears_credentials_and_dcr(self) -> None:
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)
        refresh = MagicMock()

        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_oauth_instance())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=refresh,
            ),
        ):
            result = await reauthenticate_instance(request, "inst-1")

        assert result == {"success": True}
        assert config_service.delete_config.await_count == 2
        refresh.cancel_refresh_task.assert_called_once()


# ---------------------------------------------------------------------------
# OAuth config / refresh / authorize wrappers
# ---------------------------------------------------------------------------


class TestOauthRouteWrappers:
    @pytest.mark.asyncio
    async def test_get_oauth_authorization_url_not_found(self) -> None:
        request = _admin_request()
        with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await get_oauth_authorization_url(request, "missing")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_oauth_authorization_url_delegates(self) -> None:
        request = _admin_request()
        instance = _oauth_instance()
        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)),
            patch(
                "app.api.routes.mcp_servers._build_oauth_authorization_url",
                new=AsyncMock(return_value={"authorizationUrl": "https://auth"}),
            ) as build,
        ):
            result = await get_oauth_authorization_url(request, "inst-1", base_url="https://app.example.com")
        assert result["authorizationUrl"] == "https://auth"
        assert build.await_args.kwargs["owner_type"] == "user"
        assert build.await_args.kwargs["initiated_by"] == "admin-1"

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_maps_refresh_errors(self) -> None:
        request = _admin_request()
        with patch(
            "app.api.routes.mcp_servers.mcp_token_refresh.refresh_credential_record",
            new=AsyncMock(side_effect=MCPTokenRefreshError("no refresh token")),
        ):
            with pytest.raises(HTTPException) as exc:
                await refresh_oauth_token(request, "inst-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_refresh_oauth_token_success(self) -> None:
        request = _admin_request()
        with patch(
            "app.api.routes.mcp_servers.mcp_token_refresh.refresh_credential_record",
            new=AsyncMock(return_value=OAuthTokens(access_token="new")),
        ):
            assert await refresh_oauth_token(request, "inst-1") == {"success": True}

    @pytest.mark.asyncio
    async def test_get_oauth_config_masks_secrets(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={"clientId": "abcdefghij", "clientSecret": "secretvalue12"}
        )
        request = _admin_request(config_service=config_service)
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            result = await get_oauth_config(request, "inst-1")
        assert result["configured"] is True
        assert "•" in result["clientId"]
        assert "•" in result["clientSecret"]

    @pytest.mark.asyncio
    async def test_get_oauth_config_unconfigured(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _admin_request(config_service=config_service)
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            assert await get_oauth_config(request, "inst-1") == {"configured": False}

    @pytest.mark.asyncio
    async def test_update_oauth_config_requires_admin(self) -> None:
        request = _admin_request()
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=False)):
            with pytest.raises(HTTPException) as exc:
                await update_oauth_config(
                    request, "inst-1", OAuthClientConfigRequest(clientId="c", clientSecret="s")
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_oauth_config_ok(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_oauth_instance())),
        ):
            result = await update_oauth_config(
                request, "inst-1", OAuthClientConfigRequest(clientId="cid", clientSecret="sec")
            )
        assert result == {"success": True}
        saved = config_service.set_config.await_args.args[1]
        assert saved["clientId"] == "cid"
        assert saved["clientSecret"] == "sec"


# ---------------------------------------------------------------------------
# Discovery / my-mcp-servers
# ---------------------------------------------------------------------------


class TestDiscoveryHandlers:
    @pytest.mark.asyncio
    async def test_get_my_mcp_servers_without_tools(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _admin_request(config_service=config_service)
        instances = [_api_token_instance(authMode=MCPAuthMode.NONE.value)]

        with patch(
            "app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance",
            new=AsyncMock(return_value=instances),
        ):
            result = await get_my_mcp_servers(request, include_tools=False)

        assert len(result["instances"]) == 1
        assert result["instances"][0]["isAuthenticated"] is True
        assert result["instances"][0]["tools"] == []

    @pytest.mark.asyncio
    async def test_get_my_mcp_servers_records_tools_error(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"isAuthenticated": True, "credentials": {"apiToken": "t"}})
        request = _admin_request(config_service=config_service)

        with (
            patch(
                "app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance",
                new=AsyncMock(return_value=[_api_token_instance()]),
            ),
            patch(
                "app.api.routes.mcp_servers.discover_tools",
                new=AsyncMock(side_effect=MCPConnectionError("refused")),
            ),
        ):
            result = await get_my_mcp_servers(request, include_tools=True)

        assert result["instances"][0]["toolsError"] == "refused"

    @pytest.mark.asyncio
    async def test_get_instance_tools_requires_auth(self) -> None:
        request = _admin_request()
        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch("app.api.routes.mcp_servers._resolve_effective_user_auth", new=AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_instance_tools(request, "inst-1")
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_get_instance_tools_ok(self) -> None:
        request = _admin_request()
        tool = MCPToolInfo(name="search", namespaced_name="mcp_brave_search_search", description="Search")
        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch(
                "app.api.routes.mcp_servers._resolve_effective_user_auth",
                new=AsyncMock(return_value={"isAuthenticated": True, "credentials": {"apiToken": "t"}}),
            ),
            patch("app.api.routes.mcp_servers.discover_tools", new=AsyncMock(return_value=[tool])),
        ):
            result = await get_instance_tools(request, "inst-1")
        assert result["tools"][0]["namespacedName"] == "mcp_brave_search_search"

    @pytest.mark.asyncio
    async def test_get_instance_tools_connection_error(self) -> None:
        request = _admin_request()
        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch(
                "app.api.routes.mcp_servers._resolve_effective_user_auth",
                new=AsyncMock(return_value={"isAuthenticated": True, "credentials": {"apiToken": "t"}}),
            ),
            patch(
                "app.api.routes.mcp_servers.discover_tools",
                new=AsyncMock(side_effect=MCPConnectionError("down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_instance_tools(request, "inst-1")
        assert exc.value.status_code == 502


# ---------------------------------------------------------------------------
# Agent-key routes
# ---------------------------------------------------------------------------


class TestAgentMcpRoutes:
    @pytest.mark.asyncio
    async def test_get_agent_mcp_servers(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        request = _admin_request(config_service=config_service)

        # Stub the toolsets module instead of importing it — that import pulls a
        # heavy connector registry graph and is unrelated to what this handler tests.
        toolsets_stub = types.ModuleType("app.api.routes.toolsets")
        toolsets_stub._resolve_agent_with_permission = AsyncMock(return_value={})

        with (
            patch.dict(sys.modules, {"app.api.routes.toolsets": toolsets_stub}),
            patch(
                "app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance",
                new=AsyncMock(return_value=[_api_token_instance(authMode=MCPAuthMode.NONE.value)]),
            ),
        ):
            result = await get_agent_mcp_servers(request, "agent-1", include_tools=False)

        assert len(result["instances"]) == 1

    @pytest.mark.asyncio
    async def test_authenticate_agent_instance(self) -> None:
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)
        instance = _api_token_instance(requiredEnv=[])

        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={})),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=instance)),
        ):
            result = await authenticate_agent_instance(
                request, "agent-1", "inst-1", AuthenticateRequest(apiToken="agent-tok")
            )

        assert result == {"success": True, "isAuthenticated": True}
        saved = config_service.set_config.await_args.args[1]
        assert saved["userId"] == "agent-1"
        assert saved["agentKey"] == "agent-1"

    @pytest.mark.asyncio
    async def test_authenticate_agent_instance_rejects_oauth(self) -> None:
        request = _admin_request()
        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={})),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_oauth_instance())),
        ):
            with pytest.raises(HTTPException) as exc:
                await authenticate_agent_instance(
                    request, "agent-1", "inst-1", AuthenticateRequest(apiToken="x")
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_remove_agent_instance_credentials(self) -> None:
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)
        refresh = MagicMock()

        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={})),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=refresh,
            ),
        ):
            result = await remove_agent_instance_credentials(request, "agent-1", "inst-1")

        assert result == {"success": True}
        refresh.cancel_refresh_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_reauthenticate_agent_instance(self) -> None:
        config_service = MagicMock()
        config_service.delete_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service)

        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={})),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_oauth_instance())),
            patch(
                "app.connectors.core.base.token_service.startup_service.startup_service.get_mcp_token_refresh_service",
                return_value=None,
            ),
        ):
            result = await reauthenticate_agent_instance(request, "agent-1", "inst-1")

        assert result == {"success": True}
        assert config_service.delete_config.await_count == 2

    @pytest.mark.asyncio
    async def test_get_agent_oauth_authorization_url(self) -> None:
        request = _admin_request()
        with (
            patch("app.api.routes.mcp_servers._require_mcp_agent_edit_access", new=AsyncMock(return_value={})),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_oauth_instance())),
            patch(
                "app.api.routes.mcp_servers._build_oauth_authorization_url",
                new=AsyncMock(return_value={"authorizationUrl": "https://auth"}),
            ) as build,
        ):
            result = await get_agent_oauth_authorization_url(request, "agent-1", "inst-1")

        assert result["authorizationUrl"] == "https://auth"
        assert build.await_args.args[3] == "agent-1"  # owner_id is agent key
        assert build.await_args.kwargs["owner_type"] == "agent"
        assert build.await_args.kwargs["initiated_by"] == "admin-1"
