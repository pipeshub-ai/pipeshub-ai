"""A placeholder record is a navigation stub: no content route serves it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.responses import Response

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import Connectors
from app.connectors.api import router
from app.models.entities import RecordType, WebpageRecord


def _page(*, placeholder: bool) -> WebpageRecord:
    return WebpageRecord(
        org_id="org-1", external_record_id="12345", record_name="Page", origin="CONNECTOR",
        connector_name=Connectors.CONFLUENCE, connector_id="conn-1",
        record_type=RecordType.CONFLUENCE_PAGE, version=0, mime_type="text/html",
        source_created_at=1, source_updated_at=1, is_placeholder=placeholder,
    )


def _wire(monkeypatch) -> tuple[MagicMock, MagicMock, SimpleNamespace]:
    connector = MagicMock()
    connector.stream_record = AsyncMock(return_value=Response(b"body"))
    monkeypatch.setattr(router, "_get_streaming_connector", AsyncMock(return_value=connector))
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"_key": "conn-1", "isActive": True})
    request = SimpleNamespace(
        app=SimpleNamespace(container=MagicMock(), state=SimpleNamespace(connector_registry=MagicMock()))
    )
    return connector, graph, request


async def _resolve(
    record: WebpageRecord, graph: MagicMock, request: SimpleNamespace,
    convert_to: str | None = None, is_admin: bool = False,
) -> Response:
    return await router._resolve_record_content_response(
        record=record, org_id="org-1", user_id="u", is_admin=is_admin, convert_to=convert_to,
        version=None, request=request, config_service=MagicMock(), graph_provider=graph,
    )


async def test_real_record_still_streams(monkeypatch) -> None:
    connector, graph, request = _wire(monkeypatch)
    out = await _resolve(_page(placeholder=False), graph, request)
    assert out.body == b"body"
    connector.stream_record.assert_awaited_once()


# is_admin / convert_to vary the callers: stream_record, download_file, the agent content route.
@pytest.mark.parametrize("convert_to,is_admin", [(None, False), (None, True), ("application/pdf", False)])
async def test_placeholder_is_refused_before_the_connector(monkeypatch, convert_to, is_admin) -> None:
    connector, graph, request = _wire(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await _resolve(_page(placeholder=True), graph, request, convert_to, is_admin)
    assert exc.value.status_code == 422
    connector.stream_record.assert_not_awaited()


@pytest.mark.parametrize("placeholder,refused", [(True, True), (False, False)])
async def test_internal_stream_refuses_a_placeholder(monkeypatch, placeholder: bool, refused: bool) -> None:
    connector, graph, request = _wire(monkeypatch)
    record = _page(placeholder=placeholder)
    graph.get_record_by_id = AsyncMock(return_value=record)
    monkeypatch.setattr(router, "get_org_or_503", AsyncMock(return_value={"_key": "org-1"}))
    invoke = AsyncMock(return_value=Response(b"body"))
    monkeypatch.setattr(router, "_invoke_connector_stream", invoke)
    call = router.stream_record_internal(
        request=request, record_id="r1", graph_provider=graph, config_service=MagicMock(),
        claims={"orgId": "org-1", "userId": "u"},
    )
    if refused:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 422
        invoke.assert_not_awaited()
    else:
        assert (await call).body == b"body"
