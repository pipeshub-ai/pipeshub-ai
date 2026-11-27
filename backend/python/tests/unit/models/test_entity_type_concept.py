"""A domain concept is an entity type of its own: its vectors are projected, paged and cleaned up with the rest."""

from __future__ import annotations

from app.models.entities import EntityRecord, EntityType


def test_a_concept_is_an_entity_type() -> None:
    assert EntityType("concept") is EntityType.CONCEPT
    record = EntityRecord(entity_id="c-1", entity_type="concept", name="Net revenue", org_id="org-1")
    assert record.entity_type is EntityType.CONCEPT


def test_the_index_pages_and_sweeps_see_concepts() -> None:
    """Every reader that lists the entity types (the full-index page, the connector sweep) reads them from the enum."""
    assert "concept" in {t.value for t in EntityType}
