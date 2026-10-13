"""The full-config save links an OAuth app when it is given client credentials.

A personal Google Drive connector reads its client ID and secret only from a linked
OAuth app. Creating a connector and the auth-only save (``PUT /config/auth``) both turn
client credentials into an app and link it. The full-config save (``PUT /config``) used
to store them on the connector and link nothing, so the connector could sign in and then
never start. These tests run the save against an in-memory config store, then start the
connector from what was stored.
"""

import copy
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router as router_module
from app.connectors.api.router import update_connector_instance_config
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)

# Importing a connector registers its OAuth fields (which are required) by its type name.
from app.connectors.sources.servicenow.servicenow import connector as _servicenow  # noqa: F401

CONNECTOR_ID = "drive-conn-1"
ORG_ID = "org-1"
USER_ID = "user-1"
CONFIG_PATH = f"/services/connectors/{CONNECTOR_ID}/config"
OAUTH_APPS_PATH = "/services/oauth/drive"
CLIENT_ID = "client-id.apps.googleusercontent.com"
CLIENT_SECRET = "s3cr3t-value-that-must-not-leak"


class InMemoryConfigService:
    """Stands in for ConfigurationService: values go in and come out as copies."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = copy.deepcopy(values or {})
        self.values.setdefault("/services/endpoints", {"frontend": {"publicEndpoint": "https://pipeshub.example"}})

    async def get_config(self, path: str, default: object = None, use_cache: bool = True) -> object:
        del use_cache
        return copy.deepcopy(self.values.get(path, default))

    async def set_config(self, path: str, value: object) -> bool:
        self.values[path] = copy.deepcopy(value)
        return True

    def apps(self) -> list[dict[str, Any]]:
        return self.values.get(OAUTH_APPS_PATH) or []


def _metadata() -> dict[str, Any]:
    return {
        "config": {
            "auth": {
                "schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/Drive", "fields": []}},
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://accounts.google.com/o/oauth2/v2/auth",
                        "tokenUrl": "https://oauth2.googleapis.com/token",
                        "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
                    }
                },
            }
        }
    }


def _request(body: dict[str, Any], *, role: str = "admin") -> MagicMock:
    user = {"userId": USER_ID, "orgId": ORG_ID, "role": role}
    request = MagicMock()
    request.state.user = user
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test_full_config_save"))
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=_metadata())
    registry.update_connector_instance = AsyncMock(return_value={"_key": CONNECTOR_ID})
    request.app.state.connector_registry = registry
    return request


def _instance(connector_type: str = "Drive") -> dict[str, Any]:
    return {
        "_key": CONNECTOR_ID,
        "type": connector_type,
        "name": "My Drive",
        "isActive": False,
        "authType": "OAUTH",
        "scope": "personal",
        "createdBy": USER_ID,
    }


async def _save(
    store: InMemoryConfigService, body: dict[str, Any], *, role: str = "admin", connector_type: str = "Drive"
) -> dict[str, Any]:
    request = _request(body, role=role)
    with (
        patch.object(
            router_module, "get_validated_connector_instance", AsyncMock(return_value=_instance(connector_type))
        ),
        patch.object(router_module, "resolve_config_service", return_value=store),
    ):
        return await update_connector_instance_config(CONNECTOR_ID, request)


async def _start_drive(store: InMemoryConfigService) -> bool:
    processor = MagicMock()
    processor.org_id = ORG_ID
    with patch("app.connectors.sources.google.drive.individual.connector.SyncPoint"):
        connector = GoogleDriveIndividualConnector(
            logger=logging.getLogger("test_full_config_save"),
            data_entities_processor=processor,
            data_store_provider=MagicMock(),
            config_service=store,
            connector_id=CONNECTOR_ID,
            scope="personal",
            created_by=USER_ID,
        )
    client = MagicMock()
    with (
        patch(
            "app.connectors.sources.google.drive.individual.connector.GoogleClient.build_from_services",
            AsyncMock(return_value=client),
        ),
        patch("app.connectors.sources.google.drive.individual.connector.GoogleDriveDataSource"),
    ):
        return await connector.init()


@pytest.mark.asyncio
class TestInlineCredentialsLinkAnOAuthApp:
    async def test_the_save_creates_an_app_from_the_credentials_and_links_it(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        result = await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        apps = store.apps()
        assert len(apps) == 1
        app = apps[0]
        assert app["orgId"] == ORG_ID
        assert app["connectorType"] == "Drive"
        assert app["oauthInstanceName"] == "My Drive"
        assert app["config"]["clientId"] == CLIENT_ID
        assert app["config"]["clientSecret"] == CLIENT_SECRET

        stored_auth = store.values[CONFIG_PATH]["auth"]
        assert stored_auth["oauthConfigId"] == app["_id"]
        # The caller sees which app the connector is now linked to.
        assert result["config"]["auth"]["oauthConfigId"] == app["_id"]

    async def test_the_connector_can_start_after_the_save(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})
        # Sign-in completes: the OAuth callback stores the tokens beside the saved config.
        store.values[CONFIG_PATH]["credentials"] = {"access_token": "access", "refresh_token": "refresh"}

        assert await _start_drive(store) is True

    async def test_the_secret_is_neither_returned_nor_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        with caplog.at_level(logging.DEBUG):
            result = await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert CLIENT_SECRET not in json.dumps(result)
        assert CLIENT_SECRET not in caplog.text

    async def test_the_existing_response_fields_are_still_there(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        result = await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        auth = result["config"]["auth"]
        assert result["success"] is True
        assert auth["clientId"] == CLIENT_ID
        assert "clientSecret" in auth  # masked, as before
        for key in ("authorizeUrl", "tokenUrl", "scopes", "redirectUri", "authType", "connectorType"):
            assert key in auth

    async def test_a_connector_saved_inline_earlier_is_linked_when_resaved(self) -> None:
        # Sent back as GET /config returns it: the secret comes back as the mask.
        store = InMemoryConfigService(
            {
                CONFIG_PATH: {
                    "auth": {"connectorScope": "personal", "clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET},
                    "credentials": {"access_token": "access", "refresh_token": "refresh"},
                }
            }
        )

        await _save(
            store, {"auth": {"clientId": CLIENT_ID, "clientSecret": router_module.REDACTED_PLACEHOLDER}}
        )

        apps = store.apps()
        assert len(apps) == 1
        assert apps[0]["config"] == {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
        stored = store.values[CONFIG_PATH]
        assert stored["auth"]["oauthConfigId"] == apps[0]["_id"]
        # Same client as the one that signed in, so the sign-in is kept.
        assert stored["credentials"] == {"access_token": "access", "refresh_token": "refresh"}
        assert await _start_drive(store) is True

    async def test_a_name_already_taken_by_another_app_is_refused_as_on_the_other_saves(self) -> None:
        other = {"_id": "other-app", "orgId": ORG_ID, "oauthInstanceName": "My Drive", "config": {"clientId": "x"}}
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "personal"}}, OAUTH_APPS_PATH: [other]}
        )

        with pytest.raises(HTTPException) as caught:
            await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert caught.value.status_code == 409
        assert store.apps() == [other]
        assert store.values[CONFIG_PATH] == {"auth": {"connectorScope": "personal"}}

    async def test_the_caller_can_name_the_new_app(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(
            store,
            {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET, "oauthInstanceName": "Drive app"}},
        )

        assert [app["oauthInstanceName"] for app in store.apps()] == ["Drive app"]


@pytest.mark.asyncio
class TestOtherSavesAreUnchanged:
    async def test_a_linked_connector_keeps_its_app(self) -> None:
        app = {
            "_id": "app-1",
            "orgId": ORG_ID,
            "oauthInstanceName": "Shared Drive app",
            "connectorType": "Drive",
            "config": {"clientId": "app-client", "clientSecret": "app-secret"},
        }
        store = InMemoryConfigService(
            {
                CONFIG_PATH: {"auth": {"connectorScope": "personal", "oauthConfigId": "app-1"}},
                OAUTH_APPS_PATH: [app],
            }
        )

        await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert store.apps() == [app]
        assert store.values[CONFIG_PATH]["auth"]["oauthConfigId"] == "app-1"

    async def test_an_app_named_at_the_top_level_is_linked_and_nothing_is_created(self) -> None:
        app = {"_id": "app-1", "orgId": ORG_ID, "oauthInstanceName": "Shared", "config": {"clientId": "c"}}
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "personal"}}, OAUTH_APPS_PATH: [app]}
        )

        await _save(
            store,
            {"oauthConfigId": "app-1", "auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}},
        )

        assert store.apps() == [app]
        assert store.values[CONFIG_PATH]["auth"]["oauthConfigId"] == "app-1"

    @pytest.mark.parametrize(
        "body",
        [
            {"sync": {"selectedStrategy": "SCHEDULED"}},
            {"auth": {"connectorScope": "personal"}},
            {"auth": {"clientId": "", "clientSecret": ""}},
        ],
        ids=["no-auth-section", "auth-without-credentials", "empty-credentials"],
    )
    async def test_a_save_without_credentials_creates_no_app(self, body: dict[str, Any]) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(store, body)

        assert store.apps() == []
        assert "oauthConfigId" not in store.values[CONFIG_PATH]["auth"]

    async def test_a_member_saving_credentials_creates_no_app(self) -> None:
        # As on the other save paths, only an admin's credentials become an OAuth app.
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}}, role="member")

        assert store.apps() == []
        assert "oauthConfigId" not in store.values[CONFIG_PATH]["auth"]

    async def test_a_failed_app_write_fails_the_save_and_stores_nothing(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})
        with patch.object(router_module, "_create_or_update_oauth_config", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as caught:
                await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert caught.value.status_code == 500
        assert store.values[CONFIG_PATH] == {"auth": {"connectorScope": "personal"}}


@pytest.mark.asyncio
class TestAPartialSaveLinksNothingUntilEveryRequiredFieldIsThere:
    async def test_a_client_id_alone_creates_no_app_and_its_secret_later_creates_one(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(store, {"auth": {"clientId": CLIENT_ID}})

        assert store.apps() == []
        assert "oauthConfigId" not in store.values[CONFIG_PATH]["auth"]
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == CLIENT_ID

        await _save(store, {"auth": {"clientSecret": CLIENT_SECRET}})

        apps = store.apps()
        assert len(apps) == 1
        assert apps[0]["config"] == {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
        assert store.values[CONFIG_PATH]["auth"]["oauthConfigId"] == apps[0]["_id"]
        store.values[CONFIG_PATH]["credentials"] = {"access_token": "access", "refresh_token": "refresh"}
        assert await _start_drive(store) is True

    async def test_a_servicenow_instance_url_alone_is_stored_and_creates_no_app(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        await _save(
            store, {"auth": {"instanceUrl": "https://acme.service-now.com"}}, connector_type="ServiceNow"
        )

        assert store.values.get("/services/oauth/servicenow") is None
        assert store.apps() == []
        auth = store.values[CONFIG_PATH]["auth"]
        assert auth["instanceUrl"] == "https://acme.service-now.com"
        assert "oauthConfigId" not in auth
