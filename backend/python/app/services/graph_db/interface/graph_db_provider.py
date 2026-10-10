"""
Comprehensive Graph Database Provider Interface

This interface defines all database operations needed by the application,
abstracting away the specific database implementation (ArangoDB, Neo4j, etc.).

All methods support optional transaction parameter for atomic operations.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol

from app.config.constants.arangodb import (
    CollectionNames,
    DeleteSource,
    ProgressStatus,
    RecordRelations,
)
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import Person
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.taxonomy import MAX_TAXONOMY_ALIASES
from app.utils.kh_breadcrumbs import browse_scope
from app.utils.kh_chain_tops import place_chain_tops
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Set once `stamp_kh_listing_state` has covered every node written before.
KH_LISTING_STATE_FLAG = "/migrations/kh_listing_state_v1"

# Hierarchy hops the access check walks. Whatever lists what the check may admit
# has to follow inheritance at least this far, or deeper records drop out unseen.
ACCESS_WALK_MAX_DEPTH = 50

FOLDER_CHANGED_DURING_DELETE_MESSAGE = (
    "Records were moved into this folder while it was being deleted, so nothing was deleted. "
    "Try the delete again."
)


class FolderChangedDuringDelete(RuntimeError):
    """Records were moved into a folder while it was being deleted; nothing was deleted."""


class MoveDestinationMissing(RuntimeError):
    """The parent a record was being moved under is not in the graph, or is in the trash; nothing was written."""

    def __init__(self, record_id: str, parent_record_id: str) -> None:
        super().__init__(
            f"Record {record_id} was not moved: its new parent {parent_record_id} "
            "is not in the graph or is in the trash"
        )


@dataclass(frozen=True)
class AccessCheck:
    """What ``IGraphDBProvider.check_access`` found accessible."""

    #: The asked node ids the user may access.
    node_ids: frozenset[str] = frozenset()
    #: Each accessible virtual record id, with the record to cite for it.
    records_by_vrid: dict[str, str] = field(default_factory=dict)
    #: The accessible asked nodes that every given scope admits (all of
    #: ``node_ids`` when no scope was given).
    node_ids_in_scope: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AccessibleContainers:
    """The containers a user may search, in place of enumerating their records.

    Consumed as a vector-DB predicate::

        orgId == org_id
        AND (connectorIds IN app_ids
             OR recordGroupIds IN (trusted | verify)
             OR rootRecordGroupIds IN root_group_ids
             OR virtualRecordId IN direct_records)

    Every field *widens* — the filter admits records the user cannot read, and
    the search verifier (``check_access``) is what makes the answer exact.
    That asymmetry is the whole design: a container the user cannot reach costs
    precision, a container wrongly omitted costs recall with no error to notice.
    So bounds below fail over to ``fallback_reason`` rather than truncating.

    ``trusted`` vs ``verify`` is the only performance lever. A group lands in
    ``trusted`` solely when it declares ``PermissionModel.RECORD_GROUP_LEVEL``; unset —
    which is every group until a connector says otherwise — means ``verify``.

    Collections/KBs arrive in ``app_ids``, never in the group sets: a KB record
    carries ``connectorIds = [kbId]`` and an empty ``recordGroupIds`` by design
    (see ``services.vector_db.membership._record_group_id_from_edge``). Routing
    them into the group sets makes every uploaded document invisible.

    Note the empty convention inverts ``get_accessible_connector_types``, where
    empty means "could not narrow". Here empty with ``fallback_reason is None``
    means the user genuinely reaches nothing; "could not narrow" is
    ``fallback_reason``.

    ``scope_connector_ids`` echoes the request's ``apps`` ∪ ``kb`` scope the
    sets were narrowed to (None when unscoped). Scope only ever removes
    containers; the caller compares the echo against its own reading of the
    request, so a provider that ignored the scope cannot silently widen it.
    """

    app_ids: frozenset[str] = frozenset()
    #: The subset of ``app_ids`` whose connector declares ``APP_LEVEL``
    app_ids_trusted: frozenset[str] = frozenset()
    record_group_ids_trusted: frozenset[str] = frozenset()
    record_group_ids_verify: frozenset[str] = frozenset()
    direct_records: Mapping[str, str] = field(default_factory=dict)
    #: Groups matched against a record's root instead of its own group, for
    #: connectors where every descendant inherits (Slack: channels, so a
    #: workspace costs one id per channel rather than one per thread).
    root_group_ids: frozenset[str] = frozenset()
    fallback_reason: str | None = None
    scope_connector_ids: frozenset[str] | None = None

    @property
    def record_group_ids(self) -> frozenset[str]:
        """Both group sets, as the vector filter sees them — it cannot tell them
        apart, and separating them there would only cost a clause."""
        return self.record_group_ids_trusted | self.record_group_ids_verify

    @property
    def is_empty(self) -> bool:
        return not (
            self.app_ids
            or self.record_group_ids_trusted
            or self.record_group_ids_verify
            or self.root_group_ids
            or self.direct_records
        )

    @property
    def usable(self) -> bool:
        """Whether a container filter may be built from this at all."""
        return self.fallback_reason is None and not self.is_empty


#: Filter keys that name containers (connector and Collection app ids) rather
#: than record-level predicates. Treated as one scope: ``apps`` ∪ ``kb``.
CONTAINER_SCOPE_FILTER_KEYS = ("apps", "kb")

#: A control flag rather than a filter: a project-scoped chat sets it so an
#: empty ``apps``/``kb`` selection means "search nothing" instead of falling
#: back to everything the user can reach. Carried inside ``filters`` by
#: `ChatQuery.strictScope` (see `api/routes/chatbot.py`).
STRICT_SCOPE_FILTER_KEY = "strictScope"

#: Filter keys that select below app level: a record group with its nested
#: groups, a folder or record with everything under it, and one record alone.
SELECTION_FILTER_KEYS = ("recordGroups", "records", "recordsExact")

#: Set by the server, never taken from a client: the apps a selection touches,
#: and the bounds a cited record must lie in, each in the shape of a selection:
#: a saved agent's sources, and a project's.
SELECTION_APPS_FILTER_KEY = "selectionApps"
AGENT_BOUND_FILTER_KEYS = {
    "allowedApps": "apps",
    "allowedRecordGroups": "recordGroups",
    "allowedRecords": "records",
}
PROJECT_BOUND_FILTER_KEYS = {
    "projectApps": "apps",
    "projectRecordGroups": "recordGroups",
    "projectRecords": "records",
}
ALLOWED_FILTER_KEYS = {**AGENT_BOUND_FILTER_KEYS, **PROJECT_BOUND_FILTER_KEYS}


class RowScope(Protocol):
    """Decides whether a ``check_access`` row lies inside a selection."""

    def admits(self, row: Mapping[str, Any]) -> bool: ...

#: Set on a record by ``update_queued_duplicates_status`` in the same write
#: that promotes its queued duplicates, and cleared by the indexing handler once
#: their taxonomy has been copied. Declared in the strict records schema.
DUPLICATE_RECONCILE_PENDING_FIELD = "duplicateReconcilePending"
# When the retry sweep may next pick a pending primary up (epoch ms), and how
# many retries failed; both re-armed by every promotion. The due time also
# fences the flag clear: a sweep clears only the promotion it read.
DUPLICATE_RECONCILE_DUE_AT_FIELD = "duplicateReconcileDueAt"
DUPLICATE_RECONCILE_ATTEMPTS_FIELD = "duplicateReconcileAttempts"
# The record handler reconciles within seconds of a promotion; only after this
# is the primary the retry sweep's to take.
DUPLICATE_RECONCILE_GRACE_MS = 10 * 60 * 1000


def promoted_duplicate_extraction_status(
    new_indexing_status: str, primary: Mapping[str, Any]
) -> str | None:
    """``extractionStatus`` for a QUEUED duplicate promoted when ``primary`` finished,
    or None while the primary's enrichment is still IN_PROGRESS, so the duplicates
    stay QUEUED until the handler or stale recovery resumes and finishes it.

    The duplicate shares the primary's enrichment, so an indexed primary lends
    its own outcome: COMPLETED, FAILED, or NOT_STARTED when enrichment was
    deliberately deferred (an inline enrichment is IN_PROGRESS from the same
    write that marks the primary indexed). A primary with no status comes from
    before that write existed; nothing would ever resume it, so it is promoted
    as the old mapping did, COMPLETED.
    """
    if new_indexing_status == ProgressStatus.COMPLETED.value:
        primary_status = primary.get("extractionStatus")
        if primary_status == ProgressStatus.IN_PROGRESS.value:
            return None
        return primary_status or ProgressStatus.COMPLETED.value
    if new_indexing_status == ProgressStatus.EMPTY.value:
        return ProgressStatus.EMPTY.value
    return ProgressStatus.FAILED.value


def requested_scope_ids(filters: "Mapping[str, Any] | None") -> tuple[str, ...] | None:
    """The app ids a request is scoped to, or None when it is unscoped.

    ``apps`` and ``kb`` are one scope: a Collection id is honoured under either
    key, as is a connector id. Ordered (``apps`` first) and de-duplicated,
    because the record-id path resolves a shared virtualRecordId to whichever
    scoped app it queries first.

    Unscoped only when both keys are absent, None or an empty list. Anything
    else is scoped, and values that cannot be an app id contribute nothing — so
    ``[""]``, ``[None]`` or a bare string narrow to nothing rather than widening
    to everything. ``NO_KB_SELECTED`` is kept as-is: it matches no app, which is
    exactly what an agent sending it alone means.
    """
    filters = filters or {}
    raw_values = [filters.get(key) for key in CONTAINER_SCOPE_FILTER_KEYS]
    # `strictScope` is deliberately not part of the scope: it says how an
    # EMPTY scope must be read, not which containers were asked for.
    if all(value is None or (isinstance(value, (list, tuple)) and not value) for value in raw_values):
        return None

    ordered: dict[str, None] = {}
    for value in raw_values:
        if not isinstance(value, (list, tuple)):
            continue
        for item in value:
            if isinstance(item, str) and item:
                ordered.setdefault(item, None)
    return tuple(ordered)


def _unsupported_container_filters(
    filters: "dict[str, list[str]] | None",
    time_range: "dict[str, int] | None",
) -> str | None:
    """Why this request cannot be expressed as containers, or None if it can.

    ``apps`` and ``kb`` are supported: they name containers, and both the
    container query and the verifier intersect against them. ``strictScope``
    is a control flag the container query honours too (an empty scope under it
    reaches nothing rather than everything). The rest — departments,
    categories, languages, topics — and any time range are record-level
    predicates with no container equivalent at all.
    """
    if time_range:
        return "unsupported_filter:time_range"
    present = sorted(
        key
        for key, values in (filters or {}).items()
        if values
        and key not in CONTAINER_SCOPE_FILTER_KEYS
        and key != STRICT_SCOPE_FILTER_KEY
    )
    if present:
        return f"unsupported_filters:{','.join(present)}"
    return None


def _containers_from_row(
    row: "dict | None",
    *,
    logger: Any,
    scope_connector_ids: frozenset[str] | None,
) -> AccessibleContainers:
    """Turn one provider result row into ``AccessibleContainers``.

    Shared because the bounds are correctness, not tuning: a backend that
    truncated where the other fell back would answer the same permission
    question differently. Every bound here fails over rather than trimming.
    """
    from app.services.graph_db.common.utils import (
        CONTAINER_FILTER_MAX_TERMS,
        MAX_DIRECT_GRANT_RECORDS,
    )

    if not isinstance(row, dict):
        # No row means the user document did not resolve. Distinct from "reaches
        # nothing", which is a row with empty sets.
        return AccessibleContainers(fallback_reason="user_not_found")

    unsafe = [str(a) for a in (row.get("unsafeApps") or []) if a]
    if unsafe:
        # Points for a connector whose membership arrays were never written (or
        # whose backfill gave up) carry empty connectorIds/recordGroupIds, so a
        # container filter cannot see them at all. All-or-nothing per request:
        # a mixed filter would be dominated by the unsafe half on day one.
        return AccessibleContainers(
            fallback_reason=f"membership_not_backfilled:{unsafe[0]}"
        )

    direct_rows = [r for r in (row.get("direct") or []) if isinstance(r, dict)]
    if len(direct_rows) > MAX_DIRECT_GRANT_RECORDS:
        logger.warning(
            "get_accessible_containers: %d direct-grant records exceeds %d; "
            "falling back to record ids. A connector is granting per record "
            "without creating record groups.",
            len(direct_rows),
            MAX_DIRECT_GRANT_RECORDS,
        )
        return AccessibleContainers(
            fallback_reason=f"direct_grant_overflow:{len(direct_rows)}"
        )

    app_ids = frozenset(str(a) for a in (row.get("appIds") or []) if a)
    # Intersected with app_ids rather than taken at face value: a backend that
    # forgets the new key yields an empty set and simply verifies everything,
    # which is the safe direction.
    app_ids_trusted = frozenset(
        str(a) for a in (row.get("trustedApps") or []) if a
    ) & app_ids
    trusted = frozenset(str(g) for g in (row.get("trusted") or []) if g)
    verify = frozenset(str(g) for g in (row.get("verify") or []) if g)
    root_groups = frozenset(str(g) for g in (row.get("rootGroups") or []) if g)
    direct = {
        str(r["vid"]): str(r["rid"])
        for r in direct_rows
        if r.get("vid") and r.get("rid")
    }

    total = len(app_ids) + len(trusted) + len(verify) + len(root_groups) + len(direct)
    if total > CONTAINER_FILTER_MAX_TERMS:
        logger.warning(
            "get_accessible_containers: %d filter terms exceeds %d; "
            "falling back to record ids.",
            total,
            CONTAINER_FILTER_MAX_TERMS,
        )
        return AccessibleContainers(fallback_reason=f"too_many_terms:{total}")

    return AccessibleContainers(
        app_ids=app_ids,
        app_ids_trusted=app_ids_trusted,
        record_group_ids_trusted=trusted,
        record_group_ids_verify=verify,
        root_group_ids=root_groups,
        direct_records=direct,
        scope_connector_ids=scope_connector_ids,
    )

if TYPE_CHECKING:
    from fastapi import Request

    from app.models.entities import (
        AppRole,
        AppUser,
        AppUserGroup,
        FileRecord,
        Record,
        RecordGroup,
        User,
    )
    from app.services.graph_db.common.utils import (
        EntityCandidateRows,
        PermittedEntityRows,
    )


def _distinct_connector_types(apps: "list[dict] | None") -> list[str]:
    """Distinct ``type`` values from a list of app documents, order preserved.

    Shared by every provider: the app documents have the same shape whichever
    graph returned them, so the extraction belongs beside the contract rather
    than duplicated per backend. Apps without a type are skipped — an
    untyped app cannot narrow anything, and guessing one would narrow the
    search to a collection that does not exist.
    """
    types: list[str] = []
    seen: set[str] = set()
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        app_type = app.get("type")
        if not app_type:
            continue
        value = str(app_type)
        if value not in seen:
            seen.add(value)
            types.append(value)
    return types


class IGraphDBProvider(ABC):
    """
    Comprehensive interface for graph database operations.

    This interface abstracts all database operations used throughout the application,
    allowing for multiple database implementations (ArangoDB, Neo4j, etc.) to be
    swapped via configuration.

    Design Principles:
    - All methods are database-agnostic (generic terms like 'document', 'collection', 'edge')
    - Transaction support is optional but consistent across all operations
    - Methods return Python native types (Dict, List) not database-specific objects
    - Error handling returns None/False rather than raising exceptions (where appropriate)

    Data Format Specifications:

    1. Node/Document Format:
       Nodes use a generic 'id' field for identification (not database-specific like '_key').
       Example:
       {
           "id": "user123",              # Generic node identifier
           "orgId": "org456",
           "email": "user@example.com",
           # ... other node properties
       }

       Implementation Note: Providers translate 'id' to their native field:
       - ArangoDB: 'id' → '_key'
       - Neo4j: 'id' → 'id' (native)

    2. Edge/Relationship Format:
       Edges use a generic format with separate fields for source/target nodes:
       {
           "from_id": "user123",           # Source node ID (without collection prefix)
           "from_collection": "users",     # Source collection/label name
           "to_id": "record456",           # Target node ID (without collection prefix)
           "to_collection": "records",     # Target collection/label name
           "role": "READER",               # Edge property example
           "type": "PERMISSION",           # Edge property example
           "createdAtTimestamp": 1234567890,
           # ... other edge properties
       }

       Implementation Note: Providers translate to their native format:
       - ArangoDB: Combines into '_from': "users/user123", '_to': "records/record456"
       - Neo4j: Creates relationship with startNode and endNode references

    3. Collection/Label Names:
       Collection names are database-agnostic strings (e.g., "users", "records", "permissions").
       Providers map these to their native concepts (collections in Arango, labels in Neo4j).

    4. Backward Compatibility:
       During transition, providers should handle both old format (with _key, _from, _to)
       and new generic format to ensure smooth migration.
    """

    # ==================== Connection Management ====================

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to the database and initialize collections/tables.

        Returns:
            bool: True if connection successful, False otherwise
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """
        Disconnect from the database and clean up resources.

        Returns:
            bool: True if disconnection successful, False otherwise
        """
        pass

    @abstractmethod
    async def ensure_schema(self) -> bool:
        """
        Ensure database schema is initialized (collections, graphs, and any
        required seed data). Called at startup by the connector and indexing
        services, so it must be idempotent.

        Returns:
            bool: True if schema was ensured successfully, False otherwise
        """
        pass

    # ==================== Transaction Management ====================

    @abstractmethod
    def begin_transaction(self, read: list[str], write: list[str], explicit: bool | None = None) -> str:
        """
        Begin a database transaction.

        Args:
            read (List[str]): Collections/tables to read from
            write (List[str]): Collections/tables to write to
            explicit: for a backend that can run a block either as one real
                transaction or as one commit per statement, which of the two
                this block gets; None leaves it to the setting of the backend

        Returns:
            str: Transaction ID
        """
        pass

    @abstractmethod
    async def commit_transaction(self, transaction: str) -> None:
        """Commit a database transaction."""
        pass

    @abstractmethod
    async def rollback_transaction(self, transaction: str) -> None:
        """Roll back a database transaction."""
        pass

    def is_transient_error(self, error: BaseException) -> bool:
        """Whether *error* means a rolled-back transaction block can simply
        be re-run (a deadlock, a lock timeout). Backends whose transactions
        cannot guarantee nothing landed answer False."""
        return False

    def is_write_conflict(self, error: BaseException) -> bool:
        """Whether *error* came from colliding with a concurrent writer (a
        deadlock, a lock timeout, a write-write conflict). Unlike
        :meth:`is_transient_error` it says nothing about what landed, so only
        an idempotent block may be re-run on it."""
        return False

    # ==================== Document Operations ====================

    @abstractmethod
    async def get_document(
        self,
        document_key: str,
        collection: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """
        Get a document by its key from a collection.

        Args:
            document_key (str): The document's unique identifier (generic 'id')
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context
            raise_on_error (bool): Propagate the failure instead of answering
                None. Callers that read None as "this was deleted" must pass
                True, or a graph that cannot be reached reads as a deletion.

        Returns:
            Optional[Dict]: Document data with 'id' field if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_id(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> Optional["Record"]:
        """
        Get record by internal ID (_key) with associated type document (file/mail/etc.).

        Args:
            record_id: Internal record ID (_key)
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Typed Record instance (FileRecord, MailRecord, etc.) or None
        """
        pass

    @abstractmethod
    async def get_typed_records_batch(
        self,
        record_ids: list[str],
    ) -> dict[str, "Record"]:
        """Batch-fetch typed Record instances for the given record IDs.

        Returns a dict mapping record ID to typed Record (FileRecord,
        TicketRecord, etc.). IDs not found or failing typed construction
        are silently omitted from the result.
        """
        pass

    @abstractmethod
    async def get_node_depths_batch(
        self,
        parent_id: str,
        node_ids: list[str],
        max_depth: int = 3,
        parent_type: str | None = None,
    ) -> dict[str, int]:
        """Compute traversal depth for each node relative to a parent.

        For record/folder parents: traverses nodeRelations
        (PARENT_CHILD / ATTACHMENT) edges.

        For recordGroup parents: records belonging to the group are
        level 1; their children via nodeRelations are level 2+.

        For app parents: records directly under the connector's record
        groups are level 1; their children via nodeRelations are level 2+.

        Returns ``{node_id: depth}`` for every reachable node_id.
        Unreachable IDs are omitted.
        """
        pass

    @abstractmethod
    async def get_all_documents(
        self,
        collection: str,
        transaction: str | None = None,
    ) -> list[dict]:
        """
        Get all documents from a collection.

        Args:
            collection: Collection name
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of all documents in the collection
        """
        pass

    @abstractmethod
    async def get_documents_paginated(
        self,
        collection: str,
        skip: int = 0,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
        sort_field: str | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Fetch a single page of documents from a collection using database-level
        pagination, so memory usage stays proportional to `limit` regardless of
        total collection size.

        Args:
            collection:   Collection / label name.
            skip:         Number of documents to skip (offset).
            limit:        Maximum number of documents to return.
            filters:      Optional equality filters applied as AND conditions.
                          Keys are field names, values are the expected values.
            sort_field:   Optional field to sort by (ascending). When None the
                          database's natural order is used (stable per query but
                          not guaranteed across restarts).
            transaction:  Optional transaction ID.
            raise_on_error: Propagate database errors instead of returning an
                            empty page.

        Returns:
            List of document dicts for the requested page (may be shorter than
            `limit` or empty when the collection is exhausted).
        """
        pass

    @abstractmethod
    async def batch_upsert_nodes(
        self,
        nodes: list[dict],
        collection: str,
        transaction: str | None = None,
    ) -> bool | None:
        """
        Batch upsert (insert or update) multiple nodes/documents.

        Args:
            nodes (List[Dict]): List of documents to upsert. Each document should have 'id' field:
                {
                    "id": "user123",           # Generic node identifier
                    "orgId": "org456",
                    # ... other node properties
                }
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[bool]: True if successful, False otherwise, None on error
        """
        pass

    @abstractmethod
    async def delete_nodes(
        self,
        keys: list[str],
        collection: str,
        transaction: str | None = None
    ) -> bool:
        """
        Delete multiple nodes/documents by their keys.

        Args:
            keys (List[str]): List of document keys to delete
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def update_node(
        self,
        key: str,
        collection: str,
        node_updates: dict,
        transaction: str | None = None
    ) -> bool:
        """
        Update a single node/document.

        Args:
            key (str): Document key to update
            collection (str): Collection/table name
            node_updates (Dict): Fields to update
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def update_node_if_match(
        self,
        key: str,
        collection: str,
        node: dict,
        match_field: str,
        match_value: Any,
        transaction: str | None = None,
    ) -> bool:
        """Write `node` over the existing document only while `match_field`
        still equals `match_value`. One round-trip.

        Used for optimistic concurrency: a caller that last observed
        `updatedAtTimestamp=T` must not clobber a write that already moved
        the timestamp. `batch_upsert_nodes` / `update_node` are unconditional
        and cannot express that. Returns True iff the write applied; False
        if the document is missing or the field no longer matches (the
        document is left unchanged). Does not insert a new document.
        """
        pass

    @abstractmethod
    async def batch_update_nodes(
        self,
        nodes: list[dict],
        collection: str,
        transaction: str | None = None,
    ) -> bool | None:
        """
        Batch update multiple existing nodes/documents. Does NOT create new nodes.
        
        This method only updates nodes that already exist in the database.
        If a node doesn't exist, it will be skipped (not created).
        
        This is different from batch_upsert_nodes which creates nodes if they don't exist.
        Use this method in indexing service to ensure records are not accidentally created.

        Args:
            nodes (List[Dict]): List of documents to update. Each document should have 'id' or '_key' field:
                {
                    "id": "user123",           # Generic node identifier
                    "orgId": "org456",
                    # ... other node properties to update
                }
            collection (str): Collection/table name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if every input node was found and updated.
            False if any node did not exist (zero or partial matches). Providers log
            how many nodes were updated vs requested; updated document data is not returned.
            Raises on database or validation errors.
        """
        pass

    # ==================== Edge/Relationship Operations ====================

    @abstractmethod
    async def batch_create_edges(
        self,
        edges: list[dict],
        collection: str,
        transaction: str | None = None,
    ) -> bool:
        """
        Batch create edges/relationships between nodes.

        Args:
            edges (List[Dict]): List of edges in generic format:
                {
                    "from_id": "user123",           # Source node ID
                    "from_collection": "users",     # Source collection
                    "to_id": "record456",           # Target node ID
                    "to_collection": "records",     # Target collection
                    # ... additional edge properties
                }
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def create_edges_if_absent(
        self,
        edges: list[dict],
        collection: str,
        transaction: str | None = None,
    ) -> None:
        """Create the edges that are not there and leave the ones that are untouched.

        The create-only counterpart of :meth:`batch_create_edges`, which replaces an
        existing edge's properties. For a repair or a re-run that must not reset what
        a live edge already carries (a sync state, a role). Raises on failure.
        *edges* take the same generic format.
        """
        pass

    @abstractmethod
    async def get_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get an edge/relationship between two nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Edge data in generic format if found, None otherwise
        """
        pass

    @abstractmethod
    async def delete_edge(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None
    ) -> bool:
        """
        Delete an edge/relationship between two nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def batch_delete_edges(
        self,
        edges: list[dict],
        collection: str,
        transaction: str | None = None
    ) -> int:
        """
        Batch delete edges/relationships between nodes.

        Args:
            edges (List[Dict]): List of edges in generic format:
                {
                    "from_id": "user123",           # Source node ID
                    "from_collection": "users",     # Source collection
                    "to_id": "record456",           # Target node ID
                    "to_collection": "records",     # Target collection
                }
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_from(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: str | None = None
    ) -> int:
        """
        Delete all edges originating from a node.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: list[str],
        transaction: str | None = None
    ) -> int:
        """
        Delete edges from a node by relationship types.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            relationship_types (List[str]): List of relationship type values to delete
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_to(
        self,
        to_id: str,
        to_collection: str,
        collection: str,
        transaction: str | None = None
    ) -> int:
        """
        Delete all edges pointing to a node.

        Args:
            to_id (str): Target node ID
            to_collection (str): Target node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    async def replace_edges_to(
        self,
        to_id: str,
        to_collection: str,
        edges: list[dict],
        collection: str,
        transaction: str | None = None,
    ) -> None:
        """Delete every *collection* edge into the node, then create *edges*.

        Concrete by design: a provider with real transactions keeps the two calls.
        Neo4j overrides it with one statement, since with NEO4J_EXPLICIT_TRANSACTIONS
        off a failure after the delete left the node with no edges at all.
        """
        await self.delete_edges_to(to_id, to_collection, collection, transaction)
        if edges:
            await self.batch_create_edges(edges, collection, transaction)

    async def replace_record_permissions(
        self,
        record_id: str,
        edges: list[dict],
        record_group_id: str | None,
        *,
        inherit: bool,
        transaction: str | None = None,
    ) -> None:
        """Replace the PERMISSION edges into the record and set whether it inherits from its group.

        *inherit* true writes the INHERIT_PERMISSIONS edge to *record_group_id*, when
        there is one. False removes the record's inherit edge to every record group,
        so one to a group that can no longer be looked up goes too; its inherit edges
        to a parent record or an app stay. Concrete for the same reason as
        ``replace_edges_to``: on Neo4j a failure between the separate calls left the
        new permissions beside an inherit edge that should have gone.
        """
        await self.replace_edges_to(
            record_id, CollectionNames.RECORDS.value, edges, CollectionNames.PERMISSION.value, transaction
        )
        if not inherit:
            await self._stop_inheriting_from_record_groups(record_id, transaction)
        elif record_group_id:
            await self.create_inherit_permissions_relation_record_group(record_id, record_group_id, transaction)

    async def _stop_inheriting_from_record_groups(
        self, record_id: str, transaction: str | None
    ) -> None:
        """Remove the record's INHERIT_PERMISSIONS edges to record groups. Must raise when it cannot."""
        await self.delete_edges_between_collections(
            record_id,
            CollectionNames.RECORDS.value,
            CollectionNames.INHERIT_PERMISSIONS.value,
            CollectionNames.RECORD_GROUPS.value,
            transaction,
        )

    async def link_record_to_group(
        self,
        record_id: str,
        record_group_id: str | None,
        *,
        inherit: bool | None,
        leaving_group_id: str | None = None,
        browse_root: bool | None = None,
        transaction: str | None = None,
    ) -> None:
        """Take the record out of *leaving_group_id*, then put it in *record_group_id*.

        Leaving removes its BELONGS_TO and INHERIT_PERMISSIONS edges to that group,
        and the group's hierarchy edge to it. Joining writes BELONGS_TO, and
        INHERIT_PERMISSIONS when *inherit* is true; false removes that edge and None
        leaves it alone. *browse_root* true hangs the record off the group in the
        hierarchy and false removes that edge: a record under a parent record must
        not also hang off its group. None leaves it alone. Either group may be None.
        Neo4j overrides it with one statement: a record that left its group but
        kept the inherit edge stayed readable to the old group's members.
        """
        if leaving_group_id:
            await self._delete_record_group_edge(
                record_id, leaving_group_id, CollectionNames.BELONGS_TO.value, transaction
            )
            await self._set_record_group_inheritance(record_id, leaving_group_id, transaction, inherit=False)
            await self._delete_hierarchy_edge(
                leaving_group_id, CollectionNames.RECORD_GROUPS.value, record_id, transaction
            )
        if record_group_id:
            await self.create_record_group_relation(record_id, record_group_id, transaction)
            if inherit is not None:
                await self._set_record_group_inheritance(record_id, record_group_id, transaction, inherit=inherit)
            if browse_root:
                now = get_epoch_timestamp_in_ms()
                await self.batch_create_edges(
                    [{
                        "from_id": record_group_id,
                        "from_collection": CollectionNames.RECORD_GROUPS.value,
                        "to_id": record_id,
                        "to_collection": CollectionNames.RECORDS.value,
                        "relationshipType": RecordRelations.PARENT_CHILD.value,
                        "createdAtTimestamp": now,
                        "updatedAtTimestamp": now,
                    }],
                    CollectionNames.NODE_RELATIONS.value,
                    transaction,
                )
            elif browse_root is False:
                await self._delete_hierarchy_edge(
                    record_group_id, CollectionNames.RECORD_GROUPS.value, record_id, transaction
                )

    async def _set_record_group_inheritance(
        self, record_id: str, record_group_id: str, transaction: str | None, *, inherit: bool
    ) -> None:
        if inherit:
            await self.create_inherit_permissions_relation_record_group(record_id, record_group_id, transaction)
        else:
            await self._delete_record_group_edge(
                record_id, record_group_id, CollectionNames.INHERIT_PERMISSIONS.value, transaction
            )

    async def _delete_hierarchy_edge(
        self, parent_id: str, parent_collection: str, record_id: str, transaction: str | None
    ) -> None:
        """Remove the hierarchy edge from *parent_id* to the record. Raises when the
        delete fails, where ArangoDB's delete_edge would answer False."""
        await self.batch_delete_edges(
            [{
                "from_id": parent_id,
                "from_collection": parent_collection,
                "to_id": record_id,
                "to_collection": CollectionNames.RECORDS.value,
            }],
            CollectionNames.NODE_RELATIONS.value,
            transaction,
        )

    async def _delete_record_group_edge(
        self, record_id: str, record_group_id: str, collection: str, transaction: str | None
    ) -> None:
        # Not delete_edge: ArangoDB's answers False when the delete fails, so the
        # transaction went on to commit the rest beside an edge that should have gone.
        await self.batch_delete_edges(
            [{
                "from_id": record_id,
                "from_collection": CollectionNames.RECORDS.value,
                "to_id": record_group_id,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
            }],
            collection,
            transaction,
        )

    @abstractmethod
    async def delete_edges_to_groups(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        transaction: str | None = None
    ) -> int:
        """
        Delete edges from a node to group nodes.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of edges deleted
        """
        pass

    @abstractmethod
    async def delete_edges_between_collections(
        self,
        from_id: str,
        from_collection: str,
        edge_collection: str,
        to_collection: str,
        transaction: str | None = None,
        *,
        to_connector_id: str | None = None,
    ) -> int:
        """
        Delete edges between a node and nodes in a specific collection.

        Args:
            from_id (str): Source node ID
            from_collection (str): Source node collection name
            edge_collection (str): Edge collection name
            to_collection (str): Target collection name
            transaction (Optional[Any]): Optional transaction context
            to_connector_id: Only edges to nodes of this connector (a user's roles
                and groups come from every connector).
        """
        pass

    @abstractmethod
    async def delete_nodes_and_edges(
        self,
        keys: list[str],
        collection: str,
        graph_name: str = "knowledgeGraph",
        transaction: str | None = None
    ) -> None:
        """
        Delete nodes and all their connected edges.

        Args:
            keys (List[str]): List of node keys to delete
            collection (str): Collection name
            graph_name (str): Graph name (default: "knowledgeGraph")
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def update_edge(
        self,
        from_key: str,
        to_key: str,
        edge_updates: dict,
        collection: str,
        transaction: str | None = None
    ) -> bool:
        """
        Update an edge/relationship.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            edge_updates (Dict): Fields to update
            collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    # ==================== Generic Filter Operations ====================

    @abstractmethod
    async def remove_nodes_by_field(
        self,
        collection: str,
        field_name: str,
        *,
        field_value: str,
        transaction: str | None = None,
    ) -> int:
        """
        Remove nodes from a collection matching a field value.

        Generic method that can be used for any collection and field.

        Args:
            collection (str): Collection name
            field_name (str): Field name to filter on
            field_value (str): Field value to match
            transaction (Optional[Any]): Optional transaction context

        Returns:
            int: Number of nodes removed

        Example:
            # Remove 'anyone' permissions for a file
            await provider.remove_nodes_by_field("anyone", "file_key", field_value=file_key)
        """
        pass

    @abstractmethod
    async def get_edges_to_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: str | None = None
    ) -> list[dict]:
        """
        Get all edges pointing to a specific node.

        Generic method that works with any edge collection.

        Args:
            node_id (str): Full node ID (e.g., "records/123")
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of edge documents
        """
        pass

    @abstractmethod
    async def get_edges_from_node(
        self,
        node_id: str,
        edge_collection: str,
        transaction: str | None = None
    ) -> list[dict]:
        """
        Get all edges originating from a specific node.

        Generic method that works with any edge collection.

        Args:
            node_id (str): Source node ID (e.g., "groups/123")
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of edge documents
        """
        pass

    @abstractmethod
    async def get_edges_from_node_with_target_name(
        self,
        node_id: str,
        edge_collection: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Get all edges originating from a node with target node names.

        Generic method that works with any edge collection.

        Args:
            node_id (str): Source node ID (e.g., "groups/123")
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context
            raise_on_error (bool): Raise a failed read instead of returning []

        Returns:
            List[Dict]: List of edge documents enriched with target name
        """
        pass

    @abstractmethod
    async def get_related_node_field(
        self,
        node_id: str,
        edge_collection: str,
        target_collection: str,
        field_name: str,
        direction: str = "inbound",
        transaction: str | None = None
    ) -> list[dict]:
        """
        Get a specific field from related nodes.

        Generic method to get specific fields from related nodes.

        Args:
            node_id (str): Full node ID to start from
            edge_collection (str): Edge collection to traverse
            target_collection (str): Target node collection
            field_name (str): Field to extract from related nodes
            direction (str): "inbound" or "outbound"
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Any]: List of field values from related nodes
        """
        pass

    # ==================== Query Operations ====================

    @abstractmethod
    async def execute_query(
        self,
        query: str,
        bind_vars: dict | None = None,
        transaction: str | None = None,
        timeout_seconds: float | None = None,
    ) -> list[dict] | None:
        """
        Execute a database-specific query (AQL for ArangoDB, Cypher for Neo4j).

        Args:
            query (str): Query string in database-specific language
            bind_vars (Optional[Dict]): Query parameters/variables
            transaction (Optional[Any]): Optional transaction context
            timeout_seconds: Optional server-side limit; the server stops the
                query past it (outside a transaction)

        Returns:
            Optional[List[Dict]]: Query results if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: dict[str, Any],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Get nodes from a collection matching multiple field filters.

        Generic method to query nodes by any combination of fields.

        Args:
            collection (str): Collection name
            filters (Dict[str, Any]): Dictionary of field_name: value pairs to filter on
            return_fields (Optional[List[str]]): Optional list of fields to return (None = all fields)
            transaction (Optional[Any]): Optional transaction context
            raise_on_error (bool): Raise a failed read instead of returning []

        Returns:
            List[Dict]: List of matching node documents
        """
        pass

    @abstractmethod
    async def get_nodes_by_field_in(
        self,
        collection: str,
        field_name: str,
        field_values: list[Any],
        return_fields: list[str] | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Get nodes from a collection where a field value is in a list.

        Generic method for IN queries.

        Args:
            collection (str): Collection name
            field_name (str): Field name to filter on
            field_values (List[Any]): List of values to match
            return_fields (Optional[List[str]]): Optional list of fields to return
            transaction (Optional[Any]): Optional transaction context
            raise_on_error (bool): Raise a failed query instead of logging it
                and returning ``[]``, for callers that must tell "no such
                nodes" from "could not look".

        Returns:
            List[Dict]: List of matching node documents
        """
        pass


    @abstractmethod
    async def get_child_record_ids_by_relation_type(
        self,
        record_id: str,
        relation_type: str,
        transaction: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Get record _keys of all records that have an edge pointing TO this record
        with the given relation type (e.g. child tables that reference this table via FOREIGN_KEY).

        Args:
            record_id (str): Record _key (vertex id)
            relation_type (str): Edge relation type (e.g. RecordRelations.FOREIGN_KEY.value)
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict[str, Any]]: List of dicts with record_id and FK metadata (childTable, sourceColumn, targetColumn).
        """
        pass

    @abstractmethod
    async def get_parent_record_ids_by_relation_type(
        self,
        record_id: str,
        relation_type: str,
        transaction: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Get record _keys of all records that this record has an edge pointing TO
        with the given relation type (e.g. parent tables that this table references via FOREIGN_KEY).

        Args:
            record_id (str): Record _key (vertex id)
            relation_type (str): Edge relation type (e.g. RecordRelations.FOREIGN_KEY.value)
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict[str, Any]]: List of dicts with record_id and FK metadata (parentTable, sourceColumn, targetColumn).
        """
        pass

    async def get_node_relations_batch(
        self,
        record_ids: list[str],
        relation_types: list[str],
        transaction: Optional[str] = None,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Edges for many records and relation types at once.

        Returns {record_id: {"parents": [...], "children": [...]}} where the two
        lists hold what `get_parent_record_ids_by_relation_type` and
        `get_child_record_ids_by_relation_type` return, each entry additionally
        carrying `relationType`.

        Concrete by design: this default preserves behaviour for providers that
        have not specialised it. Overriding it with a single query is what makes
        it worth calling -- the default still costs one query per record per
        relation type per direction.

        Those queries are issued concurrently. Awaiting them in place would make
        an unspecialised provider slower than the per-record code this replaced,
        which gathered the same calls; a caller must not lose latency by moving
        to the batch API.
        """
        jobs = [
            (record_id, relation_type, outgoing)
            for record_id in record_ids
            for relation_type in relation_types
            for outgoing in (True, False)
        ]

        async def fetch(record_id: str, relation_type: str, *, outgoing: bool) -> list[dict[str, Any]]:
            call = (
                self.get_parent_record_ids_by_relation_type
                if outgoing
                else self.get_child_record_ids_by_relation_type
            )
            return await call(record_id, relation_type, transaction)

        results = await asyncio.gather(
            *[fetch(rid, rel, outgoing=out) for rid, rel, out in jobs],
            return_exceptions=True,
        )

        out: dict[str, dict[str, list[dict[str, Any]]]] = {
            record_id: {"parents": [], "children": []} for record_id in record_ids
        }
        for (record_id, relation_type, outgoing), edges in zip(jobs, results):
            if isinstance(edges, BaseException):
                # One failing pair must not cost the caller every other edge.
                continue
            bucket = out[record_id]["parents" if outgoing else "children"]
            for edge in edges or []:
                if not isinstance(edge, Mapping):
                    continue
                bucket.append({**edge, "relationType": relation_type})
        return out

    @abstractmethod
    async def get_virtual_record_ids_for_record_ids(
        self,
        record_ids: list[str],
        transaction: Optional[str] = None
    ) -> dict[str, str]:
        """
        Resolve record _keys to virtualRecordIds (e.g. to fetch blob for child records).

        Args:
            record_ids (List[str]): List of record _keys
            transaction (Optional[str]): Optional transaction context

        Returns:
            Dict[str, str]: Mapping record_id -> virtual_record_id
        """
        pass

    # ==================== Record Operations ====================
    @abstractmethod
    async def get_record_by_path(
        self,
        connector_id: str,
        path: list[str],
        external_record_group_id: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get a record by its file path.

        Args:
            connector_id (str): Connector ID
            path (list[str]): Record names from a top-level record of the group down to this one
            external_record_group_id (str): External Record group ID
            transaction (str | None): Optional transaction context

        Returns:
            dict | None: The stored document (not a Record) if found, None otherwise

        Raises:
            GraphQueryError: The lookup could not be read, so None would be a guess.
        """
        pass

    @abstractmethod
    async def get_file_records_under_path(
        self,
        connector_id: str,
        external_record_group_id: str,
        path: str,
        transaction: str | None = None,
    ) -> list['Record']:
        """Live records of a group whose stored file ``path`` is ``path`` or lies
        below it, deepest first. For a source that reports a deletion by path only.

        Raises:
            GraphQueryError: The lookup could not be read, so an empty list would be a guess.
        """
        pass

    @abstractmethod
    async def get_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: str | None = None,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> Optional['Record']:
        """
        Get a record by its external ID from the source system.

        Connector sync passes ``ALL``: it decides between creating and updating
        on this answer, so hiding a trashed record would mint a duplicate.

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            transaction (Optional[Any]): Optional transaction context
            visibility: ``LIVE`` (default) leaves out records in the trash,
                ``DELETED`` returns only those, ``ALL`` returns both.

        Returns:
            Optional['Record']: Record data if found, None otherwise. None means
                there is no such record - never that the lookup failed.

        Raises:
            GraphQueryError: The lookup could not be read. Callers act on None
                by creating the record or concluding it was deleted, so a
                failure reported as None becomes a duplicate record or a
                deletion that never happened.
        """
        pass

    @abstractmethod
    async def find_slack_burst_record_by_ts(
        self,
        connector_id: str,
        channel_id: str,
        ts: str,
        transaction: Optional[str] = None,
    ) -> Optional['Record']:
        """
        Find the Slack burst MessageRecord whose startTs <= ts <= endTs for the
        given connector and channel.

        Args:
            connector_id: The connector instance ID.
            channel_id: The Slack channel (externalGroupId) to scope the search.
            ts: The message timestamp to look up.
            transaction: Optional ArangoDB transaction ID.

        Returns:
            The matching MessageRecord, or None if not found.
        """
        pass

    @abstractmethod
    async def get_record_by_external_revision_id(
        self,
        connector_id: str,
        external_revision_id: str,
        transaction: str | None = None,
        *,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> Optional['Record']:
        """
        Get a record by its external revision ID (e.g., etag for S3).

        Args:
            connector_id (str): Connector ID
            external_revision_id (str): External revision ID (e.g., etag)
            transaction (Optional[Any]): Optional transaction context
            visibility: LIVE by default. Rename detection must not match a
                record in the trash: after a hard delete there would be no
                record to match, so the renamed item is a new record.

        Returns:
            Optional[Record]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_key_by_external_id(
        self,
        external_id: str,
        connector_id: str,
        transaction: str | None = None
    ) -> str | None:
        """
        Get a record's internal key by its external ID.

        Args:
            external_id (str): External record ID
            connector_id (str): Connector ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Record key if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_records_by_status(
        self,
        org_id: str,
        connector_id: str,
        status_filters: list[str] | None,
        limit: int | None = None,
        offset: int = 0,
        transaction: str | None = None,
        record_group_id: str | None = None,
        is_placeholder: bool | None = None,
        after_key: str | None = None,
        exclude_statuses: list[str] | None = None,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list['Record']:
        """
        Get records by their indexing status.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID
            status_filters (Optional[List[str]]): List of status values to filter by.
                        A None or empty list returns records regardless of status.
            limit (Optional[int]): Maximum number of records to return
            offset (int): Number of records to skip
            transaction (Optional[Any]): Optional transaction context
            record_group_id (Optional[str]): Scope results to a specific record group
            is_placeholder (Optional[bool]): Filter on placeholder flag - True returns only 
                        stubs, False excludes them, None ignores the flag
            after_key (Optional[str]): Keyset cursor - return only records whose key
                        sorts strictly after this value. Prefer this over offset when
                        paginating a result set that mutates while being iterated.
            exclude_statuses (Optional[List[str]]): Status values to exclude, applied
                        on top of status_filters.
            visibility: ``LIVE`` (default) leaves out records in the trash,
                ``DELETED`` returns only those, ``ALL`` returns both.

        Returns:
            list[Record]: Typed records matching the filters, sorted by key.
                An empty list means no record matched - never that the query failed.

        Raises:
            GraphQueryError: The listing could not be read (database unreachable,
                malformed query, expired transaction). Callers must not treat this
                as "no matching records".
        """
        pass

    @abstractmethod
    async def get_app_needing_vector_membership_backfill(
        self,
        transaction: str | None = None,
    ) -> dict | None:
        """Return one app whose vector points still need connectorIds/recordGroupIds.

        Selects documents where ``vectorMembershipBackfilled`` is missing or false
        and ``status`` is not ``DELETING``.
        """
        pass

    @abstractmethod
    async def get_records_pending_duplicate_reconcile(
        self,
        due_before_ms: int,
        limit: int,
        transaction: str | None = None,
    ) -> list[dict]:
        """Up to ``limit`` records whose ``duplicateReconcilePending`` flag is
        set and whose ``duplicateReconcileDueAt`` (missing counts as 0) is
        before ``due_before_ms``: primaries whose handler left the reconcile
        undone. See ``app.modules.indexing.duplicate_reconcile``.

        Returns:
            ``[{"_key", "duplicateReconcileAttempts", "duplicateReconcileDueAt"}]``.

        Raises:
            Exception: on query failure.
        """
        pass

    async def update_nodes_fields_if_match(
        self,
        collection: str,
        rows: list[tuple[str, dict[str, Any], dict[str, Any]]],
        transaction: str | None = None,
    ) -> list[str]:
        """``update_node_fields_if_match`` for many nodes: each row is
        ``(key, updates, expected)``. Returns the keys whose write applied.

        The default issues one call per row; Neo4j and ArangoDB override it with
        a single statement. A row whose expectation no longer holds is left
        unchanged, which is a normal outcome, not an error.
        """
        applied: list[str] = []
        for key, updates, expected in rows:
            if await self.update_node_fields_if_match(
                key, collection, updates, expected, transaction
            ):
                applied.append(key)
        return applied

    @abstractmethod
    async def update_node_fields_if_match(
        self,
        key: str,
        collection: str,
        updates: dict[str, Any],
        expected: dict[str, Any],
        transaction: str | None = None,
    ) -> bool:
        """Merge ``updates`` into the node only while every field in
        ``expected`` still holds its value (``None`` means absent), in one
        statement. Unlike ``update_node_if_match`` the document is merged,
        not replaced. A ``None`` in ``updates`` removes or nulls the field.

        Returns:
            True iff the write applied.

        Raises:
            ValueError: when ``expected`` is empty.
            Exception: on query failure.
        """
        pass

    @abstractmethod
    async def get_entity_index_candidate(
        self,
        collection: str,
        marker: str,
        *,
        sweep_before: int | None = None,
        transaction: str | None = None,
    ) -> dict | None:
        """One app or org whose entity index projection is not at ``marker``.

        ``collection`` is ``apps`` or ``organizations``; apps being deleted
        are skipped. With ``sweep_before`` (epoch ms), an org whose stale-point
        sweep last finished before it, or never, is also returned. See
        ``app.modules.indexing.entity_index_rebuild``.

        Raises:
            ValueError: for any other collection.
            Exception: on query failure.
        """
        pass

    @abstractmethod
    async def page_entity_index_source(
        self,
        source: str,
        scope_id: str,
        after_key: str | None,
        limit: int,
        transaction: str | None = None,
    ) -> list[dict]:
        """One keyset page of ``source`` within ``scope_id``, ordered by key.

        ``source`` is a key of ``ENTITY_INDEX_SOURCES``
        (``app.services.graph_db.entity_index_queries``): records and record
        groups are scoped by connector, taxonomy nodes and departments by org
        (canonical nodes only; departments include global ones). Rows are
        ``{"_key", "name", ...}`` plus the source's extra fields, unfiltered,
        so a page shorter than ``limit`` means the source is exhausted. An
        empty ``scope_id`` returns ``[]`` without querying.

        Raises:
            ValueError: for an unknown source.
            Exception: on query failure.
        """
        pass

    @abstractmethod
    async def page_records_for_vector_membership_backfill(
        self,
        connector_id: str,
        after_key: str | None,
        limit: int,
        transaction: str | None = None,
    ) -> list[dict]:
        """Page records for a connector by stable key for membership backfill.

        Returns ``{_key, virtualRecordId}`` only, ordered by key, with keys
        strictly greater than ``after_key`` when it is set.
        """
        pass

    @abstractmethod
    async def reindex_single_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        request: Optional["Request"] = None,
        depth: int = 0,
        status_filters: list[str] | None = None,
    ) -> dict:
        """
        Validate and prepare reindex for a single record (permission checks, reset status).
        Does NOT publish events; caller should publish after success.

        Args:
            record_id: Record ID to reindex
            user_id: External user ID
            org_id: Organization ID
            request: Optional request (for signature compatibility)
            depth: Depth for children (0 = only this record)
            status_filters: Optional indexingStatus values; included in sync-events payload
                        for the consumer to filter matched records (parent/children queries).

        Returns:
            Dict: success, recordId, recordName, connector, userRole; or error code/reason
        """
        pass

    @abstractmethod
    async def reindex_record_group_records(
        self,
        record_group_id: str,
        depth: int,
        user_id: str,
        org_id: str,
    ) -> dict:
        """
        Validate record group and user permissions for reindexing.
        Does NOT publish events; caller should publish.

        Args:
            record_group_id: Record group ID
            depth: Depth for traversing children
            user_id: External user ID
            org_id: Organization ID

        Returns:
            Dict: success, connectorId, connectorName, depth, recordGroupId; or error code/reason
        """
        pass

    @abstractmethod
    async def update_indexing_status_for_record_ids(
        self, record_ids: list[str], status: str
    ) -> None:
        """
        Set indexingStatus to the specified status for each id (deduplicated).
        Skips records with isInternal true. Non-string ids are ignored. Pass a one-element list
        for a single record. Generic method for updating record status during reindex operations.
        Skips missing records; logs errors without raising.
        
        Args:
            record_ids: List of record IDs to update
            status: Target status (e.g., ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)
        """
        pass

    @abstractmethod
    async def reset_indexing_status_for_connector(
        self,
        connector_id: str,
        status: str,
        exclude_statuses: list[str] | None = None,
        transaction: str | None = None,
    ) -> None:
        """Set indexingStatus for every record on a connector in one query.

        ``exclude_statuses`` records are left unchanged (typically IN_PROGRESS).
        """
        pass

    @abstractmethod
    async def compare_and_set_indexing_status(
        self,
        record_ids: list[str],
        expected: str,
        new_status: str,
        transaction: str | None = None,
    ) -> list[str]:
        """
        Atomically set indexingStatus to `new_status` on each id, but only while that
        id still holds `expected`. One round-trip regardless of how many ids.

        Publishing to the message broker and updating the record are two writes to
        two systems, so they cannot be made atomic. The indexing service may consume
        the event and advance the record (IN_PROGRESS, COMPLETED) before the producer
        gets to mark it QUEUED. An unconditional write would clobber that and strand
        the record at QUEUED forever, so the write has to be conditional rather than
        merely ordered.

        A swap into QUEUED also stamps queuedAtTimestamp, the platform-owned clock
        the stranded-record sweep ages rows on.

        Args:
            record_ids: Record keys to attempt the swap on. Pass a one-element list
                        for a single record.
            expected: Status a record must currently hold for its write to apply
            new_status: Status to write
            transaction: Optional transaction context

        Returns:
            list[str]: The ids actually updated. Ids absent from the result held some
                       other status and were left alone - a normal outcome, not an error.
        """
        pass

    @abstractmethod
    async def get_existing_record_keys(
        self,
        record_ids: list[str],
        transaction: str | None = None,
    ) -> set[str]:
        """
        Return the subset of record_ids that exist, in one round-trip.

        Args:
            record_ids: Record keys to check
            transaction: Optional transaction context

        Returns:
            set[str]: Keys that exist. Missing ids are simply absent.
        """
        pass

    @abstractmethod
    async def get_documents_by_status(
        self,
        collection: str,
        status: str,
        transaction: str | None = None
    ) -> list[dict]:
        """
        Get all documents with a specific indexing status.

        Args:
            collection (str): Collection name
            status (str): Status to filter by
            transaction (Optional[str]): Optional transaction context

        Returns:
            List[Dict]: List of matching documents
        """
        pass

    @abstractmethod
    async def get_record_by_conversation_index(
        self,
        connector_id: str,
        conversation_index: str,
        thread_id: str,
        org_id: str,
        user_id: str,
        transaction: str | None = None
    ) -> Optional['Record']:
        """
        Get a record by conversation index (for email/chat connectors).

        Args:
            connector_id (str): Connector ID
            conversation_index (str): Conversation index
            thread_id (str): Thread ID
            org_id (str): Organization ID
            user_id (str): User ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_issue_key(
        self,
        connector_id: str,
        issue_key: str,
        transaction: str | None = None
    ) -> Optional['Record']:
        """
        Get record by Jira issue key (e.g., PROJ-123) by searching weburl pattern.

        Args:
            connector_id: Connector ID
            issue_key: Jira issue key (e.g., "PROJ-123")
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_by_weburl(
        self,
        weburl: str,
        org_id: str | None = None,
        transaction: str | None = None
    ) -> Optional['Record']:
        """
        Get record by weburl (exact match).

        Args:
            weburl: Web URL to search for
            org_id: Optional organization ID to filter by
            transaction: Optional transaction ID

        Returns:
            Optional[Record]: Record if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: str | None = None,
        transaction: str | None = None,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list['Record']:
        """
        Get all child records for a parent record by parent_external_record_id.
        Optionally filter by record_type.

        Live children only by default, so a folder whose children are all in
        the trash reads as empty.

        Args:
            connector_id (str): Connector ID
            parent_external_record_id (str): Parent record's external ID
            record_type (Optional[str]): Optional filter by record type (e.g., "COMMENT", "FILE", "TICKET")
            transaction (Optional[Any]): Optional transaction context
            visibility: ``LIVE`` (default) leaves out records in the trash,
                ``DELETED`` returns only those, ``ALL`` returns both.

        Returns:
            List[Dict]: List of child records
        """
        pass

    @abstractmethod
    async def get_records_by_record_type(
        self,
        connector_id: str,
        record_type: str,
        transaction: str | None = None,
    ) -> list['Record']:
        """Return this connector's records of ``record_type``.

        Args:
            connector_id: Connector ID
            record_type: Record type value (e.g. ``DATABASE``, ``WEBPAGE``)
            transaction: Optional transaction context
        """
        pass

    @abstractmethod
    async def get_records_by_record_group(
        self,
        record_group_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        user_key: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        transaction: str | None = None,
        status_filters: list[str] | None = None,
        after_key: str | None = None,
        exclude_statuses: list[str] | None = None,
    ) -> list['Record']:
        """
        Get all records belonging to a record group up to a specified depth.
        Uses belongsTo edges for nested record group traversal and optional
        permission checks via the knowledge hub permission model.

        Includes:
        - Records directly in the group (via belongsTo edges)
        - Records in nested record groups up to depth levels (via belongsTo edges)

        Args:
            record_group_id (str): Record group ID
            connector_id (str): Connector ID filter (records matching this connectorId are returned)
            org_id (str): Organization ID (for security filtering)
            depth (int): Depth for traversing children and nested record groups
                        (-1 = unlimited, 0 = only direct records, 1 = direct + 1 level nested, etc.)
            user_key (Optional[str]): User key for permission filtering. When provided,
                        only records the user has permission to access are returned.
                        Uses the same permission model as knowledge hub (10 permission paths).
            limit (Optional[int]): Maximum number of records to return (for pagination)
            offset (int): Number of records to skip (for pagination)
            transaction (Optional[str]): Optional transaction ID
            status_filters (Optional[List[str]]): When set, only records with
                        indexingStatus in this list are returned.
            after_key (Optional[str]): Keyset cursor - return only records whose key
                        sorts strictly after this value. Prefer this over offset when
                        paginating a result set that mutates while being iterated.
            exclude_statuses (Optional[List[str]]): Status values to exclude, applied
                        on top of status_filters.

        Returns:
            List[Record]: List of properly typed Record instances. Origin is not
                        hard-filtered here; both CONNECTOR and UPLOAD records may
                        be returned when they match connectorId/org/permission constraints.
        """
        pass

    @abstractmethod
    async def get_records_by_parent_record(
        self,
        parent_record_id: str,
        connector_id: str,
        org_id: str,
        depth: int,
        user_key: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        transaction: str | None = None,
        status_filters: list[str] | None = None,
        after_key: str | None = None,
        exclude_statuses: list[str] | None = None,
    ) -> list['Record']:
        """
        Get all child records of a parent record (folder) up to a specified depth.
        Uses graph traversal on record relations. Parent record is always included.

        Args:
            parent_record_id (str): Record ID of the parent (folder)
            connector_id (str): Connector ID (all records should be from same connector)
            org_id (str): Organization ID (for security filtering)
            depth (int): Depth for traversing children
                        (-1 = unlimited, 0 = only parent, 1 = direct children,
                         2 = children + grandchildren, etc.)
            user_key (Optional[str]): User key for permission filtering. When provided,
                        only records the user has permission to access are returned.
                        Uses the same permission model as knowledge hub (10 permission paths).
            limit (Optional[int]): Maximum number of records to return (for pagination)
            offset (int): Number of records to skip (for pagination)
            transaction (Optional[str]): Optional transaction ID
            status_filters (Optional[List[str]]): When set, only records with
                        indexingStatus in this list are returned.
            after_key (Optional[str]): Keyset cursor - return only records whose key
                        sorts strictly after this value. Prefer this over offset when
                        paginating a result set that mutates while being iterated.
            exclude_statuses (Optional[List[str]]): Status values to exclude, applied
                        on top of status_filters.

        Returns:
            List[Record]: List of properly typed Record instances, sorted by key.
        """
        pass

    # ==================== Record Group Operations ====================

    @abstractmethod
    async def get_record_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: str | None = None
    ) -> Optional['RecordGroup']:
        """
        Get a record group by its external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External record group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Record group data if found, None otherwise. None means
                there is no such group - never that the lookup failed.

        Raises:
            GraphQueryError: The lookup could not be read. Callers create a group
                when they are told None, so a failure must not look like one.
        """
        pass

    @abstractmethod
    async def get_record_group_by_id(
        self,
        record_group_id: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get a record group by its internal ID.

        Args:
            record_group_id: Internal record group ID
            transaction: Optional transaction context

        Returns:
            Optional[Dict]: Record group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_record_path(
        self,
        record_id: str,
        transaction: str | None = None
    ) -> str | None:
        pass

    @abstractmethod
    async def get_record_path_segments(
        self,
        record_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[str]:
        """Return individual record names from root ancestor to the given record.

        Unlike ``get_record_path`` (which joins names with ``/``), this
        returns each name as a separate list element so names that
        themselves contain ``/`` are preserved correctly. The chain is chosen
        by ``select_canonical_chain_names`` so every backend returns the same one.

        Returns an empty list when the record is not found. On a query failure
        returns an empty list, or raises when *raise_on_error* — callers that
        build storage paths must not mistake a failure for "no ancestors".
        """
        pass

    @abstractmethod
    async def get_descendant_virtual_record_ids(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> list[str]:
        """Return the distinct virtualRecordIds of every record below *record_id*
        along the canonical parent chain used by ``get_record_path_segments`` —
        i.e. the content stored under this record's storage path.

        Raises on a query failure: an empty list means "owns no content", which
        a storage move acts on.
        """
        pass

    @abstractmethod
    async def get_record_group_path(
        self,
        record_group_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list[str]:
        """Return record group names from root ancestor to the given group (inclusive).

        Walks BELONGS_TO edges from the group through parent record groups;
        with several parents the chain is chosen by ``select_canonical_chain_names``.
        Returns an empty list when the group is not found. On a query failure
        returns an empty list, or raises when *raise_on_error*.
        """
        pass

    @abstractmethod
    async def get_file_record_by_id(
        self,
        record_id: str,
        transaction: str | None = None
    ) -> Optional['FileRecord']:
        """
        Get a file record by its internal ID.

        Args:
            record_id: Internal file record ID
            transaction: Optional transaction context

        Returns:
            Optional[FileRecord]: The file record, or None when the file or its
                record is not stored - never that the read failed.

        Raises:
            GraphQueryError: The file record could not be read. Callers act on
                None by treating the file as gone, so a failure reported as
                None would retire or skip a file that is still there.
        """
        pass

    # ==================== User Operations ====================

    @abstractmethod
    async def get_user_by_email(
        self,
        email: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional['User']:
        """
        Get a user by email address.

        None means there is no such user. A read that fails also answers None unless
        ``raise_on_error`` is set, which a caller needs when it would act on "no such
        user" (replacing a record's permissions without them, say).

        Args:
            email (str): User email
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_by_source_id(
        self,
        source_user_id: str,
        connector_id: str,
        transaction: str | None = None
    ) -> Optional['User']:
        """
        Get a user by their source system ID.

        Args:
            source_user_id (str): User ID in source system
            connector_id (str): Connector ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_by_user_id(
        self,
        user_id: str,
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """
        Get a user by their internal user ID.

        Args:
            user_id (str): Internal user ID
            raise_on_error: raise when the lookup fails. Without it a failed
                lookup returns None, the same as a user that does not exist.

        Returns:
            Optional[Dict]: User data if found, None otherwise
        """
        pass

    @abstractmethod
    async def apply_verified_user_email(
        self,
        user_id: str,
        org_id: str,
        email: str,
    ) -> dict | None:
        """
        Set the login graph user's email after Mongo accepted a verified change.

        Connector stub users that already hold this email are merged into the
        login user. Another real login userId with this email is a conflict.

        Returns:
            Dict with email and mergedStubKeys, or None if the login user is missing.
        """
        pass

    @abstractmethod
    async def get_graph_user_keys_by_mongo_user_ids(
        self,
        user_ids: list[str],
        org_id: str | None = None,
        *,
        chunk_size: int,
    ) -> dict[str, str]:
        """
        Look up graph user document keys for Mongo user IDs.

        Args:
            user_ids: Mongo user IDs (user.userId)
            org_id: Optional organization ID to scope results
            chunk_size: Max IDs per graph query batch

        Returns:
            Mapping of Mongo userId -> graph _key

        Raises:
            ValueError: If any user_id was not found in the graph for org_id
        """
        pass

    @abstractmethod
    async def get_account_type(
        self,
        org_id: str,
        is_external: bool = False,
        transaction: str | None = None,
    ) -> str | None:
        """
        Get account type for an organization.

        Args:
            org_id: Organization ID
            is_external (bool): Filter by external flag (default False)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: Account type ('individual' or 'business'), or None
        """
        pass

    @abstractmethod
    async def get_connector_stats(
        self,
        org_id: str,
        connector_id: str,
    ) -> dict:
        """
        Get connector statistics for a specific connector.

        Args:
            org_id: Organization ID
            connector_id: Connector (app) ID

        Returns:
            Dict: success, message, data (stats and byRecordType)
        """
        pass

    @abstractmethod
    async def get_users(
        self,
        org_id: str,
        *,
        active: bool = True,
    ) -> list[dict]:
        """
        Get all users in an organization.

        Args:
            org_id (str): Organization ID
            active (bool): Filter by active status

        Returns:
            List[Dict]: List of users
        """
        pass

    @abstractmethod
    async def get_app_user_by_email(
        self,
        email: str,
        connector_id: str,
        transaction: str | None = None
    ) -> Optional['AppUser']:
        """
        Get an app-specific user by email.

        Args:
            email (str): User email
            connector_id (str): Connector ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: App user data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_app_users(
        self,
        org_id: str,
        connector_id: str
    ) -> list[dict]:
        """
        Get all users for a specific connector in an organization.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of app users
        """
        pass

    @abstractmethod
    async def list_user_knowledge_bases(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: str | None = None,
        permissions: list[str] | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: str | None = None,
    ) -> tuple[list[dict], int, dict]:
        """
        List knowledge bases with pagination, search, and filtering.
        Includes both direct user permissions and team-based permissions.

        Args:
            user_id: User ID
            org_id: Organization ID
            skip: Pagination skip
            limit: Pagination limit
            search: Optional search term for KB name
            permissions: Optional filter by permission roles
            sort_by: Sort field (name, createdAtTimestamp, updatedAtTimestamp, userRole)
            sort_order: Sort direction (asc, desc)
            transaction: Optional transaction ID

        Returns:
            Tuple of (list of KB dicts, total count, available_filters dict)
        """
        pass

    @abstractmethod
    async def get_kb_children(
        self,
        kb_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: str | None = None,
        record_types: list[str] | None = None,
        origins: list[str] | None = None,
        connectors: list[str] | None = None,
        indexing_status: list[str] | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: str | None = None,
    ) -> dict:
        """
        Get KB root contents with folders_first pagination.

        Returns:
            Dict with success, container, folders, records, totalCount, counts,
            availableFilters, paginationMode; or { success: False, reason: str }.
            A KB that is not there is reported as code 404 — callers decide what
            the reader sees from that code, never from the words in `reason`,
            which on any other failure is exception text.
        """
        pass

    @abstractmethod
    async def get_folder_children(
        self,
        kb_id: str,
        folder_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: str | None = None,
        record_types: list[str] | None = None,
        origins: list[str] | None = None,
        connectors: list[str] | None = None,
        indexing_status: list[str] | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: str | None = None,
    ) -> dict:
        """
        Get folder contents with folders_first pagination.

        Returns:
            Dict with success, container, folders, records, totalCount, counts,
            availableFilters, paginationMode; or { success: False, reason: str }.
            A folder that is not there is reported as code 404 — callers decide
            what the reader sees from that code, never from the words in
            `reason`, which on any other failure is exception text.
        """
        pass

    @abstractmethod
    async def kb_exists(self, kb_id: str) -> bool:
        """Return True if a KB document with this id exists, regardless of permissions."""
        pass

    @abstractmethod
    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: str | None = None,
    ) -> dict | None:
        """Get knowledge base with user permissions."""
        pass

    @abstractmethod
    async def update_knowledge_base(
        self,
        kb_id: str,
        updates: dict,
        transaction: str | None = None,
    ) -> bool:
        """Update knowledge base."""
        pass


    @abstractmethod
    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> dict:
        """Shared validation logic for folder creation."""
        pass

    @abstractmethod
    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: str | None = None,
        exclude_folder_id: str | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """Find a folder by name within a specific parent (KB root or folder).
        
        Args:
            kb_id: Knowledge base ID
            folder_name: Name to search for
            parent_folder_id: Parent folder ID, or None for KB root
            exclude_folder_id: Optional folder ID to exclude from results (for rename operations)
            transaction: Optional transaction ID
            raise_on_error: Raise a failed lookup instead of returning None, which
                reads as "no such folder"
        """
        pass

    @abstractmethod
    async def find_file_by_name_in_parent(
        self,
        kb_id: str,
        file_name: str,
        mime_type: str,
        parent_folder_id: str | None = None,
        exclude_record_id: str | None = None,
        transaction: str | None = None,
    ) -> dict | None:
        """Find a file by name and mime type within a specific parent (KB root or folder).
        
        Args:
            kb_id: Knowledge base ID
            file_name: Name to search for
            mime_type: MIME type to match
            parent_folder_id: Parent folder ID, or None for KB root
            exclude_record_id: Optional record ID to exclude from results (for rename/move operations)
            transaction: Optional transaction ID
        """
        pass


    @abstractmethod
    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: str | None = None,
        *,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> bool:
        """Validate that a folder exists, belongs to the KB and matches *visibility*.

        LIVE, the default, refuses a folder in the trash, which is never a place to
        put something. DELETED tells a folder in the trash from one that is not
        there, so the caller can say which.
        """
        pass





    @abstractmethod
    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: list[str],
        team_ids: list[str],
        role: str,
    ) -> dict:
        """Create KB permissions for users and teams."""
        pass

    @abstractmethod
    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: str | None = None,
    ) -> int:
        """Count the number of owners for a knowledge base."""
        pass

    @abstractmethod
    async def remove_kb_permission(
        self,
        kb_id: str,
        user_ids: list[str],
        team_ids: list[str],
        transaction: str | None = None,
    ) -> bool:
        """Remove permissions for multiple users and teams from a KB."""
        pass

    @abstractmethod
    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: str | None = None,
    ) -> str | None:
        """Get user's permission role on a KB (direct or via team)."""
        pass


    @abstractmethod
    async def is_record_folder(self, record_id: str, transaction: str | None = None) -> bool:
        """Return True if the record is a folder (has FILES doc with isFile false)."""
        pass

    @abstractmethod
    async def get_record_parent_info(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> dict | None:
        """Get parent folder/kb info for a record."""
        pass

    @abstractmethod
    async def is_record_descendant_of(
        self,
        record_id: str,
        ancestor_id: str,
        transaction: str | None = None,
    ) -> bool:
        """Return True if record is a descendant of ancestor (folder)."""
        pass

    @abstractmethod
    async def get_folder_depth(
        self,
        folder_id: str,
        transaction: str | None = None,
    ) -> int:
        """Nesting depth of a folder through PARENT_CHILD edges (1 = no parent folder)."""
        pass

    @abstractmethod
    async def get_folder_subtree_height(
        self,
        folder_id: str,
        folder_mime_types: list[str],
        transaction: str | None = None,
    ) -> int:
        """Levels of sub-folders below a folder through PARENT_CHILD edges (0 = none)."""
        pass

    @abstractmethod
    async def delete_parent_child_edge_to_record(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> bool:
        """Delete the incoming PARENT_CHILD edge to a record."""
        pass



    @abstractmethod
    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: list[str] | None = None,
        team_ids: list[str] | None = None,
        transaction: str | None = None,
    ) -> dict[str, dict[str, str]]:
        """Get current roles for users and teams on a KB."""
        pass

    @abstractmethod
    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: list[str],
        team_ids: list[str],
        new_role: str,
    ) -> dict | None:
        """Update permissions for users/teams on a KB."""
        pass

    @abstractmethod
    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: str | None = None,
    ) -> list[dict]:
        """List all permissions for a KB with entity details."""
        pass

    @abstractmethod
    async def list_kb_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: str | None,
        record_types: list[str] | None,
        origins: list[str] | None,
        connectors: list[str] | None,
        indexing_status: list[str] | None,
        date_from: int | None,
        date_to: int | None,
        sort_by: str,
        sort_order: str,
        folder_id: str | None = None,
    ) -> tuple[list[dict], int, dict]:
        """List records in a KB. Returns (records, total_count, available_filters).

        An empty list means no matching record or no access. A query that could
        not be read raises; it is never reported as an empty list.
        """
        pass

    @abstractmethod
    async def list_accessible_artifacts(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: str | None,
        artifact_types: list[str] | None,
        conversation_id: str | None,
        date_from: int | None,
        date_to: int | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[dict], int]:
        """Permission-first listing of user-visible artifacts.

        ``user_id`` is the graph user key (``_key`` / ``id``), not the
        external auth ``userId``. The caller resolves that key first.

        Display-policy filters must run in the query (not post-fetch) so
        pagination totals stay correct:

        - ``recordType == ARTIFACT``
        - ``orgId`` match, ``isDeleted != true``
        - ``visibility`` is ``VISIBLE`` or missing
        - ``isTemporary != true``
        - ``artifactType != TOOL_RESULT``

        Store and query failures must propagate. An empty result means "nothing
        visible", so swallowing an error here would show up as an empty gallery.
        """
        pass

    @abstractmethod
    async def get_artifact_detail(
        self,
        user_id: str,
        org_id: str,
        artifact_id: str,
    ) -> dict | None:
        """Return one artifact the user can access, or None to hide existence.

        Same identity and display-policy contract as
        ``list_accessible_artifacts``. ``None`` means not found or not visible;
        store and query failures must propagate rather than return ``None``.
        """
        pass

    # ==================== Group Operations ====================

    @abstractmethod
    async def get_user_group_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional['AppUserGroup']:
        """
        Get a user group by external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External group ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Group data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_user_groups(
        self,
        connector_id: str,
        org_id: str,
        transaction: str | None = None
    ) -> list['AppUserGroup']:
        """
        Get all user groups for a connector in an organization.

        Args:
            connector_id (str): Connector ID
            org_id (str): Organization ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            List[Dict]: List of user groups
        """
        pass

    @abstractmethod
    async def batch_upsert_people(
        self,
        people: list[Person],
        transaction: str | None = None
    ) -> None:
        """
        Upsert people to PEOPLE collection.

        Args:
            people (List[Person]): List of Person entities
            transaction (Optional[Any]): Optional transaction context

        Returns:
            None
        """
        pass

    @abstractmethod
    async def get_person_by_email(
        self,
        email: str,
        org_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional['Person']:
        """
        Get a person by (org_id, email) — Person's business key, same as User's.

        A read that fails answers None unless ``raise_on_error`` is set.

        Args:
            email (str): Email address; matched case-insensitively
            org_id (str): Owning org; required, same as any other org-scoped lookup
            transaction (Optional[str]): Optional transaction context

        Returns:
            Optional[Person]: The person, or None
        """
        pass

    @abstractmethod
    async def upsert_person_by_email(
        self,
        person: Person,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> str | None:
        """
        Upsert a Person keyed on (org_id, email), returning the id of the surviving node.

        A write that fails answers None unless ``raise_on_error`` is set.

        Callers must use the returned id rather than ``person.id``: on a match the
        existing node wins and its id is what every edge must point at. Never updates
        an existing node, so a caller that knows only an email cannot blank names or
        phone numbers written by a richer source.

        Args:
            person (Person): The person to insert if no match exists; carries its own
                org_id
            transaction (Optional[str]): Optional transaction context

        Returns:
            Optional[str]: Surviving person id, or None on failure
        """
        pass

    @abstractmethod
    async def ensure_app_membership(
        self,
        principal_id: str,
        principal_collection: str,
        connector_id: str,
        *,
        is_external: bool,
        source_user_id: str | None = None,
        transaction: str | None = None,
    ) -> None:
        """
        Ensure a principal (user or person) has a membership edge to an app.

        Create-only: an existing edge is left untouched, so this can never downgrade a
        real member to an external collaborator.

        Args:
            principal_id (str): User or person key
            principal_collection (str): CollectionNames.USERS or CollectionNames.PEOPLE
            connector_id (str): Target app id
            is_external (bool): True when the principal reached this app only through a
                share rather than app membership
            source_user_id (Optional[str]): Source-system user id, when known
            transaction (Optional[str]): Optional transaction context

        Returns:
            None
        """
        pass

    @abstractmethod
    async def migrate_person_to_user(
        self,
        email: str,
        user_key: str,
        org_id: str,
        transaction: str | None = None,
    ) -> str | None:
        """
        Promote a Person to a User by moving its collaborator edges onto that User.

        A Person carrying any CRM edge (lead/contact/memberOf) splits rather than merges:
        the collaborator edges move but the Person node survives holding its CRM edges,
        because a Salesforce contact is a separate thing from a platform identity that
        happens to share an address. A Person with no CRM edge is deleted once emptied.

        Must be idempotent: a second run finds nothing left to move.

        Args:
            email (str): Email identifying the Person; matched against the normalised form
            user_key (str): Key of the already-existing User to move the edges onto
            org_id (str): Owning org of the Person being migrated; required, same as
                get_person_by_email
            transaction (Optional[str]): Optional transaction context

        Returns:
            Optional[str]: PersonMigrationMode.MIGRATED or .SPLIT, or None when no Person
                exists for the email - the ordinary case, not an error.
        """
        pass

    @abstractmethod
    async def reap_stale_external_app_relations(
        self,
        connector_id: str,
        transaction: str | None = None,
    ) -> int:
        """
        Drop `isExternalUser` membership edges whose underlying grant is gone, plus any
        Person the removal left with no edges at all.

        The "still has a grant" test must mirror the candidate collection used by browse
        hoisting, including the group/role/team hop - otherwise this reaps collaborators
        whose access is real and their records vanish from the tree.

        Only flagged edges are considered, so a real app member is never at risk.

        Args:
            connector_id (str): App whose membership edges to sweep
            transaction (Optional[str]): Optional transaction context

        Returns:
            int: Number of orphaned Person nodes removed
        """
        pass

    @abstractmethod
    async def remove_app_users_except(
        self,
        connector_id: str,
        emails: list[str],
        transaction: str | None = None,
    ) -> int:
        """
        Withdraw the connector gate from the users a complete user sync no longer lists.

        Deletes the user-app edges a user sync wrote (they carry a ``sourceUserId``)
        from every user of ``connector_id`` whose email is not in ``emails``. Edges
        with no ``sourceUserId`` (an external collaborator's, a team's) are left.
        Only for a connector whose user sync lists every active source user.

        Args:
            connector_id (str): App whose gate edges to sweep
            emails (list[str]): Lower-cased emails of every active source user
            transaction (Optional[str]): Optional transaction context

        Returns:
            int: Number of edges removed

        Raises:
            ValueError: ``emails`` is empty, which would remove every user.
        """
        pass

    @abstractmethod
    async def get_app_role_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> Optional['AppRole']:
        """
        Get an app role by external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External role ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Role data if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_app_creator_user(
        self,
        connector_id: str,
        transaction:str | None=None
    )->Optional['User']:
        """
        Resolve the creator of an App/Connector by connectorId, using the
        `createdBy` field on the app document to fetch the user from `users`.

        Args:
            connector_id: Connector/App id (_key)
            transaction: Optional transaction context

        Returns:
            User if found, otherwise None
        """
        pass
    # ==================== Organization Operations ====================

    @abstractmethod
    async def get_all_orgs(
        self,
        *,
        active: bool = True,
        is_external: bool = False,
        transaction: str | None = None,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Get all organizations.

        Args:
            active (bool): Filter by active status
            is_external (bool): Filter by external flag (default False)
            transaction (Optional[str]): Optional transaction ID
            raise_on_error (bool): Raise a failed read instead of answering
                [], which is also the answer for an install with no orgs

        Returns:
            List[Dict]: List of organizations
        """
        pass

    @abstractmethod
    async def get_departments(
        self,
        org_id: str | None = None,
        transaction: str | None = None
    ) -> list[str]:
        """
        Get all departments that either have no org_id or match the given org_id.

        Args:
            org_id (Optional[str]): Organization ID to filter departments
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of department names
        """
        pass

    @abstractmethod
    async def get_org_apps(
        self,
        org_id: str,
        *,
        active_only: bool = True,
        app_type: str | None = None,
        raise_on_error: bool = False,
    ) -> list[dict]:
        """
        Get all apps for an organization.

        Args:
            org_id (str): Organization ID
            active_only: When True (default), only apps with isActive true.
            app_type: Only apps of this type, filtered in the database.
            raise_on_error: Raise instead of answering [] when the listing fails,
                for a caller that must tell "none" from "unknown".

        Returns:
            List[Dict]: List of apps
        """
        pass

    # ==================== KB Apps Migration (legacy recordGroup -> app) ====================

    @abstractmethod
    async def get_legacy_kb_record_groups(self, org_id: str) -> list[dict]:
        """
        Get every legacy KB stored as a recordGroups document for this org
        (groupType == "KB" / connectorName == "KB"), from the pre-migration
        data model where each KB was a recordGroup under a shared per-org hub
        app instead of its own app instance.

        Args:
            org_id (str): Organization ID

        Returns:
            List[Dict]: Legacy KB recordGroup documents (full docs, including
                        _key/id, groupName, createdBy, createdAtTimestamp,
                        updatedAtTimestamp).
        """
        pass

    @abstractmethod
    async def migrate_legacy_kb_to_app(
        self,
        kb_record_group: dict,
        org_id: str,
        resolved_creator_key: str | None,
    ) -> dict:
        """
        Migrate a single legacy KB from a recordGroups document to its own
        apps document, reusing the same key. Retargets every PERMISSION,
        BELONGS_TO, and INHERIT_PERMISSIONS edge that pointed at the old
        recordGroup to point at the new app instead, updates every record's
        connectorId to the KB's own app key, creates the new
        orgAppRelation/userAppRelation edges, and deletes the old recordGroup
        document plus its own outbound belongsTo edge to the old shared hub
        app.

        Args:
            kb_record_group (dict): The legacy recordGroup document (from
                        get_legacy_kb_record_groups).
            org_id (str): Organization ID.
            resolved_creator_key (Optional[str]): The creator's graph user
                        key, already resolved from whichever identifier space
                        the old `createdBy` value was in. If None, the app
                        doc is still created but no userAppRelation edge is
                        added for the creator.

        Returns:
            Dict: {"success": bool, "reason": Optional[str]}
        """
        pass

    @abstractmethod
    async def count_legacy_kb_record_groups(self, org_id: str) -> int:
        """
        Count remaining legacy KB recordGroups for this org — used to decide
        whether it's safe to delete the org's old shared hub app (only once
        this returns 0).

        Args:
            org_id (str): Organization ID

        Returns:
            int: Number of remaining legacy KB recordGroups.
        """
        pass

    @abstractmethod
    async def delete_kb_hub_app(self, org_id: str) -> bool:
        """
        Delete the org's legacy shared KB hub app (apps/knowledgeBase_{orgId})
        plus its orgAppRelation edge and every userAppRelation edge pointing
        to it. Only call once count_legacy_kb_record_groups(org_id) == 0.
        Safety-checks that nothing still references the hub app before
        deleting; logs and skips the delete (returns False) if something
        unexpected still points at it.

        Args:
            org_id (str): Organization ID

        Returns:
            bool: True if the hub app was deleted (or didn't exist), False if
                        the delete was skipped due to unexpected references.
        """
        pass

    @abstractmethod
    async def migrate_agent_hub_knowledge(self, org_id: str) -> dict:
        """
        Expand any agent knowledge source that still points at the legacy
        shared KB hub app (apps/knowledgeBase_{orgId}) into one knowledge
        source per current per-KB app for this org, preserving the original
        "search across all my collections" intent. migrate_legacy_kb_to_app
        only retargets edges owned by the recordGroup being migrated — it
        never touches AgentKnowledge nodes, which reference apps by a plain
        connectorId string rather than a graph edge, so this is a separate
        step. Must run before delete_kb_hub_app removes the hub app, or the
        reference becomes unrecoverable.

        Args:
            org_id (str): Organization ID

        Returns:
            Dict: {"agents_migrated": int, "knowledge_nodes_created": int}
        """
        pass

    @abstractmethod
    async def find_duplicate_records(
        self,
        record_key: str,
        md5_checksum: str,
        org_id: str,
        record_type: str | None = None,
        size_in_bytes: int | None = None,
        transaction: str | None = None,
    ) -> list[dict]:
        """
        Find duplicate records based on MD5 checksum, scoped to a single org.

        Live records only: a trashed record's vectors are gone, so a new copy
        that took its COMPLETED status would end up with no vectors.

        Deliberately does NOT filter by connector: dedup decisions need to see
        duplicates from *other* connectors too, so the caller can decide whether
        the duplicate resolves to the same vector collection (skip indexing) or
        a different one (index anyway) — narrowing this query to one connector
        would hide the cross-connector case entirely. Each returned record is
        the full RECORDS document, so callers already have connectorId,
        connectorName, and orgId without any extra round trip.

        ``org_id`` is required and always applied: two orgs holding
        byte-identical content must never be treated as duplicates of each
        other — matching cross-org would leak one org's virtualRecordId,
        summaryDocumentId, and graph relationships onto another org's record.

        Args:
            record_key (str): The key of the current record to exclude from results
            md5_checksum (str): MD5 checksum of the record content
            org_id (str): Restrict dedup matching to this org only
            record_type (Optional[str]): Optional record type to filter by
            size_in_bytes (Optional[int]): Optional file size in bytes to filter by
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of duplicate records that match the criteria
        """
        pass

    @abstractmethod
    async def find_next_queued_duplicate(
        self,
        record_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """
        Find the next QUEUED duplicate record with the same md5 hash.
        Works with all record types by querying the RECORDS collection directly.
        Only a live record is returned; the reference record may be in the trash.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: The next queued record if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_queued_duplicates_status(
        self,
        record_id: str,
        new_indexing_status: str,
        virtual_record_id: str | None = None,
        transaction: str | None = None,
        reason: str | None = None,
    ) -> int:
        """
        Find all QUEUED duplicate records with the same md5 hash and update their status.

        Scoped to the reference record's org; a reference record with no
        orgId updates nothing.

        Args:
            record_id (str): The record ID to use as reference for finding duplicates
            new_indexing_status (str): The new indexing status to set
            virtual_record_id (Optional[str]): Optional virtual record ID to set
            transaction (Optional[str]): Optional transaction ID
            reason (Optional[str]): Optional failure/status reason to set on duplicates

        When at least one duplicate is promoted to COMPLETED or EMPTY, the
        reference record is marked ``duplicateReconcilePending`` in the same
        batch, with ``duplicateReconcileDueAt`` set
        ``DUPLICATE_RECONCILE_GRACE_MS`` ahead and its attempt count reset, so
        a crash before its taxonomy is copied is repaired by the retry sweep.

        Returns:
            int: Number of records updated
        """
        pass

    @abstractmethod
    async def copy_document_relationships(
        self,
        source_key: str,
        target_key: str,
        transaction: str | None = None
    ) -> bool:
        """
        Copy all relationships (edges) from source document to target document.
        This includes departments, categories, subcategories, languages, and topics.

        Args:
            source_key (str): Key/ID of the source document
            target_key (str): Key/ID of the target document
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    # ==================== Permission Operations ====================

    @abstractmethod
    async def batch_upsert_records(
        self,
        records: list,
        transaction: str | None = None,
        *,
        release_trashed_external_ids: bool = False,
    ) -> None:
        """
        Batch upsert records (base record + specific type + IS_OF_TYPE edge).

        High-level method that handles:
        1. Upserting base record to records collection
        2. Upserting specific type (files, mails, etc.)
        3. Creating IS_OF_TYPE edges

        Args:
            records (List[Record]): List of Record objects
            transaction (Optional[Any]): Optional transaction context
            release_trashed_external_ids: Records in the trash in the same
                connector that hold a record's external id give it up, keeping
                it in ``trashedExternalRecordId`` behind a
                ``TRASHED_EXTERNAL_ID_PREFIX`` id. This happens in the same
                statement as the base record's write, so a write the graph
                refuses leaves them holding it, even where each statement
                commits on its own (Neo4j by default).
        """
        pass

    @abstractmethod
    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str,
        transaction: str | None = None
    ) -> None:
        """
        Create a relation edge between two records.

        Args:
            from_record_id (str): Source record ID
            to_record_id (str): Target record ID
            relation_type (str): Type of relation (e.g., "PARENT_CHILD", "ATTACHMENT", "SIBLING", "BLOCKS", etc.)
            transaction (Optional[str]): Optional transaction ID
        """
        pass

    async def upsert_record_under_parent(
        self,
        record: "Record",
        parent_record_id: str | None,
        transaction: str | None = None,
    ) -> None:
        """Upsert a moved *record* and make *parent_record_id* its only PARENT_CHILD parent.

        None leaves it under no parent: the root of its knowledge base. A parent
        that is not in the graph, or is in the trash, raises ``MoveDestinationMissing``
        before anything is written: a folder deleted while the move was on its way
        would take the item out of its old folder and put it in none, and one moved
        to the trash would hide it there. Records in the trash holding
        the record's external id give it up, as in ``batch_upsert_records``.
        Concrete by design: a provider with real transactions keeps the separate
        calls. Neo4j overrides it with one statement: with the old edge deleted on
        its own, a move that failed afterwards left the item, and everything
        beneath it, in no folder at all.
        """
        if parent_record_id:
            parent = await self.get_document(
                parent_record_id, CollectionNames.RECORDS.value, transaction, raise_on_error=True
            )
            if not parent or not is_live_record(parent):
                raise MoveDestinationMissing(record.id, parent_record_id)
        await self.delete_parent_child_edge_to_record(record.id, transaction)
        await self.batch_upsert_records([record], transaction, release_trashed_external_ids=True)
        if parent_record_id:
            await self.create_record_relation(
                parent_record_id, record.id, RecordRelations.PARENT_CHILD.value, transaction
            )
            # Out of the KB root: a second hierarchy parent would list it twice.
            await self._delete_hierarchy_edge(
                record.connector_id, CollectionNames.APPS.value, record.id, transaction
            )
        else:
            # At the root it hangs off its knowledge base's App, or the hub cannot reach it.
            now = get_epoch_timestamp_in_ms()
            await self.batch_create_edges(
                [{
                    "from_id": record.connector_id,
                    "from_collection": CollectionNames.APPS.value,
                    "to_id": record.id,
                    "to_collection": CollectionNames.RECORDS.value,
                    "relationshipType": RecordRelations.PARENT_CHILD.value,
                    "createdAtTimestamp": now,
                    "updatedAtTimestamp": now,
                }],
                CollectionNames.NODE_RELATIONS.value,
                transaction,
            )

    @abstractmethod
    async def batch_upsert_record_groups(
        self,
        record_groups: list,
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert record groups (folders/spaces/categories).

        Args:
            record_groups (List[RecordGroup]): List of RecordGroup objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_record_group_relation(
        self,
        record_id: str,
        record_group_id: str,
        transaction: str | None = None
    ) -> None:
        """
        Create BELONGS_TO edge from record to record group.

        Args:
            record_id (str): Record ID
            record_group_id (str): Record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_record_groups_relation(
        self,
        child_id: str,
        parent_id: str,
        transaction: str | None = None
    ) -> None:
        """
        Create BELONGS_TO edge from child record group to parent record group.

        Args:
            child_id (str): Child record group ID
            parent_id (str): Parent record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def create_inherit_permissions_relation_record_group(
        self,
        record_id: str,
        record_group_id: str,
        transaction: str | None = None
    ) -> None:
        """
        Create INHERIT_PERMISSIONS edge from record to record group.

        Args:
            record_id (str): Record ID
            record_group_id (str): Record group ID
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    async def get_virtual_record_ids_shared_outside_connector(
        self,
        connector_id: str,
        transaction: str | None = None,
    ) -> list[str]:
        """VRIDs of this connector's records that a live record elsewhere also holds.

        Deduplicated content is stored once, under whichever connector indexed
        it first, and every other record with that VRID reads the same storage
        documents. Before a connector's storage is deleted, these are the VRIDs
        whose documents must survive.

        Same liveness rule as ``get_records_by_virtual_record_id``: soft-deleted
        records do not count, and the lookup is not scoped by connector type.

        Raises on failure rather than returning an empty list — an empty answer
        tells the caller it may delete shared storage.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_records_by_virtual_record_id(
        self,
        virtual_record_id: str,
        accessible_record_ids: list[str] | None = None,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[str]:
        """Keys of every live record sharing this virtualRecordId.

        The authority for vector deletion. A point may be removed only when
        this returns nothing: content is deduplicated, so one virtualRecordId
        can be reached through several records, and deleting on the strength of
        one of them disappearing would take the others' vectors with it.

        Two properties the callers depend on, and that any implementation must
        preserve:

        - **Not scoped by connector or org.** The question is "does anything at
          all still reference this content", so a record belonging to another
          connector must be returned. Narrowing it would make the delete path
          unsafe rather than stricter.
        - **Soft-deleted records excluded.** A tombstone answering "yes, still
          referenced" would strand its vectors permanently.

        ``accessible_record_ids`` narrows to a permission-filtered set for read
        paths; the delete path passes nothing and sees everything.

        ``visibility=DELETED`` asks the other question the orphan sweeper needs:
        does a record in the trash still hold this content for the purge?

        Args:
            virtual_record_id: The content identity to look up
            accessible_record_ids: Optional permission filter (read paths only)
            transaction: Optional transaction ID

        Returns:
            List[str]: Record keys, empty when nothing references it
        """
        raise NotImplementedError

    @abstractmethod
    async def get_accessible_connector_types(
        self,
        user_id: str,
        org_id: str,
    ) -> list[str]:
        """Distinct connector *types* this user can reach, as ``Connectors`` values.

        The type ("DRIVE", "SLACK", "KB"), not the instance id — two Google
        Drive connections yield one entry. Used by the query path to narrow a
        multi-collection search to the collections the user could match in at
        all, instead of fanning out across every one the deployment manages.

        Purely an optimization, and callers must stay correct without it: an
        empty list means "could not narrow", never "this user can reach
        nothing". Permission enforcement stays with
        ``get_accessible_virtual_record_ids``, which gates the actual results —
        this only decides where to look.

        Args:
            user_id (str): The userId field value in the users collection
            org_id (str): The organization to scope the lookup to

        Returns:
            List[str]: Distinct connector type values, in no particular order
        """
        raise NotImplementedError

    @abstractmethod
    async def get_accessible_virtual_record_ids(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]] | None = None,
        time_range: dict[str, int] | None = None,
        *,
        raise_on_error: bool = False,
        exclude_app_ids: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        """
        Get a mapping of virtualRecordId -> recordId for all records accessible to a user.

        Each virtualRecordId maps to the specific recordId (the record's key/id) that the user
        has permission to access. This prevents cross-connector leakage where multiple connectors
        share the same virtualRecordId but only one is accessible to the user.

        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The org_id to filter anyone collection
            filters (Optional[Dict[str, List[str]]]): Optional filters for departments, categories, languages, topics etc.
                Format: {
                    'departments': [dept_ids],
                    'categories': [cat_ids],
                    'subcategories1': [subcat1_ids],
                    'subcategories2': [subcat2_ids],
                    'subcategories3': [subcat3_ids],
                    'languages': [language_ids],
                    'topics': [topic_ids],
                    'kb': [kb_ids],
                    'apps': [connector_ids]
                }
            time_range (Optional[Dict[str, int]]): Optional source-creation time bounds in epoch ms.
                Keys: 'source_created_after_ms' (inclusive lower), 'source_created_before_ms' (inclusive upper).
                Filters on record.sourceCreatedAtTimestamp.
            raise_on_error: raise when any part of the permission read fails,
                instead of leaving that part out. Without it, a failed read and a
                user who can reach nothing both return {}.
            exclude_app_ids: apps to leave out even where the user has access,
                e.g. the Acme Corp demo for someone who switched it off.

        Returns:
            Dict[str, str]: Mapping of virtualRecordId -> recordId
        """
        pass

    async def get_accessible_containers(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]] | None = None,
        time_range: dict[str, int] | None = None,
    ) -> AccessibleContainers:
        """The containers a user may search, as an alternative to enumerating records.

        Bounded by how many connectors and record groups a user reaches rather
        than how many records, which is the point: the id list this replaces
        grows with the corpus and is already past OpenSearch's default
        ``index.max_terms_count`` on large tenants.

        Concrete rather than abstract because the safe answer here is not the
        empty one. An all-empty result with no ``fallback_reason`` reads
        as "this user can search nothing" — a silent, total outage for any
        provider that had simply not implemented it. Encoding the fallback once
        is the value; a provider that overrides this opts in, and one that does
        not keeps today's behaviour. Same reasoning as
        ``get_node_relations_batch``.

        ``apps`` and ``kb`` scope the result: an implementation must narrow
        every set to ``requested_scope_ids(filters)`` and echo that scope in
        ``scope_connector_ids``. Any other filter key or a ``time_range`` must
        return a ``fallback_reason`` rather than silently dropping the
        narrowing — see ``_unsupported_container_filters``.

        Returns:
            AccessibleContainers. Check ``usable`` before building a filter from
            it; ``fallback_reason`` says why not when it is False.
        """
        return AccessibleContainers(
            fallback_reason="provider does not implement container filtering"
        )

    @abstractmethod
    async def get_entity_access_context(
        self,
        user_id: str,
        org_id: str,
        source_ids: list[str] | None = None,
        transaction: str | None = None,
        exclude_app_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        """
        The apps and record groups a user can reach, for permission-scoping
        knowledge-graph entity search (``app.modules.retrieval.entity_permissions``).

        Apps:
          - Apps linked by ``userAppRelation``, directly or via a team the
            user belongs to (same paths as ``get_user_apps``).
          - KB apps (``type == "KB"``, ``orgId == org_id``) shared through a
            ``permission`` edge, directly (``type USER``) or via a team
            (``type TEAM``) — KB sharing never creates a ``userAppRelation``.
          - The connector of each ``authenticatedAs`` link (a source account
            the user authenticated that connector as).
          - Narrowed to ``source_ids`` when it is non-empty.
          - Minus ``exclude_app_ids``, even when named in ``source_ids``; their
            record groups drop out with them.

        Record groups (only for apps that are neither KB nor
        ``permissionModel == APP_LEVEL``):
          - Seeded by the Knowledge Hub RecordGroup paths: direct USER
            permission, group/role (GROUP/ROLE edge), org (ORG edge via the
            user's ORGANIZATION ``belongsTo``), and team (TEAM edge). The
            USER, group/role and team paths also run from each linked source
            account, for record groups of that link's connector only.
          - Plus child record groups inheriting from a seed via
            ``inheritPermissions`` (depth 1..``CONTAINER_INHERIT_MAX_DEPTH``,
            through record groups only), skipping seeds with ``hideChildren``.
          - Every group filtered by ``orgId == org_id``, not deleted, and
            ``connectorId`` in the qualifying apps above.

        Args:
            user_id: The ``userId`` field of the user document.
            org_id: Organization to scope apps and record groups to.
            source_ids: Optional app/KB ids to narrow the result to.
            transaction: Optional transaction id.
            exclude_app_ids: Apps to leave out even where the user has access,
                e.g. a demo connector the user switched off.

        Returns:
            ``None`` when the user does not exist, otherwise::

                {
                  "user_key": str,
                  "apps": [{"id", "name", "type", "permissionModel"}],
                  "record_group_ids": [str],
                }

        Raises:
            Exception: on any query failure. Callers fail closed and report
                the failure instead of treating it as "no access".
        """
        pass

    @abstractmethod
    async def get_records_by_record_ids(
        self,
        record_ids: list[str],
        org_id: str,
        visibility: RecordVisibility = RecordVisibility.LIVE,
    ) -> list[dict[str, Any]]:
        """
        Batch fetch full record documents by their record IDs (_key in Arango / id in Neo4j).

        This is used after Qdrant search to fetch the specific permission-verified records
        using the recordIds from the accessible virtual ID map, preventing cross-connector
        leakage.

        Args:
            record_ids: List of record key/id values to fetch
            org_id: Organization ID for additional filtering
            visibility: ``LIVE`` (default) leaves out records in the trash,
                ``DELETED`` returns only those, ``ALL`` returns both.

        Returns:
            List[Dict[str, Any]]: List of full record dictionaries
        """
        pass

    @abstractmethod
    async def batch_upsert_record_permissions(
        self,
        record_id: str,
        permissions: list[dict],
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert permissions for a record.

        Args:
            record_id (str): Record ID
            permissions (List[Dict]): List of permission data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def get_first_user_with_permission_to_node(
        self,
        node_id: str,
        node_collection: str,
        transaction: str | None = None
    ) -> Optional['User']:
        """
        Get the first user with permission to a node.

        Args:
            node_id (str): Node ID
            node_collection (str): Node collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[User]: User object if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_users_with_permission_to_node(
        self,
        node_id: str,
        node_collection: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list['User']:
        """
        Get all users with permission to a node.

        Args:
            node_id (str): Node ID
            node_collection (str): Node collection name
            transaction (Optional[Any]): Optional transaction context
            raise_on_error: Raise when the read fails, instead of answering an
                empty list that reads as "nobody has access"

        Returns:
            List[User]: List of user objects
        """
        pass

    @abstractmethod
    async def get_groups_with_permission_to_node(
        self,
        node_id: str,
        node_collection: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> list['AppUserGroup']:
        """
        Get the user groups holding a direct permission edge to a node.

        Args:
            node_id (str): Node ID
            node_collection (str): Node collection name
            transaction (Optional[Any]): Optional transaction context
            raise_on_error: Raise when the read fails, instead of answering an
                empty list that reads as "no group has access"

        Returns:
            List[AppUserGroup]: The groups with a permission edge to the node
        """
        pass

    @abstractmethod
    async def check_record_access_with_details(
        self,
        user_id: str,
        org_id: str,
        record_id: str,
    ) -> dict | None:
        """
        Check record access and return record details if accessible.

        Args:
            user_id: The userId field value in users collection
            org_id: The organization ID
            record_id: The record ID to check access for

        Returns:
            Dict with record, knowledgeBase, folder, metadata, permissions if accessible;
            None if not, and always None for a record in the trash.
        """
        pass

    @abstractmethod
    async def get_record_owner_source_user_email(
        self,
        record_id: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> str | None:
        """
        Get the owner's source email for a record.

        Args:
            record_id (str): Record ID
            transaction (Optional[Any]): Optional transaction context
            raise_on_error (bool): Propagate a failed read instead of answering
                None, which a caller would take for "no owner"

        Returns:
            Optional[str]: Owner email if found, None otherwise
        """
        pass

    # ==================== File/Parent Operations ====================

    # ==================== Sync Point Operations ====================

    @abstractmethod
    async def get_sync_point(
        self,
        key: str,
        collection: str,
        transaction: str | None = None,
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """
        Get a sync point by key.

        Args:
            key (str): Sync point key
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context
            raise_on_error: Propagate the failure instead of answering None.

        Returns:
            Optional[Dict]: Sync point data if found, None otherwise
        """
        pass

    @abstractmethod
    async def upsert_sync_point(
        self,
        sync_point_key: str,
        sync_point_data: dict,
        collection: str,
        transaction: str | None = None
    ) -> bool:
        """
        Upsert a sync point.

        Args:
            sync_point_key (str): Sync point key
            sync_point_data (Dict): Sync point data
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def remove_sync_point(
        self,
        key: str,
        collection: str,
        transaction: str | None = None
    ) -> None:
        """
        Remove sync point by syncPointKey field.

        Args:
            key (str): Sync point key
            collection (str): Collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete_sync_points_by_connector_id(
        self,
        connector_id: str,
        transaction: str | None = None
    ) -> tuple[int, bool]:
        """
        Delete all sync points for a given connector.

        Args:
            connector_id (str): The connector ID to delete sync points for
            transaction (Optional[str]): Optional transaction context

        Returns:
            Tuple[int, bool]: Tuple of (deleted_count, success_flag)
        """
        pass

    @abstractmethod
    async def delete_connector_sync_edges(
        self,
        connector_id: str,
        transaction: str | None = None
    ) -> tuple[int, bool]:
        """
        Delete only sync-created edges for a connector (belongsTo, nodeRelations,
        permission, inheritPermissions, userAppRelation). Does not delete nodes or
        isOfType/indexing data. Used for full sync reset.

        Args:
            connector_id: The connector ID (app _key).
            transaction: Optional transaction context.

        Returns:
            Tuple of (total_deleted_edges_count, success_flag).
        """
        pass

    @abstractmethod
    async def mark_connector_sync_edges(
        self,
        connector_id: str,
        generation: int,
        transaction: str | None = None
    ) -> tuple[int, bool]:
        """Tag the edges ``delete_connector_sync_edges`` would delete with ``generation``
        (``sync_sweep.PENDING_SWEEP``) before a full sync. Every edge write clears the tag.

        Returns:
            Tuple of (tagged_edges_count, success_flag).
        """
        pass

    @abstractmethod
    async def sweep_connector_sync_edges(
        self,
        connector_id: str,
        generation: int,
        transaction: str | None = None,
        keep_collections: tuple[str, ...] = (),
    ) -> tuple[int, bool]:
        """Delete the connector's sync edges still tagged with ``generation`` after a
        successful full sync: the ones it did not produce again. Edges of
        ``keep_collections`` stay, tagged.

        Deletes nothing and reports failure when the sync wrote none of the
        connector's edges besides user-app gates, since such a run read nothing
        from its source.

        Returns:
            Tuple of (deleted_edges_count, success_flag).
        """
        pass

    @abstractmethod
    async def clear_connector_sync_edge_tags(
        self,
        connector_id: str,
        generation: int,
        transaction: str | None = None
    ) -> tuple[int, bool]:
        """Remove ``generation``'s tag, and any older one, from the connector's sync
        edges: a full sync that ends without a sweep leaves its edges as stored.

        Returns:
            Tuple of (cleared_edges_count, success_flag).
        """
        pass

    # ==================== Batch/Bulk Operations ====================

    @abstractmethod
    async def batch_upsert_app_users(
        self,
        users: list,
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert app users with org and app relations.

        Creates users if they don't exist, creates org relation and user-app relation.

        Args:
            users (List[AppUser]): List of AppUser objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def ensure_team_app_edge(
        self,
        connector_id: str,
        org_id: str,
        transaction: Optional[str] = None
    ) -> None:
        """
        Ensure the org's "All" team has an edge to the app in userAppRelation.
        Idempotent: creates teams/all_{org_id} -> apps/{connector_id} if not present.
        Used by TEAM-scope connectors so all org members get app access via the team.

        The All team and user PERMISSION edges are created by migration and user-added
        events (see ensure_all_team_with_users); this method only creates the team->app edge.
        """
        pass

    # ==================== Authenticated-as (creator -> source account) ====================

    @abstractmethod
    async def upsert_authenticated_as(
        self,
        creator_key: str,
        source_user_key: str,
        connector_id: str,
        org_id: str,
        transaction: str | None = None,
    ) -> None:
        """
        Link a connector creator to the source-account user the connector is authenticated as.

        Exactly one link exists per connector instance: re-linking with a different
        source user repoints the existing link.
        """
        pass

    @abstractmethod
    async def remove_authenticated_as(
        self,
        connector_id: str,
        transaction: str | None = None,
    ) -> bool:
        """Remove the authenticated-as link for a connector instance. True if one existed."""
        pass

    @abstractmethod
    async def ensure_all_team_with_users(self, org_id: str) -> None:
        """
        Ensure the org's 'All' team exists and every active org user has a PERMISSION edge.

        Creates team node with id=all_{org_id} if missing, fetches all active users,
        and adds PERMISSION edges for users not already in the team.
        Oldest user (by createdAtTimestamp) gets OWNER; subsequent users get READER.

        Idempotent, runs without transaction, safe to call multiple times.

        Args:
            org_id: Organization ID
        """
        pass

    @abstractmethod
    async def add_user_to_all_team(self, org_id: str, user_key: str) -> None:
        """
        Add a specific user to the org's 'All' team with a PERMISSION edge.

        Ensures All team exists, checks if user already has PERMISSION edge,
        and adds it if missing. First user in team gets OWNER, subsequent get READER.

        Idempotent, safe to call multiple times for same user.

        Args:
            org_id: Organization ID
            user_key: User node ID (graph key)
        """
        pass

    @abstractmethod
    async def batch_upsert_user_groups(
        self,
        user_groups: list,
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert user groups.

        Args:
            user_groups (List[AppUserGroup]): List of AppUserGroup objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_app_roles(
        self,
        app_roles: list,
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert app roles.

        Args:
            app_roles (List[AppRole]): List of AppRole objects
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_upsert_orgs(
        self,
        orgs: list[dict],
        transaction: str | None = None
    ) -> None:
        """
        Batch upsert organizations.

        Args:
            orgs (List[Dict]): List of organization data
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def batch_create_user_app_edges(
        self,
        edges: list[dict]
    ) -> int:
        """
        Batch create user-app relationship edges.

        Args:
            edges (List[Dict]): List of edge data

        Returns:
            int: Number of edges created
        """
        pass

    @abstractmethod
    async def batch_create_entity_relations(
        self,
        edges: list[dict],
        transaction: Optional[str] = None
    ) -> int:
        """
        Batch create entity relation edges.

        Args:
            edges (List[Dict]): List of edge data
            transaction (Optional[str]): Optional transaction context
        Returns:
            int: Number of edges created
        """
        pass
    # ==================== Entity ID Operations ====================

    @abstractmethod
    async def get_entity_id_by_email(
        self,
        email: str,
        transaction: str | None = None
    ) -> str | None:
        """
        Get entity ID (user or group) by email.

        Args:
            email (str): Email address
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[str]: Entity ID if found, None otherwise
        """
        pass

    @abstractmethod
    async def bulk_get_entity_ids_by_email(
        self,
        emails: list[str],
        transaction: str | None = None
    ) -> dict[str, tuple[str, str, str]]:
        """
        Bulk get entity IDs for multiple emails.

        Args:
            emails (List[str]): List of email addresses
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Dict[str, Tuple[str, str, str]]: Map of email to (entity_id, collection, type)
        """
        pass

    # ==================== Connector-Specific Operations ====================

    @abstractmethod
    async def delete_records_and_relations(
        self,
        record_key: str,
        *,
        hard_delete: bool = False,
        transaction: str | None = None,
    ) -> bool:
        """
        Delete a record and all its relations. False when there was no such record.
        A raw primitive: children are not re-pointed and no delete event is published.

        Args:
            record_key (str): Record key to delete
            hard_delete (bool): Whether to permanently delete or mark as deleted
            transaction (Optional[Any]): Optional transaction context
        """
        pass

    @abstractmethod
    async def delete_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
        transaction: str | None = None,
        *,
        soft_delete: bool = False,
        delete_source: DeleteSource = DeleteSource.USER,
    ) -> dict:
        """
        Main entry point for record deletion - routes to connector-specific methods.

        Args:
            record_id (str): Record ID to delete
            user_id (str): User ID performing the deletion
            org_id (str): Caller's organization; records outside it are reported as not found
            transaction (Optional[str]): Optional transaction context
            soft_delete (bool): After the same permission checks, move to the trash
                (``soft_delete_records``) exactly what the hard delete would remove,
                instead of removing it. The caller publishes the vectors-only
                cleanup from the result's ``virtualRecordIds``.
            delete_source (DeleteSource): Who the soft delete is recorded as.
                USER names ``user_id`` as the deleter; CONNECTOR (a sync delete)
                names no one.

        Returns:
            Dict: Result with success status and reason
        """
        pass

    @abstractmethod
    async def delete_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: str | None = None,
        *,
        soft_delete: bool = False,
    ) -> dict | None:
        """
        Delete a record by external ID.

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            user_id (str): User ID performing the deletion
            transaction (Optional[str]): Optional transaction context
            soft_delete (bool): Move to the trash, as a CONNECTOR delete, exactly
                what the hard delete would remove. A record already in the trash
                is left alone.

        Returns:
            The ``delete_record`` result, whose ``eventData`` the caller publishes
            after its transaction commits; None when there was no such record.
        """
        pass

    @abstractmethod
    async def remove_user_access_to_record(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: str | None = None
    ) -> None:
        """
        Remove a user's access to a record (for inbox-based deletions).

        Args:
            connector_id (str): Connector ID
            external_id (str): External record ID
            user_id (str): User ID to remove access from
            transaction (Optional[str]): Optional transaction context
        """
        pass

    @abstractmethod
    async def get_uploaded_document_ids(
        self,
        connector_id: str,
        transaction: str | None = None,
        *,
        under_record_ids: list[str] | None = None,
        among: list[str] | None = None,
    ) -> list[str]:
        """Storage document ids of the uploaded files among a connector's (or KB's) records.

        ``under_record_ids`` limits it to those records and everything they contain
        (PARENT_CHILD / ATTACHMENT, as a delete cascades); ``among`` to these ids.
        Read before the records are deleted: afterwards nothing points at them.
        A failed query raises: an empty list must mean there are no uploads, since
        the delete then goes ahead without scheduling any file removal.
        """
        pass

    @abstractmethod
    async def delete_records_recursive(
        self,
        record_ids: list[str],
        connector_id: str,
        transaction: str | None = None,
        cascade_children: bool = True,
        within_folder_id: str | None = None,
        *,
        include_trashed_roots: bool = False,
    ) -> dict:
        """Delete records and their owned descendants, scoped by connector_id.

        With *within_folder_id*, a root is deleted only if it sits under that
        folder through PARENT_CHILD / ATTACHMENT edges, checked in the same query
        as the delete; any other root is reported as failed and kept.

        A root in the trash is refused unless *include_trashed_roots*, which is
        for removing what the source no longer has.

        When *cascade_children* is True (default), traverses both PARENT_CHILD and
        ATTACHMENT edges — deleting an entire containment subtree (folders, nested
        files, etc.).  When False, only ATTACHMENT edges are traversed so child
        records linked via PARENT_CHILD survive (e.g. stories under a deleted epic).
        Survivors whose ``externalParentId`` points at a deleted root have that
        field cleared to null only if they already ``BELONGS_TO`` a RecordGroup.
        Each such survivor is reported under the ``reparented`` key as
        ``{record_id, record_group_id, inherits}`` (``inherits``: it inherited
        permissions from the deleted parent). The edge sweep below removes the
        hierarchy and inheritance edges that pointed at the deleted parent, and the
        caller must re-point them: at the deleted record's own parent when that
        parent is a record, and at the record group only when the deleted record
        hung directly off the group.

        All edges touching the deleted nodes are swept regardless of
        *cascade_children*, type docs removed, and a deleteRecord event emitted per
        record that carries a virtualRecordId (Qdrant cleanup).
        """
        raise NotImplementedError

    @abstractmethod
    async def migrate_legacy_relation_edge(
        self,
        legacy_collection: str,
        legacy_relationship_type: str,
    ) -> dict:
        """Move the hierarchy edge onto its current name.

        The legacy names are passed in so the only module naming them is the
        migration that owns the rename; a guard test keeps them out of the rest.
        Arango renames the collection; Neo4j has no rename for a relationship
        type and recreates each edge under the new type.

        Idempotent: a store already on the new name reports ``already_current``
        and changes nothing.

        Returns:
            Dict: {"migrated": int, "already_current": bool}
        """
        raise NotImplementedError

    @abstractmethod
    async def soft_delete_records(
        self,
        record_ids: list[str],
        connector_id: str,
        *,
        delete_source: str,
        batch_id: str,
        deleted_by_user_id: str | None = None,
        follow: tuple[str, ...] = ("PARENT_CHILD", "ATTACHMENT"),
        transaction: str | None = None,
        within_folder_id: str | None = None,
        include_trashed_roots: bool = False,
    ) -> dict:
        """Move live records, and their live descendants, to the trash.

        Sets ``isDeleted``, ``deletedAtTimestamp``, ``deleteSource``,
        ``deleteBatchId`` and ``deletedByUserId`` in one transaction, in chunks.
        Nodes, edges, permissions and type docs are kept, so the batch can be
        restored as it was.

        Roots are scoped by ``connector_id`` (the KB id for a KB) and must be
        live, unless *include_trashed_roots*: a caller removing what the source
        no longer has also walks from a root already in the trash, which keeps
        its own batch while its live descendants are marked. Descendants are reached through hierarchy edges whose
        ``relationshipType`` is in ``follow``: both kinds for a folder subtree,
        ``("ATTACHMENT",)`` to leave PARENT_CHILD children alone, ``()`` for the
        roots only. A descendant already in the trash keeps its own batch.
        With *within_folder_id*, a root is taken only if that folder reaches it
        through PARENT_CHILD / ATTACHMENT edges, as ``delete_records_recursive``
        checks it.

        Returns ``success``, ``soft_deleted_records`` ({record_id, name, virtual_record_id}),
        ``failed_records`` (roots that were missing, trashed or out of scope),
        ``total_requested``, ``successfully_deleted`` (roots),
        ``failed_count``, ``virtual_record_ids`` (distinct, for vector cleanup),
        ``org_id`` and ``batch_id``. A failure raises; nothing is marked.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_records_in_delete_batch(
        self,
        batch_id: str,
        org_id: str,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every record in the trash under one ``deleteBatchId``, in this org.

        Each item is ``record`` (the stored document, ``_key`` set on both
        backends), ``parentId``, ``parentRelation`` (``PARENT_CHILD`` or
        ``ATTACHMENT``), ``parentIsDeleted``, ``parentBatchId`` and
        ``parentName`` for the record it hangs under (all None at a KB or group
        root), and ``isFile`` and ``fileMimeType`` from its type doc (None when
        it has none). A failed read raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_trashed_records(
        self,
        connector_id: str,
        org_id: str,
        *,
        skip: int = 0,
        limit: int = 25,
        single_file_batches_only: bool = False,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """One page of what delete actions put in the trash in one connector (a KB), newest first.

        One item per delete batch, as restore brings a batch back whole. A
        batch's roots are its records in the trash, in ``org_id`` and
        ``connector_id``, with a ``deletedAtTimestamp`` and a ``deleteBatchId``,
        whose ``PARENT_CHILD`` or ``ATTACHMENT`` parent is not in the same batch:
        a folder deleted with its contents has one, a multi-select delete one
        per item selected. With ``single_file_batches_only``, only batches of
        one file are listed (what a file organizer may restore). Sorted by the
        batch's ``deletedAtTimestamp``, then batch id, both descending, and
        paged inside the query; ``total`` counts batches in a separate read.
        Each read walks only this connector's trash, through an index that
        holds only the trash, at a fixed cost per record, so a deleted folder's
        files never cost a read of their whole batch.

        Returns ``items`` and ``total``. Each item stands for one batch through
        its first root by key: ``record`` (that root's stored document, ``_key``
        set on both backends), ``parentId``, ``parentName`` and
        ``parentIsDeleted`` for the record it hangs under (None at the KB
        root), ``isFile``, ``fileMimeType`` and ``sizeInBytes`` from its type
        doc, ``rootCount`` (the batch's roots), ``otherRootNames`` (up to
        ``TRASH_LIST_OTHER_ROOT_NAMES`` other roots' names, by key),
        ``batchSize`` (records in the batch in this connector) and
        ``deletedByName`` and ``deletedByEmail`` for the user in
        ``deletedByUserId`` (None when unknown). A failed read raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def restore_records(
        self,
        restores: list[dict[str, Any]],
        batch_id: str | None,
        transaction: str | None = None,
        *,
        connector_id: str | None = None,
        require_live_parent: bool = False,
    ) -> list[str]:
        """Bring records back from the trash; return the ids restored.

        Each item is ``{"id": key, "set": {field: value}}``; ``set`` (optional)
        is written as well, for an indexing status. An item may also carry
        ``reclaimExternalRecordId``, the external id it gave up, to take back
        within ``connector_id`` (required then): records in the trash outside
        this batch that hold it give it up, keeping it in
        ``trashedExternalRecordId`` behind a ``TRASHED_EXTERNAL_ID_PREFIX`` id.
        All or nothing: every item must still be in the trash under
        ``batch_id``, and no live record and no other item may hold an id being
        taken back, or nothing is written and the result is empty, so a restore
        racing a purge or another restore never brings back part of a batch.
        With ``require_live_parent``, every record an item hangs under (its
        ``PARENT_CHILD`` or ``ATTACHMENT`` parent) must also be live or among
        the items, checked in the same write, so a folder trashed after the
        caller looked keeps its file from coming back under it. A
        write the graph refuses partway also leaves every record as it was,
        even where each statement commits on its own (Neo4j by default). The
        delete fields (``isDeleted``, ``deletedAtTimestamp``, ``deleteSource``,
        ``deleteBatchId``, ``deletedByUserId``, the purge counters and
        ``trashedExternalRecordId``) are cleared. A failure raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_purgeable_trashed_records(
        self,
        org_id: str,
        deleted_before: int,
        *,
        after: tuple[int, str] | None = None,
        limit: int = 500,
        max_attempts: int = 5,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """One page of this org's trash that the purge may remove, oldest first.

        A record qualifies while ``isDeleted`` is true and its
        ``deletedAtTimestamp`` is set and at most ``deleted_before``, and it has
        failed fewer than ``max_attempts`` purges. The walk is keyset by
        (``deletedAtTimestamp``, key), starting after ``after``. Records of a
        connector being deleted (``status`` DELETING), and records that still
        have a PARENT_CHILD or ATTACHMENT child of any state, are left out of
        the page but still move the cursor; ``held`` counts the second kind,
        which wait until their children are purged. Returns ``records``
        (``trash_purge_row`` shapes), ``held`` and ``next``, the ``after`` for
        the next page, or None after the last one. A failed read raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def is_trash_walk_index_ready(self) -> bool:
        """Whether the index ``get_purgeable_trashed_records`` walks is built and usable.

        Without it the walk still answers correctly, but reads the whole trash for
        every page, so the purge waits for it instead. A failed read raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def purge_trashed_records(
        self,
        record_ids: list[str],
        org_id: str,
        deleted_before: int,
        *,
        max_attempts: int = 5,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """Remove records from the trash for good: every edge, the type doc and the vertex.

        Each record is checked again inside the delete, after its write lock is
        taken: it must still be in the trash in ``org_id``, since
        ``deleted_before`` or earlier, under ``max_attempts`` failures, with its
        connector not being deleted and no record under it at all (PARENT_CHILD
        or ATTACHMENT, live or trashed), including one linked while the purge
        runs. A record restored meanwhile is left alone. All or nothing; a
        failure raises and removes nothing, and ``GraphLockUnavailableError``
        means the locks could not be taken, which says nothing about the
        records. Returns ``purged`` (the ``trash_purge_row`` of each record
        removed, read in the same write) and ``kept`` (records still stored in
        ``org_id`` and left in place). An id in neither is not stored, which
        includes one an earlier attempt removed although its answer was lost.
        """
        raise NotImplementedError

    @abstractmethod
    async def record_purge_failure(
        self,
        record_ids: list[str],
        org_id: str,
        error: str,
        transaction: str | None = None,
    ) -> int:
        """Count one failed purge on each record still in the trash; return how many were counted.

        Adds one to ``purgeAttempts`` and stores ``error`` in ``purgeLastError``.
        A record restored meanwhile is not touched. A failure raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_trash_purge_stats(
        self,
        org_id: str,
        max_attempts: int = 5,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """This org's trash, for the purge's gauges.

        ``trashed`` counts records in the trash with a ``deletedAtTimestamp``,
        ``stuck`` those that failed ``max_attempts`` purges, and
        ``oldestDeletedAt`` is the earliest ``deletedAtTimestamp`` among the rest
        (None when there are none). A failed read raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def take_back_kept_record_group(self, group_id: str, transaction: str | None = None) -> bool:
        """Clear a record group's kept-for-the-trash mark, before a sync files a record under it.

        A write on the group itself, so it waits for a purge that is deleting the
        group (Neo4j locks the node; ArangoDB's purge locks the collections), and
        the purge, which checks the mark again under that lock, keeps a group taken
        back first. Returns False when the group is gone, deleted meanwhile, and
        the caller makes a new one; the answer comes from a read made after the
        write, since a Neo4j write that waited on a node deleted meanwhile still
        reports its row. A failure of any other kind raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def purge_trash_kept_record_groups(
        self,
        org_id: str,
        *,
        limit: int = 100,
        transaction: str | None = None,
    ) -> list[str]:
        """Delete record groups kept only for the trash, once nothing belongs to them.

        A group the source removed while records in the trash still belonged to
        it is kept with ``isDeletedAtSource`` set (``on_record_group_deleted``).
        It goes with its edges once no record, live or trashed, and no child
        group belongs to it (BELONGS_TO, INHERIT_PERMISSIONS or
        ``recordGroupId``) and its connector is not being deleted, checked again
        inside the delete, so a record attached while it runs keeps the group. A group the source lists again has the mark cleared
        by its upsert and is never removed here. A group with no ``orgId`` (one
        a sync created from a record) belongs to its connector's org. Returns
        the ids removed, at most ``limit``. A failure raises.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_single_record(
        self,
        record_id: str,
        transaction: str | None = None,
    ) -> dict:
        """Delete one record vertex — no containment walk.

        Same post-inventory cleanup as ``delete_records_recursive`` (all edges,
        isOfType type doc, records vertex, optional deleteRecord payload) but
        inventory is only the given record. Children stay. Missing/empty id is a
        no-op success.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_connector_instance(
        self,
        connector_id: str,
        org_id: str,
        transaction: str | None = None
    ) -> dict[str, Any]:
        """
        Delete a connector instance and all its related data.

        This method performs a comprehensive deletion of:
        - All records associated with the connector
        - All record groups, roles, groups, drives
        - All edges (permissions, relations, classifications)
        - The connector app node itself
        - Org-app relation edges

        Classification nodes (departments, categories, topics, languages) are NOT deleted
        as they are shared resources across connectors.
        Users are NOT deleted - only userAppRelation edges are removed.

        Args:
            connector_id (str): The connector instance ID
            org_id (str): The organization ID for validation
            transaction (Optional[str]): Optional transaction context

        Returns:
            Dict[str, Any]: Dictionary containing:
                - success (bool): Whether deletion was successful
                - virtual_record_ids (List[str]): List of virtual record IDs for Qdrant cleanup
                - connector_name (str | None): The app's type/Connectors enum value —
                  needed by the vector cleanup consumer to resolve which collection(s)
                  this connector's data lives in under a per-connector-type strategy
                - deleted_records_count (int): Number of records deleted
                - deleted_record_groups_count (int): Number of record groups deleted
                - deleted_roles_count (int): Number of roles deleted
                - deleted_groups_count (int): Number of groups deleted
                - deleted_drives_count (int): Number of drives deleted
                - error (str, optional): Error message if deletion failed
        """
        pass

    @abstractmethod
    async def get_key_by_external_file_id(
        self,
        external_file_id: str
    ) -> str | None:
        """
        Get internal key by external file ID.

        Args:
            external_file_id (str): External file ID

        Returns:
            Optional[str]: Internal key if found, None otherwise
        """
        pass

    @abstractmethod
    async def organization_exists(
        self,
        organization_name: str,
        is_external: bool = False,
    ) -> bool:
        """
        Check if an organization exists.

        Args:
            organization_name (str): Organization name
            is_external (bool): Filter by external flag (default False)

        Returns:
            bool: True if exists, False otherwise
        """
        pass


    @abstractmethod
    async def get_user_sync_state(
        self,
        user_email: str,
        service_type: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get user's sync state for a specific service.

        Args:
            user_email (str): User email
            service_type (str): Service/connector type
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Sync state relation document if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_user_sync_state(
        self,
        user_email: str,
        state: str,
        service_type: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Update user's sync state for a specific service.

        Args:
            user_email (str): User email
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            service_type (str): Service/connector type
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Updated relation document if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_drive_sync_state(
        self,
        drive_id: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get drive's sync state.

        Args:
            drive_id (str): Drive ID
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Drive document with sync state if found, None otherwise
        """
        pass

    @abstractmethod
    async def update_drive_sync_state(
        self,
        drive_id: str,
        state: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Update drive's sync state.

        Args:
            drive_id (str): Drive ID
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Updated drive document if successful, None otherwise
        """
        pass

    # ==================== Page Token Operations ====================

    # ==================== Connector Registry Operations ====================

    @abstractmethod
    async def check_connector_name_exists(
        self,
        collection: str,
        instance_name: str,
        scope: str,
        org_id: str | None = None,
        user_id: str | None = None,
        transaction: str | None = None,
        exclude_connector_id: str | None = None,
    ) -> bool:
        """
        Check if a connector instance name already exists for the given scope.

        Args:
            collection: Collection name (e.g., "apps")
            instance_name: Name to check (will be normalized: lowercase, trimmed)
            scope: Connector scope ("personal" or "team")
            org_id: Organization ID (required for team scope)
            user_id: User ID (required for personal scope)
            transaction: Optional transaction ID
            exclude_connector_id: Connector being renamed; never counts as a clash with itself

        Returns:
            bool: True if name exists, False if available
        """
        pass

    @abstractmethod
    async def batch_update_connector_status(
        self,
        collection: str,
        connector_keys: list[str],
        *,
        is_active: bool,
        is_agent_active: bool,
        transaction: str | None = None,
    ) -> int:
        """
        Batch update isActive and isAgentActive status for multiple connectors.

        Args:
            collection: Collection name (e.g., "apps")
            connector_keys: List of connector instance keys to update
            is_active: New isActive value
            is_agent_active: New isAgentActive value
            transaction: Optional transaction ID

        Returns:
            int: Number of connectors updated
        """
        pass

    @abstractmethod
    async def get_user_connector_instances(
        self,
        collection: str,
        user_id: str,
        org_id: str,
        team_scope: str,
        personal_scope: str,
        transaction: str | None = None,
    ) -> list[dict]:
        """
        Get all connector instances accessible to a user (personal + team).

        Args:
            collection: Collection name (e.g., "apps")
            user_id: User ID
            org_id: Organization ID; only apps linked to it by an org-app edge are returned
            team_scope: Team scope value (e.g., "team")
            personal_scope: Personal scope value (e.g., "personal")
            transaction: Optional transaction ID

        Returns:
            List[Dict]: List of connector instance documents
        """
        pass

    @abstractmethod
    async def get_filtered_connector_instances(
        self,
        collection: str,
        edge_collection: str,
        org_id: str,
        user_id: str,
        scope: str | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 20,
        *,
        exclude_kb: bool = True,
        kb_connector_type: str | None = None,
        is_admin: bool = False,
        is_authenticated: bool | None = None,
        is_active: bool | None = None,
        connector_type_filter: str | None = None,
        is_configured: bool | None = None,
        is_agent_active: bool | None = None,
        allowed_connector_types: list[str] | None = None,
        transaction: str | None = None,
    ) -> tuple[list[dict], int]:
        """
        Get filtered connector instances with pagination.

        Args:
            collection: Collection name (e.g., "apps")
            edge_collection: Edge collection for org-app relation
            org_id: Organization ID; only apps linked to it through ``edge_collection`` are returned
            user_id: User ID. With or without ``scope``, a connector that is not
                team-scoped is returned only when this user created it, admins
                included.
            scope: Optional scope filter ("personal" or "team")
            search: Optional search query (searches name, type, appGroup)
            skip: Number of items to skip
            limit: Maximum number of items to return
            exclude_kb: Whether to exclude KB connector
            kb_connector_type: KB connector type to exclude
            is_admin: When True the caller sees all team-scoped connectors in the
                org regardless of edge membership.  When False only connectors
                reachable via the user's ``userAppRelation`` edge (direct or
                through team ``PERMISSION`` edges) are returned.
            is_authenticated: Optional filter on isAuthenticated field
            is_active: Optional filter on isActive field
            connector_type_filter: Optional exact match on connector type field
            is_configured: Optional filter on isConfigured field
            is_agent_active: Optional filter on isAgentActive field
            allowed_connector_types: When set, only connectors whose type is in
                this list are counted and returned
            transaction: Optional transaction ID

        Returns:
            Tuple of (documents, total_count):
                - List of connector documents
                - Total count of matching documents
        """
        pass

    @abstractmethod
    async def store_page_token(
        self,
        channel_id: str,
        resource_id: str,
        user_email: str,
        token: str,
        expiration: str | None = None,
        transaction: str | None = None
    ) -> dict | None:
        """
        Store page token for a channel/resource.

        Args:
            channel_id (str): Channel ID
            resource_id (str): Resource ID
            user_email (str): User email
            token (str): Page token
            expiration (Optional[str]): Token expiration
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Stored token document if successful, None otherwise
        """
        pass

    @abstractmethod
    async def get_page_token_db(
        self,
        channel_id: str | None = None,
        resource_id: str | None = None,
        user_email: str | None = None,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get page token for specific channel/resource/user.

        Args:
            channel_id (Optional[str]): Channel ID filter
            resource_id (Optional[str]): Resource ID filter
            user_email (Optional[str]): User email filter
            transaction (Optional[Any]): Optional transaction context

        Returns:
            Optional[Dict]: Token document if found, None otherwise
        """
        pass

    # ==================== Utility Operations ====================

    @abstractmethod
    async def check_collection_has_document(
        self,
        collection_name: str,
        document_id: str,
        transaction: str | None = None
    ) -> bool:
        """
        Check if a document exists in a collection.

        Args:
            collection_name (str): Collection name
            document_id (str): Document ID/key
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if document exists, False otherwise
        """
        pass

    @abstractmethod
    async def check_edge_exists(
        self,
        from_key: str,
        to_key: str,
        edge_collection: str,
        transaction: str | None = None
    ) -> bool:
        """
        Check if an edge exists between two nodes.

        Args:
            from_key (str): Source node key
            to_key (str): Target node key
            edge_collection (str): Edge collection name
            transaction (Optional[Any]): Optional transaction context

        Returns:
            bool: True if edge exists, False otherwise
        """
        pass

    @abstractmethod
    async def get_failed_records_by_org(
        self,
        org_id: str,
        connector_id: str
    ) -> list[dict]:
        """
        Get all failed records for an organization and connector.

        Generic method for getting records with indexing status FAILED.
        Records in the trash are left out.

        Args:
            org_id (str): Organization ID
            connector_id (str): Connector ID

        Returns:
            List[Dict]: List of failed record documents
        """
        pass

    @abstractmethod
    async def check_toolset_instance_in_use(
        self,
        instance_id: str,
        transaction: str | None = None
    ) -> list[str]:
        """
        Check if a toolset instance is currently in use by any active agents.

        This method finds all toolset nodes with the given instanceId and checks
        if any non-deleted agents are using them.

        Args:
            instance_id (str): Toolset instance ID to check
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of agent names that are using the toolset instance.
                      Empty list if not in use.
        """
        pass

    @abstractmethod
    async def check_connector_in_use(
        self,
        connector_id: str,
        transaction: str | None = None
    ) -> list[str]:
        """
        Check if a connector is currently in use by any active agents.

        Finds all agentKnowledge nodes referencing the given connectorId and
        returns the names of non-deleted agents linked to them via
        agentHasKnowledge edges.

        Args:
            connector_id (str): Connector ID to check.
            transaction (Optional[str]): Optional transaction ID.

        Returns:
            List[str]: Agent names using the connector. Empty list if not in use.
        """
        pass

    # ==================== Knowledge Hub Operations ====================

    # The listing reads page by keyset, and raise on failure rather than returning
    # an empty page: an empty listing is indistinguishable from "you may see
    # nothing here".

    @abstractmethod
    async def get_knowledge_hub_root_nodes_v2(
        self,
        user_key: str,
        org_id: str,
        user_app_ids: list[str],
        limit: int,
        sort_field: str = "name",
        sort_dir: str = "ASC",
        *,
        after: dict[str, Any] | None = None,
        origins: list[str] | None = None,
        node_types: list[str] | None = None,
        only_containers: bool = False,
        search_query: str | None = None,
        record_types: list[str] | None = None,
        indexing_status: list[str] | None = None,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
        size: dict[str, int | None] | None = None,
        connector_ids: list[str] | None = None,
        direction: str = "next",
        include_ids: bool = False,
        transaction: str | None = None,
        names_only: bool = False,
        exclude_app_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """The root listing (Apps), and a global search's Apps partition.

        Returns the partitioned envelope every v2 read returns:
        ``{"partitions": [{partitionId, partitionKind, appId, rows, hasMore,
        exhausted, total, countsByType, ids}], "scope": ...}``. Each row carries
        the comparator's own ``sortKey``/``nullRank``, which is what lets the
        cursor and the cross-partition merge use one ordering rather than two
        implementations of it.
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_access_context_v2(
        self,
        user_key: str,
        org_id: str,
        *,
        transaction: str | None = None,
    ) -> dict[str, list[str]]:
        """``{"grantee_ids": [...], "gated_app_ids": [...]}`` for one request.

        Every other v2 method takes both, so they are resolved once here instead
        of re-derived inside each query.
        """
        pass

    # ==================== Knowledge Base Operations ====================

    @abstractmethod
    async def get_knowledge_hub_breadcrumbs(
        self,
        node_id: str,
        user_key: str,
        org_id: str,
        transaction: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get breadcrumb trail for a node, filtered to what the caller can see.

        Ancestors without a permission role are omitted and the walk continues past
        them, so a node renders under its nearest visible ancestor -- matching where
        browse shows it. Returns [] when the node itself is not visible.

        Args:
            node_id: Node ID to get breadcrumbs for
            user_key: Graph user key; required, not optional
            org_id: Organization ID for org scoping
            transaction: Optional transaction context

        Returns:
            List of visible breadcrumb items from root to current node
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_context_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: str | None,
        transaction: str | None = None,
        parent_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Get user's context-level permissions (for upload, create folder, etc.).

        Args:
            user_key: User's internal key
            org_id: Organization ID
            parent_id: Parent node ID (None for root)
            transaction: Optional transaction context

        Returns:
            Dict with role and capability flags
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_filter_options(
        self,
        user_key: str,
        org_id: str,
        transaction: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Get available filter options (KBs and Apps) for a user.

        Args:
            user_key: User's internal key
            org_id: Organization ID
            transaction: Optional transaction context

        Returns:
            Dict with 'kbs' and 'apps' lists containing {id, name}
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_node_access(
        self,
        node_id: str,
        user_key: str,
        org_id: str,
        folder_mime_types: list[str],
        transaction: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Resolve a node to its metadata ONLY if it belongs to org_id and user_key
        holds any permission role on it.

        Returns a dict with keys: id, name, nodeType, subType, connector,
        webUrl, recordType, indexingStatus, userRole.
        Returns None for missing nodes AND for permission-denied — callers
        must not distinguish between the two cases.

        Args:
            node_id: Node ID (record _key, recordGroup _key, or app _key)
            user_key: User's internal key
            org_id: Organization ID
            folder_mime_types: MIME types that classify a record as a folder
            transaction: Optional transaction context
        """
        pass

    @abstractmethod
    async def get_linked_records(
        self,
        record_id: str,
        org_id: str,
        user_key: str,
        relation_types: list[str],
        limit: int = 10,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return cross-reference edges (anything except PARENT_CHILD / ATTACHMENT)
        enriched with recordName, recordType, connectorName, webUrl,
        permission-filtered and bounded, in a single query.

        Args:
            record_id: Source record ID (_key)
            org_id: Organization ID (used to scope results)
            user_key: User's internal key (used for permission filtering)
            relation_types: Relation types to include (e.g. LINKED_TO, RELATED, BLOCKS …)
            limit: Maximum number of results to return
            transaction: Optional transaction context

        Returns:
            List of dicts with id, name, recordType, connectorName, webUrl,
            relationshipType, hasChildren.
        """
        pass

    @abstractmethod
    async def get_knowledge_hub_parent_node(
        self,
        node_id: str,
        folder_mime_types: list[str],
        transaction: str | None = None
    ) -> dict[str, Any] | None:
        """
        Get the parent node of a given node.

        Args:
            node_id: Node ID
            folder_mime_types: List of MIME types that indicate folders
            transaction: Optional transaction context

        Returns:
            Dict with parent node info or None if at root
        """
        pass

    @abstractmethod
    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: str | None = None
    ) -> bool:
        """
        Validate that a folder exists in a knowledge base.

        Args:
            kb_id (str): Knowledge base ID
            folder_id (str): Folder ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if folder exists in KB, False otherwise
        """
        pass

    @abstractmethod
    async def _validate_folder_creation(
        self,
        kb_id: str,
        user_id: str
    ) -> dict:
        """
        Validate user permissions for folder creation.

        Args:
            kb_id (str): Knowledge base ID
            user_id (str): User ID (internal key)

        Returns:
            Dict: Validation result with 'valid' key and user info
        """
        pass

    @abstractmethod
    async def get_key_by_external_message_id(
        self,
        external_message_id: str,
        transaction: str | None = None
    ) -> str | None:
        """
        Get internal key by external message ID.

        Args:
            external_message_id (str): External message ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: Internal key if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_message_id_header_by_key(
        self,
        record_key: str,
        collection: str,
        transaction: str | None = None
    ) -> str | None:
        """
        Get messageIdHeader field from a mail record by its key.

        Args:
            record_key (str): Record key (_key or id)
            collection (str): Collection name (e.g., "records" or "mails")
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[str]: messageIdHeader value if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_related_mails_by_message_id_header(
        self,
        message_id_header: str,
        exclude_key: str,
        collection: str,
        transaction: str | None = None
    ) -> list[str]:
        """
        Find all mail records with the same messageIdHeader, excluding a specific key.

        Args:
            message_id_header (str): messageIdHeader value to search for
            exclude_key (str): Record key to exclude from results
            collection (str): Collection name (e.g., "records" or "mails")
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[str]: List of record keys (_key or id) matching the criteria
        """
        pass

    @abstractmethod
    async def check_connector_name_uniqueness(
        self,
        instance_name: str,
        scope: str,
        org_id: str,
        user_id: str,
        collection: str,
        edge_collection: str | None = None,
        transaction: str | None = None
    ) -> bool:
        """
        Check if connector instance name is unique based on scope.

        Args:
            instance_name (str): Name to check
            scope (str): Connector scope (personal/team)
            org_id (str): Organization ID
            user_id (str): User ID (for personal scope)
            collection (str): Collection name for connector instances
            edge_collection (Optional[str]): Edge collection for org-connector relationship (for team scope)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            bool: True if name is unique, False if already exists
        """
        pass

    @abstractmethod
    async def get_connector_instances_with_filters(
        self,
        collection: str,
        scope: str | None = None,
        user_id: str | None = None,
        *,
        is_admin: bool = False,
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
        transaction: str | None = None,
    ) -> tuple[list[dict], int]:
        """
        Get connector instances with filters, pagination, and access control.

        Args:
            collection (str): Collection name
            scope (Optional[str]): Scope filter (personal/team)
            user_id (Optional[str]): User ID for access control
            is_admin (bool): Whether the user is an admin
            search (Optional[str]): Search query
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of connector instances, total count)
        """
        pass

    @abstractmethod
    async def count_connector_instances_by_scope(
        self,
        collection: str,
        scope: str,
        user_id: str | None = None,
        *,
        is_admin: bool = False,
        transaction: str | None = None,
    ) -> int:
        """
        Count connector instances by scope with access control.

        Args:
            collection (str): Collection name
            scope (str): Scope filter (personal/team)
            user_id (Optional[str]): User ID for access control
            is_admin (bool): Whether the user is an admin
            transaction (Optional[str]): Optional transaction ID

        Returns:
            int: Count of connector instances
        """
        pass

    # ==================== Team Operations ====================

    @abstractmethod
    async def get_team_with_users(
        self,
        team_id: str,
        user_key: str,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get a single team with its members and permissions.

        Args:
            team_id (str): Team ID
            user_key (str): Current user's key (for permission checking)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Team data with members and permissions, None if not found
        """
        pass

    @abstractmethod
    async def get_user_teams(
        self,
        user_key: str,
        search: str | None = None,
        page: int = 1,
        limit: int = 100,
        created_by: str | None = None,
        created_after: int | None = None,
        created_before: int | None = None,
        transaction: str | None = None
    ) -> tuple[list[dict], int]:
        """
        Get all teams that a user is a member of.

        Args:
            user_key (str): User's key
            search (Optional[str]): Search query for team name or description
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            created_by (Optional[str]): Filter by creator user key
            created_after (Optional[int]): Filter teams created after this timestamp (ms)
            created_before (Optional[int]): Filter teams created before this timestamp (ms)
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of teams with members and permissions, total count)
        """
        pass

    @abstractmethod
    async def get_team_users(
        self,
        team_id: str,
        org_id: str,
        user_key: str,
        search: str | None = None,
        page: int = 1,
        limit: int = 100,
        transaction: str | None = None
    ) -> dict | None:
        """
        Get all users in a specific team.

        Args:
            team_id (str): Team ID
            org_id (str): Organization ID
            user_key (str): Current user's key (for permission checking)
            search (Optional[str]): Search query for member name or email
            page (int): Page number (1-indexed)
            limit (int): Number of members per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Optional[Dict]: Team data with paginated members, None if not found
        """
        pass

    @abstractmethod
    async def delete_team_member_edges(
        self,
        team_id: str,
        user_ids: list[str],
        transaction: str | None = None
    ) -> list[dict]:
        """
        Delete edges to remove team members.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to remove from team
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of deleted permission edges (OLD values)
        """
        pass

    @abstractmethod
    async def batch_update_team_member_roles(
        self,
        team_id: str,
        user_roles: list[dict[str, str]],
        timestamp: int,
        transaction: str | None = None
    ) -> list[dict]:
        """
        Batch update user roles in a team.

        Args:
            team_id (str): Team ID
            user_roles (List[Dict[str, str]]): List of {userId: str, role: str} dictionaries
            timestamp (int): Timestamp for updatedAtTimestamp field
            transaction (Optional[str]): Optional transaction ID

        Returns:
            List[Dict]: List of updated permission edges
        """
        pass

    @abstractmethod
    async def delete_all_team_permissions(
        self,
        team_id: str,
        transaction: str | None = None
    ) -> None:
        """
        Delete all permissions for a team.

        Args:
            team_id (str): Team ID
            transaction (Optional[str]): Optional transaction ID

        Returns:
            None
        """
        pass

    @abstractmethod
    async def get_team_owner_removal_info(
        self,
        team_id: str,
        user_ids: list[str],
        transaction: str | None = None
    ) -> dict[str, Any]:
        """
        Get information about owners being removed and total owner count for a team.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to check
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict with keys:
                - owners_being_removed (List[str]): User IDs of owners being removed
                - total_owner_count (int): Total number of owners in the team
        """
        pass

    @abstractmethod
    async def get_team_permissions_and_owner_count(
        self,
        team_id: str,
        user_ids: list[str],
        transaction: str | None = None
    ) -> dict[str, Any]:
        """
        Get team info, current permissions for specific users, and total owner count.

        Args:
            team_id (str): Team ID
            user_ids (List[str]): List of user IDs to get permissions for
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Dict with keys:
                - team (Dict): Team document
                - permissions (Dict[str, str]): Map of user_id -> role
                - owner_count (int): Total number of owners in the team
        """
        pass

    # ==================== User Operations ====================

    @abstractmethod
    async def get_organization_users(
        self,
        org_id: str,
        search: str | None = None,
        page: int = 1,
        limit: int = 100,
        transaction: str | None = None
    ) -> tuple[list[dict], int]:
        """
        Get users in an organization with pagination and search.

        Args:
            org_id (str): Organization ID
            search (Optional[str]): Search query for user name or email
            page (int): Page number (1-indexed)
            limit (int): Number of items per page
            transaction (Optional[str]): Optional transaction ID

        Returns:
            Tuple[List[Dict], int]: (List of users, total count)
        """
        pass

    @abstractmethod
    async def get_agent(
        self, agent_id: str, org_id: str | None = None, transaction: str | None = None
    ) -> dict | None:
        """
        Fetch the complete agent document with linked graph data.

        Does NOT perform any permission check — callers must invoke
        ``check_agent_permission`` separately before calling this method.

        Args:
            agent_id:    The agent key / ID.
            org_id:      The organisation key (optional).  When provided,
                         ``shareWithOrg`` is resolved against that specific
                         org's permission edge.  When omitted, ``shareWithOrg``
                         is ``True`` if *any* ORG permission edge exists on the
                         agent, ensuring the flag is never incorrectly ``False``
                         when the caller does not carry an org scope token.
            transaction: Optional transaction ID.

        Returns:
            Dict containing the agent document merged with ``toolsets``,
            ``knowledge``, and ``shareWithOrg``, or ``None`` if the agent does
            not exist or is deleted.
        """
        pass

    @abstractmethod
    async def check_agent_permission(
        self, agent_id: str, user_id: str, org_id: str
    ) -> dict | None:
        """
        Lightweight permission check: returns the caller's access rights on an
        agent without fetching toolsets or knowledge.

        This method skips the expensive toolset/knowledge joins and is suitable
        for endpoints that only need to verify access (e.g. middleware guards,
        pre-flight checks).

        Returns None if the agent does not exist, is deleted, or the user has
        no access (individual or org).

        Args:
            agent_id: The agent key / ID.
            user_id:  The internal user key (_key in ArangoDB, id in Neo4j).
            org_id:   The organisation key.

        Returns:
            Dict with keys ``{user_role, can_edit, can_delete, can_share,
            can_view, access_type}`` on success, or ``None`` if the user has
            no access.
        """
        pass

    @abstractmethod
    async def get_agents_by_web_search_provider(
        self, org_id: str, provider: str
    ) -> list[dict]:
        """
        Find all agents in the organisation that use a specific web search provider.

        Scoped via ORG-type permission edges (i.e. agents shared with the org).

        Args:
            org_id:   The organisation key.
            provider: The web search provider type (e.g. ``"serper"``, ``"tavily"``, ``"exa"``).

        Returns:
            List of dicts with ``{name, _key, creatorName}`` for each matching
            agent.  Returns an empty list when no agents match.
        """
        pass

    @abstractmethod
    async def get_agents_by_model_key(
        self, org_id: str, model_key: str
    ) -> list[dict]:
        """
        Find all agents in the organisation that use a specific AI model.

        Agents store models in a ``models`` array as either ``"{modelKey}"`` or
        ``"{modelKey}_{modelName}"``; both forms are matched.

        Scoped to the organisation via the same BELONGS_TO + permission edge
        skeleton used by ``get_agents_by_web_search_provider``.

        Args:
            org_id:    The organisation key.
            model_key: The model key to match (e.g. a UUID assigned at create time).

        Returns:
            List of dicts with ``{name, _key, creatorName}`` for each matching
            agent.  Returns an empty list when no agents match.
        """
        pass

    @abstractmethod
    async def validate_folder_for_upload(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
        org_id: str,
    ) -> dict:
        """
        Validate that a folder exists and belongs to the KB, and that the user
        has write access, before accepting an upload request.

        Args:
            kb_id:     Knowledge base ID.
            folder_id: Folder ID to validate.
            user_id:   Requesting user's external ID.
            org_id:    Organization ID.

        Returns:
            Dict with ``valid: True`` and context on success, or
            ``valid: False, success: False, code: <4xx|5xx>, reason: <str>``
            on failure.
        """
        pass

    async def backfill_app_org_ids(self) -> dict:
        """Give every App without an ``orgId`` the id of the one organization
        linked to it, so the org filters on Apps keep connectors created before
        Apps carried one. Returns ``{"backfilled": n, "ambiguous": m}``: an App
        linked from several organizations is left alone and counted."""
        raise NotImplementedError

    async def split_link_edges(self, batch_size: int = 1000) -> dict:
        """Move link edges off the hierarchy edge type. Returns
        ``{"migrated": n}``."""
        raise NotImplementedError

    async def backfill_hierarchy(self, batch_size: int = 1000, shapes: Iterable[str] | None = None) -> dict:
        """Write the hierarchy edges an older graph lacks and a sync writes today:
        from the App to a collection root item, from the parent group (or App) to
        a group, from the group to a record with no parent record, a nested
        record's inheritance from its parent record where it inherited from its
        group and nobody holds a grant on that parent or above it, and from the
        group to a nested record that still inherits from no hierarchy parent (one
        under a record of another group, or below a granted record): it keeps
        inheriting from its group, so it hangs off that group as well and keeps
        that group's audience, no more. Only adds edges, never a grant, and never
        an App inheritance edge (whether a group inherits from its App is not
        recorded).
        ``shapes`` runs only those shapes, by name; every shape when None.
        Returns ``{"added": {shape: n}}``; raises if anything is left."""
        raise NotImplementedError

    @staticmethod
    def _backfill_shapes(known: Iterable[str], shapes: Iterable[str] | None) -> list[str]:
        known = list(known)
        if shapes is None:
            return known
        wanted = set(shapes)
        if wanted - set(known):
            raise ValueError(f"Unknown hierarchy backfill shape(s): {sorted(wanted - set(known))}")
        return [name for name in known if name in wanted]

    async def remove_inherited_record_grants(
        self, connector_names: list[str], record_types: list[str], batch_size: int = 1000,
    ) -> dict:
        """Remove the permission edges to a record of these connectors (by the
        record's ``connectorName``) and record types, for connectors whose records
        take their audience from their group and carry no grant of their own today.
        Only where one of the record's grantees (its owner) also holds a grant on a
        live record group the record belongs to and inherits from, so the owner
        keeps it through that group; then all of its grants go, the other
        grantees' (the recipients older Outlook builds granted) included. Any
        other record keeps its grants. Returns ``{"removed": edges, "records":
        n}``; raises if any such edge is left."""
        raise NotImplementedError

    async def merge_duplicate_user_groups(self, connector_names: list[str]) -> dict:
        """Merge the copies of a user group (same ``connectorId`` and
        ``externalGroupId``) of these connectors (the group's ``connectorName``) onto
        the one the lookup by external id returns: the oldest by
        ``createdAtTimestamp``, then id. The copies' membership and grant edges
        (permission, belongsTo) move to it, except one it already has to the same
        node, and the copies are deleted. Returns ``{"groups": duplicated groups,
        "removed": copies, "moved": edges}``; raises if a duplicate is left."""
        raise NotImplementedError

    async def normalize_folder_mime_types(self, batch_size: int = 1000) -> dict:
        """Write ``MimeTypes.FOLDER`` on every folder record (its File type node
        says ``isFile = false``) that carries another mimeType. Returns
        ``{"normalized": n}``, and raises if any is left, so a partial run is
        retried. Records whose File says they are files are never touched."""
        raise NotImplementedError

    async def stamp_kh_listing_state(self, batch_size: int = 5000) -> dict:
        """Write the derived state the knowledge hub listing reads (sort name,
        flag and tree labels) on every record and group written before the write
        paths kept it. Returns ``{"stamped": n}``; raises if any node is left
        disagreeing with its properties and edges. Only Neo4j keeps this state;
        the listing reads it once ``KH_LISTING_STATE_FLAG`` is set."""
        raise NotImplementedError

    # Knowledge hub scopes: the global listing read from precomputed per-connector scopes, behind the
    # ENABLE_KH_SCOPE_LISTING flag. Only Neo4j keeps them; elsewhere these do nothing and the listing always
    # runs its full query.
    # True for a backend whose connector page reads its own connector's grants when
    # handed ``user_key`` and no grants, and reports them through ``grants_out``
    # (a dict it fills as ``{app_id: granted ids}``): the global listing then lets
    # every connector's page read its grants beside the others.
    kh_grants_per_connector = False

    async def get_knowledge_hub_warm_sample(self) -> dict[str, str] | None:
        """One user and one App, record group and record that user can browse
        (``userId``, ``orgId``, ``appId``, ``groupId``, ``recordId``), for warming
        the listing statements' plans; None when the backend has no use for it."""
        return None

    async def kh_scope_enabled(self) -> bool:
        return False

    async def kh_scope_mark_stale(self, connector_id: str) -> int | None:
        """A sync of this connector is about to write: stop listing it from scopes until it is re-stamped.
        Returns the generation the sync owns, for ``kh_scope_sync_ended``."""
        return None

    async def kh_scope_sync_ended(self, connector_id: str, generation: int | None = None) -> None:
        """The writer that owns ``generation`` is done."""

    async def kh_scope_mark_changed(self, connector_id: str) -> None:
        """A write outside a sync changed the tree: stop listing it from scopes until it is re-stamped."""

    async def kh_scope_forget(self, connector_id: str) -> None:
        """The connector is gone: drop its scopes."""

    async def kh_scope_stamp(self, connector_id: str) -> dict:
        return {"connector": connector_id, "stamped": False, "reason": "not supported by this backend"}

    async def kh_scope_reset_syncing(self) -> None:
        """At startup, before any sync: clear marks a crash left behind."""

    async def kh_scope_restamp_stale(self) -> list:
        return []

    async def check_access(
        self,
        user_key: str,
        org_id: str,
        *,
        node_ids: Iterable[str] = (),
        virtual_record_ids: Iterable[str] = (),
        indexed_only: bool = False,
        connector_ids: frozenset[str] | None = None,
        scopes: Iterable[RowScope] = (),
        access: dict[str, Any] | None = None,
        transaction: str | None = None,
    ) -> AccessCheck:
        """The batch access check: which of these nodes and virtual record ids
        the user may access. Every permission decision goes through it.

        A node is accessible exactly when the knowledge-hub global flatten would
        list it, except that ``hideChildren`` does not hide, only
        PARENT_CHILD/ATTACHMENT edges are hierarchy, the node's ``orgId``
        must be ``org_id``, and deleted or placeholder nodes never are. A
        virtual record id is accessible when any record carrying it is; the record
        to cite is the smallest accessible record id, so the answer is
        deterministic.

        Args:
            user_key: The user's graph key (``User.id``), not ``userId``. Empty,
                with no ``access``, means no identity: nothing is accessible.
            org_id: The request's organization.
            node_ids: Record, RecordGroup or App ids; duplicates and unknown ids
                are fine.
            virtual_record_ids: Virtual record ids, e.g. search hits.
            indexed_only: Cite only a record that has finished indexing (search).
            connector_ids: Cite only a record of these connectors (a search's
                scope: vector membership is unioned per virtual record id, so a
                hit can come from an out-of-scope copy). An empty set cites
                nothing; None does not narrow. Neither filter narrows node ids.
            scopes: Cite only a record every one of these admits (a selection
                below app level, and an agent's or project's allow-list). Like
                ``connector_ids`` they do not narrow ``node_ids``; the asked
                nodes they admit are reported apart, in ``node_ids_in_scope``.
            access: ``get_knowledge_hub_access_v3`` for this user, when the caller
                already holds it; computed otherwise.
            transaction: Optional transaction ID.

        Raises:
            PermissionVerificationUnavailableError: the graph could not answer.
                Never an empty result instead, which is what total denial looks
                like; each caller decides how to fail.
            NotImplementedError: the backend has no batch check (it implements
                no ``_kh_v3_accessible_rows``). Not defaulted to an answer: a
                backend without it must fail loudly rather than deny or admit
                everything.
        """
        asked_nodes = set(node_ids)
        # An empty scope cites nothing, so its virtual record ids need no query.
        asked_vrids = set(virtual_record_ids) if connector_ids != frozenset() else set()
        if not (asked_nodes or asked_vrids) or (not user_key and access is None):
            return AccessCheck()
        try:
            rows = await self._kh_v3_accessible_rows(
                user_key, org_id, list(asked_nodes), list(asked_vrids),
                access=access, transaction=transaction,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            self.logger.error(
                "check_access failed for %d nodes and %d vrids: %s",
                len(asked_nodes), len(asked_vrids), exc,
            )
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        records_by_vrid: dict[str, str] = {}
        for row in rows:
            vrid, record_id = row.get("vrid"), row.get("id")
            if vrid not in asked_vrids or not record_id:
                continue
            if indexed_only and row.get("indexingStatus") != ProgressStatus.COMPLETED.value:
                continue
            if connector_ids is not None and row.get("connectorId") not in connector_ids:
                continue
            if not all(scope.admits(row) for scope in scopes):
                continue
            if vrid not in records_by_vrid or record_id < records_by_vrid[vrid]:
                records_by_vrid[vrid] = record_id
        accessible_nodes = [row for row in rows if row.get("id") in asked_nodes]
        return AccessCheck(
            node_ids=frozenset(row["id"] for row in accessible_nodes),
            records_by_vrid=records_by_vrid,
            node_ids_in_scope=frozenset(
                row["id"] for row in accessible_nodes
                if all(scope.admits(row) for scope in scopes)
            ),
        )

    async def _kh_v3_accessible_rows(
        self,
        user_key: str,
        org_id: str,
        node_ids: list[str],
        virtual_record_ids: list[str],
        *,
        access: dict[str, Any] | None,
        transaction: str | None,
    ) -> list[dict[str, Any]]:
        """The rows ``check_access`` decides from: one per accessible node among
        the asked ids and the records carrying the asked virtual record ids, as
        ``{id, vrid, connectorId, indexingStatus, isInternal, groupIds}``
        (``groupIds``: the record groups a record belongs to)."""
        raise NotImplementedError

    async def get_selection_nodes(
        self,
        org_id: str,
        *,
        group_ids: list[str],
        record_ids: list[str],
        exact_record_ids: list[str],
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """What a selection below app level covers, read from the hierarchy alone.

        ``groups``: the record groups in ``group_ids`` and every group nested
        under them, as ``{id, connectorId}``. ``records``: the records in
        ``record_ids`` with everything under them (PARENT_CHILD/ATTACHMENT,
        up to ``ACCESS_WALK_MAX_DEPTH`` hops) and the records in
        ``exact_record_ids`` alone, as ``{id, vrid, connectorId}``. A folder
        is a record without content: it is returned with ``vrid`` None.

        No permissions are applied: ``check_access`` decides every hit. Only
        live nodes of ``org_id`` are returned, so an id of another org, of a
        deleted node or of the wrong kind contributes nothing. Each list holds
        at most ``limit + 1`` entries, so the caller can tell a selection that
        is over the limit from one that is at it.
        """
        raise NotImplementedError

    def _kh_v3_empty_page(
        self,
        start_id: str | None,
        *,
        include_scope: bool,
        include_total: bool,
        admitted: bool,
    ) -> dict[str, Any]:
        scope = None
        if include_scope:
            scope = (
                {"admitted": False, "nodeId": start_id}
                if not admitted
                else browse_scope(start_id or "", True, [], [])
            )
        return {
            "rows": [],
            "hasMore": False,
            "total": 0 if include_total else None,
            "counts": {} if include_total else None,
            "scope": scope,
        }

    async def _kh_v3_page_result(
        self,
        payload: dict[str, Any],
        *,
        limit: int,
        direction: str,
        include_total: bool,
        include_scope: bool,
        scoped_start: bool,
        start_id: str,
        app_id: str,
        via_parent_id: str | None,
        transaction: str | None,
        org_id: str = "",
        access: dict[str, Any] | None = None,
        listed_under: str | None = None,
        name_parents: bool = True,
    ) -> dict[str, Any]:
        """A page from a backend's ``payload``: ``rows`` (up to ``limit + 1``,
        nearest the boundary first when paging back), ``total`` and the per-type
        counts, and the breadcrumb graph of a scoped start. ``listed_under`` is
        the node whose direct children these are (None for a flatten). Without
        ``name_parents`` the rows keep their ``parentOptions`` for the caller to
        name (``name_knowledge_hub_parents``)."""
        page = list(payload.get("rows") or [])
        has_more = len(page) > limit
        page = page[:limit]
        if direction == "prev":
            page.reverse()
        if name_parents:
            await self._kh_v3_name_parents(
                page, org_id=org_id, apps={app_id}, access=access, listed_under=listed_under,
                transaction=transaction,
            )
        total = None
        counts = None
        if include_total:
            total = payload.get("total") or 0
            counts = {
                "record": payload.get("nRecord") or 0,
                "folder": payload.get("nFolder") or 0,
                "recordGroup": payload.get("nGroup") or 0,
            }
            counts = {key: value for key, value in counts.items() if value}
        scope_out = None
        if include_scope:
            if scoped_start:
                scope_out = browse_scope(
                    start_id, True,
                    payload.get("crumbNodes") or [],
                    payload.get("crumbEdges") or [],
                    via_parent_id=via_parent_id,
                )
            else:
                scope_out = await (
                    self._kh_v3_shared(("app-scope", app_id, via_parent_id),
                                       lambda: self._kh_v3_app_scope(app_id, via_parent_id, None))
                    if transaction is None else self._kh_v3_app_scope(app_id, via_parent_id, transaction)
                )
        return {
            "rows": page,
            "hasMore": has_more,
            "total": total,
            "counts": counts,
            "scope": scope_out,
        }

    async def name_knowledge_hub_parents(
        self,
        rows: list[dict[str, Any]],
        org_id: str,
        access: dict[str, Any],
        *,
        transaction: str | None = None,
    ) -> None:
        """Name the parent of rows merged from several connector pages asked for
        with ``name_parents=False``: one batch check for the whole page."""
        await self._kh_v3_name_parents(
            rows, org_id=org_id, apps=set(access.get("gated_app_ids") or ()), access=access,
            listed_under=None, transaction=transaction,
        )

    async def _kh_v3_name_parents(
        self,
        rows: list[dict[str, Any]],
        *,
        org_id: str,
        apps: set[str],
        access: dict[str, Any] | None,
        listed_under: str | None,
        transaction: str | None,
    ) -> None:
        """The parent each row names, from its hierarchy parents, its own record
        groups and the App (``parentOptions``, dropped here): the node it is
        listed under when browsing; otherwise the first one the user can access,
        a real record or group before an internal one such as Shared with Me,
        then its own group (a chain-top), and the App only when no other is
        reachable. Never a parent the user cannot access: its name would
        disclose it.
        ``apps`` are the Apps the user is gated into, named without asking."""
        def ranked(row: dict[str, Any]) -> list[dict[str, Any]]:
            # A group that is both a hierarchy parent and an own group ranks as the parent.
            by_id: dict[str, dict[str, Any]] = {}
            for o in row.get("parentOptions") or []:
                if o and o.get("id") and (o["id"] not in by_id or by_id[o["id"]].get("ownGroup")):
                    by_id[o["id"]] = o
            options = sorted(by_id.values(), key=lambda o: o["id"])
            under = [o for o in options if o["id"] == listed_under]
            nested = [o for o in options if o.get("nodeType") != "app" and not o.get("ownGroup")]
            real = [o for o in nested if not o.get("isInternal")]
            internal = [o for o in nested if o.get("isInternal")]
            own = [o for o in options if o.get("ownGroup") and o.get("nodeType") != "app"]
            app_options = [o for o in options if o.get("nodeType") == "app"]
            return under + real + internal + own + app_options

        def known(option: dict[str, Any]) -> bool:
            return option["id"] == listed_under or (option.get("nodeType") == "app" and option["id"] in apps)

        rows = [row for row in rows if "parentOptions" in row]
        unknown: set[str] = set()
        for row in rows:
            for option in ranked(row):
                if known(option):
                    break
                unknown.add(option["id"])
        admitted = (await self.check_access(
            "", org_id, node_ids=unknown, access=access, transaction=transaction,
        )).node_ids if unknown and access is not None else frozenset()
        for row in rows:
            options = ranked(row)
            row.pop("parentOptions")
            chosen = next((o for o in options if known(o) or o["id"] in admitted), None)
            if chosen is not None:
                row.update(
                    parentId=chosen["id"], parentName=chosen.get("name"),
                    parentType=chosen.get("nodeType"), parentIsInternal=bool(chosen.get("isInternal")),
                )

    async def _kh_v3_app_scope(
        self, app_id: str, via_parent_id: str | None, transaction: str | None,
    ) -> dict[str, Any]:
        # A graph error fails the page rather than naming the App "".
        app = await self.get_document(
            app_id, CollectionNames.APPS.value, transaction, raise_on_error=True,
        ) or {}
        return browse_scope(
            app_id, True,
            [{
                "id": app_id,
                "name": app.get("name") or "",
                "nodeType": "app",
                "subType": app.get("type"),
                "admitted": True,
                "isInternal": False,
                "ownGroups": [],
            }],
            [],
            via_parent_id=via_parent_id,
        )

    async def get_knowledge_hub_connector_page_v3(
        self,
        app_id: str,
        org_id: str,
        grantee_ids: list[str],
        gated_app_ids: list[str],
        granted_ids: list[str] | None = None,
        limit: int = 50,
        *,
        flatten: bool = True,
        sort_field: str = "name",
        sort_dir: str = "ASC",
        after: dict[str, Any] | None = None,
        direction: str = "next",
        filters: dict[str, Any] | None = None,
        include_total: bool = True,
        start_id: str | None = None,
        start_type: str = "app",
        grants_by_connector: dict[str, list[str]] | None = None,
        include_scope: bool = False,
        via_parent_id: str | None = None,
        transaction: str | None = None,
        name_parents: bool = True,
        user_key: str | None = None,
    ) -> dict[str, Any]:
        """One page of what the user may see under one start node: the App's
        direct children, a folder or group's (``include_scope`` adds the browse
        scope and breadcrumbs), or the whole connector (``flatten`` from the App).
        Returns ``{rows, hasMore, total, counts, scope}``; rows carry their own
        ``sortKey``/``nullRank`` for the partition merge. With neither
        ``granted_ids`` nor ``grants_by_connector``, ``user_key`` has the page read
        the grants of its own connector alone (``get_knowledge_hub_connector_grants``)."""
        raise NotImplementedError

    async def _kh_v3_shared(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> Any:
        """``compute()``, shared by the callers that ask for ``key`` while it runs:
        one click sends the main pane and the sidebar together, and both need the
        same grants and chain-tops. Nothing is kept once it completes, so no answer
        is older than the request that waited for it. Callers must not mutate it."""
        running: dict[tuple, asyncio.Future] = self.__dict__.setdefault("_kh_v3_running", {})
        task = running.get(key)
        if task is None:
            task = asyncio.ensure_future(compute())
            running[key] = task
            task.add_done_callback(lambda _done, k=key: running.pop(k, None))
        # Shielded: one caller going away must not cancel it for the others.
        return await asyncio.shield(task)

    async def _kh_v3_scoped_start_access(
        self,
        start_id: str,
        app_id: str,
        org_id: str,
        grantee_ids: list[str],
        gated_app_ids: list[str],
        grants_by_connector: dict[str, list[str]],
        transaction: str | None,
        probe: dict[str, Any] | None = None,
    ) -> tuple[bool, list[str], list[str]]:
        """What the batch check says about a browse start: whether it admits the
        start, which of its ancestors it admits (the breadcrumb of a start the
        walk from the App does not reach), and the declared scope the start lies
        in: the groups under a RECORD_GROUP_LEVEL group it admits. Records
        belonging to those groups are the rest of that scope, as in the check
        and the flatten.
        """
        ancestors, declared = await self._kh_v3_start_lineage(start_id, app_id, transaction)
        access = {
            "grantee_ids": grantee_ids,
            "gated_app_ids": gated_app_ids,
            "by_connector": grants_by_connector,
            # A backend that tests grants from the node instead of a list (Neo4j).
            "probe": probe,
        }
        admitted = (await self.check_access(
            "", org_id, node_ids=[start_id, *ancestors], access=access, transaction=transaction,
        )).node_ids
        if start_id not in admitted:
            return False, [], []
        declared = [d for d in declared if d in admitted]
        scope = await self._kh_v3_declared_groups(declared, transaction) if declared else []
        return True, [a for a in ancestors if a in admitted], scope

    async def _kh_v3_chain_tops(
        self,
        app_id: str,
        org_id: str,
        access: dict[str, Any],
        transaction: str | None,
        *,
        only_group: str | None = None,
        by_groups: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """The user's chain-tops in ``app_id`` by the node browse lists them under
        (``kh_chain_tops.place_chain_tops``). Openable means admitted by the batch
        check, against all the user's grantees. ``only_group``
        limits the candidates to records of that group, for browsing it.
        ``by_groups`` is ``_kh_v3_chain_top_groups`` for these grants, when the
        caller already holds it."""
        granted = list((access.get("by_connector") or {}).get(app_id) or [])
        if not granted and not by_groups:
            return {}

        async def admitted(ids: set[str]) -> frozenset[str]:
            return (await self.check_access(
                "", org_id, node_ids=ids, access=access, transaction=transaction,
            )).node_ids if ids else frozenset()

        # Own groups first, in one pass over the grants: they are few, and a node
        # whose own group opens is never listed under the App, so browsing the App
        # never fetches it. A user can hold thousands of grants; most belong to a
        # group they can open.
        if by_groups is None:
            by_groups = await self._kh_v3_chain_top_groups(app_id, org_id, granted, only_group, transaction)
        open_groups = await admitted({g for s in by_groups for g in s["ownGroups"]})
        ids = [
            node_id for s in by_groups
            if only_group is not None or not set(s["ownGroups"]) & open_groups
            for node_id in s["ids"]
        ]
        if not ids:
            return {}
        candidates = [
            c for c in await self._kh_v3_chain_top_candidates(app_id, ids, transaction)
            # A direct child of the App is listed by the walk from it.
            if app_id not in (c.get("parents") or ())
        ]
        if not candidates:
            return {}
        open_parents = await admitted({p for c in candidates for p in c["parents"]})
        tops = [c for c in candidates if not set(c["parents"]) & open_parents]
        if not tops:
            return {}
        # The costly walks run for the chain-tops only.
        facts = await self._kh_v3_chain_top_facts(
            app_id, [c["id"] for c in tops], sorted({g["id"] for c in tops for g in c["ownGroups"]}),
            transaction,
        )
        for c in tops:
            for g in c["ownGroups"]:
                g["underApp"] = g["id"] in facts["underApp"]
        tops = [c for c in tops if c["id"] not in facts["hidden"]]
        return place_chain_tops(tops, open_groups | open_parents, app_id)

    async def _kh_v3_chain_top_groups(
        self,
        app_id: str,
        org_id: str,
        granted_ids: list[str],
        only_group: str | None,
        transaction: str | None,
    ) -> list[dict[str, Any]]:
        """The granted nodes of ``app_id`` that could be chain-tops, by their own
        groups, as ``{ownGroups: [ids], ids: [node ids]}``: OPEN, of the request's
        org, not deleted or a placeholder; none in an App that opens everything.
        Own groups are BELONGS_TO groups, or a group's parent groups.
        ``only_group`` keeps the records of that group."""
        raise NotImplementedError

    async def _kh_v3_chain_top_candidates(
        self, app_id: str, node_ids: list[str], transaction: str | None,
    ) -> list[dict[str, Any]]:
        """These nodes as ``{id, handle, parents, ownGroups: [{id, deleted}]}``:
        ``parents`` are their hierarchy parents (the App included)."""
        raise NotImplementedError

    async def _kh_v3_chain_top_facts(
        self,
        app_id: str,
        node_ids: list[str],
        group_ids: list[str],
        transaction: str | None,
    ) -> dict[str, set[str]]:
        """``hidden``: the nodes beneath a group that hides its children;
        ``underApp``: the groups whose hierarchy reaches ``app_id``."""
        raise NotImplementedError

    async def _kh_v3_start_lineage(
        self, start_id: str, app_id: str, transaction: str | None,
    ) -> tuple[list[str], list[str]]:
        """A browse start's hierarchy ancestors, and the RECORD_GROUP_LEVEL groups
        of ``app_id`` among the start and them."""
        raise NotImplementedError

    async def _kh_v3_declared_groups(
        self, declared: list[str], transaction: str | None,
    ) -> list[str]:
        """The groups under these declared groups, group to group, deleted groups
        cut: the check's declared-scope arm."""
        raise NotImplementedError

    async def get_knowledge_hub_access_v3(
        self,
        user_key: str,
        org_id: str,
        *,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """Who the user is for the batch check and the v3 listing, resolved once per
        request: ``grantee_ids`` (the user, the groups, roles and teams it holds a
        USER permission on, its organization), ``gated_app_ids`` (the one connector
        gate) and ``by_connector`` (records and groups granted directly,
        bucketed by connector)."""
        raise NotImplementedError

    async def count_active_apps_by_type(self) -> dict[str, int]:
        """Active Apps per type (``unknown`` without one), for the connector_active
        gauge. A backend should count in the database; this default reads the
        type of every active App."""
        counts: dict[str, int] = {}
        for doc in await self.get_nodes_by_filters(
            CollectionNames.APPS.value, {"isActive": True}, return_fields=["type"],
        ) or []:
            app_type = doc.get("type") or "unknown"
            counts[app_type] = counts.get(app_type, 0) + 1
        return counts

    async def get_knowledge_hub_connector_grants(
        self,
        user_key: str,
        org_id: str,
        connector_id: str,
        *,
        transaction: str | None = None,
    ) -> list[str]:
        """One connector's bucket of ``get_knowledge_hub_access_v3``: the records
        and groups of ``connector_id`` granted directly, which is all that browsing
        inside it reads. A backend may answer from that connector's grantees alone;
        this default reads every grant."""
        access = await self.get_knowledge_hub_access_v3(user_key, org_id, transaction=transaction)
        return list(access["by_connector"].get(connector_id) or [])

    async def get_gated_apps(
        self, user_key: str, org_id: str, transaction: str | None = None,
    ) -> list[dict]:
        """The App documents of ``org_id`` this user passes the connector gate for."""
        raise NotImplementedError

    async def filter_accessible_record_ids(
        self,
        record_ids: list[str],
        user_id: str,
        org_id: str,
        *,
        scopes: Iterable[RowScope] = (),
        transaction: str | None = None,
    ) -> set[str]:
        """The subset of ``record_ids`` the user may read, by the batch access
        check (``check_access``), for records reached by graph traversal rather
        than by search (a hit's parent, attachment or child). Not gated on
        ``indexingStatus``: a record that synced but did not index still has
        its permissions and its metadata. Placeholder and internal records are
        excluded — they are stubs, not content.

        Args:
            record_ids: Record ids to adjudicate.
            user_id: The ``userId`` field value, not the graph key.
            org_id: Tenant boundary.
            scopes: Keep only a record every one of these admits (what the
                turn is limited to).

        Returns:
            The readable ids. Empty means every id was denied.

        Raises:
            PermissionVerificationUnavailableError: the graph could not answer,
                including the caller's own lookup.
        """
        asked = {r for r in record_ids if r}
        if not asked:
            return set()
        try:
            user = await self.get_user_by_user_id(user_id, raise_on_error=True)
        except Exception as exc:
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        user_key = (user or {}).get("id") or (user or {}).get("_key")
        if not user_key:
            return set()
        try:
            rows = await self._kh_v3_accessible_rows(
                user_key, org_id, list(asked), [], access=None, transaction=transaction,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        scopes = list(scopes)
        return {
            r["id"] for r in rows
            if r.get("id") in asked and not r.get("isInternal") and all(scope.admits(r) for scope in scopes)
        }

    async def filter_accessible_virtual_record_ids(
        self,
        virtual_record_ids: list[str],
        user_id: str,
        org_id: str,
        *,
        trusted_app_ids: frozenset[str] | None = None,
        trusted_group_ids: frozenset[str] | None = None,
        scope_connector_ids: frozenset[str] | None = None,
        scopes: Iterable[RowScope] = (),
        transaction: str | None = None,
    ) -> dict[str, str]:
        """``{virtualRecordId: recordId}`` for the retrieved virtual record ids the
        user may read, by the batch access check (``check_access``). The record
        cited is live, indexed and, when ``scope_connector_ids`` is given, of one
        of those connectors (an empty set cites nothing); with ``scopes``, one
        that every scope admits.

        Nothing is trusted: ``trusted_app_ids`` and ``trusted_group_ids`` are
        accepted and ignored, and every id is checked.

        Args:
            virtual_record_ids: Virtual record ids returned by a search.
            user_id: The ``userId`` field value, not the graph key.
            org_id: Tenant boundary.

        Returns:
            The readable subset. Empty means every id was denied.

        Raises:
            PermissionVerificationUnavailableError: the graph could not answer,
                including the caller's own lookup.
        """
        asked = [v for v in dict.fromkeys(virtual_record_ids) if v]
        if not asked:
            return {}
        try:
            user = await self.get_user_by_user_id(user_id, raise_on_error=True)
        except Exception as exc:
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        user_key = (user or {}).get("id") or (user or {}).get("_key")
        if not user_key:
            return {}
        check = await self.check_access(
            user_key, org_id, virtual_record_ids=asked, indexed_only=True,
            connector_ids=scope_connector_ids, scopes=scopes, transaction=transaction,
        )
        return dict(check.records_by_vrid)

    async def filter_nodes_with_permission_role(
        self,
        nodes: list[dict[str, str]],
        user_key: str,
        org_id: str,
        *,
        transaction: str | None = None,
        raise_on_error: bool = False,
    ) -> set[str]:
        """The ids among ``nodes`` (each ``{"id": str, "type": "record"|"recordGroup"}``)
        the user may access, by the batch access check (``check_access``).

        A failed check returns ``set()`` unless ``raise_on_error``, which
        re-raises it so the caller can tell a failure apart from "no access".
        """
        ids = [node.get("id") for node in nodes or [] if node.get("id")]
        if not ids or not user_key:
            return set()
        try:
            check = await self.check_access(user_key, org_id, node_ids=ids, transaction=transaction)
        except NotImplementedError:
            raise
        except Exception as exc:
            if raise_on_error:
                raise
            self.logger.warning("filter_nodes_with_permission_role: access check failed: %s", exc)
            return set()
        return set(check.node_ids)

    @abstractmethod
    async def get_record_parent_adjacency(
        self,
        record_ids: list[str],
        org_id: str,
        *,
        max_depth: int = 20,
        transaction: str | None = None,
    ) -> dict[str, Any]:
        """Batched upward parent-adjacency for retrieved records (one round trip).

        Structure + names only — does **not** apply breadcrumb priority or ACL.
        Every node is org-scoped (``orgId == org_id``; apps may omit orgId).

        Name coalesce (navigate-aligned)::

            app:          name → appName → _key
            record:       recordName → name → title → _key
            recordGroup:  groupName → name → _key

        Return shape::

            {
              "nodes": {
                "<id>": {"id": str, "type": "app"|"recordGroup"|"record", "name": str},
              },
              "parents": {
                "<child_id>": [
                  {"parent_id": str, "parent_type": str, "via": "nodeRelations"|"belongsTo"},
                  ...
                ],
              },
            }
        """
        pass

    @abstractmethod
    async def get_taxonomy_entities_for_record(
        self,
        record_key: str,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Taxonomy entities (category/subcategory/department/topic/language)
        directly linked to a single record via its ``belongsTo*`` edges.

        Not paginated and not org-scoped by traversal — the record itself
        pins the scope. Used by
        the MD5-dedup path (``SinkOrchestrator.sync_entities_for_duplicate``)
        to re-project a deduplicated record's already-copied taxonomy edges
        into the entities vector collection, so a shared category/topic/etc.
        picks up the duplicate's ``connectorId``/``recordGroupId``.

        Args:
            record_key: The record's ``_key`` (Arango) / ``id`` (Neo4j).
            transaction: Optional transaction ID.

        Returns:
            List of dicts shaped like ``EntityRecord`` source fields:
            ``{entityId, entityType, name, aliases, level, orgId}``, where
            ``aliases`` is a list of strings, ``level`` is the subcategory
            level (``"1"``/``"2"``/``"3"``) or None for every other entity
            type, and ``orgId`` is the node's org or None (a legacy node or a
            global department). ``sync_entities_for_duplicate`` reads all six.
        """
        pass

    @abstractmethod
    async def get_record_taxonomy_links(
        self,
        record_keys: list[str],
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every ``belongsTo*`` edge from the given records to a category,
        subcategory, topic or language node, with the spelling the record's
        own extraction gave that node.

        Rows are ``{recordId, collection, entityId, name, canonical,
        extractedName, migrated}``: ``name`` is the node's stored name,
        ``canonical`` whether the node is a per-org canonical node (it has a
        ``normalizedName``), ``extractedName`` the edge's own spelling or
        None, and ``migrated`` whether the edge was moved off a legacy node.
        ``app.services.graph_db.taxonomy.TaxonomyLink`` reads them.
        Departments are not included.

        Raises:
            Exception: on query failure; a partial answer would read as the
            records having fewer labels.
        """
        pass

    @abstractmethod
    async def get_entity_candidate_records(
        self,
        refs: list[dict[str, Any]],
        org_id: str,
        *,
        record_types: list[str] | None = None,
        limit_per_entity: int = 20,
        offset: int = 0,
        transaction: str | None = None,
    ) -> "dict[tuple[str, str], EntityCandidateRows]":
        """Records linked to each knowledge-graph entity in ``refs``, scoped
        to the org and to each ref's connectors. **No permission check** —
        callers (``app.modules.retrieval.entity_permissions``) check every
        row before exposing it.

        Each ref is ``{"id": str, "type": str, "connectorIds": list[str]}``:
          - taxonomy types (``department``/``category``/``subcategory``/
            ``topic``/``language``): records with an outbound ``belongsTo*``
            edge to the entity node; ``subcategory`` matches levels 1-3.
          - ``record_group``: records with a ``belongsTo`` edge to the group
            (direct members only), and the group itself must be in ``org_id``.
          - ``record``: the record itself.
          - any other type: no rows.

        Every row satisfies ``orgId == org_id``, not deleted,
        ``connectorId IN ref["connectorIds"]`` and, when ``record_types`` is
        given, ``recordType IN record_types``. At most
        ``ENTITY_CANDIDATE_SCAN_CAP`` records are scanned per entity; within
        that scan rows are deduplicated, sorted by
        ``sourceLastModifiedTimestamp`` (falling back to
        ``updatedAtTimestamp``) descending then key ascending, and paged per
        entity with ``offset``/``limit_per_entity``. Runs at most one query
        per entity type present in ``refs``; the type→collection/label/edge
        mapping is fixed, never taken from caller input.

        Args:
            refs: Entities to list records for.
            org_id: Organization scope. Empty returns ``{}`` without querying.
            record_types: Optional record-type filter.
            limit_per_entity: Max rows per entity.
            offset: Rows to skip per entity.
            transaction: Optional transaction id.

        Returns:
            ``{(entity_type, entity_id): EntityCandidateRows}`` for every ref
            queried. ``EntityCandidateRows`` is a list of rows whose
            ``capped`` is true when the scan stopped at the cap, so the rows
            are not newest across all of the entity's records and paging past
            them does not mean there are no more.
            The key carries the type because ids are only unique within a
            collection, so an id-keyed result would let one type's rows
            overwrite another's. Each row is
            ``{"_key", "recordName", "recordType", "connectorId",
            "virtualRecordId", "webUrl", "sourceLastModifiedTimestamp",
            "updatedAtTimestamp"}``. A ref with no rows maps to ``[]``.

        Raises:
            Exception: on any query failure.
        """
        pass

    @abstractmethod
    async def get_permitted_entity_records(
        self,
        refs: list[dict[str, Any]],
        org_id: str,
        user_key: str,
        *,
        app_level_connector_ids: list[str],
        record_types: list[str] | None = None,
        limit_per_entity: int = 20,
        offset: int = 0,
        window: int = 200,
        timeout_seconds: float | None = None,
    ) -> "dict[tuple[str, str], PermittedEntityRows]":
        """One window of each entity's candidate records, filtered inside the
        query by connector or permission role. Not the access decision: the
        role test reads neither ``accessRule`` nor the connector gate.

        Candidates are exactly those of :meth:`get_entity_candidate_records`
        (same refs, scoping, scan cap and newest-first order). Of these, the
        window ``[offset, offset + window)`` is walked in order and a row is
        returned when:
          - its ``connectorId`` is in ``app_level_connector_ids``, or
          - the user ``user_key`` holds a permission role on it.
        Domain, "anyone" and link shares grant no access, as in every other
        access check.
        ``app.modules.retrieval.entity_permissions`` passes every candidate's
        connector, so the whole window comes back, and decides each row with
        :meth:`check_access`; the role test remains for callers that pass
        fewer.
        The walk stops after ``limit_per_entity`` permitted rows, so the
        permission work per entity is bounded by the window and usually ends
        sooner.

        Args:
            refs: Entities, shaped as for ``get_entity_candidate_records``.
            org_id: Organization scope. Empty returns ``{}`` without querying.
            user_key: The user's graph key. Empty returns ``{}``.
            app_level_connector_ids: Connectors whose records need no
                per-record check.
            record_types: Optional record-type filter.
            limit_per_entity: Max permitted rows per entity.
            offset: Candidates to skip per entity.
            window: Candidates to walk per entity.
            timeout_seconds: Optional server-side query limit.

        Returns:
            ``{(entity_type, entity_id): PermittedEntityRows}`` for every ref
            queried, rows in candidate order and shaped as the candidate rows.
            ``window_size`` and ``examined`` give the next offset
            (``offset + examined``); ``capped`` is as for the candidates.

        Raises:
            Exception: on any query failure, including the timeout.
        """
        pass

    @abstractmethod
    async def get_taxonomy_entity_membership(
        self,
        refs: list[dict[str, Any]],
        org_id: str,
        transaction: str | None = None,
    ) -> dict[tuple[str, str], dict[str, list[str]]]:
        """Which connectors and record groups still reach each taxonomy entity,
        from the graph: the distinct ``connectorId`` / ``recordGroupId`` of
        non-deleted records in ``org_id`` with a ``belongsTo*`` edge to it.

        The entity vector store's stored membership is only a projection of
        this; connector cleanup uses it before deleting a point that looks
        exclusive to the deleted connector.

        Args:
            refs: ``{"id": str, "type": str}`` for ``department``, ``category``,
                ``subcategory`` (levels 1-3), ``topic`` or ``language``; other
                types are ignored.
            org_id: Organization scope. Empty returns ``{}`` without querying.
            transaction: Optional transaction id.

        Returns:
            ``{(entity_type, entity_id): {"connectorIds": [...],
            "recordGroupIds": [...]}}`` for every supported ref; an entity no
            record reaches maps to empty lists.

        Raises:
            Exception: on any query failure.
        """
        pass

    @abstractmethod
    async def find_taxonomy_nodes(
        self,
        collection: str,
        org_id: str,
        normalized_names: list[str],
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Canonical taxonomy nodes of ``org_id`` whose ``normalizedName`` or one
        of whose ``normalizedAliases`` is in ``normalized_names`` (Tier 0 of
        ``app.modules.entity_resolution``).

        Only nodes written with ``orgId`` and ``normalizedName`` match; legacy
        global nodes (created by name alone) are never returned, by design,
        and neither is a node merged into another (``mergedInto`` set).
        ``collection`` must be one of ``TAXONOMY_COLLECTIONS``
        (``app.services.graph_db.taxonomy``); anything else returns ``[]``.

        Returns:
            ``[{"id", "name", "normalizedName", "aliases",
            "normalizedAliases"}]``; the two alias fields are always lists and
            aligned by position.

        Raises:
            Exception: on query failure. The resolver decides whether that
                fails the record (apply mode) or is logged (shadow mode).
        """
        pass

    @abstractmethod
    async def move_taxonomy_edges(
        self,
        collection: str,
        from_key: str,
        to_key: str,
        org_id: str,
        *,
        set_merged_from: str | None,
        only_merged_from: str | None = None,
        provenance: str = "mergedFrom",
        dry_run: bool = False,
        transaction: str | None = None,
    ) -> int:
        """Move the record edges of ``org_id``'s records from taxonomy node
        ``from_key`` to ``to_key`` in ``collection``, keeping each edge's
        properties. A forward move keeps an edge's existing ``mergedFrom`` and
        otherwise sets ``set_merged_from``, so chained merges keep each edge's
        origin. With ``only_merged_from`` (undoing a merge), only edges whose
        ``mergedFrom`` equals it move, and ``mergedFrom`` is set to
        ``set_merged_from`` (normally None). An edge whose record already
        links to ``to_key`` is removed instead of duplicated. Batched and
        idempotent.

        ``provenance`` names the edge field the move records and filters on:
        ``mergedFrom`` for merges, ``migratedFrom`` for legacy migrations, so
        undoing one never hides the other's edges. Restoring with
        ``migratedFrom`` also clears ``mergedFrom``: an edge back on its legacy
        node has no merge history left.

        ``to_key`` must be a node of ``org_id``; only a ``migratedFrom``
        restore may land on a legacy node (no ``orgId``). Trashed records'
        edges move too, so a restored record finds its taxonomy. Matching
        edges are found once and moved by id in batches.

        Returns:
            How many edges matched (with ``dry_run``, how many would move).

        Raises:
            ValueError: for a non-taxonomy collection, a missing key or org,
                ``from_key == to_key``, an unknown provenance field, or (when
                not a dry run) a ``to_key`` node that does not exist or is
                not one ``org_id``'s edges may land on.
            Exception: on query failure.
        """
        pass

    @abstractmethod
    async def find_legacy_taxonomy_nodes(
        self,
        collection: str,
        org_id: str,
        limit: int,
        after_key: str | None = None,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        """Legacy nodes of ``collection`` (no ``orgId``) that records of
        ``org_id`` link to, with how many distinct such records each has,
        ordered by key after ``after_key``. Walks the org's records, trashed
        ones included, and their edges; meant for an offline migration, not
        a request path.

        Returns:
            ``[{"_key", "name", "records"}]``, at most ``limit``.
        """
        pass

    @abstractmethod
    async def create_taxonomy_node_if_absent(
        self,
        collection: str,
        node: dict[str, Any],
        transaction: str | None = None,
    ) -> None:
        """Insert a canonical taxonomy node, keeping the existing one if the
        key is already taken.

        ``node`` carries ``id`` (the deterministic key from
        ``taxonomy_node_key``), ``name``, ``normalizedName``, ``orgId`` and
        ``createdAtTimestamp``. The insert is idempotent and never updates an
        existing document, so the first spelling to create a node stays its
        display name and two concurrent creators converge on one node.
        ``aliases`` are not written here; see ``add_taxonomy_aliases``.

        Raises:
            ValueError: when ``collection`` is not a taxonomy collection.
            Exception: on write failure.
        """
        pass

    @abstractmethod
    async def ensure_taxonomy_hierarchy_edge(
        self,
        child_collection: str,
        child_key: str,
        parent_key: str,
    ) -> None:
        """Link a subcategory node to its parent (``interCategoryRelations``)
        unless the edge exists, outside any transaction.

        ``child_collection`` is a subcategory level; the parent collection
        follows from it (``CATEGORY_HIERARCHY_PARENTS``). Records sharing a
        new chain call this at once: the write is idempotent, safe under
        concurrent callers, and never leaves two edges for one pair.

        Raises:
            ValueError: when ``child_collection`` is not a subcategory level.
            Exception: on write failure.
        """
        pass

    @abstractmethod
    async def add_taxonomy_aliases(
        self,
        collection: str,
        key: str,
        aliases: list[str],
        normalized_aliases: list[str],
        *,
        org_id: str,
        max_aliases: int = MAX_TAXONOMY_ALIASES,
        transaction: str | None = None,
    ) -> None:
        """Union ``aliases`` into the node's ``aliases`` list and
        ``normalized_aliases`` into ``normalizedAliases``, both capped at
        ``max_aliases`` entries, in one atomic statement. The two lists are
        positional pairs (display spelling, its normalized form). The union is
        taken on pairs, keyed by normalized form, so the stored lists stay
        aligned at the same length through dedupe and the cap.

        Only a node of ``org_id`` is written. A legacy node (no ``orgId``)
        is shared by every org, and another org's node is not this org's to
        name; for either the call is a no-op, so one tenant's spellings never
        reach another's entity search.

        A no-op when ``aliases`` is empty or the node does not exist.

        Raises:
            ValueError: when ``collection`` is not a taxonomy collection, or
                ``org_id`` is empty.
            Exception: on write failure.
        """
        pass
