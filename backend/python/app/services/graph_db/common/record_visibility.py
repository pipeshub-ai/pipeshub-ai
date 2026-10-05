"""Which records a graph read returns: live ones, trashed ones, or both.

A record is in the trash while ``isDeleted`` is true. Records written before
soft delete existed may lack the field, and both predicates count a missing
value as live, so no data migration is needed.

Every provider query that filters on deletion builds its clause from here, so
"live" means the same thing on ArangoDB and Neo4j and in Python.
"""

from collections.abc import Mapping
from enum import Enum


class RecordVisibility(str, Enum):
    LIVE = "LIVE"
    ALL = "ALL"
    DELETED = "DELETED"


def aql_live_record(var: str) -> str:
    return f"{var}.isDeleted != true"


def cypher_live_record(var: str) -> str:
    # `<> true` alone is null for a node without the property, and WHERE drops null.
    return f"({var}.isDeleted IS NULL OR {var}.isDeleted = false)"


def aql_record_visibility(var: str, visibility: RecordVisibility) -> str:
    """A boolean AQL expression selecting ``var`` by ``visibility``."""
    visibility = RecordVisibility(visibility)
    if visibility is RecordVisibility.LIVE:
        return aql_live_record(var)
    if visibility is RecordVisibility.DELETED:
        return f"{var}.isDeleted == true"
    return "true"


def cypher_record_visibility(var: str, visibility: RecordVisibility) -> str:
    """A boolean Cypher expression selecting ``var`` by ``visibility``."""
    visibility = RecordVisibility(visibility)
    if visibility is RecordVisibility.LIVE:
        return cypher_live_record(var)
    if visibility is RecordVisibility.DELETED:
        return f"{var}.isDeleted = true"
    return "true"


def is_live_record(record: object) -> bool:
    """The Python form of the predicate, for a stored document or a ``Record``."""
    if isinstance(record, Mapping):
        return record.get("isDeleted") is not True
    return getattr(record, "is_deleted", False) is not True


def matches_visibility(record: object, visibility: RecordVisibility) -> bool:
    visibility = RecordVisibility(visibility)
    if visibility is RecordVisibility.ALL:
        return True
    live = is_live_record(record)
    return live if visibility is RecordVisibility.LIVE else not live
