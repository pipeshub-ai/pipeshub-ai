"""A knowledge base's root from the typed graph: ``get_kb_children`` takes its candidates from the knowledge base's records that have no PARENT_CHILD parent that is a record
(``kg.records.pc_top``, kg_0024), read by ``records_pc_tops``, instead of from every record of the organisation.

Only which records are candidates is kg's. Each candidate is read from ``graph.nodes`` and held to the walk's own predicates, and the page is filtered, ordered, counted and
shaped as the walk's statements do it (``KbListingsMixin``, spelt here as seeks by key), so an answer differs from the walk's only if kg leaves out a record the walk lists. For a connector kg serves
(verified, enforced, nothing waiting in the log) the candidates hold every such record: one of the knowledge base's connector that has no PARENT_CHILD relation from a record kg
holds is ``pc_top``, and the walk lists no record with a PARENT_CHILD relation from a record, held or not (a relation from a node the graph no longer holds keeps a candidate off
the page, by the walk's predicate). The walk answers (``hierarchy.answer``) when the switch is off, inside a caller's own transaction, for the system role, for a connector kg
does not serve and on any error; a share of the answers is compared with the walk's, and a difference takes the connector back to the walk.

shortcut: a record kg never holds (an id longer than its writers take) or one whose ``connectorId`` names another connector than the knowledge base it belongs to is no candidate;
a knowledge base's records carry generated ids and its own connector. Read the knowledge base's belongs-to edges as well if another writer ever breaks that.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.hierarchy import _node, _served

if TYPE_CHECKING:
    from app.core.db import Tx

# The live records of the connector $2 (an sk) with no PARENT_CHILD parent that is a record, in the order of their index (sorts are off in kg's read unit).
TOPS = """/* pipeshub:kg.kb_tops */ SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND r.kind = 3 AND r.pc_top AND NOT r.deleted ORDER BY r.sk"""


async def candidates(tx: Tx, org: str, kb_id: str) -> list[str]:
    """The ids of the knowledge base's root candidates. Raises Fallback."""
    app_sk = await _served(tx, org, kb_id)
    return [r["id"] for r in await tx.fetch(TOPS, org, app_sk)]


RECORDS, APPS, FILES = "'records'", "'apps'", "'files'"


def _edges(coll: str, end: str, x: str, cols: str) -> str:
    """The edges of ``coll`` whose ``end`` (from_id or to_id) is ``x``: a range of one value of its index, kept on that row (OFFSET 0)."""
    return f"(SELECT {cols} FROM graph.edges e WHERE e.org_id = $1 AND e.coll = '{coll}' AND e.{end} >= {x} AND e.{end} <= {x} ORDER BY e.{end} OFFSET 0)"


_CANDIDATES = f"unnest({{ids}}::text[]) AS q(id) CROSS JOIN LATERAL {_node(RECORDS, 'q.id')} r"
# The walk's root (``KbListingsMixin``): live, of the organisation, belonging to the knowledge base $2 (an App of type {kb}), with no PARENT_CHILD relation from a record.
_ROOT = (f"coalesce((r.doc->>'isDeleted')::boolean, false) = false AND r.doc->>'orgId' = $1 "
         f"AND EXISTS (SELECT 1 FROM {_edges('belongsTo', 'from_id', 'r.id', 'e.from_coll, e.to_coll, e.to_id')} b CROSS JOIN LATERAL {_node(APPS, 'b.to_id')} k "
         f"WHERE b.from_coll = 'records' AND b.to_coll = 'apps' AND b.to_id = $2 AND k.doc->>'type' = {{kb}}) "
         f"AND NOT EXISTS (SELECT 1 FROM {_edges('nodeRelations', 'to_id', 'r.id', 'e.from_coll, e.to_coll, e.doc')} p "
         f"WHERE p.doc->>'relationshipType' = 'PARENT_CHILD' AND p.from_coll = 'records' AND p.to_coll = 'records')")
# A record's first type document by id, as the walk reads it.
_TYPE = (f"(SELECT ff.doc FROM {_edges('isOfType', 'from_id', '{x}', 'e.from_coll, e.to_id')} te CROSS JOIN LATERAL {_node(FILES, 'te.to_id')} ff "
         f"WHERE te.from_coll = 'records' ORDER BY ff.id LIMIT 1)")

# The walk's root folders (``graph.children_kb_folders``) among the candidates $8: the page by name, each page folder with its subfolders and live records.
FOLDERS = f"""/* pipeshub:kg.kb_root_folders */ WITH fo AS MATERIALIZED (
      SELECT r.id, r.doc FROM {_CANDIDATES.format(ids="$8")} WHERE r.doc->>'mimeType' = ANY($3::text[]) AND {_ROOT.format(kb="$7")}
        AND ($4::text IS NULL OR position(lower($4::text) IN lower(r.doc->>'recordName')) > 0))
    SELECT (SELECT count(*) FROM fo) AS total, p.doc, cnt.subs, cnt.recs, p.rn FROM (SELECT 1) one
    LEFT JOIN LATERAL (SELECT x.id, x.doc, row_number() OVER (ORDER BY x.doc->>'recordName' COLLATE "C", x.id) AS rn FROM fo x
                       ORDER BY x.doc->>'recordName' COLLATE "C", x.id OFFSET $5 LIMIT $6) p ON true
    LEFT JOIN LATERAL (SELECT count(*) FILTER (WHERE c.doc->>'mimeType' = ANY($3::text[])) AS subs,
                              count(*) FILTER (WHERE coalesce((c.doc->>'isDeleted')::boolean, false) = false AND NOT (c.doc->>'mimeType' = ANY($3::text[]))) AS recs
                       FROM {_edges('nodeRelations', 'from_id', 'p.id', 'e.from_coll, e.to_coll, e.to_id, e.doc')} ce CROSS JOIN LATERAL {_node(RECORDS, 'ce.to_id')} c
                       WHERE ce.doc->>'relationshipType' = 'PARENT_CHILD' AND ce.from_coll = 'records' AND ce.to_coll = 'records') cnt ON true ORDER BY p.rn"""
# The walk's root records (``graph.children_records`` with no folder) among the candidates $14, with the filters they offer and a page in the asked order.
RECORDS = f"""/* pipeshub:kg.kb_root_records */ WITH recs AS MATERIALIZED (
      SELECT r.id, r.doc FROM {_CANDIDATES.format(ids="$14")} WHERE NOT (r.doc->>'mimeType' = ANY($3::text[])) AND {_ROOT.format(kb="$13")}
        AND ($4::text IS NULL OR position(lower($4::text) IN lower(r.doc->>'recordName')) > 0 OR position(lower($4::text) IN lower(r.doc->>'externalRecordId')) > 0)
        AND ($5::text[] IS NULL OR r.doc->>'recordType' = ANY($5::text[])) AND ($6::text[] IS NULL OR r.doc->>'origin' = ANY($6::text[]))
        AND ($7::text[] IS NULL OR r.doc->>'connectorName' = ANY($7::text[])) AND ($8::text[] IS NULL OR r.doc->>'indexingStatus' = ANY($8::text[])))
    SELECT s.total, s.rtypes, s.origins, s.connectors, s.statuses, p.doc, p.fdoc, p.rn FROM
      (SELECT count(*) AS total,
              coalesce(array_agg(DISTINCT doc->>'recordType') FILTER (WHERE doc->>'recordType' IS NOT NULL), '{{}}') AS rtypes,
              coalesce(array_agg(DISTINCT doc->>'origin') FILTER (WHERE doc->>'origin' IS NOT NULL), '{{}}') AS origins,
              coalesce(array_agg(DISTINCT coalesce(doc->>'connectorName', 'KNOWLEDGE_BASE')), '{{}}') AS connectors,
              coalesce(array_agg(DISTINCT doc->>'indexingStatus') FILTER (WHERE doc->>'indexingStatus' IS NOT NULL), '{{}}') AS statuses FROM recs) s
      LEFT JOIN LATERAL (SELECT q.doc, q.rn, {_TYPE.format(x="q.id")} AS fdoc
                         FROM (SELECT x.id, x.doc, row_number() OVER (ORDER BY
            (CASE WHEN $9::int = 1 AND $10::boolean THEN x.doc->>'recordName' END) COLLATE "C" ASC, (CASE WHEN $9::int = 1 AND NOT $10::boolean THEN x.doc->>'recordName' END) COLLATE "C" DESC,
            (CASE WHEN $9::int = 2 AND $10::boolean THEN x.doc->'createdAtTimestamp' END) ASC, (CASE WHEN $9::int = 2 AND NOT $10::boolean THEN x.doc->'createdAtTimestamp' END) DESC,
            (CASE WHEN $9::int = 3 AND $10::boolean THEN x.doc->'updatedAtTimestamp' END) ASC, (CASE WHEN $9::int = 3 AND NOT $10::boolean THEN x.doc->'updatedAtTimestamp' END) DESC,
            (CASE WHEN $9::int = 4 AND $10::boolean THEN {_TYPE.format(x="x.id")}->'sizeInBytes' END) ASC,
            (CASE WHEN $9::int = 4 AND NOT $10::boolean THEN {_TYPE.format(x="x.id")}->'sizeInBytes' END) DESC, x.id) AS rn
                               FROM recs x ORDER BY rn OFFSET $11 LIMIT $12) q) p ON true ORDER BY p.rn"""
