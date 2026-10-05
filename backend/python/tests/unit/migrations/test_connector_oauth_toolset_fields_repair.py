"""Connector OAuth apps saved before #3883 carry the toolset's OAuth settings; the
startup repair gives them the connector's own, once per deployment."""

import copy
import importlib
import logging

# The connector router and factory import each other through a cycle; load the router first.
import app.connectors.api.router  # noqa: F401
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.migrations.connector_oauth_toolset_fields_migration import (
    MIGRATION_FLAG_KEY,
    run_connector_oauth_toolset_fields_repair,
)

importlib.import_module("app.connectors.core.factory.connector_factory")
importlib.import_module("app.agents.actions.salesforce.salesforce")
importlib.import_module("app.agents.actions.slack.slack")

BASE_URL = "https://pipeshub.example"
SALESFORCE_PATH = "/services/oauth/salesforce"
SLACK_PATH = "/services/oauth/slack"
TOOLSET_SALESFORCE_PATH = "/services/oauths/toolsets/salesforce"
LOGGER = logging.getLogger("test-connector-oauth-repair")


class _MemoryConfigService:
    def __init__(self, store: dict, *, failing_reads: frozenset[str] = frozenset()) -> None:
        self.store = store
        self.failing_reads = failing_reads
        self.writes: list[str] = []

    async def get_config(self, path: str, default: list | dict | None = None, use_cache: bool = True) -> list | dict | None:
        if path in self.failing_reads:
            raise ConnectionError(f"store did not answer for {path}")
        return copy.deepcopy(self.store.get(path, default))

    async def set_config(self, path: str, value: list | dict) -> bool:
        self.writes.append(path)
        self.store[path] = copy.deepcopy(value)
        return True


def _broken_salesforce_app(app_id: str = "sf-app-1") -> dict:
    """A Salesforce connector OAuth app as saved from the toolset's registry entry."""
    return {
        "_id": app_id,
        "oauthInstanceName": "Acme Salesforce",
        "connectorType": "Salesforce",
        "orgId": "org-1",
        "config": {
            "clientId": "client-id-1",
            "clientSecret": "client-secret-1",
            "loginUrl": "https://acme.my.salesforce.com",
        },
        "authorizeUrl": "https://login.salesforce.com/services/oauth2/authorize",
        "tokenUrl": "https://login.salesforce.com/services/oauth2/token",
        "redirectUri": f"{BASE_URL}/toolsets/oauth/callback/salesforce",
        "scopes": {"personal_sync": [], "team_sync": [], "agent": ["api", "refresh_token", "id"]},
        "additionalParams": {"prompt": "consent"},
        "iconPath": "/assets/icons/connectors/salesforce.svg",
        "appGroup": "CRM",
        "appDescription": "Salesforce OAuth application for agent integration",
        "appCategories": ["app"],
    }


def _broken_slack_app() -> dict:
    return {
        "_id": "slack-app-1",
        "connectorType": "Slack",
        "orgId": "org-1",
        "config": {"clientId": "slack-client", "clientSecret": "slack-secret"},
        "redirectUri": f"{BASE_URL}/toolsets/oauth/callback/slack",
        "scopes": {"personal_sync": [], "team_sync": [], "agent": ["chat:write"]},
        "appGroup": "Messaging",
        "appDescription": "Slack OAuth application for agent integration",
        "appCategories": ["app"],
    }


def _correct_salesforce_app() -> dict:
    app = _broken_salesforce_app("sf-app-ok")
    app.pop("additionalParams")
    connector = get_oauth_config_registry().get_config("Salesforce")
    app.update(
        redirectUri=f"{BASE_URL}/{connector.redirect_uri}",
        scopes=connector.scopes.to_dict(),
        tokenAccessType=connector.token_access_type,
        appGroup=connector.app_group,
        appDescription=connector.app_description,
        appCategories=list(connector.app_categories),
    )
    return app


async def _repair(config_service: _MemoryConfigService) -> dict:
    return await run_connector_oauth_toolset_fields_repair(config_service, get_oauth_config_registry(), LOGGER)


class TestRepair:
    async def test_a_salesforce_app_with_toolset_settings_gets_the_connector_ones(self) -> None:
        broken = _broken_salesforce_app()
        config_service = _MemoryConfigService({SALESFORCE_PATH: [broken]})

        result = await _repair(config_service)

        [app] = config_service.store[SALESFORCE_PATH]
        assert result["apps_repaired"] == 1
        assert app["redirectUri"] == f"{BASE_URL}/connectors/oauth/callback/Salesforce"
        assert app["scopes"] == {
            "personal_sync": [],
            "team_sync": ["api", "refresh_token", "offline_access"],
            "agent": [],
        }
        assert "additionalParams" not in app
        assert app["tokenAccessType"] == "offline"
        assert app["appGroup"] == "Salesforce"
        assert app["appDescription"] == "OAuth application for accessing Salesforce API"
        assert app["appCategories"] == ["CRM", "Sales"]
        for kept in ("_id", "config", "authorizeUrl", "tokenUrl", "iconPath", "orgId", "oauthInstanceName"):
            assert app[kept] == broken[kept], kept

    async def test_an_app_already_on_the_connector_settings_is_left_alone(self) -> None:
        correct = _correct_salesforce_app()
        config_service = _MemoryConfigService({SALESFORCE_PATH: [correct]})

        await _repair(config_service)

        assert config_service.store[SALESFORCE_PATH] == [correct]
        assert SALESFORCE_PATH not in config_service.writes

    async def test_toolset_oauth_apps_are_not_touched(self) -> None:
        toolset_app = _broken_salesforce_app("sf-toolset-app")
        config_service = _MemoryConfigService({
            TOOLSET_SALESFORCE_PATH: [toolset_app],
            SALESFORCE_PATH: [_broken_salesforce_app()],
        })

        await _repair(config_service)

        assert config_service.store[TOOLSET_SALESFORCE_PATH] == [toolset_app]
        assert set(config_service.writes) == {SALESFORCE_PATH, MIGRATION_FLAG_KEY}


class TestRunsOnce:
    async def test_a_second_start_does_not_repair_again(self) -> None:
        config_service = _MemoryConfigService({SALESFORCE_PATH: [_broken_salesforce_app()]})

        first = await _repair(config_service)
        late = _broken_salesforce_app("sf-app-late")
        config_service.store[SALESFORCE_PATH].append(late)
        second = await _repair(config_service)

        assert first["apps_repaired"] == 1
        assert config_service.store[MIGRATION_FLAG_KEY]["done"] is True
        assert second["skipped"] is True
        assert config_service.store[SALESFORCE_PATH][1] == late


class TestFailures:
    async def test_a_corrupt_entry_does_not_stop_the_app_next_to_it(self) -> None:
        config_service = _MemoryConfigService({SALESFORCE_PATH: ["not-an-app", _broken_salesforce_app()]})

        result = await _repair(config_service)

        assert config_service.store[SALESFORCE_PATH][1]["redirectUri"] == f"{BASE_URL}/connectors/oauth/callback/Salesforce"
        assert result["apps_repaired"] == 1
        assert result["success"] is False
        assert MIGRATION_FLAG_KEY not in config_service.store

    async def test_a_type_the_store_cannot_read_does_not_stop_the_others_and_is_retried(self) -> None:
        config_service = _MemoryConfigService(
            {SALESFORCE_PATH: [_broken_salesforce_app()], SLACK_PATH: [_broken_slack_app()]},
            failing_reads=frozenset({SALESFORCE_PATH}),
        )

        result = await _repair(config_service)

        [slack] = config_service.store[SLACK_PATH]
        assert slack["redirectUri"] == f"{BASE_URL}/connectors/oauth/callback/Slack"
        assert slack["config"] == {"clientId": "slack-client", "clientSecret": "slack-secret"}
        assert result["success"] is False
        assert MIGRATION_FLAG_KEY not in config_service.store

        config_service.failing_reads = frozenset()
        retried = await _repair(config_service)

        assert retried["apps_repaired"] == 1
        assert config_service.store[SALESFORCE_PATH][0]["redirectUri"] == f"{BASE_URL}/connectors/oauth/callback/Salesforce"
        assert config_service.store[MIGRATION_FLAG_KEY]["done"] is True
