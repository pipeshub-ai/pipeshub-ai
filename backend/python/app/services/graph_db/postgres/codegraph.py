"""Code graph, pipeline stage states and staged-indexing state.

Code edges (CALLS, IMPORTS, INHERITS, CONTAINS, ...) are record links: ``nodeRelations`` rows between symbol blocks (``blocks``) or records,
the link type in ``ekey`` and in the edge document. Stage states are keyed documents (``stageStates``) with compare-and-set. Reads are bound
to the ambient org; the per-record access filter sits in the agent tools (``RecordAccess``), as it does on Neo4j.
"""
from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import current_org_id
from app.services.graph_db.common.record_visibility import indexed_copy_fields
from app.services.graph_db.interface.graph_db_provider import CodeFileInfo
from app.services.graph_db.postgres.base import (
    PgBase,
    _arango,
    _doc,
    _ekey,
    _field,
    _j,
    _match,
)

_LINKS = CollectionNames.NODE_RELATIONS.value
_STAGES = CollectionNames.STAGE_STATES.value
_BLOCKS = CollectionNames.BLOCKS.value
_POINTERS = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
# No record, in the trash or not, holds the revision ``g.rev`` of virtual record ``g.v``.
_UNHELD = """NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'virtualRecordId' = g.v AND r.doc->>'contentRev' = g.rev)"""
_LINK_ENDS = ("records", _BLOCKS)  # a code link ends on a record (whole file) or a block (symbol)
_REL = "coalesce(e.doc->>'relationshipType', nullif(e.ekey, ''))"      # the key also carries a constraint name, so the type is read from the document first


def _links(edge_collection: str) -> str:
    """Links live with the hierarchy in ``nodeRelations`` (their type is the ``ekey``); callers also name them ``recordLinks``, as ArangoDB stores them."""
    return _LINKS if edge_collection == CollectionNames.RECORD_LINKS.value else edge_collection


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ends(keys: list[str]) -> tuple[list[str], list[str]]:
    """``coll/id`` keys as parallel arrays; a bare id has an empty collection (any)."""
    parts = [k.split("/", 1) if "/" in k else ["", k] for k in dict.fromkeys(keys)]
    return [p[0] for p in parts], [p[1] for p in parts]


def _neighbour(r: Any, key: str, coll: str) -> dict[str, Any]:  # noqa: ANN401
    d = _doc(r["doc"])
    return {"collection": coll, "key": key, "relationshipType": r["rel"], "sourceLineNumber": d.get("sourceLineNumber"),
            "sourceColumnNumber": d.get("sourceColumnNumber"), "provenance": d.get("provenance"), "line": d.get("line"),
            "confidence": d.get("confidence")}


class CodeGraphMixin(PgBase):
    async def get_edges_from_node(self, node_id: str, edge_collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        return await super().get_edges_from_node(node_id, _links(edge_collection), transaction, raise_on_error=raise_on_error)

    async def get_edges_to_node(self, node_id: str, edge_collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        return await super().get_edges_to_node(node_id, _links(edge_collection), transaction, raise_on_error=raise_on_error)

    async def get_nodes_by_field_prefix(self, collection: str, field_name: str, prefix: str, filters: dict[str, Any] | None = None, limit: int = 400,
                                        transaction: str | None = None, *, raise_on_error: bool = False, return_fields: list[str] | None = None) -> list[dict]:
        org = current_org_id()
        extra, args = _match(filters, None, 6)
        expr = "n.doc->>'filePath'" if (collection, field_name) == ("codeFiles", "filePath") else "n.doc->>$3"  # the indexed form
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_nodes_by_field_prefix */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND $3::text IS NOT NULL AND {expr} LIKE $4 || '%' {extra} LIMIT $5""",
                                                      org, collection, _field(field_name), _like(prefix), limit, *args), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_nodes_by_field_prefix failed")
            return []
        docs = [_doc(r["doc"]) for r in rows]
        if return_fields is not None:      # only these fields; _key, _id and id all name the stored id, and _id gains its collection
            return [{f: (f"{collection}/{d['id']}" if f == "_id" else d.get("id" if f in ("_key", "id") else f)) for f in return_fields} for d in docs]
        return [_arango(_doc(r["doc"]), collection) for r in rows]

    async def search_nodes_by_field_terms(self, collection: str, field_name: str, terms: list[str], filters: dict[str, Any] | None = None,
                                          limit: int = 400, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()
        if not terms:
            return []
        extra, args = _match(filters, None, 6)
        pats = [f"%{_like(t.lower())}%" for t in terms]
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.search_nodes_by_field_terms */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND lower(n.doc->>$3) LIKE ANY($4::text[]) {extra} LIMIT $5""",
                                                      org, collection, _field(field_name), pats, limit, *args), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("search_nodes_by_field_terms failed")
            return []
        return [_arango(_doc(r["doc"]), collection) for r in rows]

    async def count_nodes_by_filters(self, collection: str, filters: dict[str, Any] | None = None, in_filters: dict[str, list[Any]] | None = None,
                                     transaction: str | None = None) -> int:
        org = current_org_id()
        extra, args = _match(filters, in_filters, 3)
        return int(await self._q(lambda tx: tx.fetchval(f"/* pipeshub:graph.count_nodes_by_filters */ SELECT count(*) FROM graph.nodes n "
                                                        f"WHERE n.org_id = $1 AND n.coll = $2 {extra}", org, collection, *args), transaction) or 0)

    async def has_nodes_by_filters(self, collection: str, filters: dict[str, Any] | None = None, in_filters: dict[str, list[Any]] | None = None,
                                   transaction: str | None = None) -> bool:
        org = current_org_id()
        extra, args = _match(filters, in_filters, 3)
        return bool(await self._q(lambda tx: tx.fetchval(f"/* pipeshub:graph.has_nodes_by_filters */ SELECT EXISTS (SELECT 1 FROM graph.nodes n "
                                                         f"WHERE n.org_id = $1 AND n.coll = $2 {extra})", org, collection, *args), transaction))

    async def get_nodes_updated_since(self, collection: str, timestamp_field: str, since: int, filters: dict[str, Any] | None = None,
                                      return_fields: list[str] | None = None, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()
        extra, args = _match(filters, None, 5)
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_nodes_updated_since */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND n.doc->$3 > to_jsonb($4::bigint) {extra}""",
                                                      org, collection, _field(timestamp_field), since, *args), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_nodes_updated_since failed")
            return []
        docs = [_arango(_doc(r["doc"]), collection) for r in rows]
        return [{f: d.get("_key" if f == "_key" else f) for f in return_fields} for d in docs] if return_fields else docs

    async def ensure_nodes(self, nodes: list[dict[str, Any]], collection: str, transaction: str | None = None) -> None:
        org = current_org_id()
        if not nodes:
            return
        docs = []
        for n in nodes:
            if "id" not in n and "_key" not in n:
                raise ValueError("ensure_nodes requires every node to carry an id")
            d = {k: v for k, v in n.items() if k not in ("_key", "_id")}
            d["id"] = n.get("id") or n["_key"]
            docs.append({"id": d["id"], "doc": d})
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.ensure_nodes */ INSERT INTO graph.nodes (org_id, coll, id, doc)
            SELECT $1, $2, x.id, jsonb_strip_nulls(x.doc) FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb)
            ON CONFLICT (org_id, coll, id) DO NOTHING""", org, collection, _j(docs)), transaction, write=True)

    async def ensure_pipeline_schema(self) -> None:
        return None  # the (org, coll, id) primary key is the stage state's unique key

    async def update_sync_point_if(self, sync_point_key: str, expected: dict[str, Any], sync_point_data: dict, collection: str) -> bool:
        org = current_org_id()
        from app.utils.time_conversion import get_epoch_timestamp_in_ms
        data = {**sync_point_data, "syncPointKey": sync_point_key, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}
        # The id is stable, so the row lock UPDATE takes re-checks `expected` against a write that committed while this waited.
        r = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.update_sync_point_if */ WITH u AS (UPDATE graph.nodes n SET doc = n.doc || $4::jsonb
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = (SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3 ORDER BY id LIMIT 1)
              AND NOT EXISTS (SELECT 1 FROM jsonb_each($5::jsonb) f WHERE n.doc->f.key IS DISTINCT FROM f.value
                  AND NOT (f.value = 'null'::jsonb AND n.doc->f.key IS NULL)) RETURNING 1) SELECT count(*) FROM u""",
                                                  org, collection, sync_point_key, _j(data), _j(expected)), None, write=True)
        return bool(r)

    # ---- code links ----------------------------------------------------------------------------------------------
    async def batch_upsert_node_relations(self, edges: list[dict], transaction: str | None = None) -> bool:
        org = current_org_id()
        if not edges:
            return True
        rows: dict[tuple, dict] = {}
        for e in edges:
            fk = e.get("from_id") or e.get("_from", "").split("/")[-1]
            tk = e.get("to_id") or e.get("_to", "").split("/")[-1]
            fc = e.get("from_collection") or (e["_from"].split("/")[0] if "/" in e.get("_from", "") else "")
            tc = e.get("to_collection") or (e["_to"].split("/")[0] if "/" in e.get("_to", "") else "")
            props = {k: v for k, v in e.items() if k not in ("from_id", "to_id", "from_collection", "to_collection", "_from", "_to")}
            key = _ekey(_LINKS, props)       # the type, and the constraint name where there is one: two foreign keys of one type between one pair are two edges
            rows[(fc, fk, tc, tk, key)] = {"fc": fc, "fi": fk, "tc": tc, "ti": tk, "k": key, "doc": props}
        # An edge whose end does not exist is skipped, as the MATCH of the Neo4j form does; an end without a collection is a record or a block.
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.batch_upsert_node_relations */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, $2, f.coll, x.fi, t.coll, x.ti, x.k, jsonb_strip_nulls(x.doc)
            FROM jsonb_to_recordset($3::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
            CROSS JOIN LATERAL (SELECT f0.coll FROM unnest(CASE WHEN x.fc = '' THEN $4::text[] ELSE ARRAY[x.fc] END) c(coll)
                JOIN graph.nodes f0 ON f0.org_id = $1 AND f0.coll = c.coll AND f0.id = x.fi LIMIT 1) f
            CROSS JOIN LATERAL (SELECT t0.coll FROM unnest(CASE WHEN x.tc = '' THEN $4::text[] ELSE ARRAY[x.tc] END) c(coll)
                JOIN graph.nodes t0 ON t0.org_id = $1 AND t0.coll = c.coll AND t0.id = x.ti LIMIT 1) t
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = graph.edges.doc || (EXCLUDED.doc - 'createdAtTimestamp')""",
                                                  org, _LINKS, _j(list(rows.values())), list(_LINK_ENDS)), transaction, write=True)
        return True

    async def get_neighbors_by_relationship_types(self, node_key: str, node_collection: str, relationship_types: list[str], direction: str,
                                                  limit: int = 25, transaction: str | None = None) -> list[dict[str, Any]]:
        org = current_org_id()
        if not relationship_types:
            return []
        if direction not in ("outbound", "inbound"):
            raise ValueError(f"direction must be 'outbound' or 'inbound', got {direction!r}")
        out = direction == "outbound"
        a, b = ("from", "to") if out else ("to", "from")
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_neighbors_by_relationship_types */ SELECT e.{b}_coll AS coll, e.{b}_id AS key,
                {_REL} AS rel, e.doc FROM graph.edges e WHERE e.org_id = $1 AND e.coll = $2 AND e.{a}_coll = $3 AND e.{a}_id = $4
                AND {_REL} = ANY($5::text[]) LIMIT $6""", org, _LINKS, node_collection, node_key, relationship_types, limit), transaction)
        except Exception:
            self.logger.exception("get_neighbors_by_relationship_types failed")
            return []
        return [_neighbour(r, r["key"], r["coll"]) for r in rows]

    async def get_neighbors_for_nodes_by_relationship_types(self, node_keys: list[str], node_collection: str, relationship_types: list[str], direction: str,
                                                            limit: int = 5000, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        org = current_org_id()
        if not node_keys or not relationship_types:
            return []
        if direction not in ("outbound", "inbound", "any"):
            raise ValueError(f"direction must be 'outbound', 'inbound' or 'any', got {direction!r}")
        legs = []
        if direction in ("outbound", "any"):
            legs.append("""SELECT e.from_id AS anchor, e.to_coll AS coll, e.to_id AS key, 'outbound' AS dir, e.ekey, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = $2 AND e.from_coll = $3 AND e.from_id = ANY($4::text[])""")
        if direction in ("inbound", "any"):  # a self-loop is one neighbour, outbound
            legs.append(f"""SELECT e.to_id AS anchor, e.from_coll AS coll, e.from_id AS key, 'inbound' AS dir, e.ekey, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = $2 AND e.to_coll = $3 AND e.to_id = ANY($4::text[])
                {"AND NOT (e.from_coll = e.to_coll AND e.from_id = e.to_id)" if direction == "any" else ""}""")
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_neighbors_for_nodes_by_relationship_types */ SELECT e.anchor, e.coll, e.key, e.dir,
                coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) AS rel, e.doc FROM ({" UNION ALL ".join(legs)}) e
                WHERE coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) = ANY($5::text[]) LIMIT $6""",
                                                      org, _LINKS, node_collection, node_keys, relationship_types, limit), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_neighbors_for_nodes_by_relationship_types failed")
            return []
        return [{"anchorKey": r["anchor"], "direction": r["dir"], **_neighbour(r, r["key"], r["coll"])} for r in rows]

    async def get_edges_by_target_keys(self, target_keys: list[str], edge_collection: str, filters: dict[str, Any] | None = None,
                                       return_field: str = "_from", transaction: str | None = None, *, raise_on_error: bool = False) -> list[str]:
        org = current_org_id()
        if not target_keys:
            return []
        if return_field not in ("_from", "_to"):
            raise ValueError("return_field must be '_from' or '_to'")
        colls, ids = _ends(target_keys)
        extra, args = _match(filters, None, 5, "e")
        c = "from" if return_field == "_from" else "to"
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_edges_by_target_keys */ SELECT DISTINCT e.{c}_coll AS coll, e.{c}_id AS key
                FROM unnest($3::text[], $4::text[]) t(c, i) JOIN graph.edges e ON e.org_id = $1 AND e.coll = $2 AND e.to_id = t.i
                AND (t.c = '' OR e.to_coll = t.c) WHERE true {extra}""", org, _links(edge_collection), colls, ids, *args), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_edges_by_target_keys failed")
            return []
        return [f"{r['coll']}/{r['key']}" for r in rows]

    async def delete_edges_by_source_keys(self, source_keys: list[str], edge_collection: str, filters: dict[str, Any] | None = None,
                                          transaction: str | None = None, exclude: dict[str, Any] | None = None) -> int:
        org = current_org_id()
        if not source_keys:
            return 0
        colls, ids = _ends(source_keys)
        extra, args = _match(filters, None, 5, "e")
        if exclude:  # a missing property keeps the edge
            extra += f""" AND NOT EXISTS (SELECT 1 FROM unnest(${5 + len(args)}::text[], ${6 + len(args)}::text[]) x(k, v) WHERE e.doc->x.k = x.v::jsonb)"""
            args += [list(exclude), [_j(v) for v in exclude.values()]]
        return int(await self._q(lambda tx: tx.fetchval(f"""/* pipeshub:graph.delete_edges_by_source_keys */ WITH d AS (DELETE FROM graph.edges e
            USING unnest($3::text[], $4::text[]) s(c, i) WHERE e.org_id = $1 AND e.coll = $2 AND e.from_id = s.i AND (s.c = '' OR e.from_coll = s.c) {extra}
            RETURNING 1) SELECT count(*) FROM d""", org, _links(edge_collection), colls, ids, *args), transaction, write=True) or 0)

    async def delete_edges_touching_nodes(self, node_keys: list[str], node_collection: str, edge_collection: str, transaction: str | None = None) -> int:
        org = current_org_id()
        if not node_keys:
            return 0
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_touching_nodes */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1
            AND coll = $2 AND ((from_coll = $3 AND from_id = ANY($4::text[])) OR (to_coll = $3 AND to_id = ANY($4::text[]))) RETURNING 1)
            SELECT count(*) FROM d""", org, _links(edge_collection), node_collection, node_keys), transaction, write=True) or 0)

    async def get_edge_rollup_by_file_prefix(self, org_id: str, file_path_prefix: str, relationship_types: list[str], direction: str, connector_id: str,
                                             limit: int = 100000, transaction: str | None = None) -> list[dict]:
        org = current_org_id()
        if direction not in ("outbound", "inbound", "any"):
            raise ValueError("direction must be 'outbound', 'inbound', or 'any'")
        legs = []
        if direction in ("outbound", "any"):
            legs.append("SELECT b.rid, e.to_coll AS oc, e.to_id AS oi, 'outbound' AS dir, e.ekey, e.doc FROM b JOIN graph.edges e ON e.org_id = $1 AND e.coll = $2 AND e.from_coll = 'blocks' AND e.from_id = b.id")
        if direction in ("inbound", "any"):
            legs.append("SELECT b.rid, e.from_coll AS oc, e.from_id AS oi, 'inbound' AS dir, e.ekey, e.doc FROM b JOIN graph.edges e ON e.org_id = $1 AND e.coll = $2 AND e.to_coll = 'blocks' AND e.to_id = b.id"
                        + (" AND NOT (e.from_coll = e.to_coll AND e.from_id = e.to_id)" if direction == "any" else ""))
        try:
            rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.get_edge_rollup_by_file_prefix */ WITH scoped AS (
                SELECT r.id FROM graph.nodes cf JOIN graph.nodes r ON r.org_id = cf.org_id AND r.coll = 'records' AND r.id = cf.id
                WHERE cf.org_id = $1 AND cf.coll = 'codeFiles' AND cf.doc->>'orgId' = $3 AND cf.doc->>'filePath' LIKE $4 || '%'
                  AND r.doc->>'connectorId' = $5),
              b AS (SELECT n.id, n.doc->>'recordId' AS rid FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'blocks'
                  AND n.doc->>'recordId' IN (SELECT id FROM scoped))
              SELECT l.rid AS "srcRecord", coalesce(l.doc->>'relationshipType', nullif(l.ekey, '')) AS rel, l.dir AS dir,
                     coalesce(t.doc->>'recordId', l.oi) AS "dstRecord", count(*) AS n
              FROM ({" UNION ALL ".join(legs)}) l LEFT JOIN graph.nodes t ON t.org_id = $1 AND t.coll = l.oc AND t.id = l.oi
              WHERE coalesce(l.doc->>'relationshipType', nullif(l.ekey, '')) = ANY($6::text[])
              GROUP BY 1, 2, 3, 4 LIMIT $7""", org, _LINKS, org_id, _like(file_path_prefix), connector_id, relationship_types, limit), transaction)
        except Exception:
            self.logger.exception("get_edge_rollup_by_file_prefix failed")
            return []
        return [dict(r) for r in rows]

    async def get_file_paths_for_records(self, org_id: str, record_ids: list[str], transaction: str | None = None, *,
                                         raise_on_error: bool = False) -> dict[str, CodeFileInfo]:
        org = current_org_id()
        if not record_ids:
            return {}
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_file_paths_for_records */ SELECT r.id AS rid, r.doc->>'recordName' AS name, c.doc AS cf
                FROM graph.nodes r LEFT JOIN graph.nodes c ON c.org_id = r.org_id AND c.coll = 'codeFiles' AND c.id = r.id
                WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $2 AND r.id = ANY($3::text[])""",
                                                  org, org_id, record_ids), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_file_paths_for_records failed")
            return {}
        out: dict[str, CodeFileInfo] = {}
        for r in rows:
            cf = _doc(r["cf"]) if r["cf"] else {}
            path = cf.get("filePath") or r["name"]
            if path:
                out[r["rid"]] = CodeFileInfo(file_path=path, referenced_names=tuple(cf.get("referencedNames") or ()),
                                             changed_names=tuple(cf.get("changedNames") or ()), has_code_file=bool(cf))
        return out

    # ---- blocks --------------------------------------------------------------------------------------------------
    async def delete_blocks_for_records(self, record_ids: list[str], transaction: str | None = None) -> int:
        org = current_org_id()
        if not record_ids:
            return 0
        # Blocks are found by recordId, so this is right before or after the record goes; every edge of a block goes with it (DETACH DELETE).
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_blocks_for_records */ WITH b AS (
            SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'blocks' AND doc->>'recordId' = ANY($2::text[])),
          de1 AS (DELETE FROM graph.edges e USING b WHERE e.org_id = $1 AND e.from_coll = 'blocks' AND e.from_id = b.id RETURNING 1),
          de2 AS (DELETE FROM graph.edges e USING b WHERE e.org_id = $1 AND e.to_coll = 'blocks' AND e.to_id = b.id RETURNING 1),
          d AS (DELETE FROM graph.nodes n USING b WHERE n.org_id = $1 AND n.coll = 'blocks' AND n.id = b.id RETURNING 1)
          SELECT count(*) FROM d""", org, record_ids), transaction, write=True) or 0)

    async def backfill_block_name_folded(self, limit: int = 5000) -> int:
        org = current_org_id()
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.backfill_block_name_folded */ WITH u AS (UPDATE graph.nodes n
            SET doc = n.doc || jsonb_build_object('nameFolded', lower(n.doc->>'name')) WHERE n.org_id = $1 AND n.coll = 'blocks' AND n.id IN (
                SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'blocks' AND doc->>'nameFolded' IS NULL AND doc->>'name' IS NOT NULL LIMIT $2)
            RETURNING 1) SELECT count(*) FROM u""", org, limit), None, write=True) or 0)

    # ---- records: revision-guarded write -------------------------------------------------------------------------
    async def compare_and_set_record_fields(self, record_ids: list[str], content_rev: str, fields: dict[str, Any]) -> list[str]:
        org = current_org_id()
        keys = sorted({r for r in record_ids if r})
        if not keys:
            return []
        if "indexingStatus" in fields:
            fields = {**indexed_copy_fields(fields["indexingStatus"]), **fields}
        # Locks are taken in id order, and the revision is read after the lock.
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.compare_and_set_record_fields */ WITH l AS (SELECT id FROM graph.nodes
            WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[]) AND doc->>'contentRev' = $3 ORDER BY id FOR UPDATE)
            UPDATE graph.nodes n SET doc = jsonb_strip_nulls(n.doc || $4::jsonb) FROM l WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = l.id RETURNING n.id""",
                                                  org, keys, content_rev, _j(fields)), None, write=True)
        return [r["id"] for r in rows]

    # ---- stage states --------------------------------------------------------------------------------------------
    async def stage_state_get(self, key: str) -> dict[str, Any] | None:
        org = current_org_id()
        r = await self._q(lambda tx: tx.fetchrow("/* pipeshub:graph.stage_state_get */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3",
                                                 org, _STAGES, key), None)
        return _doc(r["doc"]) if r else None

    async def stage_states_for_revision(self, virtual_record_id: str, rev: str) -> list[dict[str, Any]]:
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.stage_states_for_revision */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2
            AND doc->>'virtualRecordId' = $3 AND doc->>'rev' = $4""", org, _STAGES, virtual_record_id, rev), None)
        return [_doc(r["doc"]) for r in rows]

    async def stage_state_create(self, document: dict[str, Any]) -> bool:
        org = current_org_id()
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.stage_state_create */ WITH i AS (INSERT INTO graph.nodes (org_id, coll, id, doc)
            VALUES ($1, $2, $3, jsonb_strip_nulls($4::jsonb)) ON CONFLICT (org_id, coll, id) DO NOTHING RETURNING 1) SELECT count(*) FROM i""",
                                                  org, _STAGES, document["id"], _j(document)), None, write=True))

    async def stage_state_compare_and_set(self, key: str, expected: str, new: str, fields: dict[str, Any], *, expected_attempt: int | None = None) -> bool:
        org = current_org_id()
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.stage_state_compare_and_set */ WITH u AS (UPDATE graph.nodes n
            SET doc = jsonb_strip_nulls(n.doc || $5::jsonb || jsonb_build_object('status', $4::text))
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3 AND n.doc->>'status' = $6 AND ($7::bigint IS NULL OR (n.doc->>'attempt')::bigint = $7)
            RETURNING 1) SELECT count(*) FROM u""", org, _STAGES, key, new, _j(fields), expected, expected_attempt), None, write=True))

    async def get_stage_states_with_records(self, org_id: str, connector_id: str, stage: str, status: str, *, limit: int, after_key: str | None = None,
                                            exclude_indexing_statuses: list[str] | None = None) -> list[tuple[str, list[str]]]:
        """A page of the connector's ``stage`` states in ``status`` (by key after ``after_key``), each with the live, non-placeholder records of the
        connector on that state's revision whose ``indexingStatus`` is not excluded; a state none of whose records qualifies has an empty list."""
        org = current_org_id()
        if limit <= 0 or (org_id and org_id != org):
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_stage_states_with_records */ WITH st AS (SELECT s.id, s.doc->>'virtualRecordId' AS vrid, s.doc->>'rev' AS rev
                FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = $2 AND s.doc->>'connectorId' = $3 AND s.doc->>'stage' = $4 AND s.doc->>'status' = $5
                  AND ($6::text IS NULL OR s.id > $6) ORDER BY s.id LIMIT $7)
            SELECT st.id AS key, coalesce(array_agg(r.id ORDER BY r.id) FILTER (WHERE r.id IS NOT NULL), '{}') AS records FROM st
            LEFT JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.doc->>'virtualRecordId' = st.vrid AND r.doc->>'contentRev' = st.rev
              AND r.doc->>'connectorId' = $3 AND NOT coalesce((r.doc->>'isPlaceholder')::boolean, false) AND NOT coalesce((r.doc->>'isDeleted')::boolean, false)
              AND NOT (coalesce(r.doc->>'indexingStatus', '') = ANY($8::text[]))
            GROUP BY st.id ORDER BY st.id""", org, _STAGES, connector_id, stage, status, after_key, limit, exclude_indexing_statuses or []), None)
        return [(r["key"], list(r["records"])) for r in rows]

    async def get_record_content_revs(self, record_ids: list[str], *, status_field: str, statuses: list[str],
                                      exclude_indexing_statuses: list[str] | None = None) -> dict[str, str | None]:
        """Record id -> ``contentRev`` (None when it has none), for the given live records whose ``status_field`` is still in ``statuses`` and whose
        ``indexingStatus`` is not excluded: a revision returned is one the record holds while still selected."""
        org = current_org_id()
        keys = [r for r in dict.fromkeys(record_ids) if r]
        if not keys or not statuses:
            return {}
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_record_content_revs */ SELECT r.id, nullif(r.doc->>'contentRev', '') AS rev FROM graph.nodes r
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>$3::text = ANY($4::text[])
              AND NOT (coalesce(r.doc->>'indexingStatus', '') = ANY($5::text[])) AND NOT coalesce((r.doc->>'isDeleted')::boolean, false)""",
                                                  org, keys, status_field, statuses, exclude_indexing_statuses or []), None)
        return {r["id"]: r["rev"] for r in rows}

    async def stage_states_stale(self, statuses: list[str], updated_before_ms: int, limit: int) -> list[dict[str, Any]]:
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.stage_states_stale */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2
            AND doc->>'status' = ANY($3::text[]) AND doc->'updatedAtMs' < to_jsonb($4::bigint) ORDER BY doc->'updatedAtMs' LIMIT $5""",
                                                  org, _STAGES, statuses, updated_before_ms, limit), None)
        return [_doc(r["doc"]) for r in rows]

    async def stage_states_delete_for_virtual_records(self, virtual_record_ids: list[str]) -> int:
        org = current_org_id()
        ids = sorted({v for v in virtual_record_ids if v})
        if not ids:
            return 0
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.stage_states_delete_for_virtual_records */ WITH d AS (DELETE FROM graph.nodes
            WHERE org_id = $1 AND coll = $2 AND doc->>'virtualRecordId' = ANY($3::text[]) AND doc ? 'rev' RETURNING 1) SELECT count(*) FROM d""",
                                                  org, _STAGES, ids), None, write=True) or 0)

    # ---- stored-write protocol: pointers and unheld revisions -------------------------------------------------------
    async def _pointer_page(self, after_key: str | None, limit: int) -> list[dict]:
        org = current_org_id()
        if limit <= 0:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.pointer_page */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2
            AND ($3::text IS NULL OR id > $3) ORDER BY id LIMIT $4""", org, _POINTERS, after_key, limit), None)
        return [_doc(r["doc"]) for r in rows]

    async def get_switch_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        org = current_org_id()
        return [{"virtualRecordId": d["id"], "orgId": org, "documentId": d.get("documentId"), "switchSeq": d.get("switchSeq"),
                 "lastSwitchAt": d.get("lastSwitchAt"), "retired": d["retired"] if isinstance(d.get("retired"), list) else []}
                for d in await self._pointer_page(after_key, limit)]

    async def get_revision_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        org = current_org_id()
        return [{"virtualRecordId": d["id"], "orgId": org, "currentRev": d.get("currentRev"), "previousRev": d.get("previousRev")}
                for d in await self._pointer_page(after_key, limit)]

    async def stage_states_unheld_revisions(self, virtual_record_ids: list[str], *, updated_before_ms: int) -> list[tuple[str, str]]:
        """``(virtualRecordId, rev)`` with stage states, held by no record (``contentRev``, trash included), newest state older than the cut-off."""
        org = current_org_id()
        ids = sorted({v for v in virtual_record_ids if v})
        if not ids:
            return []
        rows = await self._q(lambda tx: tx.fetch(f"""/* pipeshub:graph.stage_states_unheld_revisions */ WITH g AS (SELECT s.doc->>'virtualRecordId' AS v, s.doc->>'rev' AS rev,
                max(coalesce((s.doc->>'updatedAtMs')::numeric, 0)) AS newest FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = $2
                AND s.doc->>'virtualRecordId' = ANY($3::text[]) AND s.doc->>'rev' IS NOT NULL GROUP BY 1, 2)
            SELECT g.v, g.rev FROM g WHERE g.newest < $4::bigint AND {_UNHELD} ORDER BY g.v, g.rev""", org, _STAGES, ids, updated_before_ms), None)
        return [(r["v"], r["rev"]) for r in rows]

    async def stage_states_delete_unheld_revisions(self, revisions: list[tuple[str, str]], *, updated_before_ms: int) -> int:
        """Delete the stage states of each pair that still meets the rule above, decided again inside the delete."""
        org = current_org_id()
        pairs = sorted({(v, r) for v, r in revisions if v and r})
        if not pairs:
            return 0
        return int(await self._q(lambda tx: tx.fetchval(f"""/* pipeshub:graph.stage_states_delete_unheld_revisions */ WITH g AS (SELECT p.v, p.rev,
                (SELECT max(coalesce((s.doc->>'updatedAtMs')::numeric, 0)) FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = $2
                   AND s.doc->>'virtualRecordId' = p.v AND s.doc->>'rev' = p.rev) AS newest
              FROM unnest($3::text[], $4::text[]) AS p(v, rev)),
            ok AS (SELECT g.v, g.rev FROM g WHERE g.newest < $5::bigint AND {_UNHELD}),
            d AS (DELETE FROM graph.nodes s USING ok WHERE s.org_id = $1 AND s.coll = $2 AND s.doc->>'virtualRecordId' = ok.v AND s.doc->>'rev' = ok.rev RETURNING 1)
            SELECT count(*) FROM d""", org, _STAGES, [v for v, _ in pairs], [r for _, r in pairs], updated_before_ms), None, write=True) or 0)
