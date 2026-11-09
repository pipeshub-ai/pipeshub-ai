"""Taxonomy nodes (categories, subcategories, topics, languages) and the records' ``belongsTo*`` edges to them: resolution, consolidation and reach.

The answers are those of ``Neo4jProvider``. A node is a ``graph.nodes`` row of a taxonomy collection in the ambient org: a canonical node carries
``orgId`` and ``normalizedName``, a legacy node no ``orgId``. A record reaches one over a ``graph.edges`` row of ``belongsToCategory``,
``belongsToTopic``, ``belongsToLanguage`` (or ``belongsToDepartment``) from ``records``, and a subcategory its parent over
``interCategoryRelations``. An org argument is only ever the ambient org: the statements also match it on the document's ``orgId`` (of the record,
or of the node where only the org's own nodes count), so any other org reads and changes nothing. Pages walk keys in byte order, as Neo4j compares
strings. Every statement is a literal with bound parameters; a collection name is bound only after it was checked against the taxonomy collections.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgMismatch, current_org_id
from app.models.entities import EntityType
from app.services.graph_db.common.utils import PERSON_ENTITY_TYPE
from app.services.graph_db.postgres.base import _VALIDATOR, PgBase, _doc, _j, _node
from app.services.graph_db.postgres.entity_access import _TARGETS
from app.services.graph_db.taxonomy import (
    CATEGORY_HIERARCHY_PARENTS,
    MAX_TAXONOMY_ALIASES,
    TAXONOMY_COLLECTIONS,
    TAXONOMY_EDGE_COLLECTIONS,
    TAXONOMY_ENTITY_TYPE_BY_COLLECTION,
    TAXONOMY_ENTITY_TYPES,
    alias_pairs,
    check_edge_move,
    check_edge_move_target,
    is_taxonomy_collection,
    subcategory_level,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_C = CollectionNames
_DEPT_EDGE, _DEPTS = _C.BELONGS_TO_DEPARTMENT.value, _C.DEPARTMENTS.value
# The edge collections a record reaches its taxonomy over, each with the node collections it reaches (Neo4j's _TAXONOMY_EDGE_GROUPS, in its order).
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (_C.BELONGS_TO_CATEGORY.value, (_C.CATEGORIES.value, _C.SUBCATEGORIES1.value, _C.SUBCATEGORIES2.value, _C.SUBCATEGORIES3.value)),
    (_DEPT_EDGE, (_DEPTS,)),
    (_C.BELONGS_TO_TOPIC.value, (_C.TOPICS.value,)),
    (_C.BELONGS_TO_LANGUAGE.value, (_C.LANGUAGES.value,)),
)
_GROUP_RANK = {edge: i for i, (edge, _) in enumerate(_GROUPS)}
# entity type -> (edge collection, node collections, how the node's org is matched, whether an edge from the node to the record counts too)
_REACH: dict[str, tuple[str, tuple[str, ...], str, bool]] = {
    **{t: (*_TARGETS[t], False) for t in sorted(TAXONOMY_ENTITY_TYPES | {EntityType.ORGANIZATION.value})},
    PERSON_ENTITY_TYPE: (_C.ENTITY_RELATIONS.value, (_C.USERS.value,), "org", True),     # record_people writes record -> user, Slack user -> record
}
_EDGE_MOVE_BATCH = 5000
_ORG = "orgId"          # a stored node's own field, as in entities.py


def _refs_by_type(refs: list[dict[str, Any]], types: set[str] | frozenset[str]) -> dict[str, list[str]]:
    """The ids of each supported type, first occurrence first."""
    out: dict[str, list[str]] = {}
    for ref in refs:
        rid, typ = str(ref.get("id") or ""), ref.get("type")
        if rid and typ in types and rid not in out.setdefault(typ, []):
            out[typ].append(rid)
    return {t: ids for t, ids in out.items() if ids}


def _first(*values: object) -> object:
    """Cypher's ``coalesce``: the first value that is not null."""
    return next((v for v in values if v is not None), None)


class TaxonomyMixin(PgBase):
    # ---- a record's taxonomy -----------------------------------------------------------------------------------
    async def get_taxonomy_entities_for_record(self, record_key: str, transaction: str | None = None) -> list[dict[str, Any]]:
        """The category, subcategory, department, topic and language nodes the record links to, as ``EntityRecord`` source fields, group by group."""
        if not record_key:
            return []
        edges, colls = zip(*((e, c) for e, cs in _GROUPS for c in cs), strict=True)
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_taxonomy_entities_for_record */ SELECT DISTINCT ON (e.coll, n.coll, n.id) e.coll AS edge, n.coll, n.id,
              jsonb_build_object('name', n.doc->'name', 'departmentName', n.doc->'departmentName', 'aliases', n.doc->'aliases', 'orgId', n.doc->'orgId') AS v
            FROM graph.nodes r JOIN graph.edges e ON e.org_id = r.org_id AND e.from_coll = 'records' AND e.from_id = r.id
            JOIN unnest($3::text[], $4::text[]) AS g(edge, coll) ON g.edge = e.coll AND g.coll = e.to_coll
            JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.to_coll AND n.id = e.to_id
            WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = $2 AND e.coll = ANY($3::text[]) ORDER BY e.coll, n.coll, n.id""",
                                                 current_org_id(), record_key, list(edges), list(colls)), transaction)
        out = []
        for r in sorted(rows, key=lambda r: (_GROUP_RANK[r["edge"]], r["id"], r["coll"])):
            v = _doc(r["v"])
            aliases = v["aliases"] if isinstance(v["aliases"], list) else []
            out.append({"entityId": r["id"], "name": _first(v["name"], v["departmentName"]) or r["id"], "entityType": TAXONOMY_ENTITY_TYPE_BY_COLLECTION[r["coll"]],
                        "level": subcategory_level(r["coll"]), "aliases": [str(a) for a in aliases if a], _ORG: v[_ORG]})
        return out

    async def get_record_taxonomy_links(self, record_keys: list[str], transaction: str | None = None, *, include_departments: bool = False) -> list[dict[str, Any]]:
        """Every ``belongsTo*`` edge from the records to a taxonomy node (and a department with ``include_departments``), with the edge's own
        spelling, one row per edge, by record."""
        keys = sorted({str(k) for k in record_keys or [] if k})
        if not keys:
            return []
        edges, colls = set(TAXONOMY_EDGE_COLLECTIONS.values()), set(TAXONOMY_COLLECTIONS)
        if include_departments:
            edges.add(_DEPT_EDGE)
            colls.add(_DEPTS)
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_record_taxonomy_links */ SELECT e.from_id, n.coll, n.id,
              n.doc->>'normalizedName' IS NOT NULL AS canonical, e.doc->>'migratedFrom' IS NOT NULL AS migrated,
              jsonb_build_object('name', n.doc->'name', 'departmentName', n.doc->'departmentName', 'extractedName', e.doc->'extractedName',
                                 'extractedNames', e.doc->'extractedNames') AS v
            FROM graph.edges e
            JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
            JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.to_coll AND n.id = e.to_id
            WHERE e.org_id = $1 AND e.coll = ANY($3::text[]) AND e.from_coll = 'records' AND e.from_id = ANY($2::text[]) AND e.to_coll = ANY($4::text[])
            ORDER BY e.from_id, n.coll, n.id, e.coll, e.ekey""", current_org_id(), keys, sorted(edges), sorted(colls)), transaction)
        out = []
        for r in rows:
            v = _doc(r["v"])
            out.append({"recordId": r["from_id"], "collection": r["coll"], "entityId": r["id"], "name": _first(v["name"], v["departmentName"]),
                        "canonical": bool(r["canonical"]), "extractedName": v["extractedName"], "extractedNames": v["extractedNames"], "migrated": bool(r["migrated"])})
        return out

    # ---- reach -------------------------------------------------------------------------------------------------
    async def get_taxonomy_entity_membership(self, refs: list[dict[str, Any]], org_id: str, transaction: str | None = None) -> dict[tuple[str, str], dict[str, list[str]]]:
        """The distinct connectors and record groups of the live records of ``org_id`` that reach each entity (a person over ``entityRelations``
        either way, an organisation of the org over one from the record); an entity no record reaches, or whose node is gone, maps to empty lists."""
        if not refs or not org_id:
            return {}
        by_type = _refs_by_type(refs, set(_REACH))
        org = current_org_id()

        async def go(tx: Tx) -> dict:
            out: dict[tuple[str, str], dict[str, list[str]]] = {}
            for typ, ids in by_type.items():
                edge, colls, scope, both = _REACH[typ]
                rows = await tx.fetch("""/* pipeshub:graph.get_taxonomy_entity_membership */ SELECT ref.id,
                      coalesce(array_agg(DISTINCT r.doc->>'connectorId') FILTER (WHERE r.doc->>'connectorId' IS NOT NULL), '{}') AS connectors,
                      coalesce(array_agg(DISTINCT r.doc->>'recordGroupId') FILTER (WHERE r.doc->>'recordGroupId' IS NOT NULL), '{}') AS groups
                    FROM unnest($2::text[]) AS ref(id)
                    LEFT JOIN LATERAL (
                      SELECT e.from_id AS rid FROM graph.nodes n
                        JOIN graph.edges e ON e.org_id = n.org_id AND e.coll = $3 AND e.from_coll = 'records' AND e.to_coll = n.coll AND e.to_id = n.id
                        WHERE n.org_id = $1 AND n.coll = ANY($4::text[]) AND n.id = ref.id AND ($6::text = 'none' OR ($6::text = 'org' AND n.doc->>'orgId' = $5)
                          OR ($6::text = 'external' AND n.doc->'isExternal' = 'true'::jsonb AND n.doc->>'parentOrgId' = $5))
                      UNION
                      SELECT e.to_id FROM graph.nodes n
                        JOIN graph.edges e ON e.org_id = n.org_id AND e.coll = $3 AND e.from_coll = n.coll AND e.from_id = n.id AND e.to_coll = 'records'
                        WHERE $7::boolean AND n.org_id = $1 AND n.coll = ANY($4::text[]) AND n.id = ref.id AND ($6::text = 'none' OR ($6::text = 'org' AND n.doc->>'orgId' = $5)
                          OR ($6::text = 'external' AND n.doc->'isExternal' = 'true'::jsonb AND n.doc->>'parentOrgId' = $5))) l ON true
                    LEFT JOIN graph.nodes r ON r.org_id = $1 AND r.coll = 'records' AND r.id = l.rid AND r.doc->>'orgId' = $5
                      AND coalesce((r.doc->>'isDeleted')::boolean, false) = false
                    GROUP BY ref.id""", org, ids, edge, list(colls), org_id, scope, both)
                found = {r["id"]: r for r in rows}
                out.update({(typ, i): {"connectorIds": list(found[i]["connectors"]) if i in found else [],
                                       "recordGroupIds": list(found[i]["groups"]) if i in found else []} for i in ids})
            return out
        return await self._q(go, transaction)

    async def get_unreached_taxonomy_entities(self, refs: list[dict[str, Any]], org_id: str, transaction: str | None = None) -> set[tuple[str, str]]:
        """The taxonomy entities no live record of ``org_id`` reaches any more, or whose node is gone; each entity stops at its first live record."""
        if not refs or not org_id:
            return set()
        self._require_org(org_id)    # another org matches no record here, which would report every entity unreached
        by_type = _refs_by_type(refs, TAXONOMY_ENTITY_TYPES)
        org = current_org_id()

        async def go(tx: Tx) -> set:
            out: set[tuple[str, str]] = set()
            for typ, ids in by_type.items():
                edge, colls, _, _ = _REACH[typ]
                rows = await tx.fetch("""/* pipeshub:graph.get_unreached_taxonomy_entities */ SELECT ref.id FROM unnest($2::text[]) AS ref(id)
                    WHERE NOT EXISTS (SELECT 1 FROM graph.nodes n
                      JOIN graph.edges e ON e.org_id = n.org_id AND e.coll = $3 AND e.from_coll = 'records' AND e.to_coll = n.coll AND e.to_id = n.id
                      JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                      WHERE n.org_id = $1 AND n.coll = ANY($4::text[]) AND n.id = ref.id AND r.doc->>'orgId' = $5
                        AND coalesce((r.doc->>'isDeleted')::boolean, false) = false)""", org, ids, edge, list(colls), org_id)
                out.update((typ, r["id"]) for r in rows)
            return out
        return await self._q(go, transaction)

    # ---- resolution --------------------------------------------------------------------------------------------
    async def find_taxonomy_nodes(self, collection: str, org_id: str, normalized_names: list[str], transaction: str | None = None) -> list[dict[str, Any]]:
        """The org's canonical nodes, not merged, whose ``normalizedName`` or one of whose ``normalizedAliases`` is one of the names; a legacy node never."""
        if not org_id or not normalized_names or not is_taxonomy_collection(collection):
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_taxonomy_nodes */ SELECT n.id,
              jsonb_build_object('name', n.doc->'name', 'normalizedName', n.doc->'normalizedName', 'aliases', n.doc->'aliases', 'normalizedAliases', n.doc->'normalizedAliases') AS v
            FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND n.doc->>'orgId' = $3 AND n.doc->>'mergedInto' IS NULL
              AND (n.doc->>'normalizedName' = ANY($4::text[]) OR (jsonb_typeof(n.doc->'normalizedAliases') = 'array' AND n.doc->'normalizedAliases' ?| $4::text[]))
            ORDER BY n.id""", current_org_id(), collection, org_id, list(dict.fromkeys(normalized_names))), transaction)
        return [{"id": r["id"], "name": v["name"], "normalizedName": v["normalizedName"], "aliases": _first(v["aliases"], []),
                 "normalizedAliases": _first(v["normalizedAliases"], [])} for r in rows for v in (_doc(r["v"]),)]

    async def create_taxonomy_node_if_absent(self, collection: str, node: dict[str, Any], transaction: str | None = None) -> None:
        """Insert the canonical node unless its key is taken; an existing node is never written, so the first spelling stays its name. ``aliases``
        are not written here, and a field set to None is not stored."""
        if not is_taxonomy_collection(collection):
            raise ValueError(f"{collection!r} is not a taxonomy collection")
        _VALIDATOR.validate_node(collection, node)
        if not (node.get("id") or node.get("_key")):
            raise ValueError("taxonomy node needs an id")
        org = current_org_id()
        doc = {k: v for k, v in _node(node, collection).items() if v is not None and k != "aliases"}
        if doc.get(_ORG) != org:       # a node of another org would sit in this org, unseen by its own
            raise OrgMismatch("a taxonomy node names an org other than the ambient org")
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.create_taxonomy_node_if_absent */ INSERT INTO graph.nodes (org_id, coll, id, doc)
            VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (org_id, coll, id) DO NOTHING""", org, collection, doc["id"], _j(doc)), transaction, write=True)

    async def ensure_taxonomy_hierarchy_edge(self, child_collection: str, child_key: str, parent_key: str) -> None:
        """Link the subcategory to its parent over ``interCategoryRelations`` when both exist and the edge does not, in a unit of its own; the edge's
        key makes concurrent callers converge on one edge."""
        parent_collection = CATEGORY_HIERARCHY_PARENTS.get(child_collection)
        if parent_collection is None:
            raise ValueError(f"{child_collection!r} is not a subcategory level")
        doc = {"createdAtTimestamp": get_epoch_timestamp_in_ms()}
        _VALIDATOR.validate_edge(_C.INTER_CATEGORY_RELATIONS.value, doc)
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.ensure_taxonomy_hierarchy_edge */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT c.org_id, 'interCategoryRelations', c.coll, c.id, p.coll, p.id, '', $6::jsonb FROM graph.nodes c
            JOIN graph.nodes p ON p.org_id = c.org_id AND p.coll = $4 AND p.id = $5
            WHERE c.org_id = $1 AND c.coll = $2 AND c.id = $3
            ON CONFLICT (org_id, coll, from_id, from_coll, to_id, to_coll, ekey) DO NOTHING""",
                                            current_org_id(), child_collection, child_key, parent_collection, parent_key, _j(doc)), None, write=True)

    async def add_taxonomy_aliases(self, collection: str, key: str, aliases: list[str], normalized_aliases: list[str], *, org_id: str,
                                   max_aliases: int = MAX_TAXONOMY_ALIASES, transaction: str | None = None) -> None:
        """Union the (display, normalized) pairs into the node's two lists, keyed by the normalized form, both cut to their common length first and
        capped, in one statement on the node's row. Only a node of ``org_id`` is written; a write that would change nothing takes no lock."""
        if not is_taxonomy_collection(collection):
            raise ValueError(f"{collection!r} is not a taxonomy collection")
        if not org_id:
            raise ValueError("add_taxonomy_aliases needs an org")
        pairs = alias_pairs(aliases, normalized_aliases)
        if not key or not pairs:
            return
        shown, forms = [d for d, _ in pairs], [n for _, n in pairs]
        _VALIDATOR.validate_node_update(collection, {"aliases": shown, "normalizedAliases": forms})
        # The lists are computed from the row the UPDATE locks (re-read when a concurrent writer changed it first), so no writer drops another's
        # alias; nothing in the statement may be a materialized CTE, which the re-read would not recompute. The WHERE skips a node whose aligned
        # lists already hold every spelling and fit the cap.
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.add_taxonomy_aliases */ UPDATE graph.nodes t SET doc = t.doc || (SELECT jsonb_build_object(
                'aliases', coalesce(jsonb_agg(p.d ORDER BY p.o), '[]'::jsonb), 'normalizedAliases', coalesce(jsonb_agg(p.n ORDER BY p.o), '[]'::jsonb)) FROM (
              SELECT a.d, b.n, a.o FROM jsonb_array_elements(CASE WHEN jsonb_typeof(t.doc->'aliases') = 'array' THEN t.doc->'aliases' ELSE '[]'::jsonb END) WITH ORDINALITY AS a(d, o)
                JOIN jsonb_array_elements(CASE WHEN jsonb_typeof(t.doc->'normalizedAliases') = 'array' THEN t.doc->'normalizedAliases' ELSE '[]'::jsonb END)
                  WITH ORDINALITY AS b(n, o) ON b.o = a.o
              UNION ALL
              SELECT to_jsonb(x.d), to_jsonb(x.n), x.o + 1000000000 FROM unnest($5::text[], $6::text[]) WITH ORDINALITY AS x(d, n, o)
                WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(t.doc->'normalizedAliases') = 'array' THEN t.doc->'normalizedAliases' ELSE '[]'::jsonb END)
                    WITH ORDINALITY AS c(n, o)
                  WHERE c.n = to_jsonb(x.n) AND c.o <= CASE WHEN jsonb_typeof(t.doc->'aliases') = 'array' THEN jsonb_array_length(t.doc->'aliases') ELSE 0 END)
              ORDER BY 3 LIMIT $7::int) p)
            WHERE t.org_id = $1 AND t.coll = $2::text AND t.id = $3::text AND t.doc->>'orgId' = $4::text
              AND CASE WHEN jsonb_typeof(t.doc->'aliases') = 'array' AND jsonb_typeof(t.doc->'normalizedAliases') = 'array'
                THEN jsonb_array_length(t.doc->'aliases') <> jsonb_array_length(t.doc->'normalizedAliases') OR jsonb_array_length(t.doc->'normalizedAliases') > $7::int
                  OR NOT t.doc->'normalizedAliases' @> to_jsonb($6::text[]) ELSE true END""",
                                            current_org_id(), collection, key, org_id, shown, forms, max(1, max_aliases)), transaction, write=True)

    # ---- consolidation -----------------------------------------------------------------------------------------
    async def move_taxonomy_edges(self, collection: str, from_key: str, to_key: str, org_id: str, *, set_merged_from: str | None, only_merged_from: str | None = None,
                                  provenance: str = "mergedFrom", dry_run: bool = False, transaction: str | None = None) -> int:
        """Move the edges of ``org_id``'s records (trashed ones too) from ``from_key`` to ``to_key``, a batch at a time, keeping each edge's fields and
        recording ``provenance``; an edge whose record already links to ``to_key`` is removed instead. How many edges matched (would move, dry run)."""
        check_edge_move(collection, from_key, to_key, org_id, provenance)
        org, edge = current_org_id(), TAXONOMY_EDGE_COLLECTIONS[collection]
        match = (org, edge, collection, from_key, org_id, only_merged_from, provenance)
        if dry_run:
            return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.move_taxonomy_edges_count */ SELECT count(*) FROM graph.edges e
                JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                WHERE e.org_id = $1 AND e.coll = $2 AND e.to_coll = $3 AND e.to_id = $4 AND e.from_coll = 'records' AND r.doc->>'orgId' = $5
                  AND ($6::text IS NULL OR e.doc->>$7::text = $6::text)""", *match), transaction) or 0)
        target = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.move_taxonomy_edges_target */ SELECT n.doc->>'orgId' AS org FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3""", org, collection, to_key), transaction)
        check_edge_move_target(collection, to_key, org_id, found=target is not None, target_org=target["org"] if target else None,
                               provenance=provenance, only_merged_from=only_merged_from)
        # A forward move keeps an edge's first provenance, so a chain of merges still knows where each edge came from; a restore sets it (None
        # removes it), and a restore of a migration clears the merge history too.
        clear_merge = only_merged_from is not None and provenance == "migratedFrom"
        total = 0
        while True:
            batch = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.move_taxonomy_edges_find */ SELECT e.from_id FROM graph.edges e
                JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                WHERE e.org_id = $1 AND e.coll = $2 AND e.to_coll = $3 AND e.to_id = $4 AND e.from_coll = 'records' AND r.doc->>'orgId' = $5
                  AND ($6::text IS NULL OR e.doc->>$7::text = $6::text) ORDER BY e.from_id LIMIT $8""", *match, _EDGE_MOVE_BATCH), transaction)
            if not batch:
                return total
            moved = int(await self._q(lambda tx, ids=[r["from_id"] for r in batch]: tx.fetchval("""/* pipeshub:graph.move_taxonomy_edges */ WITH m AS (
                  SELECT e.from_id, e.ekey, e.doc FROM graph.edges e JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
                  WHERE e.org_id = $1 AND e.coll = $2 AND e.to_coll = $3 AND e.to_id = $4 AND e.from_coll = 'records' AND e.from_id = ANY($8::text[]) AND r.doc->>'orgId' = $5
                    AND ($6::text IS NULL OR e.doc->>$7::text = $6::text)
                    AND EXISTS (SELECT 1 FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = $3 AND t.id = $9)
                  ORDER BY e.from_id, e.ekey FOR UPDATE OF e),
                put AS (INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                  SELECT $1, $2, 'records', m.from_id, $3, $9, m.ekey, CASE WHEN $11::boolean THEN d.doc - 'mergedFrom' ELSE d.doc END FROM m
                  CROSS JOIN LATERAL (SELECT CASE WHEN $6::text IS NULL THEN coalesce(nullif(m.doc->$7::text, 'null'::jsonb), to_jsonb($10::text)) ELSE to_jsonb($10::text) END AS v) p
                  CROSS JOIN LATERAL (SELECT CASE WHEN p.v IS NULL THEN m.doc - $7::text ELSE m.doc || jsonb_build_object($7::text, p.v) END AS doc) d
                  ORDER BY m.from_id, m.ekey
                  ON CONFLICT (org_id, coll, from_id, from_coll, to_id, to_coll, ekey) DO NOTHING),
                gone AS (DELETE FROM graph.edges e USING m WHERE e.org_id = $1 AND e.coll = $2 AND e.from_coll = 'records' AND e.from_id = m.from_id
                  AND e.to_coll = $3 AND e.to_id = $4 AND e.ekey = m.ekey RETURNING 1)
                SELECT count(*) FROM gone""", *match, ids, to_key, set_merged_from, clear_merge), transaction, write=True) or 0)
            total += moved
            if not moved:       # found but not moved (changed underneath, or the target went): reading again would find the same batch for ever
                return total

    async def find_legacy_taxonomy_nodes(self, collection: str, org_id: str, limit: int, after_key: str | None = None, transaction: str | None = None) -> list[dict[str, Any]]:
        """The legacy nodes (no ``orgId``) that records of ``org_id`` link to (trashed ones too), with how many distinct such records each has, in key
        order after ``after_key``."""
        if not is_taxonomy_collection(collection):
            raise ValueError(f"{collection!r} is not a taxonomy collection")
        if not org_id:
            return []
        # Legacy nodes no longer grow: a page walks them in key order and stops once it has enough, counting each one's records as it goes.
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_legacy_taxonomy_nodes */ SELECT n.id, jsonb_build_object('name', n.doc->'name') AS v, c.records
            FROM (SELECT x.org_id, x.coll, x.id, x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = $2 AND x.doc->>'orgId' IS NULL
                    AND x.id COLLATE "C" > $5::text COLLATE "C" ORDER BY x.id COLLATE "C") n
            CROSS JOIN LATERAL (SELECT count(DISTINCT e.from_id) AS records FROM graph.edges e
              JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
              WHERE e.org_id = $1 AND e.coll = $3 AND e.from_coll = 'records' AND e.to_coll = n.coll AND e.to_id = n.id AND r.doc->>'orgId' = $4) c
            WHERE c.records > 0 ORDER BY n.id COLLATE "C" LIMIT $6""",
                                                 current_org_id(), collection, TAXONOMY_EDGE_COLLECTIONS[collection], org_id, after_key or "", max(1, int(limit))), transaction)
        return [{"_key": r["id"], "name": _doc(r["v"])["name"], "records": r["records"]} for r in rows]

    async def find_merged_taxonomy_nodes_with_edges(self, collection: str, org_id: str, limit: int, after_key: str | None = None,
                                                    transaction: str | None = None) -> list[dict[str, Any]]:
        """The org's nodes merged into another that records of the org still link to (trashed ones too), with how many distinct such records each
        has, in key order after ``after_key``."""
        if not is_taxonomy_collection(collection):
            raise ValueError(f"{collection!r} is not a taxonomy collection")
        if not org_id:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_merged_taxonomy_nodes_with_edges */ SELECT n.id,
              jsonb_build_object('name', n.doc->'name', 'mergedInto', n.doc->'mergedInto') AS v, c.records
            FROM (SELECT x.org_id, x.coll, x.id, x.doc FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = $2 AND x.doc->>'orgId' = $4 AND x.doc->>'mergedInto' IS NOT NULL
                    AND ($5::text IS NULL OR x.id COLLATE "C" > $5::text COLLATE "C") ORDER BY x.id COLLATE "C") n
            CROSS JOIN LATERAL (SELECT count(DISTINCT e.from_id) AS records FROM graph.edges e
              JOIN graph.nodes r ON r.org_id = e.org_id AND r.coll = 'records' AND r.id = e.from_id
              WHERE e.org_id = $1 AND e.coll = $3 AND e.from_coll = 'records' AND e.to_coll = n.coll AND e.to_id = n.id AND r.doc->>'orgId' = $4) c
            WHERE c.records > 0 ORDER BY n.id COLLATE "C" LIMIT $6""",
                                                 current_org_id(), collection, TAXONOMY_EDGE_COLLECTIONS[collection], org_id, after_key, max(1, int(limit))), transaction)
        return [{"_key": r["id"], "name": v["name"], "mergedInto": v["mergedInto"], "records": r["records"]} for r in rows for v in (_doc(r["v"]),)]
