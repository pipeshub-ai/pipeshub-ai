"""Record groups, the record hierarchy and the delete of a record's subtree.

A record's hierarchy edges are ``nodeRelations`` rows (``PARENT_CHILD``, ``ATTACHMENT``) with no key; the groups a record belongs to are ``belongsTo``
edges. A walk is a recursive CTE that deduplicates by node, so a cycle ends. A subtree delete walks a level at a time with array lookups and removes
the whole set in one transaction. Every method resolves the ambient org before its ``try``; every statement is a literal with bound parameters.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import EDGE_COLLECTION_TO_RELATIONSHIP
from app.core.org_context import current_org_id
from app.models.entities import RecordGroup
from app.services.graph_db.common.utils import (
    CANONICAL_PARENT_RELATION_TYPES,
    PATH_MAX_CANDIDATES,
    deleted_record_taxonomy_refs,
    select_canonical_chain_names,
)
from app.services.graph_db.postgres.base import (
    PgBase,
    _arango,
    _doc,
    _edge,
    _edge_scope,
    _ekey,
    _j,
)
from app.services.graph_db.postgres.records import _TYPE_COLLS
from app.services.graph_db.taxonomy import RECORD_ENRICHMENT_EDGE_COLLECTIONS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_ORG_FIELD = "orgId"  # the org property of a graph document
_EDGE_COLLS = sorted({*EDGE_COLLECTION_TO_RELATIONSHIP, CollectionNames.RECORD_LINKS.value})
_NODE_COLLS = ["records", "blocks", *_TYPE_COLLS]


async def _remove_records(tx: Tx, org: str, ids: list[str]) -> int:
    """Remove records with their type documents, blocks and every edge touching any of them; returns the records removed."""
    if not ids:
        return 0
    # The records are locked before anything attached to them is read: a writer that is adding a block or a type document holds its record's row until it
    # commits, so the reads below (statements of their own, with fresh snapshots) see what it wrote instead of leaving it without an owner.
    await tx.execute("""/* pipeshub:graph.records_remove_lock_records */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])
        ORDER BY id FOR UPDATE""", org, ids)
    extra = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.records_remove_blocks */ SELECT id FROM graph.nodes
        WHERE org_id = $1 AND coll = 'blocks' AND doc->>'recordId' = ANY($2::text[])""", org, ids)]
    linked = [r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.records_remove_types */ SELECT e.to_id FROM graph.edges e
        WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id = ANY($2::text[]) AND e.to_coll = ANY($3::text[])""", org, ids, _TYPE_COLLS)]
    everything = sorted({*ids, *extra, *linked})
    # Every row the deletes touch is locked first, nodes then edges, each in key order, so two overlapping deletes wait for each other
    # instead of deadlocking (a delete statement takes its row locks in plan order).
    await tx.execute("""/* pipeshub:graph.records_remove_lock_nodes */ SELECT 1 FROM graph.nodes WHERE org_id = $1
        AND ((coll = 'records' AND id = ANY($2::text[])) OR (coll = ANY($3::text[]) AND id = ANY($4::text[])) OR (coll = 'blocks' AND id = ANY($5::text[])))
        ORDER BY coll, id FOR UPDATE""", org, ids, _TYPE_COLLS, everything, extra)
    await tx.execute("""/* pipeshub:graph.records_remove_lock_edges */ SELECT 1 FROM graph.edges WHERE org_id = $1 AND coll = ANY($2::text[])
        AND ((from_id = ANY($3::text[]) AND from_coll = ANY($4::text[])) OR (to_id = ANY($3::text[]) AND to_coll = ANY($4::text[])))
        ORDER BY coll, from_coll, from_id, to_coll, to_id, ekey FOR UPDATE""", org, _EDGE_COLLS, everything, _NODE_COLLS)
    await tx.execute("""/* pipeshub:graph.records_remove_edges_from */ DELETE FROM graph.edges
        WHERE org_id = $1 AND coll = ANY($2::text[]) AND from_id = ANY($3::text[]) AND from_coll = ANY($4::text[])""", org, _EDGE_COLLS, everything, _NODE_COLLS)
    await tx.execute("""/* pipeshub:graph.records_remove_edges_to */ DELETE FROM graph.edges
        WHERE org_id = $1 AND coll = ANY($2::text[]) AND to_id = ANY($3::text[]) AND to_coll = ANY($4::text[])""", org, _EDGE_COLLS, everything, _NODE_COLLS)
    return await tx.fetchval("""/* pipeshub:graph.records_remove_nodes */ WITH d AS (DELETE FROM graph.nodes WHERE org_id = $1 AND
        ((coll = 'records' AND id = ANY($2::text[])) OR (coll = ANY($3::text[]) AND id = ANY($4::text[])) OR (coll = 'blocks' AND id = ANY($5::text[])))
        RETURNING coll) SELECT count(*) FILTER (WHERE coll = 'records') FROM d""", org, ids, _TYPE_COLLS, everything, extra)


class HierarchyMixin(PgBase):
    async def _rows(self, sql: str, *args: Any, transaction: str | None = None) -> list:  # noqa: ANN401
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction)

    async def create_edges_if_absent(self, edges: list[dict], collection: str, transaction: str | None = None) -> None:
        """Create the edges that are not there (both ends must exist); an existing edge keeps its properties."""
        if not edges:
            return
        org = current_org_id()
        rows: dict[tuple, dict] = {}
        for e in edges:
            fc, fi, tc, ti, props = _edge(e)
            key = _ekey(collection, props)
            rows.setdefault((fc, fi, tc, ti, key), {"fc": fc, "fi": fi, "tc": tc, "ti": ti, "k": key, "doc": props})      # the first of a duplicate wins, as MERGE ... ON CREATE
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.create_edges_if_absent */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, $2, x.fc, x.fi, x.tc, x.ti, x.k, jsonb_strip_nulls(x.doc)
            FROM jsonb_to_recordset($3::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
            WHERE EXISTS (SELECT 1 FROM graph.nodes f WHERE f.org_id = $1 AND f.coll = x.fc AND f.id = x.fi)
              AND EXISTS (SELECT 1 FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = x.tc AND t.id = x.ti)
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING""", org, collection, _j(list(rows.values()))), transaction, write=True)

    async def copy_document_relationships(self, source_key: str, target_key: str, transaction: str | None = None) -> bool:
        """Copy a record's taxonomy edges (departments, categories, languages, topics) to another record; an edge the target has stays."""
        org = current_org_id()
        try:
            if not await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.copy_document_relationships_target */ SELECT EXISTS (SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = $2)", org, target_key), transaction):
                return False        # as the Neo4j provider, which matches both ends: a missing target gets nothing
            colls = [c for c in RECORD_ENRICHMENT_EDGE_COLLECTIONS if c != CollectionNames.MENTIONS_ENTITY.value]
            await self._q(lambda tx: tx.execute("""/* pipeshub:graph.copy_document_relationships */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT s.org_id, s.coll, 'records', $3, s.to_coll, s.to_id, s.ekey,
                       jsonb_strip_nulls(jsonb_build_object('createdAtTimestamp', coalesce(s.doc->'createdAtTimestamp', to_jsonb($4::bigint)),
                                                            'extractedName', s.doc->'extractedName', 'extractedNames', s.doc->'extractedNames'))
                FROM graph.edges s WHERE s.org_id = $1 AND s.coll = ANY($2::text[]) AND s.from_coll = 'records' AND s.from_id = $5
                  AND EXISTS (SELECT 1 FROM graph.nodes d WHERE d.org_id = $1 AND d.coll = 'records' AND d.id = $3)
                  AND NOT EXISTS (SELECT 1 FROM graph.edges t WHERE t.org_id = s.org_id AND t.coll = s.coll AND t.from_id = $3 AND t.to_id = s.to_id)
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING""", org, colls, target_key, get_epoch_timestamp_in_ms(), source_key),
                           transaction, write=True)
            return True
        except Exception as e:
            self.logger.error(f"❌ Error copying relationships from {source_key} to {target_key}: {e}")
            return False

    async def delete_edges_between_collections(self, from_id: str, from_collection: str, edge_collection: str, to_collection: str,
                                               transaction: str | None = None) -> int:
        """``nodeRelations`` is the hierarchy edge, ``recordLinks`` the links (stored in either collection), as the relationship types of the Neo4j provider."""
        org = current_org_id()
        colls, kind = _edge_scope(edge_collection, pair=True)
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_between_collections */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4 AND e.to_coll = $5 AND ($6::int = 0 OR ($6::int = 1 AND e.ekey = '') OR ($6::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) RETURNING 1) SELECT count(*) FROM d""",
                                                  org, colls, from_collection, from_id, to_collection, kind), transaction, write=True)

    async def delete_edges_by_relationship_types(self, from_id: str, from_collection: str, collection: str, relationship_types: list[str],
                                                 transaction: str | None = None) -> int:
        """``nodeRelations`` is the hierarchy and the links, ``recordLinks`` the links only (Neo4j's ``NODE_RELATION|RECORD_LINK`` and ``RECORD_LINK``)."""
        org = current_org_id()
        colls, kind = _edge_scope(collection)
        try:
            return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_by_relationship_types */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
                AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4 AND e.doc->>'relationshipType' = ANY($5::text[]) AND ($6::int = 0 OR ($6::int = 1 AND e.ekey = '') OR ($6::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) RETURNING 1)
                SELECT count(*) FROM d""", org, colls, from_collection, from_id, relationship_types, kind), transaction, write=True)
        except Exception as e:
            self.logger.error(f"❌ Delete edges by relationship types failed: {e}")
            return 0

    async def delete_parent_child_edge_to_record(self, record_id: str, transaction: str | None = None) -> bool:
        """Delete the hierarchy edge from a record's parent *record* (a record group's own edge to it stays)."""
        org = current_org_id()
        n = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_parent_child_edge_to_record */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.to_coll = 'records' AND e.to_id = $2 RETURNING 1) SELECT count(*) FROM d""",
                                                 org, record_id), transaction, write=True)
        return bool(n)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None) -> RecordGroup | None:
        d = await self._one("""/* pipeshub:graph.get_record_group_by_external_id */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'recordGroups'
            AND doc->>'connectorId' = $2 AND doc->>'externalGroupId' = $3 LIMIT 1""", current_org_id(), connector_id, external_id, transaction=transaction)
        return RecordGroup.from_arango_base_record_group(_arango(d, "recordGroups")) if d else None

    async def batch_upsert_record_groups(
        self,
        record_groups: list[RecordGroup],
        transaction: str | None = None
    ) -> None:
        """Batch upsert record groups"""
        current_org_id()    # resolved before the try: a missing org raises, it is not a failed read
        try:
            nodes = [rg.to_arango_base_record_group() for rg in record_groups]
            await self.batch_upsert_nodes(
                nodes,
                collection=CollectionNames.RECORD_GROUPS.value,
                transaction=transaction
            )
        except Exception as e:
            self.logger.error(f"❌ Batch upsert record groups failed: {str(e)}")
            raise

    async def create_record_group_relation(
        self,
        record_id: str,
        record_group_id: str,
        transaction: str | None = None
    ) -> None:
        """Create BELONGS_TO edge from record to record group"""
        edge = {
            "from_id": record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": record_group_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.BELONGS_TO.value,
            transaction=transaction
        )

    async def create_record_groups_relation(
        self,
        child_id: str,
        parent_id: str,
        transaction: str | None = None
    ) -> None:
        """Create BELONGS_TO edge from child record group to parent record group"""
        edge = {
            "from_id": child_id,
            "from_collection": CollectionNames.RECORD_GROUPS.value,
            "to_id": parent_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }

        await self.batch_create_edges(
            [edge],
            collection=CollectionNames.BELONGS_TO.value,
            transaction=transaction
        )

    async def get_record_group_by_id(
        self,
        record_group_id: str,
        transaction: str | None = None
    ) -> dict | None:
        """Get record group by ID"""
        return await self.get_document(record_group_id, CollectionNames.RECORD_GROUPS.value, transaction)

    async def _ancestor_chains(self, record_id: str, transaction: str | None) -> list[dict]:
        """Every canonical ancestor chain of a record (a child's ``externalParentId`` names its parent's external id or id), as the
        ``{ids, names}`` rows ``select_canonical_chain_names`` picks from; start first, root last."""
        rows = await self._rows("""/* pipeshub:graph.record_ancestor_chains */ WITH RECURSIVE up(ids, names, cur, ext) AS (
              SELECT ARRAY[r.id], ARRAY[r.doc->>'recordName'], r.id, r.doc->>'externalParentId' FROM graph.nodes r
              WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2
              UNION ALL
              SELECT up.ids || p.id, up.names || (p.doc->>'recordName'), p.id, p.doc->>'externalParentId' FROM up
              JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_coll = 'records' AND e.to_id = up.cur AND e.from_coll = 'records'
              JOIN graph.nodes p ON p.org_id = $1 AND p.coll = 'records' AND p.id = e.from_id
              WHERE up.ext IN (p.doc->>'externalRecordId', p.id) AND NOT p.id = ANY(up.ids) AND cardinality(up.ids) <= 100)
            SELECT ids, names FROM up ORDER BY cardinality(ids) DESC, ids LIMIT $3""", current_org_id(), record_id, PATH_MAX_CANDIDATES, transaction=transaction)
        return [{"ids": list(r["ids"]), "names": list(r["names"])} for r in rows]

    async def get_record_path(self, record_id: str, transaction: str | None = None) -> str | None:
        current_org_id()    # resolved before the try: a missing org raises, it is not a failed read
        try:
            rows = await self._ancestor_chains(record_id, transaction)
            return "/".join(select_canonical_chain_names(rows, ("names",))) if rows else None
        except Exception as e:
            self.logger.error(f"❌ Get record path failed: {e}")
            return None

    async def get_record_path_segments(self, record_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[str]:
        current_org_id()    # resolved before the try: a missing org raises, it is not a failed read
        try:
            rows = await self._ancestor_chains(record_id, transaction)
        except Exception as e:
            self.logger.error(f"❌ Get record path segments failed: {e}")
            if raise_on_error:
                raise
            return []
        return select_canonical_chain_names(rows, ("names",))

    async def get_record_group_virtual_record_ids(self, record_group_id: str, transaction: str | None = None) -> list[str]:
        """The distinct ``virtualRecordId`` of every record in the group or in a record group nested beneath it. Raises on a query failure: an empty list
        means "holds no content", which a storage move acts on."""
        rows = await self._rows("""/* pipeshub:graph.get_record_group_virtual_record_ids */ WITH RECURSIVE sub(id, depth) AS (SELECT $2::text, 0
              UNION SELECT e.from_id, s.depth + 1 FROM sub s JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups'
                AND e.to_coll = 'recordGroups' AND e.to_id = s.id WHERE s.depth < 50)
            SELECT DISTINCT r.doc->>'virtualRecordId' AS v FROM sub JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records'
              AND b.to_coll = 'recordGroups' AND b.to_id = sub.id
            JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = b.from_id WHERE r.doc->>'virtualRecordId' IS NOT NULL""",
                                current_org_id(), record_group_id, transaction=transaction)
        return [r["v"] for r in rows]

    async def get_record_group_path(self, record_group_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[str]:
        org = current_org_id()
        try:
            rows = await self._rows("""/* pipeshub:graph.record_group_path */ WITH RECURSIVE up(ids, gn, nm, cur) AS (
                  SELECT ARRAY[g.id], ARRAY[g.doc->>'groupName'], ARRAY[g.doc->>'name'], g.id FROM graph.nodes g
                  WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.id = $2
                  UNION ALL
                  SELECT up.ids || p.id, up.gn || (p.doc->>'groupName'), up.nm || (p.doc->>'name'), p.id FROM up
                  JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'recordGroups' AND e.from_id = up.cur AND e.to_coll = 'recordGroups'
                  JOIN graph.nodes p ON p.org_id = $1 AND p.coll = 'recordGroups' AND p.id = e.to_id
                  WHERE NOT p.id = ANY(up.ids) AND cardinality(up.ids) <= 50)
                SELECT ids, gn, nm FROM up ORDER BY cardinality(ids) DESC, ids LIMIT $3""", org, record_group_id, PATH_MAX_CANDIDATES, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Get record group path failed: {e}")
            if raise_on_error:
                raise
            return []
        return select_canonical_chain_names([{"ids": list(r["ids"]), "groupNames": list(r["gn"]), "names": list(r["nm"])} for r in rows], ("groupNames", "names"))

    async def get_descendant_virtual_record_ids(self, record_id: str, transaction: str | None = None) -> list[str]:
        """Virtual record ids of the canonical descendants (children whose ``externalParentId`` names their parent) of a record."""
        rows = await self._rows("""/* pipeshub:graph.get_descendant_virtual_record_ids */ WITH RECURSIVE down(id) AS (
              SELECT $2::text
              UNION
              SELECT c.id FROM down JOIN graph.nodes p ON p.org_id = $1 AND p.coll = 'records' AND p.id = down.id
              JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = p.id AND e.to_coll = 'records'
                AND e.doc->>'relationshipType' = ANY($3::text[])
              JOIN graph.nodes c ON c.org_id = $1 AND c.coll = 'records' AND c.id = e.to_id AND c.doc->>'externalParentId' IN (p.doc->>'externalRecordId', p.id))
            SELECT DISTINCT d.doc->>'virtualRecordId' AS v FROM down JOIN graph.nodes d ON d.org_id = $1 AND d.coll = 'records' AND d.id = down.id
            WHERE d.id <> $2 AND d.doc->>'virtualRecordId' IS NOT NULL""", current_org_id(), record_id, list(CANONICAL_PARENT_RELATION_TYPES), transaction=transaction)
        return [r["v"] for r in rows]

    async def get_record_parent_info(self, record_id: str, transaction: str | None = None) -> dict | None:
        """``{id, type}`` of a record's hierarchy parent (``"record"`` or ``"recordGroup"``; a record over a group), None at the root."""
        org = current_org_id()
        try:
            rows = await self._rows("""/* pipeshub:graph.get_record_parent_info */ SELECT e.from_id AS id, e.from_coll AS coll FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.to_coll = 'records' AND e.to_id = $2 AND e.from_coll IN ('records', 'recordGroups')
                ORDER BY (e.from_coll = 'records') DESC, e.from_id LIMIT 1""", org, record_id, transaction=transaction)
            return {"id": rows[0]["id"], "type": "recordGroup" if rows[0]["coll"] == "recordGroups" else "record"} if rows else None
        except Exception as e:
            self.logger.error(f"❌ Get record parent info failed: {e}")
            return None

    async def get_node_depths_batch(self, parent_id: str, node_ids: list[str], max_depth: int = 3, parent_type: str | None = None) -> dict[str, int]:
        """The shallowest level of each node under a parent (app, record group or record): level 1 is a direct member."""
        org = current_org_id()
        if not node_ids:
            return {}
        safe = max(1, min(int(max_depth), 10))
        rec_depth = max(1, safe - 1)
        try:
            if parent_type == "app":
                rows = await self._rows("""/* pipeshub:graph.get_node_depths_batch_app */ WITH RECURSIVE w(id, lvl) AS (
                      SELECT s.id, 1 FROM (SELECT b.from_id AS id FROM graph.nodes g JOIN graph.edges b ON b.org_id = g.org_id AND b.coll = 'belongsTo'
                          AND b.to_coll = 'recordGroups' AND b.to_id = g.id AND b.from_coll = 'records'
                        WHERE g.org_id = $1 AND g.coll = 'recordGroups' AND g.doc->>'connectorId' = $2 AND NOT coalesce((g.doc->>'isDeleted')::boolean, false)
                        UNION SELECT b.from_id FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.to_coll = 'apps' AND b.to_id = $2
                          AND b.from_coll = 'records') s
                      UNION SELECT nx.to_id, w.lvl + 1 FROM w JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
                          AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.from_id = w.id AND e.to_coll = 'records') nx ON true
                      WHERE w.lvl <= $4)
                    SELECT id, min(lvl) AS lvl FROM w WHERE id = ANY($3::text[]) GROUP BY id""", org, parent_id, list(node_ids), rec_depth)
            elif parent_type == "recordGroup":
                rows = await self._rows("""/* pipeshub:graph.get_node_depths_batch_group */ WITH RECURSIVE w(id, lvl) AS (
                      SELECT b.from_id, 1 FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.to_coll = 'recordGroups' AND b.to_id = $2
                        AND b.from_coll = 'records'
                      UNION SELECT nx.to_id, w.lvl + 1 FROM w JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
                          AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.from_id = w.id AND e.to_coll = 'records') nx ON true
                      WHERE w.lvl <= $4)
                    SELECT id, min(lvl) AS lvl FROM w WHERE id = ANY($3::text[]) GROUP BY id""", org, parent_id, list(node_ids), rec_depth)
            else:
                rows = await self._rows("""/* pipeshub:graph.get_node_depths_batch_record */ WITH RECURSIVE w(id, lvl) AS (SELECT $2::text, 0
                      UNION SELECT nx.to_id, w.lvl + 1 FROM w JOIN LATERAL (SELECT e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'nodeRelations'
                          AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.from_id = w.id AND e.to_coll = 'records') nx ON true
                      WHERE w.lvl < $4)
                    SELECT id, min(lvl) AS lvl FROM w WHERE id = ANY($3::text[]) AND lvl >= 1 GROUP BY id""", org, parent_id, list(node_ids), safe)
            return {r["id"]: int(r["lvl"]) for r in rows}
        except Exception as e:
            self.logger.error("get_node_depths_batch failed: %s", e)
            return {}

    async def delete_records_and_relations(self, record_key: str, *, hard_delete: bool = False, transaction: str | None = None) -> bool:
        """Delete a record and all its relations. False when there was no such record. A raw primitive: children are not re-pointed."""
        org = current_org_id()
        return bool(await self._q(lambda tx: _remove_records(tx, org, [record_key]), transaction, write=True))

    async def _walk_subtree(self, tx: Tx, ids: list[str], connector_id: str | None, follow: list[str], folder_id: str | None, *, include_trashed: bool,
                                live_only: bool) -> tuple[list[str], dict[str, dict], dict[str, dict]]:
        """The roots and the records (with type documents) of the containment subtrees, inside the caller's transaction."""
        org = current_org_id()
        roots = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.records_delete_roots */ WITH RECURSIVE
            below(id) AS (
              SELECT e.to_id FROM graph.edges e WHERE $4::text IS NOT NULL AND e.org_id = $1 AND e.coll = 'nodeRelations'
                AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT') AND e.from_coll = 'records' AND e.from_id = $4 AND e.to_coll = 'records'
              UNION
              SELECT e.to_id FROM below b JOIN graph.edges e ON e.org_id = $1 AND e.coll = 'nodeRelations' AND e.doc->>'relationshipType' IN ('PARENT_CHILD', 'ATTACHMENT')
                AND e.from_coll = 'records' AND e.from_id = b.id AND e.to_coll = 'records')
            SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = ANY($2::text[])
              AND ($5 OR coalesce((n.doc->>'isDeleted')::boolean, false) = false) AND ($3::text IS NULL OR n.doc->>'connectorId' = $3)
              AND ($4::text IS NULL OR n.id IN (SELECT id FROM below))""", org, ids, connector_id, folder_id, include_trashed or not live_only)]
        seen = dict.fromkeys(roots)
        frontier = roots
        while frontier and len(seen) < 1_000_000:
            # single-table statements on arrays, no join: with no statistics yet the planner scans the inner table of a join once per outer row
            cand = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.records_delete_level */ SELECT DISTINCT e.to_id AS id FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = 'records' AND e.from_id = ANY($2::text[]) AND e.to_coll = 'records'
                  AND e.doc->>'relationshipType' = ANY($3::text[])""", org, frontier, follow)]
            cand = [i for i in cand if i not in seen]
            if connector_id is not None and cand:
                cand = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.records_delete_level_connector */ SELECT id FROM graph.nodes
                    WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'connectorId' = $3""", org, cand, connector_id)]
            frontier = cand
            seen.update(dict.fromkeys(frontier))
        keys = list(seen)
        docs = {r["id"]: _doc(r["doc"]) for r in await tx.fetch("/* pipeshub:graph.records_delete_docs */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])", org, keys)}
        links = await tx.fetch("""/* pipeshub:graph.records_delete_type_edges */ SELECT DISTINCT ON (e.from_id) e.from_id AS fid, e.to_coll AS tc, e.to_id AS tid
            FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'isOfType' AND e.from_id = ANY($2::text[]) ORDER BY e.from_id, e.to_id""", org, keys)
        type_docs: dict[tuple[str, str], dict] = {}
        for coll in {r["tc"] for r in links}:
            for r in await tx.fetch("/* pipeshub:graph.records_delete_type_docs */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])",
                                    org, coll, [x["tid"] for x in links if x["tc"] == coll]):
                type_docs[(coll, r["id"])] = _doc(r["doc"])
        types = {r["fid"]: type_docs[(r["tc"], r["tid"])] for r in links if (r["tc"], r["tid"]) in type_docs}
        return roots, docs, types

    async def _inventory(self, ids: list[str], connector_id: str | None, follow: list[str], folder_id: str | None, *, include_trashed: bool,
                         live_only: bool, transaction: str | None) -> tuple[list[str], list[tuple[dict, dict | None]]]:
        """The valid roots among ``ids`` and the records (with their type documents) of the containment subtrees under them.

        The subtree is walked a level at a time with array lookups (one statement per level, a handful for a real hierarchy), not as one recursive
        statement: on tables the planner has no statistics for (a first big sync) a recursive join with a per-row lookup was planned as nested scans
        and took 18-20 s for 10k records; a statement per level is the same on any statistics."""
        current_org_id()      # resolved first: a missing org raises
        async def go(tx: Tx) -> tuple[list[str], dict[str, dict], dict[str, dict]]:
            return await self._walk_subtree(tx, ids, connector_id, follow, folder_id, include_trashed=include_trashed, live_only=live_only)

        roots, docs, types = await self._q(go, transaction)
        return roots, [(docs[i], types.get(i)) for i in dict.fromkeys(docs)]

    async def _delete_events(self, recs: list[tuple[dict, dict | None]], refs: dict) -> dict | None:
        payloads = []
        try:
            for rec, typ in recs:
                if not rec.get("virtualRecordId"):
                    continue
                p = await self._create_deleted_record_event_payload(rec, typ or {})
                if p:
                    p["connectorName"], p["origin"] = rec.get("connectorName"), rec.get("origin")
                    if r := refs.get(rec.get("id")):
                        p["taxonomyRefs"] = r
                    payloads.append(p)
        except Exception as e:
            self.logger.error(f"❌ Failed to prepare deletion event payloads: {e}")
        return {"eventType": "deleteRecord", "topic": "record-events", "payloads": payloads} if payloads else None

    async def delete_records_recursive(self, record_ids: list[str], connector_id: str, transaction: str | None = None, cascade_children: bool = True,  # noqa: FBT001, FBT002
                                       within_folder_id: str | None = None, *, include_trashed_roots: bool = False) -> dict:
        """Delete records and their owned descendants, scoped by connector (PARENT_CHILD and ATTACHMENT; with ``cascade_children`` False only
        ATTACHMENT, and the survivors that pointed at a deleted root lose that ``externalParentId`` and are reported in ``reparented``)."""
        empty = {"success": True, "deleted_records": [], "failed_records": [], "total_requested": 0, "successfully_deleted": 0, "failed_count": 0, "eventData": None}
        if not record_ids:
            return empty
        org = current_org_id()
        txn = transaction or await self.begin_transaction(read=[], write=[])
        try:
            roots, recs = await self._inventory(record_ids, connector_id, ["PARENT_CHILD", "ATTACHMENT"] if cascade_children else ["ATTACHMENT"],
                                                within_folder_id, include_trashed=include_trashed_roots, live_only=True, transaction=txn)
            keys = [r["id"] for r, _ in recs]
            roots_set = set(roots)
            failed = [{"record_id": rid, "reason": "Validation failed"} for rid in record_ids if rid not in roots_set]
            reparented: list[dict] = []
            peids: list[str] = []
            if not cascade_children and roots:
                peids = list(dict.fromkeys(r.get("externalRecordId") for r, _ in recs if r["id"] in roots_set and r.get("externalRecordId")))
            if peids:
                rows = await self._rows("""/* pipeshub:graph.records_delete_survivors */ SELECT s.id AS record_id, s.doc->>'externalParentId' AS peid,
                      (SELECT g.id FROM graph.edges b JOIN graph.nodes g ON g.org_id = b.org_id AND g.coll = 'recordGroups' AND g.id = b.to_id
                        WHERE b.org_id = s.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = s.id
                        ORDER BY (g.id = s.doc->>'recordGroupId') DESC, g.id LIMIT 1) AS gid,
                      EXISTS (SELECT 1 FROM graph.edges i WHERE i.org_id = s.org_id AND i.coll = 'inheritPermissions' AND i.from_id = s.id AND i.to_coll = 'records'
                        AND i.to_id = ANY($4::text[])) AS inherits
                    FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = 'records' AND s.doc->>'connectorId' = $2 AND s.doc->>'externalParentId' = ANY($3::text[])
                      AND NOT s.id = ANY($4::text[])""", org, connector_id, peids, keys, transaction=txn)
                reparented = [{"record_id": r["record_id"], "record_group_id": r["gid"], "inherits": bool(r["inherits"]), "parent_external_id": r["peid"]}
                              for r in rows if r["gid"]]
            refs = await deleted_record_taxonomy_refs(self, [r for r, _ in recs], self.logger)

            async def go(tx: Tx) -> None:
                if reparented:
                    await tx.execute("""/* pipeshub:graph.records_clear_survivor_parents */ UPDATE graph.nodes SET doc = doc - 'externalParentId'
                        WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])""", org, [r["record_id"] for r in reparented])
                await _remove_records(tx, org, keys)
            await self._q(go, txn, write=True)
            if transaction is None:
                await self.commit_transaction(txn)
            gone = {r.get("externalRecordId") for r, _ in recs}
            reparented = [{k: v for k, v in r.items() if k != "parent_external_id"} for r in reparented if r.get("parent_external_id") in gone]
            return {"success": True, "deleted_records": [{"record_id": r["id"], "name": r.get("recordName", "Unknown")} for r, _ in recs],
                    "failed_records": failed, "total_requested": len(record_ids), "successfully_deleted": len(roots), "failed_count": len(failed),
                    "eventData": await self._delete_events(recs, refs), "reparented": reparented}
        except BaseException as e:   # a cancel too: the unit must be rolled back and its connection given up
            if transaction is None:
                await self.rollback_transaction(txn)
                if isinstance(e, Exception):
                    self.logger.error(f"❌ Failed to delete records recursively: {e}")
                    return {"success": False, "reason": str(e), "code": 500, "eventData": None}
            raise  # the caller owns the transaction: it needs the real error to roll back and retry

    async def delete_single_record(self, record_id: str, transaction: str | None = None) -> dict:
        """The cleanup of ``delete_records_recursive`` for one live record, with no walk."""
        if not record_id:
            return {"success": True, "deleted_records": [], "failed_records": [], "total_requested": 0, "successfully_deleted": 0, "failed_count": 0, "eventData": None}
        org = current_org_id()
        txn = transaction or await self.begin_transaction(read=[], write=[])
        try:
            roots, recs = await self._inventory([record_id], None, [], None, include_trashed=False, live_only=True, transaction=txn)
            keys = [r["id"] for r, _ in recs]
            refs = await deleted_record_taxonomy_refs(self, [r for r, _ in recs], self.logger)
            await self._q(lambda tx: _remove_records(tx, org, keys), txn, write=True)
            if transaction is None:
                await self.commit_transaction(txn)
            return {"success": True, "deleted_records": [{"record_id": r["id"], "name": r.get("recordName", "Unknown")} for r, _ in recs], "failed_records": [],
                    "total_requested": 1, "successfully_deleted": len(roots), "failed_count": 0, "eventData": await self._delete_events(recs, refs)}
        except BaseException as e:   # a cancel too: the unit must be rolled back and its connection given up
            if transaction is None:
                await self.rollback_transaction(txn)
                if isinstance(e, Exception):
                    self.logger.error(f"❌ Failed to delete single record: {e}")
                    return {"success": False, "reason": str(e), "code": 500, "eventData": None}
            raise

    async def _create_deleted_record_event_payload(
        self,
        record: dict,
        file_record: dict | None = None
    ) -> dict:
        """Create deleted record event payload matching Node.js format"""
        try:
            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            return {
                "orgId": record.get(_ORG_FIELD),
                "recordId": record.get("id"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "summaryDocumentId": record.get("summaryDocumentId"),
                "virtualRecordId": record.get("virtualRecordId"),
                "connectorId": record.get("connectorId"),
                # A deleted code file's repo is owed an edge build, and by the
                # time indexing handles this event the record is gone.
                "recordGroupId": record.get("recordGroupId"),
                "connectorName": record.get("connectorName"),
                # Only a deleted CODE_FILE changes the graph, not an issue or PR.
                "recordType": record.get("recordType"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create deleted record event payload: {str(e)}")
            return {}
