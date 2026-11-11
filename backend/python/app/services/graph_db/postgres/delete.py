"""Deleting one record with its permission rules, its attachments and the event, and the unlink of what a full sync did not see.

A record goes with its type documents, blocks and every edge (``_delete_records_with_their_types``), in one transaction. Every method resolves the
ambient org before anything else (and before any ``try``); every statement is a literal with bound parameters.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
)
from app.core.org_context import current_org_id
from app.services.graph_db.common.full_sync_unlink import (
    FULL_SYNC_EDGE_COLLECTIONS,
    split_unseen_by_window,
    unseen_node_keys,
)
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    is_live_record,
)
from app.services.graph_db.common.utils import (
    deleted_record_taxonomy_refs,
    soft_delete_request_result,
)
from app.services.graph_db.postgres.base import PgBase, _doc, _j
from app.services.graph_db.postgres.hierarchy import _EDGE_COLLS, _remove_records
from app.services.graph_db.postgres.records import _TYPE_COLLS
from app.services.graph_db.postgres.trash import _sweep_edges
from app.services.graph_db.taxonomy import TAXONOMY_EDGE_COLLECTIONS
from app.services.graph_db.vector_membership_queries import can_use_membership_cleanup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence

    from app.core.db import Tx

_ORG_FIELD = "orgId"  # the org property of a graph document
# The roles that may delete a connector's record on a user's request, as on ArangoDB (Drive, Gmail, Outlook, Local FS); a connector not listed needs
# OWNER or WRITER, so a user who can only view a record never deletes it.
_DELETE_ROLES = {
    Connectors.GOOGLE_DRIVE.value: ("OWNER", "WRITER", "FILEORGANIZER"),
    Connectors.GOOGLE_MAIL.value: ("OWNER", "WRITER"),
    Connectors.OUTLOOK.value: ("OWNER",),
    Connectors.OUTLOOK_INDIVIDUAL.value: ("OWNER",),
    Connectors.LOCAL_FS.value: ("OWNER",),
}
_DEFAULT_DELETE_ROLES = ("OWNER", "WRITER")


_CHUNK = 5000      # nodes removed per statement (and per transaction, when a connector is deleted in batches)
_HANDLES = "connectorDeleteHandles"      # the ids a delete in batches must still name after the nodes are gone: written in the transaction that removes them
_CONNECTOR_NODE_COLLS = ("syncPoints", "stageStates", "agentKnowledge", "blocks")      # nodes that name their connector and hang off no edge the cascade follows (blocks: the ones no record owns any more)
_LATE_WRITE_COLLS = ("blocks", "stageStates")      # what indexing already in flight can still write for a connector after its delete
_LATE_LINK_COLLS = ("nodeRelations", "recordLinks")      # where its code links land; they carry the record group, not the connector


async def _remove_nodes(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
    """Removes nodes of one collection with every edge around them; the rows are locked in key order first."""
    if not keys:
        return 0
    keys = sorted(set(keys))
    await tx.execute("/* pipeshub:graph.connector_delete_lock */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) ORDER BY id FOR UPDATE", org, coll, keys)
    await _sweep_edges(tx, org, _EDGE_COLLS, [f"{coll}/{k}" for k in keys])
    return await tx.fetchval("/* pipeshub:graph.connector_delete_nodes */ WITH d AS (DELETE FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) RETURNING 1) SELECT count(*) FROM d",
                             org, coll, keys)


async def _remove_named_batch(tx: Tx, org: str, coll: str, connector_id: str) -> tuple[int, int]:
    """Up to ``_CHUNK`` nodes of one collection that name the connector, with their edges: (found, removed)."""
    keys = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.late_write_named */ SELECT id FROM graph.nodes
        WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3 ORDER BY id LIMIT $4""", org, coll, connector_id, _CHUNK)]
    return len(keys), int(await _remove_nodes(tx, org, coll, keys) or 0)


async def _remove_group_links_batch(tx: Tx, org: str, groups: list[str]) -> int:
    """Up to ``_CHUNK`` code links that carry one of the groups. The (org_id, coll) prefix of the key bounds the scan to the org's links."""
    return int(await tx.fetchval("""/* pipeshub:graph.late_write_links */ WITH d AS (DELETE FROM graph.edges
        WHERE (org_id, coll, from_id, from_coll, to_id, to_coll, ekey) IN (
            SELECT org_id, coll, from_id, from_coll, to_id, to_coll, ekey FROM graph.edges
            WHERE org_id = $1 AND coll = ANY($2::text[]) AND doc->>'recordGroupId' = ANY($3::text[]) LIMIT $4)
        RETURNING 1) SELECT count(*) FROM d""", org, list(_LATE_LINK_COLLS), groups, _CHUNK) or 0)


async def _lock_labels(tx: Tx, org: str, coll: str, keys: list[str]) -> None:
    await tx.execute("/* pipeshub:graph.connector_delete_lock_labels */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) ORDER BY id FOR UPDATE", org, coll, keys)


async def _remove_unlinked_labels(tx: Tx, org: str, coll: str, keys: list[str]) -> int:
    """Removes the taxonomy nodes of ``keys`` that no record links and no merge points at or from. The link check is made in the statement that deletes, after the rows
    are locked, so a record linked to a label in the meantime keeps it (the writer of such a link does not lock the label: the check is as late as it can be)."""
    await _lock_labels(tx, org, coll, keys)
    gone = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.connector_delete_unlinked_taxonomy */ DELETE FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = $2
        AND t.id = ANY($3::text[]) AND t.doc->>'mergedInto' IS NULL
          AND NOT EXISTS (SELECT 1 FROM graph.nodes o WHERE o.org_id = t.org_id AND o.coll = t.coll AND o.doc->>'mergedInto' = t.id)
          AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = t.org_id AND e.coll = $4 AND e.to_coll = t.coll AND e.to_id = t.id AND e.from_coll = 'records')
        RETURNING t.id""", org, coll, keys, TAXONOMY_EDGE_COLLECTIONS[coll])]
    await _sweep_edges(tx, org, _EDGE_COLLS, [f"{coll}/{k}" for k in gone])
    return len(gone)


async def _delete_connector(tx: Tx, org: str, connector_id: str) -> dict[str, Any]:
    row = await tx.fetchrow("/* pipeshub:graph.connector_delete_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2 FOR UPDATE", org, connector_id)
    if not row:
        return {"success": False, "error": f"Connector instance {connector_id} not found"}
    app = _doc(row["doc"])
    needs_vrids = not can_use_membership_cleanup(app)
    held = {r["coll"]: r for r in await tx.fetch("""/* pipeshub:graph.connector_delete_collect */ SELECT coll, array_agg(id ORDER BY id) AS ids,
          array_agg(DISTINCT doc->>'virtualRecordId') FILTER (WHERE doc->>'virtualRecordId' IS NOT NULL) AS vrids FROM graph.nodes
        WHERE org_id = $1 AND coll IN ('records', 'recordGroups', 'roles', 'groups') AND doc->>'connectorId' = $2 GROUP BY coll""", org, connector_id)}
    ids = {c: list(held[c]["ids"]) if c in held else [] for c in ("records", "recordGroups", "roles", "groups")}
    vrids = list(held["records"]["vrids"] or []) if needs_vrids and "records" in held else []
    taxonomy: dict[str, list[str]] = {}      # read before the records go: only these nodes are looked at afterwards
    for coll, edge in TAXONOMY_EDGE_COLLECTIONS.items():
        found = [r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.connector_delete_taxonomy */ SELECT DISTINCT e.to_id FROM graph.edges e
            JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id AND r.doc->>'connectorId' = $3
            WHERE e.org_id = $1 AND e.coll = $2 AND e.from_coll = 'records' AND e.to_coll = $4""", org, edge, connector_id, coll)] if ids["records"] else []
        if found:
            taxonomy[coll] = found
    # The connector's records are joined in the database, not bound as a list: a connector can hold millions (one distinct count over all of them).
    typed = await tx.fetchval("""/* pipeshub:graph.connector_delete_type_count */ SELECT count(DISTINCT t.id) FROM (
          SELECT e.to_id AS id FROM graph.edges e JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id AND r.doc->>'connectorId' = $2
            WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.to_coll = ANY($3::text[])
          UNION SELECT n.id FROM graph.nodes n JOIN graph.nodes r ON r.org_id = n.org_id AND r.coll = 'records' AND r.id = n.id AND r.doc->>'connectorId' = $2
            WHERE n.org_id = $1 AND n.coll = ANY($3::text[])) t""", org, connector_id, _TYPE_COLLS) if ids["records"] else 0
    blocks = await tx.fetchval("""/* pipeshub:graph.connector_delete_block_count */ SELECT count(*) FROM graph.nodes b
        JOIN graph.nodes r ON r.org_id = b.org_id AND r.coll = 'records' AND r.id = b.doc->>'recordId' AND r.doc->>'connectorId' = $2
        WHERE b.org_id = $1 AND b.coll = 'blocks'""", org, connector_id) if ids["records"] else 0
    removed_records = 0
    for pass_ in range(3):      # a record written while this ran is taken in the same unit, then the unit is checked
        if pass_ == 0:
            keys = ids["records"]
        else:
            late = await tx.fetch("/* pipeshub:graph.connector_delete_late */ SELECT id, doc->>'virtualRecordId' AS vrid FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND doc->>'connectorId' = $2",
                                  org, connector_id)
            keys = [r["id"] for r in late]
            if needs_vrids:      # a late record's vector must be named for the cleanup like the others
                vrids.extend(sorted({r["vrid"] for r in late if r["vrid"]} - set(vrids)))
        if not keys:
            break
        for i in range(0, len(keys), _CHUNK):
            removed_records += await _remove_records(tx, org, keys[i:i + _CHUNK])
        if pass_:
            ids["records"] = [*ids["records"], *keys]
    counts = {}
    for coll in ("recordGroups", "roles", "groups"):
        counts[coll] = await _remove_nodes(tx, org, coll, ids[coll])
    for coll in _CONNECTOR_NODE_COLLS:
        keys = [r["id"] for r in await tx.fetch("/* pipeshub:graph.connector_delete_named */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3",
                                                org, coll, connector_id)]
        removed = await _remove_nodes(tx, org, coll, keys)
        blocks += removed if coll == "blocks" else 0
    await tx.execute("/* pipeshub:graph.connector_delete_links */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'authenticatedAs' AND doc->>'connectorId' = $2", org, connector_id)
    await _remove_nodes(tx, org, "apps", [connector_id])
    left = await tx.fetchval("""/* pipeshub:graph.connector_delete_left */ SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND doc->>'connectorId' = $2
        AND coll IN ('records', 'recordGroups', 'roles', 'groups', 'syncPoints', 'stageStates', 'agentKnowledge', 'blocks')""", org, connector_id)
    if left:
        raise RuntimeError(f"{left} node(s) of the connector remain")
    labels = 0
    for coll, found in taxonomy.items():      # the nodes only this connector's records linked; a merge loser, and the node a loser points at, are left for the redirect
        labels += await _remove_unlinked_labels(tx, org, coll, found)
    return {"success": True, "deleted_records_count": removed_records, "deleted_record_groups_count": counts["recordGroups"], "deleted_roles_count": counts["roles"],
            "deleted_groups_count": counts["groups"], "deleted_blocks_count": blocks, "deleted_isoftype_targets_count": typed, "cascade_retries": 0,
            "deleted_orphan_type_docs_count": 0, "deleted_orphan_taxonomy_count": labels, "virtual_record_ids": vrids, "record_group_ids": ids["recordGroups"],
            "vector_membership_backfilled": bool(app.get("vectorMembershipBackfilled", False)),
            "vector_membership_backfill_exhausted": bool(app.get("vectorMembershipBackfillExhausted", False)), "connector_id": connector_id, "connector_name": app.get("type")}


async def _delete_connector_atomically(tx: Tx, org: str, connector_id: str, *, savepoint: bool) -> dict[str, Any]:
    """``_delete_connector`` as one unit inside a caller's transaction: a failure part way rolls back to the savepoint, so the caller's later commit
    keeps none of it. Without a caller's transaction the unit itself rolls back on the error."""
    if not savepoint:
        return await _delete_connector(tx, org, connector_id)
    async with tx.savepoint("connector_delete"):
        return await _delete_connector(tx, org, connector_id)


async def _stash(tx: Tx, org: str, connector_id: str, kind: str, ids: list[str]) -> None:
    for i in range(0, len(ids), _CHUNK):
        await tx.execute("/* pipeshub:graph.connector_delete_stash */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, $2, $3, $4::jsonb)", org, _HANDLES,
                         f"{connector_id}:{uuid.uuid4().hex}", _j({"orgId": org, "connectorId": connector_id, "kind": kind, "ids": ids[i:i + _CHUNK]}))


async def _stashed(tx: Tx, org: str, connector_id: str) -> dict[str, list[str]]:
    """What earlier attempts of this connector's delete stashed, by kind."""
    kinds: dict[str, list[str]] = {}
    rows = await tx.fetch("/* pipeshub:graph.connector_delete_stashed */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3", org, _HANDLES, connector_id)
    for r in rows:
        d = _doc(r["doc"])
        kinds.setdefault(d["kind"], []).extend(d["ids"])
    return kinds


async def _close_connector(tx: Tx, org: str, connector_id: str) -> dict[str, Any] | None:
    """The connector's document, after nobody can reach it any more: the grants on the app (they are the gate every access check starts from) and the
    source-account links go first, so a delete that stops part way leaves records no one can open, never a half-deleted tree that opens more than it did."""
    row = await tx.fetchrow("/* pipeshub:graph.connector_delete_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2 FOR UPDATE", org, connector_id)
    if not row:
        return None
    await tx.execute("/* pipeshub:graph.connector_delete_gate */ DELETE FROM graph.edges WHERE org_id = $1 AND coll IN ('permission', 'userAppRelation') AND to_coll = 'apps' AND to_id = $2", org, connector_id)
    await tx.execute("/* pipeshub:graph.connector_delete_links */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'authenticatedAs' AND doc->>'connectorId' = $2", org, connector_id)
    return _doc(row["doc"])


async def _record_batch(tx: Tx, org: str, connector_id: str, after: str, *, needs_vrids: bool) -> tuple[str, int, int, int] | None:
    """The next records of the connector after key ``after``, removed with what hangs off them. Their virtual record ids (when the app needs them) and the taxonomy
    nodes they link are stashed in this transaction; answers (last key, records removed, type documents, blocks), None when there are no more."""
    rows = await tx.fetch("""/* pipeshub:graph.connector_delete_next_records */ SELECT id, doc->>'virtualRecordId' AS vrid FROM graph.nodes
        WHERE org_id = $1 AND coll = 'records' AND doc->>'connectorId' = $2 AND id > $3 ORDER BY id LIMIT $4""", org, connector_id, after, _CHUNK)
    if not rows:
        return None
    ids = [r["id"] for r in rows]
    stashed: dict[str, list[str]] = {}
    if needs_vrids:
        stashed["virtualRecord"] = sorted({r["vrid"] for r in rows if r["vrid"]})
    for coll, edge in TAXONOMY_EDGE_COLLECTIONS.items():
        found = [r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.connector_delete_batch_taxonomy */ SELECT DISTINCT e.to_id FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = $2 AND e.from_coll = 'records' AND e.from_id = ANY($3::text[]) AND e.to_coll = $4""", org, edge, ids, coll)]
        if found:
            stashed[f"label:{coll}"] = found
    typed = await tx.fetchval("""/* pipeshub:graph.connector_delete_batch_types */ SELECT count(DISTINCT t.id) FROM (
          SELECT e.to_id AS id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id = ANY($2::text[]) AND e.to_coll = ANY($3::text[])
          UNION SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($3::text[]) AND n.id = ANY($2::text[])) t""", org, ids, _TYPE_COLLS)
    blocks = await tx.fetchval("/* pipeshub:graph.connector_delete_batch_blocks */ SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = 'blocks' AND doc->>'recordId' = ANY($2::text[])", org, ids)
    for kind, vals in stashed.items():
        await _stash(tx, org, connector_id, kind, vals)
    # the pipeline stage states of these records' virtual records, by the index on them, so the collection-wide pass later finds few
    await tx.execute("""/* pipeshub:graph.connector_delete_batch_stage_states */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'stageStates'
        AND doc->>'virtualRecordId' = ANY($2::text[]) AND doc->>'connectorId' = $3""", org, sorted({r["vrid"] for r in rows if r["vrid"]}), connector_id)
    return ids[-1], await _remove_records(tx, org, ids), typed, blocks


# nodes that name their connector in a collection with no index on it: taken a batch at a time with no order, so a batch stops reading at its fifth thousandth match
_UNINDEXED_COLLS = ("syncPoints", "stageStates", "agentKnowledge")


async def _node_batch(tx: Tx, org: str, connector_id: str, coll: str, after: str, *, stash_kind: str | None) -> tuple[str, int] | None:
    """The next nodes of ``coll`` that name the connector, removed with their edges. Where the collection has an index on the connector, in key order after key
    ``after`` (one pass in all). Where it has none, any ``_CHUNK`` of the matches (the next call reads again from the start, and a batch that removes nothing fails
    the delete rather than looping). shortcut: when the connector's rows are the last of a very large collection every batch reads past the others', upgrade to a
    partial index on the connector's key for these collections."""
    if coll in _UNINDEXED_COLLS:
        ids = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.connector_delete_any_nodes */ SELECT id FROM graph.nodes
            WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3 LIMIT $4""", org, coll, connector_id, _CHUNK)]
    else:
        ids = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.connector_delete_next_nodes */ SELECT id FROM graph.nodes
            WHERE org_id = $1 AND coll = $2 AND id > $3 AND doc->>'connectorId' = $4 ORDER BY id LIMIT $5""", org, coll, after, connector_id, _CHUNK)]
    if not ids:
        return None
    if stash_kind:
        await _stash(tx, org, connector_id, stash_kind, ids)
    removed = await _remove_nodes(tx, org, coll, ids)
    if coll in _UNINDEXED_COLLS and not removed:
        raise RuntimeError(f"could not remove the {coll} of the connector")
    return ids[-1], removed


async def _finish_connector(tx: Tx, org: str, connector_id: str) -> dict[str, Any] | None:
    """The last unit of a delete in batches: the source-account links, the app, the check that nothing of the connector is left, the taxonomy nodes only its
    records linked and the stash. What the answer names is read from the stash here, whatever run wrote it. None when the app is already gone (another run
    finished first)."""
    if not await tx.fetchrow("/* pipeshub:graph.connector_delete_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2 FOR UPDATE", org, connector_id):
        return None
    await tx.execute("/* pipeshub:graph.connector_delete_links */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'authenticatedAs' AND doc->>'connectorId' = $2", org, connector_id)
    await _remove_nodes(tx, org, "apps", [connector_id])
    for coll in ("records", "recordGroups", "roles", "groups", *_CONNECTOR_NODE_COLLS):      # one read per collection, so each is served by its own index
        if await tx.fetch_custom_plan("/* pipeshub:graph.connector_delete_left */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3 LIMIT 1",
                                      org, coll, connector_id):
            raise RuntimeError(f"nodes of the connector remain in {coll}")
    names = await _stashed(tx, org, connector_id)      # read last: a rival run's final batch is in it
    removed = 0
    for kind, found in names.items():
        if kind.startswith("label:"):
            for i in range(0, len(found), _CHUNK):
                removed += await _remove_unlinked_labels(tx, org, kind.removeprefix("label:"), found[i:i + _CHUNK])
    await tx.execute("/* pipeshub:graph.connector_delete_stash_drop */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'connectorId' = $3", org, _HANDLES, connector_id)
    return {"labels": removed, "virtual_record_ids": sorted(set(names.get("virtualRecord", ()))), "record_group_ids": sorted(set(names.get("recordGroup", ())))}


async def _delete_in_batches(q: Callable[..., Awaitable[Any]], org: str, connector_id: str) -> dict[str, Any]:
    """``delete_connector_instance`` without a caller's transaction; ``q(fn, None, write=True)`` runs ``fn`` in a transaction of its own.
    A failure after the grants are gone answers ``partial``: the connector is still there, closed to its users, and a call finishes it."""
    async def run(fn: Callable[[Tx], Awaitable[Any]]) -> Any:      # noqa: ANN401
        return await q(fn, None, write=True)

    removed = {"records": 0, "recordGroups": 0, "roles": 0, "groups": 0}
    typed = blocks = 0
    try:
        app = await run(lambda tx: _close_connector(tx, org, connector_id))      # an earlier attempt may have removed the grants and some records already
        if app is None:
            return {"success": False, "error": f"Connector instance {connector_id} not found"}
        needs_vrids = not can_use_membership_cleanup(app)
        for _ in range(3):      # a record written while this ran is taken by the next pass
            batches = 0
            last = ""
            while got := await run(lambda tx: _record_batch(tx, org, connector_id, last, needs_vrids=needs_vrids)):      # noqa: B023
                last, n, t, b = got
                removed["records"] += n
                typed, blocks = typed + t, blocks + b
                batches += 1
            if not batches:
                break
        for coll in ("recordGroups", "roles", "groups", *_CONNECTOR_NODE_COLLS):
            last = ""
            while got := await run(lambda tx: _node_batch(tx, org, connector_id, coll, last, stash_kind="recordGroup" if coll == "recordGroups" else None)):      # noqa: B023
                last, n = got
                if coll in removed:
                    removed[coll] += n
                blocks += n if coll == "blocks" else 0
        done = await run(lambda tx: _finish_connector(tx, org, connector_id))
    except Exception as e:
        return {"success": False, "partial": True, "error": f"Failed to delete connector instance: {e}"}
    if done is None:      # another call finished it between this call's last batch and its own last unit
        return {"success": False, "error": f"Connector instance {connector_id} not found"}
    return {"success": True, "deleted_records_count": removed["records"], "deleted_record_groups_count": removed["recordGroups"], "deleted_roles_count": removed["roles"],
            "deleted_groups_count": removed["groups"], "deleted_blocks_count": blocks, "deleted_isoftype_targets_count": typed, "cascade_retries": 0,
            "deleted_orphan_type_docs_count": 0, "deleted_orphan_taxonomy_count": done["labels"], "virtual_record_ids": done["virtual_record_ids"],
            "record_group_ids": done["record_group_ids"], "vector_membership_backfilled": bool(app.get("vectorMembershipBackfilled", False)),
            "vector_membership_backfill_exhausted": bool(app.get("vectorMembershipBackfillExhausted", False)), "connector_id": connector_id, "connector_name": app.get("type")}


class DeleteMixin(PgBase):
    async def _drows(self, sql: str, *args: Any, transaction: str | None = None, write: bool = False) -> list:  # noqa: ANN401
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction, write=write)

    async def _direct_attachment_ids(self, record_id: str, org_id: str, transaction: str | None) -> list[str]:
        rows = await self._drows("""/* pipeshub:graph.records_direct_attachments */ SELECT DISTINCT e.to_id AS id FROM graph.edges e
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'records' AND a.id = e.to_id AND a.doc->>'orgId' = $3
            WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = $2 AND e.doc->>'relationshipType' = 'ATTACHMENT'""",
                                current_org_id(), record_id, org_id, transaction=transaction)
        return [r["id"] for r in rows]

    async def _attachments_to_delete(self, record_id: str, org_id: str, transaction: str | None) -> tuple[list[str], list[dict]]:
        """A record's direct ATTACHMENT children and their deleteRecord payloads, read before the delete (two reads for any number of them).

        Attachments already in the trash, from an earlier batch, go too: a restore refuses an item whose parent is in the trash, so with its mail
        gone for good one could never come back."""
        ids = await self._direct_attachment_ids(record_id, org_id, transaction)
        if not ids:
            return [], []
        rows = await self._drows("""/* pipeshub:graph.records_attachment_payload_docs */ SELECT r.doc AS rec, ty.doc AS typ FROM graph.nodes r LEFT JOIN LATERAL (SELECT t.doc, t.coll AS tcoll FROM graph.edges e JOIN graph.nodes t ON t.org_id = e.org_id AND t.coll = e.to_coll AND t.id = e.to_id
    WHERE e.org_id = r.org_id AND e.coll = 'isOfType' AND e.from_id = r.id LIMIT 1) ty ON true
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'virtualRecordId' IS NOT NULL""",
                                current_org_id(), ids, transaction=transaction)
        payloads = []
        for r in rows:
            rec = _doc(r["rec"])
            payload = await self._create_deleted_record_event_payload(rec, _doc(r["typ"]) if r["typ"] else None)
            if payload:
                payload["connectorName"], payload["origin"] = rec.get("connectorName"), rec.get("origin")
                payloads.append(payload)
        return ids, payloads

    async def _refuse_without_delete_role(self, record_id: str, record: dict, user_id: str, transaction: str | None) -> dict | None:
        """A refusal unless the user holds a deleting role on the record (granted to them, or to a group, role or team they hold a USER permission on, as the access check reads membership); a role that cannot be read refuses."""
        user = await self.get_user_by_user_id(user_id)
        if not user:
            return {"success": False, "code": 404, "reason": f"User not found: {user_id}"}
        user_key = user.get("id") or user.get("_key")
        rows = await self._drows("""/* pipeshub:graph.delete_record_roles */ SELECT p.doc->>'role' AS role FROM graph.edges p
            WHERE p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = 'records' AND p.to_id = $2
              AND ((p.from_coll = 'users' AND p.from_id = $3 AND p.doc->>'type' = 'USER')
                OR (p.from_coll IN ('groups', 'roles', 'teams') AND EXISTS (SELECT 1 FROM graph.edges b WHERE b.org_id = $1
                    AND b.coll = 'permission' AND b.from_coll = 'users' AND b.from_id = $3 AND b.to_coll = p.from_coll AND b.to_id = p.from_id
                    AND b.doc->>'type' = 'USER')))""", current_org_id(), record_id, user_key, transaction=transaction)
        allowed = _DELETE_ROLES.get(record.get("connectorName", ""), _DEFAULT_DELETE_ROLES)
        if not any(r["role"] in allowed for r in rows):
            return {"success": False, "code": 403, "reason": "User lacks permission to delete this record"}
        return None

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
        """Main entry point for record deletion. KB records require OWNER or WRITER."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            # Get record to determine connector type
            record = await self.get_document(record_id, CollectionNames.RECORDS.value, transaction)
            # A record in another org is reported exactly like a missing one so IDs can't be probed.
            if not org_id or not record or record.get(_ORG_FIELD) != org_id:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record not found: {record_id}"
                }

            connector_name = record.get("connectorName", "")
            origin = record.get("origin", "")

            # KB records: require OWNER or WRITER (retired roles read as READER)
            is_kb_record = (
                origin == OriginTypes.UPLOAD.value
                or connector_name == Connectors.KNOWLEDGE_BASE.value
            )
            if is_kb_record:
                kb_context = await self._get_kb_context_for_record(record_id, transaction)
                if not kb_context:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Knowledge base context not found for record {record_id}",
                    }
                user = await self.get_user_by_user_id(user_id)
                if not user:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"User not found: {user_id}",
                    }
                user_key = user.get("id") or user.get("_key")
                user_role = await self.get_user_kb_permission(
                    kb_context.get("kb_id"), user_key, transaction
                )
                if user_role not in ["OWNER", "WRITER"]:
                    return {
                        "success": False,
                        "code": 403,
                        "reason": "User lacks permission to delete records",
                    }
                # A folder goes with its contents; deleting only the folder would
                # strand them without a hierarchy parent.
                if soft_delete:
                    result = await self.soft_delete_records(
                        [record_id],
                        record.get("connectorId") or "",
                        delete_source=DeleteSource(delete_source).value,
                        batch_id=str(uuid.uuid4()),
                        deleted_by_user_id=user_key if DeleteSource(delete_source) is DeleteSource.USER else None,
                        transaction=transaction,
                    )
                    return soft_delete_request_result(record_id, record, result)
                cascade = await self.delete_records_recursive(
                    [record_id], record.get("connectorId"), transaction=transaction,
                )
                if not cascade.get("success") or not cascade.get("successfully_deleted"):
                    return {
                        "success": False,
                        "code": cascade.get("code", 500),
                        "reason": cascade.get("reason") or f"Record {record_id} could not be deleted",
                    }
                return {
                    "success": True,
                    "record_id": record_id,
                    "message": "Record deleted successfully",
                    "eventData": cascade.get("eventData"),
                    "connectorId": record.get("connectorId"),
                    "orgId": record.get(_ORG_FIELD),
                    "isKb": True,
                }

            # A user's request needs a deleting role; a connector's own delete (a sync removing what the source removed) is not a user's.
            if DeleteSource(delete_source) is DeleteSource.USER and (refusal := await self._refuse_without_delete_role(record_id, record, user_id, transaction)):
                return refusal

            if soft_delete:
                deleted_by = None
                if DeleteSource(delete_source) is DeleteSource.USER:
                    deleter = await self.get_user_by_user_id(user_id) or {}
                    deleted_by = deleter.get("id") or deleter.get("_key")
                # What the hard delete removes: the record and its direct attachments, as on
                # ArangoDB. One statement marks them all with one batch, so a restore of the
                # mail brings its attachments back with it.
                trash_ids = [record_id]
                if is_live_record(record):
                    trash_ids += await self._direct_attachment_ids(record_id, org_id, transaction)
                result = await self.soft_delete_records(
                    trash_ids,
                    record.get("connectorId") or "",
                    delete_source=DeleteSource(delete_source).value,
                    batch_id=str(uuid.uuid4()),
                    deleted_by_user_id=deleted_by,
                    follow=(),
                    transaction=transaction,
                )
                return soft_delete_request_result(record_id, record, result)

            # Get file record for event publishing before deletion
            file_record = None
            try:
                file_record = await self.get_document(record_id, CollectionNames.FILES.value, transaction)
            except Exception as e:
                self.logger.debug(f"File record not found for record {record_id}: {e}")

            # A mail's attachments go with it, as on ArangoDB: left behind, they stay
            # searchable with nothing left to clean up their vectors.
            attachment_ids, attachment_payloads = await self._attachments_to_delete(
                record_id, org_id, transaction
            )
            taxonomy_refs = await deleted_record_taxonomy_refs(self, [record], self.logger)

            # Create event payload for router to publish
            event_data = None
            try:
                payload = await self._create_deleted_record_event_payload(record, file_record)
                if payload:
                    connector_name = record.get("connectorName", "")
                    origin = record.get("origin", "")

                    # Set connector and origin info
                    if connector_name:
                        payload["connectorName"] = connector_name
                    if origin:
                        payload["origin"] = origin
                    if refs := taxonomy_refs.get(record_id):
                        payload["taxonomyRefs"] = refs

                    event_data = {
                        "eventType": "deleteRecord",
                        "topic": "record-events",
                        "payload": payload,
                        "payloads": [payload, *attachment_payloads],
                    }
            except Exception as e:
                self.logger.error(f"❌ Failed to create deletion event payload: {str(e)}")
                event_data = None

            # The record and its blocks (joined by field, not by an edge, so the sweep misses them) go together or not at all.
            txn = transaction or await self.begin_transaction([], [])
            try:
                await self._delete_records_with_their_types([*attachment_ids, record_id], txn)
                await self.delete_blocks_for_records([*attachment_ids, record_id], transaction=txn)
            except BaseException:
                if txn != transaction:
                    await self.rollback_transaction(txn)
                raise
            if txn != transaction:
                await self.commit_transaction(txn)
            # Their children are left without a parent: the connector's scopes no longer describe its tree.
            if record.get("connectorId"):
                try:
                    await self.kh_scope_mark_changed(record["connectorId"])
                except Exception as e:
                    self.logger.warning(f"kh scope: could not mark {record['connectorId']} changed after a delete: {e}")

            return {
                "success": True,
                "record_id": record_id,
                "message": "Record deleted successfully",
                "attachments_deleted": len(attachment_ids),
                "eventData": event_data,
                # Lets the caller invalidate the right cache entry without
                # re-reading the record it just deleted.
                "connectorId": record.get("connectorId"),
                "orgId": record.get(_ORG_FIELD),
                "isKb": is_kb_record,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete record {record_id}: {e}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Record deletion failed: {e}"
            }

    async def delete_record_by_external_id(
        self,
        connector_id: str,
        external_id: str,
        user_id: str,
        transaction: str | None = None,
        *,
        soft_delete: bool = False,
    ) -> dict | None:
        """Delete a record by external ID; ``soft_delete`` trashes, as a CONNECTOR delete, what the hard delete removes."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            record = await self.get_record_by_external_id(
                connector_id,
                external_id,
                transaction,
                visibility=RecordVisibility.LIVE if soft_delete else RecordVisibility.ALL,
            )
            if not record:
                self.logger.warning(f"⚠️ Record {external_id} not found for connector {connector_id}")
                return None

            if soft_delete:
                return await self.delete_record(
                    record.id, user_id, record.org_id, transaction,
                    soft_delete=True, delete_source=DeleteSource.CONNECTOR,
                )
            result = await self.delete_record(record.id, user_id, record.org_id, transaction, delete_source=DeleteSource.CONNECTOR)
            # Raised, as on ArangoDB, so the caller's transaction rolls back.
            if not result.get("success"):
                raise Exception(f"Deletion failed: {result.get('reason', 'Unknown error')}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Delete record by external ID failed: {str(e)}")
            raise

    async def unlink_nodes_missed_by_full_sync(self, connector_id: str, seen: Mapping[str, Collection[str]], *, window_start_ms: int | None = None,
                                               windowed_group_types: Collection[str] = (), transaction: str | None = None) -> tuple[int, bool]:
        """Remove the edges of the nodes of ``connector_id`` that a full sync did not see (the nodes stay); outside its window only the grants.
        Answers ``(edges removed, ok)``; on ``False`` inside a caller's transaction the statement may have aborted it, so the caller must roll back."""
        current_org_id()      # resolved before the try: a missing org raises, it is not a failed delete
        try:
            collected = await self._collect_connector_entities(connector_id, transaction)
            unseen = unseen_node_keys(collected, seen)
            record_fields: dict[str, dict] = {}
            group_fields: dict[str, dict] = {}
            if window_start_ms is not None:
                record_fields = await self._node_fields_by_id(CollectionNames.RECORDS.value, unseen[CollectionNames.RECORDS.value],
                                                              ("sourceCreatedAtTimestamp", "recordGroupId"), transaction)
                group_fields = await self._node_fields_by_id(CollectionNames.RECORD_GROUPS.value, unseen[CollectionNames.RECORD_GROUPS.value],
                                                             ("sourceCreatedAtTimestamp", "groupType"), transaction)
            unlink, outside = split_unseen_by_window(unseen, record_fields, group_fields, window_start_ms, windowed_group_types)
            unlinked, failed = await self._delete_all_edges_for_nodes(transaction or "", unlink, list(FULL_SYNC_EDGE_COLLECTIONS))
            # Outside the window the source was not read: keep the structure, drop the grants.
            ungranted, failed_grants = await self._delete_all_edges_for_nodes(transaction or "", outside, [CollectionNames.PERMISSION.value])
            if failed or failed_grants:
                self.logger.warning(f"Full sync of {connector_id}: could not remove the links of the {len(unlink)} node(s) it missed from {failed or []}, "
                                    f"or the grants of the {len(outside)} outside its window from {failed_grants or []}")
            return (unlinked + ungranted, not failed and not failed_grants)
        except Exception as e:
            self.logger.error(f"Error unlinking nodes missed by the full sync of {connector_id}: {e}", exc_info=True)
            return (0, False)

    async def _collect_connector_entities(self, connector_id: str, transaction: str | None = None, *, include_virtual_record_ids: bool = False) -> dict:
        """The ids of a connector's records, record groups, roles and groups."""
        rows = await self._drows("""/* pipeshub:graph.records_collect_connector_entities */ SELECT coll, array_agg(id) AS ids,
              CASE WHEN coll = 'records' THEN array_agg(DISTINCT doc->>'virtualRecordId') FILTER (WHERE doc->>'virtualRecordId' IS NOT NULL) END AS vrids
            FROM graph.nodes WHERE org_id = $1 AND coll IN ('records', 'recordGroups', 'roles', 'groups') AND doc->>'connectorId' = $2 GROUP BY coll""",
                                current_org_id(), connector_id, transaction=transaction)
        by = {r["coll"]: r for r in rows}
        ids = {c: list(by[c]["ids"]) if c in by else [] for c in ("records", "recordGroups", "roles", "groups")}
        return {"record_keys": ids["records"], "record_ids": ids["records"],
                "virtual_record_ids": list(by["records"]["vrids"] or []) if include_virtual_record_ids and "records" in by else [],
                "record_group_keys": ids["recordGroups"], "role_keys": ids["roles"], "group_keys": ids["groups"],
                "all_node_ids": [f"records/{i}" for i in ids["records"]] + [f"recordGroups/{i}" for i in ids["recordGroups"]]
                                + [f"roles/{i}" for i in ids["roles"]] + [f"groups/{i}" for i in ids["groups"]] + [f"apps/{connector_id}"]}

    async def _node_fields_by_id(self, collection: str, keys: list[str], fields: tuple[str, ...], transaction: str | None = None) -> dict[str, dict]:
        if not keys:
            return {}
        rows = await self._drows("/* pipeshub:graph.records_node_fields_by_id */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])",
                                current_org_id(), collection, list(keys), transaction=transaction)
        return {r["id"]: {f: _doc(r["doc"]).get(f) for f in fields} for r in rows}

    async def _delete_all_edges_for_nodes(self, transaction: str, node_ids: list[str], edge_collections: list[str]) -> tuple[int, list[str]]:
        """Delete the edges of the given collections around ``'<coll>/<key>'`` nodes: all or none. (count, collections that failed)."""
        org = current_org_id()
        if not node_ids or not edge_collections:
            return (0, [])
        try:
            n = await self._q(lambda tx: _sweep_edges(tx, org, list(edge_collections), node_ids), transaction or None, write=True)
            return (n, [])
        except Exception as e:
            self.logger.error(f"❌ Failed to delete relationships: {e}")
            return (0, list(edge_collections))


    # ---- deleting a connector instance (or a collection) with everything it holds ---------------------------------------------------
    async def delete_connector_instance(self, connector_id: str, org_id: str, transaction: str | None = None) -> dict[str, Any]:
        """Deletes the connector (or collection) and everything of it: its records with their type documents and blocks, record groups, roles, user groups, sync
        points, stage states, the agent knowledge that names it, its authenticated-as links, the taxonomy nodes only its records linked, and the app with every
        edge around it. Nothing of another connector or org is touched. The answer names what the vector cleanup needs (virtual record ids only when the app
        cannot be cleaned by membership; the record group ids).

        In a caller's transaction it is all or nothing (a failure rolls back to a savepoint). Without one it runs in batches, each its own transaction, so a connector
        of millions of records holds no lock for long and no statement outlasts the limit: the grants on the app go first (nobody can open what is left), the app goes
        last, and a failure answers ``success: False`` with the connector still there for a retry to finish. The ids the cleanup needs are stashed in the transaction
        that removes what they name, so the retry's answer names everything the connector had."""
        org = current_org_id()
        if org_id != org:
            return {"success": False, "error": f"Connector instance {connector_id} not found"}
        try:
            if transaction is None:
                result = await _delete_in_batches(self._q, org, connector_id)
                if result.get("partial"):
                    self.logger.error("Connector instance %s is only partly deleted: %s", connector_id, result["error"])
            else:
                result = await self._q(lambda tx: _delete_connector_atomically(tx, org, connector_id, savepoint=True), transaction, write=True)
        except Exception as e:
            self.logger.error("Failed to delete connector instance %s: %s", connector_id, e, exc_info=True)
            return {"success": False, "error": f"Failed to delete connector instance: {e}"}
        if result.get("success"):
            try:
                await self.kh_scope_forget(connector_id)
            except Exception as e:
                self.logger.warning("kh scope: could not drop the scopes of deleted connector %s: %s", connector_id, e)
        return result

    async def sweep_deleted_connector_writes(self, connector_id: str, record_group_ids: Sequence[str] = ()) -> tuple[int, int]:
        """See the interface. The code-link writer checks that both ends exist but does not lock them, and no foreign key ties an edge to its
        nodes, so a link whose statement still saw the block can commit after this pass removed the block and its edges: the links of
        ``record_group_ids`` are removed and counted too. Each batch commits on its own: the pass runs after the delete, outside any transaction."""
        org = current_org_id()
        groups = list(dict.fromkeys(g for g in record_group_ids if g))
        removed = 0
        for coll in _LATE_WRITE_COLLS:
            while True:
                found, n = await self._q(lambda tx, coll=coll: _remove_named_batch(tx, org, coll, connector_id), None, write=True)
                removed += n
                if found < _CHUNK:
                    break
        while groups:
            n = await self._q(lambda tx: _remove_group_links_batch(tx, org, groups), None, write=True)
            removed += n
            if n < _CHUNK:
                break

        async def count(tx: Tx) -> int:
            nodes = await tx.fetchval("""/* pipeshub:graph.late_write_left */ SELECT count(*) FROM graph.nodes
                WHERE org_id = $1 AND coll = ANY($2::text[]) AND doc->>'connectorId' = $3""", org, list(_LATE_WRITE_COLLS), connector_id)
            links = await tx.fetchval("""/* pipeshub:graph.late_write_links_left */ SELECT count(*) FROM graph.edges
                WHERE org_id = $1 AND coll = ANY($2::text[]) AND doc->>'recordGroupId' = ANY($3::text[])""", org, list(_LATE_LINK_COLLS), groups) if groups else 0
            return int(nodes or 0) + int(links or 0)
        return removed, await self._q(count, None)
