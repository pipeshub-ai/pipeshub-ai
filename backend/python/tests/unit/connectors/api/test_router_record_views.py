"""The connector routes ask the installed record view decorator where a record's content comes from and how it is shown, and show
every record as stored when none is installed."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from app.connectors.api.router import stream_record_internal
from app.connectors.record_views import (
    DefaultRecordViewDecorator,
    StoredDocument,
    set_decorator,
)
from app.modules.indexing.extension import RecordOwnerUnknown
from tests.unit.connectors.api.test_router_deep1 import (
    _ROUTER,
    TestStreamRecordInternalEdgeCases,
)

CLAIMS = TestStreamRecordInternalEdgeCases._CLAIMS


class Stored(DefaultRecordViewDecorator):
    def __init__(self, source: StoredDocument | None = None, *, raises: bool = False) -> None:
        self.source, self.raises, self.asked = source, raises, []

    async def stream_source(self, record: object, graph: object) -> StoredDocument | None:
        self.asked.append(record)
        if self.raises:
            raise RecordOwnerUnknown("owner not readable")
        return self.source


async def test_a_record_the_decorator_names_a_stored_document_for_is_streamed_from_storage_not_from_its_connector() -> None:
    decorator = Stored(StoredDocument("doc-9", "application/pdf"))
    set_decorator(decorator)
    helper = TestStreamRecordInternalEdgeCases()
    req, record, config_service, graph_provider = helper._setup()
    called: list[str] = []

    async def api_call(route: str, token: str) -> dict:
        called.append(route)
        return {"data": b"stored-bytes"}

    with (
        patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="t"),
        patch(f"{_ROUTER}.make_api_call", side_effect=api_call),
    ):
        result = await stream_record_internal(req, "rec-1", graph_provider, config_service, claims=CLAIMS)

    assert isinstance(result, Response)
    assert result.body == b"stored-bytes"
    assert result.media_type == "application/pdf"
    assert called == ["http://storage:8080/api/v1/document/internal/doc-9/buffer"]
    req.app.container.connectors_map["conn-1"].stream_record.assert_not_awaited()
    assert decorator.asked == [record]


async def test_a_record_whose_owner_cannot_be_read_is_a_404_not_a_server_error() -> None:
    set_decorator(Stored(raises=True))
    req, _record, config_service, graph_provider = TestStreamRecordInternalEdgeCases()._setup()

    with pytest.raises(HTTPException) as err:
        await stream_record_internal(req, "rec-1", graph_provider, config_service, claims=CLAIMS)

    assert err.value.status_code == 404
    req.app.container.connectors_map["conn-1"].stream_record.assert_not_awaited()


async def test_with_the_default_decorator_the_connector_streams_the_record_as_before() -> None:
    req, record, config_service, graph_provider = TestStreamRecordInternalEdgeCases()._setup()

    await stream_record_internal(req, "rec-1", graph_provider, config_service, claims=CLAIMS)

    req.app.container.connectors_map["conn-1"].stream_record.assert_awaited_once()


async def test_connector_stats_pass_through_the_decorator() -> None:
    from app.connectors.api import router

    class Stats(DefaultRecordViewDecorator):
        async def decorate_stats(self, stats: dict, graph: object, *, surface: str) -> dict:
            return {**stats, "surface": surface}

    set_decorator(Stats())
    graph = MagicMock()
    graph.get_connector_stats = AsyncMock(return_value={"success": True, "data": {"total": 3}})
    request = MagicMock()
    request.state.user = {"orgId": "o1"}

    with patch.object(router, "authorize_connector_stats", AsyncMock()):
        result = await router.get_connector_stats_endpoint(request, "c1", graph)

    assert result == {"success": True, "data": {"total": 3, "surface": "connector_stats"}}
