"""Responses that carry stored records leave out the fields the services keep for themselves.

The record endpoint strips `INTERNAL_RECORD_FIELDS` (`api_record`). So do the record update
response (`PUT /api/v1/kb/record/{id}`) and the search response (`POST /api/v1/search`, `records` and
`virtual_to_record_map`). The Node API's search history is covered by
`backend/nodejs/apps/tests/modules/enterprise_search/controller/es_controller.internal-record-fields.test.ts`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.edition_config  # noqa: F401
from app.api.routes.search import SearchQuery, search
from app.connectors.sources.localKB.api import kb_router
from app.connectors.sources.localKB.api.kb_router import (
    UPDATE_RESPONSE_DOCUMENTED_FIELDS,
    update_record,
)
from app.services.graph_db.common.utils import (
    INTERNAL_RECORD_FIELDS,
    api_record,
    api_records,
)
from tests.support.openapi_spec import openapi_spec

DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "failedAt": 1_700_000_100_000, "indexingAttempts": 2, "pageCount": 12}
ALREADY_INTERNAL = {"republishCount": 3, "lastRepublishedAt": 5, "rateLimitedSince": 7, "p_color": "red"}


def _stored(**extra: object) -> dict[str, Any]:
    return {"_key": "rec-1", "id": "rec-1", "recordName": "a.pdf", **DIAGNOSTICS, **ALREADY_INTERNAL, **extra}


def _leaked(value: object) -> list[str]:
    text = json.dumps(value)
    return sorted(name for name in (*DIAGNOSTICS, *ALREADY_INTERNAL) if f'"{name}"' in text)


class TestApiRecords:
    def test_a_list_and_a_map_of_records_are_stripped_and_the_input_is_untouched(self) -> None:
        stored = _stored()
        assert api_records([stored]) == [{"_key": "rec-1", "id": "rec-1", "recordName": "a.pdf"}]
        assert api_records({"v1": stored}) == {"v1": {"_key": "rec-1", "id": "rec-1", "recordName": "a.pdf"}}
        assert "pageCount" in stored

    def test_what_is_not_a_record_passes_through(self) -> None:
        assert api_records([None, "x"]) == [None, "x"]
        assert api_records({"v1": None}) == {"v1": None}


class _Retrieval:
    async def search_with_filters(self, **_: object) -> dict[str, Any]:
        # The records of the list and of the map are the same objects, as the retrieval service builds them.
        doc = _stored()
        return {
            "searchResults": [{"content": "x", "score": 0.9, "virtual_record_id": "v1", "block_index": 0,
                               "metadata": {"recordId": "rec-1", "virtualRecordId": "v1", "id": "rec-1"}}],
            "records": [doc],
            "status": "success",
            "status_code": 200,
            "message": "Query processed successfully. Relevant records retrieved.",
            "virtual_to_record_map": {"v1": doc},
        }


@pytest.mark.asyncio
async def test_the_search_response_carries_no_internal_record_field() -> None:
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1"}
    request.app.container.logger.return_value = MagicMock()

    response = await search(
        request=request,
        body=SearchQuery(query="q", rewriteQuery=False, limit=5),
        retrieval_service=_Retrieval(),
        graph_provider=MagicMock(),
    )
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["records"][0]["recordName"] == "a.pdf"
    assert body["virtual_to_record_map"]["v1"]["recordName"] == "a.pdf"
    assert _leaked(body) == []


@pytest.mark.asyncio
async def test_the_update_response_carries_no_internal_record_field() -> None:
    graph = MagicMock()
    graph._get_kb_context_for_record = AsyncMock(return_value=None)   # the un-enriched answer
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1"}
    request.app.state.graph_provider = graph
    request.app.container.logger.return_value = MagicMock()
    request.json = AsyncMock(return_value={"updates": {"recordName": "b.pdf"}})
    service = MagicMock()
    service.update_record = AsyncMock(return_value={"success": True, "updatedRecord": _stored(recordName="b.pdf")})

    response = await update_record("rec-1", request, kb_service=service, kafka_service=MagicMock())

    assert response["updatedRecord"]["recordName"] == "b.pdf"
    assert _leaked(response) == []


def test_the_node_api_strips_the_same_names() -> None:
    """`internal-record-fields.ts` mirrors `INTERNAL_RECORD_FIELDS`; a name in one list only leaks or over-strips."""
    ts = Path(__file__).resolve().parents[5] / "backend/nodejs/apps/src/modules/enterprise_search/utils/internal-record-fields.ts"
    body = ts.read_text(encoding="utf-8").split("new Set([", 1)[1].split("]);", 1)[0]
    assert set(re.findall(r"'([A-Za-z]+)'", body)) == set(INTERNAL_RECORD_FIELDS)


# -- the date-bounded and timeline answers -----------------------------------------------------------

IN_RANGE_MS = 1_735_689_600_000   # 2025-01-01
OUTSIDE_MS = 1_577_836_800_000    # 2020-01-01


def _dated(key: str, vrid: str, created: int) -> dict[str, Any]:
    return _stored(
        _key=key, id=key, recordName=f"{key}.pdf", virtualRecordId=vrid,
        sourceCreatedAtTimestamp=created, sourceLastModifiedTimestamp=created, recordProps={"a": 1},
    )


class _DatedRetrieval:
    """The bounded search finds `rec-in`; the same search without dates also finds `rec-out`."""

    async def search_with_filters(self, *, time_range: dict | None = None, **_: object) -> dict[str, Any]:
        docs = [_dated("rec-in", "v-in", IN_RANGE_MS)]
        if not time_range:
            docs.append(_dated("rec-out", "v-out", OUTSIDE_MS))
        return {
            "searchResults": [
                {"content": "x", "score": 0.9, "virtual_record_id": d["virtualRecordId"], "block_index": 0,
                 "metadata": {"recordId": d["_key"], "virtualRecordId": d["virtualRecordId"]}}
                for d in docs
            ],
            "records": docs,
            "virtual_to_record_map": {d["virtualRecordId"]: d for d in docs},
            "status": "success", "status_code": 200, "message": "ok",
        }


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1"}
    request.app.container.logger.return_value = MagicMock()
    return request


@pytest.mark.asyncio
async def test_the_best_matches_outside_the_dates_carry_no_internal_record_field() -> None:
    body = SearchQuery(
        query="q", rewriteQuery=False, limit=5, createdAfter="2024-12-01", createdBefore="2025-01-15", timeline="off",
    )
    response = await search(request=_request(), body=body, retrieval_service=_DatedRetrieval(), graph_provider=MagicMock())
    out = json.loads(response.body)

    assert out["outsideDates"]["records"], sorted(out)   # the case reaches the block
    assert out["outsideDates"]["records"][0]["recordName"] == "rec-out.pdf"
    assert _leaked(out) == []


@pytest.mark.asyncio
async def test_a_timeline_search_carries_no_internal_record_field() -> None:
    body = SearchQuery(query="q", rewriteQuery=False, createdAfter="2024-01-01", createdBefore="2025-06-30", timeline="created")
    response = await search(request=_request(), body=body, retrieval_service=_DatedRetrieval(), graph_provider=MagicMock())
    out = json.loads(response.body)

    assert out.get("records"), sorted(out)
    assert _leaked(out) == []


# -- the record update response ----------------------------------------------------------------------

def _update_request(graph: MagicMock) -> MagicMock:
    request = _request()
    request.app.state.graph_provider = graph
    request.json = AsyncMock(return_value={"updates": {"recordName": "b.pdf"}})
    return request


def _service(updated: dict[str, Any]) -> MagicMock:
    service = MagicMock()
    service.update_record = AsyncMock(return_value={"success": True, "updatedRecord": updated})
    return service


TRASH = {"isDeleted": True, "deletedAtTimestamp": 1_790_000_000_000, "deleteBatchId": "b-1", "deleteSource": "USER"}


@pytest.mark.asyncio
async def test_the_update_of_a_trashed_record_keeps_the_trash_fields_the_spec_documents() -> None:
    graph = MagicMock()
    graph._get_kb_context_for_record = AsyncMock(return_value=None)
    response = await update_record(
        "rec-1", _update_request(graph), kb_service=_service(_stored(recordName="b.pdf", **TRASH)), kafka_service=MagicMock(),
    )
    record = response["updatedRecord"]

    assert {name: record[name] for name in UPDATE_RESPONSE_DOCUMENTED_FIELDS} == {
        name: TRASH[name] for name in UPDATE_RESPONSE_DOCUMENTED_FIELDS
    }
    assert _leaked(record) == []


@pytest.mark.asyncio
async def test_the_enriched_update_response_carries_no_internal_record_field(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = MagicMock()
    graph._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1", "kb_name": "KB"})
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "u1"})
    graph.get_user_kb_permission = AsyncMock(return_value="OWNER")
    graph.get_knowledge_base = AsyncMock(return_value={"id": "kb-1"})
    graph.get_knowledge_hub_parent_node = AsyncMock(return_value=None)
    monkeypatch.setattr(kb_router, "_keep_update_inside_connection", AsyncMock(return_value=None))

    response = await update_record(
        "rec-1", _update_request(graph), kb_service=_service(_stored(recordName="b.pdf")), kafka_service=MagicMock(),
    )

    assert response["location"] == "kb_root"   # the enriched branch ran
    assert _leaked(response) == []


def test_the_update_response_keeps_exactly_the_internal_names_the_spec_documents() -> None:
    """Nothing the public spec documents may disappear, and nothing undocumented stays."""
    record = openapi_spec()["components"]["schemas"]["UpdateRecordResponseSchema"]["properties"]["record"]
    documented: set[str] = set(record.get("properties", {}))
    for part in record.get("allOf", []):
        documented |= set(part.get("properties", {}))
    assert documented & INTERNAL_RECORD_FIELDS == UPDATE_RESPONSE_DOCUMENTED_FIELDS


@pytest.mark.parametrize(
    ("key", "dropped"),
    [("p_x", True), ("p_" + "a" * 64, True), ("p_" + "a" * 65, False), ("p_", False), ("P_x", False),
     ("p_x-y", False), ("pageCount", True), ("pageCountX", False), ("recordName", False)],
)
def test_api_record_key_edges(key: str, dropped: bool) -> None:
    shown = api_record({key: 1, "_key": "k"})
    assert (key not in shown) is dropped
    assert "_key" in shown


def test_api_record_keeps_only_the_names_it_is_told_to() -> None:
    shown = api_record({"failedAt": 1, "deleteSource": "USER", "republishCount": 2}, keep={"deleteSource"})
    assert shown == {"deleteSource": "USER"}
