import re
from collections.abc import Iterable
from logging import Logger
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    RecordRelations,
)
from app.services.graph_db.taxonomy import TAXONOMY_ENTITY_TYPE_BY_COLLECTION

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Connectors whose record groups are scoped by their root instead of by the full
# descendant closure. Slack qualifies because grants sit on the channel and every
# thread under it inherits, so carrying one group id per thread buys nothing —
# a busy workspace has ~50x more threads than channels. Records from these
# connectors must set `rootRecordGroupId`; see ROOT_RECORD_GROUP_IDS_FIELD.
ROOT_SCOPED_CONNECTOR_TYPES = frozenset(
    value.upper()
    for value in (Connectors.SLACK.value, Connectors.SLACK_WORKSPACE.value)
)

# Records granted directly to a user with no container covering them. Expected
# near-empty: app-level connectors write a blanket ORG grant and land in
# app_ids, Drive shared-with-me files get a synthetic group, KB uploads land in
# app_ids. What is left is a record-level connector granting per record while
# creating no record group.
#
# On overflow the whole request falls back to the record-id path. Truncating
# instead would be a permanent, silent, error-free hole — exactly the failure
# mode container filtering is supposed to avoid.
MAX_DIRECT_GRANT_RECORDS = 2_000

# Ceiling on |app_ids| + |record_group_ids| + |direct_records| in one filter.
# Filter size is what actually breaks: OpenSearch rejects a terms clause above
# index.max_terms_count (65,536 by default) and degrades well before it, Redis
# parses the query string on its main thread outside search-timeout, and Qdrant
# probes the payload index once per value per segment. Far below all three, so
# that hitting it means something is wrong upstream rather than that the tenant
# is merely large.
CONTAINER_FILTER_MAX_TERMS = 25_000

# Depth for the INHERIT_PERMISSIONS closure when expanding accessible record
# groups. Not a taste call — an invariant: the closure must be at least as deep
# as the per-record verifier, which walks `1..20`. A shallower closure omits
# containers whose records the verifier would have admitted, and a container
# omitted is unrecoverable recall loss with nothing to notice.
CONTAINER_INHERIT_MAX_DEPTH = 20

# A user reaching one KB through several grants (direct, or more than one team)
# acts with the strongest of them: the ranking both providers already use to pick
# the highest permission on a record. Roles not listed rank below all of these.
KB_ROLE_PRIORITY: dict[str, int] = {
    "OWNER": 6,
    "ORGANIZER": 5,
    "FILEORGANIZER": 4,
    "WRITER": 3,
    "COMMENTER": 2,
    "READER": 1,
}

# How deep a delete follows containment (PARENT_CHILD / ATTACHMENT) from a
# folder or record. Folder nesting has no enforced limit, so this is a guard
# against a cycle, not a product limit: a cascade that stopped at 20 left
# anything deeper behind.
CONTAINMENT_MAX_DEPTH = 1000

# Records considered per entity when listing an entity's records, taken
# before the newest-first sort. Without a bound, a language or broad category
# linked to most of an org's records is sorted in full on every page. Past the
# cap the order is newest among the first records found, and paging ends; the
# provider reports that through ``EntityCandidateRows.capped``.
ENTITY_CANDIDATE_SCAN_CAP = 10_000

# Edge types that make one record the storage/display parent of another.
CANONICAL_PARENT_RELATION_TYPES = (
    RecordRelations.PARENT_CHILD.value,
    RecordRelations.ATTACHMENT.value,
)

# Chains only branch on graph anomalies (duplicate parent edges, two nodes with
# the same externalRecordId, several BELONGS_TO parents), so this caps result
# size; it is not a tuning knob.
PATH_MAX_CANDIDATES = 64

# Deepest a knowledge-base folder may sit; a folder directly in the collection is
# depth 1. Enforced on folder create, upload and move.
KB_MAX_FOLDER_DEPTH = 20


# Bound on the chains enumerated by ancestor_chain_rows; chains only branch on
# graph anomalies, so this is a safety net, not a tuning knob.
ANCESTOR_CHAIN_ENUMERATION_CAP = 10_000


class AncestorChainLimitError(ValueError):
    """More ancestor chains than ANCESTOR_CHAIN_ENUMERATION_CAP: a cut list could
    leave out the canonical chain, so no chain is picked."""


def ancestor_chain_rows(
    start: str,
    links: list[dict[str, str]] | None,
    *,
    max_depth: int,
) -> list[list[str]]:
    """Every maximal ancestor chain from *start*, as id lists ``[start, parent, ...]``.

    *links* are distinct ``{"child", "parent"}`` pairs, so parallel edges between
    the same two nodes count once (the path count no longer doubles per level).
    A chain never repeats a node and has at most *max_depth* steps, matching the
    path traversals this replaces; ``select_canonical_chain_names`` picks one.
    Raises AncestorChainLimitError past ANCESTOR_CHAIN_ENUMERATION_CAP chains.
    """
    parents: dict[str, list[str]] = {}
    for link in links or []:
        child, parent = link.get("child"), link.get("parent")
        if child and parent and parent not in parents.setdefault(child, []):
            parents[child].append(parent)
    for ids in parents.values():
        ids.sort()
    rows: list[list[str]] = []
    path = [start]
    on_path = {start}

    def walk() -> None:
        nexts = [p for p in parents.get(path[-1], []) if p not in on_path] if len(path) <= max_depth else []
        if not nexts:
            if len(rows) >= ANCESTOR_CHAIN_ENUMERATION_CAP:
                raise AncestorChainLimitError(
                    f"{start} has more than {ANCESTOR_CHAIN_ENUMERATION_CAP} ancestor chains"
                )
            rows.append(list(path))
            return
        for parent in nexts:
            path.append(parent)
            on_path.add(parent)
            walk()
            on_path.discard(parent)
            path.pop()

    walk()
    return rows


def select_canonical_chain_names(
    rows: list[Any] | None,
    name_fields: tuple[str, ...],
) -> list[str]:
    """Pick one ancestor chain from path-query candidates; return names root-first.

    Each row is ``{"ids": [start, parent, ..., ancestor], <name_field>: [...]}``
    with lists aligned by position. Arango and Neo4j both return every
    canonical chain and this picks the same one for both: the longest, ties
    broken by the lexicographically smallest id sequence. A node's name is the
    first non-empty string among *name_fields*; nodes without one are dropped.
    """
    best_key: tuple[int, list[str]] | None = None
    best_row: dict[str, Any] | None = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ids = row.get("ids")
        if not isinstance(ids, list) or not ids:
            continue
        key = (-len(ids), [str(i) for i in ids])
        if best_key is None or key < best_key:
            best_key, best_row = key, row
    if best_row is None:
        return []
    names: list[str] = []
    for index in range(len(best_row["ids"])):
        for name_field in name_fields:
            values = best_row.get(name_field)
            value = values[index] if isinstance(values, list) and index < len(values) else None
            if isinstance(value, str) and value:
                names.append(value)
                break
    names.reverse()
    return names


class EntityCandidateRows(list):
    """One entity's candidate record rows, plus whether the provider's scan
    stopped at ``ENTITY_CANDIDATE_SCAN_CAP``.

    When ``capped`` the rows are the newest within an arbitrary bounded subset
    of the entity's records, so neither "newest first" nor "no more records"
    holds for the entity as a whole. A list subclass so existing callers that
    treat the value as a plain list keep working.
    """

    def __init__(self, rows: Iterable[dict[str, Any]] = (), *, capped: bool = False) -> None:
        super().__init__(rows)
        self.capped = capped


class PermittedEntityRows(EntityCandidateRows):
    """The permitted rows found in one window of an entity's candidates.

    ``window_size`` is how many candidates the window held (fewer than asked
    means the candidates ran out). ``examined`` is how many of them were
    walked: all of them, or up to the last row returned when the limit was
    reached. The next window starts at ``offset + examined``.
    """

    def __init__(
        self,
        rows: Iterable[dict[str, Any]] = (),
        *,
        capped: bool = False,
        window_size: int = 0,
        examined: int = 0,
    ) -> None:
        super().__init__(rows, capped=capped)
        self.window_size = window_size
        self.examined = examined

    @classmethod
    def from_window(
        cls,
        hits: Iterable[dict[str, Any]],
        *,
        limit: int,
        window_size: int,
        capped: bool,
    ) -> "PermittedEntityRows":
        """Build from query hits shaped ``{"pos": int, "row": dict}``, where
        ``pos`` is the hit's index in the window."""
        if limit <= 0:
            return cls(capped=capped, window_size=window_size, examined=0)
        ordered = sorted(
            (h for h in hits if h and isinstance(h.get("row"), dict)),
            key=lambda h: int(h.get("pos") or 0),
        )[:limit]
        examined = int(ordered[-1]["pos"]) + 1 if len(ordered) >= limit else window_size
        return cls(
            (h["row"] for h in ordered),
            capped=capped,
            window_size=window_size,
            examined=min(examined, window_size),
        )


def dedupe_agents_by_id(rows: Optional[List[Dict[str, Any]]]) -> List[str]:
    """
    Collapse a list of ``{agentId, agentName}`` rows into a list of agent names,
    deduped by ``agentId``.

    Used by ``check_toolset_instance_in_use`` and ``check_connector_in_use`` on
    both Arango and Neo4j providers. Dedupe must happen by id, not by name —
    two distinct agents can legitimately share a display name, and collapsing
    them would under-report the true blocker count in 409 messages.

    Order is preserved: the first occurrence of each ``agentId`` keeps its
    position in the output.

    Args:
        rows: Result rows, each expected to have ``agentId`` and ``agentName``.
              ``None``, empty list, and non-dict entries are tolerated.

    Returns:
        List of agent names (with duplicates allowed for distinct ids).
    """
    if not rows:
        return []

    seen_ids: set = set()
    names: List[str] = []
    for r in rows:
        if not r or not isinstance(r, dict):
            continue
        aid = r.get("agentId")
        if aid and aid not in seen_ids:
            seen_ids.add(aid)
            names.append(r.get("agentName", "Unknown"))
    return names


# Record-node status fields a query may select on (interpolated, so only these names).
RECORD_STATUS_FIELDS = frozenset({"indexingStatus", "parsingStatus", "extractionStatus"})


def record_status_field(name: str) -> str:
    if name not in RECORD_STATUS_FIELDS:
        raise ValueError(f"not a record status field: {name!r}")
    return name


def build_connector_stats_response(
    rows: List[Dict[str, Any]],
    statuses: List[str],
    org_id: str,
    connector_id: str,
    origin: str = "CONNECTOR",
) -> Dict[str, Any]:
    """
    Build connector stats response from aggregated query rows.

    Used by both ArangoDB and Neo4j providers to process
    get_connector_stats query results.

    Args:
        rows: Query results with recordType, indexingStatus, extractionStatus,
            entitiesStatus (the record's ``entities`` stage state on its current
            revision; absent or None when it has none), cnt
        statuses: List of valid indexing status values
        org_id: Organization ID
        connector_id: Connector ID
        origin: Origin type ("CONNECTOR" or "COLLECTION"), defaults to "CONNECTOR"

    Returns:
        Formatted stats response dictionary
    """
    indexing_status_counts = {s: 0 for s in statuses}
    extraction_status_counts = dict.fromkeys(statuses, 0)
    entities_status_counts = dict.fromkeys(statuses, 0)
    record_type_counts: Dict[str, Dict[str, Any]] = {}
    total = 0

    for row in rows:
        cnt = row.get("cnt", 0)
        total += cnt
        st = row.get("indexingStatus")
        if st in indexing_status_counts:
            indexing_status_counts[st] += cnt
        ex = row.get("extractionStatus")
        if ex in extraction_status_counts:
            extraction_status_counts[ex] += cnt
        en = row.get("entitiesStatus")
        if en in entities_status_counts:
            entities_status_counts[en] += cnt
        rt = row.get("recordType")
        if rt:
            if rt not in record_type_counts:
                record_type_counts[rt] = {
                    "recordType": rt,
                    "total": 0,
                    "indexingStatus": {s: 0 for s in statuses},
                    "extractionStatus": dict.fromkeys(statuses, 0),
                    "entitiesStatus": dict.fromkeys(statuses, 0),
                }
            record_type_counts[rt]["total"] += cnt
            if st in statuses:
                record_type_counts[rt]["indexingStatus"][st] += cnt
            if ex in statuses:
                record_type_counts[rt]["extractionStatus"][ex] += cnt
            if en in statuses:
                record_type_counts[rt]["entitiesStatus"][en] += cnt

    return {
        "orgId": org_id,
        "connectorId": connector_id,
        "origin": origin,
        "stats": {
            "total": total,
            "indexingStatus": indexing_status_counts,
            "extractionStatus": extraction_status_counts,
            "entitiesStatus": entities_status_counts,
        },
        "byRecordType": list(record_type_counts.values()),
    }


# Trash writes go in pages of this many keys, one statement each: the ArangoDB
# mark (inside one stream transaction) and restores.
SOFT_DELETE_CHUNK = 1000

# Cleared when a record leaves the trash; ``isDeleted`` is set to false instead.
TRASH_STATE_FIELDS = (
    "deletedAtTimestamp",
    "deleteSource",
    "deleteBatchId",
    "deletedByUserId",
    "purgeAttempts",
    "purgeLastError",
    "trashedExternalRecordId",
)

# Bookkeeping the services keep on a stored record for their own use: the trash
# state (the record endpoint refuses a trashed record, and a restore clears it),
# when a rate-limit wait began, the stranded sweep's re-sends and the duplicate
# taxonomy copy. No client reads them, and the record endpoint's response
# schema rejects fields it does not list, so the endpoint leaves these out.
# `deletedByUserId` predates the trash and is documented, so it stays in.
INTERNAL_RECORD_FIELDS = frozenset({
    "deletedAtTimestamp",
    "deleteSource",
    "deleteBatchId",
    "purgeAttempts",
    "purgeLastError",
    "trashedExternalRecordId",
    "rateLimitedSince",
    "lastRepublishedAt",
    "republishCount",
    "duplicateReconcilePending",
    "duplicateReconcileAttempts",
    "duplicateReconcileDueAt",
})


def api_record(record: dict[str, Any]) -> dict[str, Any]:
    """The stored record as the record endpoint returns it, without ``INTERNAL_RECORD_FIELDS``."""
    return {key: value for key, value in record.items() if key not in INTERNAL_RECORD_FIELDS}


# A Recently deleted row for a multi-select delete names this many of its other items.
TRASH_LIST_OTHER_ROOT_NAMES = 3

# Unique per record and never a source id, so no sync or move can land on it.
TRASHED_EXTERNAL_ID_PREFIX = "trashed:"

# Stamped on a file in the same write that restores it from the trash. With the
# file still NOT_STARTED it means the re-index its lost vectors need was never
# taken up, which a retried restore and the stranded sweep both act on.
RESTORED_AT_FIELD = "restoredAtTimestamp"


def restore_items(
    restores: list[dict[str, Any]], connector_id: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """``restore_records`` items as ``{id, set}``, and the ``{id, ext}`` external ids they take back."""
    items: list[dict[str, Any]] = []
    reclaims: list[dict[str, str]] = []
    for item in restores:
        fields = dict(item.get("set") or {})
        if external_id := item.get("reclaimExternalRecordId"):
            fields["externalRecordId"] = external_id
            reclaims.append({"id": item["id"], "ext": external_id})
        items.append({"id": item["id"], "set": fields})
    if reclaims and not connector_id:
        raise ValueError("restore_records needs connector_id to take an external id back")
    return items, reclaims


def empty_soft_delete_result(batch_id: str) -> dict[str, Any]:
    return soft_delete_result([], [], [], batch_id)


def soft_delete_result(
    requested: list[str],
    root_keys: list[str],
    marked: list[dict[str, Any]],
    batch_id: str,
) -> dict[str, Any]:
    """The ``soft_delete_records`` result, the same on both providers."""
    roots = set(root_keys)
    failed = [
        {"record_id": rid, "reason": "Not found, already deleted, or outside this connector"}
        for rid in requested
        if rid not in roots
    ]
    vrids = list(dict.fromkeys(m["vrid"] for m in marked if m.get("vrid")))
    org_ids = {m.get("orgId") for m in marked if m.get("orgId")}
    return {
        "success": True,
        "soft_deleted_records": [
            {"record_id": m["id"], "name": m.get("name") or "Unknown", "virtual_record_id": m.get("vrid")}
            for m in marked
        ],
        "failed_records": failed,
        "total_requested": len(requested),
        "successfully_deleted": len(roots),
        "failed_count": len(failed),
        "virtual_record_ids": vrids,
        "org_id": next(iter(org_ids)) if len(org_ids) == 1 else None,
        "batch_id": batch_id,
    }


def soft_delete_request_result(record_id: str, record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Shape a ``soft_delete_records`` result like the hard ``delete_record`` result."""
    if not result.get("successfully_deleted"):
        return {"success": False, "code": 404, "reason": f"Record not found: {record_id}"}
    connector_name = record.get("connectorName")
    is_kb = record.get("origin") == "UPLOAD" or connector_name == Connectors.KNOWLEDGE_BASE.value
    return {
        "success": True,
        "record_id": record_id,
        "connector": connector_name,
        "isKb": is_kb,
        "connectorId": record.get("connectorId"),
        "orgId": record.get("orgId"),
        "softDeleted": True,
        "batchId": result.get("batch_id"),
        "softDeletedRecords": result.get("soft_deleted_records", []),
        "virtualRecordIds": result.get("virtual_record_ids", []),
        "eventData": None,
    }


_STORAGE_DOCUMENT_ID = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def uploaded_document_id(record: Dict[str, Any], type_doc: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The storage document holding an uploaded file's original bytes, or None.

    A knowledge-base upload keeps its file in a storage document of its own,
    named by the record's externalRecordId. Folders are uploads too but hold no
    file, and other origins use externalRecordId for the source system's id.
    """
    if record.get("origin") != OriginTypes.UPLOAD.value:
        return None
    if (type_doc or {}).get("isFile") is False:
        return None
    document_id = record.get("externalRecordId")
    if isinstance(document_id, str) and _STORAGE_DOCUMENT_ID.match(document_id):
        return document_id
    return None


def is_storage_document_id(value: object) -> bool:
    return isinstance(value, str) and _STORAGE_DOCUMENT_ID.match(value) is not None


def trash_purge_row(
    key: str, record: dict[str, Any], type_doc: dict[str, Any] | None, delete_payload: dict[str, Any]
) -> dict[str, Any]:
    """What the purge needs about one record in the trash, the same on both stores.

    ``deleteRecordPayload`` is the hard delete's own ``deleteRecord`` payload, so
    indexing cleans a purged record exactly as it cleans a deleted one.
    """
    type_doc = type_doc or {}
    return {
        "id": key,
        "orgId": record.get("orgId"),
        "connectorId": record.get("connectorId"),
        "connectorName": record.get("connectorName"),
        "origin": record.get("origin"),
        "deletedAtTimestamp": record.get("deletedAtTimestamp"),
        "virtualRecordId": record.get("virtualRecordId"),
        "storageDocumentId": record.get("storageDocumentId"),
        "filePath": type_doc.get("path"),
        "uploadDocumentId": uploaded_document_id(record, type_doc),
        "deleteRecordPayload": {
            **delete_payload,
            "connectorName": record.get("connectorName"),
            "origin": record.get("origin"),
        },
    }


def cleans_taxonomy_points(record: dict[str, Any]) -> bool:
    """Whether a deleted record's ``deleteRecord`` names its taxonomy entities
    (``taxonomyRefs``): a knowledge-base record with vectors. Connector records
    are left out: their deletes do not clean taxonomy points yet."""
    return bool(record.get("virtualRecordId")) and (
        record.get("origin") == OriginTypes.UPLOAD.value
        or record.get("connectorName") == Connectors.KNOWLEDGE_BASE.value
    )


async def record_taxonomy_refs(
    graph: "IGraphDBProvider", record_keys: Iterable[str], logger: Logger,
) -> dict[str, list[dict[str, str]]]:
    """The taxonomy entities each record links to, as ``{record key: [{"id",
    "type"}]}``, read before a delete removes the edges so the indexing service
    can then delete the entity points no other record reaches
    (``EntityVectorStore.delete_unreached_entities``).

    A failed read returns ``{}``: the delete must not fail over a cleanup, and
    without refs the points stay as they always did.
    """
    keys = list(dict.fromkeys(str(key) for key in record_keys if key))
    if not keys:
        return {}
    try:
        links = await graph.get_record_taxonomy_links(keys, include_departments=True)
    except Exception as exc:
        logger.warning(
            "Could not read the taxonomy links of %d deleted record(s); their entity points stay: %s",
            len(keys), exc,
        )
        return {}
    refs: dict[str, list[dict[str, str]]] = {}
    for link in links:
        entity_type = TAXONOMY_ENTITY_TYPE_BY_COLLECTION.get(link.get("collection"))
        record_key, entity_id = link.get("recordId"), link.get("entityId")
        if not entity_type or not record_key or not entity_id:
            continue
        ref = {"id": str(entity_id), "type": entity_type}
        bucket = refs.setdefault(str(record_key), [])
        if ref not in bucket:
            bucket.append(ref)
    return refs


async def deleted_record_taxonomy_refs(
    graph: "IGraphDBProvider", records: Iterable[dict[str, Any]], logger: Logger,
) -> dict[str, list[dict[str, str]]]:
    """``record_taxonomy_refs`` for the records in ``records`` that
    ``cleans_taxonomy_points``, keyed by ``_key`` (ArangoDB) or ``id`` (Neo4j)."""
    return await record_taxonomy_refs(
        graph,
        [record.get("_key") or record.get("id") for record in records if cleans_taxonomy_points(record)],
        logger,
    )


def jira_issue_browse_url_regex(issue_key: str) -> str:
    """A regex matching a Jira webUrl for exactly ``issue_key``.

    The key must end the URL or be followed by ``/``, ``?`` or ``#``, so ENG-1
    does not match ENG-12. The leading ``.*`` and trailing ``$`` make it mean
    the same under Neo4j's whole-string ``=~`` and Arango's substring REGEX_TEST.
    """
    return f".*{re.escape(f'/browse/{issue_key}')}(?:[/?#].*)?$"


async def org_id_from_app_edge(graph_provider: "IGraphDBProvider", app_id: str) -> str | None:
    """The org an app belongs to, from its org-app edge.

    Connector apps created before ``orgId`` was stored on the document have
    their org only on this edge. Neo4j returns a bare id in ``from_id``;
    Arango a document handle in ``_from`` ("organizations/<key>").
    """
    edges = await graph_provider.get_edges_to_node(
        f"{CollectionNames.APPS.value}/{app_id}",
        CollectionNames.ORG_APP_RELATION.value,
    )
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        source = edge.get("from_id") or edge.get("_from")
        if source:
            return str(source).rsplit("/", 1)[-1]
    return None
