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

from app.agents.mcp.client import MCPConnectionError, ToolListing
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
from app.config.constants.service import DefaultEndpoints

_DEFAULT_FRONTEND = DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


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
    @pytest.mark.parametrize(
        ("status", "role", "expected"),
        [
            ("valid", "admin", True),
            ("valid", "member", False),
            ("rejected", "member", False),
            ("unknown", "member", False),
        ],
    )
    async def test_uses_the_callers_live_role(self, status, role, expected) -> None:
        from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus

        config_service = MagicMock()
        request = _mock_request(headers={"authorization": "Bearer t", "x-organization-id": "org-1"})
        caller = CallerRole(CallerRoleStatus(status), role)

        with patch(
            "app.api.routes.mcp_servers.fetch_caller_role",
            new=AsyncMock(return_value=caller),
        ) as mock_role:
            assert await _check_user_is_admin("admin-1", request, config_service) is expected

        mock_role.assert_awaited_once_with(request, config_service)


class TestGetConfiguredFrontendBaseUrl:
    @pytest.mark.asyncio
    async def test_reads_public_endpoint(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "https://app.example.com/"}}
        )
        assert await _get_configured_frontend_base_url(config_service) == "https://app.example.com"

    @pytest.mark.asyncio
    async def test_without_configuration_uses_the_default(self) -> None:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        assert await _get_configured_frontend_base_url(config_service) == _DEFAULT_FRONTEND

    @pytest.mark.asyncio
    async def test_an_unreadable_store_is_an_error_not_the_default(self) -> None:
        """The default address would make every registered OAuth client look registered for the
        wrong address and get replaced, signing out everyone who signed in through it."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(HTTPException) as exc:
            await _get_configured_frontend_base_url(config_service)
        assert exc.value.status_code == 503
        assert config_service.get_config.await_args.kwargs["raise_on_error"] is True


class TestFilterStdioEnv:
    def test_keeps_only_allowlisted_non_empty(self) -> None:
        instance = {"requiredEnv": ["API_KEY"], "optionalEnv": ["DEBUG"]}
        filtered = _filter_stdio_env(instance, {"API_KEY": " secret ", "DEBUG": "", "OTHER": "x"})
        assert filtered == {"API_KEY": "secret"}


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


def _real_registry():
    from app.agents.mcp.registry import MCPRegistry

    registry = MCPRegistry()
    registry.auto_discover_templates()
    return registry


class TestStdioPolicy:
    """Custom STDIO is operator opt-in; catalog servers always run the registry command."""

    @staticmethod
    def _custom_stdio(**overrides) -> MCPServerInstanceConfig:
        fields = dict(
            name="custom", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE,
            command="npx", args=["-y", "some-mcp-server"],
        )
        fields.update(overrides)
        return MCPServerInstanceConfig(**fields)

    @pytest.mark.asyncio
    async def test_create_custom_stdio_forbidden_when_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service, registry=_real_registry())
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await create_instance(request, self._custom_stdio())
        assert exc.value.status_code == 403
        assert "MCP_ALLOW_CUSTOM_STDIO" in exc.value.detail
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_custom_stdio_allowed_when_flag_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service, registry=_real_registry())
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            record = await create_instance(request, self._custom_stdio(required_env=["MY_API_KEY"]))
        assert record["command"] == "npx"
        assert record["requiredEnv"] == ["MY_API_KEY"]
        config_service.set_config.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag", ["true", "false"])
    @pytest.mark.parametrize(
        ("type_id", "overrides"),
        [
            ("slack", {"command": "touch", "args": ["/tmp/x"]}),
            ("slack", {"args": ["-y", "some-other-package"]}),
            ("github", {"command": "touch", "args": ["/tmp/x"]}),
        ],
    )
    async def test_create_catalog_rejects_command_override(
        self, type_id: str, overrides: dict, flag: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", flag)
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service, registry=_real_registry())
        payload = MCPServerInstanceConfig(
            name=type_id, type_id=type_id, transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, **overrides,
        )
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await create_instance(request, payload)
        assert exc.value.status_code == 400
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_catalog_stdio_stores_template_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service, registry=_real_registry())
        payload = MCPServerInstanceConfig(
            name="Exa", type_id="exa", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN,
            command="npx", args=["-y", "exa-mcp-server@3.4.1"],
        )
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            record = await create_instance(request, payload)
        assert record["command"] == "npx"
        assert record["args"] == ["-y", "exa-mcp-server@3.4.1"]
        assert record["transport"] == MCPTransport.STDIO.value

    @pytest.mark.asyncio
    async def test_update_instance_applies_same_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        request = _admin_request(config_service=config_service, registry=_real_registry())
        existing = _api_token_instance(typeId=None, isCustom=True, transport=MCPTransport.SSE.value, url="https://x")
        with (
            patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)),
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=existing)),
        ):
            with pytest.raises(HTTPException) as exc:
                await update_instance(request, "inst-1", self._custom_stdio(command="touch", args=["/tmp/x"]))
        assert exc.value.status_code == 403
        config_service.set_config.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag", ["true", "false"])
    @pytest.mark.parametrize("env_name", ["NODE_OPTIONS", "LD_PRELOAD", "PATH", "PYTHONPATH", "lower_case"])
    async def test_dangerous_env_names_rejected(
        self, env_name: str, flag: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", flag)
        request = _admin_request(registry=_real_registry())
        payload = self._custom_stdio(
            auth_mode=MCPAuthMode.API_TOKEN, required_env=[env_name], env={env_name: "--require /tmp/x.js"},
        )
        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc:
                await create_instance(request, payload)
        assert exc.value.status_code == 400
        assert env_name in exc.value.detail

    def test_catalog_template_env_names_pass_validation(self) -> None:
        from app.agents.mcp.stdio_policy import rejected_env_names

        for template in _real_registry().list_templates():
            assert rejected_env_names(template.required_env + template.optional_env) == [], template.type_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("flag", "expected"), [("true", True), ("false", False)])
    async def test_catalog_reports_custom_stdio_allowed(
        self, flag: str, expected: bool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", flag)
        result = await list_catalog(_admin_request(registry=_real_registry()), page=1, limit=10, search=None)
        assert result["customStdioAllowed"] is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("flag", "expected"), [("true", None), ("false", "custom_stdio_disabled")])
    async def test_my_mcp_servers_reports_disabled_reason(
        self, flag: str, expected: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", flag)
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=None)
        instances = [
            _api_token_instance(_id="custom", typeId=None, isCustom=True, authMode=MCPAuthMode.NONE.value, command="npx"),
            _api_token_instance(_id="slack", typeId="slack", authMode=MCPAuthMode.NONE.value),
        ]
        with patch(
            "app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance",
            new=AsyncMock(return_value=instances),
        ):
            result = await get_my_mcp_servers(_admin_request(config_service=config_service), include_tools=False)
        reasons = {entry["_id"]: entry["disabledReason"] for entry in result["instances"]}
        assert reasons == {"custom": expected, "slack": None}


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
            transport=MCPTransport.STDIO,
            supported_auth_modes=[MCPAuthMode.API_TOKEN],
            command="npx",
            args=["-y", "server"],
            required_env=["API_KEY"],
            optional_env=[],
            default_url=None,
            authorization_url=None,
            token_url=None,
            default_scopes=[],
            replaced_by=None,
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
    async def test_create_instance_ignores_env_values_in_the_payload(self) -> None:
        """Credential values are never part of an instance; they arrive per user through
        /authenticate. An `env` map on create is ignored, not stored."""
        config_service = MagicMock()
        config_service.set_config = AsyncMock(return_value=True)
        registry = MagicMock()
        registry.get_template.return_value = MagicMock(
            transport=MCPTransport.STDIO,
            supported_auth_modes=[MCPAuthMode.API_TOKEN],
            command="npx",
            args=[],
            required_env=["API_KEY"],
            optional_env=[],
            default_url=None,
            authorization_url=None,
            token_url=None,
            default_scopes=[],
            replaced_by=None,
        )
        request = _admin_request(config_service=config_service, registry=registry)
        payload = MCPServerInstanceConfig.model_validate({
            "name": "Brave", "typeId": "brave_search", "transport": "stdio", "authMode": "api_token",
            "env": {"API_KEY": "x", "EVIL": "y"},
        })

        with patch("app.api.routes.mcp_servers._check_user_is_admin", new=AsyncMock(return_value=True)):
            record = await create_instance(request, payload)

        assert "env" not in record
        assert "EVIL" not in str(config_service.set_config.await_args.args[1])

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
        config_service.list_keys_in_directory = AsyncMock(return_value=[])
        config_service.delete_config = AsyncMock(return_value=True)
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
        # A new URL invalidates every stored credential for the old one.
        assert result["credentialsReset"] is True
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
        request.app.state.graph_provider.check_mcp_instance_in_use = AsyncMock(return_value=[])
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


@pytest.fixture
def _instance_in_callers_org():
    """The handler under test looks the instance up (org-scoped); this one exists."""
    with patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value={"_id": "inst-1"})):
        yield


@pytest.mark.usefixtures("_instance_in_callers_org")
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
            result = await get_oauth_config(request, "inst-1")
        # Unconfigured, it still names the redirect URI an admin registers the app with.
        assert result == {"configured": False, "redirectUri": f"{_DEFAULT_FRONTEND}/mcp-servers/oauth/callback/"}

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
    @pytest.mark.parametrize("record,connected_at", [
        ({"isAuthenticated": True, "credentials": {"apiToken": "t"}, "connectedAt": 1_791_000_000_000}, 1_791_000_000_000),
        ({"isAuthenticated": True, "credentials": {"apiToken": "t"}}, None),
        ({"isAuthenticated": True, "credentials": {"apiToken": "t"}, "connectedAt": "yesterday"}, None),
    ])
    async def test_each_server_says_when_its_sign_in_was_made(self, record: dict, connected_at: int | None) -> None:
        """So the chat's sign-in card can tell that a new sign-in went through."""
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=record)
        request = _admin_request(config_service=config_service)

        with patch(
            "app.api.routes.mcp_servers.resolve_mcp_instances_with_inheritance",
            new=AsyncMock(return_value=[_api_token_instance()]),
        ):
            result = await get_my_mcp_servers(request, include_tools=False)

        assert result["instances"][0]["connectedAt"] == connected_at

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
                "app.agents.mcp.discovery.discover_tool_listing",
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
            patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(return_value=ToolListing(tools=[tool]))),
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
                "app.agents.mcp.discovery.discover_tool_listing",
                new=AsyncMock(side_effect=MCPConnectionError("down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_instance_tools(request, "inst-1")
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_get_instance_tools_connection_error_is_not_echoed(self) -> None:
        from app.utils.user_messages import action_failed

        request = _admin_request()
        error = MCPConnectionError("SENTINEL connect to http://10.0.0.7:9000/mcp refused")
        with (
            patch("app.api.routes.mcp_servers._get_org_instance", new=AsyncMock(return_value=_api_token_instance())),
            patch(
                "app.api.routes.mcp_servers._resolve_effective_user_auth",
                new=AsyncMock(return_value={"isAuthenticated": True, "credentials": {"apiToken": "t"}}),
            ),
            patch("app.agents.mcp.discovery.discover_tool_listing", new=AsyncMock(side_effect=error)),
            patch("app.api.routes.mcp_servers.logger") as mock_logger,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_instance_tools(request, "inst-1")
        assert exc.value.status_code == 502
        assert exc.value.detail == action_failed("load this MCP server's tools")
        assert "SENTINEL" not in exc.value.detail
        assert exc.value.__cause__ is error
        log_call = mock_logger.warning.call_args
        assert log_call.kwargs["exc_info"] is True
        assert error in log_call.args


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
