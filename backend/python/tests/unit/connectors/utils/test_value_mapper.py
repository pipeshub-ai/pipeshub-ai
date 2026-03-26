"""Unit tests for app.connectors.utils.value_mapper — ValueMapper and convenience functions.

Covers: ValueMapper.__init__, map_status, map_priority, map_type,
map_delivery_status, _normalize_value, _find_partial_match,
convenience functions (map_status, map_priority, map_type,
map_delivery_status, map_relationship_type).
"""

import pytest

from app.config.constants.arangodb import RecordRelations
from app.connectors.utils.value_mapper import (
    DEFAULT_RELATION_MAPPINGS,
    PRIORITY_HIGH,
    PRIORITY_HIGHEST,
    PRIORITY_LOW,
    PRIORITY_LOWEST_THRESHOLD,
    PRIORITY_MEDIUM,
    ValueMapper,
    map_delivery_status,
    map_priority,
    map_relationship_type,
    map_status,
    map_type,
)
from app.models.entities import (
    DeliveryStatus,
    ItemType,
    Priority,
    Status,
)


# ============================================================================
# _normalize_value
# ============================================================================

class TestNormalizeValue:
    def test_lowercase_and_strip(self):
        assert ValueMapper._normalize_value("  In Progress  ") == "in progress"

    def test_already_normal(self):
        assert ValueMapper._normalize_value("done") == "done"


# ============================================================================
# _find_partial_match
# ============================================================================

class TestFindPartialMatch:
    def test_underscore_to_space_match(self):
        mappings = {"in_progress": "IP"}
        result = ValueMapper._find_partial_match("in progress", mappings)
        assert result == "IP"

    def test_no_match(self):
        mappings = {"done": "DONE"}
        result = ValueMapper._find_partial_match("cancelled", mappings)
        assert result is None

    def test_custom_match_func(self):
        mappings = {"bug": "BUG_TYPE"}
        result = ValueMapper._find_partial_match(
            "big bug report",
            mappings,
            match_func=lambda key, norm: key in norm,
        )
        assert result == "BUG_TYPE"

    def test_custom_match_func_no_match(self):
        mappings = {"bug": "BUG_TYPE"}
        result = ValueMapper._find_partial_match(
            "feature request",
            mappings,
            match_func=lambda key, norm: key in norm,
        )
        assert result is None


# ============================================================================
# ValueMapper.__init__
# ============================================================================

class TestValueMapperInit:
    def test_default_mappings(self):
        mapper = ValueMapper()
        # Should have default status mappings
        assert "new" in mapper.status_mappings
        assert "low" in mapper.priority_mappings
        assert "task" in mapper.type_mappings
        assert "on track" in mapper.delivery_status_mappings

    def test_custom_mappings_merged(self):
        custom_status = {"custom_status": Status.OPEN}
        mapper = ValueMapper(status_mappings=custom_status)
        assert "custom_status" in mapper.status_mappings
        # Default still present
        assert "new" in mapper.status_mappings

    def test_custom_overrides_default(self):
        custom_status = {"new": Status.REOPENED}
        mapper = ValueMapper(status_mappings=custom_status)
        assert mapper.status_mappings["new"] == Status.REOPENED

    def test_all_custom_mappings(self):
        mapper = ValueMapper(
            status_mappings={"x": Status.DONE},
            priority_mappings={"x": Priority.HIGH},
            type_mappings={"x": ItemType.BUG},
            delivery_status_mappings={"x": DeliveryStatus.AT_RISK},
        )
        assert mapper.status_mappings["x"] == Status.DONE
        assert mapper.priority_mappings["x"] == Priority.HIGH
        assert mapper.type_mappings["x"] == ItemType.BUG
        assert mapper.delivery_status_mappings["x"] == DeliveryStatus.AT_RISK


# ============================================================================
# map_status
# ============================================================================

class TestMapStatus:
    def test_none_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_status(None) is None

    def test_empty_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_status("") is None

    def test_exact_match(self):
        mapper = ValueMapper()
        assert mapper.map_status("done") == Status.DONE

    def test_case_insensitive(self):
        mapper = ValueMapper()
        assert mapper.map_status("DONE") == Status.DONE

    def test_whitespace_trimmed(self):
        mapper = ValueMapper()
        assert mapper.map_status("  done  ") == Status.DONE

    def test_partial_match_underscore(self):
        mapper = ValueMapper()
        # "in_progress" is a default mapping
        assert mapper.map_status("In Progress") == Status.IN_PROGRESS

    def test_no_match_returns_original(self):
        mapper = ValueMapper()
        result = mapper.map_status("VERY_CUSTOM_STATUS")
        assert result == "VERY_CUSTOM_STATUS"

    def test_all_default_statuses(self):
        mapper = ValueMapper()
        for key, expected in ValueMapper.DEFAULT_STATUS_MAPPINGS.items():
            result = mapper.map_status(key)
            assert result == expected, f"Failed for key={key}"


# ============================================================================
# map_priority
# ============================================================================

class TestMapPriority:
    def test_none_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_priority(None) is None

    def test_empty_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_priority("") is None

    def test_exact_match(self):
        mapper = ValueMapper()
        assert mapper.map_priority("high") == Priority.HIGH

    def test_case_insensitive(self):
        mapper = ValueMapper()
        assert mapper.map_priority("HIGH") == Priority.HIGH

    def test_numeric_p0(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P0") == Priority.UNKNOWN

    def test_numeric_p1(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P1") == Priority.HIGHEST

    def test_numeric_p2(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P2") == Priority.HIGH

    def test_numeric_p3(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P3") == Priority.MEDIUM

    def test_numeric_p4(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P4") == Priority.LOW

    def test_numeric_p5(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P5") == Priority.LOWEST

    def test_numeric_p9(self):
        mapper = ValueMapper()
        assert mapper.map_priority("P9") == Priority.LOWEST

    def test_numeric_0(self):
        mapper = ValueMapper()
        assert mapper.map_priority("0") == Priority.UNKNOWN

    def test_numeric_1(self):
        mapper = ValueMapper()
        assert mapper.map_priority("1") == Priority.HIGHEST

    def test_numeric_2(self):
        mapper = ValueMapper()
        assert mapper.map_priority("2") == Priority.HIGH

    def test_numeric_3(self):
        mapper = ValueMapper()
        assert mapper.map_priority("3") == Priority.MEDIUM

    def test_numeric_4(self):
        mapper = ValueMapper()
        assert mapper.map_priority("4") == Priority.LOW

    def test_numeric_5_plus(self):
        mapper = ValueMapper()
        assert mapper.map_priority("5") == Priority.LOWEST
        assert mapper.map_priority("10") == Priority.LOWEST

    def test_no_match_returns_original(self):
        mapper = ValueMapper()
        result = mapper.map_priority("WEIRD_PRIORITY")
        assert result == "WEIRD_PRIORITY"

    def test_all_default_priorities(self):
        mapper = ValueMapper()
        for key, expected in ValueMapper.DEFAULT_PRIORITY_MAPPINGS.items():
            result = mapper.map_priority(key)
            assert result == expected, f"Failed for key={key}"

    def test_zammad_priority(self):
        mapper = ValueMapper()
        assert mapper.map_priority("1 low") == Priority.LOW
        assert mapper.map_priority("2 normal") == Priority.MEDIUM
        assert mapper.map_priority("3 high") == Priority.HIGH
        assert mapper.map_priority("4 urgent") == Priority.HIGHEST


# ============================================================================
# map_type
# ============================================================================

class TestMapType:
    def test_none_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_type(None) is None

    def test_empty_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_type("") is None

    def test_exact_match(self):
        mapper = ValueMapper()
        assert mapper.map_type("bug") == ItemType.BUG

    def test_case_insensitive(self):
        mapper = ValueMapper()
        assert mapper.map_type("BUG") == ItemType.BUG

    def test_partial_match_substring(self):
        """Partial match: 'task' is in 'my task' or 'my task' contains 'task'."""
        mapper = ValueMapper()
        # The partial match uses: key in norm or norm in key
        result = mapper.map_type("task assignment")
        assert result == ItemType.TASK

    def test_no_match_returns_original(self):
        mapper = ValueMapper()
        result = mapper.map_type("UNKNOWN_TYPE")
        assert result == "UNKNOWN_TYPE"

    def test_all_default_types(self):
        mapper = ValueMapper()
        for key, expected in ValueMapper.DEFAULT_TYPE_MAPPINGS.items():
            result = mapper.map_type(key)
            assert result == expected, f"Failed for key={key}"


# ============================================================================
# map_delivery_status
# ============================================================================

class TestMapDeliveryStatus:
    def test_none_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_delivery_status(None) is None

    def test_empty_returns_none(self):
        mapper = ValueMapper()
        assert mapper.map_delivery_status("") is None

    def test_exact_match(self):
        mapper = ValueMapper()
        assert mapper.map_delivery_status("on track") == DeliveryStatus.ON_TRACK

    def test_case_insensitive(self):
        mapper = ValueMapper()
        assert mapper.map_delivery_status("ON TRACK") == DeliveryStatus.ON_TRACK

    def test_underscore_variant(self):
        mapper = ValueMapper()
        assert mapper.map_delivery_status("at_risk") == DeliveryStatus.AT_RISK

    def test_partial_match(self):
        mapper = ValueMapper()
        # "off track" should match "off_track" via partial match
        assert mapper.map_delivery_status("off track") == DeliveryStatus.OFF_TRACK

    def test_no_match_returns_original(self):
        mapper = ValueMapper()
        result = mapper.map_delivery_status("CUSTOM_STATUS")
        assert result == "CUSTOM_STATUS"

    def test_all_default_delivery_statuses(self):
        mapper = ValueMapper()
        for key, expected in ValueMapper.DEFAULT_DELIVERY_STATUS_MAPPINGS.items():
            result = mapper.map_delivery_status(key)
            assert result == expected, f"Failed for key={key}"


# ============================================================================
# Convenience functions: map_status, map_priority, map_type, map_delivery_status
# ============================================================================

class TestConvenienceMapStatus:
    def test_default_mapper(self):
        assert map_status("done") == Status.DONE

    def test_none(self):
        assert map_status(None) is None

    def test_custom_mappings(self):
        result = map_status("custom_st", custom_mappings={"custom_st": Status.BLOCKED})
        assert result == Status.BLOCKED


class TestConvenienceMapPriority:
    def test_default_mapper(self):
        assert map_priority("high") == Priority.HIGH

    def test_none(self):
        assert map_priority(None) is None

    def test_custom_mappings(self):
        result = map_priority("p_custom", custom_mappings={"p_custom": Priority.CRITICAL})
        assert result == Priority.CRITICAL


class TestConvenienceMapType:
    def test_default_mapper(self):
        assert map_type("bug") == ItemType.BUG

    def test_none(self):
        assert map_type(None) is None

    def test_custom_mappings(self):
        result = map_type("custom_t", custom_mappings={"custom_t": ItemType.EPIC})
        assert result == ItemType.EPIC


class TestConvenienceMapDeliveryStatus:
    def test_default_mapper(self):
        assert map_delivery_status("on track") == DeliveryStatus.ON_TRACK

    def test_none(self):
        assert map_delivery_status(None) is None

    def test_custom_mappings(self):
        result = map_delivery_status("custom_ds", custom_mappings={"custom_ds": DeliveryStatus.HIGH_RISK})
        assert result == DeliveryStatus.HIGH_RISK


# ============================================================================
# map_relationship_type (module-level function)
# ============================================================================

class TestMapRelationshipType:
    def test_none_returns_none(self):
        assert map_relationship_type(None) is None

    def test_empty_returns_none(self):
        assert map_relationship_type("") is None

    def test_exact_match(self):
        assert map_relationship_type("blocks") == RecordRelations.BLOCKS

    def test_case_insensitive(self):
        assert map_relationship_type("BLOCKS") == RecordRelations.BLOCKS

    def test_partial_match(self):
        # "depends on" vs "depends_on"
        assert map_relationship_type("depends on") == RecordRelations.DEPENDS_ON

    def test_no_match_returns_original(self):
        result = map_relationship_type("custom_relation")
        assert result == "custom_relation"

    def test_all_default_mappings(self):
        for key, expected in DEFAULT_RELATION_MAPPINGS.items():
            result = map_relationship_type(key)
            assert result == expected, f"Failed for key={key}"

    def test_custom_mappings(self):
        custom = {"my_rel": RecordRelations.REVIEWS}
        result = map_relationship_type("my_rel", custom_mappings=custom)
        assert result == RecordRelations.REVIEWS

    def test_custom_overrides_default(self):
        custom = {"blocks": RecordRelations.RELATED}
        result = map_relationship_type("blocks", custom_mappings=custom)
        assert result == RecordRelations.RELATED


# ============================================================================
# Priority constant values
# ============================================================================

class TestPriorityConstants:
    def test_constants_order(self):
        """Priority level constants should be ordered 1-5."""
        assert PRIORITY_HIGHEST == 1
        assert PRIORITY_HIGH == 2
        assert PRIORITY_MEDIUM == 3
        assert PRIORITY_LOW == 4
        assert PRIORITY_LOWEST_THRESHOLD == 5
