"""The connector config routes answer with stored secrets masked, and a save that
sends the mask back keeps the stored secret.

Which ``auth`` fields are secret comes from the connector's registry schema
(``isSecret``, or a PASSWORD input) and from its OAuth app's secret fields.
"""

import copy
import json
import logging
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.redaction import REDACTED_PLACEHOLDER
from app.connectors.api.router import (
    get_connector_instance_config,
    update_connector_instance_auth_config,
    update_connector_instance_config,
    update_connector_instance_filters_sync_config,
)

_ROUTER = "app.connectors.api.router"
_CONFIG_PATH = "/services/connectors/conn1/config"
_OAUTH_APPS_PATH = "/services/oauth/jira"

_STORED_API_TOKEN = "stored-api-token"
_STORED_TOKEN_SECRET = "stored-token-secret"
_STORED_CLIENT_SECRET = "stored-client-secret"

_METADATA = {
    "config": {
        "auth": {
            "schemas": {
                "API_TOKEN": {
                    "fields": [
                        {"name": "apiToken", "fieldType": "PASSWORD", "isSecret": True},
                        # A PASSWORD input whose schema forgot isSecret, like BookStack's token_secret.
                        {"name": "tokenSecret", "fieldType": "PASSWORD", "isSecret": False},
                        {"name": "baseUrl", "fieldType": "URL", "isSecret": False},
                        {"name": "serviceAccountJson", "fieldType": "FILE", "isSecret": True},
                    ]
                }
            }
        }
    }
}


def _stored_config(auth_type: str = "API_TOKEN") -> dict[str, Any]:
    auth: dict[str, Any] = {
        "connectorScope": "personal",
        "authType": auth_type,
        "baseUrl": "https://jira.example.com",
    }
    if auth_type == "API_TOKEN":
        auth.update(apiToken=_STORED_API_TOKEN, tokenSecret=_STORED_TOKEN_SECRET, serviceAccountJson="")
    else:
        auth.update(clientId="stored-client-id", clientSecret=_STORED_CLIENT_SECRET, oauthConfigId="app-1")
    return {
        "auth": auth,
        "sync": {"selectedStrategy": "MANUAL"},
        "filters": {"sync": {"values": {}}, "indexing": {"values": {}}},
        "credentials": {"access_token": "stored-access-token"},
    }


def _oauth_apps() -> list[dict[str, Any]]:
    return [
        {
            "_id": "app-1",
            "orgId": "org-1",
            "oauthInstanceName": "Jira app",
            "config": {"clientId": "stored-client-id", "clientSecret": _STORED_CLIENT_SECRET},
        }
    ]


def _instance(auth_type: str = "API_TOKEN") -> dict[str, Any]:
    return {
        "_key": "conn1",
        "type": "jira",
        "authType": auth_type,
        "isActive": False,
        "scope": "personal",
        "createdBy": "user-1",
        "name": "My Jira",
    }


def _config_service(auth_type: str = "API_TOKEN") -> AsyncMock:
    stored = {_CONFIG_PATH: _stored_config(auth_type), _OAUTH_APPS_PATH: _oauth_apps()}
    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=lambda path, **kwargs: stored.get(path, kwargs.get("default", {})))
    svc.set_config = AsyncMock(return_value=True)
    return svc


def _request(config_service: AsyncMock, body: dict[str, Any], *, role: str = "member") -> MagicMock:
    user = {"userId": "user-1", "orgId": "org-1", "role": role}
    registry = AsyncMock()
    registry.get_connector_instance = AsyncMock(return_value=_instance())
    registry.get_connector_metadata = AsyncMock(return_value=copy.deepcopy(_METADATA))
    registry.update_connector_instance = AsyncMock(return_value={"_key": "conn1"})

    container = SimpleNamespace(
        logger=MagicMock(return_value=logging.getLogger("test")),
        config_service=MagicMock(return_value=config_service),
    )
    request = MagicMock()
    request.state.user.get = lambda key, default=None: user.get(key, default)
    request.json = AsyncMock(return_value=body)
    request.app.container = container
    request.app.state = SimpleNamespace(connector_registry=registry)
    return request


@pytest.fixture(autouse=True)
def _edition_and_access() -> Iterator[None]:
    with (
        patch(f"{_ROUTER}.resolve_config_service", side_effect=lambda container, org_id: container.config_service()),
        patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()),
        patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock),
        patch(f"{_ROUTER}._get_secret_oauth_field_names_from_registry", return_value={"clientSecret"}),
    ):
        yield


def _saved(config_service: AsyncMock, path: str = _CONFIG_PATH) -> dict[str, Any] | list[dict[str, Any]]:
    saves = [call.args[1] for call in config_service.set_config.await_args_list if call.args[0] == path]
    assert len(saves) == 1, f"expected one save to {path}, got {len(saves)}"
    return saves[0]


def _assert_secrets_masked(auth: dict[str, Any]) -> None:
    assert auth["apiToken"] == REDACTED_PLACEHOLDER
    assert auth["tokenSecret"] == REDACTED_PLACEHOLDER
    assert auth["baseUrl"] == "https://jira.example.com"
    assert auth["serviceAccountJson"] == ""
    serialized = json.dumps(auth)
    assert _STORED_API_TOKEN not in serialized
    assert _STORED_TOKEN_SECRET not in serialized


async def test_config_read_masks_stored_secrets() -> None:
    config_service = _config_service()

    with patch(f"{_ROUTER}.is_request_admin", return_value=False):
        result = await get_connector_instance_config("conn1", _request(config_service, {}))

    _assert_secrets_masked(result["config"]["config"]["auth"])


async def test_config_read_masks_client_secret_stored_in_oauth_connector_auth() -> None:
    config_service = _config_service("OAUTH")
    request = _request(config_service, {})
    request.app.state.connector_registry.get_connector_instance.return_value = _instance("OAUTH")

    with patch(f"{_ROUTER}.is_request_admin", return_value=False):
        result = await get_connector_instance_config("conn1", request)

    auth = result["config"]["config"]["auth"]
    assert auth["clientSecret"] == REDACTED_PLACEHOLDER
    assert auth["clientId"] == "stored-client-id"


_Save = Callable[[MagicMock], Awaitable[dict[str, Any]]]
_SAVES: list[tuple[str, _Save, dict[str, Any]]] = [
    ("auth", lambda request: update_connector_instance_auth_config("conn1", request, AsyncMock()), {"auth": {"baseUrl": "https://jira.example.com"}}),
    ("config", lambda request: update_connector_instance_config("conn1", request), {"sync": {"selectedStrategy": "SCHEDULED"}}),
    ("filters-sync", lambda request: update_connector_instance_filters_sync_config("conn1", request, AsyncMock()), {"sync": {"selectedStrategy": "SCHEDULED"}}),
]


@pytest.mark.parametrize(("route", "save", "body"), _SAVES, ids=[route for route, _, _ in _SAVES])
async def test_each_save_answers_with_secrets_masked(route: str, save: _Save, body: dict[str, Any]) -> None:
    config_service = _config_service()

    result = await save(_request(config_service, body))

    _assert_secrets_masked(result["config"]["auth"])
    saved_auth = _saved(config_service)["auth"]
    assert saved_auth["apiToken"] == _STORED_API_TOKEN
    assert saved_auth["tokenSecret"] == _STORED_TOKEN_SECRET


async def test_auth_save_that_sends_the_mask_keeps_the_stored_secrets() -> None:
    config_service = _config_service()
    body = {"auth": {"apiToken": REDACTED_PLACEHOLDER, "tokenSecret": REDACTED_PLACEHOLDER, "baseUrl": "https://new.example.com"}}

    result = await update_connector_instance_auth_config("conn1", _request(config_service, body), AsyncMock())

    saved_auth = _saved(config_service)["auth"]
    assert saved_auth["apiToken"] == _STORED_API_TOKEN
    assert saved_auth["tokenSecret"] == _STORED_TOKEN_SECRET
    assert saved_auth["baseUrl"] == "https://new.example.com"
    assert result["config"]["auth"]["apiToken"] == REDACTED_PLACEHOLDER


async def test_auth_save_with_a_new_secret_replaces_the_stored_one() -> None:
    config_service = _config_service()
    body = {"auth": {"apiToken": "new-api-token", "tokenSecret": REDACTED_PLACEHOLDER}}

    result = await update_connector_instance_auth_config("conn1", _request(config_service, body), AsyncMock())

    saved_auth = _saved(config_service)["auth"]
    assert saved_auth["apiToken"] == "new-api-token"
    assert saved_auth["tokenSecret"] == _STORED_TOKEN_SECRET
    assert result["config"]["auth"]["apiToken"] == REDACTED_PLACEHOLDER


async def test_config_save_that_sends_the_mask_keeps_the_secret_and_the_connector_signed_in() -> None:
    config_service = _config_service()
    request = _request(
        config_service,
        {"auth": {"apiToken": REDACTED_PLACEHOLDER, "baseUrl": "https://jira.example.com"}, "sync": {"selectedStrategy": "SCHEDULED"}},
    )

    result = await update_connector_instance_config("conn1", request)

    assert _saved(config_service)["auth"]["apiToken"] == _STORED_API_TOKEN
    assert result["config"]["auth"]["apiToken"] == REDACTED_PLACEHOLDER
    updates = request.app.state.connector_registry.update_connector_instance.await_args.kwargs["updates"]
    assert "isActive" not in updates
    assert "isAuthenticated" not in updates


async def test_config_save_with_a_new_secret_replaces_the_stored_one() -> None:
    config_service = _config_service()
    request = _request(config_service, {"auth": {"apiToken": "new-api-token"}})

    result = await update_connector_instance_config("conn1", request)

    assert _saved(config_service)["auth"]["apiToken"] == "new-api-token"
    assert result["config"]["auth"]["apiToken"] == REDACTED_PLACEHOLDER
    updates = request.app.state.connector_registry.update_connector_instance.await_args.kwargs["updates"]
    assert updates["isActive"] is False


async def test_oauth_auth_save_that_sends_the_mask_keeps_the_oauth_apps_secret() -> None:
    config_service = _config_service("OAUTH")
    body = {"auth": {"clientId": "stored-client-id", "clientSecret": REDACTED_PLACEHOLDER, "oauthConfigId": "app-1"}}
    request = _request(config_service, body, role="admin")

    with (
        patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance("OAUTH")),
        patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret"]),
        patch(f"{_ROUTER}._get_oauth_config_path", return_value=_OAUTH_APPS_PATH),
        patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock),
        patch(f"{_ROUTER}._link_to_shared_oauth_app", new_callable=AsyncMock, return_value=None),
    ):
        await update_connector_instance_auth_config("conn1", request, AsyncMock())

    (app,) = _saved(config_service, _OAUTH_APPS_PATH)
    assert app["config"]["clientSecret"] == _STORED_CLIENT_SECRET
