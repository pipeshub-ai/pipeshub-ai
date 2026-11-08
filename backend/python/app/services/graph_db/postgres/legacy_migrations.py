"""The start-up migrations away from legacy data models: knowledge bases stored as record groups under one shared hub app, agent knowledge that names
that hub, and the hierarchy edge under its pre-rename collection.

A PostgreSQL graph store never wrote these shapes itself; they arrive only with data copied from an older store. So every method answers on a fresh
store too (nothing to count, nothing to move, already current) and never raises there. The answers are ``Neo4jProvider``'s; every statement is a literal
with bound parameters, in the ambient org.
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    Connectors,
    ConnectorScopes,
)
from app.core.org_context import current_or_system, current_org_id
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.postgres.base import (
    _VALIDATOR,
    PgBase,
    _arango,
    _doc,
    _ekey,
    _j,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_KB = Connectors.KNOWLEDGE_BASE.value
_RG = CollectionNames.RECORD_GROUPS.value
_APPS = CollectionNames.APPS.value
_ORG_APP = CollectionNames.ORG_APP_RELATION.value
_USER_APP = CollectionNames.USER_APP_RELATION.value
_KNOWLEDGE = CollectionNames.AGENT_KNOWLEDGE.value
_HAS_KNOWLEDGE = CollectionNames.AGENT_HAS_KNOWLEDGE.value
_MOVE_PAGE = 10_000
# Neo4j relabels the record group in place, so the app keeps every property its REMOVE list does not name. Of those, besides the fields the migration
# sets, an app can hold only the description: the strict app schema refuses a group's access rule, internal flag and permission model, and fields of
# older schemas.
_APP_KEEPS = ("description",)


def _legacy_record_group_ids(raw: Any) -> list[str] | None:  # noqa: ANN401
    """The ``recordGroups`` ids of a legacy knowledge entry's ``filters`` (stringified JSON or an object); None when absent or empty, which meant "every
    knowledge base" (``Neo4jProvider._extract_legacy_record_group_ids``)."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    groups = parsed.get("recordGroups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        return None
    return [g for g in groups if isinstance(g, str) and g] or None


class LegacyMigrationMixin(PgBase):
    # ---- knowledge bases stored as record groups -----------------------------------------------------------------------------
    async def get_legacy_kb_record_groups(self, org_id: str) -> list[dict]:
        """The org's knowledge bases still stored as record groups (``groupType`` and ``connectorName`` both KB), whole documents in id order; [] when the
        read fails, as on Neo4j. An ``org_id`` other than the ambient org reads nothing."""
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_legacy_kb_record_groups */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'recordGroups' AND n.doc->>'orgId' = $2 AND n.doc->>'groupType' = $3 AND n.doc->>'connectorName' = $3
                ORDER BY n.id""", org, org_id, _KB), None)
        except Exception as e:
            self.logger.error(f"Get legacy KB record groups failed: {e}")
            return []
        return [_arango(_doc(r["doc"]), _RG) for r in rows]

    async def count_legacy_kb_record_groups(self, org_id: str) -> int:
        """How many of the org's knowledge bases are still record groups; -1 when the count fails, so the caller never takes "unknown" for none and
        deletes the hub app."""
        org = current_org_id()
        try:
            return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.count_legacy_kb_record_groups */ SELECT count(*) FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'recordGroups' AND n.doc->>'orgId' = $2 AND n.doc->>'groupType' = $3 AND n.doc->>'connectorName' = $3""",
                                                            org, org_id, _KB), None))
        except Exception as e:
            self.logger.error(f"Count legacy KB record groups failed: {e}")
            return -1

    async def migrate_legacy_kb_to_app(self, kb_record_group: dict, org_id: str, resolved_creator_key: str | None) -> dict:
        """Turn one legacy knowledge base (a record group of the org) into its own app with the same key, in one transaction: the app document, every
        edge of the record group moved onto the app (Neo4j relabels the node in place, so every relationship stays; its belongsTo to the hub app goes),
        the records of the knowledge base named by the app, the organization's and the creator's relation to it, the record group gone. ``{"success":
        True}``, or ``{"success": False, "reason": ...}`` with nothing changed (a record group that is not there, an app that already has the key)."""
        org = current_org_id()
        kb_key = kb_record_group.get("_key") or kb_record_group.get("id")
        if not kb_key:
            return {"success": False, "reason": "Legacy KB record group missing _key/id"}
        now = get_epoch_timestamp_in_ms()
        hub = f"knowledgeBase_{org_id}"
        org_edge = {"createdAtTimestamp": now}
        user_edge = {"syncState": "NOT_STARTED", "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now}

        async def go(tx: Tx) -> None:
            row = await tx.fetchrow("""/* pipeshub:graph.migrate_legacy_kb_to_app_read */ SELECT doc FROM graph.nodes
                WHERE org_id = $1 AND coll = 'recordGroups' AND id = $2 AND doc->>'orgId' = $3 FOR UPDATE""", org, kb_key, org_id)
            if row is None:
                raise ValueError(f"RecordGroup {kb_key} not found during migration")
            stored = _doc(row["doc"])
            app = {k: stored[k] for k in _APP_KEEPS if stored.get(k) is not None}
            app.update({"id": kb_key, "name": kb_record_group.get("groupName") or "Untitled", "type": _KB, "appGroup": AppGroups.LOCAL_STORAGE.value,
                        "authType": "NONE", "scope": ConnectorScopes.PERSONAL.value, "isActive": True, "isAgentActive": True, "isConfigured": True,
                        "isAuthenticated": True, "vectorMembershipBackfilled": False, "hideConnector": True, "orgId": org_id,
                        "createdAtTimestamp": kb_record_group.get("createdAtTimestamp") or now, "updatedAtTimestamp": kb_record_group.get("updatedAtTimestamp") or now})
            if created_by := resolved_creator_key or kb_record_group.get("createdBy"):
                app["createdBy"] = created_by
            _VALIDATOR.validate_node(_APPS, app)
            _VALIDATOR.validate_node_update(CollectionNames.RECORDS.value, {"connectorId": kb_key})
            _VALIDATOR.validate_edge(_ORG_APP, org_edge)
            if resolved_creator_key:
                _VALIDATOR.validate_edge(_USER_APP, user_edge)
            if not await tx.fetchval("""/* pipeshub:graph.migrate_legacy_kb_to_app_node */ WITH i AS (INSERT INTO graph.nodes (org_id, coll, id, doc)
                    VALUES ($1, 'apps', $2, $3::jsonb) ON CONFLICT (org_id, coll, id) DO NOTHING RETURNING 1) SELECT count(*) FROM i""", org, kb_key, _j(app)):
                raise ValueError(f"An app {kb_key} already exists; the legacy KB record group was left as it is")
            await tx.execute("""/* pipeshub:graph.migrate_legacy_kb_to_app_hub_edge */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'belongsTo'
                AND from_coll = 'recordGroups' AND from_id = $2 AND to_coll = 'apps' AND to_id = $3""", org, kb_key, hub)
            # Every other edge at either end moves with the node (its key does not depend on its ends); an edge of the same key already on the app
            # takes the moved one's document.
            await tx.execute("""/* pipeshub:graph.migrate_legacy_kb_to_app_edges */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
                    AND ((e.from_coll = 'recordGroups' AND e.from_id = $2) OR (e.to_coll = 'recordGroups' AND e.to_id = $2))
                  RETURNING e.coll, e.from_coll, e.from_id, e.to_coll, e.to_id, e.ekey, e.doc)
                INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, d.coll, CASE WHEN d.from_coll = 'recordGroups' AND d.from_id = $2 THEN 'apps' ELSE d.from_coll END, d.from_id,
                       CASE WHEN d.to_coll = 'recordGroups' AND d.to_id = $2 THEN 'apps' ELSE d.to_coll END, d.to_id, d.ekey, d.doc FROM d
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""", org, kb_key)
            await tx.execute("/* pipeshub:graph.migrate_legacy_kb_to_app_group */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'recordGroups' AND id = $2", org, kb_key)
            await tx.execute("""/* pipeshub:graph.migrate_legacy_kb_to_app_records */ UPDATE graph.nodes n SET doc = n.doc || jsonb_build_object('connectorId', $2::text)
                WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->'externalGroupId' = to_jsonb($2::text)
                  AND n.doc->'connectorId' IS DISTINCT FROM to_jsonb($2::text)""", org, kb_key)
            edges = [{"c": _ORG_APP, "fc": CollectionNames.ORGS.value, "fi": org_id, "k": _ekey(_ORG_APP, org_edge), "doc": org_edge}]
            if resolved_creator_key:
                edges.append({"c": _USER_APP, "fc": CollectionNames.USERS.value, "fi": resolved_creator_key, "k": _ekey(_USER_APP, user_edge), "doc": user_edge})
            await tx.execute("""/* pipeshub:graph.migrate_legacy_kb_to_app_relations */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, x.c, x.fc, x.fi, 'apps', $2, x.k, x.doc FROM jsonb_to_recordset($3::jsonb) AS x(c text, fc text, fi text, k text, doc jsonb)
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc""", org, kb_key, _j(edges))
            if not await tx.fetchval("""/* pipeshub:graph.migrate_legacy_kb_to_app_owner */ SELECT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = $1
                    AND e.coll = 'permission' AND e.from_coll = 'users' AND e.to_coll = 'apps' AND e.to_id = $2 AND e.doc->>'role' = 'OWNER')""", org, kb_key):
                self.logger.warning(f"Migrated KB {kb_key} has no OWNER permission edge (degenerate legacy data)")

        try:
            await self._q(go, None, write=True)
        except Exception as e:
            self.logger.error(f"Failed to migrate legacy KB {kb_key} to app: {e}")
            return {"success": False, "reason": str(e)}
        return {"success": True}

    async def migrate_agent_hub_knowledge(self, org_id: str) -> dict:
        """Replace each agent knowledge source of the org that names the hub app with one per current knowledge base app (those its legacy
        ``filters.recordGroups`` named, or every one when it named none), skipping a knowledge base the agent already has, in one transaction.
        ``{"agents_migrated": <hub sources replaced>, "knowledge_nodes_created": n}``; a failed write answers zeros with ``"error"`` and changes nothing.
        Unlike Neo4j, two hub sources of one agent do not both add the same knowledge base."""
        org = current_org_id()
        hub = f"knowledgeBase_{org_id}"
        now = get_epoch_timestamp_in_ms()

        async def go(tx: Tx) -> tuple[int, int]:
            kb_ids = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.migrate_agent_hub_knowledge_kbs */ SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'apps' AND n.doc->>'orgId' = $2 AND n.doc->>'type' = $3 AND n.id <> $4 ORDER BY n.id""", org, org_id, _KB, hub)]
            if not kb_ids:
                return 0, 0
            rows = await tx.fetch("""/* pipeshub:graph.migrate_agent_hub_knowledge_read */ SELECT a.id AS agent, a.doc->>'createdBy' AS created_by, k.id AS knowledge,
                    k.doc->'filters' AS filters,
                    ARRAY(SELECT DISTINCT o.doc->>'connectorId' FROM graph.edges e2 JOIN graph.nodes o ON o.org_id = e2.org_id AND o.coll = 'agentKnowledge' AND o.id = e2.to_id
                          WHERE e2.org_id = $1 AND e2.coll = 'agentHasKnowledge' AND e2.from_coll = 'agentInstances' AND e2.from_id = a.id
                            AND e2.to_coll = 'agentKnowledge' AND o.doc->>'connectorId' IS NOT NULL) AS existing
                FROM graph.nodes a
                JOIN graph.edges e ON e.org_id = a.org_id AND e.coll = 'agentHasKnowledge' AND e.from_coll = 'agentInstances' AND e.from_id = a.id AND e.to_coll = 'agentKnowledge'
                JOIN graph.nodes k ON k.org_id = e.org_id AND k.coll = 'agentKnowledge' AND k.id = e.to_id
                WHERE a.org_id = $1 AND a.coll = 'agentInstances' AND a.doc->>'orgId' = $2 AND k.doc->'connectorId' = to_jsonb($3::text)
                ORDER BY a.id, k.id FOR UPDATE OF k""", org, org_id, hub)
            if not rows:
                return 0, 0
            current = set(kb_ids)
            have: dict[str, set[str]] = {}
            nodes, edges, old = [], [], []
            for r in rows:
                existing = have.setdefault(r["agent"], set(r["existing"]))
                scoped = _legacy_record_group_ids(json.loads(r["filters"]) if r["filters"] is not None else None)
                for kb in [k for k in scoped if k in current] if scoped else kb_ids:
                    if kb in existing:
                        continue
                    existing.add(kb)
                    key = str(uuid.uuid4())
                    # The agent's own creator; "system" only for an agent that has none.
                    node = {"id": key, "connectorId": kb, "filters": "{}", "createdBy": r["created_by"] or "system", "createdAtTimestamp": now, "updatedAtTimestamp": now}
                    edge = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
                    _VALIDATOR.validate_node(_KNOWLEDGE, node)
                    _VALIDATOR.validate_edge(_HAS_KNOWLEDGE, edge)
                    nodes.append({"id": key, "doc": node})
                    edges.append({"fi": r["agent"], "ti": key, "k": _ekey(_HAS_KNOWLEDGE, edge), "doc": edge})
                old.append(r["knowledge"])
            if nodes:
                await tx.execute("""/* pipeshub:graph.migrate_agent_hub_knowledge_nodes */ INSERT INTO graph.nodes (org_id, coll, id, doc)
                    SELECT $1, 'agentKnowledge', x.id, x.doc FROM jsonb_to_recordset($2::jsonb) AS x(id text, doc jsonb)""", org, _j(nodes))
                await tx.execute("""/* pipeshub:graph.migrate_agent_hub_knowledge_edges */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                    SELECT $1, 'agentHasKnowledge', 'agentInstances', x.fi, 'agentKnowledge', x.ti, x.k, x.doc FROM jsonb_to_recordset($2::jsonb) AS x(fi text, ti text, k text, doc jsonb)""",
                                 org, _j(edges))
            # The hub source goes with every edge on it (Neo4j deletes the agent's edge, then the node with whatever else hangs off it).
            await tx.execute("""/* pipeshub:graph.migrate_agent_hub_knowledge_drop_edges */ DELETE FROM graph.edges e WHERE e.org_id = $1
                AND ((e.from_coll = 'agentKnowledge' AND e.from_id = ANY($2::text[])) OR (e.to_coll = 'agentKnowledge' AND e.to_id = ANY($2::text[])))""", org, old)
            await tx.execute("""/* pipeshub:graph.migrate_agent_hub_knowledge_drop */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'agentKnowledge'
                AND id = ANY($2::text[])""", org, old)
            return len(rows), len(nodes)

        try:
            agents, created = await self._q(go, None, write=True)
        except SchemaValidationError:
            raise
        except Exception as e:
            self.logger.error(f"Failed to migrate agent hub knowledge for org {org_id}: {e}")
            return {"agents_migrated": 0, "knowledge_nodes_created": 0, "error": str(e)}
        return {"agents_migrated": agents, "knowledge_nodes_created": created}

    # ---- the hierarchy edge under its pre-rename collection ------------------------------------------------------------------
    async def migrate_legacy_relation_edge(self, legacy_collection: str, legacy_relationship_type: str) -> dict:
        """Move every edge stored under ``legacy_collection`` (Arango's pre-rename name; the relationship type is Neo4j's half) onto ``nodeRelations``, with
        its document and the key that collection gives it (a link type keeps two links of one pair apart), in pages of one transaction each, so a run
        that stops resumes. The ambient org, or every org under a system context, as the other start-up sweeps. An edge already under the new name
        keeps its document: the moved copy of the same edge is dropped. ``{"migrated": <edges moved off the old name>, "already_current": <none were
        there>}``; a failure raises, so the caller leaves its completion flag unset."""
        current = CollectionNames.NODE_RELATIONS.value
        if not legacy_collection or legacy_collection == current:
            raise ValueError(f"Not a legacy edge collection: {legacy_collection!r}")
        current_or_system()        # no context at all is an error, not an empty store
        moved, errors = 0, []

        async def page(tx: Tx, org: str) -> int:
            rows = await tx.fetch("""/* pipeshub:graph.migrate_legacy_relation_edge_page */ SELECT from_coll, from_id, to_coll, to_id, ekey, doc FROM graph.edges
                WHERE org_id = $1 AND coll = $2 ORDER BY from_coll, from_id, to_coll, to_id, ekey LIMIT $3 FOR UPDATE""", org, legacy_collection, _MOVE_PAGE)
            if not rows:
                return 0
            out = [{"fc": r["from_coll"], "fi": r["from_id"], "tc": r["to_coll"], "ti": r["to_id"], "old": r["ekey"], "doc": (d := _doc(r["doc"])),
                    "k": _ekey(current, d)} for r in rows]
            payload = _j(out)
            await tx.execute("""/* pipeshub:graph.migrate_legacy_relation_edge_insert */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, 'nodeRelations', x.fc, x.fi, x.tc, x.ti, x.k, x.doc FROM jsonb_to_recordset($2::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING""", org, payload)
            return int(await tx.fetchval("""/* pipeshub:graph.migrate_legacy_relation_edge_delete */ WITH d AS (DELETE FROM graph.edges e
                USING jsonb_to_recordset($3::jsonb) AS x(fc text, fi text, tc text, ti text, old text) WHERE e.org_id = $1 AND e.coll = $2
                  AND e.from_coll = x.fc AND e.from_id = x.fi AND e.to_coll = x.tc AND e.to_id = x.ti AND e.ekey = x.old RETURNING 1) SELECT count(*) FROM d""",
                                         org, legacy_collection, payload))

        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                while n := await self._q(lambda tx, org=org: page(tx, org), None, write=True):
                    moved += n
        self._raise_failed(errors)
        if moved:
            self.logger.info(f"Moved {moved} edge(s) from '{legacy_collection}' to '{current}'")
        return {"migrated": moved, "already_current": moved == 0}
