"""``allowed_types``: the entity types a request may see, enforced where entity
types are owned (``entity_permissions``), so no caller can reach a person or an
organisation its knowledge-graph arm leaves out."""

from __future__ import annotations

import pytest

from app.modules.retrieval.entity_permissions import (
    SEARCHABLE_ENTITY_TYPES,
    TAXONOMY_ENTITY_TYPES,
    list_accessible_entity_records,
    search_entities_for_user,
)
from tests.unit.modules.retrieval.test_entity_permissions import (
    _context,
    _graph,
    _hit,
    _row,
    _store,
)

_TAXONOMY_ARM = TAXONOMY_ENTITY_TYPES | {"record", "record_group"}


class TestSearch:
    async def test_people_and_organisations_outside_the_allowed_types_are_never_returned(self) -> None:
        store = _store([_hit("u-ann", "person", 0.95), _hit("acme", "organization", 0.9), _hit("t1", "topic", 0.8)])
        graph = _graph(candidates=lambda refs, org, **k: {
            "u-ann": [_row("r1", "kb-1")], "acme": [_row("r2", "kb-1")], "t1": [_row("r3", "kb-1")],
        })

        hits = await search_entities_for_user(store, graph, _context(), "ann", top_k=5, allowed_types=_TAXONOMY_ARM)

        assert [(h.entity_id, h.entity_type) for h in hits] == [("t1", "topic")]

    async def test_every_type_asks_the_store_for_the_allowed_types_only(self) -> None:
        """Left to "every type", people and companies would take candidate slots."""
        store = _store([])
        await search_entities_for_user(
            store, _graph(), _context(), "ann", top_k=5, allowed_types=_TAXONOMY_ARM,
        )
        kwargs = store.search_entities_passes.call_args.kwargs
        assert set(kwargs["entity_types"]) == _TAXONOMY_ARM
        assert kwargs["include_named"] is False

    async def test_asked_types_outside_the_allowed_ones_search_nothing(self) -> None:
        store = _store([_hit("u-ann", "person", 0.95)])
        hits = await search_entities_for_user(
            store, _graph(), _context(), "ann", entity_types=["person"], allowed_types=_TAXONOMY_ARM,
        )
        assert hits == []
        store.search_entities_passes.assert_not_called()

    async def test_by_default_every_searchable_type_is_allowed(self) -> None:
        store = _store([])
        await search_entities_for_user(store, _graph(), _context(), "ann")
        kwargs = store.search_entities_passes.call_args.kwargs
        assert kwargs["entity_types"] is None and kwargs["include_named"] is True


class TestListing:
    @pytest.mark.parametrize("entity_type", ["person", "organization", "named_entity"])
    async def test_a_type_outside_the_allowed_ones_is_refused(self, entity_type: str) -> None:
        assert entity_type in SEARCHABLE_ENTITY_TYPES
        with pytest.raises(ValueError, match="Unsupported entity type"):
            await list_accessible_entity_records(
                _graph(), _context(), entity_id="u-ann", entity_type=entity_type, allowed_types=_TAXONOMY_ARM,
            )
