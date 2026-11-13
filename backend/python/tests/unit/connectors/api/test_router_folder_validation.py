"""The save routes hold only new folder values to the newer folder rules.

Both save handlers run end to end against a config store holding real nested
dicts, so the merge they do into the stored config is the real one: the values
saved before must be read before that merge, or a saved value looks new and a
new one looks saved. Enabling never applies the newer rules.
"""

from __future__ import annotations

import copy
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.core.registry.connector_builder import CommonFields

_ROUTER = "app.connectors.api.router"
CONNECTOR_TYPE = "Folder Share"


def _metadata(*, path_style: str | None = "windows", wildcards: bool = False) -> dict[str, Any]:
    field = CommonFields.folder_paths_filter("share", path_style=path_style, wildcards=wildcards).to_schema_dict()
    return {"config": {"filters": {"sync": {"schema": {"fields": [field]}}}}}


def _stored(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "auth": {"authType": "BASIC_AUTH"},
        "credentials": {"access_token": "tok"},
        "filters": {"sync": {"values": values}, "indexing": {"values": {}}},
        "sync": {"selectedStrategy": "MANUAL"},
    }


def _folders(operator: str, *values: str) -> dict[str, Any]:
    return {"folder_paths": {"operator": operator, "value": list(values)}}


MODIFIED = {"modified": {"operator": "is_after", "value": {"start": 1700000000000}}}


def _request(stored: dict[str, Any], body: dict[str, Any], metadata: dict[str, Any]) -> MagicMock:
    config_service = AsyncMock()

    async def get_config(path: str, *args: object, **kwargs: object) -> object:
        if path.endswith("/config") and "/connectors/" in path:
            return stored
        return kwargs.get("default")

    config_service.get_config = AsyncMock(side_effect=get_config)

    async def set_config(path: str, value: dict[str, Any], *args: object, **kwargs: object) -> bool:
        stored.clear()
        stored.update(copy.deepcopy(value))
        return True

    config_service.set_config = AsyncMock(side_effect=set_config)

    registry = AsyncMock()

    async def get_metadata(connector_type: str, instance_data: object = None) -> dict | None:
        return metadata if connector_type == CONNECTOR_TYPE else None

    registry.get_connector_metadata = AsyncMock(side_effect=get_metadata)
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


def _instance(**overrides: object) -> dict[str, Any]:
    inst = {
        "_key": "c1", "type": CONNECTOR_TYPE, "scope": "team", "createdBy": "u1",
        "authType": "BASIC_AUTH", "isActive": False, "isConfigured": True,
        "isAuthenticated": True, "name": "Share", "appGroup": "Shares", "appGroupId": "g1",
    }
    inst.update(overrides)
    return inst


async def _save_filters(stored: dict[str, Any], values: dict[str, Any], **meta: bool | str | None) -> dict[str, Any]:
    from app.connectors.api.router import update_connector_instance_filters_sync_config

    body = {"filters": {"sync": {"values": values}}}
    req = _request(stored, body, _metadata(**meta))
    with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()), \
         patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock), \
         patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1000):
        return await update_connector_instance_filters_sync_config("c1", req, graph_provider=AsyncMock())


async def _save_config(stored: dict[str, Any], values: dict[str, Any], **meta: bool | str | None) -> dict[str, Any]:
    from app.connectors.api.router import update_connector_instance_config

    body = {"filters": {"sync": {"values": values}}}
    req = _request(stored, body, _metadata(**meta))
    with patch(f"{_ROUTER}.get_validated_connector_instance", new_callable=AsyncMock, return_value=_instance()), \
         patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock), \
         patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1000):
        return await update_connector_instance_config("c1", req)


async def _enable(stored: dict[str, Any], **meta: bool | str | None) -> dict[str, Any]:
    from app.connectors.api.router import toggle_connector_instance

    req = _request(stored, {"type": "sync"}, _metadata(**meta))
    with patch(f"{_ROUTER}.check_beta_connector_access", new_callable=AsyncMock), \
         patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=1000), \
         patch(f"{_ROUTER}._ensure_connector_initialized", new_callable=AsyncMock, return_value=MagicMock()):
        return await toggle_connector_instance("c1", req, graph_provider=AsyncMock())


SAVES = [_save_filters, _save_config]


@pytest.mark.parametrize("save", SAVES)
class TestSaveRoutes:
    async def test_a_new_empty_folder_name_is_refused(self, save) -> None:
        stored = _stored(_folders("in", "a//b"))
        with pytest.raises(HTTPException) as exc:
            await save(stored, _folders("in", "a//b", "c//d"))
        assert exc.value.status_code == 400
        assert exc.value.detail == 'Folder "c//d" has an empty folder name between two slashes. Remove the extra "/".'

    async def test_a_new_empty_folder_name_is_refused_on_a_first_save(self, save) -> None:
        with pytest.raises(HTTPException) as exc:
            await save(_stored({}), _folders("in", "a//b"))
        assert exc.value.status_code == 400

    async def test_a_stored_value_kept_as_is_saves(self, save) -> None:
        stored = _stored(_folders("in", "a//b"))
        result = await save(copy.deepcopy(stored), {**_folders("in", "a//b"), **MODIFIED})
        assert result["success"] is True

    async def test_a_stored_value_saves_when_only_another_filter_changes(self, save) -> None:
        stored = _stored({**_folders("in", "a//b", "\\\\srv\\share"), **MODIFIED})
        result = await save(stored, _folders("in", "a//b", "\\\\srv\\share"))
        assert result["success"] is True

    async def test_switching_a_stored_root_to_exclude_is_refused(self, save) -> None:
        stored = _stored(_folders("in", "/"))
        with pytest.raises(HTTPException) as exc:
            await save(stored, _folders("not_in", "/"))
        assert exc.value.status_code == 400
        assert "leaves nothing to sync" in exc.value.detail

    async def test_a_stored_exclude_of_the_root_saves_unchanged(self, save) -> None:
        stored = _stored(_folders("not_in", "/"))
        assert (await save(stored, _folders("not_in", "/")))["success"] is True

    async def test_a_new_network_path_is_refused_on_a_share(self, save) -> None:
        with pytest.raises(HTTPException) as exc:
            await save(_stored({}), _folders("in", "\\\\server\\share\\x"))
        assert "network path" in exc.value.detail

    async def test_a_network_path_is_a_folder_name_on_an_object_store(self, save) -> None:
        result = await save(_stored({}), _folders("in", "//server/share"), path_style=None)
        assert result["success"] is True

    async def test_without_wildcards_a_double_star_inside_a_name_is_accepted(self, save) -> None:
        assert (await save(_stored({}), _folders("in", "a**b")))["success"] is True

    async def test_with_wildcards_a_double_star_inside_a_name_is_refused(self, save) -> None:
        with pytest.raises(HTTPException) as exc:
            await save(_stored({}), _folders("in", "a**b"), wildcards=True)
        assert '"**" must be a whole folder name' in exc.value.detail

    async def test_climbing_out_is_refused_even_when_stored(self, save) -> None:
        stored = _stored(_folders("in", "../x"))
        with pytest.raises(HTTPException) as exc:
            await save(stored, _folders("in", "../x"))
        assert "without \"..\"" in exc.value.detail


class TestEnable:
    async def test_a_stored_exclude_of_the_root_enables(self) -> None:
        assert (await _enable(_stored(_folders("not_in", "/"))))["success"] is True

    async def test_a_stored_empty_folder_name_enables(self) -> None:
        assert (await _enable(_stored(_folders("in", "a//b", "\\\\srv\\x", "a**b")), wildcards=True))["success"] is True

    async def test_climbing_out_still_blocks_enabling(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _enable(_stored(_folders("in", "../x")))
        assert exc.value.status_code == 400
