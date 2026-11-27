"""API1-S3: an admin toolset-instance write when the store blips during its re-read of the shared list.

The instance list at /services/toolset-instances is one document for every org. ``_save_org_instances``
re-reads it under its lock and replays the change onto that read. A failed re-read must refuse the write;
on main it reads as ``[]`` and the write keeps only this request's rows, erasing every other org's.
The real ConfigurationService runs on FlakyKV (fails chosen reads of one key, counted from 1).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401 - binds the routes in the order their circular imports need
from app.config.configuration_service import ConfigurationService
from app.utils import error_class
from tests.unit.connectors.test_oauth_config_read_outage import LOG, FlakyKV, _refusal

INSTANCES = "/services/toolset-instances"
A1 = {"_id": "a-1", "orgId": "org-a", "toolsetType": "calculator", "authType": "NONE", "instanceName": "A one"}
A2 = {"_id": "a-2", "orgId": "org-a", "toolsetType": "calculator", "authType": "NONE", "instanceName": "A two"}
B1 = {"_id": "b-1", "orgId": "org-b", "toolsetType": "jira", "authType": "NONE", "instanceName": "B's Jira"}
SIGN_IN = "/services/toolsets/a-1/u1"
LOAD, SAVE_REREAD = 1, 2  # reads of INSTANCES: the mutation load, then the re-read inside _save_org_instances


@pytest.fixture
async def kv(monkeypatch: pytest.MonkeyPatch) -> FlakyKV:
    import secrets
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    store = FlakyKV()
    await store.create_key(INSTANCES, [A1, A2, B1])
    await store.create_key(SIGN_IN, {"isAuthenticated": True, "credentials": {"access_token": "t"}})
    return store


@pytest.fixture
async def config(kv: FlakyKV) -> ConfigurationService:
    service = ConfigurationService(LOG, kv)
    yield service
    await service.close()


def _admin_of_org_a():  # noqa: ANN202 - a patch context
    graph = MagicMock()
    graph.check_toolset_instance_in_use = AsyncMock(return_value=[])
    return patch.multiple(
        "app.api.routes.toolsets",
        _get_user_context=MagicMock(return_value={"user_id": "u1", "org_id": "org-a"}),
        _check_user_is_admin=AsyncMock(return_value=True),
        _get_graph_provider=MagicMock(return_value=graph),
        sync_capabilities=AsyncMock(return_value=False),
    )


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {
        "name": "calculator", "display_name": "Calculator", "supported_auth_types": ["NONE"], "tools": [],
    }
    request.app.state.toolset_registry = registry
    return request


def _assert_retry_later(refusal: Exception | None) -> None:
    assert isinstance(refusal, HTTPException) and refusal.status_code == 503
    assert refusal.headers == {"Retry-After": "5"}
    assert error_class.classify_error(refusal.__cause__) == "unavailable"


def _ids(kv: FlakyKV) -> list[str]:
    return sorted(i["_id"] for i in kv.saved(INSTANCES))


async def _delete(config: ConfigurationService) -> Any:  # noqa: ANN401
    from app.api.routes.toolsets import delete_toolset_instance
    return await delete_toolset_instance("a-1", _request({}), config_service=config)


async def _create(config: ConfigurationService) -> Any:  # noqa: ANN401
    from app.api.routes.toolsets import create_toolset_instance
    body = {"instanceName": "A three", "toolsetType": "calculator", "authType": "NONE"}
    return await create_toolset_instance(_request(body), config_service=config)


async def _update(config: ConfigurationService) -> Any:  # noqa: ANN401
    from app.api.routes.toolsets import update_toolset_instance
    return await update_toolset_instance("a-1", _request({"instanceName": "A one renamed"}), config_service=config)


@pytest.mark.parametrize("route", [_delete, _create, _update], ids=["delete", "create", "update"])
async def test_a_failed_reread_refuses_and_keeps_every_orgs_instances(
    kv: FlakyKV, config: ConfigurationService, route: Any,  # noqa: ANN401
) -> None:
    kv.fail_reads[INSTANCES] = {SAVE_REREAD}
    with _admin_of_org_a():
        refusal = await _refusal(route(config))
    assert kv.reads[INSTANCES] == SAVE_REREAD, "the fault must land on the re-read, not the mutation load"
    assert _ids(kv) == ["a-1", "a-2", "b-1"]
    _assert_retry_later(refusal)


async def test_a_delete_whose_save_fails_keeps_the_instances_sign_ins(kv: FlakyKV, config: ConfigurationService) -> None:
    kv.fail_reads[INSTANCES] = {SAVE_REREAD}
    with _admin_of_org_a():
        refusal = await _refusal(_delete(config))
    _assert_retry_later(refusal)
    assert kv.saved(SIGN_IN) is not None, "the instance is still listed, so its users' sign-ins must still be there"


async def test_a_delete_whose_write_fails_keeps_the_instances_sign_ins(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    real_create = kv.create_key

    async def refuse_list_write(key: str, value: Any, overwrite: bool = True, ttl: int | None = None) -> bool:  # noqa: ANN401
        if key == INSTANCES:
            raise ConnectionError("kv down")
        return await real_create(key, value, overwrite, ttl)

    kv.create_key = refuse_list_write  # type: ignore[method-assign]
    with _admin_of_org_a():
        refusal = await _refusal(_delete(config))
    assert isinstance(refusal, HTTPException) and refusal.status_code == 500
    assert _ids(kv) == ["a-1", "a-2", "b-1"]
    assert kv.saved(SIGN_IN) is not None, "the instance is still listed, so its users' sign-ins must still be there"


async def test_control_a_healthy_delete_removes_only_its_instance_and_sign_ins(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    with _admin_of_org_a():
        await _delete(config)
    assert _ids(kv) == ["a-2", "b-1"]
    assert kv.saved(SIGN_IN) is None


async def test_a_first_create_on_a_store_without_the_list_succeeds(kv: FlakyKV, config: ConfigurationService) -> None:
    await kv.delete_key(INSTANCES)
    with _admin_of_org_a():
        result = await _create(config)
    assert result["status"] == "success"
    assert _ids(kv) == [result["instance"]["_id"]]


@pytest.mark.parametrize("read", [LOAD, SAVE_REREAD], ids=["load", "reread"])
@pytest.mark.parametrize("route", [_delete, _create, _update], ids=["delete", "create", "update"])
async def test_a_list_read_that_fails_without_an_outage_is_500(
    kv: FlakyKV, config: ConfigurationService, route: Any, read: int,  # noqa: ANN401
) -> None:
    real_get = kv.get_key

    async def undecodable(key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if key == INSTANCES and kv.reads.get(key, 0) + 1 == read:
            kv.reads[key] = read
            raise ValueError("stored value could not be decrypted")
        return await real_get(key, raise_on_error=raise_on_error)

    kv.get_key = undecodable  # type: ignore[method-assign]
    with _admin_of_org_a():
        refusal = await _refusal(route(config))
    assert isinstance(refusal, HTTPException) and refusal.status_code == 500, "a bug is not an outage: no retry hint"
    assert _ids(kv) == ["a-1", "a-2", "b-1"]
