"""Unit tests for app.api.routes.mcp_servers helpers and route handlers."""

import json
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Pre-import stubs for heavy infrastructure modules that mcp_servers.py
# transitively imports.  These are mocked before any app.api.routes import
# so the import chain doesn't pull in etcd3, aiohttp, etc.
# ---------------------------------------------------------------------------
_MODULES_TO_STUB = [
    "app.containers",
    "app.containers.connector",
    "app.utils.time_conversion",
]

_stashed: dict = {}
for _mod_name in _MODULES_TO_STUB:
    if _mod_name not in sys.modules:
        _stub = types.ModuleType(_mod_name)
        _stub.__path__ = []
        sys.modules[_mod_name] = _stub
        _stashed[_mod_name] = True

# Provide the symbols that mcp_servers.py actually imports from these stubs
_containers_connector = sys.modules["app.containers.connector"]
_containers_connector.ConnectorAppContainer = MagicMock()

_time_module = sys.modules["app.utils.time_conversion"]
_time_module.get_epoch_timestamp_in_ms = MagicMock(return_value=1700000000000)

# Now we can safely import the route module
from app.api.routes.mcp_servers import (  # noqa: E402
    _get_registry,
    _get_user_context,
    _parse_request_json,
    authenticate_instance,
    create_instance,
    get_catalog,
    get_catalog_item,
    get_instances,
    get_my_mcp_servers,
)


@pytest.fixture
def mock_request():
    """Request with headers and app.state for MCP route tests."""
    req = MagicMock()
    req.headers = {
        "x-user-id": "user-1",
        "x-org-id": "org-1",
        "x-is-admin": "false",
    }
    registry = MagicMock()
    req.app = MagicMock()
    req.app.state = types.SimpleNamespace(mcp_server_registry=registry)
    req.body = AsyncMock(return_value=b"{}")
    return req


@pytest.fixture
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value=[])
    svc.set_config = AsyncMock()
    svc.delete_config = AsyncMock()
    svc.list_keys_in_directory = AsyncMock(return_value=[])
    return svc


class TestGetUserContext:
    def test_valid_headers(self):
        request = MagicMock()
        request.headers = {"x-user-id": "u1", "x-org-id": "o1"}
        result = _get_user_context(request)
        assert result == {"user_id": "u1", "org_id": "o1"}

    def test_missing_user_id(self):
        request = MagicMock()
        request.headers = {"x-org-id": "o1"}
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == 401

    def test_missing_org_id(self):
        request = MagicMock()
        request.headers = {"x-user-id": "u1"}
        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == 400


class TestParseRequestJson:
    def test_valid_json(self):
        request = MagicMock()
        result = _parse_request_json(request, b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_empty_body(self):
        request = MagicMock()
        assert _parse_request_json(request, b"") == {}

    def test_invalid_json(self):
        request = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            _parse_request_json(request, b"not json")
        assert exc_info.value.status_code == 400


class TestGetRegistry:
    def test_uses_registry_from_app_state(self):
        reg = MagicMock()
        request = MagicMock()
        request.app.state = types.SimpleNamespace(mcp_server_registry=reg)
        assert _get_registry(request) is reg

    def test_fallback_get_mcp_server_registry(self):
        request = MagicMock()
        request.app.state = types.SimpleNamespace()
        fallback = MagicMock()
        with patch("app.api.routes.mcp_servers.get_mcp_server_registry", return_value=fallback):
            assert _get_registry(request) is fallback


class TestGetCatalog:
    async def test_returns_catalog_list(self, mock_request):
        registry = mock_request.app.state.mcp_server_registry
        registry.list_templates.return_value = {
            "items": [{"type_id": "github", "display_name": "GitHub"}],
            "total": 5,
            "page": 1,
            "limit": 20,
            "totalPages": 1,
        }
        result = await get_catalog(mock_request, page=1, limit=20, search=None)
        assert result["status"] == "success"
        assert result["items"] == [{"type_id": "github", "display_name": "GitHub"}]
        assert result["total"] == 5
        registry.list_templates.assert_called_once_with(search=None, page=1, limit=20)

    async def test_with_search(self, mock_request):
        registry = mock_request.app.state.mcp_server_registry
        registry.list_templates.return_value = {"items": [], "total": 0, "page": 1, "limit": 20, "totalPages": 0}
        await get_catalog(mock_request, page=1, limit=20, search="github")
        registry.list_templates.assert_called_once_with(search="github", page=1, limit=20)


class TestGetCatalogItem:
    async def test_found(self, mock_request):
        registry = mock_request.app.state.mcp_server_registry
        registry.get_template_schema.return_value = {"type_id": "slack", "configSchema": {}}
        result = await get_catalog_item("slack", mock_request)
        assert result["status"] == "success"
        assert "template" in result
        assert result["template"]["type_id"] == "slack"

    async def test_not_found(self, mock_request):
        mock_request.app.state.mcp_server_registry.get_template_schema.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await get_catalog_item("missing", mock_request)
        assert exc_info.value.status_code == 404


class TestGetInstances:
    async def test_returns_instances(self, mock_request, mock_config_service):
        from app.agents.constants.mcp_server_constants import get_mcp_server_instances_path

        instances = [
            {"_id": "i1", "orgId": "org-1", "instanceName": "A"},
            {"_id": "i2", "orgId": "other", "instanceName": "B"},
        ]
        mock_config_service.get_config = AsyncMock(return_value=instances)

        result = await get_instances(mock_request, mock_config_service)
        assert result["status"] == "success"
        assert len(result["instances"]) == 1
        assert result["instances"][0]["_id"] == "i1"
        mock_config_service.get_config.assert_awaited_with(
            get_mcp_server_instances_path(),
            default=[],
        )


class TestCreateInstance:
    async def test_success(self, mock_request, mock_config_service):
        from app.agents.constants.mcp_server_constants import get_mcp_server_instances_path

        mock_request.app.state.mcp_server_registry.get_template.return_value = None
        mock_request.headers["x-is-admin"] = "true"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"instanceName": "My MCP", "serverType": "custom"}).encode()
        )
        mock_config_service.get_config = AsyncMock(return_value=[])

        result = await create_instance(mock_request, mock_config_service)
        assert result["status"] == "success"
        assert result["message"] == "MCP server instance created successfully."
        inst = result["instance"]
        assert inst["instanceName"] == "My MCP"
        uuid.UUID(inst["_id"])
        mock_config_service.set_config.assert_awaited()
        call_args = mock_config_service.set_config.await_args
        assert call_args[0][0] == get_mcp_server_instances_path()
        saved = call_args[0][1]
        assert len(saved) == 1
        assert saved[0]["instanceName"] == "My MCP"

    async def test_not_admin_forbidden(self, mock_request, mock_config_service):
        mock_request.headers["x-is-admin"] = "false"
        mock_request.body = AsyncMock(return_value=json.dumps({"instanceName": "X"}).encode())
        with pytest.raises(HTTPException) as exc_info:
            await create_instance(mock_request, mock_config_service)
        assert exc_info.value.status_code == 403

    async def test_missing_instance_name(self, mock_request, mock_config_service):
        mock_request.app.state.mcp_server_registry.get_template.return_value = None
        mock_request.headers["x-is-admin"] = "true"
        mock_request.body = AsyncMock(return_value=json.dumps({"serverType": "custom"}).encode())
        with pytest.raises(HTTPException) as exc_info:
            await create_instance(mock_request, mock_config_service)
        assert exc_info.value.status_code == 400


class TestAuthenticateInstance:
    async def test_success_api_token(self, mock_request, mock_config_service):
        from app.agents.constants.mcp_server_constants import (
            get_mcp_server_config_path,
            get_mcp_server_instances_path,
        )

        instance_id = "inst-uuid"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"auth": {"apiToken": "secret-token"}}).encode()
        )

        async def get_config_side_effect(path, default=None):
            if path == get_mcp_server_instances_path():
                return [
                    {
                        "_id": instance_id,
                        "orgId": "org-1",
                        "authMode": "api",
                        "serverType": "github",
                        "transport": "stdio",
                        "command": "",
                        "args": [],
                        "url": "",
                        "requiredEnv": [],
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=get_config_side_effect)

        result = await authenticate_instance(instance_id, mock_request, mock_config_service)
        assert result["status"] == "success"
        assert result["isAuthenticated"] is True
        mock_config_service.set_config.assert_awaited_once()
        path, saved_auth = mock_config_service.set_config.await_args[0]
        assert path == get_mcp_server_config_path(instance_id, "user-1")
        assert saved_auth["isAuthenticated"] is True
        assert saved_auth["auth"]["apiToken"] == "secret-token"

    async def test_instance_not_found(self, mock_request, mock_config_service):
        mock_request.body = AsyncMock(return_value=b"{}")
        mock_config_service.get_config = AsyncMock(return_value=[])
        with pytest.raises(HTTPException) as exc_info:
            await authenticate_instance("missing-id", mock_request, mock_config_service)
        assert exc_info.value.status_code == 404


class TestGetMyMcpServers:
    async def test_returns_merged_view(self, mock_request, mock_config_service):
        from app.agents.constants.mcp_server_constants import (
            get_mcp_server_config_path,
            get_mcp_server_instances_path,
        )

        inst_id = "i1"

        async def get_config_side_effect(path, default=None):
            if path == get_mcp_server_instances_path():
                return [
                    {
                        "_id": inst_id,
                        "orgId": "org-1",
                        "enabled": True,
                        "instanceName": "n1",
                        "displayName": "Display One",
                        "serverType": "custom",
                        "description": "d",
                        "transport": "stdio",
                        "authMode": "none",
                        "supportedAuthTypes": [],
                        "iconPath": "",
                    }
                ]
            if path == get_mcp_server_config_path(inst_id, "user-1"):
                return {"isAuthenticated": True}
            return default

        mock_config_service.get_config = AsyncMock(side_effect=get_config_side_effect)

        result = await get_my_mcp_servers(
            mock_request,
            page=1,
            limit=20,
            search=None,
            include_registry=False,
            auth_status=None,
            config_service=mock_config_service,
        )
        assert result["status"] == "success"
        assert len(result["mcpServers"]) == 1
        row = result["mcpServers"][0]
        assert row["instanceId"] == inst_id
        assert row["isAuthenticated"] is True

    async def test_include_registry(self, mock_request, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value=[
                {
                    "_id": "cfg-1",
                    "orgId": "org-1",
                    "enabled": True,
                    "instanceName": "custom-a",
                    "displayName": "Custom A",
                    "serverType": "custom",
                    "description": "",
                    "transport": "stdio",
                    "authMode": "none",
                    "supportedAuthTypes": [],
                    "iconPath": "",
                }
            ]
        )
        registry = mock_request.app.state.mcp_server_registry
        registry.list_templates.return_value = {
            "items": [
                {
                    "type_id": "github",
                    "display_name": "GitHub MCP",
                    "description": "desc",
                    "transport": "stdio",
                    "auth_mode": "none",
                    "supported_auth_types": [],
                    "icon_path": "/icons/gh.png",
                }
            ]
        }

        result = await get_my_mcp_servers(
            mock_request,
            page=1,
            limit=20,
            search=None,
            include_registry=True,
            auth_status=None,
            config_service=mock_config_service,
        )
        from_registry = [m for m in result["mcpServers"] if m.get("isFromRegistry")]
        assert len(from_registry) == 1
        assert from_registry[0]["serverType"] == "github"
        assert from_registry[0]["isConfigured"] is False

    async def test_search_filter(self, mock_request, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value=[
                {
                    "_id": "a",
                    "orgId": "org-1",
                    "enabled": True,
                    "instanceName": "one",
                    "displayName": "Alpha Tool",
                    "serverType": "alpha",
                    "description": "no match",
                    "transport": "stdio",
                    "authMode": "none",
                    "supportedAuthTypes": [],
                    "iconPath": "",
                },
                {
                    "_id": "b",
                    "orgId": "org-1",
                    "enabled": True,
                    "instanceName": "two",
                    "displayName": "Company GitHub Integration",
                    "serverType": "custom",
                    "description": "",
                    "transport": "stdio",
                    "authMode": "none",
                    "supportedAuthTypes": [],
                    "iconPath": "",
                },
            ]
        )

        result = await get_my_mcp_servers(
            mock_request,
            page=1,
            limit=20,
            search="github",
            include_registry=False,
            auth_status=None,
            config_service=mock_config_service,
        )
        assert len(result["mcpServers"]) == 1
        assert result["mcpServers"][0]["displayName"] == "Company GitHub Integration"
