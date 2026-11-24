"""A knowledge-base folder's listing from the typed graph: ``get_folder_children`` filters, orders and counts the folder's children by their ``kg.records`` row (the columns
``model.kb_listing`` keeps for it) instead of reading every child's document and the file it is typed as. Only the page's rows are read from ``graph.nodes``, as the walk shapes them.

The children are the folder's PARENT_CHILD children that kg holds: those whose primary parent is the folder and that have a PARENT_CHILD parent record (``NOT pc_top``: the primary
parent ranks a PARENT_CHILD one first, so it is one), and those the folder parents by a hierarchy link that is a PARENT_CHILD one (kind 3). The page is filtered, ordered,
counted and shaped as the walk's statements (``KbListingsMixin``) do it, so an answer differs from the walk's only if kg's columns do. The walk answers (``hierarchy.answer``) when the
switch is off, inside a caller's own transaction, for the system role, for a connector kg does not serve, for a folder kg holds no row for or that is another connector's, when a
child is another connector's or holds a value kg's columns cannot hold as the walk reads it (``kb_odd``), and on any error; a share of the answers is compared with the walk's.

shortcut: every child's row is read to order, filter and count them (one range of the children index, typed columns only: about 50,000 rows in a tenth of a second); a folder of
millions would want an index per sort key. A child kg never holds (an id longer than its writers take) is not listed, as for the root (kg/kb_root.py).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.hierarchy import Fallback, _id, _node, _served
from app.services.graph_db.postgres.kg.kb_root import _TYPE, _edges
from app.services.graph_db.postgres.kg.model import FILE_DIR

if TYPE_CHECKING:
    from app.core.db import Tx

_RECS, _FILES = "'records'", "'files'"


def _kids(x: str) -> str:
    """The PARENT_CHILD children of the record of sk ``x`` that kg holds, live or not, each a seek of its key (as hierarchy.py spells them: a key read as a range of one value)."""
    return (f"(SELECT c.* FROM kg.records c WHERE c.org_id = $1 AND c.parent_sk >= {x} AND c.parent_sk <= {x} AND c.kind = 3 AND NOT c.pc_top ORDER BY c.parent_sk, c.sk) "
            f"UNION ALL (SELECT c.* FROM (SELECT l.child_sk FROM kg.links l WHERE l.org_id = $1 AND (l.parent_sk, l.kind) >= ({x}, 1) AND (l.parent_sk, l.kind) <= ({x}, 1) "
            f"ORDER BY l.parent_sk, l.kind, l.child_sk) l CROSS JOIN LATERAL (SELECT * FROM (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk >= l.child_sk ORDER BY k.sk LIMIT 1) s "
            f"WHERE s.sk = l.child_sk AND s.kind = 3 AND s.parent_sk IS DISTINCT FROM {x} OFFSET 0) c "
            # OFFSET 0: a probe of the primary key per link; as a semi-join PostgreSQL 18 read the organisation's kind-3 links by links_child
            f"WHERE EXISTS (SELECT 1 FROM kg.links p WHERE p.org_id = $1 AND p.parent_sk = {x} AND p.kind = 3 AND p.child_sk = l.child_sk OFFSET 0))")


# A child kg cannot answer for as the walk reads it: another connector's ($3, whose rows may wait in its own log) or one with a value its columns cannot hold.
_ODD = "bool_or(k.kb_odd OR k.app_sk IS DISTINCT FROM $3)"
FOLDER = f"/* pipeshub:kg.kb_folder */ SELECT r.sk, r.app_sk, r.file_kind, r.kb_odd FROM {_id('3', '$2')} r"

# The walk's subfolders (``graph.children_folder_folders``) of the folder $2 (an sk; $7: it is typed as a folder itself), by name, each page folder with its subfolders and live
# records and the path of its folder file read as the walk reads them.
FOLDERS = f"""/* pipeshub:kg.kb_folder_folders */ WITH kids AS MATERIALIZED ({_kids("$2")}),
    fo AS MATERIALIZED (SELECT k.sk, k.id, k.name FROM kids k WHERE $7 AND k.file_kind = {FILE_DIR} AND ($4::text IS NULL OR position(lower($4::text) IN lower(k.name)) > 0))
    SELECT (SELECT count(*) FROM fo) AS total, (SELECT coalesce({_ODD}, false) FROM kids k) AS odd, p.doc, p.path, p.subs, p.recs, p.odd AS sub_odd, p.rn FROM (SELECT 1) one
    LEFT JOIN LATERAL (SELECT n.doc, q.rn, cnt.subs, cnt.recs, cnt.odd,
                              (SELECT f.doc->>'path' FROM {_edges('isOfType', 'from_id', 'q.id', 'e.from_coll, e.to_id')} te CROSS JOIN LATERAL {_node(_FILES, 'te.to_id')} f
                               WHERE te.from_coll = 'records' AND f.doc->>'isFile' = 'false' ORDER BY f.id LIMIT 1) AS path
                       FROM (SELECT x.sk, x.id, row_number() OVER (ORDER BY x.name COLLATE "C", x.id) AS rn FROM fo x ORDER BY x.name COLLATE "C", x.id OFFSET $5 LIMIT $6) q
                       CROSS JOIN LATERAL {_node(_RECS, 'q.id')} n
                       CROSS JOIN LATERAL (SELECT count(*) FILTER (WHERE k.file_kind = {FILE_DIR}) AS subs,
                                                  count(*) FILTER (WHERE NOT k.deleted AND (k.file_kind IS NULL OR k.file_kind IN (1, 2))) AS recs, coalesce({_ODD}, false) AS odd
                                           FROM ({_kids("q.sk")}) k) cnt) p ON true ORDER BY p.rn"""
# The walk's records (``graph.children_folder_records``) of the folder $2, with the filters they offer and a page in the asked order ($9 the key, $10 ascending).
RECORDS_PAGE = f"""/* pipeshub:kg.kb_folder_records */ WITH kids AS MATERIALIZED ({_kids("$2")}),
    recs AS MATERIALIZED (SELECT k.sk, k.id, k.name, k.record_type, k.origin, k.connector_name, k.indexing_status, k.doc_created_ms, k.doc_updated_ms, k.file_size FROM kids k
      WHERE NOT k.deleted AND k.file_kind IS DISTINCT FROM {FILE_DIR}
        AND ($4::text IS NULL OR position(lower($4::text) IN lower(k.name)) > 0 OR position(lower($4::text) IN lower(k.external_id)) > 0)
        AND ($5::text[] IS NULL OR k.record_type = ANY($5::text[])) AND ($6::text[] IS NULL OR k.origin = ANY($6::text[]))
        AND ($7::text[] IS NULL OR k.connector_name = ANY($7::text[])) AND ($8::text[] IS NULL OR k.indexing_status = ANY($8::text[])))
    SELECT s.total, o.odd, s.rtypes, s.origins, s.connectors, s.statuses, p.doc, p.fdoc, p.rn FROM
      (SELECT count(*) AS total,
              coalesce(array_agg(DISTINCT record_type) FILTER (WHERE record_type IS NOT NULL), '{{}}') AS rtypes,
              coalesce(array_agg(DISTINCT origin) FILTER (WHERE origin IS NOT NULL), '{{}}') AS origins,
              coalesce(array_agg(DISTINCT coalesce(connector_name, 'KNOWLEDGE_BASE')), '{{}}') AS connectors,
              coalesce(array_agg(DISTINCT indexing_status) FILTER (WHERE indexing_status IS NOT NULL), '{{}}') AS statuses FROM recs) s
      CROSS JOIN (SELECT coalesce({_ODD}, false) AS odd FROM kids k) o
      LEFT JOIN LATERAL (SELECT n.doc, q.rn, {_TYPE.format(x="q.id")} AS fdoc
                         FROM (SELECT x.id, row_number() OVER (ORDER BY
            (CASE WHEN $9::int = 1 AND $10::boolean THEN x.name END) COLLATE "C" ASC, (CASE WHEN $9::int = 1 AND NOT $10::boolean THEN x.name END) COLLATE "C" DESC,
            (CASE WHEN $9::int = 2 AND $10::boolean THEN x.doc_created_ms END) ASC, (CASE WHEN $9::int = 2 AND NOT $10::boolean THEN x.doc_created_ms END) DESC,
            (CASE WHEN $9::int = 3 AND $10::boolean THEN x.doc_updated_ms END) ASC, (CASE WHEN $9::int = 3 AND NOT $10::boolean THEN x.doc_updated_ms END) DESC,
            (CASE WHEN $9::int = 4 AND $10::boolean THEN x.file_size END) ASC, (CASE WHEN $9::int = 4 AND NOT $10::boolean THEN x.file_size END) DESC, x.id) AS rn
                               FROM recs x ORDER BY rn OFFSET $11 LIMIT $12) q CROSS JOIN LATERAL {_node(_RECS, 'q.id')} n) p ON true ORDER BY p.rn"""


async def folder(tx: Tx, org: str, kb_id: str, folder_id: str) -> tuple[int, int, bool]:
    """(the folder's sk, the knowledge base's app sk, whether the folder is typed as a folder) for a folder kg answers for. Raises Fallback."""
    app_sk = await _served(tx, org, kb_id)
    row = await tx.fetchrow(FOLDER, org, folder_id)
    if row is None:
        raise Fallback("unknown")
    if row["app_sk"] != app_sk or row["kb_odd"]:
        raise Fallback("odd")
    return row["sk"], app_sk, row["file_kind"] == FILE_DIR


def checked(rows: list, *odd: str) -> None:
    """Fallback when a row of a statement of this module says the walk must answer (its ``odd`` columns)."""
    if any(r[c] for r in rows for c in odd):
        raise Fallback("odd")
