"""P2.13 MD-3: the record routes (stream/download, which a citation opens; details; content) for a record the user
cannot read themselves: answer_only → a typed 403; full → the stream is served; nothing → today's denial; flag off
→ today's denial without a publication read."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router
from tests.support.record_access import InMemoryRecordAccess

DENIED = HTTPException(status_code=403, detail="You do not have permission to access this record")


def _publications(enabled: bool, decision: str) -> MagicMock:
    service = MagicMock()
    service.enabled = AsyncMock(return_value=enabled)
    service.read_decision = AsyncMock(return_value=decision)
    return service


async def _gate(service: MagicMock, *, allow_full: bool) -> None:
    with patch.object(router, "KnowledgePublications", return_value=service):
        await router._publication_raw_read(
            MagicMock(), MagicMock(), "o1", "u1", "r1", allow_full=allow_full, denied=DENIED
        )


async def test_md3_answer_only_is_a_typed_refusal() -> None:
    for allow_full in (True, False):
        with pytest.raises(HTTPException) as refused:
            await _gate(_publications(True, "published_answer_only"), allow_full=allow_full)
        assert refused.value.status_code == 403
        assert refused.value.detail["code"] == "publication_answer_only"


async def test_md3_full_opens_the_stream_only() -> None:
    await _gate(_publications(True, "published_full"), allow_full=True)
    with pytest.raises(HTTPException) as denied:
        await _gate(_publications(True, "published_full"), allow_full=False)
    assert denied.value is DENIED


async def test_md3_nothing_published_is_todays_denial() -> None:
    with pytest.raises(HTTPException) as denied:
        await _gate(_publications(True, "denied"), allow_full=True)
    assert denied.value is DENIED


async def test_md3_flag_off_reads_no_publication() -> None:
    service = _publications(False, "published_full")
    with pytest.raises(HTTPException) as denied:
        await _gate(service, allow_full=True)
    assert denied.value is DENIED
    service.read_decision.assert_not_awaited()


def _request(user: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


async def test_md3_the_stream_route_asks_the_publication_gate_when_the_user_has_no_access() -> None:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"_key": "o1"})
    record = MagicMock(org_id="o1", connector_id="c1")
    graph.get_record_by_id = AsyncMock(return_value=record)
    InMemoryRecordAccess().user("u1", "o1").record("r1", "o1").serve(graph)
    service = _publications(True, "published_answer_only")
    with patch.object(router, "KnowledgePublications", return_value=service), pytest.raises(HTTPException) as refused:
        await router.stream_record(
            _request({"orgId": "o1", "userId": "u1"}), "r1", convertTo=None, version=None,
            graph_provider=graph, config_service=MagicMock(),
        )
    assert refused.value.detail["code"] == "publication_answer_only"
    service.read_decision.assert_awaited_once_with("u1", "o1", "r1", own=False)


def _route_request() -> MagicMock:
    request = _request({"orgId": "o1", "userId": "u1"})
    request.app.container.logger.return_value = MagicMock()
    request.app.container.config_service.return_value = MagicMock()
    return request


def _no_access_graph() -> MagicMock:
    graph = MagicMock()
    InMemoryRecordAccess().user("u1", "o1").record("r1", "o1").serve(graph)
    graph.get_document = AsyncMock(return_value={
        "_key": "r1", "_id": "records/r1", "_rev": "x", "orgId": "o1", "recordName": "policy.pdf",
        "recordType": "FILE", "connectorId": "c1",
    })
    return graph


async def test_pf5_the_details_route_serves_a_full_publication_s_record() -> None:
    service = _publications(True, "published_full")
    with patch.object(router, "KnowledgePublications", return_value=service), patch(
        "app.modules.demo_data.access.is_hidden_demo_record", AsyncMock(return_value=False)
    ):
        details = await router.get_record_by_id("r1", _route_request(), graph_provider=_no_access_graph())
    assert details is not None
    assert details["record"]["id"] == "r1" and "_id" not in details["record"] and "_rev" not in details["record"]
    assert (details["knowledgeBase"], details["folder"], details["metadata"]) == (None, None, None)
    assert details["permissions"] == [
        {"id": "r1", "name": "policy.pdf", "type": "FILE", "relationship": "READER", "accessType": "PUBLICATION"}
    ]


@pytest.mark.parametrize(("decision", "status"), [("published_answer_only", 403), ("denied", 404)])
async def test_pf5_the_details_route_refuses_answer_only_typed_and_nothing_as_today(decision: str, status: int) -> None:
    with patch.object(router, "KnowledgePublications", return_value=_publications(True, decision)), pytest.raises(
        HTTPException
    ) as refused:
        await router.get_record_by_id("r1", _route_request(), graph_provider=_no_access_graph())
    assert refused.value.status_code == status
    if decision == "published_answer_only":
        assert refused.value.detail["code"] == "publication_answer_only"


async def test_pf6_the_content_route_serves_a_full_publication_s_record() -> None:
    service = _publications(True, "published_full")
    fetched = AsyncMock(return_value={"ok": True, "records": [{"id": "r1"}]})
    with patch.object(router, "KnowledgePublications", return_value=service), patch(
        "app.modules.demo_data.access.is_hidden_demo_record", AsyncMock(return_value=False)
    ), patch.object(router, "_fetch_multiple_records_impl", fetched), patch.object(
        router, "record_to_text", return_value="the text"
    ):
        answer = await router.get_record_content("r1", _route_request(), graph_provider=_no_access_graph())
    assert answer == {"content": "the text"}


@pytest.mark.parametrize(("decision", "status"), [("published_answer_only", 403), ("denied", 403)])
async def test_pf6_the_content_route_refuses_answer_only_typed_and_nothing_as_today(decision: str, status: int) -> None:
    fetched = AsyncMock(side_effect=AssertionError("nothing is read"))
    with patch.object(router, "KnowledgePublications", return_value=_publications(True, decision)), patch.object(
        router, "_fetch_multiple_records_impl", fetched
    ), pytest.raises(HTTPException) as refused:
        await router.get_record_content("r1", _route_request(), graph_provider=_no_access_graph())
    assert refused.value.status_code == status
    if decision == "published_answer_only":
        assert refused.value.detail["code"] == "publication_answer_only"
