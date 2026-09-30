"""Data shapes shared by the resolver, the graph transformer and the tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityType
from app.modules.entity_resolution.normalizer import normalize_name

MAX_ALIASES_PER_NODE = 20


class ResolutionMode(str, Enum):
    """How the resolver behaves for a record.

    ``OFF`` skips resolution entirely. ``SHADOW`` computes and logs every
    decision but writes nothing and leaves the extracted names untouched.
    ``APPLY`` rewrites the record's metadata to canonical names and hands the
    graph transformer the nodes to use.
    """

    OFF = "off"
    SHADOW = "shadow"
    APPLY = "apply"


@dataclass(frozen=True)
class TaxonomyKind:
    """One resolvable slot of the semantic metadata.

    ``slot`` names the metadata field, ``collection`` the graph collection the
    canonical node lives in, and ``level`` the subcategory level. Names only
    ever resolve within one kind, so a level-1 subcategory is never a
    candidate for a level-2 one and a topic never merges with a category.
    """

    slot: str
    collection: str
    entity_type: EntityType
    level: str | None = None


CATEGORY = TaxonomyKind("category", CollectionNames.CATEGORIES.value, EntityType.CATEGORY)
SUBCATEGORY_1 = TaxonomyKind(
    "subcategory1", CollectionNames.SUBCATEGORIES1.value, EntityType.SUBCATEGORY, "1"
)
SUBCATEGORY_2 = TaxonomyKind(
    "subcategory2", CollectionNames.SUBCATEGORIES2.value, EntityType.SUBCATEGORY, "2"
)
SUBCATEGORY_3 = TaxonomyKind(
    "subcategory3", CollectionNames.SUBCATEGORIES3.value, EntityType.SUBCATEGORY, "3"
)
TOPIC = TaxonomyKind("topic", CollectionNames.TOPICS.value, EntityType.TOPIC)
LANGUAGE = TaxonomyKind("language", CollectionNames.LANGUAGES.value, EntityType.LANGUAGE)

SUBCATEGORY_CHAIN: tuple[TaxonomyKind, ...] = (SUBCATEGORY_1, SUBCATEGORY_2, SUBCATEGORY_3)
RESOLVED_KINDS: tuple[TaxonomyKind, ...] = (CATEGORY, *SUBCATEGORY_CHAIN, TOPIC)
KINDS_BY_COLLECTION: dict[str, TaxonomyKind] = {
    kind.collection: kind for kind in (*RESOLVED_KINDS, LANGUAGE)
}


@dataclass
class ExtractedName:
    """One name as extraction produced it, after cleaning and in-record dedupe."""

    index: int
    kind: TaxonomyKind
    raw: str
    display: str
    normalized: str


@dataclass(frozen=True)
class WinnerCandidate:
    """The nearest existing entity of the same kind, offered to the model."""

    entity_id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass
class ResolvedEntity:
    """The canonical node one or more extracted names resolved to."""

    kind: TaxonomyKind
    key: str
    name: str
    normalized: str
    is_new: bool
    decision: str
    aliases: list[str] = field(default_factory=list)
    new_aliases: list[str] = field(default_factory=list)
    extracted_names: list[str] = field(default_factory=list)

    @property
    def extracted_name(self) -> str:
        """The first extracted spelling, written on this record's edge."""
        return self.extracted_names[0] if self.extracted_names else self.name


@dataclass
class ResolutionStats:
    names_seen: int = 0
    names_dropped: int = 0
    names_deduped: int = 0
    tier0_hits: int = 0
    winners_offered: int = 0
    model_calls: int = 0
    model_failures: int = 0
    vector_failures: int = 0
    rejected_decisions: int = 0
    merges: int = 0
    in_record_merges: int = 0
    new_nodes: int = 0
    alias_cap_hits: int = 0
    latency_ms: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class EntityResolution:
    """The resolver's output for one record.

    ``entries`` is keyed by ``(collection, normalized canonical name)``, which
    is how the graph transformer looks a rewritten metadata name back up.
    """

    org_id: str
    mode: ResolutionMode
    stats: ResolutionStats = field(default_factory=ResolutionStats)
    entries: dict[tuple[str, str], ResolvedEntity] = field(default_factory=dict)
    assignments: dict[int, ResolvedEntity] = field(default_factory=dict)

    def add(self, entity: ResolvedEntity) -> None:
        self.entries[(entity.kind.collection, entity.normalized)] = entity

    def get(self, collection: str, name: str) -> ResolvedEntity | None:
        return self.entries.get((collection, normalize_name(name)))

    def decisions_for_log(self) -> list[dict[str, Any]]:
        return [
            {
                "slot": entity.kind.slot,
                "extracted": list(entity.extracted_names),
                "decision": entity.decision,
                "key": entity.key,
                "name": entity.name,
                "new": entity.is_new,
                "newAliases": list(entity.new_aliases),
            }
            for entity in self.entries.values()
        ]


class MergeDecision(BaseModel):
    """One pairwise answer from the model.

    ``same`` with ``target`` set means the extracted name is the offered
    winner. ``same`` with ``same_as_item`` set means it is the same concept as
    another item of this record. ``same`` false means a new entity, with an
    optional cleaned display form in ``canonical_name``.
    """

    i: int = Field(description="Index of the item this decision is for")
    same: bool = Field(description="True when the name denotes an existing entity")
    target: str = Field(default="", description="The offered winner id when same is true")
    same_as_item: int = Field(
        default=-1, description="Index of another item this name is the same concept as"
    )
    canonical_name: str = Field(
        default="", description="Cleaned display form when the name is new; may be empty"
    )


class MergeDecisions(BaseModel):
    decisions: list[MergeDecision] = Field(default_factory=list)


__all__ = [
    "CATEGORY",
    "KINDS_BY_COLLECTION",
    "LANGUAGE",
    "MAX_ALIASES_PER_NODE",
    "RESOLVED_KINDS",
    "SUBCATEGORY_1",
    "SUBCATEGORY_2",
    "SUBCATEGORY_3",
    "SUBCATEGORY_CHAIN",
    "TOPIC",
    "EntityResolution",
    "ExtractedName",
    "MergeDecision",
    "MergeDecisions",
    "ResolutionMode",
    "ResolutionStats",
    "ResolvedEntity",
    "TaxonomyKind",
    "WinnerCandidate",
]
