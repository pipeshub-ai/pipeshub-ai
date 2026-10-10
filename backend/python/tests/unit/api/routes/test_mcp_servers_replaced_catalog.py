"""A replaced catalog entry (the archived community Slack server) makes no new servers, while the
servers already made from it keep working; its replacement is Slack's own hosted server."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.agents.mcp.models import MCPAuthMode, MCPServerInstanceConfig, MCPTransport
from app.agents.mcp.registry import get_mcp_registry
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import create_instance, update_instance
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator

_OLD_SLACK = {
    "_id": "inst-1", "orgId": "org-1", "createdBy": "admin-1", "name": "Slack", "typeId": "slack",
    "transport": "stdio", "authMode": "api_token", "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-slack@2025.4.25"], "isCustom": False, "createdAt": 1, "updatedAt": 1,
}


@pytest.fixture(autouse=True)
def _caller_is_admin() -> "Iterator[None]":
    with patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=True)):
        yield


@pytest.fixture
def registry() -> Any:  # noqa: ANN401
    catalog = get_mcp_registry()
    catalog.auto_discover_templates()
    return catalog


def _old_slack_payload(**fields: Any) -> MCPServerInstanceConfig:  # noqa: ANN401
    return MCPServerInstanceConfig(
        name="Slack", type_id="slack", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN, **fields,
    )


class TestTheCatalog:
    def test_every_replacement_exists_and_is_itself_offered(self, registry: Any) -> None:  # noqa: ANN401
        for template in registry.list_templates():
            if template.replaced_by:
                replacement = registry.get_template(template.replaced_by)
                assert replacement is not None, template.type_id
                assert replacement.replaced_by is None, template.type_id

    def test_the_community_slack_server_is_replaced_by_slacks_own(self, registry: Any) -> None:  # noqa: ANN401
        assert registry.get_template("slack").replaced_by == "slack_official"

    def test_slacks_own_server_signs_in_with_the_admins_slack_app(self, registry: Any) -> None:  # noqa: ANN401
        slack = registry.get_template("slack_official")
        assert slack.transport == MCPTransport.STREAMABLE_HTTP
        assert slack.default_url == "https://mcp.slack.com/mcp"
        assert slack.supported_auth_modes == [MCPAuthMode.OAUTH]
        # Slack registers no clients on the fly: an admin enters the workspace's own app.
        assert slack.supports_dcr is False
        assert slack.token_url == "https://slack.com/api/oauth.v2.user.access"
        # Sign-in asks for the scopes the server itself publishes.
        assert slack.default_scopes == []

    def test_the_catalog_still_lists_the_replaced_entry_with_its_replacement(self, registry: Any) -> None:  # noqa: ANN401
        """Existing servers' pages look their entry up in the catalog."""
        dumped = {t.type_id: t.model_dump(by_alias=True) for t in registry.list_templates()}
        assert dumped["slack"]["replacedBy"] == "slack_official"
        assert dumped["slack_official"]["replacedBy"] is None


class TestServersFromAReplacedEntry:
    async def test_a_new_one_is_refused_and_names_the_replacement(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()

        with pytest.raises(HTTPException) as exc:
            await create_instance(route_request(store, registry=registry), _old_slack_payload())

        assert exc.value.status_code == 400
        assert "no longer offered for new servers" in exc.value.detail
        assert "Add Slack instead" in exc.value.detail
        assert not any(key.startswith("/services/mcp/instances/") for key in store.writes)

    async def test_an_existing_one_can_still_be_edited(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService({"/services/mcp/instances/inst-1": dict(_OLD_SLACK)})

        record = await update_instance(
            route_request(store, registry=registry), "inst-1", _old_slack_payload(description="Team Slack"),
        )

        assert record["typeId"] == "slack"
        assert record["args"] == ["-y", "@modelcontextprotocol/server-slack@2025.4.25"]

    async def test_another_server_cant_be_switched_to_it(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService({"/services/mcp/instances/inst-2": {
            **_OLD_SLACK, "_id": "inst-2", "typeId": None, "isCustom": True,
            "transport": "streamable_http", "url": "https://mcp.example.com/mcp", "authMode": "none",
        }})

        with pytest.raises(HTTPException) as exc:
            await update_instance(route_request(store, registry=registry), "inst-2", _old_slack_payload())

        assert exc.value.status_code == 400

    async def test_slacks_own_server_can_be_added(self, registry: Any) -> None:  # noqa: ANN401
        store = FakeConfigService()
        payload = MCPServerInstanceConfig(
            name="Slack", type_id="slack_official", transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.OAUTH,
        )

        record = await create_instance(route_request(store, registry=registry), payload)

        assert record["url"] == "https://mcp.slack.com/mcp"
        assert record["authMode"] == "oauth"
