"""Writes: upsert, batched delete, payload and membership. Every function takes the org explicitly and runs in the unit of work
of the caller (one transaction per batch). Statements are single round trips: arrays bound as parameters, expanded with ``unnest``
(COPY is refused under RLS, and a temp table needs a privilege the service roles do not hold).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.permissions.scope_fields import (
    ACL_SCOPE_FIELD,
    PATH_IDS_FIELD,
    SCOPE_GENERATION_FIELD,
    SCOPE_KEYS,
    SCOPE_STAMP_FIELD,
    SCOPE_VERSION_FIELD,
)
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.pgvector.errors import (
    OrgMismatch,
    OrgRequired,
    PgVectorError,
)
from app.services.vector_db.pgvector.scope import require_org
from app.services.vector_db.pgvector.sql_filters import Compiled

if TYPE_CHECKING:
    from datetime import datetime

    from app.core.db import Tx
    from app.services.vector_db.models import VectorPoint

UPSERT_BATCH = 500          # points per unit of work (one org, one collection, one space)
DELETE_BATCH = 5000         # rows per transaction, keyed on (org_id, point_sk)
MEMBERSHIP_KEYS = (CONNECTOR_IDS_FIELD, RECORD_GROUP_IDS_FIELD, ROOT_RECORD_GROUP_IDS_FIELD)
# group-level too: the permission scope fields of a virtual record, written with the membership arrays behind the same fence
GROUP_KEYS = (*MEMBERSHIP_KEYS, *SCOPE_KEYS)
# metadata keys that live in columns: a payload merge must not change them (the columns would go stale)
PROMOTED_METADATA = ("orgId", "virtualRecordId", "blockId", "entityType", "entityId", "kind", "level")
# The change in a collection's point count is a row of ``lexical_df_delta`` under this term (term ids are never negative); the fold
# job compacts them and the search adds the unfolded ones.
COUNT_TERM = -1


@dataclass
class Prepared:
    """The columns of a batch of points, one list per column."""

    org: str
    point_ids: list[uuid.UUID]
    group_keys: list[str]
    vrids: list[str | None]
    block_ids: list[str | None]
    ent_types: list[str | None]
    ent_ids: list[str | None]
    ent_kinds: list[str | None]
    ent_levels: list[str | None]
    embeddings: list[str]
    dims: list[int]
    models: list[str | None]
    term_start: list[int]
    term_end: list[int]
    terms: list[int]
    weights: list[float]
    payloads: list[str]
    contents: list[str | None]
    groups: dict[str, dict[str, list[str]]]       # group_key -> membership arrays carried by the points


def _text(v: object) -> str | None:
    return None if v is None else str(v)


def halfvec_text(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in vec) + "]"


def _ids(value: object) -> list[str]:
    if not value:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def prepare(points: list[VectorPoint]) -> Prepared:
    """Columns from points. One org per batch, named by every point's ``metadata.orgId``; the last point wins on a repeated id."""
    by_id: dict[uuid.UUID, VectorPoint] = {}
    for p in points:
        try:
            key = uuid.UUID(str(p.id))
            by_id.pop(key, None)             # a repeated id moves to the end, so the iteration below follows each point's last occurrence
            by_id[key] = p
        except ValueError as e:
            raise PgVectorError(f"point id {p.id!r} is not a UUID") from e
    org: str | None = None
    out = Prepared("", [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], [], {})
    for pid, p in by_id.items():
        payload = p.payload or {}
        meta = payload.get("metadata") or {}
        point_org = require_org(meta.get("orgId"))
        if org is not None and point_org != org:
            raise OrgMismatch("a batch of points must belong to one organisation")
        org = point_org
        if not p.dense_vector:
            raise PgVectorError(f"point {pid} has no dense vector")
        vrid = _text(meta.get("virtualRecordId"))
        out.point_ids.append(pid)
        out.group_keys.append(vrid or str(pid))          # records group by vrid, entities are their own group
        out.vrids.append(vrid)
        out.block_ids.append(_text(meta.get("blockId")))
        out.ent_types.append(_text(meta.get("entityType")))
        out.ent_ids.append(_text(meta.get("entityId")))
        out.ent_kinds.append(_text(meta.get("kind")))
        out.ent_levels.append(_text(meta.get("level")))
        out.embeddings.append(halfvec_text(p.dense_vector))
        out.dims.append(len(p.dense_vector))
        out.models.append(_text(meta.get("embeddingModel")))
        out.term_start.append(len(out.terms))
        if p.sparse_vector and p.sparse_vector.indices:
            terms = dict(zip((int(i) for i in p.sparse_vector.indices), (float(v) for v in p.sparse_vector.values), strict=True))
            out.terms.extend(terms)          # one entry per term: a repeated index would count twice in the document frequency
            out.weights.extend(terms.values())
        out.term_end.append(len(out.terms))
        stored = {k: v for k, v in payload.items() if k != "page_content" and k not in GROUP_KEYS}
        out.payloads.append(json.dumps(stored, separators=(",", ":")))
        out.contents.append(payload.get("page_content"))
        if any(k in payload for k in GROUP_KEYS):
            # Only the arrays a payload carries, merged per key over the batch's other points of the same group (a group is one record, so its
            # points should agree; where they do not, the later point of the batch wins that key).
            out.groups.setdefault(out.group_keys[-1], {}).update(membership_of(payload))
    if org is None:
        raise OrgRequired("no points to write")
    out.org = org
    return out


def wrap(col: str) -> str:
    """A 32-bit term id as the signed integer the postings store (ids above 2^31 - 1 wrap by 2^32; the search does the same)."""
    return f"(CASE WHEN {col} > 2147483647 THEN {col} - 4294967296 ELSE {col} END)::int"


_INCOMING = """incoming AS (
          SELECT * FROM unnest($4::uuid[], $5::text[], $6::text[], $7::text[], $8::text[], $9::text[], $10::text[], $11::text[],
                               $12::text[], $13::int[], $14::int[], $17::text[], $18::text[])
            AS t(point_id, group_key, vrid, block_id, ent_type, ent_id, ent_kind, ent_level, emb, ts, te, payload, content)
          WHERE t.point_id = ANY($19::uuid[]))"""
_NEW_TERMS = "($15::bigint[])[i.ts + 1 : i.te]"
_TERMS_OF = f"unnest({_NEW_TERMS}, ($16::real[])[i.ts + 1 : i.te]) AS t(term, w)"
_COUNT = ("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) "
          f"SELECT $1, $2, $3, {COUNT_TERM}, count(*) FROM %s HAVING count(*) > 0")      # the points counted are those that have terms

# New points: the insert is the only writer of a point that does not exist yet (a concurrent writer of the same point waits for it
# on the unique key and then inserts nothing), so the postings and the count are exactly those of the rows this statement inserted.
_INSERT_NEW = f"""/* pipeshub:pgvector.upsert_new */
        WITH {_INCOMING},
        ins AS (
          INSERT INTO retrieval.points AS pts (org_id, collection_id, space_id, point_id, group_key, vrid, block_id, ent_type, ent_id,
                                               ent_kind, ent_level, embedding, embedding_dim, lex_terms)
          SELECT $1, $2, $3, i.point_id, i.group_key, i.vrid, i.block_id, i.ent_type, i.ent_id, i.ent_kind, i.ent_level,
                 i.emb::ext.halfvec, ext.vector_dims(i.emb::ext.halfvec)::smallint, CASE WHEN i.te > i.ts THEN {_NEW_TERMS} END
          FROM incoming i ORDER BY i.point_id       -- one order for every writer, so two batches of the same new points cannot deadlock
          ON CONFLICT (org_id, collection_id, space_id, point_id) DO NOTHING
          RETURNING pts.point_sk, pts.point_id),
        txt AS (
          INSERT INTO retrieval.point_text (org_id, point_sk, page_content, payload)
          SELECT $1, ins.point_sk, i.content, i.payload::jsonb FROM ins JOIN incoming i ON i.point_id = ins.point_id
          RETURNING 1),
        post AS (
          INSERT INTO retrieval.postings (org_id, collection_id, space_id, term, point_sk, w)
          SELECT $1, $2, $3, {wrap('t.term')}, ins.point_sk, t.w
            FROM ins JOIN incoming i ON i.point_id = ins.point_id CROSS JOIN LATERAL {_TERMS_OF} WHERE i.te > i.ts
           ORDER BY 4, 5         -- in key order: the index is filled page by page instead of at random
          RETURNING 1),
        cnt AS ({_COUNT % '(SELECT 1 FROM ins JOIN incoming i ON i.point_id = ins.point_id WHERE i.te > i.ts) x'} RETURNING 1)
        SELECT point_id FROM ins"""

_LOCK_OLD = """/* pipeshub:pgvector.upsert_lock */
        SELECT p.point_sk FROM retrieval.points p
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_id = ANY($4::uuid[])
        ORDER BY p.point_sk FOR UPDATE"""

# Points that exist (their rows are locked by the statement above, in key order, so the terms read here are the committed ones and no
# other writer changes them until this transaction ends): the postings follow the difference between the old and the new terms.
_UPDATE_OLD = f"""/* pipeshub:pgvector.upsert_existing */
        WITH {_INCOMING},
        old AS (
          SELECT p.point_sk, p.point_id, p.lex_terms FROM retrieval.points p JOIN incoming i ON i.point_id = p.point_id
          WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_id = ANY($19::uuid[])),
        upd AS (          -- only a row that differs is rewritten: a re-index of unchanged content makes no new row version and no graph insert (the row is still locked, which is a small WAL record)
          UPDATE retrieval.points p
             SET group_key = i.group_key, vrid = i.vrid, block_id = i.block_id, ent_type = i.ent_type, ent_id = i.ent_id,
                 ent_kind = i.ent_kind, ent_level = i.ent_level, embedding = i.emb::ext.halfvec,
                 embedding_dim = ext.vector_dims(i.emb::ext.halfvec)::smallint, lex_terms = CASE WHEN i.te > i.ts THEN {_NEW_TERMS} END
            FROM incoming i
           WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_id = i.point_id AND p.point_id = ANY($19::uuid[])
             AND ((p.group_key, p.vrid, p.block_id, p.ent_type, p.ent_id, p.ent_kind, p.ent_level, p.lex_terms)
                  IS DISTINCT FROM (i.group_key, i.vrid, i.block_id, i.ent_type, i.ent_id, i.ent_kind, i.ent_level,
                                    CASE WHEN i.te > i.ts THEN {_NEW_TERMS} END)
                  OR CASE WHEN p.embedding_dim = ext.vector_dims(i.emb::ext.halfvec)       -- halfvec has no equality: zero distance is equal
                          THEN (p.embedding OPERATOR(ext.<->) i.emb::ext.halfvec) <> 0 ELSE true END)
          RETURNING p.point_sk, p.point_id),
        txt AS (
          INSERT INTO retrieval.point_text AS pt (org_id, point_sk, page_content, payload)
          SELECT $1, o.point_sk, i.content, i.payload::jsonb FROM old o JOIN incoming i ON i.point_id = o.point_id
          ON CONFLICT (org_id, point_sk) DO UPDATE SET page_content = EXCLUDED.page_content, payload = EXCLUDED.payload
           WHERE (pt.page_content, pt.payload) IS DISTINCT FROM (EXCLUDED.page_content, EXCLUDED.payload)
          RETURNING 1),
        newp AS (
          SELECT o.point_sk, {wrap('t.term')} AS term, t.w
            FROM old o JOIN incoming i ON i.point_id = o.point_id CROSS JOIN LATERAL {_TERMS_OF} WHERE i.te > i.ts),
        dropped AS (
          DELETE FROM retrieval.postings q
           USING (SELECT o.point_sk, {wrap('t.term')} AS term FROM old o CROSS JOIN LATERAL unnest(o.lex_terms) AS t(term)
                  EXCEPT SELECT n.point_sk, n.term FROM newp n) s
           WHERE q.org_id = $1 AND q.collection_id = $2 AND q.space_id = $3 AND q.point_sk = s.point_sk AND q.term = s.term
          RETURNING 1),
        post AS (
          INSERT INTO retrieval.postings AS q (org_id, collection_id, space_id, term, point_sk, w)
          SELECT $1, $2, $3, n.term, n.point_sk, n.w FROM newp n ORDER BY n.term, n.point_sk
          ON CONFLICT (org_id, collection_id, space_id, term, point_sk) DO UPDATE SET w = EXCLUDED.w WHERE q.w IS DISTINCT FROM EXCLUDED.w
          RETURNING 1),
        cnt AS (          -- a point that gains or loses all its terms enters or leaves the count; a tombstoned one is counted by the revive below
          INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d)
          SELECT $1, $2, $3, {COUNT_TERM}, z.d FROM (
            SELECT (count(*) FILTER (WHERE i.te > i.ts) - count(*) FILTER (WHERE o.lex_terms IS NOT NULL)) AS d
              FROM upd JOIN incoming i ON i.point_id = upd.point_id JOIN old o ON o.point_sk = upd.point_sk
             WHERE NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = upd.point_sk)) z
          WHERE z.d <> 0
          RETURNING 1)
        SELECT point_id FROM old"""


# A point deleted while HNSW safe mode was on (a tombstone) and written again is alive again, and counted again. This statement owns the count
# of such a point (the update above leaves a tombstoned point out of its own count adjustment, whichever way its terms changed): it
# enters the count when it has terms now. Always run, not only in safe mode: the tombstone outlives a switch of the mode, and without
# tombstones this is an index probe that finds nothing.
_REVIVE = f"""/* pipeshub:pgvector.revive */
        WITH r AS (
          DELETE FROM retrieval.point_tombstones t USING retrieval.points p
          WHERE t.org_id = $1 AND p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_id = ANY($4::uuid[])
            AND t.point_sk = p.point_sk
          RETURNING t.point_sk, p.lex_terms)
        {_COUNT % '(SELECT 1 FROM r WHERE r.lex_terms IS NOT NULL) x'}"""


# The group rows the points join, locked FOR KEY SHARE (the lightest lock that the group collection's FOR UPDATE conflicts with; it does not
# block another writer's update of the row) in key order, before any point is written: the collection never removes the membership of a
# group under a writer that is adding points to it, whether or not the points carry membership. A group with no row has no membership to lose.
_LOCK_GROUPS = """/* pipeshub:pgvector.lock_groups */
        SELECT 1 FROM retrieval.point_groups WHERE org_id = $1 AND collection_id = $2 AND group_key = ANY($3::text[])
        ORDER BY group_key FOR KEY SHARE"""


async def upsert(tx: Tx, collection_id: int, space_id: int, b: Prepared, computed_at: datetime, *, safe_mode: bool) -> None:
    """Points, their text, payload and postings, and their groups' membership, in this transaction. New points are inserted; points
    that exist are locked and updated in place (``point_sk`` is kept: the generated bit column and the indexes follow). A point
    deleted between the two steps is inserted by the next round. The rows of the points' groups are locked first (see ``_LOCK_GROUPS``)."""
    args = (b.org, collection_id, space_id, b.point_ids, b.group_keys, b.vrids, b.block_ids, b.ent_types, b.ent_ids, b.ent_kinds,
            b.ent_levels, b.embeddings, b.term_start, b.term_end, b.terms, b.weights, b.payloads, b.contents)
    await tx.execute(_LOCK_GROUPS, b.org, collection_id, sorted(set(b.group_keys)))
    todo = list(b.point_ids)
    for _ in range(4):
        made = {r["point_id"] for r in await tx.fetch(_INSERT_NEW, *args, todo)}
        todo = [i for i in todo if i not in made]
        if not todo:
            break
        await tx.fetch(_LOCK_OLD, b.org, collection_id, space_id, todo)
        done = {r["point_id"] for r in await tx.fetch(_UPDATE_OLD, *args, todo)}
        todo = [i for i in todo if i not in done]
        if not todo:
            break
    else:
        raise PgVectorError("the points kept changing under the write; retry")
    # A point deleted while HNSW safe mode was on (a tombstone) and written again is alive again, and counted again. Always run, not
    # only in safe mode: the tombstone outlives a switch of the mode, and without tombstones this is an index probe that finds nothing.
    await tx.execute(_REVIVE, b.org, collection_id, space_id, b.point_ids)
    if b.groups:
        await write_groups(tx, b.org, collection_id, b.groups, computed_at, allow_empty=False)


_GROUP_ROWS = """/* pipeshub:pgvector.write_groups */
        WITH g AS (SELECT * FROM jsonb_to_recordset($3::jsonb) AS g(k text, c text[], r text[], x text[], p text[], a text[], s text, v int, n int))
        INSERT INTO retrieval.point_groups AS pg (org_id, collection_id, group_key, connector_ids, record_group_ids,
                                                  root_record_group_ids, path_ids, acl_scope, scope_stamp, scope_version, scope_generation,
                                                  computed_at)
        SELECT $1, $2, g.k, coalesce(g.c, '{}'), coalesce(g.r, '{}'), coalesce(g.x, '{}'), g.p, g.a, g.s, g.v, g.n, $4::timestamptz FROM g
        ORDER BY g.k         -- one order for every writer, so two writers of overlapping groups cannot deadlock
        ON CONFLICT (org_id, collection_id, group_key) DO UPDATE
          SET connector_ids = coalesce((SELECT g.c FROM g WHERE g.k = EXCLUDED.group_key), pg.connector_ids),
              record_group_ids = coalesce((SELECT g.r FROM g WHERE g.k = EXCLUDED.group_key), pg.record_group_ids),
              root_record_group_ids = coalesce((SELECT g.x FROM g WHERE g.k = EXCLUDED.group_key), pg.root_record_group_ids),
              path_ids = coalesce((SELECT g.p FROM g WHERE g.k = EXCLUDED.group_key), pg.path_ids),
              acl_scope = coalesce((SELECT g.a FROM g WHERE g.k = EXCLUDED.group_key), pg.acl_scope),
              scope_stamp = coalesce((SELECT g.s FROM g WHERE g.k = EXCLUDED.group_key), pg.scope_stamp),
              scope_version = coalesce((SELECT g.v FROM g WHERE g.k = EXCLUDED.group_key), pg.scope_version),
              scope_generation = coalesce((SELECT g.n FROM g WHERE g.k = EXCLUDED.group_key), pg.scope_generation),
              computed_at = EXCLUDED.computed_at
          WHERE pg.computed_at < EXCLUDED.computed_at
            AND ($5::boolean OR cardinality(EXCLUDED.connector_ids) + cardinality(EXCLUDED.record_group_ids)
                                + cardinality(EXCLUDED.root_record_group_ids) > 0)
        RETURNING pg.group_key, pg.connector_ids, pg.record_group_ids, pg.root_record_group_ids, pg.path_ids, pg.acl_scope"""

# A separate statement, so it has a snapshot of its own: the group rows above are locked by this transaction, a writer that waited for
# them has seen the members the earlier writer committed, and the diff below is against those, not against the state at its start.
_GROUP_MEMBERS = """/* pipeshub:pgvector.write_group_members */
        WITH w AS (SELECT * FROM jsonb_to_recordset($3::jsonb) AS w(k text, kind smallint, members text[])),
        want AS (SELECT w.k AS group_key, w.kind, u.member_id FROM w CROSS JOIN LATERAL unnest(w.members) AS u(member_id)),
        del AS (
          DELETE FROM retrieval.group_members m USING w
          WHERE m.org_id = $1 AND m.collection_id = $2 AND m.group_key = w.k AND m.kind = w.kind
            AND NOT EXISTS (SELECT 1 FROM want x WHERE x.group_key = m.group_key AND x.kind = m.kind AND x.member_id = m.member_id)
          RETURNING 1)
        INSERT INTO retrieval.group_members (org_id, collection_id, group_key, kind, member_id)
        SELECT $1, $2, group_key, kind, member_id FROM want ON CONFLICT DO NOTHING"""
_KIND_OF = {CONNECTOR_IDS_FIELD: (1, "connector_ids"), RECORD_GROUP_IDS_FIELD: (2, "record_group_ids"),
            ROOT_RECORD_GROUP_IDS_FIELD: (3, "root_record_group_ids"), PATH_IDS_FIELD: (4, "path_ids"), ACL_SCOPE_FIELD: (5, "acl_scope")}
# the key of a group's payload row -> the recordset column of ``_GROUP_ROWS``
_ROW_COLUMNS = ((CONNECTOR_IDS_FIELD, "c"), (RECORD_GROUP_IDS_FIELD, "r"), (ROOT_RECORD_GROUP_IDS_FIELD, "x"), (PATH_IDS_FIELD, "p"),
                (ACL_SCOPE_FIELD, "a"), (SCOPE_STAMP_FIELD, "s"), (SCOPE_VERSION_FIELD, "v"), (SCOPE_GENERATION_FIELD, "n"))


async def write_groups(tx: Tx, org: str, collection_id: int, groups: dict[str, dict[str, Any]], computed_at: datetime,
                       *, allow_empty: bool) -> int:
    """Membership and scope fields of groups, behind the compare-and-set fence: a row is replaced only by a writer with a later
    ``computed_at``, so of two concurrent writers the one that read the graph later wins whatever the order they commit in. A group
    row that does not exist yet is created. Only the keys a group carries are replaced (a caller that supplies ``connectorIds`` alone
    leaves the rest as it is, as a payload merge does on the other providers). ``allow_empty=False`` (indexing) never replaces arrays
    with empty ones: an upsert that carries no membership must not erase what the membership sync wrote. The rows of ``group_members``
    (kinds 1-3 membership, 4 path, 5 override scope) are made to match the arrays that result."""
    rows = [{"k": k, **{col: v[key] if key in v else None for key, col in _ROW_COLUMNS}} for k, v in groups.items()]
    won = await tx.fetch(_GROUP_ROWS, org, collection_id, json.dumps(rows), computed_at, allow_empty)
    members = [{"k": r["group_key"], "kind": kind, "members": list(r[column] or [])}
               for r in won for key, (kind, column) in _KIND_OF.items() if key in groups[r["group_key"]]]
    if members:
        await tx.execute(_GROUP_MEMBERS, org, collection_id, json.dumps(members))
    return len(won)


def _from(c: Compiled, *, text: bool = True) -> str:
    """Joins the compiled filter needs. ``g`` and ``pt`` are keyed by the org parameter $1."""
    sql = ""
    if c.needs_group:
        sql += " LEFT JOIN retrieval.point_groups g ON g.org_id = $1 AND g.collection_id = p.collection_id AND g.group_key = p.group_key"
    if c.needs_text and text:
        sql += " JOIN retrieval.point_text pt ON pt.org_id = $1 AND pt.point_sk = p.point_sk"
    return sql


_VICTIMS = """SELECT p.point_sk, p.space_id, p.lex_terms FROM retrieval.points p
                 WHERE p.org_id = $1 AND p.collection_id = $2 AND p.point_sk = ANY($3::bigint[])
                   AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)"""
_POSTINGS_OF = f"""SELECT g.point_sk, g.space_id, {wrap('t.term')} AS term FROM %s g CROSS JOIN LATERAL unnest(g.lex_terms) AS t(term)"""
_DROP_POSTINGS = ("DELETE FROM retrieval.postings q USING (%s) s WHERE q.org_id = $1 AND q.collection_id = $2 AND q.space_id = s.space_id "
                  "AND q.term = s.term AND q.point_sk = s.point_sk")
_DROP_COUNT = ("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) "
               f"SELECT $1, $2, g.space_id, {COUNT_TERM}, -count(*) FROM %s g WHERE g.lex_terms IS NOT NULL GROUP BY g.space_id")
# HNSW safe mode: the vector indexes are not touched; the postings go at once, so a search never reads them; the nightly job deletes for real.
_DELETE_TOMBSTONE = f"""/* pipeshub:pgvector.delete_tombstone */
            WITH victims AS ({_VICTIMS}),
            ins AS (INSERT INTO retrieval.point_tombstones (org_id, point_sk) SELECT $1, point_sk FROM victims
                    ON CONFLICT DO NOTHING RETURNING point_sk),
            tombed AS (SELECT v.point_sk, v.space_id, v.lex_terms FROM victims v JOIN ins ON ins.point_sk = v.point_sk),
            dp AS ({_DROP_POSTINGS % (_POSTINGS_OF % 'tombed')} RETURNING 1),
            dn AS ({_DROP_COUNT % 'tombed'} RETURNING 1)
            SELECT count(*) FROM ins"""
_DELETE_BATCH = f"""/* pipeshub:pgvector.delete_batch */
            WITH victims AS ({_VICTIMS}),
            gone AS (DELETE FROM retrieval.points d USING victims v WHERE d.org_id = $1 AND d.point_sk = v.point_sk
                     RETURNING d.point_sk, d.space_id, d.lex_terms),
            txt AS (DELETE FROM retrieval.point_text x USING victims v WHERE x.org_id = $1 AND x.point_sk = v.point_sk RETURNING 1),
            dp AS ({_DROP_POSTINGS % (_POSTINGS_OF % 'gone')} RETURNING 1),
            dn AS ({_DROP_COUNT % 'gone'} RETURNING 1)
            SELECT count(*) FROM gone"""


async def delete_batch(tx: Tx, org: str, collection_id: int, c: Compiled, *, safe_mode: bool) -> int:
    """One batch of at most DELETE_BATCH points matching ``c``. The victims are locked first, by a statement of their own: a writer
    that holds one of them is waited for, and the next statement (a new snapshot) then sees everything that writer committed, its
    postings included. Normally the rows go (with their text and postings). In HNSW safe mode they are tombstoned instead (the vector
    indexes are not touched; the postings go at once, so a search never reads them) and the nightly job deletes them for real. The
    postings and the count change by exactly the rows that went."""
    n = len(c.params) + 3                      # the filter's parameters are $3.., then the batch size
    locked = await tx.fetch(
        f"""/* pipeshub:pgvector.delete_lock */
        SELECT p.point_sk FROM retrieval.points p{_from(c)}
        WHERE p.org_id = $1 AND p.collection_id = $2 AND ({c.where})
          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)
        ORDER BY p.point_sk LIMIT ${n} FOR UPDATE OF p""",
        org, collection_id, *c.params, DELETE_BATCH)
    if not locked:
        return 0
    sks = [r["point_sk"] for r in locked]
    sql = _DELETE_TOMBSTONE if safe_mode else _DELETE_BATCH
    await tx.fetchval(sql, org, collection_id, sks)
    return len(sks)


async def point_ids_matching(tx: Tx, org: str, collection_id: int, c: Compiled) -> list[uuid.UUID]:
    """The ids of the live points matching ``c`` (compiled with ``first_param=3``), read once so that a write that changes what the filter
    reads (a membership or scope write) does not change which points the rest of the same update reaches."""
    rows = await tx.fetch(
        f"""/* pipeshub:pgvector.point_ids_matching */
        SELECT p.point_id FROM retrieval.points p{_from(c)}
        WHERE p.org_id = $1 AND p.collection_id = $2 AND ({c.where})
          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)""",
        org, collection_id, *c.params,
    )
    return [r["point_id"] for r in rows]


async def group_keys_matching(tx: Tx, org: str, collection_id: int, c: Compiled) -> list[str]:
    rows = await tx.fetch(
        f"""/* pipeshub:pgvector.group_keys */
        SELECT DISTINCT p.group_key FROM retrieval.points p{_from(c)}
        WHERE p.org_id = $1 AND p.collection_id = $2 AND ({c.where})""",
        org, collection_id, *c.params,
    )
    return [r["group_key"] for r in rows]


def _merge_args(payload: dict[str, Any]) -> tuple[str, str | None]:
    """(JSON to merge into point_text.payload, new page_content or None)."""
    meta = payload.get("metadata")
    if isinstance(meta, dict) and any(k in meta for k in PROMOTED_METADATA):
        raise PgVectorError("a payload update may not change orgId, virtualRecordId, blockId or the entity keys")
    rest = {k: v for k, v in payload.items() if k not in GROUP_KEYS and k != "page_content"}
    return json.dumps(rest, separators=(",", ":")), payload.get("page_content")


async def merge_payload(tx: Tx, org: str, collection_id: int, payload: dict[str, Any], c: Compiled | None, ids: list[uuid.UUID] | None,
                        *, replace: bool = False) -> int:
    """Shallow-merge ``payload`` into the stored payload of the points named by ``ids`` or matching ``c`` (compiled with
    ``first_param=5``). ``replace``: replace it instead, page_content included (the text goes unless the payload carries it). Membership arrays are not stored there; they go
    through :func:`write_groups`."""
    doc, content = _merge_args(payload)
    if not json.loads(doc) and content is None and not replace:
        return 0
    c = c or Compiled()
    where = "p.point_id = ANY($5::uuid[])" if ids is not None else f"({c.where})"
    params = [org, collection_id, doc, content, ids] if ids is not None else [org, collection_id, doc, content, *c.params]
    set_payload = "$3::jsonb" if replace else "pt.payload || $3::jsonb"
    content = "$4::text" if replace else "coalesce($4::text, pt.page_content)"      # an overwrite drops the text it does not carry, as on Qdrant
    status = await tx.execute(
        f"""/* pipeshub:pgvector.merge_payload */
        UPDATE retrieval.point_text pt SET payload = {set_payload}, page_content = {content}
        FROM retrieval.points p{_from(c, text=False) if ids is None else ""}
        WHERE pt.org_id = $1 AND pt.point_sk = p.point_sk AND p.org_id = $1 AND p.collection_id = $2 AND {where}""",
        *params,
    )
    return int(status.rsplit(" ", 1)[-1])


async def group_keys_of_ids(tx: Tx, org: str, collection_id: int, ids: list[uuid.UUID]) -> list[str]:
    rows = await tx.fetch(
        """/* pipeshub:pgvector.group_keys_of_ids */
        SELECT DISTINCT p.group_key FROM retrieval.points p
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.point_id = ANY($3::uuid[])""",
        org, collection_id, ids,
    )
    return [r["group_key"] for r in rows]


_PG_INT = (-(2**31), 2**31 - 1)


def _stamp_int(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int) or not _PG_INT[0] <= value <= _PG_INT[1]:
        raise PgVectorError(f"{key} must be an integer in the range of a PostgreSQL integer, not {value!r}")
    return value


def _stamp_ids(payload: dict[str, Any], key: str) -> list[str]:
    value = payload[key]
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise PgVectorError(f"{key} must be a list of strings, not {value!r}")
    return list(value)


def scope_row(payload: dict[str, Any]) -> dict[str, Any]:
    """The scope fields of a payload: none when it carries no scope key, else all five, well-typed (a non-empty string stamp, integer
    version and generation within PostgreSQL's range, lists of strings), or ``PgVectorError`` naming the field and nothing is stored. A
    partial stamp is refused rather than completed with a default: a default would be read as a stamp of the current meaning."""
    present = [k for k in SCOPE_KEYS if k in payload]
    if not present:
        return {}
    if len(present) != len(SCOPE_KEYS):
        raise PgVectorError(f"the scope fields are written together; missing {sorted(set(SCOPE_KEYS) - set(present))}")
    stamp = payload[SCOPE_STAMP_FIELD]
    if not isinstance(stamp, str) or not stamp:
        raise PgVectorError(f"{SCOPE_STAMP_FIELD} must be a non-empty string, not {stamp!r}")
    return {SCOPE_STAMP_FIELD: stamp, PATH_IDS_FIELD: _stamp_ids(payload, PATH_IDS_FIELD), ACL_SCOPE_FIELD: _stamp_ids(payload, ACL_SCOPE_FIELD),
            SCOPE_VERSION_FIELD: _stamp_int(payload, SCOPE_VERSION_FIELD), SCOPE_GENERATION_FIELD: _stamp_int(payload, SCOPE_GENERATION_FIELD)}


def membership_of(payload: dict[str, Any]) -> dict[str, Any]:
    """The group-level part of a payload: the membership arrays it carries and its scope fields (``scope_row``)."""
    return {**{k: _ids(payload[k]) for k in MEMBERSHIP_KEYS if k in payload}, **scope_row(payload)}
