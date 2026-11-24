"""Which records a graph read returns: live ones, trashed ones, or both.

A record is in the trash while ``isDeleted`` is true. Records written before
soft delete existed may lack the field, and both predicates count a missing
value as live, so no data migration is needed.

Every provider query that filters on deletion builds its clause from here, so
"live" means the same thing on ArangoDB and Neo4j and in Python.
"""

from collections.abc import Mapping
from enum import Enum

from app.config.constants.arangodb import ProgressStatus


class RecordVisibility(str, Enum):
    LIVE = "LIVE"
    ALL = "ALL"
    DELETED = "DELETED"


def aql_live_record(var: str) -> str:
    return f"{var}.isDeleted != true"


def cypher_live_record(var: str) -> str:
    # `<> true` alone is null for a node without the property, and WHERE drops null.
    return f"({var}.isDeleted IS NULL OR {var}.isDeleted = false)"


# Set when indexing completes and cleared only when the record turns out empty, so
# a record queued again after an edit keeps serving its previous indexed copy.
HAS_INDEXED_COPY = "hasIndexedCopy"


def indexed_copy_fields(indexing_status: object) -> dict[str, bool]:
    """The ``hasIndexedCopy`` write that goes with an ``indexingStatus`` write."""
    status = getattr(indexing_status, "value", indexing_status)
    if status == ProgressStatus.COMPLETED.value:
        return {HAS_INDEXED_COPY: True}
    if status == ProgressStatus.EMPTY.value:
        return {HAS_INDEXED_COPY: False}
    return {}


def is_searchable_record(record: Mapping[str, object]) -> bool:
    """The Python form of the search gate below, for a record already read."""
    return (
        record.get("indexingStatus") == ProgressStatus.COMPLETED.value
        or record.get(HAS_INDEXED_COPY) is True
    )


def is_readable_record(record: Mapping[str, object]) -> bool:
    """Whether a read by id can serve the record's stored content: searchable, or
    parsed on demand for its current revision. Search itself keeps the gate above."""
    # A record without a revision stores under None, so None matches None (as parse_now_marker does).
    return is_searchable_record(record) or (
        record.get("parseNowStatus") == "STORED" and record.get("parseNowRev") == record.get("contentRev")
    )


def aql_searchable_record(var: str, completed_param: str = "completedStatus") -> str:
    """Indexed now, or still holding a previous indexed copy. Needs ``@<completed_param>``."""
    return f"({var}.indexingStatus == @{completed_param} OR {var}.{HAS_INDEXED_COPY} == true)"


def cypher_searchable_record(var: str, completed_param: str = "completedStatus") -> str:
    """Indexed now, or still holding a previous indexed copy. Needs ``$<completed_param>``."""
    return f"({var}.indexingStatus = ${completed_param} OR {var}.{HAS_INDEXED_COPY} = true)"


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


# The virtual record a record is indexing into while it replaces its indexed copy. The record keeps serving the copy under
# ``virtualRecordId`` until the new set commits; until then the pending vrid is held by the record so that no sweep or delete
# removes the set while it is written. Only a record that is running (IN_PROGRESS or QUEUED) holds it: any other status releases it.
PENDING_VIRTUAL_RECORD_ID = "pendingVirtualRecordId"
# NOT_STARTED too: a connector save mid-run stores it, and a record edited faster than it indexes must still commit.
PENDING_HOLDING_STATUSES = (ProgressStatus.IN_PROGRESS.value, ProgressStatus.QUEUED.value, ProgressStatus.NOT_STARTED.value)


def aql_holds_vrid(var: str, vrid_expr: str) -> str:
    """AQL: ``var`` holds the virtual record ``vrid_expr`` (a bind variable or a correlated variable): it names it, or is running into it.

    The ``!= null`` guard is what lets the optimizer use the sparse index on the pending field for a correlated or batch form."""
    statuses = ", ".join(f'"{s}"' for s in PENDING_HOLDING_STATUSES)
    return (
        f"({var}.virtualRecordId == {vrid_expr} OR ({var}.{PENDING_VIRTUAL_RECORD_ID} != null "
        f"AND {var}.{PENDING_VIRTUAL_RECORD_ID} == {vrid_expr} AND {var}.indexingStatus IN [{statuses}]))"
    )


def cypher_holds_vrid(var: str, vrid_expr: str) -> str:
    """Cypher form of :func:`aql_holds_vrid`."""
    statuses = ", ".join(f"'{s}'" for s in PENDING_HOLDING_STATUSES)
    return (
        f"({var}.virtualRecordId = {vrid_expr} OR ({var}.{PENDING_VIRTUAL_RECORD_ID} = {vrid_expr} "
        f"AND {var}.indexingStatus IN [{statuses}]))"
    )


def sql_holds_vrid(alias: str, vrid_expr: str) -> str:
    """SQL form (``graph.nodes`` rows). Repeats the partial-index predicate so the planner can use it."""
    statuses = ", ".join(f"'{s}'" for s in PENDING_HOLDING_STATUSES)
    return (
        f"({alias}.doc->>'virtualRecordId' = {vrid_expr} OR ({alias}.doc ? '{PENDING_VIRTUAL_RECORD_ID}' "
        f"AND {alias}.doc->>'{PENDING_VIRTUAL_RECORD_ID}' = {vrid_expr} AND {alias}.doc->>'indexingStatus' IN ({statuses})))"
    )


def holds_vrid(record: Mapping[str, object], vrid: str) -> bool:
    """The Python form, for a stored document."""
    if record.get("virtualRecordId") == vrid:
        return True
    return record.get(PENDING_VIRTUAL_RECORD_ID) == vrid and record.get("indexingStatus") in PENDING_HOLDING_STATUSES
