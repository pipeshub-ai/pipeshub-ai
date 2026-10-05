"""A connector and an agent toolset can register an OAuth config under the same name
("Drive", "Slack", ...). Each side must keep its own: a connector OAuth app built from
the toolset's entry was saved with the toolset's callback URL and scopes."""

import importlib
from unittest.mock import MagicMock, patch

import pytest

# The toolset routes import the connector router through a cycle; load the router first.
import app.connectors.api.router as connector_router
from app.api.routes.toolsets import get_toolset_schema
from app.connectors.core.registry.auth_builder import OAuthConfig, OAuthScopeConfig
from app.connectors.core.registry.oauth_config_registry import (
    TOOLSET_SOURCE,
    OAuthConfigRegistry,
    get_oauth_config_registry,
)
from app.connectors.core.registry.types import AuthField

BASE_URL = "https://pipeshub.example"
SHARED_NAMES = ["Calendar", "Confluence", "Drive", "Gmail", "Jira", "Salesforce", "Slack", "Zoom"]
TOOLSET_MODULES = [
    "app.agents.actions.google.calendar.calendar",
    "app.agents.actions.confluence.confluence",
    "app.agents.actions.google.drive.drive",
    "app.agents.actions.google.gmail.gmail",
    "app.agents.actions.jira.jira",
    "app.agents.actions.salesforce.salesforce",
    "app.agents.actions.slack.slack",
    "app.agents.actions.zoom.zoom",
]


def _connector_drive() -> OAuthConfig:
    return OAuthConfig(
        connector_name="Drive",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        redirect_uri="connectors/oauth/callback/Drive",
        scopes=OAuthScopeConfig(personal_sync=["drive.readonly"]),
        auth_fields=[
            AuthField(name="clientId", display_name="Client ID"),
            AuthField(name="clientSecret", display_name="Client Secret", is_secret=True),
            AuthField(name="connectorOnly", display_name="Connector only", required=False),
        ],
        app_group="Google Workspace",
    )


def _toolset_drive() -> OAuthConfig:
    return OAuthConfig(
        connector_name="Drive",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        redirect_uri="toolsets/oauth/callback/drive",
        scopes=OAuthScopeConfig(agent=["drive"]),
        auth_fields=[
            AuthField(name="clientId", display_name="Client ID"),
            AuthField(name="clientSecret", display_name="Client Secret", is_secret=True),
        ],
        token_access_type="offline",
        additional_params={"prompt": "consent"},
        app_group="Toolsets",
    )


def _startup_order_registry() -> OAuthConfigRegistry:
    """Connectors register first and toolsets second, as connectors_main does."""
    registry = OAuthConfigRegistry()
    registry.register(_connector_drive())
    registry.register(_toolset_drive(), source=TOOLSET_SOURCE)
    return registry


class _MemoryConfigService:
    def __init__(self) -> None:
        self.store: dict = {}

    async def get_config(self, path: str, default: list | dict | None = None, use_cache: bool = True) -> list | dict | None:
        return self.store.get(path, default)

    async def set_config(self, path: str, value: list | dict) -> bool:
        self.store[path] = value
        return True


class TestRegistryKeepsEachSide:
    def test_connector_lookup_keeps_the_connector_entry(self) -> None:
        registry = _startup_order_registry()
        assert registry.get_config("Drive").redirect_uri == "connectors/oauth/callback/Drive"
        assert registry.get_config("Drive", source=TOOLSET_SOURCE).redirect_uri == "toolsets/oauth/callback/drive"

    def test_toolset_lookup_keeps_the_toolset_entry_whatever_the_order(self) -> None:
        registry = OAuthConfigRegistry()
        registry.register(_toolset_drive(), source=TOOLSET_SOURCE)
        registry.register(_connector_drive())
        assert registry.get_config("Drive", source=TOOLSET_SOURCE).redirect_uri == "toolsets/oauth/callback/drive"
        assert registry.get_config("Drive").redirect_uri == "connectors/oauth/callback/Drive"

    def test_name_only_one_side_registers_resolves_from_either(self) -> None:
        registry = OAuthConfigRegistry()
        meet = OAuthConfig(connector_name="Meet", authorize_url="a", token_url="t", redirect_uri="toolsets/oauth/callback/meet")
        registry.register(meet, source=TOOLSET_SOURCE)
        assert registry.get_config("Meet") is meet
        assert registry.has_config("Meet") is True

    def test_shared_name_listed_once_with_the_connector_entry(self) -> None:
        registry = _startup_order_registry()
        assert registry.list_connectors() == ["Drive"]
        assert registry.get_all_configs()["Drive"].redirect_uri == "connectors/oauth/callback/Drive"

    def test_removing_one_side_keeps_the_other(self) -> None:
        registry = _startup_order_registry()
        assert registry.remove_config("Drive", source=TOOLSET_SOURCE) is True
        assert registry.get_config("Drive", source=TOOLSET_SOURCE).redirect_uri == "connectors/oauth/callback/Drive"

    def test_unknown_source_is_refused(self) -> None:
        with pytest.raises(ValueError):
            OAuthConfigRegistry().register(_connector_drive(), source="mcp")

    def test_an_empty_source_is_refused_rather_than_removing_both_sides(self) -> None:
        registry = _startup_order_registry()
        with pytest.raises(ValueError):
            registry.remove_config("Drive", source="")
        assert registry.get_config("Drive", source=TOOLSET_SOURCE).redirect_uri == "toolsets/oauth/callback/drive"


class TestConnectorOAuthAppDefaults:
    @pytest.mark.asyncio
    async def test_new_connector_app_takes_the_connector_redirect_scopes_and_fields(self) -> None:
        registry = _startup_order_registry()
        config_service = _MemoryConfigService()
        with patch(
            "app.connectors.core.registry.oauth_config_registry.get_oauth_config_registry",
            return_value=registry,
        ):
            app_id = await connector_router._create_or_update_oauth_config(
                connector_type="Drive",
                auth_config={"clientId": "cid", "clientSecret": "not-a-secret", "connectorOnly": "kept"},
                instance_name="Drive app",
                user_id="u1",
                org_id="o1",
                is_admin=True,
                config_service=config_service,
                base_url=BASE_URL,
                connector_scope="personal",
            )

        saved = next(c for c in config_service.store["/services/oauth/drive"] if c["_id"] == app_id)
        assert saved["redirectUri"] == f"{BASE_URL}/connectors/oauth/callback/Drive"
        assert saved["scopes"] == {"personal_sync": ["drive.readonly"], "team_sync": [], "agent": []}
        assert saved["config"]["connectorOnly"] == "kept"
        assert "additionalParams" not in saved
        assert "tokenAccessType" not in saved
        assert saved["appGroup"] == "Google Workspace"


class TestToolsetSchemaMetadata:
    @pytest.mark.asyncio
    async def test_toolset_schema_reads_the_toolset_entry(self) -> None:
        oauth_registry = OAuthConfigRegistry()
        oauth_registry.register(_toolset_drive(), source=TOOLSET_SOURCE)
        oauth_registry.register(_connector_drive())

        toolset_registry = MagicMock()
        toolset_registry.get_toolset_metadata.return_value = {
            "name": "Drive", "display_name": "Drive", "description": "",
            "category": "file_storage", "supported_auth_types": ["OAUTH"], "tools": [],
        }
        request = MagicMock()
        request.app.state.toolset_registry = toolset_registry
        request.app.state.oauth_config_registry = oauth_registry

        result = await get_toolset_schema("Drive", request)
        assert result["toolset"]["oauthConfig"]["appGroup"] == "Toolsets"


class TestRealRegistrations:
    def test_each_shared_name_keeps_the_connector_and_toolset_callbacks_apart(self) -> None:
        importlib.import_module("app.connectors.core.factory.connector_factory")
        for module in TOOLSET_MODULES:
            importlib.import_module(module)
        registry = get_oauth_config_registry()

        wrong = {}
        for name in SHARED_NAMES:
            connector = registry.get_config(name)
            toolset = registry.get_config(name, source=TOOLSET_SOURCE)
            if not connector.redirect_uri.startswith("connectors/oauth/callback/"):
                wrong[name] = ("connector", connector.redirect_uri)
            elif not toolset.redirect_uri.startswith("toolsets/oauth/callback/"):
                wrong[name] = ("toolset", toolset.redirect_uri)
        assert wrong == {}
