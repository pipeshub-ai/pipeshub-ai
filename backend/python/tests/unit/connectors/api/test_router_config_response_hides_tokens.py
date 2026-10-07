"""The connector config routes answer without the owner's OAuth tokens.

The stored config keeps ``credentials`` (access and refresh tokens) and ``oauth``
(flow state) for the server's own use. GET already left them out of its answer;
the save routes returned the stored document as it was, tokens included.
"""

import copy
import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.api.router import (
    get_connector_instance_config,
    update_connector_instance_auth_config,
    update_connector_instance_config,
    update_connector_instance_filters_sync_config,
)

_ROUTER = "app.connectors.api.router"
_CONFIG_PATH = "/services/connectors/conn1/config"

_TOKENS = {"access_token": "stored-access-token", "refresh_token": "stored-refresh-token"}
_OAUTH_STATE = {"state": "stored-oauth-state"}


def _stored_config() -> dict[str, Any]:
    return {
        "auth": {"connectorScope": "personal", "authType": "API_TOKEN"},
        "sync": {"selectedStrategy": "MANUAL"},
        "filters": {"sync": {"values": {}}, "indexing": {"values": {}}},
        "credentials": copy.deepcopy(_TOKENS),
        "oauth": copy.deepcopy(_OAUTH_STATE),
    }


def _instance() -> dict[str, Any]:
    return {
        "_key": "conn1",
        "type": "jira",
        "authType": "API_TOKEN",
        "isActive": False,
        "scope": "personal",
        "createdBy": "user-1",
        "name": "My Jira",
    }


def _config_service() -> AsyncMock:
    stored = _stored_config()
    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=lambda path, **kwargs: stored if path == _CONFIG_PATH else kwargs.get("default", {}))
    svc.set_config = AsyncMock(return_value=True)
    return svc


def _request(config_service: AsyncMock, body: dict[str, Any]) -> MagicMock:
    user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    registry = AsyncMock()
    registry.get_connector_instance = AsyncMock(return_value=_instance())
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
    ):
        yield


def _saved_config(config_service: AsyncMock) -> dict[str, Any]:
    config_service.set_config.assert_awaited_once()
    path, saved = config_service.set_config.await_args.args
    assert path == _CONFIG_PATH
    return saved


def _assert_no_tokens(returned: dict[str, Any]) -> None:
    assert "credentials" not in returned
    assert "oauth" not in returned
    assert {"auth", "sync", "filters"} <= returned.keys()


async def test_filters_sync_save_answers_without_tokens_and_keeps_them_stored() -> None:
    config_service = _config_service()
    request = _request(config_service, {"sync": {"selectedStrategy": "SCHEDULED"}})

    result = await update_connector_instance_filters_sync_config("conn1", request, AsyncMock())

    _assert_no_tokens(result["config"])
    assert result["config"]["sync"]["selectedStrategy"] == "SCHEDULED"
    saved = _saved_config(config_service)
    assert saved["credentials"] == _TOKENS
    assert saved["oauth"] == _OAUTH_STATE


async def test_config_save_without_auth_answers_without_tokens_and_keeps_them_stored() -> None:
    config_service = _config_service()
    request = _request(config_service, {"sync": {"selectedStrategy": "SCHEDULED"}})

    result = await update_connector_instance_config("conn1", request)

    _assert_no_tokens(result["config"])
    saved = _saved_config(config_service)
    assert saved["credentials"] == _TOKENS
    assert saved["oauth"] == _OAUTH_STATE


async def test_config_save_with_auth_answers_without_token_keys() -> None:
    config_service = _config_service()
    request = _request(config_service, {"auth": {"apiToken": "new-api-token"}})

    result = await update_connector_instance_config("conn1", request)

    _assert_no_tokens(result["config"])
    saved = _saved_config(config_service)
    assert saved["credentials"] is None
    assert saved["oauth"] is None


async def test_auth_save_answers_without_token_keys() -> None:
    config_service = _config_service()
    request = _request(config_service, {"auth": {"apiToken": "new-api-token"}})

    result = await update_connector_instance_auth_config("conn1", request, AsyncMock())

    _assert_no_tokens(result["config"])
    saved = _saved_config(config_service)
    assert saved["credentials"] is None
    assert saved["oauth"] is None


async def test_config_read_answers_without_tokens() -> None:
    config_service = _config_service()
    request = _request(config_service, {})

    with patch(f"{_ROUTER}.is_request_admin", return_value=False):
        result = await get_connector_instance_config("conn1", request)

    _assert_no_tokens(result["config"]["config"])
    config_service.set_config.assert_not_awaited()
