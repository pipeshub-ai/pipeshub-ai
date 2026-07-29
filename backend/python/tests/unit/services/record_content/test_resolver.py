"""Unit tests for RecordContentResolver.

Covers:
- Happy path: graph lookup → authorize → blob strategy → ResolvedRecordContent
- Graph lookup returns None + artifact fallback succeeds
- Graph lookup returns None + no artifact registry → RecordNotFoundError
- Authorizer denies → RecordAccessDeniedError (no fetch called)
- Unsupported record origin → RecordContentError
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.record_content.models import (
    RecordAccessDeniedError,
    RecordContentError,
    RecordNotFoundError,
    ResolvedRecordContent,
)
from app.services.record_content.resolver import RecordContentResolver


def _make_actor(org_id="org1", user_id="user1"):
    actor = MagicMock()
    actor.org_id = org_id
    actor.user_id = user_id
    return actor


def _make_record(record_id="rec1", org_id="org1", origin="UPLOAD", filename="test.txt", ext_id=None):
    r = MagicMock()
    r.id = record_id
    r.org_id = org_id
    r.origin = origin
    r.externalRecordId = ext_id
    r.get = lambda key, default=None: {
        "orgId": org_id,
        "_key": record_id,
        "origin": origin,
        "externalRecordId": ext_id,
        "name": filename,
    }.get(key, default)
    return r


def _make_resolved(record_id="rec1"):
    return ResolvedRecordContent(
        record_id=record_id,
        filename="test.txt",
        mime_type="text/plain",
        size_bytes=4,
        content=b"data",
        version=None,
        source="blob",
    )


@pytest.fixture
def graph():
    g = AsyncMock()
    g.get_record_by_id = AsyncMock(return_value=None)
    g.check_record_access_with_details = AsyncMock(return_value={"ok": True})
    g.get_edge = AsyncMock(return_value={"_id": "e"})
    return g


@pytest.fixture
def config():
    return MagicMock()


@pytest.mark.asyncio
async def test_happy_path_blob(graph, config):
    """Records with UPLOAD origin hit BlobBackedContentStrategy."""
    record = _make_record(origin="UPLOAD")
    graph.get_record_by_id.return_value = record
    resolved = _make_resolved()

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=True),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock(return_value=b"data")),
        patch(
            "app.services.record_content.resolver.build_resolved_content",
            return_value=resolved,
        ),
    ):
        result = await resolver.resolve(
            actor=_make_actor(), ref="rec1", max_bytes=1000
        )

    assert result.record_id == "rec1"
    assert result.content == b"data"


@pytest.mark.asyncio
async def test_not_found_raises(graph, config):
    """Graph returns None and no artifact registry → RecordNotFoundError."""
    graph.get_record_by_id.return_value = None
    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with pytest.raises(RecordNotFoundError):
        await resolver.resolve(actor=_make_actor(), ref="nonexistent", max_bytes=1000)


@pytest.mark.asyncio
async def test_authorizer_deny_no_fetch(graph, config):
    """RecordAccessDeniedError from authorizer → fetch strategy never called."""
    record = _make_record(origin="UPLOAD")
    graph.get_record_by_id.return_value = record

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(
            resolver._authorizer, "authorize",
            new=AsyncMock(side_effect=RecordAccessDeniedError("denied")),
        ),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock()) as mock_fetch,
    ):
        with pytest.raises(RecordAccessDeniedError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)

        mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_origin_raises(graph, config):
    """No strategy supports the record → RecordContentError."""
    record = _make_record(origin="UNKNOWN")
    graph.get_record_by_id.return_value = record

    resolver = RecordContentResolver(graph_provider=graph, config_service=config)

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=False),
        patch.object(resolver._connector_strategy, "supports", return_value=False),
    ):
        with pytest.raises(RecordContentError):
            await resolver.resolve(actor=_make_actor(), ref="rec1", max_bytes=1000)


@pytest.mark.asyncio
async def test_artifact_fallback_used_when_graph_none(graph, config):
    """When graph returns None and artifact_registry is set with conversation_id,
    the artifact registry is consulted."""
    graph.get_record_by_id.side_effect = [None, _make_record(origin="UPLOAD")]
    resolved = _make_resolved()

    mock_registry = MagicMock()
    mock_metadata = MagicMock()
    mock_metadata.artifact_id = "rec1"
    mock_registry.resolve = AsyncMock(return_value=mock_metadata)

    resolver = RecordContentResolver(
        graph_provider=graph, config_service=config, artifact_registry=mock_registry
    )

    with (
        patch.object(resolver._authorizer, "authorize", new=AsyncMock()),
        patch.object(resolver._blob_strategy, "supports", return_value=True),
        patch.object(resolver._blob_strategy, "fetch", new=AsyncMock(return_value=b"data")),
        patch(
            "app.services.record_content.resolver.build_resolved_content",
            return_value=resolved,
        ),
    ):
        result = await resolver.resolve(
            actor=_make_actor(), ref="report.pdf",
            conversation_id="conv1", max_bytes=1000
        )

    mock_registry.resolve.assert_called_once()
    assert result.record_id == "rec1"
