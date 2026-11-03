"""Record-content routes and the caller's org: stream_record never serves a caller
without an org or a record whose org only resembles the caller's, and the internal
agent content route answers per org and user claim. Content resolution is replaced
by a sentinel so each test stops at the authorisation decision.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.api import router as router_mod

SENTINEL = object()
RECORD_ID = "rec-1"


def _record(org_id: object) -> MagicMock:
    record = MagicMock()
    record.id = RECORD_ID
    record.org_id = org_id
    record.connector_id = "conn-1"
    record.connector_name = Connectors.GOOGLE_DRIVE
    record.record_name = "a.txt"
    return record


def _graph(record: object, known_orgs: tuple[Any, ...] = ("org-1", "org-2")) -> MagicMock:
    async def get_document(key: object, collection: str, *_a: object, **_k: object) -> dict | None:
        if collection == CollectionNames.ORGS.value:
            return {"_key": key} if key in known_orgs else None
        return {"_key": key, "type": "DRIVE", "isActive": True, "name": "Drive"}

    graph = MagicMock()
    graph.get_record_by_id = AsyncMock(return_value=record)
    graph.get_document = AsyncMock(side_effect=get_document)
    graph.check_record_access_with_details = AsyncMock(return_value={"id": RECORD_ID})
    return graph


def _request(user: dict[str, Any]) -> SimpleNamespace:
    container = SimpleNamespace(logger=lambda: MagicMock(), connectors_map={}, config_service=lambda: MagicMock())
    app_ = SimpleNamespace(container=container, state=SimpleNamespace(connector_registry=MagicMock()))
    return SimpleNamespace(state=SimpleNamespace(user=user), app=app_, headers={})


async def _stream(user: dict[str, Any], graph: MagicMock) -> tuple[int, Any]:
    resolve = AsyncMock(return_value=SENTINEL)
    with patch.object(router_mod, "_resolve_record_content_response", new=resolve):
        try:
            out = await router_mod.stream_record(
                _request(user), RECORD_ID, convertTo=None, version=None, graph_provider=graph, config_service=MagicMock()
            )
        except HTTPException as exc:
            return exc.status_code, exc.detail
    assert out is SENTINEL
    return 200, resolve.await_args.kwargs


async def _internal_content(claims: dict[str, Any], graph: MagicMock) -> tuple[int, Any]:
    resolve = AsyncMock(return_value=SENTINEL)
    with patch.object(router_mod, "_resolve_record_content_response", new=resolve):
        try:
            out = await router_mod.get_record_content_internal(
                _request(claims), RECORD_ID, version=None, graph_provider=graph, config_service=MagicMock(), claims=claims
            )
        except HTTPException as exc:
            return exc.status_code, exc.detail
    assert out is SENTINEL
    return 200, resolve.await_args.kwargs


@pytest.mark.parametrize("caller_org", [None, ""], ids=["no-org-claim", "empty-org-claim"])
@pytest.mark.parametrize("record_org", ["org-1", "", None])
async def test_caller_without_org_never_gets_content(caller_org: object, record_org: object) -> None:
    # The falsy ids are known orgs here, so the refusal cannot come from the org lookup failing.
    graph = _graph(_record(record_org), known_orgs=("org-1", None, ""))
    user = {"userId": "u1", "role": "admin"}
    if caller_org is not None:
        user["orgId"] = caller_org

    status, _ = await _stream(user, graph)

    assert status == 404
    graph.check_record_access_with_details.assert_not_awaited()


@pytest.mark.parametrize("record_org", ["ORG-1", "org-1 ", " org-1", "org-10", "org-"])
async def test_near_miss_org_ids_do_not_match(record_org: str) -> None:
    # The record's org exists, so the refusal has to come from comparing it with the caller's.
    graph = _graph(_record(record_org), known_orgs=("org-1", record_org))

    status, detail = await _stream({"userId": "u1", "orgId": "org-1", "role": "member"}, graph)

    assert (status, detail) == (404, "Record not found")
    graph.check_record_access_with_details.assert_not_awaited()


@pytest.mark.parametrize(
    ("claims", "record_org", "expected"),
    [
        ({"orgId": "org-1", "userId": "u1"}, "org-1", 200),
        ({"orgId": "org-1", "userId": "u1"}, "org-2", 403),
        ({"orgId": "org-1", "userId": "u1"}, "", 403),
        ({"orgId": "org-1", "userId": "u1"}, None, 403),
        ({"orgId": "org-1"}, "org-1", 403),
        ({"userId": "u1"}, "org-1", 401),
        ({"orgId": "", "userId": "u1"}, "", 401),
    ],
    ids=[
        "same-org", "other-org", "record-org-empty", "record-org-none",
        "token-without-user", "token-without-org", "empty-org-on-both",
    ],
)
async def test_internal_content_route_org_matrix(claims: dict, record_org: object, expected: int) -> None:
    graph = _graph(_record(record_org))

    status, out = await _internal_content(claims, graph)

    assert status == expected
    if expected == 200:
        assert out["is_admin"] is False and out["org_id"] == "org-1"
    else:
        graph.check_record_access_with_details.assert_not_awaited()
