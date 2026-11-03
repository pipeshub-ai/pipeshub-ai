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

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service_accounts import SERVICE_ACCOUNT_EMAIL_DOMAIN
from app.core.org_context import (
    MissingOrgContext,
    OrgMismatch,
    SystemContextNotAllowed,
    current_or_system,
    current_org_id,
)
from app.models.entities import AppRole, AppUserGroup, User
from app.services.graph_db.postgres.base import (
    NotImplementedInPostgres,
    PgBase,
    _arango,
    _doc,
    _j,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


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

    # ---- users and orgs ------------------------------------------------------------------------------------------
    @_soft(None, flag=True)
    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict | None:
        d = await self._one("/* pipeshub:graph.get_user_by_user_id */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND doc->>'userId' = $2 LIMIT 1",
                            current_org_id(), user_id)
        return _arango(d, "users") if d else None

    @_soft(None, flag=True)
    async def get_user_by_email(self, email: str, transaction: str | None = None, *, raise_on_error: bool = False) -> User | None:
        d = await self._one("/* pipeshub:graph.get_user_by_email */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND lower(doc->>'email') = lower($2) LIMIT 1",
                            current_org_id(), email, transaction=transaction)
        return User.from_arango_user(_arango(d, "users")) if d else None

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
