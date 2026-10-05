"""Unit tests for ``ops/entity_filters.py``."""
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    ENTITY_INDEX_CACHE_KEY,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
    is_filterable_entity,
    merge_filter_groups,
    remember_entities,
)


class TestIsFilterableEntity:
    def test_record_scoped_types_need_only_id(self) -> None:
        assert is_filterable_entity("record_group", None) is True
        assert is_filterable_entity("subcategory", None) is True

    def test_taxonomy_needs_name(self) -> None:
        assert is_filterable_entity("topic", "Roadmap") is True
        assert is_filterable_entity("topic", "") is False

    def test_record_is_not_filterable(self) -> None:
        assert is_filterable_entity("record", "doc") is False


class TestRememberEntities:
    def test_populates_all_caches(self) -> None:
        state: dict = {}
        remember_entities(state, [
            {"entityId": "t1", "entityType": "topic", "name": "Roadmap"},
            {"entityId": "s1", "entityType": "subcategory", "name": "Contracts"},
            {"entityId": "rg-1", "entityType": "record_group", "name": "Legal space"},
            {"entityId": "rec-1", "entityType": "record", "name": "Q3 plan"},
            {"entityId": "x", "entityType": "person", "name": "Ada"},
            {"entityId": "", "entityType": "topic", "name": "blank"},
        ])

        assert state[ENTITY_ID_FILTER_KEY_CACHE_KEY] == {"t1": ("topics", "Roadmap")}
        assert state[RECORD_SCOPED_ENTITY_CACHE_KEY] == {"s1": "subcategory", "rg-1": "record_group"}
        assert set(state[ENTITY_INDEX_CACHE_KEY]) == {"t1", "s1", "rg-1", "rec-1"}
        assert state[ENTITY_INDEX_CACHE_KEY]["rec-1"] == {"type": "record", "name": "Q3 plan"}

    def test_merges_with_existing_caches(self) -> None:
        state: dict = {}
        remember_entities(state, [{"entityId": "t1", "entityType": "topic", "name": "A"}])
        remember_entities(state, [{"entityId": "rg-1", "entityType": "record_group", "name": "B"}])

        assert set(state[ENTITY_INDEX_CACHE_KEY]) == {"t1", "rg-1"}
        assert "t1" in state[ENTITY_ID_FILTER_KEY_CACHE_KEY]


class TestMergeFilterGroups:
    def test_unions_without_mutating_base(self) -> None:
        base = {"topics": ["a"]}
        merged = merge_filter_groups(base, {"topics": ["a", "b"], "departments": ["d"], "empty": []})
        assert merged == {"topics": ["a", "b"], "departments": ["d"]}
        assert base == {"topics": ["a"]}

    def test_none_extra_returns_base(self) -> None:
        base = {"topics": ["a"]}
        assert merge_filter_groups(base, None) is base
