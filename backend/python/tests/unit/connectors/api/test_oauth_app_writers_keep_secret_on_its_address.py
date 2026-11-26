"""An OAuth app's stored client secret stays with the address it was saved for."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (
    _create_or_update_oauth_config,
    _get_oauth_config_path,
    update_oauth_config,
)
from tests.support.in_memory_config import InMemoryConfigService

_ROUTER = "app.connectors.api.router"
_ADMIN = {"user_id": "u1", "org_id": "org-1", "is_admin": True}
OLD = "https://old.example.com"
NEW = "https://new.example.com"


def _app() -> dict[str, Any]:
    return {
        "_id": "app-1", "orgId": "org-1", "createdBy": "u1", "instanceUrl": OLD,
        "config": {"clientId": "c", "clientSecret": "stored-secret", "instanceUrl": OLD},
    }


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test"))
    return request


def _store() -> InMemoryConfigService:
    """A real store: the route may save through set_config or a read-modify-write."""
    store = InMemoryConfigService()
    store.values[_get_oauth_config_path("Servicenow")] = [_app()]
    return store


def _saved(store: InMemoryConfigService) -> dict[str, Any]:
    return store.values[_get_oauth_config_path("Servicenow")][0]["config"]


async def _put(config: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    with patch(f"{_ROUTER}._get_user_context", return_value=_ADMIN), \
         patch(f"{_ROUTER}._validate_admin_only"), \
         patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
        await update_oauth_config("Servicenow", "app-1", _request({"config": config}), config_service=store)
    return _saved(store)


async def _auth_only(auth: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    with patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId", "clientSecret", "instanceUrl"]), \
         patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
        await _create_or_update_oauth_config(
            connector_type="Servicenow", auth_config=auth, instance_name="App", user_id="u1",
            org_id="org-1", is_admin=True, config_service=store, base_url="", oauth_app_id="app-1",
        )
    return _saved(store)


class TestEditingAnAppByItsId:
    async def test_a_new_address_without_the_secret_is_refused(self) -> None:
        with pytest.raises(HTTPException) as refused:
            await _put({"instanceUrl": NEW})
        assert refused.value.status_code == 400

    async def test_a_new_address_with_the_secret_typed_is_saved(self) -> None:
        saved = await _put({"instanceUrl": NEW, "clientSecret": "typed"})
        assert saved["clientSecret"] == "typed" and saved["instanceUrl"] == NEW

    async def test_the_same_address_is_saved(self) -> None:
        assert (await _put({"instanceUrl": OLD, "clientId": "d"}))["clientSecret"] == "stored-secret"


class TestSavingClientCredentialsOntoAnApp:
    async def test_a_new_address_without_the_secret_is_refused(self) -> None:
        with pytest.raises(HTTPException) as refused:
            await _auth_only({"instanceUrl": NEW})
        assert refused.value.status_code == 400

    async def test_a_new_address_with_the_secret_typed_is_saved(self) -> None:
        assert (await _auth_only({"instanceUrl": NEW, "clientSecret": "typed"}))["clientSecret"] == "typed"

    async def test_the_same_address_is_saved(self) -> None:
        assert (await _auth_only({"instanceUrl": OLD, "clientId": "d"}))["clientSecret"] == "stored-secret"
