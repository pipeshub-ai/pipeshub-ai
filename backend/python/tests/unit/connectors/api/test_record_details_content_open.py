"""The record-details route, when it is the first call of a content open (``X-Record-Content-Open: 1``).

It then applies the stream route's connector refusals, loaded connector or not, so a cached copy is never
shown for a record the stream would refuse. Without the header the route is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import CollectionNames
from app.connectors.api import router as router_mod
from tests.support.record_access import InMemoryRecordAccess

RECORD_ID = "rec-1"
ACTIVE = {"_key": "conn-1", "type": "SLACK", "isActive": True, "name": "Slack"}


def _doc(**over: object) -> dict[str, object]:
    return {"_key": RECORD_ID, "origin": "CONNECTOR", "recordType": "FILE", "connectorName": "SLACK", "connectorId": "conn-1", **over}


def _graph(doc: dict[str, Any], app_doc: dict[str, Any] | None) -> MagicMock:
    async def get_document(key: str, collection: str, *_a: object, **_k: object) -> dict[str, Any] | None:
        return doc if collection == CollectionNames.RECORDS.value else app_doc

    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=get_document)
    return InMemoryRecordAccess().readable("u1", "org-1", RECORD_ID).serve(graph)


async def _details(graph: MagicMock, header: str | None) -> dict[str, Any] | None:
    container = SimpleNamespace(logger=lambda: MagicMock(), config_service=lambda: MagicMock(), connectors_map={"conn-1": object()})
    request = SimpleNamespace(
        state=SimpleNamespace(user={"userId": "u1", "orgId": "org-1"}),
        app=SimpleNamespace(container=container),
        headers={},
    )
    with patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock()), \
            patch.object(router_mod, "refuse_outside_connection", new=AsyncMock()), \
            patch.object(router_mod, "hide_record_parents_outside_connection", new=AsyncMock()), \
            patch.object(router_mod, "hide_source_paths"), \
            patch.object(router_mod, "_stage_states_of", new=AsyncMock(return_value=[])):
        return await router_mod.get_record_by_id(RECORD_ID, request, x_record_content_open=header, graph_provider=graph)


async def test_a_deleted_connector_is_404_for_a_content_open() -> None:
    with pytest.raises(HTTPException) as exc:
        await _details(_graph(_doc(), None), "1")
    assert exc.value.status_code == 404
    assert "no longer exists" in exc.value.detail


async def test_a_disabled_connector_is_409_even_when_it_is_in_memory() -> None:
    disabled = {**ACTIVE, "isActive": False}
    with pytest.raises(HTTPException) as exc:
        await _details(_graph(_doc(), disabled), "1")
    assert exc.value.status_code == 409
    assert "currently disabled" in exc.value.detail


async def test_without_the_header_the_answer_is_today_s() -> None:
    for app_doc in (None, {**ACTIVE, "isActive": False}):
        out = await _details(_graph(_doc(), app_doc), None)
        assert out["record"]["id"] == RECORD_ID


async def test_an_active_connector_answers_with_details() -> None:
    out = await _details(_graph(_doc(), ACTIVE), "1")
    assert out["record"]["id"] == RECORD_ID


@pytest.mark.parametrize(
    "doc",
    [
        _doc(origin="UPLOAD", connectorName="KB"),
        _doc(recordType="ARTIFACT"),
        _doc(connectorName="ATTACHMENTS"),
        _doc(connectorName="CODING_SANDBOX"),
    ],
    ids=["storage-backed", "artifact", "attachments", "coding-sandbox"],
)
async def test_storage_backed_and_artifact_records_are_unaffected(doc) -> None:
    out = await _details(_graph(doc, None), "1")
    assert out["record"]["id"] == RECORD_ID


async def test_a_header_with_another_value_is_not_the_intent() -> None:
    out = await _details(_graph(_doc(), None), "0")
    assert out["record"]["id"] == RECORD_ID


async def test_access_refusal_is_unchanged() -> None:
    graph = _graph(_doc(), ACTIVE)
    InMemoryRecordAccess().user("u1", "org-1").record(RECORD_ID, "org-1").serve(graph)
    with pytest.raises(HTTPException) as exc:
        await _details(graph, "1")
    assert exc.value.status_code == 404


def test_both_routes_use_the_one_helper() -> None:
    import inspect

    assert "refuse_connector_state" in inspect.getsource(router_mod._get_streaming_connector)
    assert "refuse_connector_state" in inspect.getsource(router_mod._refuse_connector_state_for_open)
    assert "refuse_connector_state" in inspect.getsource(router_mod._resolve_record_content_response)
