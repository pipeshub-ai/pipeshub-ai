"""The save routes ask the edition about a connector's sync settings before storing them.

Each problem the edition names refuses the save with a 400 that carries it, and nothing is stored.
With no problems the save goes through as before.
"""

from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_ROUTER = "app.connectors.api.router"
CONNECTOR_TYPE = "Example"


def _stored() -> dict[str, Any]:
    return {
        "auth": {"authType": "API_TOKEN"},
        "filters": {"sync": {"values": {}}, "indexing": {"values": {}}},
        "sync": {"selectedStrategy": "MANUAL", "customValues": {"flag": False}},
    }


def _instance() -> dict[str, Any]:
    return {
        "_key": "c1", "type": CONNECTOR_TYPE, "scope": "team", "createdBy": "u1", "authType": "API_TOKEN",
        "isActive": False, "isConfigured": True, "isAuthenticated": True, "name": "Example",
        "appGroup": "Examples", "appGroupId": "g1",
    }


def _request(stored: dict[str, Any], body: dict[str, Any]) -> MagicMock:
    config_service = AsyncMock()

    async def get_config(path: str, *args: object, **kwargs: object) -> object:
        if path.endswith("/config") and "/connectors/" in path:
            return stored
        return kwargs.get("default")

    async def set_config(path: str, value: dict[str, Any], *args: object, **kwargs: object) -> bool:
        stored.clear()
        stored.update(copy.deepcopy(value))
        return True

    config_service.get_config = AsyncMock(side_effect=get_config)
    config_service.set_config = AsyncMock(side_effect=set_config)
    registry = AsyncMock()
    registry.get_connector_metadata = AsyncMock(return_value={"config": {}})
    registry.update_connector_instance = AsyncMock(return_value={"_key": "c1"})
    registry.get_connector_instance = AsyncMock(return_value=_instance())
    container = MagicMock()
    container.logger.return_value = logging.getLogger("test")
    container.config_service.return_value = config_service
    container.messaging_producer = AsyncMock()
    req = MagicMock()
    user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.headers = MagicMock()
    req.headers.get = lambda k, default=None: default
    req.json = AsyncMock(return_value=body)
    req.app.container = container
    req.app.state.connector_registry = registry
    req.app.state.graph_provider = AsyncMock()
    return req


async def _save_filters(req: MagicMock) -> dict[str, Any]:
    from app.connectors.api.router import update_connector_instance_filters_sync_config

    return await update_connector_instance_filters_sync_config("c1", req, graph_provider=AsyncMock())


async def _save_config(req: MagicMock) -> dict[str, Any]:
    from app.connectors.api.router import update_connector_instance_config

    return await update_connector_instance_config("c1", req)


BODY = {"sync": {"selectedStrategy": "MANUAL", "customValues": {"flag": True}}}


@pytest.mark.parametrize("save", [_save_filters, _save_config])
class TestEditionSyncChecks:
    async def _run(self, save, problems: list[str]) -> tuple[MagicMock, dict[str, Any], AsyncMock]:
        stored = _stored()
        req = _request(stored, BODY)
        seen: list[tuple[object, ...]] = []

        async def check_settings(*args: object) -> list[str]:
            seen.append(copy.deepcopy(args[1:]))  # the save goes on to change the stored dict
            return problems

        check = AsyncMock(side_effect=check_settings)
        check.seen = seen
        with patch(f"{_ROUTER}.validate_sync_settings", check), \
             patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()), \
             patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock), \
             patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1000):
            await save(req)
        return req, stored, check

    async def test_a_problem_refuses_the_save_with_its_message(self, save) -> None:
        with pytest.raises(HTTPException) as refused:
            await self._run(save, ["Install the app first."])
        assert refused.value.status_code == 400
        assert refused.value.detail == "Install the app first."

    async def test_a_refused_save_stores_nothing(self, save) -> None:
        stored = _stored()
        req = _request(stored, BODY)
        with patch(f"{_ROUTER}.validate_sync_settings", AsyncMock(return_value=["No."])), \
             patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()), \
             patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock), \
             patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1000), \
             pytest.raises(HTTPException):
            await save(req)
        req.app.container.config_service.return_value.set_config.assert_not_awaited()
        assert stored["sync"]["customValues"]["flag"] is False

    async def test_no_problems_saves_and_the_check_sees_old_and_new_settings(self, save) -> None:
        _, stored, check = await self._run(save, [])
        assert stored["sync"]["customValues"]["flag"] is True
        [(connector_id, connector_type, old_config, new_config)] = check.seen
        assert (connector_id, connector_type) == ("c1", CONNECTOR_TYPE)
        assert old_config["sync"]["customValues"]["flag"] is False
        assert new_config["sync"]["customValues"]["flag"] is True


async def test_the_default_edition_names_no_problems() -> None:
    from app.edition_services import validate_sync_settings

    assert await validate_sync_settings(MagicMock(), "c1", CONNECTOR_TYPE, {}, {"sync": {}}) == []
