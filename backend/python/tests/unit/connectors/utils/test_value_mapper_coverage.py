"""
Additional tests for app.connectors.utils.value_mapper to cover missing lines/branches.

Targets:
  - Line 256: partial match return in map_status
  - Line 289: numeric 0 return in map_priority (via "P00" to bypass exact match)
  - Line 361: partial match return in map_delivery_status
  - Line 434: partial match return in map_relationship_type
  - Line 300 / 298->304: unreachable dead code (documented but cannot be covered)
"""

import pytest

from app.config.constants.arangodb import RecordRelations
from app.connectors.utils.value_mapper import (
    ValueMapper,
    map_delivery_status,
    map_relationship_type,
    map_status,
)
from app.models.entities import (
    DeliveryStatus,
    Priority,
    Status,
)


# ============================================================================
# map_status - partial match path (line 256)
# ============================================================================


class TestMapStatusPartialMatch:
    """Hit the partial match return in map_status (line 256)."""

    def test_partial_match_via_space_to_underscore(self):
        """Custom mapping with only underscore form; input uses spaces."""
        custom = {"my_custom_status": Status.BLOCKED}
        mapper = ValueMapper(status_mappings=custom)
        result = mapper.map_status("my custom status")
        assert result == Status.BLOCKED

    def test_partial_match_normalized_space_to_underscore(self):
        """Input has spaces; key has underscores. Second partial match condition:
        normalized.replace(' ', '_') == key"""
        custom = {"waiting_for_review": Status.WAITING}
        mapper = ValueMapper(status_mappings=custom)
        result = mapper.map_status("waiting for review")
        assert result == Status.WAITING

    def test_convenience_function_partial_match(self):
        """Convenience map_status with custom mapping triggering partial match."""
        result = map_status(
            "waiting for review",
            custom_mappings={"waiting_for_review": Status.WAITING},
        )
        assert result == Status.WAITING


# ============================================================================
# map_priority - numeric zero via non-exact-match path (line 289)
# ============================================================================


class TestMapPriorityNumericZeroBranch:
    """Hit the numeric 0 return in the numeric priority branch (line 289).

    "0" and "p0" are in the default mappings and match exactly, so they
    never reach the numeric branch.  Use "P00" (normalizes to "p00") to
    bypass exact match but still parse as int 0.
    """

    def test_p00_maps_to_unknown(self):
        mapper = ValueMapper()
        result = mapper.map_priority("P00")
        assert result == Priority.UNKNOWN

    def test_p000_maps_to_unknown(self):
        mapper = ValueMapper()
        result = mapper.map_priority("P000")
        assert result == Priority.UNKNOWN

    def test_p_only_returns_original(self):
        """A single 'p' (len==1) goes to the else branch and is not a digit."""
        mapper = ValueMapper()
        result = mapper.map_priority("p")
        assert result == "p"

    def test_p_followed_by_non_digit(self):
        """'pABC' - starts with p, num_part='ABC', not a digit -> return original."""
        mapper = ValueMapper()
        result = mapper.map_priority("pABC")
        assert result == "pABC"


# ============================================================================
# map_delivery_status - partial match path (line 361)
# ============================================================================


class TestMapDeliveryStatusPartialMatch:
    """Hit the partial match return in map_delivery_status (line 361)."""

    def test_partial_match_via_custom_underscore_mapping(self):
        """Custom mapping with underscore form only; input uses spaces."""
        custom = {"very_off_track": DeliveryStatus.OFF_TRACK}
        mapper = ValueMapper(delivery_status_mappings=custom)
        result = mapper.map_delivery_status("very off track")
        assert result == DeliveryStatus.OFF_TRACK

    def test_partial_match_normalized_space_to_underscore(self):
        """Input has spaces, key has underscores. Second condition:
        normalized.replace(' ', '_') == key."""
        custom = {"very_off_track": DeliveryStatus.OFF_TRACK}
        mapper = ValueMapper(delivery_status_mappings=custom)
        result = mapper.map_delivery_status("very off track")
        assert result == DeliveryStatus.OFF_TRACK

    def test_convenience_function_partial_match(self):
        """Convenience map_delivery_status triggering partial match."""
        result = map_delivery_status(
            "slightly at risk",
            custom_mappings={"slightly_at_risk": DeliveryStatus.SOME_RISK},
        )
        assert result == DeliveryStatus.SOME_RISK


# ============================================================================
# map_relationship_type - partial match path (line 434)
# ============================================================================


class TestMapRelationshipTypePartialMatch:
    """Hit the partial match return in map_relationship_type (line 434)."""

    def test_partial_match_via_custom_underscore_mapping(self):
        """Custom mapping with underscore form; input uses spaces."""
        custom = {"is_parent_of": RecordRelations.RELATED}
        result = map_relationship_type("is parent of", custom_mappings=custom)
        assert result == RecordRelations.RELATED

    def test_partial_match_normalized_space_to_underscore(self):
        """Input has spaces, key has underscores. Second condition:
        normalized.replace(' ', '_') == key."""
        custom = {"is_parent_of": RecordRelations.RELATED}
        result = map_relationship_type("is parent of", custom_mappings=custom)
        assert result == RecordRelations.RELATED


# ============================================================================
# _find_partial_match - space-to-underscore direction coverage
# ============================================================================


class TestFindPartialMatchUnderscoreToSpace:
    """Ensure both directions of the default partial match are covered."""

    def test_space_to_underscore_match(self):
        """Input with spaces, mapping key with underscores."""
        mappings = {"hello_world": "HW"}
        result = ValueMapper._find_partial_match("hello world", mappings)
        assert result == "HW"

    def test_input_space_replaced_to_match_underscore_key(self):
        """Second condition: normalized.replace(' ', '_') == key.
        Input with spaces, key with underscores."""
        mappings = {"good_morning": "GM"}
        result = ValueMapper._find_partial_match("good morning", mappings)
        assert result == "GM"

    def test_neither_direction_matches(self):
        mappings = {"foo_bar": "FB"}
        result = ValueMapper._find_partial_match("baz_qux", mappings)
        assert result is None
