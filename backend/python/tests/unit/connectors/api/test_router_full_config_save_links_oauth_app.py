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
from urllib.parse import urlparse

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

    def __init__(self, values: dict[str, Any] | None = None, *, failing_paths: tuple[str, ...] = ()) -> None:
        self.values: dict[str, Any] = copy.deepcopy(values or {})
        self.failing_paths = failing_paths
        self.values.setdefault("/services/endpoints", {"frontend": {"publicEndpoint": "https://pipeshub.example"}})

    async def get_config(self, path: str, default: object = None, use_cache: bool = True) -> object:
        del use_cache
        return copy.deepcopy(self.values.get(path, default))

    async def set_config(self, path: str, value: object) -> bool:
        if path in self.failing_paths:
            return False
        self.values[path] = copy.deepcopy(value)
        return True

    def apps(self) -> list[dict[str, Any]]:
        return self.values.get(OAUTH_APPS_PATH) or []


def _metadata(connector_type: str = "Drive") -> dict[str, Any]:
    if connector_type == "ServiceNow":
        # As registered: the authorize and token URLs default to a placeholder host.
        urls = router_module._placeholder_oauth_urls("ServiceNow")
        return {
            "config": {
                "auth": {
                    "schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/ServiceNow", "fields": []}},
                    "oauthConfigs": {"OAUTH": {**urls, "scopes": ["useraccount"]}},
                }
            }
        }
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


def _request(body: dict[str, Any], *, role: str = "admin", connector_type: str = "Drive") -> MagicMock:
    user = {"userId": USER_ID, "orgId": ORG_ID, "role": role}
    request = MagicMock()
    request.state.user = user
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test_full_config_save"))
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=_metadata(connector_type))
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
    store: InMemoryConfigService,
    body: dict[str, Any],
    *,
    role: str = "admin",
    connector_type: str = "Drive",
    requests: list[MagicMock] | None = None,
) -> dict[str, Any]:
    request = _request(body, role=role, connector_type=connector_type)
    if requests is not None:
        requests.append(request)
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
        for key in ("authorizeUrl", "tokenUrl", "scopes", "redirectUri", "authType", "connectorType"):
            assert key in auth

    async def test_the_linked_connector_keeps_no_copy_of_the_secret(self) -> None:
        # As on creating a connector and on PUT /config/auth: the app holds the secret.
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})

        result = await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert "clientSecret" not in store.values[CONFIG_PATH]["auth"]
        assert "clientSecret" not in result["config"]["auth"]
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == CLIENT_ID

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
        assert "clientSecret" not in stored["auth"]
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
        # The connector shows its app's client, and holds no secret of its own.
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == "c"
        assert "clientSecret" not in store.values[CONFIG_PATH]["auth"]

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

    async def test_a_failed_app_list_write_fails_the_save_and_stores_nothing(self) -> None:
        # The settings store reports a failed write by returning False, not by raising.
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "personal"}}}, failing_paths=(OAUTH_APPS_PATH,)
        )

        with pytest.raises(HTTPException) as caught:
            await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert caught.value.status_code == 500
        assert store.apps() == []
        assert store.values[CONFIG_PATH] == {"auth": {"connectorScope": "personal"}}

    async def test_a_failed_connector_write_takes_back_the_new_app_and_fails_the_save(self) -> None:
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "personal"}}}, failing_paths=(CONFIG_PATH,)
        )

        with pytest.raises(HTTPException) as caught:
            await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})

        assert caught.value.status_code == 500
        assert store.apps() == []
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
        assert auth["tokenUrl"] == ""


SHARED_APP = {
    "_id": "app-1",
    "orgId": ORG_ID,
    "oauthInstanceName": "Shared Drive app",
    "connectorType": "Drive",
    "config": {"clientId": "app-client", "clientSecret": "app-secret"},
}
TOKENS = {"access_token": "access", "refresh_token": "refresh"}


def _linked_store(**auth: str) -> InMemoryConfigService:
    return InMemoryConfigService(
        {
            CONFIG_PATH: {
                "auth": {"connectorScope": "personal", "clientId": "app-client", "oauthConfigId": "app-1", **auth},
                "credentials": dict(TOKENS),
            },
            OAUTH_APPS_PATH: [copy.deepcopy(SHARED_APP)],
        }
    )


@pytest.mark.asyncio
class TestALinkedConnectorSavedAgainBehavesAsOnTheAuthOnlySave:
    async def test_a_new_secret_without_naming_the_app_links_a_new_app_and_leaves_the_old_one(self) -> None:
        store = _linked_store()

        await _save(store, {"auth": {"clientSecret": "new-secret"}})

        apps = {app["_id"]: app for app in store.apps()}
        assert apps.pop("app-1") == SHARED_APP
        (new_app,) = apps.values()
        assert new_app["config"] == {"clientId": "app-client", "clientSecret": "new-secret"}
        assert new_app["oauthInstanceName"] == "My Drive"
        stored = store.values[CONFIG_PATH]
        assert stored["auth"]["oauthConfigId"] == new_app["_id"]
        assert "clientSecret" not in stored["auth"]
        # A different client: the old sign-in goes, as on every credential change.
        assert stored["credentials"] is None

    async def test_a_new_secret_for_the_app_the_request_names_updates_that_app(self) -> None:
        store = _linked_store()

        await _save(store, {"auth": {"oauthConfigId": "app-1", "clientSecret": "new-secret"}})

        assert len(store.apps()) == 1
        assert store.apps()[0]["config"] == {"clientId": "app-client", "clientSecret": "new-secret"}
        assert store.values[CONFIG_PATH]["auth"]["oauthConfigId"] == "app-1"
        assert "clientSecret" not in store.values[CONFIG_PATH]["auth"]

    async def test_a_failed_connector_write_puts_the_named_app_back(self) -> None:
        store = _linked_store()
        store.failing_paths = (CONFIG_PATH,)
        before = copy.deepcopy(store.values)

        with pytest.raises(HTTPException) as caught:
            await _save(store, {"auth": {"oauthConfigId": "app-1", "clientSecret": "new-secret"}})

        assert caught.value.status_code == 500
        assert store.values == before

    async def test_a_get_then_put_round_trip_changes_nothing(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})
        await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})
        store.values[CONFIG_PATH]["credentials"] = dict(TOKENS)
        before = copy.deepcopy(store.values)
        # What GET /config returns, sent straight back.
        sent = router_module._config_for_response(store.values[CONFIG_PATH], frozenset({"clientSecret"}))

        await _save(store, {"auth": sent["auth"], "sync": {"selectedStrategy": "SCHEDULED"}})

        assert store.apps() == before[OAUTH_APPS_PATH]
        assert store.values[CONFIG_PATH]["auth"] == before[CONFIG_PATH]["auth"]
        assert store.values[CONFIG_PATH]["credentials"] == TOKENS

    async def test_a_stale_inline_copy_sent_back_does_not_rewrite_the_app(self) -> None:
        # Saved before this change: linked, yet still holding an older client inline.
        store = _linked_store(clientId=CLIENT_ID, clientSecret=CLIENT_SECRET)

        await _save(
            store, {"auth": {"clientId": CLIENT_ID, "clientSecret": router_module.REDACTED_PLACEHOLDER}}
        )

        assert store.apps() == [SHARED_APP]
        stored = store.values[CONFIG_PATH]
        assert stored["auth"]["oauthConfigId"] == "app-1"
        assert "clientSecret" not in stored["auth"]
        assert stored["credentials"] == TOKENS

    async def test_a_new_client_id_without_its_secret_is_refused(self) -> None:
        store = _linked_store()
        before = copy.deepcopy(store.values)

        with pytest.raises(HTTPException) as caught:
            await _save(store, {"auth": {"clientId": "another-client"}})

        assert caught.value.status_code == 400
        assert store.values == before


def test_a_blank_required_value_counts_as_missing() -> None:
    assert router_module._has_required_oauth_credentials({"clientId": "id", "clientSecret": "s"}, "Drive")
    assert not router_module._has_required_oauth_credentials({"clientId": "id", "clientSecret": "   "}, "Drive")
    assert not router_module._has_required_oauth_credentials({"client_id": " ", "client_secret": "s"}, "Drive")
    # Either spelling may carry the value.
    assert router_module._has_required_oauth_credentials(
        {"clientId": "  ", "client_id": "id", "clientSecret": "s"}, "Drive"
    )


SERVICENOW_APPS_PATH = "/services/oauth/servicenow"
SN_URLS = {
    "instanceUrl": "https://acme.service-now.com",
    "authorizeUrl": "https://acme.service-now.com/oauth_auth.do",
    "tokenUrl": "https://acme.service-now.com/oauth_token.do",
}


@pytest.mark.asyncio
class TestServiceNowProviderUrls:
    async def test_urls_saved_first_are_kept_and_go_into_the_app_with_the_credentials(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "team"}}})

        await _save(store, {"auth": dict(SN_URLS)}, connector_type="ServiceNow")

        auth = store.values[CONFIG_PATH]["auth"]
        assert {key: auth[key] for key in SN_URLS} == SN_URLS
        assert store.values.get(SERVICENOW_APPS_PATH) is None

        await _save(
            store, {"auth": {"clientId": "sn-client", "clientSecret": "sn-secret"}}, connector_type="ServiceNow"
        )

        (app,) = store.values[SERVICENOW_APPS_PATH]
        assert app["config"]["authorizeUrl"] == SN_URLS["authorizeUrl"]
        assert app["config"]["tokenUrl"] == SN_URLS["tokenUrl"]
        assert app["config"]["instanceUrl"] == SN_URLS["instanceUrl"]
        assert store.values[CONFIG_PATH]["auth"]["oauthConfigId"] == app["_id"]

    async def test_a_secret_with_only_placeholder_urls_saved_is_refused_and_nothing_is_stored(self) -> None:
        defaults = router_module._placeholder_oauth_urls("ServiceNow")
        assert urlparse(defaults["authorizeUrl"]).hostname == "example.service-now.com"
        # Saved before this change, when the full save wrote the registry's placeholder host.
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "team", "instanceUrl": SN_URLS["instanceUrl"], **defaults}}}
        )
        before = copy.deepcopy(store.values)

        with pytest.raises(HTTPException) as caught:
            await _save(
                store, {"auth": {"clientId": "sn-client", "clientSecret": "sn-secret"}}, connector_type="ServiceNow"
            )

        assert caught.value.status_code == 400
        assert store.values == before

    async def test_placeholder_urls_sent_with_the_credentials_are_refused_and_nothing_is_stored(self) -> None:
        defaults = router_module._placeholder_oauth_urls("ServiceNow")
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "team"}}})
        before = copy.deepcopy(store.values)

        with pytest.raises(HTTPException) as caught:
            await _save(
                store,
                {
                    "auth": {
                        "instanceUrl": SN_URLS["instanceUrl"],
                        **defaults,
                        "clientId": "sn-client",
                        "clientSecret": "sn-secret",
                    }
                },
                connector_type="ServiceNow",
            )

        assert caught.value.status_code == 400
        assert store.values == before

    @pytest.mark.parametrize("sent_urls", ["placeholders", "none"])
    async def test_the_placeholder_host_is_never_stored_on_the_connector(self, sent_urls: str) -> None:
        defaults = router_module._placeholder_oauth_urls("ServiceNow")
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "team"}}})
        auth = {"instanceUrl": SN_URLS["instanceUrl"], **(defaults if sent_urls == "placeholders" else {})}

        await _save(store, {"auth": auth}, connector_type="ServiceNow")

        stored = store.values[CONFIG_PATH]["auth"]
        assert stored["authorizeUrl"] == ""
        assert stored["tokenUrl"] == ""
        assert store.values.get(SERVICENOW_APPS_PATH) is None

    async def test_a_secret_with_the_instance_urls_waits_for_the_client_id(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "team"}}})

        await _save(store, {"auth": {**SN_URLS, "clientSecret": "sn-secret"}}, connector_type="ServiceNow")

        stored = store.values[CONFIG_PATH]["auth"]
        assert {key: stored[key] for key in SN_URLS} == SN_URLS
        assert store.values.get(SERVICENOW_APPS_PATH) is None


@pytest.mark.asyncio
class TestTheConnectorAndItsAppAgree:
    async def test_the_same_secret_sent_back_in_full_keeps_the_sign_in(self) -> None:
        store = InMemoryConfigService({CONFIG_PATH: {"auth": {"connectorScope": "personal"}}})
        await _save(store, {"auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}})
        store.values[CONFIG_PATH]["credentials"] = dict(TOKENS)
        apps_before = copy.deepcopy(store.apps())
        requests: list[MagicMock] = []

        await _save(
            store,
            {
                "auth": {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET},
                "sync": {"selectedStrategy": "SCHEDULED"},
            },
            requests=requests,
        )

        assert store.values[CONFIG_PATH]["credentials"] == TOKENS
        assert store.apps() == apps_before
        updates = requests[0].app.state.connector_registry.update_connector_instance.await_args.kwargs["updates"]
        assert "isAuthenticated" not in updates

    async def test_a_new_secret_takes_the_client_id_from_the_app_not_an_older_copy(self) -> None:
        store = _linked_store(clientId=CLIENT_ID)  # an older client ID still stored inline

        await _save(store, {"auth": {"clientSecret": "new-secret"}})

        apps = {app["_id"]: app for app in store.apps()}
        assert apps.pop("app-1") == SHARED_APP
        (new_app,) = apps.values()
        assert new_app["config"] == {"clientId": "app-client", "clientSecret": "new-secret"}
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == "app-client"

    async def test_an_echoed_older_client_id_does_not_reach_the_named_app(self) -> None:
        store = _linked_store(clientId=CLIENT_ID)

        await _save(
            store, {"auth": {"oauthConfigId": "app-1", "clientId": CLIENT_ID, "clientSecret": "new-secret"}}
        )

        assert store.apps()[0]["config"] == {"clientId": "app-client", "clientSecret": "new-secret"}
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == "app-client"

    async def test_a_new_client_id_with_its_secret_links_a_new_app_holding_both(self) -> None:
        store = _linked_store()

        await _save(store, {"auth": {"clientId": "another-client", "clientSecret": "another-secret"}})

        apps = {app["_id"]: app for app in store.apps()}
        assert apps.pop("app-1") == SHARED_APP
        (new_app,) = apps.values()
        assert new_app["config"] == {"clientId": "another-client", "clientSecret": "another-secret"}
        assert store.values[CONFIG_PATH]["auth"]["clientId"] == "another-client"
        assert store.values[CONFIG_PATH]["credentials"] is None

    async def test_a_snake_case_secret_replaces_an_older_camel_case_copy(self) -> None:
        store = InMemoryConfigService(
            {CONFIG_PATH: {"auth": {"connectorScope": "personal", "clientId": CLIENT_ID, "clientSecret": "old"}}}
        )

        await _save(store, {"auth": {"client_secret": "new"}})

        (app,) = store.apps()
        assert app["config"] == {"clientId": CLIENT_ID, "clientSecret": "new"}
        auth = store.values[CONFIG_PATH]["auth"]
        assert "clientSecret" not in auth
        assert "client_secret" not in auth
