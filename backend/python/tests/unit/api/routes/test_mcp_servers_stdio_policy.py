"""Catalog servers keep their template's launch settings; custom STDIO follows the policy."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.client import build_transport
from app.agents.mcp.errors import MCPConnectionError
from app.agents.mcp.models import MCPAuthMode, MCPServerInstanceConfig, MCPTransport
from app.agents.mcp.registry import get_mcp_registry
from app.agents.mcp.service import instance_config_from_dict
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import create_instance, update_instance
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _caller_is_admin() -> "Iterator[None]":
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
        yield


@pytest.fixture
def registry() -> Any:  # noqa: ANN401
    catalog = get_mcp_registry()
    catalog.auto_discover_templates()
    return catalog


def _payload(**fields: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    return MCPServerInstanceConfig(**fields)


async def _bad_request(coro: Any) -> str:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 400
    return exc.value.detail


async def _forbidden(coro: Any) -> str:  # noqa: ANN401
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 403
    return exc.value.detail


class TestCatalogInstances:
    async def test_a_command_override_is_refused(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = _payload(
            name="GitHub", type_id="github", transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.OAUTH,
            command="bash", args=["-c", "id"],
        )

        detail = await _bad_request(create_instance(route_request(store, registry=registry), payload))

        assert "always runs its catalog command" in detail
        assert store.writes == []

    async def test_url_and_sign_in_overrides_are_ignored(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = _payload(
            name="GitHub", type_id="github", transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.OAUTH,
            url="https://evil.example.net/mcp",
            authorization_url="https://evil.example.net/auth", token_url="https://evil.example.net/token",
            scopes=["everything"],
        )

        record = await create_instance(route_request(store, registry=registry), payload)

        assert record["url"] == "https://api.githubcopilot.com/mcp/"
        assert record["tokenUrl"] == "https://github.com/login/oauth/access_token"
        assert record["authorizationUrl"] == "https://github.com/login/oauth/authorize"
        assert record["scopes"] == ["repo", "read:org"]
        assert record["command"] is None
        assert record["args"] == []

    async def test_a_different_transport_is_rejected(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = _payload(
            name="GitHub", type_id="github", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN,
            command="bash", args=["-c", "id"],
        )

        detail = await _bad_request(create_instance(route_request(store, registry=registry), payload))

        assert "streamable_http" in detail
        assert store.writes == []

    async def test_an_unsupported_auth_mode_is_rejected(self, registry: Any) -> None:  # noqa: ANN401
        payload = _payload(name="Exa", type_id="exa", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.OAUTH)

        detail = await _bad_request(create_instance(route_request(FakeConfigService(), registry=registry), payload))

        assert "oauth" in detail

    async def test_update_also_keeps_the_template_command(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService({
            "/services/mcp/instances/inst-1": {
                "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Exa", "typeId": "exa",
                "transport": "stdio", "authMode": "api_token", "command": "npx", "args": ["-y", "exa-mcp-server"],
                "isCustom": False, "createdAt": 1, "updatedAt": 1,
            },
        })
        payload = _payload(name="Exa", type_id="exa", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN)

        record = await update_instance(route_request(store, registry=registry), "inst-1", payload)

        assert record["args"] == ["-y", "exa-mcp-server@3.4.1"]


class TestCustomStdio:

    @pytest.fixture(autouse=True)
    def _custom_stdio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")

    async def test_a_shell_command_is_rejected(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = _payload(
            name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, command="bash", args=["-c", "id"],
        )

        detail = await _bad_request(create_instance(route_request(store, registry=registry), payload))

        assert "Shells" in detail
        assert store.writes == []

    async def test_a_code_loading_env_name_is_rejected(self, registry: Any) -> None:  # noqa: ANN401
        payload = _payload(
            name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN, command="npx",
            args=["-y", "@acme/mcp"], required_env=["NODE_OPTIONS"],
        )

        detail = await _bad_request(create_instance(route_request(FakeConfigService(), registry=registry), payload))

        assert "NODE_OPTIONS" in detail

    async def test_an_allowed_launcher_is_saved(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = _payload(
            name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN, command="npx",
            args=["-y", "@acme/mcp"], required_env=["ACME_TOKEN"],
        )

        record = await create_instance(route_request(store, registry=registry), payload)

        assert (record["command"], record["args"], record["requiredEnv"]) == ("npx", ["-y", "@acme/mcp"], ["ACME_TOKEN"])

    async def test_custom_stdio_can_be_switched_off(self, registry: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "false")
        payload = _payload(name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, command="npx")

        detail = await _forbidden(create_instance(route_request(FakeConfigService(), registry=registry), payload))

        assert "disabled" in detail


class TestOffByDefault:
    @pytest.fixture(autouse=True)
    def _unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)

    async def test_a_custom_stdio_server_is_refused_and_says_how_to_enable_it(self, registry: Any) -> None:  # noqa: ANN401
        payload = _payload(name="x", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.NONE, command="npx", args=["-y", "pkg"])

        detail = await _forbidden(create_instance(route_request(FakeConfigService(), registry=registry), payload))

        assert "MCP_ALLOW_CUSTOM_STDIO=true" in detail and "run a command on the PipesHub server" in detail

    async def test_a_catalog_stdio_server_is_still_created(self, registry: Any) -> None:  # noqa: ANN401
        template = next(t for t in registry.list_templates() if t.transport == MCPTransport.STDIO)
        payload = _payload(
            name=template.display_name, type_id=template.type_id, transport=MCPTransport.STDIO,
            auth_mode=template.default_auth_mode,
        )

        record = await create_instance(route_request(FakeConfigService(), registry=registry), payload)

        assert record["command"] == template.command

    async def test_the_catalog_says_whether_custom_stdio_is_allowed(self, registry: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
        from app.api.routes.mcp_servers import list_catalog

        off = await list_catalog(route_request(FakeConfigService(), registry=registry), page=1, limit=5, search=None)
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
        on = await list_catalog(route_request(FakeConfigService(), registry=registry), page=1, limit=5, search=None)

        assert (off["customStdioAllowed"], on["customStdioAllowed"]) == (False, True)


def _stored(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    record: dict[str, Any] = {
        "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "x", "transport": "stdio",
        "authMode": "none", "createdAt": 1, "updatedAt": 1,
    }
    record.update(fields)
    return record


class TestRuntime:

    @pytest.fixture(autouse=True)
    def _custom_stdio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")

    def test_a_tampered_catalog_record_runs_the_template_command(self) -> None:
        config = instance_config_from_dict(_stored(typeId="exa", command="bash", args=["-c", "id"], transport="stdio"))

        assert (config.command, config.args, config.required_env) == (
            "npx", ["-y", "exa-mcp-server@3.4.1"], ["EXA_API_KEY"],
        )

    def test_an_unknown_type_keeps_the_stored_values(self) -> None:
        config = instance_config_from_dict(_stored(typeId="retired_server", command="npx", args=["-y", "old"]))

        assert (config.command, config.args) == ("npx", ["-y", "old"])

    def test_a_custom_shell_record_cannot_be_launched(self) -> None:
        config = instance_config_from_dict(_stored(command="bash", args=["-c", "id"], isCustom=True))

        with pytest.raises(MCPConnectionError, match="Shells"):
            build_transport(config)

    def test_a_record_with_a_code_loading_env_name_cannot_be_launched(self) -> None:
        config = instance_config_from_dict(_stored(command="npx", args=["-y", "x"], requiredEnv=["NODE_OPTIONS"]))

        with pytest.raises(MCPConnectionError, match="NODE_OPTIONS"):
            build_transport(config)

    def test_a_catalog_stdio_record_builds(self) -> None:
        # Saved before the version was pinned: the template's pinned args are what runs.
        config = instance_config_from_dict(_stored(typeId="exa", command="npx", args=["-y", "exa-mcp-server"]))
        assert config.args == ["-y", "exa-mcp-server@3.4.1"]

        with patch("app.agents.mcp.client.stdio_client") as stdio:
            build_transport(config, env={"EXA_API_KEY": "k"})

        stdio.assert_called_once()
