"""Unit tests for app.utils.agent_fetch_full_record."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.agent_fetch_full_record import (
    AgentFetchFullRecordArgs,
    _R_LABEL_RE,
    _resolve_record_ids,
    create_agent_fetch_full_record_tool,
)


# ---------------------------------------------------------------------------
# _R_LABEL_RE regex tests
# ---------------------------------------------------------------------------


class TestRLabelRegex:
    def test_matches_simple_r_label(self):
        assert _R_LABEL_RE.match("R1") is not None
        assert _R_LABEL_RE.match("R10") is not None
        assert _R_LABEL_RE.match("R999") is not None

    def test_matches_with_sub_label(self):
        m = _R_LABEL_RE.match("R1-4")
        assert m is not None
        assert m.group(1) == "1"

    def test_case_insensitive(self):
        assert _R_LABEL_RE.match("r1") is not None
        assert _R_LABEL_RE.match("r1-2") is not None

    def test_no_match_invalid(self):
        assert _R_LABEL_RE.match("X1") is None
        assert _R_LABEL_RE.match("Record1") is None
        assert _R_LABEL_RE.match("R") is None
        assert _R_LABEL_RE.match("") is None
        assert _R_LABEL_RE.match("R1-") is None

    def test_no_match_embedded(self):
        # Full match required from start
        assert _R_LABEL_RE.match("  R1") is None  # leading spaces


# ---------------------------------------------------------------------------
# AgentFetchFullRecordArgs
# ---------------------------------------------------------------------------


class TestAgentFetchFullRecordArgs:
    def test_basic_creation(self):
        args = AgentFetchFullRecordArgs(record_ids=["R1", "R2"])
        assert args.record_ids == ["R1", "R2"]
        assert args.reason == "Fetching full record content for a comprehensive answer"

    def test_custom_reason(self):
        args = AgentFetchFullRecordArgs(record_ids=["R1"], reason="Need more context")
        assert args.reason == "Need more context"

    def test_empty_list(self):
        args = AgentFetchFullRecordArgs(record_ids=[])
        assert args.record_ids == []


# ---------------------------------------------------------------------------
# _resolve_record_ids
# ---------------------------------------------------------------------------


class TestResolveRecordIds:
    def test_empty_input(self):
        found, not_found = _resolve_record_ids([], {}, None)
        assert found == []
        assert not_found == []

    def test_r_label_resolution(self):
        vrid_map = {"uuid-1": {"title": "Doc 1", "id": "arango-1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1"], vrid_map, label_map)
        assert len(found) == 1
        assert found[0]["title"] == "Doc 1"
        assert found[0]["virtual_record_id"] == "uuid-1"

    def test_r_label_with_sublabel(self):
        """R1-4 should resolve to base R1."""
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1-4"], vrid_map, label_map)
        assert len(found) == 1
        assert found[0]["virtual_record_id"] == "uuid-1"

    def test_r_label_case_insensitive(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["r1"], vrid_map, label_map)
        assert len(found) == 1

    def test_uuid_direct_lookup(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        found, not_found = _resolve_record_ids(["uuid-1"], vrid_map, None)
        assert len(found) == 1
        assert found[0]["virtual_record_id"] == "uuid-1"

    def test_legacy_arango_key_lookup(self):
        vrid_map = {"uuid-1": {"title": "Doc 1", "id": "arango-key-1"}}
        found, not_found = _resolve_record_ids(["arango-key-1"], vrid_map, None)
        assert len(found) == 1
        assert found[0]["virtual_record_id"] == "uuid-1"

    def test_not_found(self):
        vrid_map = {}
        found, not_found = _resolve_record_ids(["R999"], vrid_map, {"R1": "uuid-1"})
        assert found == []
        assert not_found == ["R999"]

    def test_duplicate_records_skipped(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1", "R1"], vrid_map, label_map)
        assert len(found) == 1

    def test_duplicate_via_different_ids(self):
        """Same record resolved via R-label and UUID should only appear once."""
        vrid_map = {"uuid-1": {"title": "Doc 1", "id": "arango-1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1", "uuid-1"], vrid_map, label_map)
        assert len(found) == 1

    def test_resolved_but_record_is_none(self):
        """If the resolved vid maps to None, it's treated as not found."""
        vrid_map = {"uuid-1": None}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1"], vrid_map, label_map)
        assert found == []
        assert not_found == ["R1"]

    def test_no_label_map(self):
        """When label_to_virtual_record_id is None, R-labels can't resolve."""
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        found, not_found = _resolve_record_ids(["R1"], vrid_map, None)
        assert found == []
        assert not_found == ["R1"]

    def test_mixed_found_and_not_found(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}, "uuid-2": {"title": "Doc 2"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R1", "R99", "uuid-2"], vrid_map, label_map)
        assert len(found) == 2
        assert not_found == ["R99"]

    def test_does_not_mutate_original_record(self):
        """Resolved records are shallow copies - original not mutated."""
        original = {"title": "Doc 1"}
        vrid_map = {"uuid-1": original}
        found, not_found = _resolve_record_ids(["uuid-1"], vrid_map, None)
        assert "virtual_record_id" in found[0]
        assert "virtual_record_id" not in original

    def test_r_label_with_whitespace(self):
        """R-label with leading/trailing spaces should be stripped."""
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["  R1  "], vrid_map, label_map)
        assert len(found) == 1

    def test_legacy_lookup_skips_none_records(self):
        """Strategy 3 should skip None records in the map."""
        vrid_map = {"uuid-1": None, "uuid-2": {"id": "target", "title": "Doc"}}
        found, not_found = _resolve_record_ids(["target"], vrid_map, None)
        assert len(found) == 1
        assert found[0]["title"] == "Doc"

    def test_r_label_not_in_label_map(self):
        """R-label that doesn't exist in label_map falls to other strategies."""
        vrid_map = {"R5": {"title": "Doc via UUID"}}
        label_map = {"R1": "uuid-1"}
        found, not_found = _resolve_record_ids(["R5"], vrid_map, label_map)
        assert len(found) == 1

    def test_empty_label_map(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        found, not_found = _resolve_record_ids(["R1"], vrid_map, {})
        assert found == []
        assert not_found == ["R1"]


# ---------------------------------------------------------------------------
# create_agent_fetch_full_record_tool
# ---------------------------------------------------------------------------


class TestCreateAgentFetchFullRecordTool:
    def test_creates_tool(self):
        tool = create_agent_fetch_full_record_tool({})
        assert tool is not None
        assert tool.name == "fetch_full_record"

    async def test_tool_success(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}, "uuid-2": {"title": "Doc 2"}}
        label_map = {"R1": "uuid-1", "R2": "uuid-2"}
        tool = create_agent_fetch_full_record_tool(vrid_map, label_map)

        result = await tool.ainvoke({"record_ids": ["R1", "R2"]})
        assert result["ok"] is True
        assert result["record_count"] == 2
        assert len(result["records"]) == 2

    async def test_tool_none_found(self):
        tool = create_agent_fetch_full_record_tool({})
        result = await tool.ainvoke({"record_ids": ["R999"]})
        assert result["ok"] is False
        assert "error" in result

    async def test_tool_partial_found(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        tool = create_agent_fetch_full_record_tool(vrid_map, label_map)

        result = await tool.ainvoke({"record_ids": ["R1", "R999"]})
        assert result["ok"] is True
        assert result["record_count"] == 1
        assert result["not_found"] == ["R999"]

    async def test_tool_with_reason(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        label_map = {"R1": "uuid-1"}
        tool = create_agent_fetch_full_record_tool(vrid_map, label_map)

        result = await tool.ainvoke({
            "record_ids": ["R1"],
            "reason": "Need full content for summary"
        })
        assert result["ok"] is True

    async def test_tool_exception_handling(self):
        """If _resolve_record_ids raises, the tool returns error."""
        tool = create_agent_fetch_full_record_tool({})

        with patch(
            "app.utils.agent_fetch_full_record._resolve_record_ids",
            side_effect=Exception("unexpected error")
        ):
            result = await tool.ainvoke({"record_ids": ["R1"]})
            assert result["ok"] is False
            assert "Failed to fetch records" in result["error"]

    async def test_tool_with_uuid_ids(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        tool = create_agent_fetch_full_record_tool(vrid_map)

        result = await tool.ainvoke({"record_ids": ["uuid-1"]})
        assert result["ok"] is True
        assert result["record_count"] == 1

    async def test_tool_no_label_map(self):
        vrid_map = {"uuid-1": {"title": "Doc 1"}}
        tool = create_agent_fetch_full_record_tool(vrid_map, None)

        result = await tool.ainvoke({"record_ids": ["uuid-1"]})
        assert result["ok"] is True

    async def test_tool_empty_record_ids(self):
        tool = create_agent_fetch_full_record_tool({})
        result = await tool.ainvoke({"record_ids": []})
        assert result["ok"] is False
