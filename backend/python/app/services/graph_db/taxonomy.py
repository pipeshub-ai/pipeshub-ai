"""Taxonomy collections shared by both graph providers.

Category, subcategory, topic and language nodes are the collections the
indexing-side entity resolver creates per org (see
``app.modules.entity_resolution``). Keeping the set and the level mapping in
one place keeps the Arango and Neo4j providers in parity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

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

# Spellings kept per taxonomy node. A spelling past the cap is never stored,
# so every later record that uses it asks the merge model again; 20 was hit
# by popular nodes. Aliases are not shown to users (KG-17) or embedded, and
# the merge prompt shows only a few per candidate, so a larger cap costs
# payload and alias-node count only.
MAX_TAXONOMY_ALIASES = 200

# Set on a node merged into another (app.modules.entity_resolution.consolidation);
# lookups skip it and the resolver follows it to the winner.
MERGED_INTO_FIELD = "mergedInto"
# A redirect chain longer than this is a corrupt graph, not a real history.
MAX_MERGE_REDIRECT_HOPS = 16

# Each subcategory level's parent collection over interCategoryRelations.
CATEGORY_HIERARCHY_PARENTS: dict[str, str] = {
    CollectionNames.SUBCATEGORIES1.value: CollectionNames.CATEGORIES.value,
    CollectionNames.SUBCATEGORIES2.value: CollectionNames.SUBCATEGORIES1.value,
    CollectionNames.SUBCATEGORIES3.value: CollectionNames.SUBCATEGORIES2.value,
}
_HIERARCHY_EDGE_NAMESPACE = uuid.UUID("6f6a0f53-1f3e-4c1b-9c55-6c2b2f0e8a11")


_DEPARTMENT_NAMESPACE = uuid.UUID("0b7d1c52-8e0f-4f8e-9a3b-5d2a6f1c7e44")


def global_department_key(department_name: str) -> str:
    """Deterministic key of the global (org-less) department seeded for
    ``department_name``, so services seeding at once create one node."""
    return str(uuid.uuid5(_DEPARTMENT_NAMESPACE, department_name))


def hierarchy_edge_key(child_key: str, parent_key: str) -> str:
    """Deterministic key of the hierarchy edge from ``child_key`` to
    ``parent_key``, so concurrent writers of one edge converge on one."""
    return str(uuid.uuid5(_HIERARCHY_EDGE_NAMESPACE, f"{child_key}->{parent_key}"))


# Edges indexing enrichment writes from a record. Every record delete removes
# these, so an edge never points at a record that is gone; a new enrichment edge
# joins this tuple instead of being added path by path.
RECORD_ENRICHMENT_EDGE_COLLECTIONS: tuple[str, ...] = (
    CollectionNames.BELONGS_TO_DEPARTMENT.value,
    CollectionNames.BELONGS_TO_CATEGORY.value,
    CollectionNames.BELONGS_TO_LANGUAGE.value,
    CollectionNames.BELONGS_TO_TOPIC.value,
)

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
    "RECORD_ENRICHMENT_EDGE_COLLECTIONS",
    "TAXONOMY_COLLECTIONS",
    "TAXONOMY_EDGE_COLLECTIONS",
    "TAXONOMY_ENTITY_TYPES",
    "TaxonomyLink",
    "alias_pairs",
    "is_taxonomy_collection",
    "edge_spellings",
    "own_record_labels",
    "record_spelling",
    "record_spellings",
    "subcategory_level",
    "taxonomy_links",
]


EDGE_PROVENANCE_FIELDS = frozenset({"mergedFrom", "migratedFrom"})


def check_edge_move(collection: str, from_key: str, to_key: str, org_id: str, provenance: str) -> None:
    """Validate a ``move_taxonomy_edges`` call before it touches the graph."""
    if provenance not in EDGE_PROVENANCE_FIELDS:
        raise ValueError(f"{provenance!r} is not an edge provenance field")
    if not is_taxonomy_collection(collection):
        raise ValueError(f"{collection!r} is not a taxonomy collection")
    if not from_key or not to_key or not org_id:
        raise ValueError("moving taxonomy edges needs both keys and an org")
    if from_key == to_key:
        raise ValueError("cannot move taxonomy edges onto the same node")


def check_edge_move_target(
    collection: str, to_key: str, org_id: str, *, found: bool, target_org: str | None,
    provenance: str, only_merged_from: str | None,
) -> None:
    """An org's edges may only land on that org's node, or back on the
    legacy node (no ``orgId``) they were migrated from."""
    if not found:
        raise ValueError(f"{collection}/{to_key} not found")
    if target_org == org_id:
        return
    if target_org is None and provenance == "migratedFrom" and only_merged_from == to_key:
        return
    raise ValueError(f"{collection}/{to_key} is not a node of org {org_id}")


def edge_spellings(extracted_name: object, extracted_names: object) -> list[str]:
    """The raw spellings an edge records for its record, first one first:
    ``extractedNames`` when present, else ``extractedName``."""
    raw = extracted_names if isinstance(extracted_names, list) else [extracted_name]
    spellings: list[str] = []
    for value in raw:
        if isinstance(value, str) and value.strip() and value not in spellings:
            spellings.append(value)
    if not spellings and isinstance(extracted_name, str) and extracted_name.strip():
        spellings.append(extracted_name)
    return spellings


def record_spellings(
    name: str | None,
    extracted_name: object,
    extracted_names: object = None,
    *,
    canonical: bool,
    migrated: bool,
) -> list[str]:
    """How a record's own content spells the node one of its edges reaches,
    every spelling it used, or ``[]`` when the edge does not say.

    The edge's ``extractedNames`` (or ``extractedName``) are those spellings.
    Without them, a legacy node's name is one, since a legacy node was created
    from the exact name each of its records extracted, and so is a canonical
    node's on an edge migrated off a legacy node of the same name. Any other
    canonical node is named by whichever record created it.
    """
    from app.modules.entity_resolution.normalizer import display_form, normalize_name

    raw = edge_spellings(extracted_name, extracted_names)
    if not raw and name and (not canonical or migrated):
        raw = [name]
    shown: list[str] = []
    seen: set[str] = set()
    for value in raw:
        cleaned = display_form(value)
        if cleaned and normalize_name(cleaned) not in seen:
            seen.add(normalize_name(cleaned))
            shown.append(cleaned)
    return shown


def record_spelling(
    name: str | None, extracted_name: object, *, canonical: bool, migrated: bool,
) -> str | None:
    """The first of :func:`record_spellings`, or ``None``."""
    spellings = record_spellings(name, extracted_name, canonical=canonical, migrated=migrated)
    return spellings[0] if spellings else None


@dataclass(frozen=True)
class TaxonomyLink:
    """One record's ``belongsTo*`` edge to a category, subcategory, topic or
    language node, as ``get_record_taxonomy_links`` returns it."""

    record_id: str
    collection: str
    entity_id: str
    name: str
    # Every raw spelling the edge records for its record, first one first.
    extracted_names: tuple[str, ...]
    # The node is a per-org canonical node (it has a normalizedName).
    canonical: bool
    # The edge was moved off a legacy node of the same name.
    migrated: bool

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TaxonomyLink | None:
        record_id, entity_id = row.get("recordId"), row.get("entityId")
        collection = row.get("collection")
        if not record_id or not entity_id or not is_taxonomy_collection(collection or ""):
            return None
        return cls(
            record_id=str(record_id),
            collection=str(collection),
            entity_id=str(entity_id),
            name=str(row.get("name") or ""),
            extracted_names=tuple(edge_spellings(row.get("extractedName"), row.get("extractedNames"))),
            canonical=bool(row.get("canonical")),
            migrated=bool(row.get("migrated")),
        )

    @property
    def extracted_name(self) -> str | None:
        return self.extracted_names[0] if self.extracted_names else None

    @property
    def spellings(self) -> tuple[str, ...]:
        return tuple(record_spellings(
            self.name, self.extracted_name, list(self.extracted_names),
            canonical=self.canonical, migrated=self.migrated,
        ))

    @property
    def spelling(self) -> str | None:
        return self.spellings[0] if self.spellings else None


def taxonomy_links(rows: list[dict[str, Any]] | None) -> list[TaxonomyLink]:
    return [link for row in rows or [] if (link := TaxonomyLink.from_row(row)) is not None]


# Keys of a record's metadata read whose items are taxonomy nodes the record
# was extracted into; departments are the org's own list and are left alone.
_RECORD_LABEL_KEYS = ("categories", "subcategories1", "subcategories2", "subcategories3", "topics", "languages")


def own_record_labels(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """A record's ``{departments, categories, ..., languages}`` read with each
    taxonomy item named as the record's own extraction spells it.

    Items arrive as ``{id, name, extractedName, extractedNames, canonical,
    migrated}``. A node the record spells two ways is listed under each
    spelling; an item whose edge does not record the spelling is left out.
    """
    if not isinstance(metadata, dict):
        return metadata
    shown = dict(metadata)
    for key in _RECORD_LABEL_KEYS:
        items: list[dict[str, Any]] = []
        for item in metadata.get(key) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            items.extend(
                {"id": item["id"], "name": spelling}
                for spelling in record_spellings(
                    item.get("name"), item.get("extractedName"), item.get("extractedNames"),
                    canonical=bool(item.get("canonical")), migrated=bool(item.get("migrated")),
                )
            )
        shown[key] = items
    return shown
