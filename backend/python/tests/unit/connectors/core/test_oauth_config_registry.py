"""Unit tests for app.connectors.core.registry.oauth_config_registry."""

import pytest
from unittest.mock import MagicMock, patch

from app.connectors.core.registry.auth_builder import (
    OAuthConfig,
    OAuthScopeConfig,
    OAuthScopeType,
)
from app.connectors.core.registry.oauth_config_registry import (
    OAuthConfigRegistry,
    get_oauth_config_registry,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scope_config(personal=None, team=None, agent=None):
    return OAuthScopeConfig(
        personal_sync=personal or [],
        team_sync=team or [],
        agent=agent or [],
    )


def _make_oauth_config(
    name="TestConnector",
    scopes=None,
    auth_fields=None,
    token_access_type=None,
    additional_params=None,
    icon_path="/icons/connectors/test.svg",
    app_group="TestGroup",
    app_description="A test connector",
    app_categories=None,
    documentation_links=None,
):
    return OAuthConfig(
        connector_name=name,
        authorize_url="https://example.com/authorize",
        token_url="https://example.com/token",
        redirect_uri="connectors/oauth/callback/test",
        scopes=scopes or _make_scope_config(),
        auth_fields=auth_fields or [],
        token_access_type=token_access_type,
        additional_params=additional_params or {},
        icon_path=icon_path,
        app_group=app_group,
        app_description=app_description,
        app_categories=app_categories or ["category1"],
        documentation_links=documentation_links or [],
    )


# ---------------------------------------------------------------------------
# Registration and retrieval
# ---------------------------------------------------------------------------

class TestOAuthConfigRegistryBasics:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    def test_register_and_get_config(self):
        config = _make_oauth_config("Jira")
        self.registry.register(config)
        assert self.registry.get_config("Jira") is config

    def test_get_config_missing_returns_none(self):
        assert self.registry.get_config("NonExistent") is None

    def test_register_overwrites_existing(self):
        config1 = _make_oauth_config("Jira", app_description="v1")
        config2 = _make_oauth_config("Jira", app_description="v2")
        self.registry.register(config1)
        self.registry.register(config2)
        assert self.registry.get_config("Jira").app_description == "v2"

    def test_has_config_true(self):
        self.registry.register(_make_oauth_config("Jira"))
        assert self.registry.has_config("Jira") is True

    def test_has_config_false(self):
        assert self.registry.has_config("Jira") is False

    def test_list_connectors_empty(self):
        assert self.registry.list_connectors() == []

    def test_list_connectors(self):
        self.registry.register(_make_oauth_config("Jira"))
        self.registry.register(_make_oauth_config("Slack"))
        names = self.registry.list_connectors()
        assert "Jira" in names
        assert "Slack" in names

    def test_remove_config_existing(self):
        self.registry.register(_make_oauth_config("Jira"))
        assert self.registry.remove_config("Jira") is True
        assert self.registry.get_config("Jira") is None

    def test_remove_config_missing(self):
        assert self.registry.remove_config("NonExistent") is False

    def test_get_all_configs_returns_copy(self):
        config = _make_oauth_config("Jira")
        self.registry.register(config)
        all_configs = self.registry.get_all_configs()
        assert all_configs == {"Jira": config}
        # Mutating copy should not affect registry
        all_configs["Jira"] = None
        assert self.registry.get_config("Jira") is config


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestGetMetadata:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    def test_returns_metadata_from_config(self):
        config = _make_oauth_config(
            "Jira",
            icon_path="/icons/jira.svg",
            app_group="Atlassian",
            app_description="Jira integration",
            app_categories=["PM", "Dev"],
        )
        self.registry.register(config)
        metadata = self.registry.get_metadata("Jira")
        assert metadata["iconPath"] == "/icons/jira.svg"
        assert metadata["appGroup"] == "Atlassian"
        assert metadata["appDescription"] == "Jira integration"
        assert metadata["appCategories"] == ["PM", "Dev"]

    def test_defaults_when_no_config(self):
        metadata = self.registry.get_metadata("Unknown")
        assert metadata["iconPath"] == "/icons/connectors/default.svg"
        assert metadata["appGroup"] == ""
        assert metadata["appDescription"] == ""
        assert metadata["appCategories"] == []


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

class TestGetScopes:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()
        scopes = _make_scope_config(
            personal=["read", "write"],
            team=["admin.read"],
            agent=["agent.execute"],
        )
        self.registry.register(_make_oauth_config("Jira", scopes=scopes))

    def test_get_scopes_no_type_returns_all(self):
        scopes = self.registry.get_scopes("Jira")
        assert sorted(scopes) == sorted(["admin.read", "agent.execute", "read", "write"])

    def test_get_scopes_personal(self):
        scopes = self.registry.get_scopes("Jira", OAuthScopeType.PERSONAL_SYNC)
        assert scopes == ["read", "write"]

    def test_get_scopes_team(self):
        scopes = self.registry.get_scopes("Jira", OAuthScopeType.TEAM_SYNC)
        assert scopes == ["admin.read"]

    def test_get_scopes_agent(self):
        scopes = self.registry.get_scopes("Jira", OAuthScopeType.AGENT)
        assert scopes == ["agent.execute"]

    def test_get_scopes_missing_connector(self):
        assert self.registry.get_scopes("NonExistent") == []

    def test_get_scopes_missing_connector_with_type(self):
        assert self.registry.get_scopes("NonExistent", OAuthScopeType.AGENT) == []


# ---------------------------------------------------------------------------
# OAuth discovery helpers
# ---------------------------------------------------------------------------

class TestOAuthDiscovery:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()
        self.registry.register(
            _make_oauth_config(
                "Jira",
                scopes=_make_scope_config(personal=["read"], team=["admin"], agent=["exec"]),
            )
        )
        self.registry.register(
            _make_oauth_config(
                "Slack",
                scopes=_make_scope_config(personal=["chat.read"]),
            )
        )

    def test_get_oauth_connectors(self):
        result = self.registry.get_oauth_connectors()
        assert result == ["Jira", "Slack"]

    def test_has_oauth_true(self):
        assert self.registry.has_oauth("Jira") is True

    def test_has_oauth_false(self):
        assert self.registry.has_oauth("Unknown") is False

    def test_get_oauth_connectors_by_scope_type_personal(self):
        result = self.registry.get_oauth_connectors_by_scope_type(OAuthScopeType.PERSONAL_SYNC)
        assert "Jira" in result
        assert "Slack" in result

    def test_get_oauth_connectors_by_scope_type_team(self):
        result = self.registry.get_oauth_connectors_by_scope_type(OAuthScopeType.TEAM_SYNC)
        assert result == ["Jira"]

    def test_get_oauth_connectors_by_scope_type_agent(self):
        result = self.registry.get_oauth_connectors_by_scope_type(OAuthScopeType.AGENT)
        assert result == ["Jira"]

    def test_get_connectors_with_personal_sync(self):
        result = self.registry.get_connectors_with_personal_sync()
        assert "Jira" in result
        assert "Slack" in result

    def test_get_connectors_with_team_sync(self):
        result = self.registry.get_connectors_with_team_sync()
        assert result == ["Jira"]

    def test_get_connectors_with_agent(self):
        result = self.registry.get_connectors_with_agent()
        assert result == ["Jira"]


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

class TestSearchHelpers:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    def test_prepare_search_tokens_none(self):
        assert self.registry._prepare_search_tokens(None) == []

    def test_prepare_search_tokens_empty(self):
        assert self.registry._prepare_search_tokens("") == []

    def test_prepare_search_tokens_normal(self):
        tokens = self.registry._prepare_search_tokens("  Hello  World  ")
        assert tokens == ["hello", "world"]

    def test_matches_search_no_tokens(self):
        assert self.registry._matches_search({}, [], []) is True

    def test_matches_search_all_match(self):
        item = {"name": "Jira", "group": "Atlassian"}
        assert self.registry._matches_search(item, ["jira", "atlassian"], ["name", "group"]) is True

    def test_matches_search_partial_fail(self):
        item = {"name": "Jira", "group": "Atlassian"}
        assert self.registry._matches_search(item, ["jira", "google"], ["name", "group"]) is False

    def test_matches_search_list_field(self):
        item = {"categories": ["PM", "Dev"]}
        assert self.registry._matches_search(item, ["pm"], ["categories"]) is True

    def test_matches_search_missing_field(self):
        item = {"name": "Jira"}
        assert self.registry._matches_search(item, ["jira"], ["name", "missing"]) is True


# ---------------------------------------------------------------------------
# get_oauth_config_registry_connectors (async)
# ---------------------------------------------------------------------------

class TestGetOAuthConfigRegistryConnectors:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        result = await self.registry.get_oauth_config_registry_connectors()
        assert result["connectors"] == []
        assert result["pagination"]["totalCount"] == 0
        assert result["pagination"]["totalPages"] == 0
        assert result["pagination"]["hasPrev"] is False
        assert result["pagination"]["hasNext"] is False

    @pytest.mark.asyncio
    async def test_returns_connectors_with_fields(self):
        auth_fields = [
            AuthField(name="clientId", display_name="Client ID", is_secret=False),
            AuthField(name="clientSecret", display_name="Client Secret", is_secret=True),
        ]
        self.registry.register(
            _make_oauth_config(
                "Jira",
                auth_fields=auth_fields,
                token_access_type="offline",
                additional_params={"prompt": "consent"},
            )
        )
        result = await self.registry.get_oauth_config_registry_connectors()
        assert len(result["connectors"]) == 1
        conn = result["connectors"][0]
        assert conn["name"] == "Jira"
        assert conn["authType"] == "OAUTH"
        assert "authFields" in conn
        assert len(conn["authFields"]) == 2
        assert conn["tokenAccessType"] == "offline"
        assert conn["additionalParams"] == {"prompt": "consent"}

    @pytest.mark.asyncio
    async def test_includes_documentation_links(self):
        links = [DocumentationLink(title="Setup Guide", url="https://docs.example.com", doc_type="guide")]
        self.registry.register(_make_oauth_config("Jira", documentation_links=links))
        result = await self.registry.get_oauth_config_registry_connectors()
        conn = result["connectors"][0]
        assert "documentationLinks" in conn
        assert conn["documentationLinks"][0]["title"] == "Setup Guide"

    @pytest.mark.asyncio
    async def test_pagination(self):
        for i in range(5):
            self.registry.register(_make_oauth_config(f"Connector{i}"))
        result = await self.registry.get_oauth_config_registry_connectors(page=1, limit=2)
        assert len(result["connectors"]) == 2
        assert result["pagination"]["totalCount"] == 5
        assert result["pagination"]["totalPages"] == 3
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["hasPrev"] is False

    @pytest.mark.asyncio
    async def test_pagination_page2(self):
        for i in range(5):
            self.registry.register(_make_oauth_config(f"Connector{i}"))
        result = await self.registry.get_oauth_config_registry_connectors(page=2, limit=2)
        assert len(result["connectors"]) == 2
        assert result["pagination"]["hasPrev"] is True
        assert result["pagination"]["hasNext"] is True
        assert result["pagination"]["prevPage"] == 1
        assert result["pagination"]["nextPage"] == 3

    @pytest.mark.asyncio
    async def test_pagination_last_page(self):
        for i in range(5):
            self.registry.register(_make_oauth_config(f"Connector{i}"))
        result = await self.registry.get_oauth_config_registry_connectors(page=3, limit=2)
        assert len(result["connectors"]) == 1
        assert result["pagination"]["hasNext"] is False
        assert result["pagination"]["nextPage"] is None

    @pytest.mark.asyncio
    async def test_search_filter(self):
        self.registry.register(_make_oauth_config("Jira", app_group="Atlassian"))
        self.registry.register(_make_oauth_config("Slack", app_group="Communication"))
        result = await self.registry.get_oauth_config_registry_connectors(search="atlassian")
        assert len(result["connectors"]) == 1
        assert result["connectors"][0]["name"] == "Jira"

    @pytest.mark.asyncio
    async def test_no_optional_fields_when_not_set(self):
        self.registry.register(_make_oauth_config("Jira"))
        result = await self.registry.get_oauth_config_registry_connectors()
        conn = result["connectors"][0]
        assert "tokenAccessType" not in conn
        assert "additionalParams" not in conn


# ---------------------------------------------------------------------------
# get_connector_registry_info
# ---------------------------------------------------------------------------

class TestGetConnectorRegistryInfo:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    def test_returns_none_for_missing(self):
        assert self.registry.get_connector_registry_info("Unknown") is None

    def test_returns_info_for_registered(self):
        auth_fields = [AuthField(name="clientId", display_name="Client ID")]
        links = [DocumentationLink(title="Guide", url="https://x.com", doc_type="guide")]
        self.registry.register(
            _make_oauth_config(
                "Jira",
                auth_fields=auth_fields,
                token_access_type="offline",
                additional_params={"access_type": "offline"},
                documentation_links=links,
            )
        )
        info = self.registry.get_connector_registry_info("Jira")
        assert info is not None
        assert info["name"] == "Jira"
        assert info["authType"] == "OAUTH"
        assert "authFields" in info
        assert info["tokenAccessType"] == "offline"
        assert info["additionalParams"] == {"access_type": "offline"}
        assert "documentationLinks" in info

    def test_no_optional_fields_when_not_set(self):
        self.registry.register(_make_oauth_config("Jira"))
        info = self.registry.get_connector_registry_info("Jira")
        assert "tokenAccessType" not in info
        assert "additionalParams" not in info

    def test_no_auth_fields_when_empty(self):
        self.registry.register(_make_oauth_config("Jira", auth_fields=[]))
        info = self.registry.get_connector_registry_info("Jira")
        assert "authFields" not in info

    def test_no_doc_links_when_empty(self):
        self.registry.register(_make_oauth_config("Jira"))
        info = self.registry.get_connector_registry_info("Jira")
        assert "documentationLinks" not in info


# ---------------------------------------------------------------------------
# get_oauth_configs_for_connector (async)
# ---------------------------------------------------------------------------

class TestGetOAuthConfigsForConnector:
    def setup_method(self):
        self.registry = OAuthConfigRegistry()

    @pytest.mark.asyncio
    async def test_filters_by_org_id(self):
        configs = [
            {"_id": "c1", "orgId": "org1", "oauthInstanceName": "prod"},
            {"_id": "c2", "orgId": "org2", "oauthInstanceName": "other"},
        ]
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira", oauth_configs=configs, org_id="org1"
        )
        assert len(result["oauthConfigs"]) == 1
        assert result["oauthConfigs"][0]["oauthInstanceName"] == "prod"

    @pytest.mark.asyncio
    async def test_admin_full_config(self):
        configs = [
            {"_id": "c1", "orgId": "org1", "oauthInstanceName": "prod", "clientSecret": "secret"},
        ]
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira",
            oauth_configs=configs,
            org_id="org1",
            include_full_config=True,
            is_admin=True,
        )
        assert result["oauthConfigs"][0].get("clientSecret") == "secret"

    @pytest.mark.asyncio
    async def test_non_admin_redacted(self):
        configs = [
            {"_id": "c1", "orgId": "org1", "oauthInstanceName": "prod", "clientSecret": "secret"},
        ]
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira",
            oauth_configs=configs,
            org_id="org1",
            include_full_config=False,
            is_admin=False,
        )
        assert "clientSecret" not in result["oauthConfigs"][0]
        assert result["oauthConfigs"][0]["oauthInstanceName"] == "prod"

    @pytest.mark.asyncio
    async def test_pagination(self):
        configs = [
            {"_id": f"c{i}", "orgId": "org1", "oauthInstanceName": f"inst{i}"}
            for i in range(5)
        ]
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira", oauth_configs=configs, org_id="org1",
            page=1, limit=2,
        )
        assert len(result["oauthConfigs"]) == 2
        assert result["pagination"]["totalCount"] == 5
        assert result["pagination"]["hasNext"] is True

    @pytest.mark.asyncio
    async def test_search_filter(self):
        configs = [
            {"_id": "c1", "orgId": "org1", "oauthInstanceName": "production"},
            {"_id": "c2", "orgId": "org1", "oauthInstanceName": "staging"},
        ]
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira", oauth_configs=configs, org_id="org1",
            search="production",
        )
        assert len(result["oauthConfigs"]) == 1

    @pytest.mark.asyncio
    async def test_empty_configs(self):
        result = await self.registry.get_oauth_configs_for_connector(
            connector_type="Jira", oauth_configs=[], org_id="org1",
        )
        assert result["oauthConfigs"] == []
        assert result["pagination"]["totalCount"] == 0


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

class TestGlobalRegistry:
    def test_get_oauth_config_registry_returns_instance(self):
        reg = get_oauth_config_registry()
        assert isinstance(reg, OAuthConfigRegistry)

    def test_get_oauth_config_registry_is_singleton(self):
        reg1 = get_oauth_config_registry()
        reg2 = get_oauth_config_registry()
        assert reg1 is reg2
