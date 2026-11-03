"""Taxonomy entity resolution for the indexing pipeline.

Extraction returns free-text categories, subcategories and topics. Without
resolution every spelling ("bug bash testing", "Bug bash testing", "bug bash
testing session") becomes its own graph node and its own vector point. The
:class:`EntityResolver` maps each extracted name to one canonical, per-org
node before anything is written, in three tiers:

1. exact match on the normalized name (indexed graph lookup),
2. the single nearest existing entity of the same type and level (hybrid
   vector search, no score threshold),
3. one structured model call per record that decides, pair by pair, whether
   the extracted name and its nearest entity are the same concept.

See ``docs/entity-resolution.md`` for the design and the operating modes.
"""

from app.modules.entity_resolution.models import (
    EntityResolution,
    ResolutionMode,
    ResolutionStats,
    ResolvedEntity,
    TaxonomyKind,
)
from app.modules.entity_resolution.resolver import EntityResolver

__all__ = [
    "EntityResolution",
    "EntityResolver",
    "ResolutionMode",
    "ResolutionStats",
    "ResolvedEntity",
    "TaxonomyKind",
]
