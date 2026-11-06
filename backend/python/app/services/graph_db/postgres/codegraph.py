"""Code graph, pipeline stage states and staged-indexing state.

Code edges (CALLS, IMPORTS, INHERITS, CONTAINS, ...) are record links: ``nodeRelations`` rows between symbol blocks (``blocks``) or records,
the link type in ``ekey`` and in the edge document. Stage states are keyed documents (``stageStates``) with compare-and-set. Reads are bound
to the ambient org; the per-record access filter sits in the agent tools (``RecordAccess``), as it does on Neo4j.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core import org_context
from app.core.org_context import current_or_system, current_org_id
from app.services.graph_db.common.record_visibility import indexed_copy_fields
from app.services.graph_db.interface.graph_db_provider import CodeFileInfo
from app.services.graph_db.postgres.base import (
    _LINK_COLLS,
    PgBase,
    _arango,
    _doc,
    _edge_scope,
    _ekey,
    _field,
    _j,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_LINKS = CollectionNames.NODE_RELATIONS.value
_STAGES = CollectionNames.STAGE_STATES.value
_BLOCKS = CollectionNames.BLOCKS.value
_POINTERS = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
_LINK_ENDS = ("records", _BLOCKS)  # a code link ends on a record (whole file) or a block (symbol)


def _filters(filters: dict | None, in_filters: dict | None = None) -> list:
    """The three parameters of the fixed filter predicate: equality keys, their JSON values, and the membership filters as one JSON object.
    Empty ones constrain nothing (a missing property equals null only through ``IS DISTINCT FROM``, as before)."""
    keys = list(filters or {})
    return [[_field(k) for k in keys], [_j((filters or {})[k]) for k in keys], _j({_field(k): list(v) for k, v in (in_filters or {}).items()})]


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _retired(v: Any) -> list:  # noqa: ANN401
    """The retired revisions of a pointer: a list, or a JSON string of one (as the Neo4j provider decodes it); anything else is none."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _same_end(a: str, b: str) -> bool:
    """Two names of an edge end are one end when equal or when one leaves the collection out."""
    return a == b or "" in (a, b)


def _ends(keys: list[str]) -> tuple[list[str], list[str]]:
    """``coll/id`` keys as parallel arrays; a bare id has an empty collection (any)."""
    parts = [k.split("/", 1) if "/" in k else ["", k] for k in dict.fromkeys(keys)]
    return [p[0] for p in parts], [p[1] for p in parts]


def _neighbour(r: Any, key: str, coll: str) -> dict[str, Any]:  # noqa: ANN401
    d = _doc(r["doc"])
    return {"collection": coll, "key": key, "relationshipType": r["rel"], "sourceLineNumber": d.get("sourceLineNumber"),
            "sourceColumnNumber": d.get("sourceColumnNumber"), "provenance": d.get("provenance"), "line": d.get("line"),
            "confidence": d.get("confidence")}



def _page_whole_ids(pointers: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The first ``limit`` pointers by virtual record id, and with them every other organisation's pointer of the id the page ends on.
    A walk continues after that id, so a pointer cut off from its id here would never be read."""
    ordered = sorted(pointers, key=lambda p: p["virtualRecordId"])
    page = ordered[:limit]
    if page and len(ordered) > limit:
        page += [p for p in ordered[limit:] if p["virtualRecordId"] == page[-1]["virtualRecordId"]]
    return page


class CodeGraphMixin(PgBase):
    async def _sweep_orgs(self) -> list[str | None]:
        """``[None]`` under an org context (the ambient org); under a system context (the indexing tick's sweeps) every organisation, through the
        vetted ``graph.system_orgs`` (no jobs login needed). No context at all raises."""
        if current_or_system().kind != "system":
            return [None]
        orgs: set[str] = set()
        for external in (False, True):       # active or not, internal or external, in pages of 1,000
            after, after_id = "", ""
            while True:
                rows = await self._system_read(lambda tx, a=after, i=after_id, x=external: tx.fetch(
                    "/* pipeshub:graph.system_orgs */ SELECT org_id, node_id FROM graph.system_orgs($1, $2, $3, $4, 1000)", True, x, a, i))
                orgs.update(r[0] for r in rows)
                if len(rows) < 1000:
                    break
                after, after_id = rows[-1][0], rows[-1][1]
        return sorted(orgs)

    @contextmanager
    def _sweep(self, org: str | None, errors: list[tuple[str, Exception]]) -> Iterator[None]:
        """One organisation's part of a sweep, bound to that org. A failure of one of several organisations is logged with its id and kept in
        ``errors`` so the later organisations still run; ``_raise_failed`` raises it after the loop, so it is never silent."""
        if org is None:
            yield
            return
        try:
            with org_context.for_org(org, job="graph.sweep"):
                yield
        except Exception as e:
            self.logger.exception("Sweep failed for org %s", org)
            errors.append((org, e))

    @staticmethod
    def _raise_failed(errors: list[tuple[str, Exception]]) -> None:
        if errors:
            first = errors[0][1]
            first.add_note(f"{len(errors)} organisation(s) failed in this pass: {sorted(o for o, _ in errors)}")
            raise first

    async def get_nodes_by_field_prefix(self, collection: str, field_name: str, prefix: str, filters: dict[str, Any] | None = None, limit: int = 400,
                                        transaction: str | None = None, *, raise_on_error: bool = False, return_fields: list[str] | None = None) -> list[dict]:
        org = current_org_id()
        keys, vals, ins = _filters(filters)
        try:
            if (collection, field_name) == ("codeFiles", "filePath"):      # the indexed form: the range the text_pattern_ops index serves
                rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_by_field_prefix_path */ SELECT n.doc FROM graph.nodes n
                    WHERE n.org_id = $1 AND n.coll = $2 AND $3::text IS NOT NULL AND n.doc->>'filePath' ~>=~ $4::text AND n.doc->>'filePath' ~<~ ($4::text || chr(1114111))
                    AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($8::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key)) LIMIT $5""", org, collection, _field(field_name), prefix, limit, keys, vals, ins), transaction)
            else:
                rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_by_field_prefix */ SELECT n.doc FROM graph.nodes n
                    WHERE n.org_id = $1 AND n.coll = $2 AND n.doc->>$3 ~>=~ $4::text AND n.doc->>$3 ~<~ ($4::text || chr(1114111))
                    AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($8::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key)) LIMIT $5""", org, collection, _field(field_name), prefix, limit, keys, vals, ins), transaction)
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
        keys, vals, ins = _filters(filters)
        pats = [f"%{_like(t.lower())}%" for t in terms]
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.search_nodes_by_field_terms */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND lower(n.doc->>$3) LIKE ANY($4::text[])
                AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($8::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key)) LIMIT $5""", org, collection, _field(field_name), pats, limit, keys, vals, ins), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("search_nodes_by_field_terms failed")
            return []
        return [_arango(_doc(r["doc"]), collection) for r in rows]

    async def count_nodes_by_filters(self, collection: str, filters: dict[str, Any] | None = None, in_filters: dict[str, list[Any]] | None = None,
                                     transaction: str | None = None) -> int:
        org = current_org_id()
        keys, vals, ins = _filters(filters, in_filters)
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.count_nodes_by_filters */ SELECT count(*) FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = $2
            AND NOT EXISTS (SELECT 1 FROM unnest($3::text[], $4::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($5::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key))""", org, collection, keys, vals, ins), transaction) or 0)

    async def has_nodes_by_filters(self, collection: str, filters: dict[str, Any] | None = None, in_filters: dict[str, list[Any]] | None = None,
                                   transaction: str | None = None) -> bool:
        org = current_org_id()
        keys, vals, ins = _filters(filters, in_filters)
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.has_nodes_by_filters */ SELECT EXISTS (SELECT 1 FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = $2
            AND NOT EXISTS (SELECT 1 FROM unnest($3::text[], $4::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($5::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key)))""", org, collection, keys, vals, ins), transaction))

    async def get_nodes_updated_since(self, collection: str, timestamp_field: str, since: int, filters: dict[str, Any] | None = None,
                                      return_fields: list[str] | None = None, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()
        keys, vals, ins = _filters(filters)
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_updated_since */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND n.doc->$3 > to_jsonb($4::bigint)
                AND NOT EXISTS (SELECT 1 FROM unnest($5::text[], $6::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($7::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = n.doc->f.key))""", org, collection, _field(timestamp_field), since, keys, vals, ins), transaction)
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
            SELECT $1, $2, x.id, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(x.doc) AS _nv WHERE _nv.value <> 'null'::jsonb) FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb)
            ON CONFLICT (org_id, coll, id) DO NOTHING""", org, collection, _j(docs)), transaction, write=True)

    async def ensure_pipeline_schema(self) -> None:
        return None  # the (org, coll, id) primary key is the stage state's unique key

    async def update_sync_point_if(self, sync_point_key: str, expected: dict[str, Any], sync_point_data: dict, collection: str) -> bool:
        org = current_org_id()
        from app.utils.time_conversion import get_epoch_timestamp_in_ms
        data = {**sync_point_data, "syncPointKey": sync_point_key, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}
        # The id is stable, so the row lock UPDATE takes re-checks `expected` against a write that committed while this waited.
        r = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.update_sync_point_if */ WITH u AS (UPDATE graph.nodes n SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(n.doc || $4::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb)
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = (SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3 ORDER BY id LIMIT 1)
              AND NOT EXISTS (SELECT 1 FROM jsonb_each($5::jsonb) f WHERE n.doc->f.key IS DISTINCT FROM f.value
                  AND NOT (f.value = 'null'::jsonb AND n.doc->f.key IS NULL)) RETURNING 1) SELECT count(*) FROM u""",
                                                  org, collection, sync_point_key, _j(data), _j(expected)), None, write=True)
        return bool(r)

    # ---- code links ----------------------------------------------------------------------------------------------
    async def batch_upsert_node_relations(
        self, edges: list[dict], transaction: str | None = None, *, count_created: bool = False
    ) -> bool | int:
        if count_created:  # this store does not report which edges a write created; the store's before/after comparison covers it
            raise NotImplementedError("batch_upsert_node_relations(count_created=True) is not supported on the PostgreSQL graph store")
        org = current_org_id()
        if not edges:
            return True
        groups: dict[tuple, list[dict]] = {}
        for e in edges:
            fk = e.get("from_id") or e.get("_from", "").split("/")[-1]
            tk = e.get("to_id") or e.get("_to", "").split("/")[-1]
            fc = e.get("from_collection") or (e["_from"].split("/")[0] if "/" in e.get("_from", "") else "")
            tc = e.get("to_collection") or (e["_to"].split("/")[0] if "/" in e.get("_to", "") else "")
            props = {k: v for k, v in e.items() if k not in ("from_id", "to_id", "from_collection", "to_collection", "_from", "_to")}
            key = _ekey(_LINKS, props)       # the type, and the constraint name where there is one: two foreign keys of one type between one pair are two edges
            same = groups.setdefault((fk, tk, key), [])
            # The same edge named once with its collections and once without is one edge; repeats apply in order, a null clears the property (``r += props``).
            twin = next((r for r in same if _same_end(r["fc"], fc) and _same_end(r["tc"], tc)), None)
            if twin:
                twin.update(fc=twin["fc"] or fc, tc=twin["tc"] or tc, doc={**twin["doc"], **props})
            else:
                same.append({"fc": fc, "fi": fk, "tc": tc, "ti": tk, "k": key, "doc": props})
        rows = [r for g in groups.values() for r in g]
        # An edge whose end does not exist is skipped, as the MATCH of the Neo4j form does; an end without a collection is a record or a block.
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.batch_upsert_node_relations */ WITH src AS (SELECT f.coll AS fcoll, x.fi, t.coll AS tcoll, x.ti, x.k, x.doc
              FROM jsonb_to_recordset($3::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
              CROSS JOIN LATERAL (SELECT f0.coll FROM unnest(CASE WHEN x.fc = '' THEN $4::text[] ELSE ARRAY[x.fc] END) WITH ORDINALITY c(coll, o)
                  JOIN graph.nodes f0 ON f0.org_id = $1 AND f0.coll = c.coll AND f0.id = x.fi ORDER BY c.o LIMIT 1) f
              CROSS JOIN LATERAL (SELECT t0.coll FROM unnest(CASE WHEN x.tc = '' THEN $4::text[] ELSE ARRAY[x.tc] END) WITH ORDINALITY c(coll, o)
                  JOIN graph.nodes t0 ON t0.org_id = $1 AND t0.coll = c.coll AND t0.id = x.ti ORDER BY c.o LIMIT 1) t)
            INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, $2, fcoll, fi, tcoll, ti, k, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(doc) AS _nv WHERE _nv.value <> 'null'::jsonb) FROM src
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(graph.edges.doc || (
                SELECT s.doc - 'createdAtTimestamp' FROM src s WHERE s.fcoll = graph.edges.from_coll AND s.fi = graph.edges.from_id
                AND s.tcoll = graph.edges.to_coll AND s.ti = graph.edges.to_id AND s.k = graph.edges.ekey)) AS _nv WHERE _nv.value <> 'null'::jsonb)""",
                                                  org, _LINKS, _j(rows), list(_LINK_ENDS)), transaction, write=True)
        return True

    async def get_neighbors_by_relationship_types(self, node_key: str, node_collection: str, relationship_types: list[str], direction: str,
                                                  limit: int = 25, transaction: str | None = None) -> list[dict[str, Any]]:
        org = current_org_id()
        if not relationship_types:
            return []
        if direction not in ("outbound", "inbound"):
            raise ValueError(f"direction must be 'outbound' or 'inbound', got {direction!r}")
        out = direction == "outbound"
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_neighbors_by_relationship_types */ SELECT l.coll, l.key, l.rel, l.doc FROM (
                SELECT e.to_coll AS coll, e.to_id AS key, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) AS rel, e.doc FROM graph.edges e
                  WHERE $7::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4
                UNION ALL
                SELECT e.from_coll, e.from_id, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')), e.doc FROM graph.edges e
                  WHERE NOT $7::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = $4) l
                WHERE l.rel = ANY($5::text[]) LIMIT $6""", org, _LINK_COLLS, node_collection, node_key, relationship_types, limit, out), transaction)
        except Exception:
            self.logger.exception("get_neighbors_by_relationship_types failed")
            return []
        return [_neighbour(r, r["key"], r["coll"]) for r in rows]

    async def get_neighbors_for_nodes_by_relationship_types(self, node_keys: list[str], node_collection: str, relationship_types: list[str], direction: str,
                                                            limit: int = 5000, transaction: str | None = None, *,
                                                            raise_on_error: bool = False, after: str | None = None) -> list[dict[str, Any]]:
        org = current_org_id()
        if not node_keys or not relationship_types:
            return []
        if direction not in ("outbound", "inbound", "any"):
            raise ValueError(f"direction must be 'outbound', 'inbound' or 'any', got {direction!r}")
        out, inn, both = direction in ("outbound", "any"), direction in ("inbound", "any"), direction == "any"
        paged = after is not None
        try:
            if paged:
                # cursor = the row's sort key, ending in the edge's own key so parallel edges never tie; ORDER BY before LIMIT makes a page the next slice of the ordered edges.
                rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_neighbors_for_nodes_by_relationship_types */ SELECT e.anchor, e.coll, e.key, e.dir,
                    coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) AS rel, e.doc,
                    concat_ws(chr(1), e.anchor, e.dir, e.coll, e.key, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')), e.edge_coll, e.ekey) COLLATE "C" AS page_key FROM (
                    SELECT e.from_id AS anchor, e.to_coll AS coll, e.to_id AS key, 'outbound' AS dir, e.ekey, e.doc, e.coll AS edge_coll FROM graph.edges e
                      WHERE $7::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = ANY($4::text[])
                    UNION ALL
                    SELECT e.to_id, e.from_coll, e.from_id, 'inbound', e.ekey, e.doc, e.coll FROM graph.edges e
                      WHERE $8::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = ANY($4::text[])
                        AND NOT ($9::bool AND e.from_coll = e.to_coll AND e.from_id = e.to_id)) e
                    WHERE coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) = ANY($5::text[])
                      AND concat_ws(chr(1), e.anchor, e.dir, e.coll, e.key, coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')), e.edge_coll, e.ekey) COLLATE "C" > $10::text
                    ORDER BY page_key LIMIT $6""",
                                                          org, _LINK_COLLS, node_collection, node_keys, relationship_types, limit, out, inn, both, after), transaction)
            else:
                rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_neighbors_for_nodes_by_relationship_types */ SELECT e.anchor, e.coll, e.key, e.dir,
                    coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) AS rel, e.doc FROM (
                    SELECT e.from_id AS anchor, e.to_coll AS coll, e.to_id AS key, 'outbound' AS dir, e.ekey, e.doc FROM graph.edges e
                      WHERE $7::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = ANY($4::text[])
                    UNION ALL
                    SELECT e.to_id, e.from_coll, e.from_id, 'inbound', e.ekey, e.doc FROM graph.edges e
                      WHERE $8::bool AND e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = ANY($4::text[])
                        AND NOT ($9::bool AND e.from_coll = e.to_coll AND e.from_id = e.to_id)) e
                    WHERE coalesce(e.doc->>'relationshipType', nullif(e.ekey, '')) = ANY($5::text[]) LIMIT $6""",
                                                          org, _LINK_COLLS, node_collection, node_keys, relationship_types, limit, out, inn, both), transaction)
        except Exception:
            if raise_on_error:
                raise
            self.logger.exception("get_neighbors_for_nodes_by_relationship_types failed")
            return []
        return [{"anchorKey": r["anchor"], "direction": r["dir"], **_neighbour(r, r["key"], r["coll"]),
                 **({"cursor": r["page_key"]} if paged else {})} for r in rows]

    async def get_edges_by_target_keys(self, target_keys: list[str], edge_collection: str, filters: dict[str, Any] | None = None,
                                       return_field: str = "_from", transaction: str | None = None, *, raise_on_error: bool = False) -> list[str]:
        org = current_org_id()
        if not target_keys:
            return []
        if return_field not in ("_from", "_to"):
            raise ValueError("return_field must be '_from' or '_to'")
        ends, ids = _ends(target_keys)
        keys, vals, ins = _filters(filters)
        pcolls, kind = _edge_scope(edge_collection)
        want_from = return_field == "_from"
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_edges_by_target_keys */ SELECT DISTINCT
                  CASE WHEN $9::bool THEN e.from_coll ELSE e.to_coll END AS coll, CASE WHEN $9::bool THEN e.from_id ELSE e.to_id END AS key
                FROM unnest($3::text[], $4::text[]) t(c, i) JOIN graph.edges e ON e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_id = t.i
                AND (t.c = '' OR e.to_coll = t.c) AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))
                AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE e.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($8::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = e.doc->f.key))""", org, pcolls, ends, ids, kind, keys, vals, ins, want_from), transaction)
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
        ends, ids = _ends(source_keys)
        keys, vals, ins = _filters(filters)
        ex_keys = [_field(k) for k in (exclude or {})]
        ex_vals = [_j(v) for v in (exclude or {}).values()]      # a missing property keeps the edge
        pcolls, kind = _edge_scope(edge_collection)
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_by_source_keys */ WITH d AS (DELETE FROM graph.edges e
            USING unnest($3::text[], $4::text[]) s(c, i) WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_id = s.i AND (s.c = '' OR e.from_coll = s.c)
            AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))
            AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE e.doc->f.k IS DISTINCT FROM f.v::jsonb)
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($8::jsonb) f WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = e.doc->f.key))
            AND NOT (cardinality($9::text[]) > 0 AND EXISTS (SELECT 1 FROM unnest($9::text[], $10::text[]) x(k, v) WHERE e.doc->x.k = x.v::jsonb))
            RETURNING 1) SELECT count(*) FROM d""", org, pcolls, ends, ids, kind, keys, vals, ins, ex_keys, ex_vals), transaction, write=True) or 0)

    async def delete_edges_touching_nodes(self, node_keys: list[str], node_collection: str, edge_collection: str, transaction: str | None = None) -> int:
        org = current_org_id()
        if not node_keys:
            return 0
        pcolls, kind = _edge_scope(edge_collection)
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_touching_nodes */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = ANY($2::text[]) AND ((e.from_coll = $3 AND e.from_id = ANY($4::text[])) OR (e.to_coll = $3 AND e.to_id = ANY($4::text[])))
            AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) RETURNING 1) SELECT count(*) FROM d""", org, pcolls, node_collection, node_keys, kind), transaction, write=True) or 0)

    async def get_edge_rollup_by_file_prefix(self, org_id: str, file_path_prefix: str, relationship_types: list[str], direction: str, connector_id: str,
                                             limit: int = 100000, transaction: str | None = None) -> list[dict]:
        org = current_org_id()
        if direction not in ("outbound", "inbound", "any"):
            raise ValueError("direction must be 'outbound', 'inbound', or 'any'")
        out, inn, both = direction in ("outbound", "any"), direction in ("inbound", "any"), direction == "any"
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_edge_rollup_by_file_prefix */ WITH scoped AS (
                SELECT r.id FROM graph.nodes cf JOIN graph.nodes r ON r.org_id = cf.org_id AND r.coll = 'records' AND r.id = cf.id
                WHERE cf.org_id = $1 AND cf.coll = 'codeFiles' AND cf.doc->>'orgId' = $3 AND cf.doc->>'filePath' ~>=~ $4::text
                  AND cf.doc->>'filePath' ~<~ ($4::text || chr(1114111)) AND r.doc->>'connectorId' = $5),
              b AS (SELECT n.id, n.doc->>'recordId' AS rid FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'blocks'
                  AND n.doc->>'recordId' IN (SELECT id FROM scoped))
              SELECT l.rid AS "srcRecord", coalesce(l.doc->>'relationshipType', nullif(l.ekey, '')) AS rel, l.dir AS dir,
                     coalesce(t.doc->>'recordId', l.oi) AS "dstRecord", count(*) AS n
              FROM (SELECT b.rid, e.to_coll AS oc, e.to_id AS oi, 'outbound' AS dir, e.ekey, e.doc FROM b JOIN graph.edges e ON $8::bool AND e.org_id = $1
                      AND e.coll = ANY($2::text[]) AND e.from_coll = 'blocks' AND e.from_id = b.id
                    UNION ALL
                    SELECT b.rid, e.from_coll, e.from_id, 'inbound', e.ekey, e.doc FROM b JOIN graph.edges e ON $9::bool AND e.org_id = $1
                      AND e.coll = ANY($2::text[]) AND e.to_coll = 'blocks' AND e.to_id = b.id
                      AND NOT ($10::bool AND e.from_coll = e.to_coll AND e.from_id = e.to_id)) l
                LEFT JOIN graph.nodes t ON t.org_id = $1 AND t.coll = l.oc AND t.id = l.oi
              WHERE coalesce(l.doc->>'relationshipType', nullif(l.ekey, '')) = ANY($6::text[])
              GROUP BY 1, 2, 3, 4 LIMIT $7""", org, _LINK_COLLS, org_id, file_path_prefix, connector_id, relationship_types, limit, out, inn, both), transaction)
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
          -- one DELETE: an edge with both ends in b (blocks of one file, a self-loop) must not be the target of two sub-statements
          de AS (DELETE FROM graph.edges e WHERE e.org_id = $1 AND ((e.from_coll = 'blocks' AND e.from_id IN (SELECT id FROM b))
                 OR (e.to_coll = 'blocks' AND e.to_id IN (SELECT id FROM b))) RETURNING 1),
          d AS (DELETE FROM graph.nodes n USING b WHERE n.org_id = $1 AND n.coll = 'blocks' AND n.id = b.id RETURNING 1)
          SELECT count(*) FROM d""", org, record_ids), transaction, write=True) or 0)

    async def backfill_block_name_folded(self, limit: int = 5000) -> int:
        current_or_system()
        done = 0
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():     # ``limit`` bounds the whole pass, as one query does on Neo4j
            if done >= limit:
                break
            with self._sweep(org_id, errors):
                org = current_org_id()
                done += int(await self._q(lambda tx, org=org, n=limit - done: tx.fetchval("""/* pipeshub:graph.backfill_block_name_folded */ WITH u AS (UPDATE graph.nodes n
                SET doc = n.doc || jsonb_build_object('nameFolded', lower(n.doc->>'name')) WHERE n.org_id = $1 AND n.coll = 'blocks' AND n.id IN (
                    SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'blocks' AND doc->>'nameFolded' IS NULL AND doc->>'name' IS NOT NULL LIMIT $2)
                RETURNING 1) SELECT count(*) FROM u""", org, n), None, write=True) or 0)
        self._raise_failed(errors)
        return done

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
            UPDATE graph.nodes n SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(n.doc || $4::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb) FROM l WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = l.id RETURNING n.id""",
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
            VALUES ($1, $2, $3, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each($4::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb)) ON CONFLICT (org_id, coll, id) DO NOTHING RETURNING 1) SELECT count(*) FROM i""",
                                                  org, _STAGES, document["id"], _j(document)), None, write=True))

    async def stage_state_compare_and_set(self, key: str, expected: str, new: str, fields: dict[str, Any], *, expected_attempt: int | None = None) -> bool:
        org = current_org_id()
        return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.stage_state_compare_and_set */ WITH u AS (UPDATE graph.nodes n
            SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(n.doc || $5::jsonb || jsonb_build_object('status', $4::text)) AS _nv WHERE _nv.value <> 'null'::jsonb)
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
        current_or_system()
        out: list[dict[str, Any]] = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                rows = await self._q(lambda tx, org=org: tx.fetch("""/* pipeshub:graph.stage_states_stale */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2
                    AND doc->>'status' = ANY($3::text[]) AND doc->'updatedAtMs' < to_jsonb($4::bigint) ORDER BY doc->'updatedAtMs' LIMIT $5""",
                                                          org, _STAGES, statuses, updated_before_ms, limit), None)
                out += [{**_doc(r["doc"]), "orgId": org} for r in rows]    # the row's org, not the document's own claim
        self._raise_failed(errors)
        return sorted(out, key=lambda d: d.get("updatedAtMs") or 0)[:limit]

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
        current_or_system()
        out = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                out += [{"virtualRecordId": d["id"], "orgId": org, "documentId": d.get("documentId"), "switchSeq": d.get("switchSeq"),
                         "lastSwitchAt": d.get("lastSwitchAt"), "retired": _retired(d.get("retired"))} for d in await self._pointer_page(after_key, limit)]
        self._raise_failed(errors)
        return _page_whole_ids(out, limit)

    async def get_revision_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        current_or_system()
        out = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                out += [{"virtualRecordId": d["id"], "orgId": org, "currentRev": d.get("currentRev"), "previousRev": d.get("previousRev")}
                        for d in await self._pointer_page(after_key, limit)]
        self._raise_failed(errors)
        return _page_whole_ids(out, limit)

    async def stage_states_unheld_revisions(self, virtual_record_ids: list[str], *, updated_before_ms: int) -> list[tuple[str, str]]:
        """``(virtualRecordId, rev)`` with stage states, held by no record (``contentRev``, trash included), newest state older than the cut-off."""
        current_or_system()
        ids = sorted({v for v in virtual_record_ids if v})
        if not ids:
            return []
        out: list[tuple[str, str]] = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                rows = await self._q(lambda tx, org=org: tx.fetch("""/* pipeshub:graph.stage_states_unheld_revisions */ WITH g AS (SELECT s.doc->>'virtualRecordId' AS v, s.doc->>'rev' AS rev,
                        max(coalesce((s.doc->>'updatedAtMs')::numeric, 0)) AS newest FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = $2
                        AND s.doc->>'virtualRecordId' = ANY($3::text[]) AND s.doc->>'rev' IS NOT NULL GROUP BY 1, 2)
                    SELECT g.v, g.rev FROM g WHERE g.newest < $4::bigint AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'virtualRecordId' = g.v AND r.doc->>'contentRev' = g.rev) ORDER BY g.v, g.rev""", org, _STAGES, ids, updated_before_ms), None)
                out += [(r["v"], r["rev"]) for r in rows]
        self._raise_failed(errors)
        return sorted(out)

    async def stage_states_delete_unheld_revisions(self, revisions: list[tuple[str, str]], *, updated_before_ms: int) -> int:
        """Delete the stage states of each pair that still meets the rule above, decided again inside the delete.

        The states are locked first and the rule is read by a second statement: under READ COMMITTED the DELETE alone re-checks only the row it
        locks, not the age or the holder its own subquery computed, so a state claimed (updated) while this waited would be deleted."""
        current_or_system()
        pairs = sorted({(v, r) for v, r in revisions if v and r})
        if not pairs:
            return 0
        deleted = 0
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()

                async def run(tx: Any, org: str = org) -> int:  # noqa: ANN401
                    vs, rs = [v for v, _ in pairs], [r for _, r in pairs]
                    await tx.fetch("""/* pipeshub:graph.stage_states_lock_revisions */ SELECT s.id FROM graph.nodes s JOIN unnest($3::text[], $4::text[]) p(v, rev)
                        ON s.doc->>'virtualRecordId' = p.v AND s.doc->>'rev' = p.rev WHERE s.org_id = $1 AND s.coll = $2 ORDER BY s.id FOR UPDATE OF s""",
                                   org, _STAGES, vs, rs)
                    return await tx.fetchval("""/* pipeshub:graph.stage_states_delete_unheld_revisions */ WITH g AS (SELECT p.v, p.rev,
                            (SELECT max(coalesce((s.doc->>'updatedAtMs')::numeric, 0)) FROM graph.nodes s WHERE s.org_id = $1 AND s.coll = $2
                               AND s.doc->>'virtualRecordId' = p.v AND s.doc->>'rev' = p.rev) AS newest
                          FROM unnest($3::text[], $4::text[]) AS p(v, rev)),
                        ok AS (SELECT g.v, g.rev FROM g WHERE g.newest < $5::bigint AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'virtualRecordId' = g.v AND r.doc->>'contentRev' = g.rev)),
                        d AS (DELETE FROM graph.nodes s USING ok WHERE s.org_id = $1 AND s.coll = $2 AND s.doc->>'virtualRecordId' = ok.v AND s.doc->>'rev' = ok.rev RETURNING 1)
                        SELECT count(*) FROM d""", org, _STAGES, vs, rs, updated_before_ms)
                deleted += int(await self._q(run, None, write=True) or 0)
        self._raise_failed(errors)
        return deleted
