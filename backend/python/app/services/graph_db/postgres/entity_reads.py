"""Named entities and their value mentions: the reads.

The answers are those of ``Neo4jProvider`` (``app.modules.named_entities.graph_ops``). Every statement binds the ambient org and also matches the
document's ``orgId`` with the org argument, so any other org reads nothing. A failed read raises: no method answers "empty" for "failed".
The record sets of ``get_records_for_named_entities`` depend only on the records they are given (``within``) and on live records of the org; an
empty ``within`` matches nothing.
Every statement is a literal with bound parameters. A number is read only from a JSON number, so a malformed value never matches and never
raises.
"""
from __future__ import annotations

from typing import Any

from app.core.org_context import current_org_id
from app.modules.named_entities.graph_ops import (
    _MIN_DATE_SPAN_MS,
    _SCAN_CAP,
    _WITHIN_CHUNK,
    NamedEntityQuery,
    filter_constraints,
)
from app.services.graph_db.postgres.base import PgBase, _doc


def _uniq(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _node(doc: dict, *fields: str) -> dict:
    return {f: doc.get(f) for f in fields}


class EntityReadsMixin(PgBase):
    # ---- nodes -------------------------------------------------------------------------------------------------
    async def find_named_entities(self, org_id: str, kind: str, norm_keys: list[str]) -> list[dict]:
        """The org's nodes of ``kind`` whose key, or any alias, is one of ``norm_keys`` (a merged node too: its redirect is followed by the caller)."""
        org = current_org_id()
        if not norm_keys:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_named_entities */ SELECT n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2 AND n.doc->>'kind' = $3
              AND (n.doc->>'normKey' = ANY($4::text[]) OR n.doc->'normalizedAliases' ?| $4::text[]) ORDER BY n.id""", org, org_id, kind, _uniq(norm_keys)), None)
        return [_doc(r["doc"]) for r in rows]

    async def get_named_entities_by_ids(self, org_id: str, ids: list[str]) -> list[dict]:
        org = current_org_id()
        if not ids:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_named_entities_by_ids */ SELECT n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.id = ANY($2::text[]) AND n.doc->>'orgId' = $3 ORDER BY n.id""", org, _uniq(ids), org_id), None)
        return [{"id": d["id"], **_node(d, "kind", "name", "normKey", "normalizedAliases")} for d in map(_doc, (r["doc"] for r in rows))]

    async def find_named_entities_by_alias(self, org_id: str, pairs: list[tuple[str, str]], limit: int = 3) -> list[dict]:
        """Nodes carrying a pair's key as an alias other than their own key (and not merged), at most ``limit`` per pair, each row tagged with that key."""
        return await self._by_pair(org_id, pairs, limit, alias=True)

    async def find_named_entities_by_key_prefix(self, org_id: str, prefixes: list[tuple[str, str]], limit: int = 21) -> list[dict]:
        """Nodes whose key is a pair's key followed by a space and more, at most ``limit`` per pair, each row tagged with that key."""
        return await self._by_pair(org_id, prefixes, limit, alias=False)

    async def _by_pair(self, org_id: str, pairs: list[tuple[str, str]], limit: int, *, alias: bool) -> list[dict]:
        org = current_org_id()
        if not pairs:
            return []
        kinds, keys = zip(*dict.fromkeys(pairs), strict=True)
        if alias:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_named_entities_by_alias */ SELECT p.key, n.doc FROM unnest($3::text[], $4::text[]) AS p(kind, key)
                CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = 'namedEntities' AND x.doc->>'orgId' = $2
                  AND x.doc->>'kind' = p.kind AND x.doc->'normalizedAliases' ? p.key AND x.doc->>'normKey' <> p.key AND x.doc->>'mergedInto' IS NULL
                  ORDER BY x.id LIMIT $5) n ORDER BY p.key, n.id""", org, org_id, list(kinds), list(keys), max(1, limit)), None)
        else:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_named_entities_by_key_prefix */ SELECT p.key, n.doc FROM unnest($3::text[], $4::text[]) AS p(kind, key)
                CROSS JOIN LATERAL (SELECT x.id, x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = 'namedEntities' AND x.doc->>'orgId' = $2
                  AND x.doc->>'kind' = p.kind AND starts_with(x.doc->>'normKey', p.key || ' ')
                  ORDER BY x.doc->>'normKey', x.id LIMIT $5) n ORDER BY p.key, n.doc->>'normKey', n.id""", org, org_id, list(kinds), list(keys), max(1, limit)), None)
        return [{"key": r["key"], "id": d["id"], **_node(d, "kind", "name", "normKey")} for r, d in ((r, _doc(r["doc"])) for r in rows)]

    async def query_named_entities(self, org_id: str, query: NamedEntityQuery) -> dict:
        """A page of the org's nodes in id order, narrowed by kinds and a name prefix; the cursor is the offset of the next page."""
        org = current_org_id()
        skip = int(query.cursor or 0)
        limit = max(1, min(int(query.limit or 50), 200))
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.query_named_entities */ SELECT n.doc FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2
              AND (cardinality($3::text[]) = 0 OR n.doc->>'kind' = ANY($3::text[])) AND ($4::text = '' OR starts_with(lower(n.doc->>'name'), $4::text))
            ORDER BY n.id OFFSET $5 LIMIT $6""", org, org_id, [str(k) for k in query.kinds or []], (query.name_prefix or "").strip().lower(), skip, limit), None)
        return {"entities": [_doc(r["doc"]) for r in rows], "cursor": str(skip + limit) if len(rows) == limit else ""}

    async def get_named_entities_for_record(self, record_id: str) -> list[dict]:
        """The nodes the record mentions, each with the edge's count and blocks, then the record's value rows."""
        org = current_org_id()

        async def go(tx: Any) -> tuple[list, list]:  # noqa: ANN401
            nodes = await tx.fetch("""/* pipeshub:graph.get_named_entities_for_record */ SELECT n.doc AS node, e.doc AS edge FROM graph.edges e
                JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id
                WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.from_id = $2 AND e.to_coll = 'namedEntities' ORDER BY n.id""", org, record_id)
            values = await tx.fetch("""/* pipeshub:graph.get_named_entities_for_record_values */ SELECT doc FROM graph.nodes
                WHERE org_id = $1 AND coll = 'valueMentions' AND doc->>'recordId' = $2 ORDER BY id""", org, record_id)
            return nodes, values
        nodes, values = await self._q(go, None)
        return [{**_doc(r["node"]), **_node(_doc(r["edge"]), "mentionCount", "blockIndexes")} for r in nodes] + [_doc(r["doc"]) for r in values]

    # ---- mentions ----------------------------------------------------------------------------------------------
    async def get_named_entity_mentions_in_records(self, org_id: str, entity_ids: list[str], record_ids: list[str]) -> list[dict]:
        """The org's mention edges between the records and the entities given (records the caller already resolved)."""
        org = current_org_id()
        if not entity_ids or not record_ids:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_named_entity_mentions_in_records */ SELECT e.from_id, e.to_id, e.doc FROM graph.edges e
            JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
            JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id
            WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.from_id = ANY($3::text[]) AND e.to_coll = 'namedEntities'
              AND e.to_id = ANY($4::text[]) AND e.doc->>'orgId' = $2 ORDER BY e.from_id, e.to_id""", org, org_id, _uniq(record_ids), _uniq(entity_ids)), None)
        return [{"recordId": r["from_id"], "entityId": r["to_id"], **_node(_doc(r["doc"]), "blockIndexes", "extractedNames", "mentionCount")} for r in rows]

    async def get_named_entity_mentions_from_records(self, org_id: str, record_ids: list[str]) -> list[dict]:
        """Every mention edge of the records (records the caller can read); a merged node is left out: its mentions moved to the node it merged into."""
        org = current_org_id()
        if not record_ids:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_named_entity_mentions_from_records */ SELECT e.from_id, e.to_id, e.doc, n.doc->>'kind' AS kind FROM graph.edges e
            JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
            JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id AND n.doc->>'mergedInto' IS NULL
            WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.from_id = ANY($3::text[]) AND e.to_coll = 'namedEntities'
              AND e.doc->>'orgId' = $2 ORDER BY e.from_id, e.to_id""", org, org_id, _uniq(record_ids)), None)
        return [{"recordId": r["from_id"], "entityId": r["to_id"], "kind": r["kind"], "extractedNames": _doc(r["doc"]).get("extractedNames")} for r in rows]

    # ---- value mentions ----------------------------------------------------------------------------------------
    async def get_named_entity_date_spans(self, org_id: str, record_ids: list[str], start_ms: int, end_ms: int) -> dict[str, tuple[bool, int]]:
        """Each record with a date value overlapping [start_ms, end_ms): whether it names a date in it (one starting in it, no longer than it or a day),
        and the narrowest span in milliseconds of its dates overlapping it."""
        org = current_org_id()
        found: dict[str, tuple[bool, int]] = {}
        ids = _uniq(record_ids)
        for at in range(0, len(ids), _WITHIN_CHUNK):
            rows = await self._q(lambda tx, part=ids[at:at + _WITHIN_CHUNK]: tx.fetch("""/* pipeshub:graph.get_named_entity_date_spans */ SELECT d.rid, min(d.e - d.s) AS span,
                  max(CASE WHEN d.s >= $4::float8 AND d.e - d.s <= $6::float8 THEN 1 ELSE 0 END) AS named
                FROM (SELECT v.doc->>'recordId' AS rid, f.s, f.e FROM graph.nodes v CROSS JOIN LATERAL (SELECT
                    CASE WHEN jsonb_typeof(v.doc->'startMs') = 'number' THEN (v.doc->>'startMs')::float8 END AS s,
                    CASE WHEN jsonb_typeof(v.doc->'endMs') = 'number' THEN (v.doc->>'endMs')::float8 END AS e) f
                  WHERE v.org_id = $1 AND v.coll = 'valueMentions' AND v.doc->>'orgId' = $2 AND v.doc->>'recordId' = ANY($3::text[])) d
                WHERE d.s < $5::float8 AND d.e > $4::float8 GROUP BY d.rid""", org, org_id, part, float(start_ms), float(end_ms), float(max(end_ms - start_ms, _MIN_DATE_SPAN_MS))), None)
            found.update({r["rid"]: (bool(r["named"]), int(r["span"] or 0)) for r in rows if r["rid"]})
        return found

    async def get_named_entity_values_in_records(self, org_id: str, query: NamedEntityQuery | None, record_ids: list[str], per_record: int = 10) -> dict[str, list[str]]:
        """For the records (already resolved by the caller), the names of their values that satisfy one of the query's value constraints, up to
        ``per_record`` each, largest first (a date: latest). Entity-node constraints are ignored."""
        org = current_org_id()
        if query is None or not record_ids:
            return {}
        found: dict[str, list[str]] = {}
        for parts in filter_constraints(None, query):
            for part in parts:
                if part.source != "value":
                    continue
                for rid, names in await self._value_rows(org, org_id, part.binds, "names", _uniq(record_ids), per_record):
                    known = found.setdefault(str(rid), [])
                    known.extend(n for n in names if n and n not in known)
        return {rid: names[:per_record] for rid, names in found.items()}

    async def _value_rows(self, org: str, org_id: str, b: dict, mode: str, record_ids: list[str] | None, count: int) -> list:
        """``mode`` ``names``: per record the names of the rows that satisfy ``b`` (largest first); ``records``: the record ids that have one."""
        args = (org, org_id, b["kinds"], b["dateStart"], b["dateEnd"], b["amountMin"], b["amountMax"], b["currency"], b["quantityMin"], b["quantityMax"],
                b["dimension"], b["percentMin"], b["percentMax"], record_ids, count)
        if mode == "names":
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_named_entity_values_in_records */ SELECT d.rid, (array_agg(d.name ORDER BY d.size DESC, d.name COLLATE "C"))[1:$15::int] AS names
                FROM (SELECT v.doc->>'recordId' AS rid, v.doc->>'name' AS name, coalesce(f.ah, f.a, f.si, f.nv, f.s, 0) AS size FROM graph.nodes v CROSS JOIN LATERAL (SELECT
                    CASE WHEN jsonb_typeof(v.doc->'startMs') = 'number' THEN (v.doc->>'startMs')::float8 END AS s,
                    CASE WHEN jsonb_typeof(v.doc->'endMs') = 'number' THEN (v.doc->>'endMs')::float8 END AS e,
                    CASE WHEN jsonb_typeof(v.doc->'amountFloat') = 'number' THEN (v.doc->>'amountFloat')::float8 END AS a,
                    CASE WHEN jsonb_typeof(v.doc->'amountHighFloat') = 'number' THEN (v.doc->>'amountHighFloat')::float8 END AS ah,
                    CASE WHEN jsonb_typeof(v.doc->'siValue') = 'number' THEN (v.doc->>'siValue')::float8 END AS si,
                    CASE WHEN jsonb_typeof(v.doc->'numericValue') = 'number' THEN (v.doc->>'numericValue')::float8 END AS nv) f
                  WHERE v.org_id = $1 AND v.coll = 'valueMentions' AND v.doc->>'orgId' = $2 AND v.doc->>'recordId' = ANY($14::text[]) AND v.doc->>'name' IS NOT NULL
                    AND (cardinality($3::text[]) = 0 OR v.doc->>'kind' = ANY($3::text[]))
                    AND ($4::float8 IS NULL OR (f.s < $5::float8 AND f.e > $4::float8))
                    AND ($6::float8 IS NULL OR (($8::text = '' OR v.doc->>'currency' = $8::text) AND coalesce(f.ah, f.a) >= $6::float8 AND f.a <= $7::float8))
                    AND ($9::float8 IS NULL OR (($11::text = '' OR v.doc->>'dimension' = $11::text) AND f.si >= $9::float8 AND f.si <= $10::float8))
                    AND ($12::float8 IS NULL OR (v.doc->>'kind' = 'percentage' AND f.nv >= $12::float8 AND f.nv <= $13::float8))) d
                GROUP BY d.rid""", *args), None)
            return [(r["rid"], r["names"] or []) for r in rows]
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_for_named_entities_values */ SELECT DISTINCT v.doc->>'recordId' AS rid FROM graph.nodes v CROSS JOIN LATERAL (SELECT
                CASE WHEN jsonb_typeof(v.doc->'startMs') = 'number' THEN (v.doc->>'startMs')::float8 END AS s,
                CASE WHEN jsonb_typeof(v.doc->'endMs') = 'number' THEN (v.doc->>'endMs')::float8 END AS e,
                CASE WHEN jsonb_typeof(v.doc->'amountFloat') = 'number' THEN (v.doc->>'amountFloat')::float8 END AS a,
                CASE WHEN jsonb_typeof(v.doc->'amountHighFloat') = 'number' THEN (v.doc->>'amountHighFloat')::float8 END AS ah,
                CASE WHEN jsonb_typeof(v.doc->'siValue') = 'number' THEN (v.doc->>'siValue')::float8 END AS si,
                CASE WHEN jsonb_typeof(v.doc->'numericValue') = 'number' THEN (v.doc->>'numericValue')::float8 END AS nv) f
              WHERE v.org_id = $1 AND v.coll = 'valueMentions' AND v.doc->>'orgId' = $2 AND v.doc->>'recordId' IS NOT NULL
                AND ($14::text[] IS NULL OR v.doc->>'recordId' = ANY($14::text[]))
                AND (cardinality($3::text[]) = 0 OR v.doc->>'kind' = ANY($3::text[]))
                AND ($4::float8 IS NULL OR (f.s < $5::float8 AND f.e > $4::float8))
                AND ($6::float8 IS NULL OR (($8::text = '' OR v.doc->>'currency' = $8::text) AND coalesce(f.ah, f.a) >= $6::float8 AND f.a <= $7::float8))
                AND ($9::float8 IS NULL OR (($11::text = '' OR v.doc->>'dimension' = $11::text) AND f.si >= $9::float8 AND f.si <= $10::float8))
                AND ($12::float8 IS NULL OR (v.doc->>'kind' = 'percentage' AND f.nv >= $12::float8 AND f.nv <= $13::float8))
              ORDER BY rid LIMIT $15::int""", *args), None)
        return [r["rid"] for r in rows]

    # ---- the records of a filter -------------------------------------------------------------------------------
    async def get_records_for_named_entities(self, org_id: str, entity_ids: list[str] | None = None, query: NamedEntityQuery | None = None,
                                             limit: int = 5000, within: list[str] | None = None) -> dict:
        """The live records of the org that satisfy every constraint of the filter: each constraint is matched on its own and the record sets
        intersected. A constraint too broad to list is checked within the narrowest one that could be; only when none can is the filter too broad
        (``truncated``). With ``within``, only those records are considered, so the answer never depends on records the caller cannot read; an
        empty ``within`` matches nothing."""
        org = current_org_id()
        cap = min(max(1, limit), _SCAN_CAP)
        if entity_ids:      # a stored id keeps working after a merge: its survivor's mentions count too
            survivors = await self.resolve_named_entity_redirects(org_id, list(entity_ids))
            entity_ids = _uniq([*entity_ids, *survivors.values()])
        predicates = filter_constraints(entity_ids, query)
        if not predicates:
            return {"hits": [], "truncated": False}
        if within is not None:
            selected = _uniq(within)
            for parts in predicates:
                matched: set[str] = set()
                for start in range(0, len(selected), _WITHIN_CHUNK):
                    matched.update(await self._match_within(org, org_id, parts, selected[start:start + _WITHIN_CHUNK]))
                selected = [r for r in selected if r in matched]
                if not selected:
                    return {"hits": [], "truncated": False}
            if len(selected) > cap:
                return {"hits": [], "truncated": True}
            return {"hits": await self._record_hits(org, org_id, selected), "truncated": False}
        found = [await self._match(org, org_id, parts, cap) for parts in predicates]
        listed = [ids for ids in found if ids is not None]
        if not listed:
            return {"hits": [], "truncated": True}
        selected_set = set(min(listed, key=len))
        for parts, ids in zip(predicates, found, strict=True):
            if not selected_set:
                break
            selected_set &= set(ids if ids is not None else await self._match_within(org, org_id, parts, sorted(selected_set)))
        if not selected_set:
            return {"hits": [], "truncated": False}
        return {"hits": await self._record_hits(org, org_id, sorted(selected_set)), "truncated": False}

    async def _match(self, org: str, org_id: str, parts: list, cap: int) -> list[str] | None:
        """Record ids for one constraint, or None when there are more than ``cap``."""
        matched: set[str] = set()
        for part in parts:
            ids = await (self._node_records(org, org_id, part.binds, cap) if part.source == "node" else self._value_rows(org, org_id, part.binds, "records", None, cap + 1))
            if ids is None or len(ids) > cap:
                return None
            matched.update(ids)
            if len(matched) > cap:
                return None
        return sorted(matched)

    async def _node_records(self, org: str, org_id: str, b: dict, cap: int) -> list[str] | None:
        keys = list(b["ids"])
        if b["kinds"] or b["prefix"] or not keys:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_for_named_entities_keys */ SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2 AND (cardinality($3::text[]) = 0 OR n.id = ANY($3::text[]))
                  AND (cardinality($4::text[]) = 0 OR n.doc->>'kind' = ANY($4::text[])) AND ($5::text = '' OR starts_with(lower(n.doc->>'name'), $5::text))
                ORDER BY n.id LIMIT $6""", org, org_id, b["ids"], b["kinds"], b["prefix"], cap + 1), None)
            keys = [r["id"] for r in rows]
            if len(keys) > cap:
                return None
        if not keys:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_for_named_entities_nodes */ SELECT DISTINCT e.from_id AS rid FROM graph.edges e
            JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
            WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.to_coll = 'namedEntities' AND e.to_id = ANY($3::text[])
              AND e.doc->>'orgId' = $2 ORDER BY rid LIMIT $4""", org, org_id, keys, cap + 1), None)
        return None if len(rows) > cap else [r["rid"] for r in rows]

    async def _match_within(self, org: str, org_id: str, parts: list, candidates: list[str]) -> list[str]:
        matched: set[str] = set()
        for part in parts:
            if part.source == "node":
                b = part.binds
                rows = await self._q(lambda tx, b=b: tx.fetch("""/* pipeshub:graph.get_records_for_named_entities_nodes_within */ SELECT DISTINCT e.from_id AS rid FROM graph.edges e
                    JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                    JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id
                    WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.from_id = ANY($3::text[]) AND e.to_coll = 'namedEntities'
                      AND e.doc->>'orgId' = $2 AND (cardinality($4::text[]) = 0 OR n.id = ANY($4::text[])) AND (cardinality($5::text[]) = 0 OR n.doc->>'kind' = ANY($5::text[]))
                      AND ($6::text = '' OR starts_with(lower(n.doc->>'name'), $6::text))""", org, org_id, candidates, b["ids"], b["kinds"], b["prefix"]), None)
                matched.update(r["rid"] for r in rows)
            else:
                matched.update(await self._value_rows(org, org_id, part.binds, "records", candidates, len(candidates) + 1))
        return sorted(matched)

    async def _record_hits(self, org: str, org_id: str, ids: list[str]) -> list[dict]:
        """The live records of the org among ``ids``; a trashed record never comes back from an entity filter."""
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_records_for_named_entities_hits */ SELECT r.id, r.doc FROM graph.nodes r
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($3::text[]) AND r.doc->>'orgId' = $2 AND r.doc->>'isDeleted' IS DISTINCT FROM 'true' ORDER BY r.id""", org, org_id, ids), None)
        hits = []
        for r in rows:
            d = _doc(r["doc"])
            stamp = d.get("sourceLastModifiedTimestamp") or 0
            hits.append({"recordId": r["id"], "virtualRecordId": d.get("virtualRecordId"), "connectorId": d.get("connectorId"), "recordType": d.get("recordType"),
                         "sortTs": stamp if stamp != 0 else (d.get("updatedAtTimestamp") or 0)})
        return hits
