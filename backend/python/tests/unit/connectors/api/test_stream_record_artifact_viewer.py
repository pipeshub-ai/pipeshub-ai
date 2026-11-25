"""`GET /api/v1/stream/record/{id}` on an artifact (SS0, AV-11, AV-12).

Sharing a conversation used to give every reader a READER edge on every
artifact in it, and this route served the bytes to any edge holder. An artifact
now streams to its owner, or to a reader whose access to its sources is
established by its provenance; a READER edge alone is not enough.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from app.config.constants.arangodb import Connectors
from app.models.entities import RecordType
from tests.support.record_access import InMemoryRecordAccess

pytestmark = pytest.mark.asyncio


def _record(record_type: RecordType = RecordType.ARTIFACT) -> SimpleNamespace:
    return SimpleNamespace(
        id="art-1",
        org_id="org-1",
        record_name="report.pdf",
        record_type=record_type,
        mime_type="application/pdf",
        connector_name=Connectors.CODING_SANDBOX if record_type == RecordType.ARTIFACT else Connectors.GOOGLE_DRIVE,
        connector_id="conn-1",
        external_record_id="ext-1",
    )


def _request(user_id: str) -> MagicMock:
    req = MagicMock()
    user = {"userId": user_id, "orgId": "org-1", "role": "member"}
    req.state.user.get = lambda key, default=None: user.get(key, default)
    return req


def _graph(record: SimpleNamespace, edges: dict[tuple[str, str], str]) -> AsyncMock:
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"_key": "org-1"})
    gp.get_record_by_id = AsyncMock(return_value=record)
    # Every org member passes the record check, so only the artifact's own rule can refuse the viewer.
    access = InMemoryRecordAccess().user("owner", "org-1").user("viewer", "org-1").record(record.id, "org-1")
    access.grant(record.id, org="org-1").serve(gp)
    gp.get_user_by_user_id = AsyncMock(side_effect=lambda user_id: {"_key": f"key-{user_id}"})

    async def get_edge(**kw: str) -> dict[str, str] | None:
        role = edges.get((kw["from_id"], kw["to_id"]))
        return {"role": role} if role else None

    gp.get_edge = AsyncMock(side_effect=get_edge)
    gp.get_typed_records_batch = AsyncMock(side_effect=lambda ids: {rid: record for rid in ids if rid == record.id})
    return gp


async def _stream(user_id: str, gp: AsyncMock) -> Any:  # noqa: ANN401 - the route's response
    from app.connectors.api.router import stream_record

    with patch(
        "app.connectors.api.router._resolve_record_content_response",
        AsyncMock(return_value=Response(content=b"bytes")),
    ), patch("app.modules.demo_data.access.is_hidden_demo_record", AsyncMock(return_value=False)):
        return await stream_record(
            _request(user_id), "art-1", convertTo=None, version=None, graph_provider=gp, config_service=AsyncMock()
        )


async def test_a_reader_edge_alone_does_not_stream_an_artifact() -> None:
    """AV-11: before SS0 this answered 200 with the bytes."""
    gp = _graph(_record(), {("key-owner", "art-1"): "OWNER", ("key-viewer", "art-1"): "READER"})

    with pytest.raises(HTTPException) as refused:
        await _stream("viewer", gp)

    assert refused.value.status_code == 403


async def test_the_owner_streams_their_artifact() -> None:
    """AV-12."""
    gp = _graph(_record(), {("key-owner", "art-1"): "OWNER"})
    assert (await _stream("owner", gp)).body == b"bytes"


async def test_a_reader_streams_when_provenance_allows() -> None:
    """AV-12: provenance known and every source readable by the reader."""
    from app.services.artifact_registry.viewer_access import ArtifactViewerAccess

    class Provenance:
        async def sources_of(self, org_id: str, artifact_ids: list[str]) -> dict[str, frozenset[str]]:
            return dict.fromkeys(artifact_ids, frozenset())

    gp = _graph(_record(), {("key-viewer", "art-1"): "READER"})
    with patch(
        "app.connectors.api.router.artifact_viewer_access",
        lambda graph: ArtifactViewerAccess(graph, Provenance()),
    ):
        assert (await _stream("viewer", gp)).body == b"bytes"


async def test_a_record_that_is_not_an_artifact_is_unchanged() -> None:
    """AV-12: no provenance check for connector records."""
    gp = _graph(_record(RecordType.FILE), {})
    assert (await _stream("viewer", gp)).body == b"bytes"
    gp.get_edge.assert_not_awaited()
