"""People a record names: persons keyed by a source's own user id, the promotion of a person to a user, the lookup of a principal by email, and the
``entityRelations`` edges between records and the people (and organisations) they name.

The rules are those of ``Neo4jProvider``, in the ambient org (``current_org_id()``): an ``org_id`` argument is only checked against it (another org reads
nothing, a write refuses it). An ``entityRelations`` edge is one per pair and ``edgeType`` (``_ekey``), as Neo4j's ``MERGE ... {edgeType}``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    PERSON_CRM_EDGES,
    PERSON_TRANSFERABLE_EDGES,
    CollectionNames,
    EntityOrigin,
    PersonMigrationMode,
)
from app.core.org_context import current_org_id
from app.models.entities import Person
from app.services.graph_db.postgres.base import _VALIDATOR, _arango, _doc, _ekey, _j
from app.services.graph_db.postgres.identity import IdentityMixin, _soft

if TYPE_CHECKING:
    from app.core.db import Tx

_ER = CollectionNames.ENTITY_RELATIONS.value
_ENDS = ("_from", "_to", "from_id", "to_id", "from_collection", "to_collection")
# A principal found by email: (collection, the permission type a grant to it carries), in the order an address is resolved (a member before a group
# before a person), as the Arango provider reads them one after the other; Neo4j's unordered match leaves the order open.
_BY_EMAIL = ((CollectionNames.USERS.value, "USER"), (CollectionNames.GROUPS.value, "GROUP"), (CollectionNames.PEOPLE.value, "USER"))
_BY_EMAIL_COLLS = [c for c, _ in _BY_EMAIL]


def _relation_ends(e: dict) -> tuple[str, str, str, str] | None:
    """(from_coll, from_id, to_coll, to_id) of an edge in either shape, or None when an end is missing (Neo4j skips such an edge)."""
    if "_from" in e and "_to" in e:
        fc, _, fi = str(e["_from"] or "").partition("/")
        tc, _, ti = str(e["_to"] or "").partition("/")
    elif "from_id" in e and "to_id" in e:
        fc, fi, tc, ti = e.get("from_collection") or "", e["from_id"] or "", e.get("to_collection") or "", e["to_id"] or ""
    else:
        return None
    return (fc, fi, tc, ti) if fc and fi and tc and ti else None



class PeopleMixin(IdentityMixin):
    # ---- persons a source names by its own user id --------------------------------------------------------------------------------
    @_soft(None, flag=True)
    async def get_person_by_source_key(self, source_key: str, org_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> Person | None:
        """The person of this org with this source key (``"<connector id>:<source user id>"``); as ``get_person_by_email``, a document field with no index."""
        if not self._mine(org_id):
            return None
        org = current_org_id()
        if isinstance(source_key, str) and 0 < len(source_key) <= 400:      # the vetted read of graph_0006
            d = await self._one("/* pipeshub:graph.person_by_source_key */ SELECT node_id, doc FROM graph.person_by_source_key($1)", source_key, transaction=transaction)
        else:
            d = await self._one("/* pipeshub:graph.get_person_by_source_key */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND doc->>'sourceKey' = $2 ORDER BY id LIMIT 1",
                                org, source_key, transaction=transaction)
        return Person.from_arango_person(_arango(d, "person")) if d else None

    @_soft(None, flag=True)
    async def upsert_person_by_source_key(self, person: Person, transaction: str | None = None, *, raise_on_error: bool = False) -> str | None:
        """The id of the person of this org with this source key, created when there is none; an existing one is never changed. Two creators of one key are
        serialised by a lock on it, so both answer the one node. A person of another org is refused, one without a source key fails."""
        org = current_org_id()
        node = person.to_arango_person()
        self._require_org(person.org_id)
        if not person.source_key:
            raise ValueError("a person keyed by source needs a source key")
        _VALIDATOR.validate_node(CollectionNames.PEOPLE.value, node)
        async with self._atomic(transaction) as tid:
            await self._id_rows("/* pipeshub:graph.upsert_person_source_lock */ SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [f"person_source:{org}:{person.source_key}"],
                                tid, write=True)
            if len(person.source_key) <= 400:      # the vetted read of graph_0006
                rows = await self._id_rows("/* pipeshub:graph.person_by_source_key */ SELECT node_id AS id FROM graph.person_by_source_key($1)", [person.source_key], tid)
            else:
                rows = await self._id_rows("/* pipeshub:graph.upsert_person_source_find */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND doc->>'sourceKey' = $2 ORDER BY id LIMIT 1",
                                           [org, person.source_key], tid)
            if rows:
                return rows[0]["id"]
            return node["_key"] if await self.create_node_if_absent(node["_key"], "person", node, tid) else None

    async def migrate_person_to_user(self, email: str, user_key: str, org_id: str, transaction: str | None = None) -> str | None:
        """Moves the person's grants and app memberships, and the entity relations that point at it, onto the user, in one transaction; a person with a CRM
        edge (``memberOf``, ``lead``, ``contact``, either direction) keeps it and stays (SPLIT), any other goes with its edges (MIGRATED). An edge the user already
        has (to the same node; for an entity relation, from the same node with the same type) stays and the person's copy goes. None when the org has no person
        with the email or no such user; every person with the email is moved, SPLIT if any stays. Serialised with the creators of the email, so a second run finds nothing to move. A failure raises."""
        self._require_org(org_id)
        org, low = current_org_id(), email.lower()

        async def move(tx: Tx, pid: str) -> str:
            is_crm = await tx.fetchval("""/* pipeshub:graph.migrate_person_crm */ SELECT EXISTS (SELECT 1 FROM graph.edges WHERE org_id = $1 AND coll = ANY($3::text[]) AND from_coll = 'person' AND from_id = $2)
                OR EXISTS (SELECT 1 FROM graph.edges WHERE org_id = $1 AND coll = ANY($3::text[]) AND to_coll = 'person' AND to_id = $2)""", org, pid, list(PERSON_CRM_EDGES))
            # one grant and one membership per node (a MERGE without properties): the person's first, and none where the user has one
            moved = await tx.fetchval("""/* pipeshub:graph.migrate_person_move_grants */ WITH s AS (SELECT DISTINCT ON (e.coll, e.to_coll, e.to_id) e.coll, e.to_coll, e.to_id, e.ekey, e.doc
                  FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($4::text[]) AND e.from_coll = 'person' AND e.from_id = $2 ORDER BY e.coll, e.to_coll, e.to_id, e.ekey),
                i AS (INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                  SELECT $1, s.coll, 'users', $3, s.to_coll, s.to_id, s.ekey, s.doc FROM s
                  WHERE NOT EXISTS (SELECT 1 FROM graph.edges x WHERE x.org_id = $1 AND x.coll = s.coll AND x.from_coll = 'users' AND x.from_id = $3 AND x.to_coll = s.to_coll AND x.to_id = s.to_id)
                  ORDER BY s.coll, s.to_coll, s.to_id ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING RETURNING 1)
                SELECT count(*) FROM i""", org, pid, user_key, list(PERSON_TRANSFERABLE_EDGES))
            await tx.execute("""/* pipeshub:graph.migrate_person_drop_grants */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = ANY($3::text[]) AND from_coll = 'person' AND from_id = $2""",
                             org, pid, list(PERSON_TRANSFERABLE_EDGES))
            # the records naming the person follow it to the user whether or not it splits: authorship is the human's, not the CRM contact's
            named = await tx.fetchval("""/* pipeshub:graph.migrate_person_move_relations */ WITH i AS (INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                  SELECT $1, 'entityRelations', e.from_coll, e.from_id, 'users', $3, e.ekey, e.doc FROM graph.edges e
                  WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.to_coll = 'person' AND e.to_id = $2
                    AND NOT EXISTS (SELECT 1 FROM graph.edges x WHERE x.org_id = $1 AND x.coll = 'entityRelations' AND x.from_coll = e.from_coll AND x.from_id = e.from_id
                                      AND x.to_coll = 'users' AND x.to_id = $3 AND x.doc->'edgeType' IS NOT DISTINCT FROM e.doc->'edgeType')
                  ORDER BY e.from_coll, e.from_id, e.ekey ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING RETURNING 1)
                SELECT count(*) FROM i""", org, pid, user_key)
            await tx.execute("/* pipeshub:graph.migrate_person_drop_relations */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'entityRelations' AND to_coll = 'person' AND to_id = $2",
                             org, pid)
            if not is_crm:      # DETACH DELETE: whatever else the person held goes with it
                await tx.execute("""/* pipeshub:graph.migrate_person_drop_edges */ DELETE FROM graph.edges WHERE org_id = $1
                    AND ((from_coll = 'person' AND from_id = $2) OR (to_coll = 'person' AND to_id = $2))""", org, pid)
                await tx.execute("/* pipeshub:graph.migrate_person_drop_node */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND id = $2", org, pid)
            mode = PersonMigrationMode.SPLIT if is_crm else PersonMigrationMode.MIGRATED
            self.logger.info("Person -> user %s: %s (%s grant and membership, %s entity relation edges moved)", user_key, mode, moved, named)
            return mode

        async def go(tx: Tx) -> str | None:
            await tx.fetchval("/* pipeshub:graph.migrate_person_lock */ SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"person:{org}:{low}")
            # every match, as Neo4j's MATCH: a Salesforce contact is written without this lock, so an external share can make a second person with the address
            persons = await tx.fetch("/* pipeshub:graph.migrate_person_find */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND doc->>'email' = $2 ORDER BY id FOR UPDATE",
                                     org, low)
            # Neo4j matches the user too: no user, nothing moves. Its row is locked, so two persons moving onto it never both find a node free.
            if not persons or await tx.fetchval("/* pipeshub:graph.migrate_person_user */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2 FOR NO KEY UPDATE",
                                                org, user_key) is None:
                return None
            modes = [await move(tx, r["id"]) for r in persons]
            return PersonMigrationMode.SPLIT if PersonMigrationMode.SPLIT in modes else PersonMigrationMode.MIGRATED

        try:
            return await self._q(go, transaction, write=True)
        except Exception as e:
            self.logger.error("Migrate person to user failed for user %s in org %s: %s", user_key, org_id, e)
            raise

    # ---- a principal by email -------------------------------------------------------------------------------------------------------
    @_soft(None)
    async def get_entity_id_by_email(self, email: str, transaction: str | None = None) -> str | None:
        """The id of the user, else the group, else the person of the org with this email (case-insensitive). A failed read answers None."""
        found = await self._principals_by_email(_BY_EMAIL_COLLS, [email], transaction)
        if found is not None:      # the vetted read of graph_0006: the user, else the group, else the person; the lowest id (byte order, as COLLATE "C") of a kind
            return min(found, key=lambda r: (_BY_EMAIL_COLLS.index(r["coll"]), r["node_id"]))["node_id"] if found else None
        rows = await self._id_rows("""/* pipeshub:graph.get_entity_id_by_email */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = ANY($2::text[]) AND lower(doc->>'email') = lower($3)
            ORDER BY array_position($2::text[], coll), id COLLATE "C" LIMIT 1""", [current_org_id(), _BY_EMAIL_COLLS, email], transaction)
        return rows[0]["id"] if rows else None

    @_soft(dict)
    async def bulk_get_entity_ids_by_email(self, emails: list[str], transaction: str | None = None) -> dict[str, tuple[str, str, str]]:
        """``{stored email: (id, collection, permission type)}`` of the users, groups and persons of the org whose email is one of ``emails`` (case-insensitive),
        keyed by the address as stored, as on Neo4j; an address held by more than one is the user's, else the group's. A failed read answers ``{}``."""
        wanted = sorted({e for e in emails if e})
        if not wanted:
            return {}
        found = await self._principals_by_email(_BY_EMAIL_COLLS, wanted, transaction)      # the vetted read of graph_0006
        if found is not None and all(isinstance(_doc(r["doc"]).get("email"), str) for r in found):
            best: dict[str, tuple[int, str]] = {}      # per stored address the least (kind, id): ids in byte order, as COLLATE "C"
            for r in found:
                at, e = (_BY_EMAIL_COLLS.index(r["coll"]), r["node_id"]), _doc(r["doc"])["email"]
                if e not in best or at < best[e]:
                    best[e] = at
            return {e: (k, _BY_EMAIL_COLLS[i], dict(_BY_EMAIL)[_BY_EMAIL_COLLS[i]]) for e, (i, k) in best.items()}
        rows = await self._id_rows("""/* pipeshub:graph.bulk_get_entity_ids_by_email */ SELECT DISTINCT ON (doc->>'email') doc->>'email' AS email, id, coll FROM graph.nodes
            WHERE org_id = $1 AND coll = ANY($2::text[]) AND lower(doc->>'email') IN (SELECT lower(x) FROM unnest($3::text[]) x)
            ORDER BY doc->>'email', array_position($2::text[], coll), id COLLATE "C" """, [current_org_id(), _BY_EMAIL_COLLS, wanted], transaction)
        kinds = dict(_BY_EMAIL)
        return {r["email"]: (r["id"], r["coll"], kinds[r["coll"]]) for r in rows}

    # ---- the entity relations of a record -------------------------------------------------------------------------------------------------
    async def batch_create_entity_relations(self, edges: list[dict], transaction: str | None = None) -> bool:
        """Writes each edge between two nodes of the org, one per pair and ``edgeType`` (an existing one takes the new properties); an edge whose end or
        type is missing, or whose end is not a node, is skipped. Every edge is checked against the schema before the first is written. True."""
        if not edges:
            return True
        rows = self._entity_relation_rows(edges)
        if not rows:
            return True
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.batch_create_entity_relations */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, 'entityRelations', x.fc, x.fi, x.tc, x.ti, x.k, jsonb_strip_nulls(x.doc)
            FROM jsonb_to_recordset($2::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
            WHERE EXISTS (SELECT 1 FROM graph.nodes f WHERE f.org_id = $1 AND f.coll = x.fc AND f.id = x.fi)
              AND EXISTS (SELECT 1 FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = x.tc AND t.id = x.ti)
            ORDER BY x.fc, x.fi, x.tc, x.ti, x.k
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""", current_org_id(), _j(rows)),
                      transaction, write=True)
        return True

    def _entity_relation_rows(self, edges: list[dict]) -> list[dict]:
        """The edges as rows to store, one per pair and ``edgeType`` (the last of a duplicate wins, as SET after MERGE); each is checked against the schema first."""
        for e in edges:
            _VALIDATOR.validate_edge(_ER, e)
        rows: dict[tuple, dict] = {}
        for e in edges:
            ends = _relation_ends(e)
            if ends is None or not e.get("edgeType"):
                self.logger.warning("Skipping invalid entity relation edge (an end or the edgeType is missing): %s", e)
                continue
            props = {k: v for k, v in e.items() if k not in _ENDS}
            fc, fi, tc, ti = ends
            k = _ekey(_ER, props)
            rows[(fc, fi, tc, ti, k)] = {"fc": fc, "fi": fi, "tc": tc, "ti": ti, "k": k, "doc": props}
        return list(rows.values())

    async def replace_record_entity_relations(self, record_id: str, to_collections: list[str], origin: str, edges: list[dict],
                                              transaction: str | None = None) -> None:
        """Replaces the record's ``entityRelations`` edges of this ``origin`` (an edge without one is INFERRED) to nodes of ``to_collections`` with ``edges``;
        no edges clears them. One statement: the new edges are written and only the record's other edges of that origin go, so a failed write leaves the old
        links, in or out of a transaction, and an edge of another origin stays. Every row is scoped to the ambient org."""
        if not record_id or not to_collections or not origin:
            raise ValueError("replacing a record's entity relations needs a record, collections and an origin")
        rows = self._entity_relation_rows(edges) if edges else []
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.replace_record_entity_relations */ WITH ins AS (INSERT INTO graph.edges
                (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, 'entityRelations', x.fc, x.fi, x.tc, x.ti, x.k, jsonb_strip_nulls(x.doc)
            FROM jsonb_to_recordset($2::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
            WHERE EXISTS (SELECT 1 FROM graph.nodes f WHERE f.org_id = $1 AND f.coll = x.fc AND f.id = x.fi)
              AND EXISTS (SELECT 1 FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = x.tc AND t.id = x.ti)
            ORDER BY x.fc, x.fi, x.tc, x.ti, x.k
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc
            RETURNING from_coll, from_id, to_coll, to_id, ekey),
            gone AS (DELETE FROM graph.edges d WHERE d.org_id = $1 AND d.coll = 'entityRelations' AND d.from_coll = 'records' AND d.from_id = $3
                AND d.to_coll = ANY($4::text[]) AND coalesce(d.doc->>'origin', $5) = $6
                AND NOT EXISTS (SELECT 1 FROM ins i WHERE (i.from_coll, i.from_id, i.to_coll, i.to_id, i.ekey) = (d.from_coll, d.from_id, d.to_coll, d.to_id, d.ekey)))
            SELECT count(*) FROM ins""", current_org_id(), _j(rows), record_id, list(to_collections), EntityOrigin.INFERRED.value, origin),
                      transaction, write=True)

    async def delete_record_entity_relations(self, record_id: str, to_collection: str, origin: str, transaction: str | None = None) -> int:
        """Deletes the record's ``entityRelations`` edges to nodes of ``to_collection`` of this ``origin`` (an edge without one is INFERRED); how many went."""
        if not record_id or not to_collection or not origin:
            raise ValueError("deleting a record's entity relations needs a record, a collection and an origin")
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_record_entity_relations */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'entityRelations'
            AND from_coll = 'records' AND from_id = $2 AND to_coll = $3 AND coalesce(doc->>'origin', $4) = $5 RETURNING 1) SELECT count(*) FROM d""",
                                                         current_org_id(), record_id, to_collection, EntityOrigin.INFERRED.value, origin), transaction, write=True))

    async def get_record_people(self, record_id: str, org_id: str) -> list[dict[str, Any]]:
        """The users of ``org_id`` linked to the record of ``org_id`` by an ``entityRelations`` edge either way, each once, by id: ``{"id", "name", "email"}``."""
        if not record_id or not org_id or not self._mine(org_id):
            return []
        rows = await self._id_rows("""/* pipeshub:graph.get_record_people */ WITH rec AS (SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = $2 AND doc->>'orgId' = $3),
            linked AS (SELECT e.to_id AS id FROM graph.edges e JOIN rec ON e.from_id = rec.id WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.from_coll = 'records' AND e.to_coll = 'users'
                       UNION SELECT e.from_id FROM graph.edges e JOIN rec ON e.to_id = rec.id WHERE e.org_id = $1 AND e.coll = 'entityRelations' AND e.to_coll = 'records' AND e.from_coll = 'users')
            SELECT u.id, u.doc FROM graph.nodes u JOIN linked l ON l.id = u.id WHERE u.org_id = $1 AND u.coll = 'users' AND u.doc->>'orgId' = $3 ORDER BY u.id COLLATE "C" """,
                                   [current_org_id(), record_id, org_id], None)
        return [{"id": r["id"], "name": (d := _doc(r["doc"])).get("fullName"), "email": d.get("email")} for r in rows]
