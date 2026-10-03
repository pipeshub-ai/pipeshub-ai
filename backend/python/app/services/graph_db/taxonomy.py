"""Taxonomy collections shared by both graph providers.

Category, subcategory, topic and language nodes are the collections the
indexing-side entity resolver creates per org (see
``app.modules.entity_resolution``). Keeping the set and the level mapping in
one place keeps the Arango and Neo4j providers in parity.
"""

from __future__ import annotations

from app.config.constants.arangodb import CollectionNames

SUBCATEGORY_LEVELS: dict[str, str] = {
    CollectionNames.SUBCATEGORIES1.value: "1",
    CollectionNames.SUBCATEGORIES2.value: "2",
    CollectionNames.SUBCATEGORIES3.value: "3",
}

TAXONOMY_COLLECTIONS: frozenset[str] = frozenset(
    {
        CollectionNames.CATEGORIES.value,
        CollectionNames.TOPICS.value,
        CollectionNames.LANGUAGES.value,
        *SUBCATEGORY_LEVELS,
    }
)

# Set on a node merged into another (app.modules.entity_resolution.consolidation);
# lookups skip it and the resolver follows it to the winner.
MERGED_INTO_FIELD = "mergedInto"
# A redirect chain longer than this is a corrupt graph, not a real history.
MAX_MERGE_REDIRECT_HOPS = 16

# The edge collection a record reaches each taxonomy collection over.
TAXONOMY_EDGE_COLLECTIONS: dict[str, str] = {
    CollectionNames.CATEGORIES.value: CollectionNames.BELONGS_TO_CATEGORY.value,
    **dict.fromkeys(SUBCATEGORY_LEVELS, CollectionNames.BELONGS_TO_CATEGORY.value),
    CollectionNames.TOPICS.value: CollectionNames.BELONGS_TO_TOPIC.value,
    CollectionNames.LANGUAGES.value: CollectionNames.BELONGS_TO_LANGUAGE.value,
}

# Entity types records reach over a belongsTo* edge (departments included).
TAXONOMY_ENTITY_TYPES: frozenset[str] = frozenset(
    {"department", "category", "subcategory", "topic", "language"}
)

def subcategory_level(collection: str | None) -> str | None:
    """The subcategory level of ``collection``, or ``None`` for other taxonomy."""
    if not collection:
        return None
    return SUBCATEGORY_LEVELS.get(collection)


def is_taxonomy_collection(collection: str) -> bool:
    return collection in TAXONOMY_COLLECTIONS


def alias_pairs(aliases: list[str], normalized_aliases: list[str]) -> list[tuple[str, str]]:
    """Zip display aliases with their normalized forms, dropping blanks and
    repeats by normalized form, so both providers union the same pairs."""
    if len(aliases or []) != len(normalized_aliases or []):
        raise ValueError("aliases and normalized_aliases must align by position")
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for display, normalized in zip(aliases or [], normalized_aliases or []):
        if not display or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        pairs.append((display, normalized))
    return pairs


__all__ = [
    "MAX_MERGE_REDIRECT_HOPS",
    "MERGED_INTO_FIELD",
    "SUBCATEGORY_LEVELS",
    "TAXONOMY_COLLECTIONS",
    "TAXONOMY_EDGE_COLLECTIONS",
    "TAXONOMY_ENTITY_TYPES",
    "alias_pairs",
    "is_taxonomy_collection",
    "subcategory_level",
]
