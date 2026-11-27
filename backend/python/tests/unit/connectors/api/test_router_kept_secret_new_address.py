"""A connector save must not send a stored secret to a different server.

The form sends a stored secret back as the mask and the save keeps the stored value, so a
changed address would otherwise receive it. The secret has to be typed again.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.redaction import REDACTED_PLACEHOLDER
from app.connectors.api import router as router_module
from app.connectors.api.router import (
    check_connector_connection,
    update_connector_instance_auth_config,
    update_connector_instance_config,
)
from app.connectors.core.base.connector.connector_service import ConnectionCheckResult
from tests.support.in_memory_config import InMemoryConfigService

CONNECTOR_ID = "conn-1"
PATH = f"/services/connectors/{CONNECTOR_ID}/config"
MESSAGE = "Enter the credentials again to use this new endpoint."
SECRET = {"apiToken": "saved-token"}
SAVED_AUTH = {"baseUrl": "https://wiki.example.com", "authType": "API_TOKEN", **SECRET}
METADATA = {
    "config": {
        "auth": {
            "schemas": {
                "API_TOKEN": {
                    "fields": [
                        {"name": "baseUrl", "fieldType": "URL"},
                        {"name": "apiToken", "fieldType": "PASSWORD"},
                    ]
                }
            }
        }
    }
}


class Store(InMemoryConfigService):
    """A real store: the route may save through set_config or a read-modify-write."""

    def __init__(self, auth: dict[str, Any] | None) -> None:
        super().__init__()
        if auth:
            self.values[PATH] = {"auth": dict(auth)}


def _request(body: dict[str, Any], metadata: dict[str, Any] = METADATA) -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test_kept_secret"))
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value=metadata)
    registry.update_connector_instance = AsyncMock(return_value={"_key": CONNECTOR_ID})
    request.app.state.connector_registry = registry
    return request


def _schema(*fields: dict[str, Any]) -> dict[str, Any]:
    return {"config": {"auth": {"schemas": {"API_TOKEN": {"fields": [*fields, {"name": "apiToken", "fieldType": "PASSWORD"}]}}}}}


async def _full_save(store: Store, auth: dict[str, Any], metadata: dict[str, Any] = METADATA, *, auth_only: bool = False) -> dict[str, Any]:
    instance = {"_key": CONNECTOR_ID, "type": "Wiki", "name": "w", "isActive": False, "authType": "API_TOKEN",
                "scope": "team", "createdBy": "u1"}
    with (
        patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=instance)),
        patch.object(router_module, "resolve_config_service", return_value=store),
        patch.object(router_module, "_require_valid_auth_config"),
    ):
        request = _request({"auth": auth}, metadata)
        if auth_only:
            graph = AsyncMock(get_document=AsyncMock(return_value={"scope": "team"}))
            return await update_connector_instance_auth_config(CONNECTOR_ID, request, graph)
        return await update_connector_instance_config(CONNECTOR_ID, request)


MASKED = {"apiToken": REDACTED_PLACEHOLDER}


async def test_a_new_address_with_the_secret_still_masked_is_refused() -> None:
    store = Store(SAVED_AUTH)

    with pytest.raises(HTTPException) as caught:
        await _full_save(store, {"baseUrl": "https://collect.example.net", **MASKED})

    assert caught.value.status_code == 400
    assert caught.value.detail == MESSAGE
    assert store.values[PATH]["auth"] == SAVED_AUTH


async def test_a_new_address_with_the_secret_typed_again_is_saved() -> None:
    store = Store(SAVED_AUTH)

    await _full_save(store, {"baseUrl": "https://other.example.com", "apiToken": "typed"})

    assert store.values[PATH]["auth"]["apiToken"] == "typed"
    assert store.values[PATH]["auth"]["baseUrl"] == "https://other.example.com"


async def test_the_same_address_keeps_the_stored_secret() -> None:
    store = Store(SAVED_AUTH)

    await _full_save(store, {"baseUrl": "HTTPS://Wiki.Example.com/", **MASKED})

    assert store.values[PATH]["auth"]["apiToken"] == "saved-token"


async def test_a_first_time_setup_is_not_refused() -> None:
    store = Store(None)

    await _full_save(store, {"baseUrl": "https://wiki.example.com", "apiToken": "typed"})

    assert store.values[PATH]["auth"]["apiToken"] == "typed"


async def test_an_oauth_connector_whose_secret_lives_on_its_app_is_not_refused() -> None:
    store = Store({"authType": "OAUTH", "oauthConfigId": "app-1", "instanceUrl": "https://a.example.com"})
    instance = {"_key": CONNECTOR_ID, "type": "Wiki", "name": "w", "isActive": False, "authType": "OAUTH",
                "scope": "team", "createdBy": "u1"}
    with (
        patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=instance)),
        patch.object(router_module, "resolve_config_service", return_value=store),
        patch.object(router_module, "_require_valid_auth_config"),
        patch.object(router_module, "_link_to_shared_oauth_app", AsyncMock(return_value=None)),
    ):
        request = _request({"auth": {"oauthConfigId": "app-1", "instanceUrl": "https://b.example.com"}})
        result = await update_connector_instance_config(CONNECTOR_ID, request)

    assert result["success"] is True
    assert store.values[PATH]["auth"]["instanceUrl"] == "https://b.example.com"


NO_ADDRESS = {"authType": "API_TOKEN", **SECRET}


@pytest.mark.parametrize("auth_only", [False, True], ids=["full-save", "auth-only"])
@pytest.mark.parametrize("key", ["baseUrl", "host", "domain", "instanceUrl", "base_url", "endpointUrl", "consoleUrl",
                                 "sharepointDomain", "accountIdentifier"])
async def test_a_new_address_is_refused_even_when_none_was_stored(key: str, auth_only: bool) -> None:
    store = Store(NO_ADDRESS)

    with pytest.raises(HTTPException) as caught:
        await _full_save(store, {key: "collect.example.net", **MASKED}, auth_only=auth_only)

    assert caught.value.detail == MESSAGE
    assert store.values[PATH]["auth"] == NO_ADDRESS


@pytest.mark.parametrize("auth_only", [False, True], ids=["full-save", "auth-only"])
async def test_an_url_typed_field_with_any_name_counts_as_an_address(auth_only: bool) -> None:
    store = Store(NO_ADDRESS)
    metadata = _schema({"name": "wikiLocation", "fieldType": "URL"})

    with pytest.raises(HTTPException):
        await _full_save(store, {"wikiLocation": "https://collect.example.net", **MASKED}, metadata, auth_only=auth_only)


@pytest.mark.parametrize("auth_only", [False, True], ids=["full-save", "auth-only"])
async def test_an_unchanged_schema_default_is_not_a_change(auth_only: bool) -> None:
    store = Store(NO_ADDRESS)
    metadata = _schema({"name": "port", "fieldType": "NUMBER", "default_value": "445"})

    await _full_save(store, {"port": "445", **MASKED}, metadata, auth_only=auth_only)
    assert store.values[PATH]["auth"]["apiToken"] == "saved-token"

    with pytest.raises(HTTPException):
        await _full_save(store, {"port": "9999", **MASKED}, metadata, auth_only=auth_only)


async def test_a_value_is_a_change_when_the_schema_has_no_default() -> None:
    store = Store(NO_ADDRESS)

    with pytest.raises(HTTPException):
        await _full_save(store, {"port": "445", **MASKED}, _schema({"name": "port", "fieldType": "NUMBER"}))


async def test_a_blank_address_beside_a_kept_secret_is_not_a_change() -> None:
    store = Store(NO_ADDRESS)

    await _full_save(store, {"instanceUrl": "", **MASKED})

    assert store.values[PATH]["auth"]["apiToken"] == "saved-token"


@pytest.mark.parametrize("saved", [SAVED_AUTH, NO_ADDRESS], ids=["stored-address", "no-stored-address"])
@pytest.mark.parametrize("key", ["baseUrl", "instanceUrl", "host"])
async def test_the_connection_check_does_not_send_the_stored_secret_to_a_new_address(saved: dict, key: str) -> None:
    check = AsyncMock(return_value=ConnectionCheckResult(success=True, message="ok"))
    connector = MagicMock(check_connection=check)
    request = _request({"connectorId": CONNECTOR_ID, "auth": {key: "https://collect.example.net", **MASKED}})
    store = Store(saved)
    instance = {"type": "Wiki", "authType": "API_TOKEN", "scope": "team"}
    with (
        patch.object(router_module, "check_beta_connector_access", AsyncMock()),
        patch.object(router_module, "_connection_check_class", return_value=connector),
        patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=instance)),
        patch.object(router_module, "resolve_config_service", return_value=store),
    ):
        result = await check_connector_connection("Wiki", request)

    assert result == {"success": False, "message": MESSAGE}
    check.assert_not_awaited()


async def test_the_connection_check_runs_for_the_same_address_and_for_a_blank_one() -> None:
    check = AsyncMock(return_value=ConnectionCheckResult(success=True, message="ok"))
    connector = MagicMock(check_connection=check)
    instance = {"type": "Wiki", "authType": "API_TOKEN", "scope": "team"}
    for auth in ({"baseUrl": "https://wiki.example.com", **MASKED}, {"instanceUrl": "", **MASKED}):
        with (
            patch.object(router_module, "check_beta_connector_access", AsyncMock()),
            patch.object(router_module, "_connection_check_class", return_value=connector),
            patch.object(router_module, "get_validated_connector_instance", AsyncMock(return_value=instance)),
            patch.object(router_module, "resolve_config_service", return_value=Store(SAVED_AUTH)),
        ):
            result = await check_connector_connection("Wiki", _request({"connectorId": CONNECTOR_ID, "auth": auth}))
        assert result == {"success": True, "message": "ok"}
