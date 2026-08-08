"""Tests for ``app.agents.actions.knowledge_graph.ops.entity_records`` — the
progressive ``find_records_by_entity`` tool.

The tool uses a two-step approach: (1) INBOUND graph traversal from the
entity to find connected record keys, (2) permission-check via
``get_accessible_virtual_record_ids`` WITHOUT metadata filters, then
intersection. Tests verify both steps and their interaction.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops.entity_records import (
    execute_find_records_by_entity,
)


def _graph_provider(
    *,
    entity_doc: dict | None = None,
    connected_records: list[dict] | None = None,
    accessible_map: dict | None = None,
    fetched_records: list[dict] | None = None,
) -> AsyncMock:
    """Pre-wired mock matching the two-step call pattern."""
    gp = AsyncMock()
    gp.get_document.return_value = entity_doc
    gp.get_related_nodes.return_value = connected_records or []
    gp.get_accessible_virtual_record_ids.return_value = accessible_map or {}
    gp.get_records_by_record_ids.return_value = fetched_records or []
    return gp


class TestExecuteFindRecordsByEntityGuards:
    @pytest.mark.asyncio
    async def test_no_state(self) -> None:
        ok, text = await execute_find_records_by_entity(None, "e1", "department")
        assert ok is False
        assert "not initialized" in text

    @pytest.mark.asyncio
    async def test_missing_entity_id(self) -> None:
        ok, text = await execute_find_records_by_entity({"org_id": "o1"}, None, "department")
        assert ok is False
        assert "required" in text

    @pytest.mark.asyncio
    async def test_missing_entity_type(self) -> None:
        ok, text = await execute_find_records_by_entity({"org_id": "o1"}, "e1", None)
        assert ok is False
        assert "required" in text

    @pytest.mark.asyncio
    async def test_unsupported_entity_type_rejected(self) -> None:
        ok, text = await execute_find_records_by_entity({"org_id": "o1"}, "p1", "person")
        assert ok is False
        assert "person" in text
        assert "informational only" in text

    @pytest.mark.asyncio
    async def test_unknown_entity_type_rejected(self) -> None:
        ok, text = await execute_find_records_by_entity({"org_id": "o1"}, "x1", "bogus")
        assert ok is False
        assert "bogus" in text

    @pytest.mark.asyncio
    async def test_no_graph_provider(self) -> None:
        state: dict = {"graph_provider": None, "org_id": "o1"}
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is False
        assert "Graph provider" in text

    @pytest.mark.asyncio
    async def test_entity_not_found(self) -> None:
        gp = _graph_provider(entity_doc=None)
        state = {"graph_provider": gp, "org_id": "o1", "user_id": "u1"}
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is False
        assert "not found" in text


class TestExecuteFindRecordsByEntityHappyPath:
    def _state(self, gp: AsyncMock) -> dict:
        return {"graph_provider": gp, "org_id": "o1", "user_id": "u1"}

    @pytest.mark.asyncio
    async def test_resolves_entity_name_via_graph_lookup_when_not_cached(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[],  # no connected records
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        assert "Legal" in text
        gp.get_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_cached_entity_name_without_graph_lookup(self) -> None:
        gp = _graph_provider(connected_records=[])
        state = self._state(gp)
        state["_kg_entity_id_filter_key"] = {"d1": ("departments", "Legal")}
        ok, _text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        gp.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cached_entry_ignored_when_filter_key_mismatches_entity_type(self) -> None:
        gp = _graph_provider(
            entity_doc={"name": "Billing"},
            connected_records=[],
        )
        state = self._state(gp)
        state["_kg_entity_id_filter_key"] = {"d1": ("topics", "Something Else")}
        ok, _text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        gp.get_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_connected_records_from_graph(self) -> None:
        """If the INBOUND traversal returns nothing, no permission check is
        needed — short-circuit with a clear message."""
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        assert "No records found" in text
        gp.get_accessible_virtual_record_ids.assert_not_awaited()
        gp.get_records_by_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connected_but_not_accessible(self) -> None:
        """Records linked via graph edges but not in the user's accessible
        set should be filtered out."""
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}, {"_key": "r2"}],
            accessible_map={"v99": "r99"},  # different records
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        assert "No accessible records" in text
        gp.get_records_by_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_inbound_traversal_uses_correct_edge_and_direction(self) -> None:
        gp = _graph_provider(
            entity_doc={"name": "Billing"},
            connected_records=[{"_key": "r1"}],
            accessible_map={"v1": "r1"},
            fetched_records=[{"_key": "r1", "recordName": "Invoice", "recordType": "FILE"}],
        )
        state = self._state(gp)
        ok, _text = await execute_find_records_by_entity(state, "t1", "topic")
        assert ok is True
        gp.get_related_nodes.assert_awaited_once()
        call_args = gp.get_related_nodes.call_args
        assert call_args[0][0] == "topics/t1"
        assert "belongsToTopic" in call_args[0][1]
        assert call_args[0][2] == "records"
        assert call_args[1].get("direction") == "inbound" or call_args[0][3] == "inbound"

    @pytest.mark.asyncio
    async def test_permission_check_called_without_metadata_filters(self) -> None:
        """The accessible-records call must NOT pass metadata filters — the
        whole point of the two-step approach is bypassing the broken
        metadata-filter AQL path."""
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}],
            accessible_map={"v1": "r1"},
            fetched_records=[{"_key": "r1", "recordName": "Doc", "recordType": "FILE"}],
        )
        state = self._state(gp)
        ok, _text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        gp.get_accessible_virtual_record_ids.assert_awaited_once_with(
            user_id="u1", org_id="o1",
        )

    @pytest.mark.asyncio
    async def test_returns_rendered_records(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}, {"_key": "r2"}],
            accessible_map={"v1": "r1", "v2": "r2"},
            fetched_records=[
                {"_key": "r1", "recordName": "Contract A", "recordType": "FILE", "webUrl": "http://x/1"},
                {"_key": "r2", "recordName": "Contract B", "recordType": "FILE"},
            ],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        assert "Contract A" in text
        assert "Contract B" in text
        assert "record_id=" in text
        assert "url=http://x/1" in text

    @pytest.mark.asyncio
    async def test_filters_by_record_types(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}, {"_key": "r2"}],
            accessible_map={"v1": "r1", "v2": "r2"},
            fetched_records=[
                {"_key": "r1", "recordName": "Contract A", "recordType": "FILE"},
                {"_key": "r2", "recordName": "Message B", "recordType": "MESSAGE"},
            ],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(
            state, "d1", "department", record_types=["file"],
        )
        assert ok is True
        assert "Contract A" in text
        assert "Message B" not in text

    @pytest.mark.asyncio
    async def test_pagination(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": f"r{i}"} for i in range(5)],
            accessible_map={f"v{i}": f"r{i}" for i in range(5)},
            fetched_records=[
                {"_key": f"r{i}", "recordName": f"Doc {i}", "recordType": "FILE"}
                for i in range(5)
            ],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(
            state, "d1", "department", page=1, limit=2,
        )
        assert ok is True
        assert "Found 5 records" in text
        assert "more record" in text

    @pytest.mark.asyncio
    async def test_get_related_nodes_failure(self) -> None:
        gp = _graph_provider(entity_doc={"departmentName": "Legal"})
        gp.get_related_nodes.side_effect = RuntimeError("boom")
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is False
        assert "Lookup failed" in text

    @pytest.mark.asyncio
    async def test_get_accessible_virtual_record_ids_failure(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}],
        )
        gp.get_accessible_virtual_record_ids.side_effect = RuntimeError("boom")
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is False
        assert "Lookup failed" in text

    @pytest.mark.asyncio
    async def test_get_records_by_record_ids_failure(self) -> None:
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}],
            accessible_map={"v1": "r1"},
        )
        gp.get_records_by_record_ids.side_effect = RuntimeError("boom")
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is False
        assert "Lookup failed" in text

    @pytest.mark.asyncio
    async def test_topic_name_field_used_for_graph_lookup(self) -> None:
        """category/subcategory/topic/language use the plain "name" field
        while department uses "departmentName" (see ops/entity_filters.py)."""
        gp = _graph_provider(
            entity_doc={"name": "Billing"},
            connected_records=[],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "t1", "topic")
        assert ok is True
        assert "Billing" in text

    @pytest.mark.asyncio
    async def test_intersection_only_keeps_accessible_connected_records(self) -> None:
        """Core invariant: a record must be BOTH connected (graph edge) AND
        accessible (permission) to appear in results."""
        gp = _graph_provider(
            entity_doc={"departmentName": "Legal"},
            connected_records=[{"_key": "r1"}, {"_key": "r2"}, {"_key": "r3"}],
            accessible_map={"v1": "r1", "v3": "r3", "v99": "r99"},
            fetched_records=[
                {"_key": "r1", "recordName": "Accessible + Connected", "recordType": "FILE"},
                {"_key": "r3", "recordName": "Also Both", "recordType": "FILE"},
            ],
        )
        state = self._state(gp)
        ok, text = await execute_find_records_by_entity(state, "d1", "department")
        assert ok is True
        assert "Accessible + Connected" in text
        assert "Also Both" in text
        assert "Found 2 records" in text
        call_args = gp.get_records_by_record_ids.call_args
        fetched_ids = set(call_args[0][0])
        assert fetched_ids == {"r1", "r3"}
