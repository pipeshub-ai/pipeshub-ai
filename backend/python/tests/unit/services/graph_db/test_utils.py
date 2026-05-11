"""
Unit tests for graph_db common utility functions.

Covers:
- ``build_connector_stats_response`` — connector stats aggregation
- ``dedupe_agents_by_id`` — agent dedupe by id (used by in-use checks)
"""

import pytest

from app.services.graph_db.common.utils import (
    build_connector_stats_response,
    dedupe_agents_by_id,
)


STATUSES = ["PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"]


class TestBuildConnectorStatsResponseEmpty:
    """Tests for empty / trivial input."""

    def test_empty_rows(self):
        result = build_connector_stats_response(
            rows=[],
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["orgId"] == "org-1"
        assert result["connectorId"] == "conn-1"
        assert result["origin"] == "CONNECTOR"
        assert result["stats"]["total"] == 0
        assert result["stats"]["indexingStatus"] == {s: 0 for s in STATUSES}
        assert result["byRecordType"] == []

    def test_empty_statuses_list(self):
        result = build_connector_stats_response(
            rows=[],
            statuses=[],
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["indexingStatus"] == {}
        assert result["stats"]["total"] == 0


class TestBuildConnectorStatsResponseSingleRow:
    """Tests for single-row input."""

    def test_single_row_known_status(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 10
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 10
        assert result["stats"]["indexingStatus"]["PENDING"] == 0
        assert len(result["byRecordType"]) == 1
        rt = result["byRecordType"][0]
        assert rt["recordType"] == "FILE"
        assert rt["total"] == 10
        assert rt["indexingStatus"]["COMPLETED"] == 10
        assert rt["indexingStatus"]["PENDING"] == 0

    def test_single_row_unknown_status(self):
        """An unknown status should be added to total but not to indexing_status_counts."""
        rows = [
            {"recordType": "FILE", "indexingStatus": "UNKNOWN_STATUS", "cnt": 5}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 5
        # The unknown status is not counted in the known statuses
        assert all(v == 0 for v in result["stats"]["indexingStatus"].values())
        # byRecordType still has the record
        assert len(result["byRecordType"]) == 1
        rt = result["byRecordType"][0]
        assert rt["total"] == 5
        # Unknown status not in the per-record indexingStatus dict
        assert rt["indexingStatus"]["COMPLETED"] == 0

    def test_single_row_missing_record_type(self):
        """If recordType is None/missing, no byRecordType entry should be created."""
        rows = [
            {"indexingStatus": "COMPLETED", "cnt": 3}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 3
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 3
        assert result["byRecordType"] == []

    def test_row_with_zero_count(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 0}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 0
        assert result["byRecordType"][0]["total"] == 0

    def test_row_missing_cnt_defaults_to_zero(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED"}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 0


class TestBuildConnectorStatsResponseMultipleRows:
    """Tests for multiple-row input and aggregation."""

    def test_multiple_rows_same_record_type(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10},
            {"recordType": "FILE", "indexingStatus": "PENDING", "cnt": 5},
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 15
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 10
        assert result["stats"]["indexingStatus"]["PENDING"] == 5
        assert len(result["byRecordType"]) == 1
        rt = result["byRecordType"][0]
        assert rt["total"] == 15
        assert rt["indexingStatus"]["COMPLETED"] == 10
        assert rt["indexingStatus"]["PENDING"] == 5

    def test_multiple_record_types(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10},
            {"recordType": "EMAIL", "indexingStatus": "COMPLETED", "cnt": 20},
            {"recordType": "EMAIL", "indexingStatus": "FAILED", "cnt": 3},
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 33
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 30
        assert result["stats"]["indexingStatus"]["FAILED"] == 3
        assert len(result["byRecordType"]) == 2
        rt_map = {rt["recordType"]: rt for rt in result["byRecordType"]}
        assert rt_map["FILE"]["total"] == 10
        assert rt_map["FILE"]["indexingStatus"]["COMPLETED"] == 10
        assert rt_map["EMAIL"]["total"] == 23
        assert rt_map["EMAIL"]["indexingStatus"]["COMPLETED"] == 20
        assert rt_map["EMAIL"]["indexingStatus"]["FAILED"] == 3

    def test_all_statuses_present(self):
        rows = [
            {"recordType": "DOC", "indexingStatus": "PENDING", "cnt": 1},
            {"recordType": "DOC", "indexingStatus": "IN_PROGRESS", "cnt": 2},
            {"recordType": "DOC", "indexingStatus": "COMPLETED", "cnt": 3},
            {"recordType": "DOC", "indexingStatus": "FAILED", "cnt": 4},
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 10
        assert result["stats"]["indexingStatus"]["PENDING"] == 1
        assert result["stats"]["indexingStatus"]["IN_PROGRESS"] == 2
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 3
        assert result["stats"]["indexingStatus"]["FAILED"] == 4

    def test_mixed_known_and_unknown_statuses(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 10},
            {"recordType": "FILE", "indexingStatus": "WEIRD", "cnt": 2},
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        # Total includes all rows
        assert result["stats"]["total"] == 12
        # Known status counted
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 10
        # Unknown status not in the dict (WEIRD not in STATUSES)
        assert "WEIRD" not in result["stats"]["indexingStatus"]
        # byRecordType total includes both
        assert result["byRecordType"][0]["total"] == 12
        assert result["byRecordType"][0]["indexingStatus"]["COMPLETED"] == 10

    def test_record_type_none_not_added_to_by_record_type(self):
        """Rows with recordType=None are aggregated in total but not in byRecordType."""
        rows = [
            {"recordType": None, "indexingStatus": "COMPLETED", "cnt": 7},
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 3},
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["stats"]["total"] == 10
        assert result["stats"]["indexingStatus"]["COMPLETED"] == 10
        # Only FILE in byRecordType
        assert len(result["byRecordType"]) == 1
        assert result["byRecordType"][0]["recordType"] == "FILE"


class TestBuildConnectorStatsResponseOutputStructure:
    """Tests for output structure and field presence."""

    def test_output_has_required_keys(self):
        result = build_connector_stats_response(
            rows=[],
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert "orgId" in result
        assert "connectorId" in result
        assert "origin" in result
        assert "stats" in result
        assert "byRecordType" in result
        assert "total" in result["stats"]
        assert "indexingStatus" in result["stats"]

    def test_origin_is_connector(self):
        result = build_connector_stats_response(
            rows=[],
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        assert result["origin"] == "CONNECTOR"

    def test_by_record_type_entries_have_correct_structure(self):
        rows = [
            {"recordType": "FILE", "indexingStatus": "COMPLETED", "cnt": 1}
        ]
        result = build_connector_stats_response(
            rows=rows,
            statuses=STATUSES,
            org_id="org-1",
            connector_id="conn-1",
        )
        rt = result["byRecordType"][0]
        assert "recordType" in rt
        assert "total" in rt
        assert "indexingStatus" in rt
        # indexingStatus should have all statuses as keys
        for s in STATUSES:
            assert s in rt["indexingStatus"]

    def test_org_id_and_connector_id_propagated(self):
        result = build_connector_stats_response(
            rows=[],
            statuses=STATUSES,
            org_id="my-org",
            connector_id="my-conn",
        )
        assert result["orgId"] == "my-org"
        assert result["connectorId"] == "my-conn"


# ---------------------------------------------------------------------------
# dedupe_agents_by_id
# ---------------------------------------------------------------------------


class TestDedupeAgentsByIdEmpty:
    """Trivial / empty inputs."""

    def test_none_returns_empty_list(self):
        assert dedupe_agents_by_id(None) == []

    def test_empty_list_returns_empty_list(self):
        assert dedupe_agents_by_id([]) == []


class TestDedupeAgentsByIdHappyPath:
    """Standard inputs the providers actually return."""

    def test_single_agent(self):
        rows = [{"agentId": "agt/a1", "agentName": "Helper"}]
        assert dedupe_agents_by_id(rows) == ["Helper"]

    def test_multiple_distinct_agents_preserve_order(self):
        rows = [
            {"agentId": "agt/a1", "agentName": "First"},
            {"agentId": "agt/a2", "agentName": "Second"},
            {"agentId": "agt/a3", "agentName": "Third"},
        ]
        assert dedupe_agents_by_id(rows) == ["First", "Second", "Third"]


class TestDedupeAgentsByIdDeduping:
    """The core behaviour the function exists to provide."""

    def test_same_id_appears_twice_collapses(self):
        """DB returned the same agent in two rows (e.g. via two knowledge nodes)."""
        rows = [
            {"agentId": "agt/a1", "agentName": "Helper"},
            {"agentId": "agt/a1", "agentName": "Helper"},
        ]
        assert dedupe_agents_by_id(rows) == ["Helper"]

    def test_two_agents_same_name_different_ids_both_kept(self):
        """Critical: this is the bug-fix scenario — distinct agents must NOT collapse by name."""
        rows = [
            {"agentId": "agt/a1", "agentName": "kb-agent"},
            {"agentId": "agt/a2", "agentName": "kb-agent"},
        ]
        result = dedupe_agents_by_id(rows)
        assert result == ["kb-agent", "kb-agent"]
        assert len(result) == 2

    def test_first_occurrence_wins_when_same_id_has_different_names(self):
        """Defensive: if two rows share an id but somehow have different names,
        the first row's name is kept (deterministic order)."""
        rows = [
            {"agentId": "agt/a1", "agentName": "OriginalName"},
            {"agentId": "agt/a1", "agentName": "AnotherName"},
        ]
        assert dedupe_agents_by_id(rows) == ["OriginalName"]

    def test_mixed_duplicate_and_unique(self):
        rows = [
            {"agentId": "agt/a1", "agentName": "A"},
            {"agentId": "agt/a2", "agentName": "B"},
            {"agentId": "agt/a1", "agentName": "A"},  # dup id, drop
            {"agentId": "agt/a3", "agentName": "C"},
            {"agentId": "agt/a2", "agentName": "B"},  # dup id, drop
        ]
        assert dedupe_agents_by_id(rows) == ["A", "B", "C"]


class TestDedupeAgentsByIdDefensive:
    """Defensive handling of malformed rows — must not crash."""

    def test_none_row_skipped(self):
        rows = [None, {"agentId": "agt/a1", "agentName": "Real"}]
        assert dedupe_agents_by_id(rows) == ["Real"]

    def test_non_dict_row_skipped(self):
        rows = [
            "not-a-dict",
            42,
            ["list", "of", "things"],
            {"agentId": "agt/a1", "agentName": "Real"},
        ]
        assert dedupe_agents_by_id(rows) == ["Real"]

    def test_row_missing_agent_id_skipped(self):
        rows = [
            {"agentName": "OrphanWithoutId"},
            {"agentId": "agt/a1", "agentName": "Real"},
        ]
        assert dedupe_agents_by_id(rows) == ["Real"]

    def test_row_with_falsy_agent_id_skipped(self):
        rows = [
            {"agentId": "", "agentName": "EmptyId"},
            {"agentId": None, "agentName": "NoneId"},
            {"agentId": "agt/a1", "agentName": "Real"},
        ]
        assert dedupe_agents_by_id(rows) == ["Real"]

    def test_row_missing_agent_name_uses_unknown(self):
        rows = [
            {"agentId": "agt/a1"},  # name missing
            {"agentId": "agt/a2", "agentName": "Named"},
        ]
        assert dedupe_agents_by_id(rows) == ["Unknown", "Named"]

    def test_all_rows_invalid_returns_empty(self):
        rows = [None, "string", 42, {"agentName": "no-id"}]
        assert dedupe_agents_by_id(rows) == []


class TestDedupeAgentsByIdReturnShape:
    """Shape contract — callers expect list[str]."""

    def test_returns_list_not_set(self):
        rows = [{"agentId": "agt/a1", "agentName": "X"}]
        result = dedupe_agents_by_id(rows)
        assert isinstance(result, list)

    def test_each_element_is_string(self):
        rows = [
            {"agentId": "agt/a1", "agentName": "X"},
            {"agentId": "agt/a2"},  # missing name → fallback string
        ]
        for name in dedupe_agents_by_id(rows):
            assert isinstance(name, str)
