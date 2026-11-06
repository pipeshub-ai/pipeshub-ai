"""Users, orgs, the people behind an app, user groups and roles, and the links between a user and a source account.

Conventions: the org is the ambient one (``current_org_id()``); an ``org_id`` argument is only checked against it (a different org reads as
"nothing", and a write refuses it), never used to reach another org's rows. The whole graph of one org lives in one partition, so "the user
belongs to the org" is the partition, not an edge, except where the interface asks for the edge: a user linked to an app without an org edge
(a guest, an external collaborator) is still listed for the app. Every statement is set-based (no per-row queries).
"""
from __future__ import annotations

import contextlib
import functools
import json
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import FOLDER_MIME_TYPES, CollectionNames, Connectors
from app.config.constants.service_accounts import SERVICE_ACCOUNT_EMAIL_DOMAIN
from app.core.org_context import (
    MissingOrgContext,
    OrgMismatch,
    SystemContextNotAllowed,
    current_or_system,
    current_org_id,
)
from app.models.entities import AppRole, AppUser, AppUserGroup, Person, User
from app.services.graph_db.common.utils import dedupe_agents_by_id
from app.services.graph_db.postgres.base import (
    NotImplementedInPostgres,
    PgBase,
    _arango,
    _doc,
    _ekey,
    _j,
)
from app.services.graph_db.user_email_identity import (
    PERMISSION_ROLE_RANK,
    STUB_EDGE_COLLECTIONS,
    GraphUserEmailConflictError,
    classify_email_peer,
    connector_ids_of_users,
    graph_user_key,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


_KB = Connectors.KNOWLEDGE_BASE.value
_ORG_FIELD = "orgId"  # the org property of a graph document, not a claim


def _js(v: Any) -> Any:  # noqa: ANN401
    return json.loads(v) if isinstance(v, str) else v


_SERVICE_SUFFIX = f"@{SERVICE_ACCOUNT_EMAIL_DOMAIN}"


def _soft(default: Any, *, flag: bool = False) -> Any:  # noqa: ANN401
    """Neo4j swallows a failed read of these methods and answers a default (``flag``: unless ``raise_on_error``). A missing org never is."""
    def deco(fn: Any) -> Any:  # noqa: ANN401
        @functools.wraps(fn)
        async def wrapper(self: Any, *a: Any, **k: Any) -> Any:  # noqa: ANN401
            try:
                return await fn(self, *a, **k)
            except (MissingOrgContext, SystemContextNotAllowed, OrgMismatch):
                raise
            except Exception as e:
                self.logger.error("%s failed: %s", fn.__name__, e)
                if flag and k.get("raise_on_error"):
                    raise
                return default() if callable(default) else default
        return wrapper
    return deco




def _far(coll: str, incoming: bool, e: dict) -> tuple:  # noqa: FBT001
    """The identity of an edge of a user: the node at its other end and its key. A user holds one grant on a node whatever its type, as a MERGE does, so a
    permission is keyed by the node alone."""
    key = "" if coll == "permission" else _ekey(coll, e)
    return (e["from_collection"], e["from_id"], key) if incoming else (e["to_collection"], e["to_id"], key)


class IdentityMixin(PgBase):
    # ---- helpers -------------------------------------------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def _atomic(self, transaction: str | None) -> AsyncIterator[str]:
        """The caller's transaction, or one of our own that commits at the end of the block."""
        if transaction:
            yield transaction
            return
        tid = await self.begin_transaction([], [])
        try:
            yield tid
        except BaseException:
            await self.rollback_transaction(tid)
            raise
        await self.commit_transaction(tid)

    @staticmethod
    def _mine(org_id: str | None) -> bool:
        """An explicit org argument is only ever the ambient org; any other reads nothing."""
        return not org_id or org_id == current_org_id()

    @staticmethod
    def _require_org(org_id: str | None) -> None:
        """A write names the ambient org or none: it is refused, not skipped, for any other."""
        if not IdentityMixin._mine(org_id):
            raise OrgMismatch("the org argument differs from the ambient org")

    async def _id_rows(self, sql: str, args: list[Any], transaction: str | None, *, write: bool = False) -> list:
        return await self._q(lambda tx: tx.fetch(sql, *args), transaction, write=write)

    async def _enrich_created_by(self, entities: list[dict], transaction: str | None = None) -> None:
        """createdByUser (graph id, userId, name, email) from the createdBy graph keys, one query for all of them."""
        keys = list({e["createdBy"] for e in entities if e and e.get("createdBy") and e["createdBy"] != "system"})
        users: dict[str, dict] = {}
        if keys:
            rows = await self._id_rows("/* pipeshub:graph.created_by_users */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = ANY($2::text[])",
                                    [current_org_id(), keys], transaction)
            users = {r["id"]: _doc(r["doc"]) for r in rows}
        for e in entities:
            if not e:
                continue
            key = e.pop("createdBy", None)
            u = users.get(key) if key and key != "system" else None
            e["createdByUser"] = ({"id": key, "userId": u["userId"], "name": u.get("fullName") or "", "email": u.get("email") or ""}
                                  if u and u.get("userId") else None)

    # ---- users and orgs ------------------------------------------------------------------------------------------
    @_soft(None, flag=True)
    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict | None:
        d = await self._one("/* pipeshub:graph.get_user_by_user_id */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND doc->>'userId' = $2 LIMIT 1",
                            current_org_id(), user_id)
        return _arango(d, "users") if d else None

    @_soft(None, flag=True)
    async def get_user_by_email(self, email: str, transaction: str | None = None, *, raise_on_error: bool = False,
                                org_id: str | None = None) -> User | None:
        if not self._mine(org_id):
            return None
        d = await self._one("/* pipeshub:graph.get_user_by_email */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND lower(doc->>'email') = lower($2) LIMIT 1",
                            current_org_id(), email, transaction=transaction)
        return User.from_arango_user(_arango(d, "users")) if d else None

    @_soft(dict, flag=True)
    async def get_users_by_emails(self, emails: list[str], transaction: str | None = None, *, raise_on_error: bool = False,
                                  org_id: str | None = None) -> dict[str, User]:
        org = current_org_id()
        wanted = list(dict.fromkeys(e.lower() for e in emails if e))
        if not wanted or not self._mine(org_id):
            return {}
        # lower(doc->>'email') is the expression index nodes_user_email (org_id, lower(doc->>'email')).
        rows = await self._id_rows("/* pipeshub:graph.get_users_by_emails */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND lower(doc->>'email') = ANY($2::text[])",
                                   [org, wanted], transaction)
        out: dict[str, User] = {}
        for r in rows:
            d = _arango(_doc(r["doc"]), "users")
            out.setdefault(d["email"].lower(), User.from_arango_user(d))
        return out

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, transaction: str | None = None, raise_on_error: bool = False) -> list[dict]:
        # One org under an org context; every org under a system context, through the vetted ``graph.system_orgs`` (pages of 1,000).
        ctx = current_or_system()
        if ctx.kind == "system":
            out: list[dict] = []
            after, after_id = "", ""
            while True:
                rows = await self._system_read(lambda tx, a=after, i=after_id: tx.fetch(
                    "/* pipeshub:graph.system_orgs */ SELECT org_id, node_id, node_json FROM graph.system_orgs($1, $2, $3, $4, 1000)", not active, is_external, a, i))
                out += [_arango(_doc(json.loads(r["node_json"])), "organizations") for r in rows]
                if len(rows) < 1000:
                    return out
                after, after_id = rows[-1][0], rows[-1][1]       # the (org, id) of the last row: an org may hold more than one organization document
        org = ctx.org_id
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_all_orgs */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'organizations'
            AND (NOT $2 OR coalesce((doc->>'isActive')::boolean, false)) AND coalesce((doc->>'isExternal')::boolean, false) = $3""",
                                                  org, active, is_external), transaction)
        return [_arango(_doc(r["doc"]), "organizations") for r in rows]

    async def get_org_apps(self, org_id: str, *, active_only: bool = True, app_type: str | None = None, raise_on_error: bool = False) -> list[dict]:
        if not self._mine(org_id):    # never another org's apps, and not under a system context (current_org_id refuses it)
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_org_apps */ SELECT a.doc FROM graph.edges e JOIN graph.nodes a
            ON a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id WHERE e.org_id = $1 AND e.coll = 'orgAppRelation' AND e.from_id = $1
            AND (NOT $2 OR coalesce((a.doc->>'isActive')::boolean, false)) AND ($3::text IS NULL OR a.doc->>'type' = $3)""",
                                                  current_org_id(), active_only, app_type), None)
        return [_arango(_doc(r["doc"]), "apps") for r in rows]

    async def batch_upsert_orgs(self, orgs: list[dict], transaction: str | None = None) -> None:
        if orgs:
            await self.batch_upsert_nodes(orgs, CollectionNames.ORGS.value, transaction)

    @_soft(None)
    async def get_account_type(self, org_id: str, is_external: bool = False,  # noqa: FBT001, FBT002
                               transaction: str | None = None) -> str | None:
        if not self._mine(org_id):
            return None
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.get_account_type */ SELECT doc->>'accountType' FROM graph.nodes
            WHERE org_id = $1 AND coll = 'organizations' AND id = $2 AND coalesce((doc->>'isExternal')::boolean, false) = $3 LIMIT 1""",
                                                   current_org_id(), org_id, is_external), transaction)

    @_soft(list)
    async def get_users(self, org_id: str, *, active: bool = True) -> list[dict]:
        if not self._mine(org_id):
            return []
        rows = await self._id_rows("""/* pipeshub:graph.get_users */ SELECT u.doc FROM graph.edges b JOIN graph.nodes u ON u.org_id = b.org_id AND u.coll = 'users' AND u.id = b.from_id
            WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.to_coll = 'organizations' AND b.to_id = $1 AND b.doc->>'entityType' = 'ORGANIZATION'
              AND (NOT $2 OR coalesce((u.doc->>'isActive')::boolean, false))""", [current_org_id(), active], None)
        return [_arango(_doc(r["doc"]), "users") for r in rows]

    @_soft(lambda: ([], 0))
    async def get_organization_users(self, org_id: str, search: str | None = None, page: int = 1, limit: int = 100,
                                     transaction: str | None = None, *, include_service_accounts: bool = False) -> tuple[list[dict], int]:
        if not self._mine(org_id):
            return [], 0
        org = current_org_id()
        # the search, the service-account filter and the page are bound parameters of one fixed statement
        rows = await self._id_rows("""/* pipeshub:graph.get_organization_users */ WITH m AS (SELECT u.id, u.doc FROM graph.edges b
              JOIN graph.nodes u ON u.org_id = b.org_id AND u.coll = 'users' AND u.id = b.from_id
              WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.to_coll = 'organizations' AND b.to_id = $1 AND b.doc->>'entityType' = 'ORGANIZATION'
                AND coalesce((u.doc->>'isActive')::boolean, false)
                AND ($2::text IS NULL OR position(lower($2) in lower(coalesce(u.doc->>'fullName', ''))) > 0 OR position(lower($2) in lower(coalesce(u.doc->>'email', ''))) > 0)
                AND ($3::boolean OR (coalesce(u.doc->>'kind', '') <> 'service' AND right(lower(coalesce(u.doc->>'email', '')), length($4::text)) <> $4::text))),
            cnt AS (SELECT count(DISTINCT id) AS total FROM m)
            SELECT cnt.total, p.doc FROM cnt LEFT JOIN LATERAL (SELECT DISTINCT ON (id) id, doc FROM m ORDER BY id) p ON false
            UNION ALL
            SELECT cnt.total, p.doc FROM cnt, LATERAL (SELECT doc FROM (SELECT DISTINCT ON (id) id, doc FROM m ORDER BY id) d
              ORDER BY doc->>'fullName' COLLATE "C" ASC NULLS LAST, id OFFSET $5 LIMIT $6) p""",
                                  [org, search or None, include_service_accounts, _SERVICE_SUFFIX, (page - 1) * limit, limit], transaction)
        total = rows[0]["total"] if rows else 0
        out = []
        for r in rows:
            if r["doc"] is None:
                continue
            d = _doc(r["doc"])
            out.append(_arango({"id": d["id"], "userId": d.get("userId"), "name": d.get("fullName"), "email": d.get("email"), "isActive": d.get("isActive"),
                                "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp")}, "users"))
        return out, total

    async def find_users(self, org_id: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        # lower(), not casefold(): the stored side is lower() too, which keeps "ß". Not _soft: a failed read raises, as the interface says.
        # Lowered by the database, once, so that the stored side and the asked side are lowered by the same function.
        wanted_raw = " ".join((query or "").split())
        if not org_id or not wanted_raw or not self._mine(org_id):
            return []
        wanted = await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.lower */ SELECT lower($1::text)", wanted_raw), None)
        if "@" in wanted:    # an email names one person: all their nodes, no name normalising
            rows = await self._id_rows("""/* pipeshub:graph.find_users_by_email */ SELECT u.id, u.doc->>'fullName' AS "fullName", u.doc->>'email' AS email FROM graph.nodes u
                WHERE u.org_id = $1 AND u.coll = 'users' AND lower(u.doc->>'email') = $2 ORDER BY u.id COLLATE "C" """, [current_org_id(), wanted], None)
            return [dict(r) for r in rows]
        # a name's words are cut at single spaces and rejoined, so runs of spaces compare equal
        rows = await self._id_rows("""/* pipeshub:graph.find_users */ WITH u AS (
              SELECT x.id, x.doc, lower(coalesce(x.doc->>'email', '')) AS email, array_to_string(array_remove(string_to_array(lower(coalesce(x.doc->>'fullName', '')), ' '), ''), ' ') AS name,
                     array_to_string(array_remove(string_to_array(lower(coalesce(x.doc->>'firstName', '') || ' ' || coalesce(x.doc->>'lastName', '')), ' '), ''), ' ') AS parts
              FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = 'users'),
            hits AS (
              SELECT id, name, (email = $2 OR name = $2 OR parts = $2) AS exact, CASE WHEN email <> '' THEN email ELSE 'id:' || id END AS person FROM u
              WHERE email = $2 OR name = $2 OR parts = $2 OR (length($2) >= 3 AND (starts_with(name, $2) OR starts_with(split_part(email, '@', 1), $2)
                OR EXISTS (SELECT 1 FROM unnest(string_to_array(name, ' ')) w WHERE starts_with(w, $2))))),
            cands AS (SELECT * FROM hits WHERE exact OR NOT EXISTS (SELECT 1 FROM hits WHERE exact)),
            people AS (SELECT person FROM (SELECT DISTINCT ON (person) person, name, id FROM cands ORDER BY person, name COLLATE "C", id COLLATE "C") d
                       ORDER BY name COLLATE "C", id COLLATE "C" LIMIT $3)
            SELECT o.id, o.doc->>'fullName' AS "fullName", o.doc->>'email' AS email FROM u o
            WHERE lower(o.doc->>'email') IN (SELECT person FROM people) OR 'id:' || o.id IN (SELECT person FROM people)
            ORDER BY lower(coalesce(o.doc->>'fullName', o.doc->>'email', '')) COLLATE "C", o.id COLLATE "C" """, [current_org_id(), wanted, max(1, limit)], None)
        return [dict(r) for r in rows]

    async def get_graph_user_keys_by_mongo_user_ids(self, user_ids: list[str], org_id: str | None = None, *, chunk_size: int) -> dict[str, str]:
        if not user_ids:
            return {}
        rows = await self._id_rows("/* pipeshub:graph.user_keys_by_user_ids */ SELECT doc->>'userId' AS uid, id FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND doc->>'userId' = ANY($2::text[])"
                                " AND ($3::text IS NULL OR doc->>'orgId' = $3)", [current_org_id(), list(user_ids), org_id], None)
        found = {r["uid"]: r["id"] for r in rows if r["uid"] and r["id"]}
        missing = [u for u in user_ids if u not in found]
        if missing:
            raise ValueError(f"Users not found in graph: {missing}")
        return found

    @_soft(None, flag=True)
    async def get_user_by_source_id(self, source_user_id: str, connector_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> User | None:
        rows = await self._id_rows("""/* pipeshub:graph.get_user_by_source_id */ SELECT u.doc FROM graph.edges e JOIN graph.nodes u ON u.org_id = e.org_id AND u.coll = 'users' AND u.id = e.from_id
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id
            WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.to_coll = 'apps' AND e.to_id = $2 AND e.doc->>'sourceUserId' = $3 LIMIT 1""",
                                [current_org_id(), connector_id, source_user_id], transaction)
        return User.from_arango_user(_arango(_doc(rows[0]["doc"]), "users")) if rows else None

    @_soft(None)
    async def get_app_creator_user(self, connector_id: str, transaction: str | None = None) -> User | None:
        app = await self.get_document(connector_id, CollectionNames.APPS.value, transaction=transaction)
        created_by = (app or {}).get("createdBy")
        user = await self.get_user_by_user_id(user_id=created_by) if created_by else None
        return User.from_arango_user(user) if user else None

    async def get_app_users(self, org_id: str, connector_id: str, *, raise_on_error: bool = False) -> list[dict]:
        """Every member of the org linked to the app (a guest or an external collaborator, who is no member, is not listed). A link the source names
        no user for has no ``sourceUserId`` (None here, never an error), and an app without a type has an empty ``appName``."""
        try:
            if not self._mine(org_id):
                return []
            rows = await self._id_rows("""/* pipeshub:graph.get_app_users */ SELECT u.doc, r.doc->>'sourceUserId' AS sid, a.doc->>'type' AS app
                FROM graph.edges r JOIN graph.nodes u ON u.org_id = r.org_id AND u.coll = 'users' AND u.id = r.from_id
                JOIN graph.nodes a ON a.org_id = r.org_id AND a.coll = 'apps' AND a.id = r.to_id
                WHERE r.org_id = $1 AND r.coll = 'userAppRelation' AND r.from_coll = 'users' AND r.to_id = $2
                  AND EXISTS (SELECT 1 FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = u.id
                              AND b.to_coll = 'organizations' AND b.to_id = $1 AND b.doc->>'entityType' = 'ORGANIZATION')""", [current_org_id(), connector_id], None)
            return [{**_arango(_doc(r["doc"]), "users"), "sourceUserId": r["sid"], "appName": (r["app"] or "").upper()} for r in rows]
        except (MissingOrgContext, SystemContextNotAllowed, OrgMismatch):
            raise
        except Exception as e:
            self.logger.error("get_app_users failed: %s", e)
            if raise_on_error:
                raise
            return []

    async def batch_upsert_app_users(self, users: list, transaction: str | None = None) -> None:
        if not users:
            return
        org = current_org_id()
        orgs = await self.get_all_orgs(transaction=transaction)
        if not orgs:
            raise Exception("No organizations found in the database")
        org_node = orgs[0].get("id") or orgs[0].get("_key")
        connector_id = users[0].connector_id
        now = get_epoch_timestamp_in_ms()
        async with self._atomic(transaction) as tx:
            await self._q(lambda t: t.execute("""/* pipeshub:graph.ensure_minimal_app */ INSERT INTO graph.nodes (org_id, coll, id, doc)
                VALUES ($1, 'apps', $2, $3::jsonb) ON CONFLICT DO NOTHING""", org, connector_id, _j({"id": connector_id, "name": connector_id, "isActive": True})), tx, write=True)
            # the database lowers an address, as it lowers the stored ones: Python's lower() may disagree on non-ASCII letters
            low = {r["raw"]: r["low"] for r in await self._id_rows("/* pipeshub:graph.lower_emails */ SELECT e AS raw, lower(e) AS low FROM unnest($1::text[]) e",
                                                                   [list({u.email for u in users})], tx)}
            have = {r["email"]: (r["id"], bool(r["active"])) for r in await self._id_rows(
                """/* pipeshub:graph.users_by_email */ SELECT lower(doc->>'email') AS email, id, coalesce((doc->>'isActive')::boolean, false) AS active
                   FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND lower(doc->>'email') = ANY($2::text[])""", [org, sorted(set(low.values()))], tx)}
            new: dict[str, Any] = {}
            for u in users:
                if low[u.email] not in have and low[u.email] not in new:
                    new[low[u.email]] = u
            if new:
                nodes = []
                for u in new.values():
                    d = u.to_arango_base_user()
                    d.update({"id": u.id, "orgId": org_node, "isActive": False})
                    d.pop("_key", None)
                    nodes.append(d)
                await self.batch_upsert_nodes(nodes, CollectionNames.USERS.value, tx)
                for u in new.values():
                    have[low[u.email]] = (u.id, False)
                    try:  # the address may already be a Person from an external share; one contact must not fail a sync
                        await self.migrate_person_to_user(u.email, u.id, org_node, transaction=tx)
                    except NotImplementedInPostgres:
                        pass
                    except Exception as e:
                        self.logger.error("Person adoption failed for %s: %s", u.email, e)
                member_edges = [{"from_id": u.id, "from_collection": "users", "to_id": org_node, "to_collection": "organizations", "createdAtTimestamp": u.created_at,
                                 "updatedAtTimestamp": u.updated_at, "entityType": "ORGANIZATION"} for u in new.values() if not u.is_guest]
                await self.batch_create_edges(member_edges, CollectionNames.BELONGS_TO.value, tx)
            # a guest an earlier sync made an org member loses it, unless an admin invite made them active
            heal = list({have[low[u.email]][0] for u in users if u.is_guest and low[u.email] not in new and not have[low[u.email]][1]})
            if heal:
                await self._q(lambda t: t.execute("""/* pipeshub:graph.guest_org_edge_heal */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'belongsTo'
                    AND from_coll = 'users' AND from_id = ANY($2::text[]) AND to_coll = 'organizations' AND to_id = $3""", org, heal, org_node), tx, write=True)
            links = {}
            for u in users:
                key = have[low[u.email]][0]
                links[key] = {"from_id": key, "from_collection": "users", "to_id": connector_id, "to_collection": "apps", "sourceUserId": u.source_user_id,
                              "isExternalUser": False, "syncState": "NOT_STARTED", "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now}
            await self.batch_create_edges(list(links.values()), CollectionNames.USER_APP_RELATION.value, tx)

    async def batch_create_user_app_edges(self, edges: list[dict]) -> int:
        if not edges:
            return 0
        await self.batch_create_edges(edges, CollectionNames.USER_APP_RELATION.value)
        return len(edges)

    # ---- groups and roles ----------------------------------------------------------------------------------------
    @_soft(None, flag=True)
    async def get_user_group_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> AppUserGroup | None:
        rows = await self._id_rows("""/* pipeshub:graph.get_user_group_by_external_id */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'groups'
            AND doc->>'externalGroupId' = $2 AND doc->>'connectorId' = $3 LIMIT 1""", [current_org_id(), external_id, connector_id], transaction)
        return AppUserGroup.from_arango_base_user_group(_arango(_doc(rows[0]["doc"]), "groups")) if rows else None

    @_soft(list, flag=True)
    async def get_user_groups(self, connector_id: str, org_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[AppUserGroup]:
        if not self._mine(org_id):
            return []
        rows = await self._id_rows("/* pipeshub:graph.get_user_groups */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'groups' AND doc->>'connectorId' = $2",
                                [current_org_id(), connector_id], transaction)
        return [AppUserGroup.from_arango_base_user_group(_arango(_doc(r["doc"]), "groups")) for r in rows]

    async def get_user_directory_groups(self, user_id: str, org_id: str, app_names: Sequence[str] | None = None, limit: int = 1000) -> list[dict[str, str]]:
        """The groups of a PipesHub user as synced by connectors: the user's own, and those of each source account it signed a connector in as
        (only that connector's groups). ``{id, externalId, name, connectorId, appName}`` rows, by ``externalId``."""
        if not self._mine(org_id):
            return []
        rows = await self._id_rows("""/* pipeshub:graph.get_user_directory_groups */ WITH u AS (SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND doc->>'userId' = $2 LIMIT 1),
            gid AS (
              SELECT p.to_id AS id FROM u JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = u.id
                AND p.to_coll = 'groups' AND p.doc->>'type' = 'USER'
              UNION
              SELECT p.to_id FROM u JOIN graph.edges l ON l.org_id = $1 AND l.coll = 'authenticatedAs' AND l.from_coll = 'users' AND l.from_id = u.id
                JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = l.to_id AND p.to_coll = 'groups' AND p.doc->>'type' = 'USER'
                JOIN graph.nodes sg ON sg.org_id = $1 AND sg.coll = 'groups' AND sg.id = p.to_id AND sg.doc->>'connectorId' = l.doc->>'connectorId')
            SELECT g.id AS "id", coalesce(g.doc->>'externalGroupId', '') AS "externalId", coalesce(g.doc->>'name', '') AS "name",
                   coalesce(g.doc->>'connectorId', '') AS "connectorId", coalesce(g.doc->>'connectorName', '') AS "appName"
            FROM gid JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'groups' AND g.id = gid.id
            WHERE $3::text[] IS NULL OR g.doc->>'connectorName' = ANY($3::text[])
            ORDER BY 2, 1 LIMIT $4""", [current_org_id(), user_id, list(app_names) if app_names is not None else None, max(1, int(limit))], None)
        return [dict(r) for r in rows]

    @_soft(None, flag=True)
    async def get_app_role_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> AppRole | None:
        rows = await self._id_rows("""/* pipeshub:graph.get_app_role_by_external_id */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'roles'
            AND doc->>'externalRoleId' = $2 AND doc->>'connectorId' = $3 LIMIT 1""", [current_org_id(), external_id, connector_id], transaction)
        return AppRole.from_arango_base_role(_arango(_doc(rows[0]["doc"]), "roles")) if rows else None

    async def batch_upsert_user_groups(self, user_groups: list, transaction: str | None = None) -> None:
        org = current_org_id()
        nodes = [{**g.to_arango_base_user_group(), "orgId": org} for g in user_groups]  # the org is the ambient one, whatever the model says
        await self.batch_upsert_nodes(nodes, CollectionNames.GROUPS.value, transaction)

    async def batch_upsert_app_roles(self, app_roles: list, transaction: str | None = None) -> None:
        org = current_org_id()
        nodes = [{**r.to_arango_base_role(), "orgId": org} for r in app_roles]
        await self.batch_upsert_nodes(nodes, CollectionNames.ROLES.value, transaction)

    async def delete_edges_to_groups(self, from_id: str, from_collection: str, collection: str, transaction: str | None = None) -> int:
        org = current_org_id()
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_to_groups */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1 AND coll = $2
            AND from_coll = $3 AND from_id = $4 AND to_coll = 'groups' RETURNING 1) SELECT count(*) FROM d""", org, collection, from_collection, from_id),
                          transaction, write=True)

    # ---- account links and app membership ------------------------------------------------------------------------
    async def ensure_app_membership(self, principal_id: str, principal_collection: str, connector_id: str, *, is_external: bool,
                                    source_user_id: str | None = None, transaction: str | None = None) -> None:
        """Create-only: an existing edge is left alone, so a member is never downgraded to an external collaborator."""
        now = get_epoch_timestamp_in_ms()
        props = {"isExternalUser": is_external, "syncState": "NOT_STARTED", "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now}
        if source_user_id is not None:
            props["sourceUserId"] = source_user_id
        org = current_org_id()
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.ensure_app_membership */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
            SELECT $1, 'userAppRelation', $2, p.id, 'apps', a.id, '', $5::jsonb FROM graph.nodes p, graph.nodes a
            WHERE p.org_id = $1 AND p.coll = $2 AND p.id = $3 AND a.org_id = $1 AND a.coll = 'apps' AND a.id = $4
            ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING""", org, principal_collection, principal_id, connector_id, _j(props)), transaction, write=True)

    async def ensure_team_app_edge(self, connector_id: str, org_id: str, transaction: str | None = None) -> None:
        """The org's "All" team gets an edge to the app (the app is created bare when missing, as MERGE does). Idempotent."""
        if not self._mine(org_id):
            return
        org, team, ts = current_org_id(), f"all_{org_id}", get_epoch_timestamp_in_ms()
        props = {"sourceUserId": team, "syncState": "NOT_STARTED", "lastSyncUpdate": ts, "createdAtTimestamp": ts, "updatedAtTimestamp": ts}

        async def go(tx: Any) -> None:  # noqa: ANN401
            if not await tx.fetchval("/* pipeshub:graph.team_exists */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'teams' AND id = $2", org, team):
                return
            await tx.execute("/* pipeshub:graph.ensure_bare_app */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, 'apps', $2, $3::jsonb) ON CONFLICT DO NOTHING",
                             org, connector_id, _j({"id": connector_id}))
            await tx.execute("""/* pipeshub:graph.ensure_team_app_edge */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                VALUES ($1, 'userAppRelation', 'teams', $2, 'apps', $3, '', $4::jsonb) ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO NOTHING""", org, team, connector_id, _j(props))
        await self._q(go, transaction, write=True)

    async def upsert_authenticated_as(self, creator_key: str, source_user_key: str, connector_id: str, org_id: str, transaction: str | None = None) -> None:
        """One link per connector: a stale one (other ends) is dropped, the right one is created or touched."""
        self._require_org(org_id)
        org, ts = current_org_id(), get_epoch_timestamp_in_ms()

        async def go(tx: Any) -> None:  # noqa: ANN401
            if await tx.fetchval("""/* pipeshub:graph.authenticated_as_ends */ SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id IN ($2, $3)""",
                                 org, creator_key, source_user_key) < len({creator_key, source_user_key}):
                return
            await tx.execute("""/* pipeshub:graph.authenticated_as_drop_stale */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'authenticatedAs'
                AND doc->>'connectorId' = $2 AND NOT (from_id = $3 AND to_id = $4)""", org, connector_id, creator_key, source_user_key)
            await tx.execute("""/* pipeshub:graph.authenticated_as_upsert */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                VALUES ($1, 'authenticatedAs', 'users', $2, 'users', $3, $6, $4::jsonb)
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = graph.edges.doc || jsonb_build_object('orgId', $1::text, 'updatedAtTimestamp', $5::bigint)""",
                             org, creator_key, source_user_key,
                             _j({"connectorId": connector_id, "orgId": org, "createdAtTimestamp": ts, "updatedAtTimestamp": ts}), ts, connector_id)
        await self._q(go, transaction, write=True)

    async def remove_authenticated_as(self, connector_id: str, transaction: str | None = None) -> bool:
        org = current_org_id()
        n = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.remove_authenticated_as */ WITH d AS (DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'authenticatedAs'
            AND doc->>'connectorId' = $2 RETURNING 1) SELECT count(*) FROM d""", org, connector_id), transaction, write=True)
        return bool(n)

    # ---- apps and connector instances ----------------------------------------------------------------------------
    async def get_user_apps(self, user_id: str, transaction: str | None = None) -> list:
        """The apps this user passes the connector gate for (the access check's gate, in the user's own org)."""
        org = current_org_id()
        gated = (await self.get_knowledge_hub_access_v3(user_id, org, transaction=transaction))["gated_app_ids"]
        if not gated:
            return []
        rows = await self._id_rows("/* pipeshub:graph.get_user_apps */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])", [org, gated], transaction)
        return [_arango(_doc(r["doc"]), "apps") for r in rows]

    async def get_user_app_ids(self, user_key: str, transaction: str | None = None) -> list[str]:
        """Apps the user is linked to, directly, through a team, or by authenticating as another account; hidden ones left out."""
        rows = await self._id_rows("""/* pipeshub:graph.get_user_app_ids */ WITH ids AS (
              SELECT e.to_id AS app FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'apps'
              UNION SELECT t.to_id FROM graph.edges p JOIN graph.edges t ON t.org_id = p.org_id AND t.coll = 'userAppRelation' AND t.from_coll = 'teams' AND t.from_id = p.to_id AND t.to_coll = 'apps'
                WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2 AND p.to_coll = 'teams' AND p.doc->>'type' = 'USER'
              UNION SELECT l.doc->>'connectorId' FROM graph.edges l WHERE l.org_id = $1 AND l.coll = 'authenticatedAs' AND l.from_coll = 'users' AND l.from_id = $2)
            SELECT DISTINCT a.id FROM ids JOIN graph.nodes a ON a.org_id = $1 AND a.coll = 'apps' AND a.id = ids.app
            WHERE NOT coalesce((a.doc->>'isHidden')::boolean, false)""", [current_org_id(), user_key], transaction)
        return [r["id"] for r in rows]

    # ---- connector instances -------------------------------------------------------------------------------------
    @_soft(list)
    async def get_user_connector_instances(self, collection: str, user_id: str, org_id: str, team_scope: str, personal_scope: str, transaction: str | None = None) -> list[dict]:
        if not self._mine(org_id):
            return []
        rows = await self._id_rows("""/* pipeshub:graph.get_user_connector_instances */ SELECT n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
            AND EXISTS (SELECT 1 FROM graph.edges o WHERE o.org_id = n.org_id AND o.coll = 'orgAppRelation' AND o.from_coll = 'organizations' AND o.from_id = n.org_id AND o.to_id = n.id)
            AND (n.doc->>'scope' = $3 OR (n.doc->>'scope' = $4 AND n.doc->>'createdBy' = $5))""",
                                [current_org_id(), collection, team_scope, personal_scope, user_id], transaction)
        return [_arango(_doc(r["doc"]), collection) for r in rows]

    @_soft(lambda: ([], 0))
    async def get_filtered_connector_instances(self, collection: str, edge_collection: str, org_id: str, user_id: str, scope: str | None = None,
                                               search: str | None = None, skip: int = 0, limit: int = 20, *, exclude_kb: bool = True,
                                               kb_connector_type: str | None = None, is_admin: bool = False, is_authenticated: bool | None = None,
                                               is_active: bool | None = None, connector_type_filter: str | None = None, is_configured: bool | None = None,
                                               is_agent_active: bool | None = None, allowed_connector_types: list[str] | None = None,
                                               transaction: str | None = None) -> tuple[list[dict], int]:
        if not org_id or not self._mine(org_id):
            return [], 0
        # The org edge is the tenant boundary of an instance; only the creator sees a personal one, admins included. Every filter is a bound
        # parameter of one fixed statement (NULL: not asked).
        rows = await self._id_rows("""/* pipeshub:graph.get_filtered_connector_instances */ WITH f AS (SELECT n.id, n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
              AND EXISTS (SELECT 1 FROM graph.edges o WHERE o.org_id = n.org_id AND o.coll = $3 AND o.from_coll = 'organizations' AND o.from_id = n.org_id AND o.to_id = n.id)
              AND (n.doc->>'scope' = 'team' OR n.doc->>'createdBy' = $4)
              AND ($5::text IS NULL OR n.doc->>'type' <> $5)
              AND (NOT $6::boolean OR (n.doc->>'scope' = 'personal' AND n.doc->>'createdBy' = $4))
              AND (NOT $7::boolean OR (n.doc->>'scope' = 'team' AND ($8::boolean OR n.id IN (
                    SELECT e.to_id FROM graph.nodes u JOIN graph.edges e ON e.org_id = u.org_id AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.from_id = u.id
                    WHERE u.org_id = n.org_id AND u.coll = 'users' AND u.doc->>'userId' = $4
                    UNION SELECT t.to_id FROM graph.nodes u JOIN graph.edges p ON p.org_id = u.org_id AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = u.id AND p.to_coll = 'teams' AND p.doc->>'type' = 'USER'
                    JOIN graph.edges t ON t.org_id = p.org_id AND t.coll = 'userAppRelation' AND t.from_coll = 'teams' AND t.from_id = p.to_id
                    WHERE u.org_id = n.org_id AND u.coll = 'users' AND u.doc->>'userId' = $4))))
              AND ($9::text IS NULL OR position(lower($9) in lower(coalesce(n.doc->>'name', ''))) > 0 OR position(lower($9) in lower(coalesce(n.doc->>'type', ''))) > 0
                   OR position(lower($9) in lower(coalesce(n.doc->>'appGroup', ''))) > 0)
              AND ($10::boolean IS NULL OR n.doc->'isAuthenticated' = to_jsonb($10::boolean))
              AND ($11::boolean IS NULL OR n.doc->'isActive' = to_jsonb($11::boolean))
              AND ($12::text IS NULL OR n.doc->>'type' = $12)
              AND ($13::boolean IS NULL OR coalesce((n.doc->>'isConfigured')::boolean, false) = $13)
              AND ($14::boolean IS NULL OR coalesce((n.doc->>'isAgentActive')::boolean, false) = $14)
              AND ($15::text[] IS NULL OR n.doc->>'type' = ANY($15))), cnt AS (SELECT count(*) AS total FROM f)
            SELECT cnt.total, p.doc FROM cnt LEFT JOIN LATERAL (SELECT doc FROM f ORDER BY doc->'createdAtTimestamp' DESC, id OFFSET $16 LIMIT $17) p ON true""",
                                  [current_org_id(), collection, edge_collection, user_id, kb_connector_type if exclude_kb and kb_connector_type else None,
                                   scope == "personal", scope == "team", is_admin, search or None, is_authenticated, is_active, connector_type_filter or None,
                                   is_configured, is_agent_active, list(allowed_connector_types) if allowed_connector_types is not None else None,
                                   max(skip, 0), max(limit, 0)], transaction)
        return [_arango(_doc(r["doc"]), collection) for r in rows if r["doc"] is not None], (rows[0]["total"] if rows else 0)

    @_soft(False)
    async def check_connector_name_exists(self, collection: str, instance_name: str, scope: str, org_id: str | None = None, user_id: str | None = None,
                                          transaction: str | None = None, exclude_connector_id: str | None = None) -> bool:
        if org_id and not self._mine(org_id):
            return False
        if scope != "personal" and not org_id:      # a team connector is looked up under an org
            return False
        rows = await self._id_rows("""/* pipeshub:graph.check_connector_name_exists */ SELECT 1 FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
              AND n.doc->>'scope' = $4 AND lower(regexp_replace(n.doc->>'name', '^\\s+|\\s+$', '', 'g')) = lower(regexp_replace($3, '^\\s+|\\s+$', '', 'g'))
              AND (($5::boolean AND n.doc->>'createdBy' = $6) OR (NOT $5::boolean AND EXISTS (SELECT 1 FROM graph.edges o WHERE o.org_id = n.org_id AND o.coll = 'orgAppRelation'
                    AND o.from_coll = 'organizations' AND o.from_id = n.org_id AND o.to_id = n.id)))
              AND ($7::text IS NULL OR n.id <> $7) LIMIT 1""",
                                  [current_org_id(), collection, instance_name, scope, scope == "personal", user_id, exclude_connector_id or None], transaction)
        return bool(rows)

    @_soft(0)
    async def batch_update_connector_status(self, collection: str, connector_keys: list[str], *, is_active: bool, is_agent_active: bool, transaction: str | None = None) -> int:
        org = current_org_id()
        if not connector_keys:
            return 0
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.batch_update_connector_status */ WITH u AS (UPDATE graph.nodes SET doc = doc || jsonb_build_object('isActive', $4::boolean, 'isAgentActive', $5::boolean)
            WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) RETURNING 1) SELECT count(*) FROM u""", org, collection, list(connector_keys), is_active, is_agent_active),
                             transaction, write=True)

    async def check_connector_in_use(self, connector_id: str, transaction: str | None = None) -> list[str]:
        """Names of the live agents whose knowledge names the connector (distinct agents with the same name count apart)."""
        rows = await self._id_rows("""/* pipeshub:graph.check_connector_in_use */ SELECT DISTINCT a.id AS "agentId", a.doc->>'name' AS "agentName" FROM graph.nodes k
            JOIN graph.edges e ON e.org_id = k.org_id AND e.coll = 'agentHasKnowledge' AND e.to_coll = 'agentKnowledge' AND e.to_id = k.id
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'agentInstances' AND a.id = e.from_id AND coalesce((a.doc->>'isDeleted')::boolean, false) = false
            WHERE k.org_id = $1 AND k.coll = 'agentKnowledge' AND k.doc->>'connectorId' = $2""", [current_org_id(), connector_id], transaction)
        return dedupe_agents_by_id([dict(r) for r in rows])

    # ---- teams ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _team_dict(t: dict, cur_role: str | None, members: list, count: int, *, is_org_admin: bool = False) -> dict:
        owner = is_org_admin or cur_role == "OWNER"
        org = t.get(_ORG_FIELD)
        return {"id": t.get("id"), "name": t.get("name"), "description": t.get("description"), "createdBy": t.get("createdBy"), "orgId": org,
                "createdAtTimestamp": t.get("createdAtTimestamp"), "updatedAtTimestamp": t.get("updatedAtTimestamp"), "members": members, "memberCount": count,
                "canEdit": owner, "canDelete": owner and org is not None and t.get("id") != f"all_{org}", "canManageMembers": owner}

    @_soft(lambda: ([], 0))
    async def get_user_teams(self, user_key: str, search: str | None = None, page: int = 1, limit: int = 100, created_by: str | None = None,
                             created_after: int | None = None, created_before: int | None = None, transaction: str | None = None, *,
                             include_service_accounts: bool = False, org_id: str | None = None, is_org_admin: bool = False) -> tuple[list[dict], int]:
        """The user's teams; for an organisation administrator (with ``org_id``), every team of the organisation."""
        everyone = bool(is_org_admin and org_id)
        if everyone and not self._mine(org_id):
            return [], 0
        # one fixed statement: the source (every team of the organisation, or the user's), the filters, the page and the service-account switch are bound
        rows = await self._id_rows("""/* pipeshub:graph.get_user_teams */ WITH s AS (
              SELECT t.org_id, t.id, t.doc, NULL::text AS cur FROM graph.nodes t WHERE $4::boolean AND t.org_id = $1 AND t.coll = 'teams' AND t.doc->>'orgId' = $2
              UNION ALL
              SELECT t.org_id, t.id, t.doc, p.doc->>'role' AS cur FROM graph.edges p
              JOIN graph.nodes t ON t.org_id = p.org_id AND t.coll = 'teams' AND t.id = p.to_id
              WHERE NOT $4::boolean AND p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $3 AND p.to_coll = 'teams'),
            t AS (SELECT * FROM s t WHERE ($5::text IS NULL OR position(lower($5) in lower(coalesce(t.doc->>'name', ''))) > 0
                                          OR position(lower($5) in lower(coalesce(t.doc->>'description', ''))) > 0)
                AND ($6::text IS NULL OR t.doc->>'createdBy' = $6)
                AND ($7::numeric IS NULL OR (t.doc->>'createdAtTimestamp')::numeric >= $7)
                AND ($8::numeric IS NULL OR (t.doc->>'createdAtTimestamp')::numeric <= $8)),
            cnt AS (SELECT count(DISTINCT id) AS total FROM t)
            SELECT cnt.total, pg.doc, pg.cur, pg.members FROM cnt LEFT JOIN LATERAL (SELECT t.doc, t.cur, (SELECT coalesce(jsonb_agg(jsonb_build_object('id', mu.id, 'userId', mu.doc->'userId', 'userName', mu.doc->'fullName', 'userEmail', mu.doc->'email',
              'role', mp.doc->'role', 'joinedAt', mp.doc->'createdAtTimestamp', 'isOwner', coalesce(mp.doc->>'role' = 'OWNER', false)) ORDER BY mu.id), '[]'::jsonb)
          FROM graph.edges mp JOIN graph.nodes mu ON mu.org_id = mp.org_id AND mu.coll = 'users' AND mu.id = mp.from_id
          WHERE mp.org_id = t.org_id AND mp.coll = 'permission' AND mp.from_coll = 'users' AND mp.to_coll = 'teams' AND mp.to_id = t.id
            AND coalesce((mu.doc->>'isActive')::boolean, false) AND ($9::boolean OR (coalesce(mu.doc->>'kind', '') <> 'service' AND right(lower(coalesce(mu.doc->>'email', '')), length($10::text)) <> $10::text))) AS members
              FROM t ORDER BY t.doc->'createdAtTimestamp' DESC, t.id OFFSET $11 LIMIT $12) pg ON true""",
                                  [current_org_id(), org_id, user_key, everyone, search or None, created_by or None, created_after, created_before,
                                   include_service_accounts, _SERVICE_SUFFIX, (page - 1) * limit, limit], transaction)
        teams = []
        for r in rows:
            if r["doc"] is None:
                continue
            m = _js(r["members"])
            teams.append(self._team_dict(_doc(r["doc"]), r["cur"], m, len(m), is_org_admin=everyone))
        await self._enrich_created_by(teams, transaction)
        return teams, (rows[0]["total"] if rows else 0)

    async def _team_view(self, team_id: str, org_id: str, user_key: str, search: str | None, offset: int | None, limit: int | None,
                         transaction: str | None, *, include_service_accounts: bool = False, is_org_admin: bool = False) -> dict | None:
        if not self._mine(org_id):
            return None
        rows = await self._id_rows("""/* pipeshub:graph.team_view */ SELECT t.doc, (SELECT cp.doc->>'role' FROM graph.edges cp WHERE cp.org_id = t.org_id AND cp.coll = 'permission'
              AND cp.from_coll = 'users' AND cp.from_id = $3 AND cp.to_coll = 'teams' AND cp.to_id = t.id LIMIT 1) AS cur, (SELECT coalesce(jsonb_agg(jsonb_build_object('id', mu.id, 'userId', mu.doc->'userId', 'userName', mu.doc->'fullName', 'userEmail', mu.doc->'email',
              'role', mp.doc->'role', 'joinedAt', mp.doc->'createdAtTimestamp', 'isOwner', coalesce(mp.doc->>'role' = 'OWNER', false)) ORDER BY mu.id), '[]'::jsonb)
          FROM graph.edges mp JOIN graph.nodes mu ON mu.org_id = mp.org_id AND mu.coll = 'users' AND mu.id = mp.from_id
          WHERE mp.org_id = t.org_id AND mp.coll = 'permission' AND mp.from_coll = 'users' AND mp.to_coll = 'teams' AND mp.to_id = t.id
            AND coalesce((mu.doc->>'isActive')::boolean, false) AND ($5::boolean OR (coalesce(mu.doc->>'kind', '') <> 'service' AND right(lower(coalesce(mu.doc->>'email', '')), length($6::text)) <> $6::text))
            AND ($7::text IS NULL OR position(lower($7) in lower(coalesce(mu.doc->>'fullName', ''))) > 0 OR position(lower($7) in lower(coalesce(mu.doc->>'email', ''))) > 0)) AS members
            FROM graph.nodes t WHERE t.org_id = $1 AND t.coll = 'teams' AND t.id = $2 AND t.doc->>'orgId' = $4""",
                                  [current_org_id(), team_id, user_key, current_org_id(), include_service_accounts, _SERVICE_SUFFIX, search or None], transaction)
        if not rows:
            return None
        members = _js(rows[0]["members"])
        shown = members if offset is None else members[offset:offset + (limit or 0)]
        team = self._team_dict(_doc(rows[0]["doc"]), rows[0]["cur"], shown, len(members), is_org_admin=is_org_admin)
        await self._enrich_created_by([team], transaction)
        return team

    @_soft(None)
    async def get_team_with_users(self, team_id: str, org_id: str, user_key: str, transaction: str | None = None, *,
                                  include_service_accounts: bool = False, is_org_admin: bool = False) -> dict | None:
        return await self._team_view(team_id, org_id, user_key, None, None, None, transaction,
                                     include_service_accounts=include_service_accounts, is_org_admin=is_org_admin)

    async def find_team_by_name(self, org_id: str, name: str, exclude_team_id: str | None = None, transaction: str | None = None) -> dict | None:
        """The first team of the organisation with this name, ignoring case and surrounding whitespace (``{"id", "name"}``)."""
        if not self._mine(org_id):
            return None
        r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.find_team_by_name */ SELECT t.id, t.doc->>'name' AS name FROM graph.nodes t
            WHERE t.org_id = $1 AND t.coll = 'teams' AND t.doc->>'orgId' = $2 AND jsonb_typeof(t.doc->'name') = 'string'
              AND lower(btrim(t.doc->>'name')) = lower(btrim($3)) AND t.id <> $4 ORDER BY t.id LIMIT 1""",
                                                 current_org_id(), org_id, name, exclude_team_id or ""), transaction)
        return {"id": r["id"], "name": r["name"]} if r else None

    @_soft(None)
    async def get_team_users(self, team_id: str, org_id: str, user_key: str, search: str | None = None, page: int = 1, limit: int = 100,
                             transaction: str | None = None, *, include_service_accounts: bool = False) -> dict | None:
        return await self._team_view(team_id, org_id, user_key, search, (page - 1) * limit, limit, transaction,
                                     include_service_accounts=include_service_accounts)

    @_soft(list)
    async def delete_team_member_edges(self, team_id: str, user_ids: list[str], transaction: str | None = None) -> list[dict]:
        rows = await self._id_rows("""/* pipeshub:graph.delete_team_member_edges */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND from_coll = 'users'
            AND from_id = ANY($2::text[]) AND to_coll = 'teams' AND to_id = $3 RETURNING doc, from_id, to_id""", [current_org_id(), list(user_ids), team_id], transaction, write=True)
        out = []
        for r in rows:
            e = {**_doc(r["doc"]), "from_id": r["from_id"], "to_id": r["to_id"], "from_collection": "users", "to_collection": "teams"}
            e["_from"], e["_to"] = f"users/{r['from_id']}", f"teams/{r['to_id']}"
            if "id" in e:
                e.setdefault("_key", e["id"])
            out.append(e)
        return out

    async def get_team_owner_removal_info(self, team_id: str, user_ids: list[str], transaction: str | None = None) -> dict[str, Any]:
        rows = await self._id_rows("""/* pipeshub:graph.get_team_owner_removal_info */ SELECT array_agg(p.from_id) FILTER (WHERE p.from_coll = 'users' AND p.from_id = ANY($3::text[])) AS removing,
              count(*) AS total FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = 'teams' AND p.to_id = $2 AND p.doc->>'role' = 'OWNER'""",
                                [current_org_id(), team_id, list(user_ids)], transaction)
        return {"owners_being_removed": list(rows[0]["removing"] or []), "total_owner_count": rows[0]["total"]}

    # ---- knowledge bases: the collection itself ------------------------------------------------------------------
    async def get_knowledge_base(self, kb_id: str, user_id: str, transaction: str | None = None) -> dict | None:
        """The collection with the user's role and its folders; None when it does not exist or the user holds no role on it."""
        role = await self.get_user_kb_permission(kb_id, user_id, transaction)
        rows = await self._id_rows("""/* pipeshub:graph.get_knowledge_base */ SELECT kb.doc, (SELECT coalesce(jsonb_agg(jsonb_build_object('id', f.id, 'name', f.doc->'recordName',
              'createdAtTimestamp', f.doc->'createdAtTimestamp', 'updatedAtTimestamp', f.doc->'updatedAtTimestamp', 'webUrl', f.doc->'webUrl', 'mimeType', f.doc->'mimeType')
              ORDER BY f.doc->>'recordName', f.id), '[]'::jsonb) FROM graph.edges b JOIN graph.nodes f ON f.org_id = b.org_id AND f.coll = 'records' AND f.id = b.from_id
              WHERE b.org_id = kb.org_id AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.to_id = kb.id AND f.doc->>'mimeType' = ANY($3::text[])) AS folders
            FROM graph.nodes kb WHERE kb.org_id = $1 AND kb.coll = 'apps' AND kb.id = $2 AND kb.doc->>'type' = $4""", [current_org_id(), kb_id, FOLDER_MIME_TYPES, _KB], transaction)
        if not rows or not role:
            return None
        d = _doc(rows[0]["doc"])
        return {"id": d.get("id"), "name": d.get("name") if d.get("name") is not None else "Untitled", "description": d.get("description"),
                "createdAtTimestamp": d.get("createdAtTimestamp"), "updatedAtTimestamp": d.get("updatedAtTimestamp"), "createdBy": d.get("createdBy"),
                "userRole": role, "status": d.get("status"), "folders": _js(rows[0]["folders"])}

    async def update_knowledge_base(self, kb_id: str, updates: dict, transaction: str | None = None) -> dict | None:
        clean = {k: v for k, v in updates.items() if k not in ("id", "_key")}
        rows = await self._id_rows("""/* pipeshub:graph.update_knowledge_base */ UPDATE graph.nodes SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(doc || $3::jsonb) AS _nv
                WHERE _nv.value <> 'null'::jsonb)
            WHERE org_id = $1 AND coll = 'apps' AND id = $2 AND doc->>'type' = $4 RETURNING doc""", [current_org_id(), kb_id, _j(clean), _KB], transaction, write=True)
        return _arango(_doc(rows[0]["doc"]), "apps") if rows else None

    async def _get_kb_context_for_record(self, record_id: str, transaction: str | None = None) -> dict | None:
        rows = await self._id_rows("""/* pipeshub:graph.get_kb_context_for_record */ SELECT kb.id, kb.doc->>'name' AS name, kb.doc->>'orgId' AS org FROM graph.edges b
            JOIN graph.nodes kb ON kb.org_id = b.org_id AND kb.coll = 'apps' AND kb.id = b.to_id AND kb.doc->>'type' = $3
            WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'records' AND b.from_id = $2 ORDER BY kb.id LIMIT 1""", [current_org_id(), record_id, _KB], transaction)
        return {"kb_id": rows[0]["id"], "kb_name": rows[0]["name"], "org_id": rows[0]["org"]} if rows else None


    # ---- organizations, people, and the email of a login user ---------------------------------------------------------------------
    @_soft(False)
    async def organization_exists(self, organization_name: str, is_external: bool = False) -> bool:  # noqa: FBT001, FBT002
        """Whether the ambient org holds an organization of this name (an external one when asked); another org's are out of reach."""
        rows = await self._id_rows("""/* pipeshub:graph.organization_exists */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'organizations' AND doc->>'name' = $2
            AND coalesce((doc->>'isExternal')::boolean, false) = $3 LIMIT 1""", [current_org_id(), organization_name, is_external], None)
        return bool(rows)

    @_soft(None)
    async def get_app_user_by_email(self, email: str, connector_id: str, transaction: str | None = None) -> AppUser | None:
        rows = await self._id_rows("""/* pipeshub:graph.get_app_user_by_email */ SELECT u.doc, r.doc->>'sourceUserId' AS sid FROM graph.edges r
            JOIN graph.nodes u ON u.org_id = r.org_id AND u.coll = 'users' AND u.id = r.from_id
            WHERE r.org_id = $1 AND r.coll = 'userAppRelation' AND r.from_coll = 'users' AND r.to_coll = 'apps' AND r.to_id = $2 AND lower(u.doc->>'email') = lower($3)
            ORDER BY u.id LIMIT 1""", [current_org_id(), connector_id, email], transaction)
        return AppUser.from_arango_user({**_arango(_doc(rows[0]["doc"]), "users"), "sourceUserId": rows[0]["sid"]}) if rows else None

    @_soft(None, flag=True)
    async def get_person_by_email(self, email: str, org_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> Person | None:
        """The person of this org with this email (stored lowercase). Persons are looked up by a document field: there is no index for it."""
        if not self._mine(org_id):
            return None
        d = await self._one("/* pipeshub:graph.get_person_by_email */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND doc->>'email' = $2 ORDER BY id LIMIT 1",
                            current_org_id(), email.lower(), transaction=transaction)
        return Person.from_arango_person(_arango(d, "person")) if d else None

    @_soft(None, flag=True)
    async def upsert_person_by_email(self, person: Person, transaction: str | None = None, *, raise_on_error: bool = False) -> str | None:
        """The id of the person of this org with this email, created when there is none; an existing one is never changed (it may hold names a
        connector that knows only an email would blank). Two creators of one email are serialised by a lock on it. A person of another org is refused."""
        org = current_org_id()
        node = person.to_arango_person()
        self._require_org(person.org_id)
        async with self._atomic(transaction) as tid:
            await self._id_rows("/* pipeshub:graph.upsert_person_lock */ SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [f"person:{org}:{node['email']}"], tid, write=True)
            rows = await self._id_rows("/* pipeshub:graph.upsert_person_find */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'person' AND doc->>'email' = $2 ORDER BY id LIMIT 1",
                                       [org, node["email"]], tid)
            if rows:
                return rows[0]["id"]
            return node["_key"] if await self.create_node_if_absent(node["_key"], "person", node, tid) else None

    async def apply_verified_user_email(self, user_id: str, org_id: str, email: str) -> dict | None:
        """Sets the login user's email and merges into them the connector stubs that hold it: their edges move over (the stronger permission role wins,
        edges between the two and ones the login has are left as they are) and the stubs go, all in one transaction. A second real login with the
        email is a conflict. None when the login user is not there."""
        self._require_org(org_id)
        org = current_org_id()
        async with self._atomic(None) as tid:
            # Two verifications of one address are serialised: the second reads what the first committed, and finds its login user there as a conflict.
            await self._id_rows("/* pipeshub:graph.verified_email_lock */ SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [f"verified_email:{org}:{email.lower()}"], tid, write=True)
            found = await self._id_rows("/* pipeshub:graph.verified_email_login */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND doc->>'userId' = $2 LIMIT 1",
                                        [org, user_id], tid)
            keep_key = graph_user_key(_arango(_doc(found[0]["doc"]), "users")) if found else None
            if not keep_key:
                return None
            peers = [_arango(_doc(r["doc"]), "users") for r in await self._id_rows(
                "/* pipeshub:graph.verified_email_peers */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND lower(doc->>'email') = lower($2) ORDER BY id",
                [org, email], tid)]
            stubs: list[str] = []
            for peer in peers:
                kind = classify_email_peer(user_id, keep_key, peer)
                if kind == "login":
                    raise GraphUserEmailConflictError("Email already belongs to another login user in the graph", conflicting_user_id=str(peer.get("userId") or ""))
                if kind == "stub" and (key := graph_user_key(peer)):
                    stubs.append(key)
            connector_ids = await connector_ids_of_users(self, stubs)
            for stub in stubs:
                await self._absorb_user_stub(keep_key, stub, tid)
            await self.batch_upsert_nodes([{"id": keep_key, "userId": user_id, "orgId": org, "email": email, "updatedAtTimestamp": get_epoch_timestamp_in_ms()}], "users", tid)
        return {"email": email, "mergedStubKeys": stubs, "connectorIds": connector_ids}

    async def _absorb_user_stub(self, keep_key: str, stub_key: str, tid: str) -> None:
        """Moves the stub's edges to the login user, then removes the stub. Edges between the two are dropped with the stub; an edge the login already has (same other end and
        key) stays, except that a stronger permission role replaces it."""
        def rank(e: dict) -> int:
            return PERMISSION_ROLE_RANK.get(str(e.get("role") or "").upper(), 0)

        for coll in STUB_EDGE_COLLECTIONS:
            moved: list[dict] = []
            for incoming in (False, True):
                read = self.get_edges_to_node if incoming else self.get_edges_from_node

                held = {_far(coll, incoming, e): e for e in await read(f"users/{keep_key}", coll, tid, raise_on_error=True)}
                for e in await read(f"users/{stub_key}", coll, tid, raise_on_error=True):
                    k = _far(coll, incoming, e)
                    have = held.get(k)
                    if k[1] in (stub_key, keep_key) or (have is not None and not (coll == "permission" and rank(e) > rank(have))):
                        continue
                    props = {k2: v for k2, v in {**(have or {}), **e}.items() if k2 not in ("_from", "_to", "_key", "from_id", "to_id", "from_collection", "to_collection")}
                    ends = {"from_collection": k[0], "from_id": k[1], "to_collection": "users", "to_id": keep_key} if incoming else {
                        "from_collection": "users", "from_id": keep_key, "to_collection": k[0], "to_id": k[1]}
                    moved.append({**props, **ends})
            if moved:
                await self.batch_create_edges(moved, coll, tid)
        await self.delete_nodes([stub_key], "users", tid)


    # ---- the "All" team, which holds every member of the org ----------------------------------------------------------------------
    async def _all_team(self, org_id: str, tid: str) -> str:
        """The id of the org's "All" team, created when it is not there. Callers hold the team's lock, so two of them never both see it empty."""
        team_id, now = f"all_{org_id}", get_epoch_timestamp_in_ms()
        await self._id_rows("/* pipeshub:graph.all_team_lock */ SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", [f"all_team:{current_org_id()}:{team_id}"], tid, write=True)
        await self.create_node_if_absent(team_id, "teams", {"id": team_id, "name": "All", "description": "All organization members", "createdBy": "system", "orgId": org_id,
                                                           "createdAtTimestamp": now, "updatedAtTimestamp": now}, tid)
        return team_id

    async def _join_all_team(self, team_id: str, user_keys: list[str], *, owner_if_empty: bool, tid: str) -> None:
        """Gives each user without an edge to the team a READER one; when the team has no member the first gets OWNER and becomes its creator. The team is locked."""
        now = get_epoch_timestamp_in_ms()
        held = {r["from_id"] for r in await self._id_rows("""/* pipeshub:graph.all_team_members */ SELECT p.from_id FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission'
            AND p.to_coll = 'teams' AND p.to_id = $2""", [current_org_id(), team_id], tid)}
        edges = []
        for key in user_keys:
            if key in held:
                continue
            role = "OWNER" if owner_if_empty and not held else "READER"
            held.add(key)
            if role == "OWNER":
                await self.update_node(team_id, "teams", {"createdBy": key, "updatedAtTimestamp": now}, tid)
            edges.append({"from_id": key, "from_collection": "users", "to_id": team_id, "to_collection": "teams", "type": "USER", "role": role,
                          "createdAtTimestamp": now, "updatedAtTimestamp": now})
        if edges:
            await self.batch_create_edges(edges, "permission", tid)

    async def add_user_to_all_team(self, org_id: str, user_key: str) -> None:
        """Adds the user to the org's "All" team (made when missing): OWNER if nobody is in it, else READER. Idempotent. A user who is not in the graph is not added."""
        self._require_org(org_id)
        async with self._atomic(None) as tid:
            team_id = await self._all_team(org_id, tid)
            # a read that raises, not check_collection_has_document: a lookup that failed is not "the user is not in the graph"
            if await self._id_rows("/* pipeshub:graph.all_team_user_exists */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", [current_org_id(), user_key], tid):
                await self._join_all_team(team_id, [user_key], owner_if_empty=True, tid=tid)

    async def ensure_all_team_with_users(self, org_id: str) -> None:
        """Makes sure every active user of the org is in its "All" team: the oldest becomes OWNER if the team was empty, the rest READER. Idempotent."""
        self._require_org(org_id)
        async with self._atomic(None) as tid:
            team_id = await self._all_team(org_id, tid)
            users = sorted(await self.get_users(org_id, active=True), key=lambda u: u.get("createdAtTimestamp", 0))
            await self._join_all_team(team_id, [k for u in users if (k := u.get("id"))], owner_if_empty=True, tid=tid)

    # ---- what an agent still uses, and counts of connector instances ----------------------------------------------------------------
    async def check_api_toolset_in_use(self, instance_id: str, transaction: str | None = None) -> list[str]:
        """Names of the live agents with an operation of this API toolset attached (an entry of ``apiToolsetTools`` starting ``<instance>:``)."""
        rows = await self._id_rows("""/* pipeshub:graph.check_api_toolset_in_use */ SELECT DISTINCT a.id AS "agentId", a.doc->>'name' AS "agentName" FROM graph.nodes a
            WHERE a.org_id = $1 AND a.coll = 'agentInstances' AND coalesce((a.doc->>'isDeleted')::boolean, false) = false AND jsonb_typeof(a.doc->'apiToolsetTools') = 'array'
              AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(a.doc->'apiToolsetTools') t WHERE left(t, length($2)) = $2)""", [current_org_id(), f"{instance_id}:"], transaction)
        return dedupe_agents_by_id([dict(r) for r in rows])

    async def check_mcp_instance_in_use(self, instance_id: str, transaction: str | None = None) -> list[str]:
        """Names of the live agents with this MCP server instance attached."""
        rows = await self._id_rows("""/* pipeshub:graph.check_mcp_instance_in_use */ SELECT DISTINCT a.id AS "agentId", a.doc->>'name' AS "agentName" FROM graph.nodes s
            JOIN graph.edges e ON e.org_id = s.org_id AND e.coll = 'agentHasMcpServer' AND e.to_coll = 'agentMcpServers' AND e.to_id = s.id
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'agentInstances' AND a.id = e.from_id AND coalesce((a.doc->>'isDeleted')::boolean, false) = false
            WHERE s.org_id = $1 AND s.coll = 'agentMcpServers' AND s.doc->>'instanceId' = $2""", [current_org_id(), instance_id], transaction)
        return dedupe_agents_by_id([dict(r) for r in rows])

    async def check_toolset_instance_in_use(self, instance_id: str, transaction: str | None = None) -> list[str]:
        """Names of the live agents using a toolset of this instance; two agents with one name count apart."""
        rows = await self._id_rows("""/* pipeshub:graph.check_toolset_instance_in_use */ SELECT DISTINCT a.id AS "agentId", a.doc->>'name' AS "agentName" FROM graph.nodes s
            JOIN graph.edges e ON e.org_id = s.org_id AND e.coll = 'agentHasToolset' AND e.to_coll = 'agentToolsets' AND e.to_id = s.id
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'agentInstances' AND a.id = e.from_id AND coalesce((a.doc->>'isDeleted')::boolean, false) = false
            WHERE s.org_id = $1 AND s.coll = 'agentToolsets' AND s.doc->>'instanceId' = $2""", [current_org_id(), instance_id], transaction)
        return dedupe_agents_by_id([dict(r) for r in rows])

    async def check_connector_name_uniqueness(self, instance_name: str, scope: str, org_id: str, user_id: str, collection: str, edge_collection: str | None = None,
                                              transaction: str | None = None) -> bool:
        """Whether no other instance of the scope holds this name (without case or surrounding space): the user's own for a personal one, the org's for a team one.
        A check that cannot be made lets the name through, as on the other stores."""
        # edge_collection is ignored: orgAppRelation is the only relation an org holds its instances by
        try:
            return not await self.check_connector_name_exists(collection, instance_name, scope, org_id, user_id, transaction)
        except (MissingOrgContext, SystemContextNotAllowed, OrgMismatch):
            raise
        except Exception as e:
            self.logger.error("Error checking name uniqueness: %s", e)
            return True

    @_soft(0)
    async def count_connector_instances_by_scope(self, collection: str, scope: str, user_id: str | None = None, *, is_admin: bool = False, transaction: str | None = None) -> int:
        """How many configured instances of the scope there are (a personal scope narrowed to the user's own when one is given)."""
        rows = await self._id_rows("""/* pipeshub:graph.count_connector_instances_by_scope */ SELECT count(*) AS n FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
            AND n.doc->>'scope' = $3 AND n.doc->>'isConfigured' = 'true' AND ($3 <> 'personal' OR $4::text IS NULL OR n.doc->>'createdBy' = $4)""",
                                  [current_org_id(), collection, scope, user_id or None], transaction)
        return rows[0]["n"]
