"""DELETE /api/v1/records/{record_id} against a stubbed provider: every access-result
shape the route cannot read as an upload fails closed, near-miss origin and connector
values do not pass as uploads, each provider failure keeps its status, and nothing of
the removed DELETE /api/v1/delete/record/{id} route is left behind.
"""

import importlib
import importlib.util
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import app.connectors.api.router as router_mod
import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.api.router import delete_record

ORG_A = "org-A"
RECORD_ID = "rec-1"
KB_ACCESS = {"record": {"origin": "UPLOAD", "connectorName": "KB"}}


def _request(user_id: str = "user-a", org_id: str = ORG_A) -> MagicMock:
    user = {"userId": user_id, "orgId": org_id}
    req = MagicMock()
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.app.container.logger.return_value = MagicMock()
    return req


def _provider(access: object, delete_result: object = None) -> MagicMock:
    provider = MagicMock()
    provider.check_record_access_with_details = AsyncMock(return_value=access)
    provider.delete_record = AsyncMock(
        return_value=delete_result if delete_result is not None else {"success": True, "eventData": None}
    )
    provider.delete_records_and_relations = AsyncMock()
    return provider


async def _call(provider: MagicMock, kafka: AsyncMock) -> dict:
    return await delete_record(RECORD_ID, _request(), provider, kafka)


def _assert_no_delete(provider: MagicMock, kafka: AsyncMock) -> None:
    provider.delete_record.assert_not_awaited()
    provider.delete_records_and_relations.assert_not_awaited()
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("access", [None, {}, [], False], ids=repr)
async def test_falsy_access_result_is_404_and_nothing_is_deleted(access: object) -> None:
    provider, kafka = _provider(access), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == 404
    assert exc.value.detail == "You do not have access to this record"
    _assert_no_delete(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access",
    [
        {"record": None},
        {"permissions": []},
        {"record": {"id": RECORD_ID}},
        {"record": {"origin": None, "connectorName": None}},
        {"record": {"origin": "", "connectorName": ""}},
    ],
    ids=["record-none", "no-record-key", "record-without-origin-or-connector", "both-none", "both-empty"],
)
async def test_access_result_without_usable_record_is_403_not_500(access: dict) -> None:
    provider, kafka = _provider(access), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == 403
    assert "synced from a connector" in exc.value.detail
    _assert_no_delete(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access",
    [{"record": "rec-1"}, {"record": 7}, ["record"], True],
    ids=["record-str", "record-int", "access-list", "access-true"],
)
async def test_wrongly_typed_access_result_never_deletes(access: object) -> None:
    provider, kafka = _provider(access), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code in (403, 500)
    _assert_no_delete(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"origin": "upload", "connectorName": "DRIVE"},
        {"origin": " UPLOAD", "connectorName": "DRIVE"},
        {"origin": "UPLOAD ", "connectorName": "DRIVE"},
        {"origin": "CONNECTOR", "connectorName": "kb"},
        {"origin": "CONNECTOR", "connectorName": "KNOWLEDGE_BASE"},
        {"origin": "CONNECTOR", "connectorName": "KB "},
        {"origin": OriginTypes.UPLOAD, "connectorName": "DRIVE"},
        {"origin": "CONNECTOR", "connectorName": Connectors.KNOWLEDGE_BASE},
        {"origin": ["UPLOAD"], "connectorName": "DRIVE"},
        {"origin": True, "connectorName": True},
        {"Origin": "UPLOAD", "connector_name": "KB"},
    ],
    ids=[
        "origin-lower-case", "origin-leading-space", "origin-trailing-space", "connector-lower-case",
        "connector-enum-name", "connector-trailing-space", "origin-enum-member", "connector-enum-member",
        "origin-in-a-list", "booleans", "wrong-key-names",
    ],
)
async def test_near_miss_values_do_not_pass_as_an_upload(record: dict) -> None:
    provider, kafka = _provider({"record": record}), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == 403
    _assert_no_delete(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delete_result", "status", "detail"),
    [
        ({"success": False, "code": 403, "reason": "User lacks permission to delete records"}, 403,
         "User lacks permission to delete records"),
        ({"success": False, "code": "403", "reason": "Insufficient permissions. User role: READER"}, 403,
         "Insufficient permissions. User role: READER"),
        ({"success": False, "code": 404, "reason": "Record not found: rec-1"}, 404, "Record not found: rec-1"),
        ({"success": False, "code": 400, "reason": "Unsupported connector: X"}, 400, "Unsupported connector: X"),
        ({"success": False, "code": 500, "reason": "Neo4j record deletion failed: boom"}, 500, None),
        ({"success": False, "reason": "Transaction failed: boom"}, 500, None),
        ({"success": False, "code": 403}, 500, None),
        ({"success": False, "code": "nope", "reason": "x"}, 500, None),
        ({"success": False}, 500, None),
    ],
    ids=["403", "string-code-403", "404", "400", "500", "no-code", "403-without-reason", "garbage-code", "bare-failure"],
)
async def test_provider_failure_is_reported_with_its_status(
    delete_result: dict, status: int, detail: str | None
) -> None:
    provider, kafka = _provider(KB_ACCESS, delete_result), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == status
    if detail is not None:
        assert exc.value.detail == detail
    else:
        assert "boom" not in str(exc.value.detail)
        assert "Neo4j" not in str(exc.value.detail)
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_result", [{"reason": "no success key"}, "ok"], ids=["no-success-key", "not-a-dict"])
async def test_malformed_provider_result_is_500_not_a_reported_success(delete_result: object) -> None:
    provider, kafka = _provider(KB_ACCESS, delete_result), AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == 500
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_access_check_error_is_500_without_leaking_and_without_deleting() -> None:
    provider, kafka = _provider(KB_ACCESS), AsyncMock()
    provider.check_record_access_with_details = AsyncMock(
        side_effect=RuntimeError("bolt://neo4j:7687 connection refused")
    )

    with pytest.raises(HTTPException) as exc:
        await _call(provider, kafka)

    assert exc.value.status_code == 500
    assert "bolt" not in str(exc.value.detail)
    _assert_no_delete(provider, kafka)


def _routes() -> list[tuple[str, str]]:
    return [
        (method, route.path)
        for route in router_mod.router.routes
        for method in sorted(getattr(route, "methods", None) or ())
    ]


def test_the_hard_delete_twin_route_is_gone() -> None:
    assert not [path for _, path in _routes() if "/delete/record" in path]
    assert not hasattr(router_mod, "handle_record_deletion")


def test_exactly_one_route_deletes_a_record_by_id() -> None:
    deletes = [path for method, path in _routes() if method == "DELETE" and "record" in path.lower()]
    assert deletes == ["/api/v1/records/{record_id}"]


_RESOLVER_MODULES = ["app.edition_config", "app.connectors.api.router", "app.connectors.api.connector_resolvers"]
if importlib.util.find_spec("app.ee") is not None:
    _RESOLVER_MODULES.append("app.ee.connectors.api.connector_resolvers")


@pytest.mark.parametrize("module", _RESOLVER_MODULES)
def test_assert_hard_delete_record_org_is_removed_everywhere(module: str) -> None:
    mod = importlib.import_module(module)
    assert not hasattr(mod, "assert_hard_delete_record_org")
    assert "assert_hard_delete_record_org" not in (getattr(mod, "__all__", None) or [])


def test_router_no_longer_calls_the_unscoped_provider_delete() -> None:
    assert "delete_records_and_relations" not in inspect.getsource(router_mod)
