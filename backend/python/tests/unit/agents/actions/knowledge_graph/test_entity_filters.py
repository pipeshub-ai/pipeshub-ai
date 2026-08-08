"""Tests for ``app.agents.actions.knowledge_graph.ops.entity_filters``."""
from __future__ import annotations

from app.agents.actions.knowledge_graph.ops.entity_filters import (
    DEFAULT_SCORE_THRESHOLD,
    ENTITY_TYPE_TO_FILTER_KEY,
    group_entities_into_filters,
    merge_filter_groups,
)


class TestGroupEntitiesIntoFilters:
    def test_empty_list(self) -> None:
        assert group_entities_into_filters([]) == {}

    def test_groups_by_filter_key(self) -> None:
        entities = [
            {"entityId": "d1", "entityType": "department", "name": "Engineering", "score": 0.9},
            {"entityId": "t1", "entityType": "topic", "name": "Machine Learning", "score": 0.8},
        ]
        result = group_entities_into_filters(entities)
        assert result == {"departments": ["Engineering"], "topics": ["Machine Learning"]}

    def test_subcategory_maps_to_level_one(self) -> None:
        entities = [{"entityId": "s1", "entityType": "subcategory", "name": "Backend", "score": 0.9}]
        assert group_entities_into_filters(entities) == {"subcategories1": ["Backend"]}

    def test_drops_below_threshold(self) -> None:
        entities = [{"entityId": "d1", "entityType": "department", "name": "Engineering", "score": 0.1}]
        assert group_entities_into_filters(entities, score_threshold=0.5) == {}

    def test_default_threshold_is_0_7(self) -> None:
        # Raised from 0.5: a hard AND filter on a mid-confidence match can
        # silently zero out search results when the graph has no belongsTo*
        # edge yet linking a record to that entity (see ops/search.py's
        # fallback, which is the safety net for whatever slips past this).
        assert DEFAULT_SCORE_THRESHOLD == 0.7

    def test_score_between_old_and_new_threshold_now_dropped(self) -> None:
        entities = [{"entityId": "d1", "entityType": "department", "name": "Engineering", "score": 0.6}]
        assert group_entities_into_filters(entities) == {}

    def test_drops_non_filterable_type(self) -> None:
        entities = [{"entityId": "p1", "entityType": "person", "name": "Jane Doe", "score": 0.9}]
        assert group_entities_into_filters(entities) == {}

    def test_drops_missing_name_or_type(self) -> None:
        entities = [
            {"entityId": "d1", "entityType": "department", "name": None, "score": 0.9},
            {"entityId": "d2", "entityType": None, "name": "Engineering", "score": 0.9},
        ]
        assert group_entities_into_filters(entities) == {}

    def test_falls_back_to_canonical_name_when_name_missing(self) -> None:
        entities = [
            {"entityId": "d1", "entityType": "department", "name": None,
             "canonicalName": "Engineering", "score": 0.9},
        ]
        assert group_entities_into_filters(entities) == {"departments": ["Engineering"]}

    def test_dedupes_same_entity_name_within_key(self) -> None:
        entities = [
            {"entityId": "d1", "entityType": "department", "name": "Engineering", "score": 0.9},
            {"entityId": "d1", "entityType": "department", "name": "Engineering", "score": 0.95},
        ]
        assert group_entities_into_filters(entities) == {"departments": ["Engineering"]}

    def test_missing_score_treated_as_pass(self) -> None:
        entities = [{"entityId": "d1", "entityType": "department", "name": "Engineering"}]
        assert group_entities_into_filters(entities) == {"departments": ["Engineering"]}

    def test_all_mapping_keys_covered(self) -> None:
        entities = [
            {"entityId": f"id-{t}", "entityType": t, "name": f"Name {t}", "score": 1.0}
            for t in ENTITY_TYPE_TO_FILTER_KEY
        ]
        result = group_entities_into_filters(entities)
        assert set(result) == set(ENTITY_TYPE_TO_FILTER_KEY.values())


class TestMergeFilterGroups:
    def test_none_extra_returns_base_unchanged(self) -> None:
        base = {"apps": ["a1"]}
        assert merge_filter_groups(base, None) == base

    def test_empty_extra_returns_base_unchanged(self) -> None:
        base = {"apps": ["a1"]}
        assert merge_filter_groups(base, {}) == base

    def test_merges_new_key(self) -> None:
        base = {"apps": ["a1"]}
        extra = {"departments": ["d1"]}
        assert merge_filter_groups(base, extra) == {"apps": ["a1"], "departments": ["d1"]}

    def test_unions_existing_key_without_duplicates(self) -> None:
        base = {"departments": ["d1"]}
        extra = {"departments": ["d1", "d2"]}
        result = merge_filter_groups(base, extra)
        assert result["departments"] == ["d1", "d2"]

    def test_does_not_mutate_base(self) -> None:
        base = {"apps": ["a1"]}
        merge_filter_groups(base, {"apps": ["a2"]})
        assert base == {"apps": ["a1"]}

    def test_skips_empty_value_lists(self) -> None:
        base = {"apps": ["a1"]}
        extra = {"departments": []}
        assert merge_filter_groups(base, extra) == {"apps": ["a1"]}
