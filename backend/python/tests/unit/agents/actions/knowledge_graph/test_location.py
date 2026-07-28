"""Phase 7 verification tests — RecordLocation breadcrumbs.

Covers:
1. RecordLocation.render() for KB and nested Confluence records.
2. Record.to_llm_context() emits Location when set.
3. resolve_locations() calls the provider exactly once per batch (no N+1).
4. Empty / missing records are tolerated gracefully.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.location import (
    RecordLocation,
    resolve_locations,
)
from app.models.entities import Connectors, OriginTypes, Record, RecordType


# ---------------------------------------------------------------------------
# RecordLocation.render()
# ---------------------------------------------------------------------------


class TestRecordLocationRender:
    def test_empty_location_renders_empty_string(self) -> None:
        loc = RecordLocation(path=(), parent_id=None, truncated=False)
        assert loc.render() == ""

    def test_kb_record_location(self) -> None:
        """A KB record: path shows the KB name, parent_id for navigate()."""
        loc = RecordLocation(
            path=("Engineering KB",),
            parent_id="aabbccdd-1111-2222-3333-aabbccdd0001",
            truncated=False,
        )
        rendered = loc.render()
        assert "Engineering KB" in rendered
        assert "aabbccdd" in rendered

    def test_nested_confluence_record_location(self) -> None:
        """A nested Confluence page: path has multiple levels."""
        loc = RecordLocation(
            path=("Confluence Space", "Runbooks", "Networking"),
            parent_id="page-parent-id-xyz",
            truncated=False,
        )
        rendered = loc.render()
        assert "Confluence Space" in rendered
        assert "Runbooks" in rendered
        assert "Networking" in rendered
        assert "page-par" in rendered  # first 8 chars of parent_id
        assert " > " in rendered

    def test_truncated_path_shows_ellipsis(self) -> None:
        loc = RecordLocation(path=("Level1",), parent_id="pid", truncated=True)
        rendered = loc.render()
        assert "…" in rendered

    def test_parent_id_shortened(self) -> None:
        loc = RecordLocation(path=(), parent_id="abcdefgh12345678", truncated=False)
        rendered = loc.render()
        assert "abcdefgh" in rendered
        assert "12345678" not in rendered  # only first 8 chars shown


# ---------------------------------------------------------------------------
# Record.to_llm_context() emits Location
# ---------------------------------------------------------------------------


def _make_record(**kwargs: Any) -> Record:
    defaults = {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "Test Record",
        "record_type": RecordType.FILE,
        "external_record_id": "ext-1",
        "version": 1,
        "origin": OriginTypes.UPLOAD,
        "connector_name": Connectors.KNOWLEDGE_BASE,
        "connector_id": "conn-1",
    }
    defaults.update(kwargs)
    return Record(**defaults)


class TestRecordToLlmContextWithLocation:
    def test_location_present_in_context_when_set(self) -> None:
        rec = _make_record(location="Engineering KB > Runbooks (parent id: aabbccdd)")
        ctx = rec.to_llm_context()
        assert "Location" in ctx
        assert "Engineering KB > Runbooks" in ctx

    def test_location_absent_when_none(self) -> None:
        rec = _make_record(location=None)
        ctx = rec.to_llm_context()
        assert "Location" not in ctx

    def test_location_line_between_connector_id_and_mime_type(self) -> None:
        """Location line appears right after connector fields, before MIME type."""
        rec = _make_record(
            location="KB > Space",
            mime_type="application/pdf",
        )
        ctx = rec.to_llm_context()
        loc_pos = ctx.index("Location")
        mime_pos = ctx.index("MIME Type")
        assert loc_pos < mime_pos

    def test_file_record_subclass_preserves_location(self) -> None:
        """FileRecord.to_llm_context() calls super() — location must still appear
        for JIRA/Confluence attachments which are typed as FILE."""
        from app.models.entities import FileRecord

        rec = FileRecord(
            id="file-1",
            org_id="org-1",
            record_name="earnings.pdf",
            record_type=RecordType.FILE,
            external_record_id="attachment_1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA,
            connector_id="conn-1",
            mime_type="application/pdf",
            is_file=True,
            extension="pdf",
            location="[PA-1194] Parent ticket (parent id: ticket-p)",
        )
        ctx = rec.to_llm_context()
        assert "Location        : [PA-1194] Parent ticket (parent id: ticket-p)" in ctx
        assert "MIME Type" in ctx
        # Location must appear before MIME Type (same position as base Record)
        assert ctx.index("Location") < ctx.index("MIME Type")


# ---------------------------------------------------------------------------
# resolve_locations() — exactly one batched provider call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_locations_one_round_trip() -> None:
    """Provider.get_record_locations is called ONCE for the whole batch,
    not once per record (no N+1)."""
    mock_provider = MagicMock()
    mock_provider.get_record_locations = AsyncMock(return_value={
        "r1": {"path": ["KB"], "parentId": "p1", "truncated": False},
        "r2": {"path": ["Space", "Page"], "parentId": "p2", "truncated": False},
    })

    cache: dict = {}
    locs = await resolve_locations(
        ["r1", "r2"],
        graph_provider=mock_provider,
        org_id="org-test",
        cache=cache,
    )

    mock_provider.get_record_locations.assert_called_once()
    assert "r1" in locs
    assert "r2" in locs
    assert locs["r1"].path == ("KB",)
    assert locs["r2"].path == ("Space", "Page")


@pytest.mark.asyncio
async def test_resolve_locations_cache_prevents_second_call() -> None:
    """When all ids are already in cache, the provider is never called."""
    mock_provider = MagicMock()
    mock_provider.get_record_locations = AsyncMock()

    prefilled_cache: dict = {
        "r1": RecordLocation(path=("KB",), parent_id="p1", truncated=False),
    }
    await resolve_locations(
        ["r1"],
        graph_provider=mock_provider,
        org_id="org-test",
        cache=prefilled_cache,
    )

    mock_provider.get_record_locations.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_locations_empty_ids_returns_empty() -> None:
    mock_provider = MagicMock()
    mock_provider.get_record_locations = AsyncMock()

    result = await resolve_locations(
        [],
        graph_provider=mock_provider,
        org_id="org-test",
        cache={},
    )
    assert result == {}
    mock_provider.get_record_locations.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_locations_provider_error_tolerated() -> None:
    """If the provider raises, resolve_locations returns empty dicts for unknowns."""
    mock_provider = MagicMock()
    mock_provider.get_record_locations = AsyncMock(side_effect=RuntimeError("DB down"))

    cache: dict = {}
    result = await resolve_locations(
        ["r1", "r2"],
        graph_provider=mock_provider,
        org_id="org-test",
        cache=cache,
    )
    assert "r1" in result
    assert result["r1"].render() == ""  # empty location on error
