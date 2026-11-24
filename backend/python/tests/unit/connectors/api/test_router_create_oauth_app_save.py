"""Creating a connector fails when a save it was asked to make fails (docs/logging.md, "Routes").

The real create route, OAuth app helpers and ``_prepare_connector_config`` run against an in-memory config store whose
chosen path fails; only the registry's graph writes are mocks. An app or connector config that was not saved used to
answer 200, and every sign-in of that connector then failed on the missing client secret.
"""
from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router as routes
from app.connectors.core.base.token_service.oauth_service import (
    OAuthStoreUnavailableError,
)

# Importing a connector registers its OAuth fields by its type name (Drive: clientId, secret clientSecret).
from app.connectors.sources.google.drive.individual import connector as _drive  # noqa: F401
from app.utils.error_class import classify_error
from tests.support.in_memory_config import InMemoryConfigService

CONNECTOR_ID = "conn-1"
ORG_ID = "org-1"
USER_ID = "user-1"
CONFIG_PATH = f"/services/connectors/{CONNECTOR_ID}/config"
OAUTH_APPS_PATH = "/services/oauth/drive"
CLIENT_ID = "client-id.apps.googleusercontent.com"
# Built at run time: a traceback prints source lines, so a literal would show in a stack.
CLIENT_SECRET = "-".join(("SECRET", "MARKER"))
APP_FAILED = "Failed to create/update OAuth configuration"
LOGGER = "app.test.create_oauth_app_save"
EXISTING_APP = {
    "_id": "app-1", "orgId": ORG_ID, "connectorType": "Drive", "oauthInstanceName": "Shared Drive app",
    "createdBy": USER_ID, "config": {"clientId": "old-client", "clientSecret": "old-secret"},
}


class Store(InMemoryConfigService):
    """ConfigurationService's write contract: a failed write answers False; ``raising`` paths raise like a dropped link."""

    def __init__(self, *, failing: tuple[str, ...] = (), raising: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.values["/services/endpoints"] = {"frontend": {"publicEndpoint": "https://pipeshub.example"}}
        self.failing, self.raising = failing, raising

    async def set_config(self, key: str, value: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        if key in self.raising:
            raise ConnectionError("kv down")
        if key in self.failing:
            return False
        return await super().set_config(key, value, **kwargs)


def _metadata() -> dict[str, Any]:
    return {
        "scope": ["personal", "team"],
        "supportedAuthTypes": ["OAUTH", "NONE"],
        "config": {"auth": {
            "schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/Drive", "fields": []}},
            "oauthConfigs": {"OAUTH": {"authorizeUrl": "https://accounts.google.com/o/oauth2/v2/auth",
                                       "tokenUrl": "https://oauth2.googleapis.com/token",
                                       "scopes": ["https://www.googleapis.com/auth/drive.readonly"]}},
        }},
    }


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": USER_ID, "orgId": ORG_ID, "role": "admin"}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger(LOGGER))
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=_metadata())
    registry.create_connector_instance_on_configuration = AsyncMock(return_value={"_key": CONNECTOR_ID, "type": "Drive"})
    registry.get_connector_instance = AsyncMock(return_value={
        "_key": CONNECTOR_ID, "type": "Drive", "authType": "OAUTH", "scope": "personal", "createdBy": USER_ID})
    registry._normalize_connector_name = MagicMock(return_value="drive")
    registry._get_beta_connector_names = MagicMock(return_value=[])
    request.app.state.connector_registry = registry
    return request


def _body(auth: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    auth = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET} if auth is None else auth
    return {"connectorType": "Drive", "instanceName": "My Drive", "scope": "personal", "authType": "OAUTH",
            "config": {"auth": auth}, **extra}


async def _create(store: Store, body: dict[str, Any]) -> tuple[dict[str, Any] | HTTPException, MagicMock]:
    """The route's answer (its dict, or the HTTPException it raised) and the request."""
    request = _request(body)
    graph = AsyncMock()
    graph.get_account_type = AsyncMock(return_value="individual")
    with (
        patch.object(routes, "resolve_config_service", return_value=store),
        patch.object(routes, "check_beta_connector_access", AsyncMock(return_value=None)),
        patch.object(routes, "_store_config_action_items", AsyncMock(return_value=None)),
    ):
        try:
            return await routes.create_connector_instance(request, graph), request
        except HTTPException as e:
            return e, request


async def _start_sign_in(store: Store, request: MagicMock) -> dict[str, Any]:
    with (
        patch.object(routes, "resolve_config_service", return_value=store),
        patch.object(routes, "check_beta_connector_access", AsyncMock(return_value=None)),
    ):
        return await routes.get_oauth_authorization_url(CONNECTOR_ID, request, None, graph_provider=AsyncMock())


@pytest.mark.parametrize("failure", ["write-answers-false", "write-raises"])
async def test_an_app_that_was_not_saved_fails_the_create_and_stores_no_connector_config(
    failure: str, caplog: pytest.LogCaptureFixture,
) -> None:
    store = Store(failing=(OAUTH_APPS_PATH,)) if failure == "write-answers-false" else Store(raising=(OAUTH_APPS_PATH,))

    with caplog.at_level(logging.DEBUG):
        answer, request = await _create(store, _body())

    assert isinstance(answer, HTTPException)
    # A new app's failed save may still have landed, so the answer says to check (test_router_create_rollback.py).
    assert answer.status_code == 500 and "may have been saved" in answer.detail
    assert isinstance(answer.__cause__, HTTPException) and answer.__cause__.detail == APP_FAILED
    assert CONFIG_PATH not in store.values and OAUTH_APPS_PATH not in store.values
    request.app.state.connector_registry.create_connector_instance_on_configuration.assert_awaited_once()
    assert CLIENT_SECRET not in caplog.text


async def test_a_failed_update_of_a_named_app_fails_and_leaves_the_app_as_it_was() -> None:
    # Before, the connector silently linked the old app and the new credentials were dropped.
    store = Store(failing=(OAUTH_APPS_PATH,))
    store.values[OAUTH_APPS_PATH] = [copy.deepcopy(EXISTING_APP)]

    answer, _ = await _create(store, _body(oauthConfigId="app-1"))

    assert isinstance(answer, HTTPException)
    assert (answer.status_code, answer.detail) == (500, APP_FAILED)
    assert store.values[OAUTH_APPS_PATH] == [EXISTING_APP]
    assert CONFIG_PATH not in store.values


@pytest.mark.parametrize(("instance_removed", "detail"), [
    # The app this create made is kept: another connector may already link to it.
    (True, "The connector's settings could not be saved, so it was not created. "
           "Its OAuth app was saved: pick that app when you try again."),
    (False, "The connector was created, but its settings were not saved. Open it to finish setting it up, or delete it."),
], ids=["rolled-back", "rollback-failed"])
async def test_a_connector_config_that_was_not_saved_fails_the_create_and_says_what_is_left(
    instance_removed: bool, detail: str, caplog: pytest.LogCaptureFixture,
) -> None:
    # The rollback itself: test_router_create_rollback.py.
    store = Store(failing=(CONFIG_PATH,))

    with caplog.at_level(logging.DEBUG), patch.object(
        routes._CreateUndo, "run", AsyncMock(return_value=instance_removed)
    ):
        answer, _ = await _create(store, _body())

    assert isinstance(answer, HTTPException) and answer.status_code == 500
    assert answer.detail == detail
    assert isinstance(answer.__cause__, OAuthStoreUnavailableError)
    assert classify_error(answer) == "unavailable"  # the request line's class: our store, not our bug
    assert CONFIG_PATH not in store.values
    assert "Stored initial config" not in caplog.text
    assert CLIENT_SECRET not in caplog.text and CLIENT_SECRET not in answer.detail


async def test_a_saved_app_is_linked_and_the_connector_can_start_sign_in() -> None:
    store = Store()

    answer, request = await _create(store, _body())

    assert answer["success"] is True
    (app,) = store.values[OAUTH_APPS_PATH]
    assert app["config"] == {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
    auth = store.values[CONFIG_PATH]["auth"]
    assert auth["oauthConfigId"] == app["_id"] and "clientSecret" not in auth
    signin = await _start_sign_in(store, request)
    assert signin["success"] is True and CLIENT_ID in signin["authorizationUrl"]


@pytest.mark.parametrize(("auth", "auth_type"), [({"connectorScope": "personal"}, "OAUTH"), ({"clientId": CLIENT_ID}, "NONE")],
                         ids=["oauth-without-credentials", "not-an-oauth-connector"])
async def test_a_create_that_wants_no_app_still_succeeds_without_one(auth: dict[str, Any], auth_type: str) -> None:
    # None from _handle_oauth_config_creation means "no app wanted" here, not a failure.
    store = Store(failing=(OAUTH_APPS_PATH,))

    answer, _ = await _create(store, {**_body(auth), "authType": auth_type})

    assert isinstance(answer, dict) and answer["success"] is True
    assert OAUTH_APPS_PATH not in store.values
