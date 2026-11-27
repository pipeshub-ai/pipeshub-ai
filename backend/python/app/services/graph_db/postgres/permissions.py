"""Permissions: who a user is to the graph, and the batch access check that every permission decision goes through."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors, RecordTypes
from app.config.constants.neo4j import parse_node_id
from app.core.org_context import current_or_system, current_org_id
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import AppUserGroup, User, substitute_user_email
from app.models.permission import ORG_SHARE_PERMISSION_TYPES, RETIRED_ROLES
from app.models.source_dates import DATED_ONLY_TIME_RANGE_KEY, upload_times_undated
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.team_grant_role import (
    TEAM_GRANT_ROLE_CAP,
    TEAM_GRANT_ROLES,
)
from app.services.graph_db.interface.graph_db_provider import (
    STRICT_SCOPE_FILTER_KEY,
    AccessibleContainers,
    _distinct_connector_types,
    _unsupported_container_filters,
    requested_scope_ids,
)
from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.base import _VALIDATOR, PgBase, _arango, _doc, _j
from app.services.graph_db.postgres.identity import _AGENT_CAN, _soft
from app.services.graph_db.postgres.kg import listing as kgl
from app.services.graph_db.postgres.kg import read as kg_read
from app.services.graph_db.postgres.kh_listing import UNDECIDED_MAX, class_sets
from app.services.graph_db.taxonomy import own_record_labels
from app.utils import error_class
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import Tx

_NO_ACCESS: dict[str, Any] = {"grantee_ids": [], "gated_app_ids": [], "by_connector": {}}
# Every grant of a viewer in one statement, so a writable transaction (read committed, a snapshot per statement) never joins two states of them: the live
# records and groups that the grantees ($2) hold a permission edge to (an organisation's only of a sharing type, $4), and, inside its own connector only,
# what a source account the user authenticated as ($5, connector $6, collection $7) and its groups, roles and teams hold (never an organisation's), each
# holder read by its collection and id, so a node sharing another's id holds nothing. A group, role or team counts only while it is a node, as the user's own
# do (``_gate``): every writer of a membership reads the group's node first and a delete takes the node's edges with it, so a membership of a group that is
# gone is one written beside the group's delete, never one ahead of the group's sync. Edges by the key, nodes by primary key (LATERAL ... OFFSET 0: a
# lookup per row whatever the statistics say).
_GRANTS_SQL = """/* pipeshub:graph.access_grants_all */ WITH acct AS (SELECT * FROM unnest($5::text[], $6::text[], $7::text[]) AS a(id, cid, coll)),
  holder AS (SELECT a.coll, a.id, a.cid FROM acct a
    UNION SELECT b.to_coll, b.to_id, a.cid FROM acct a CROSS JOIN LATERAL (SELECT b.to_coll, b.to_id FROM graph.edges b WHERE b.org_id = $1 AND b.coll = 'permission'
      AND b.from_coll = a.coll AND b.from_id = a.id AND b.to_coll IN ('groups', 'roles', 'teams') AND b.doc->>'type' = 'USER'
      AND EXISTS (SELECT 1 FROM graph.nodes g WHERE g.org_id = $1 AND g.coll = b.to_coll AND g.id = b.to_id) OFFSET 0) b),
  hit AS (SELECT e.to_coll, e.to_id, NULL::text AS only_cid FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_id = ANY($2::text[])
      AND e.from_coll = ANY($3::text[]) AND e.to_coll IN ('records', 'recordGroups') AND (e.from_coll <> 'organizations' OR e.doc->>'type' = ANY($4::text[]))
    UNION ALL SELECT e.to_coll, e.to_id, h.cid FROM holder h CROSS JOIN LATERAL (SELECT e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'permission'
      AND e.from_id = h.id AND e.from_coll = h.coll AND e.to_coll IN ('records', 'recordGroups') OFFSET 0) e)
SELECT n.cid, h.to_id AS id FROM hit h CROSS JOIN LATERAL (SELECT n.doc->>'connectorId' AS cid FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = h.to_coll
    AND n.id = h.to_id AND NOT coalesce((n.doc->>'isDeleted')::boolean, false) OFFSET 0) n
WHERE n.cid IS NOT NULL AND (h.only_cid IS NULL OR n.cid = h.only_cid)"""


_ROLE_RANK = {"OWNER": 4, "WRITER": 3, "READER": 2}
_KB_TYPE = Connectors.KNOWLEDGE_BASE.value
_CTX_RANK = {"OWNER": 6, "ADMIN": 5, "EDITOR": 4, "WRITER": 3, "READER": 1}
_DETAIL_RANK = {"OWNER": 3, "WRITER": 2, "READER": 1}      # the roles a record's access details name
_CTX_UP = ("OWNER", "ADMIN", "EDITOR", "WRITER")
_CANDIDATE_COLUMNS = """r.doc->>'virtualRecordId' AS vrid, r.doc->>'connectorId' AS app,
    NOT coalesce((r.doc->>'isDeleted')::boolean, false) AND NOT coalesce((r.doc->>'isPlaceholder')::boolean, false)
    AND (r.doc->'isInternal' IS NULL OR r.doc->'isInternal' IN ('null', 'false', '0', '""', '[]', '{}'))
    AND (r.doc->'isPlaceholder' IS NULL OR r.doc->'isPlaceholder' IN ('null', 'false', '0', '""', '[]', '{}')) AS plain"""
_CHECK_CHUNK = 2000     # candidate records handed to the access check at once
# the metadata filters of a search: key, the edge from the record, the collection of the item, the field its name is in
_META_FILTERS = (("departments", "belongsToDepartment", "departments", "departmentName"), ("categories", "belongsToCategory", "categories", "name"),
                 ("subcategories1", "belongsToCategory", "subcategories1", "name"), ("subcategories2", "belongsToCategory", "subcategories2", "name"),
                 ("subcategories3", "belongsToCategory", "subcategories3", "name"), ("languages", "belongsToLanguage", "languages", "name"),
                 ("topics", "belongsToTopic", "topics", "name"))


def _ctx_perms(role: str, *, admin: bool | None = None) -> dict[str, Any]:
    """What a role may do inside a folder, collection or connector; at the root (``admin`` given) only an administrator may."""
    up = admin if admin is not None else role in _CTX_UP
    return {"role": role, "canUpload": up, "canCreateFolders": up, "canEdit": up,
            "canDelete": admin if admin is not None else role in ("OWNER", "ADMIN", "WRITER"),
            "canManagePermissions": admin if admin is not None else role in ("OWNER", "ADMIN")}


_INHERIT_HOPS = 8     # the longest INHERIT_PERMISSIONS chain the walk to a node's grantees follows, as on the other stores


def _best_role(rows: Any, coll: str, node_id: str, ranks: dict[str, int]) -> tuple[str, str] | None:  # noqa: ANN401
    """The highest role of the grant rows of ``_role_on`` (or ``_roles_on``) for the node ``(coll, node_id)``, with its source."""
    best: tuple[str, str] | None = None
    for r in rows:
        role = r["role"]
        if r["kind"] == "team":
            role = r["role"] if r["role"] else r["member_role"]
            role = TEAM_GRANT_ROLE_CAP if role == "OWNER" else role
        if role in RETIRED_ROLES:
            role = "READER"
        if r["kind"] == "direct" and r["app_type"] == _KB_TYPE and role not in TEAM_GRANT_ROLES:
            continue      # a direct grant on a collection counts only with a collection role
        if r["kind"] == "team" and role not in TEAM_GRANT_ROLES:
            continue
        if role not in ranks:
            continue
        source = ("KNOWLEDGE_BASE" if r["app_type"] == _KB_TYPE else "DIRECT") if r["kind"] == "direct" else {
            "group": "GROUP", "team": "KNOWLEDGE_BASE_TEAM" if r["tcoll"] == "apps" else "GROUP", "org": "ORGANIZATION"}[r["kind"]]
        if r["kind"] == "direct" and not (r["tcoll"] == "apps" or (r["tcoll"], r["tid"]) == (coll, node_id)):
            source = "DIRECT_USER_RECORD_GROUP"
        if best is None or ranks[role] > ranks[best[0]] or (ranks[role] == ranks[best[0]] and source < best[1]):      # a tie goes to the source that sorts first, whatever the row order
            best = (role, source)
    return best


# A node whose rule needs a grant of its own (RESTRICTED, PARENT_AND_GRANT) takes its role from its own grants alone: the role walk reads the rule in the
# same statement as the grants (one snapshot) and does not leave such a node, so a grant on what it inherits from never gives or raises its role
# (the ``own_only`` column of ``_role_on``'s and ``_roles_on``'s walk).


class PermissionsMixin(PgBase):
    async def get_user_kb_permission(self, kb_id: str, user_id: str, transaction: str | None = None) -> str | None:
        """The user's effective role on a collection: the highest of the direct grant and what each team the collection is shared with gives its members.
        A team's grant gives the role on its own edge, else the member's role in the team, and never more than ``TEAM_GRANT_ROLE_CAP``: a team owner
        does not own what the team is shared with. A retired role reads as READER; a role that is none of the grant roles gives nothing, the direct grant's too."""
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_user_kb_permission */
            WITH me AS (SELECT 1 FROM graph.nodes u WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $2),
            kb AS (SELECT k.id FROM graph.nodes k WHERE k.org_id = $1 AND k.coll = 'apps' AND k.id = $3 AND k.doc->>'type' = 'KB')
            SELECT CASE WHEN d.doc->>'role' = ANY($5::text[]) THEN 'READER' WHEN d.doc->>'role' = ANY($6::text[]) THEN d.doc->>'role' END AS role FROM me, kb JOIN graph.edges d ON d.org_id = $1 AND d.coll = 'permission' AND d.from_coll = 'users' AND d.from_id = $2
              AND d.to_coll = 'apps' AND d.to_id = kb.id AND d.doc->>'type' = 'USER'
            UNION ALL
            SELECT CASE WHEN b.role = 'OWNER' THEN $4::text WHEN b.role = ANY($5::text[]) THEN 'READER' WHEN b.role = ANY($6::text[]) THEN b.role END
            FROM me, kb JOIN graph.edges ut ON ut.org_id = $1 AND ut.coll = 'permission' AND ut.from_coll = 'users' AND ut.from_id = $2 AND ut.to_coll = 'teams' AND ut.doc->>'type' = 'USER'
              JOIN graph.nodes t ON t.org_id = $1 AND t.coll = 'teams' AND t.id = ut.to_id
              JOIN graph.edges tg ON tg.org_id = $1 AND tg.coll = 'permission' AND tg.from_coll = 'teams' AND tg.from_id = t.id AND tg.to_coll = 'apps' AND tg.to_id = kb.id AND tg.doc->>'type' = 'TEAM',
              LATERAL (SELECT CASE WHEN tg.doc->>'role' IS NOT NULL AND tg.doc->>'role' <> '' THEN tg.doc->>'role' ELSE ut.doc->>'role' END AS role) b""",
                                                  org, user_id, kb_id, TEAM_GRANT_ROLE_CAP, sorted(RETIRED_ROLES), list(TEAM_GRANT_ROLES)), transaction)
        # Fail closed: only a grant role (a retired one already read as READER in the statement) counts; any other stored role gives nothing, so it
        # can neither be ranked nor handed back as a role.
        roles = [r["role"] for r in rows if r["role"] in _ROLE_RANK]
        return max(roles, key=_ROLE_RANK.__getitem__) if roles else None

    async def _gate(self, tx: Tx, org: str, user_key: str) -> tuple[list[str], list[str], list[Any]] | None:
        """Who the user is (``grantees``), the apps they pass the connector gate for, and the source accounts they authenticated as; None when the
        user is no node of the graph. The part of ``get_knowledge_hub_access_v3`` that does not read the grants."""
        # single-table lookups (edges by the key, nodes by primary key): no plan depends on table statistics

        async def exist(coll: str, ids: list[str]) -> set[str]:
            if not ids:
                return set()
            # a withdrawn SourceAccount links nobody (its links are kept for when it is listed again)
            return {r["id"] for r in await tx.fetch("/* pipeshub:graph.access_exist */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])"
                                                    " AND coalesce(doc->>'state', '') <> 'withdrawn'", org, coll, ids)}

        via = await tx.fetch("""/* pipeshub:graph.access_grantee_edges */ SELECT e.coll, e.to_coll, e.to_id, e.doc->>'type' AS t FROM graph.edges e WHERE e.org_id = $1
            AND e.coll IN ('permission', 'belongsTo') AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll IN ('groups', 'roles', 'teams', 'organizations')""", org, user_key)
        grantees = sorted(await exist("users", [user_key]))
        for coll in ("groups", "roles", "teams"):
            grantees += sorted(await exist(coll, [r["to_id"] for r in via if r["coll"] == "permission" and r["to_coll"] == coll and r["t"] == "USER"]))
        grantees += sorted(await exist("organizations", [r["to_id"] for r in via if r["coll"] == "belongsTo" and r["to_coll"] == "organizations"]))
        if user_key not in grantees:
            return None
        links = await tx.fetch("""/* pipeshub:graph.access_links */ SELECT e.to_coll, e.to_id, e.doc->>'connectorId' AS cid FROM graph.edges e
            WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = ANY($3::text[])""",
                              org, user_key, list(access_sql.SOURCE_ACCOUNT_COLLS))
        live = {(c, i) for c in access_sql.SOURCE_ACCOUNT_COLLS for i in await exist(c, [r["to_id"] for r in links if r["to_coll"] == c])}
        links = [r for r in links if (r["to_coll"], r["to_id"]) in live]          # the source account must exist
        # A grant on a collection (an app of type KB) counts only with a role it recognises, as ``get_user_kb_permission`` reads it: a retired role is
        # READER, a role of OWNER, WRITER or READER is itself, a team's grant without a role takes the member's role in the team; any other role, or none,
        # is no grant. A connector's grant has no role to read.
        app_ids = {r["to_id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate_edges */ SELECT e.to_id FROM graph.edges e
            JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id
            WHERE e.org_id = $1 AND e.coll IN ('permission', 'userAppRelation') AND e.from_coll = ANY($3::text[]) AND e.to_coll = 'apps' AND e.from_id = ANY($2::text[])
              AND (e.coll <> 'permission' OR coalesce(a.doc->>'type', '') <> 'KB' OR coalesce(nullif(e.doc->>'role', ''), CASE WHEN e.from_coll = 'teams' THEN (
                    SELECT ut.doc->>'role' FROM graph.edges ut WHERE ut.org_id = $1 AND ut.coll = 'permission' AND ut.from_coll = 'users' AND ut.from_id = $4
                      AND ut.to_coll = 'teams' AND ut.to_id = e.from_id AND ut.doc->>'type' = 'USER' LIMIT 1) END) = ANY($5::text[]))""",
                                              org, grantees, access_sql.PRINCIPALS, user_key, [*_ROLE_RANK, *sorted(RETIRED_ROLES)])}
        app_ids |= {x["cid"] for x in links if x["cid"]}
        gated = [r["id"] for r in await tx.fetch("""/* pipeshub:graph.access_gate */ SELECT a.id FROM graph.nodes a WHERE a.org_id = $1 AND a.coll = 'apps'
            AND a.doc->>'orgId' = $1 AND a.id = ANY($2::text[])""", org, sorted(app_ids))]
        return grantees, gated, links

    async def get_knowledge_hub_access_v3(self, user_key: str, org_id: str, *, transaction: str | None = None) -> dict[str, Any]:
        """Who this user is (the user, the groups, roles and teams they hold a USER permission on, their organization), the apps they pass the
        connector gate for, and the records and groups granted to them, per connector. An org other than the ambient one is nobody's."""
        org = current_org_id()
        if org_id != org:
            return dict(_NO_ACCESS)

        async def go(tx: Tx) -> dict:
            gate = await self._gate(tx, org, user_key)
            if gate is None:
                return dict(_NO_ACCESS)
            grantees, gated, links = gate
            by_conn: dict[str, list[str]] = {a: [] for a in gated}
            linked = [lk for lk in links if lk["cid"] and lk["cid"] in by_conn]
            for r in await tx.fetch(_GRANTS_SQL, org, grantees, access_sql.PRINCIPALS, list(ORG_SHARE_PERMISSION_TYPES),
                                    [lk["to_id"] for lk in linked], [lk["cid"] for lk in linked], [lk["to_coll"] for lk in linked]):
                if r["cid"] in by_conn:
                    by_conn[r["cid"]].append(r["id"])
            return {"grantee_ids": grantees, "gated_app_ids": gated, "by_connector": {k: list(dict.fromkeys(v)) for k, v in by_conn.items()}}
        # one snapshot outside a transaction: the gate and the grants are one state of the graph
        return await self._q(go, transaction, isolation="repeatable_read")

    async def _linked_holders(self, tx: Tx, org: str, links: list[Any], gated: list[str]) -> dict[str, list[tuple[str, str]]]:
        """Per gated connector, the (collection, id) of the source accounts the user authenticated it as and of their groups, roles and teams: their grants count
        inside that connector only."""
        out: dict[str, list[tuple[str, str]]] = {}
        for lk in links:
            if not lk["cid"] or lk["cid"] not in gated:
                continue
            badges = [(r["to_coll"], r["to_id"]) for r in await tx.fetch("""/* pipeshub:graph.access_linked_groups */ SELECT b.to_coll, b.to_id FROM graph.edges b WHERE b.org_id = $1
                AND b.coll = 'permission' AND b.from_coll = $3 AND b.from_id = $2 AND b.to_coll IN ('groups', 'roles', 'teams') AND b.doc->>'type' = 'USER'
                AND EXISTS (SELECT 1 FROM graph.nodes g WHERE g.org_id = $1 AND g.coll = b.to_coll AND g.id = b.to_id)""", org, lk["to_id"], lk["to_coll"])]
            out.setdefault(lk["cid"], []).extend([(lk["to_coll"], lk["to_id"]), *badges])
        return out

    async def _check_gate(self, tx: Tx, org: str, user_key: str) -> dict[str, Any]:
        """The access a check reads when the caller holds none: the gate of ``get_knowledge_hub_access_v3`` without its grants (``linked`` instead), so the check reads the grants
        of the nodes it decides (``access.Grants``), not every grant the user holds. Read in the check's own snapshot (``_kh_v3_accessible_rows``)."""
        gate = await self._gate(tx, org, user_key)
        if gate is None:
            return dict(_NO_ACCESS)
        grantees, gated, links = gate
        return {"grantee_ids": grantees, "gated_app_ids": gated, "linked": await self._linked_holders(tx, org, links, gated)}

    async def _kh_v3_accessible_rows(self, user_key: str, org_id: str, node_ids: list[str], virtual_record_ids: list[str], *,
                                     access: dict[str, Any] | None, transaction: str | None) -> list[dict[str, Any]]:
        """The asked nodes and virtual record ids the user may access, as the rows ``check_access`` reads. An org other than the ambient one admits nothing.

        One check reads one state of the graph: outside a transaction (and in a read-only snapshot) the gate, kg's classes, the walk and every grant it reads per node
        share one repeatable-read snapshot. A writable transaction reads at read committed, statement by statement, so there the grants are read whole first, in one
        statement (``get_knowledge_hub_access_v3``, as before), and no answer joins grants of two states."""
        org = current_org_id()
        ids = list(dict.fromkeys(i for i in node_ids if i))
        vrids = list(dict.fromkeys(v for v in virtual_record_ids if v))
        if (not ids and not vrids) or org_id != org:
            return []
        # an explicit transaction sees its own writes before kg has been given them (a read-only snapshot has none: kg answers in it); the system role has no read on kg
        snapshot = getattr(self._txns.get(transaction), "snapshot", False) if transaction else False
        if access is None and transaction and not snapshot:
            access = await self.get_knowledge_hub_access_v3(user_key, org_id, transaction=transaction)
        use_kg = (transaction is None or snapshot) and kg_read.enabled() and current_or_system().kind != "system"

        def unit(fn: Callable[[Tx], Awaitable[Any]]) -> Awaitable[Any]:
            return self._db.run(fn, isolation="repeatable_read", jobs=current_or_system().kind == "system")

        async def check(tx: Tx) -> list[dict[str, Any]]:
            acc = access if access is not None else await self._check_gate(tx, org, user_key)

            async def walk(i: list[str], v: list[str], a: dict[str, Any]) -> list[dict[str, Any]]:
                return await access_sql.accessible_rows(tx, org, i, v, a)
            if not use_kg:
                return await walk(ids, vrids, acc)
            # on the check's connection, in a savepoint: kg's settings end with its read. The samples run after the call, in units of their own.
            return await kg_read.accessible_rows(lambda fn: kg_read.in_savepoint(tx, fn), org, ids, vrids, acc, walk, self._db, sample_run=unit)
        # through _q, the provider's one way to the database, as every other read: a store that cannot answer fails the check (check_access raises)
        return await self._q(check, transaction, isolation="repeatable_read")

    async def get_selection_nodes(self, org_id: str, *, group_ids: list[str], record_ids: list[str],
                                  exact_record_ids: list[str], limit: int) -> dict[str, list[dict[str, Any]]]:
        """What a selection covers, from the hierarchy alone (see the interface). Walks down a level at a time with single-table lookups,
        through live nodes only, and stops once more than ``limit`` nodes are found (the caller reads that as too large). Another org reads nothing."""
        org = current_org_id()
        if org_id != org:
            return {"groups": [], "records": []}

        async def walk(tx: Tx, coll: str, roots: list[str], exact: list[str]) -> list[dict[str, Any]]:
            found: dict[str, dict[str, Any]] = {}

            async def live(ids: list[str]) -> list[Any]:          # at most one past the limit, as the children: a long id list is never read in full
                return await tx.fetch("""/* pipeshub:graph.selection_nodes_live */ SELECT id, doc->>'connectorId' AS cid, doc->>'virtualRecordId' AS vrid FROM graph.nodes
                    WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) AND id <> ALL($4::text[]) AND coalesce((doc->>'isDeleted')::boolean, false) = false
                    LIMIT $5""", org, coll, ids, list(found), limit + 1 - len(found))

            rows = await live(list(roots))
            for hop in range(access_sql.MAX_HOPS + 1):
                fresh = [r for r in rows if r["id"] not in found]
                found.update((r["id"], r) for r in fresh)
                frontier = [r["id"] for r in fresh]
                if not frontier or len(found) > limit or hop == access_sql.MAX_HOPS:
                    break
                # only live children not found yet, and no more than would take the walk past the limit: a wide level is never read in full
                rows = await tx.fetch("""/* pipeshub:graph.selection_nodes_children */ SELECT DISTINCT n.id, n.doc->>'connectorId' AS cid, n.doc->>'virtualRecordId' AS vrid
                    FROM graph.edges e JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = e.to_coll AND n.id = e.to_id
                    WHERE e.org_id = $1 AND e.coll = 'nodeRelations' AND e.from_coll = $2 AND e.to_coll = $2 AND e.from_id = ANY($3::text[])
                      AND e.doc->>'relationshipType' = ANY($4::text[]) AND coalesce((n.doc->>'isDeleted')::boolean, false) = false AND n.id <> ALL($5::text[])
                    LIMIT $6""", org, coll, frontier, ["PARENT_CHILD", "ATTACHMENT"], list(found), limit + 1 - len(found))
            if exact and len(found) <= limit:
                found.update((r["id"], r) for r in await live(exact))
            return list(found.values())[:limit + 1]

        async def go(tx: Tx) -> dict[str, list[dict[str, Any]]]:
            groups = await walk(tx, "recordGroups", group_ids, []) if group_ids else []
            records = await walk(tx, "records", record_ids, exact_record_ids) if record_ids or exact_record_ids else []
            return {"groups": [{"id": g["id"], "connectorId": g["cid"]} for g in groups],
                    "records": [{"id": r["id"], "vrid": r["vrid"], "connectorId": r["cid"]} for r in records]}
        return await self._q(go, None)

    # ---- who holds a grant on a node, and the writes of grants -----------------------------------------------------
    async def _users_granted_on(self, node_id: str, limit: int | None, transaction: str | None) -> list[dict]:
        """The users and source accounts holding a PERMISSION edge on the node or on an ancestor it inherits from (up to ``_INHERIT_HOPS``), nearest first,
        then by id. A source account is the address a connector acts as, like a synced user's."""
        key = parse_node_id(node_id)[1] or node_id
        return await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_users_with_permission_to_node */ WITH RECURSIVE anc(coll, id, depth) AS (
              SELECT n.coll, n.id, 0 FROM graph.nodes n WHERE n.org_id = $1 AND n.id = $2
              UNION
              SELECT i.to_coll, i.to_id, a.depth + 1 FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < $3)
            SELECT u.doc FROM anc
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll IN ('users', 'sourceAccounts') AND p.to_coll = anc.coll AND p.to_id = anc.id
              JOIN graph.nodes u ON u.org_id = $1 AND u.coll = p.from_coll AND u.id = p.from_id
                AND NOT (u.coll = 'sourceAccounts' AND coalesce(u.doc->>'state', '') = 'withdrawn')      -- a withdrawn account holds for nobody
            GROUP BY u.coll, u.id, u.doc ORDER BY min(anc.depth), u.id LIMIT $4""", current_org_id(), key, _INHERIT_HOPS, limit), transaction)

    @_soft(None)
    async def get_first_user_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None) -> User | None:
        rows = await self._users_granted_on(node_id, 1, transaction)
        return User.from_arango_user(_arango(_doc(rows[0]["doc"]), "users")) if rows else None

    @_soft(list, flag=True)
    async def get_users_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None, *,
                                                raise_on_error: bool = False) -> list[User]:
        return [User.from_arango_user(_arango(_doc(r["doc"]), "users")) for r in await self._users_granted_on(node_id, None, transaction)]

    @_soft(list, flag=True)
    async def get_groups_with_permission_to_node(self, node_id: str, node_collection: str, transaction: str | None = None, *,
                                                 raise_on_error: bool = False) -> list[AppUserGroup]:
        """The synced user groups (those with an ``externalGroupId``) holding a PERMISSION edge on the node itself."""
        key = parse_node_id(node_id)[1] or node_id
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_groups_with_permission_to_node */ SELECT DISTINCT g.id, g.doc FROM graph.nodes n
            JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'groups' AND p.to_coll = n.coll AND p.to_id = n.id
            JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'groups' AND g.id = p.from_id AND g.doc->>'externalGroupId' IS NOT NULL
            WHERE n.org_id = $1 AND n.id = $2 ORDER BY g.id""", current_org_id(), key), transaction)
        return [AppUserGroup.from_arango_base_user_group(_arango(_doc(r["doc"]), "groups")) for r in rows]

    async def batch_upsert_record_permissions(self, record_id: str, permissions: list[dict], transaction: str | None = None) -> None:
        """Grants on one record: each edge is pointed at the record, whatever its own target says."""
        if permissions:
            await self.batch_create_edges([{**p, "to_id": record_id, "to_collection": "records"} for p in permissions], "permission", transaction)

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str, transaction: str | None = None) -> None:
        now = get_epoch_timestamp_in_ms()
        await self.batch_create_edges([{"from_id": record_id, "from_collection": "records", "to_id": record_group_id, "to_collection": "recordGroups",
                                        "createdAtTimestamp": now, "updatedAtTimestamp": now}], "inheritPermissions", transaction)

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str, transaction: str | None = None) -> None:
        """Takes the user's grant on the record (live or in the trash) away; a record that is not there is a no-op."""
        record = await self.get_record_by_external_id(connector_id, external_id, transaction, visibility=RecordVisibility.ALL)
        if record:
            await self.delete_edge(user_id, "users", record.id, "records", "permission", transaction)

    async def remove_user_access_to_connector(self, connector_id: str, user_id: str, transaction: str | None = None) -> int:
        """The user's own PERMISSION edges to the connector's records, record groups, user groups and roles go; grants
        through groups, teams or the organization stay. Returns how many went."""
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.remove_user_access_to_connector */ WITH gone AS (
            DELETE FROM graph.edges p USING graph.nodes n
            WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2
              AND p.to_coll IN ('records', 'recordGroups', 'groups', 'roles')
              AND n.org_id = p.org_id AND n.coll = p.to_coll AND n.id = p.to_id AND n.doc->>'connectorId' = $3
            RETURNING 1) SELECT count(*) FROM gone""", current_org_id(), user_id, connector_id), transaction, write=True) or 0

    async def remove_unlinked_user_access_to_connector(self, connector_id: str, transaction: str | None = None) -> int:
        """Every user's own PERMISSION edges to the connector's records, record groups, user groups and roles go when the user has no
        userAppRelation edge to the connector, checked in the same statement. Returns how many went."""
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.remove_unlinked_user_access_to_connector */ WITH gone AS (
            DELETE FROM graph.edges p USING graph.nodes n
            WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users'
              AND p.to_coll IN ('records', 'recordGroups', 'groups', 'roles')
              AND n.org_id = p.org_id AND n.coll = p.to_coll AND n.id = p.to_id AND n.doc->>'connectorId' = $2
              AND NOT EXISTS (SELECT 1 FROM graph.edges l WHERE l.org_id = p.org_id AND l.coll = 'userAppRelation'
                              AND l.from_coll = 'users' AND l.from_id = p.from_id AND l.to_coll = 'apps' AND l.to_id = $2)
            RETURNING 1) SELECT count(*) FROM gone""", current_org_id(), connector_id), transaction, write=True) or 0

    async def get_user_granted_records(self, connector_id: str, user_id: str, transaction: str | None = None) -> list[dict[str, Any]]:
        """The connector's records the user holds a direct USER grant on; grants held by a group, a team or the organization are not included."""
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_user_granted_records */ SELECT DISTINCT n.id, n.doc->>'externalRecordId' AS ext,
            n.doc->>'recordType' AS rtype FROM graph.edges e JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'records' AND n.id = e.to_id
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'records'
              AND n.doc->>'connectorId' = $3""", current_org_id(), user_id, connector_id), transaction)
        return [{"id": r["id"], "externalRecordId": r["ext"], "recordType": r["rtype"]} for r in rows]

    # ---- teams ---------------------------------------------------------------------------------------------------
    async def batch_update_team_member_roles(self, team_id: str, user_roles: list[dict[str, str]], timestamp: int, transaction: str | None = None) -> list[dict]:
        """Sets the role on each listed user's edge to the team (a user with no edge is skipped); the updated edges come back. A user named twice gets the last role."""
        last = {ur["userId"]: ur.get("role") for ur in user_roles}
        if not last:
            return []
        org = current_org_id()
        for role in set(last.values()):
            _VALIDATOR.validate_edge_update("permission", {"role": role, "updatedAtTimestamp": timestamp})
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.batch_update_team_member_roles */ UPDATE graph.edges e
            SET doc = jsonb_strip_nulls(e.doc || jsonb_build_object('role', x.role, 'updatedAtTimestamp', $4::bigint))
            FROM jsonb_to_recordset($3::jsonb) AS x("userId" text, role text)
            WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = x."userId" AND e.to_coll = 'teams' AND e.to_id = $2
            RETURNING e.doc, e.from_id, e.to_id""", org, team_id, _j([{"userId": u, "role": r} for u, r in last.items()]), timestamp), transaction, write=True)
        return [{**_doc(r["doc"]), "from_id": r["from_id"], "to_id": r["to_id"], "from_collection": "users", "to_collection": "teams",
                 "_from": f"users/{r['from_id']}", "_to": f"teams/{r['to_id']}"} for r in rows]

    async def delete_all_team_permissions(self, team_id: str, transaction: str | None = None) -> None:
        """Every grant on the team (its members' edges) goes; the team's own grants to apps stay."""
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.delete_all_team_permissions */ DELETE FROM graph.edges WHERE org_id = $1 AND coll = 'permission'
            AND to_coll = 'teams' AND to_id = $2""", current_org_id(), team_id), transaction, write=True)

    async def get_team_permissions_and_owner_count(self, team_id: str, user_ids: list[str], transaction: str | None = None) -> dict[str, Any]:
        """The team (None when absent), the given users' roles on it, and how many owners it has."""
        org = current_org_id()

        async def go(tx: Tx) -> dict[str, Any]:
            team = await tx.fetchrow("/* pipeshub:graph.team_for_permissions */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'teams' AND id = $2", org, team_id)
            perms = await tx.fetch("""/* pipeshub:graph.team_member_roles */ SELECT p.from_id, p.doc->>'role' AS role FROM graph.edges p JOIN graph.nodes u
                ON u.org_id = p.org_id AND u.coll = 'users' AND u.id = p.from_id WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users'
                AND p.to_coll = 'teams' AND p.to_id = $2 AND p.from_id = ANY($3::text[])""", org, team_id, list(user_ids))
            owners = await tx.fetchval("""/* pipeshub:graph.team_owner_count */ SELECT count(*) FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission'
                AND p.to_coll = 'teams' AND p.to_id = $2 AND p.doc->>'role' = 'OWNER'""", org, team_id)
            return {"team": _arango(_doc(team["doc"]), "teams") if team else None, "permissions": {r["from_id"]: r["role"] for r in perms}, "owner_count": owners or 0}
        return await self._q(go, transaction)

    # ---- agents, apps and the knowledge hub ----------------------------------------------------------------------
    async def check_agent_permission(self, agent_id: str, user_id: str, org_id: str, transaction: str | None = None) -> dict | None:
        """How the user reaches the agent: their own grant, else their organization's; None for neither, for a deleted agent, and for another org."""
        if org_id != current_org_id():
            return None
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.check_agent_permission */ SELECT p.doc->>'role' AS role, p.doc->>'type' AS t FROM graph.nodes a
                JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.to_coll = 'agentInstances' AND p.to_id = a.id
                  AND ((p.from_coll = 'users' AND p.from_id = $3 AND p.doc->>'type' = 'USER') OR (p.from_coll = 'organizations' AND p.from_id = $1 AND p.doc->>'type' = 'ORG'))
                JOIN graph.nodes g ON g.org_id = $1 AND g.coll = p.from_coll AND g.id = p.from_id
                WHERE a.org_id = $1 AND a.coll = 'agentInstances' AND a.id = $2 AND a.doc->>'isDeleted' IS DISTINCT FROM 'true'
                ORDER BY (p.doc->>'type' = 'USER') DESC LIMIT 1""", org_id, agent_id, user_id), transaction)
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="check_agent_permission")
            return None
        if not r:
            return None
        role = r["role"]
        return {"access_type": "INDIVIDUAL" if r["t"] == "USER" else "ORG", "user_role": role, **{k: role in v for k, v in _AGENT_CAN.items()}, "can_view": True}

    async def get_user_permission_app_ids(self, user_key: str, org_id: str, transaction: str | None = None) -> list[str]:
        """Apps of the org the user holds a USER grant on, directly or through a team they are in that holds a TEAM grant on it; hidden ones left out."""
        if org_id != current_org_id():
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_user_permission_app_ids */ WITH ids AS (
              SELECT e.to_id AS app FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_coll = 'users' AND e.from_id = $2
                AND e.to_coll = 'apps' AND e.doc->>'type' = 'USER'
              UNION SELECT g.to_id FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
                JOIN graph.edges g ON g.org_id = m.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = t.id AND g.to_coll = 'apps' AND g.doc->>'type' = 'TEAM'
                WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER')
            SELECT DISTINCT a.id FROM ids JOIN graph.nodes a ON a.org_id = $1 AND a.coll = 'apps' AND a.id = ids.app
            WHERE a.doc->>'orgId' = $1 AND a.doc->>'isHidden' IS DISTINCT FROM 'true'""", org_id, user_key), transaction)
        return [r["id"] for r in rows]

    async def get_hidden_app_ids(self, app_ids: list[str], transaction: str | None = None) -> list[str]:
        """The Apps of this org among ``app_ids`` whose ``isHidden`` is true (a project's own file collection), as Neo4j and ArangoDB answer."""
        if not app_ids:
            return []
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_hidden_app_ids */ SELECT id FROM graph.nodes
            WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[]) AND doc->'isHidden' = 'true'::jsonb ORDER BY id""",
                                                 current_org_id(), list(app_ids)), transaction)
        return [r["id"] for r in rows]

    async def get_gated_apps(self, user_key: str, org_id: str, transaction: str | None = None) -> list[dict]:
        return await self.get_user_apps(user_key, transaction) if org_id == current_org_id() else []

    async def get_knowledge_hub_access_context_v2(self, user_key: str, org_id: str, *, transaction: str | None = None) -> dict[str, list[str]]:
        """Who the user is and the apps they pass the gate for: the gate part of ``get_knowledge_hub_access_v3``, without reading the grants."""
        org = current_org_id()
        if org_id != org:
            return {"grantee_ids": [], "gated_app_ids": []}

        async def go(tx: Tx) -> dict[str, list[str]]:
            gate = await self._gate(tx, org, user_key)
            return {"grantee_ids": list(dict.fromkeys(gate[0])), "gated_app_ids": list(dict.fromkeys(gate[1]))} if gate else {"grantee_ids": [], "gated_app_ids": []}
        return await self._q(go, transaction)

    async def get_accessible_containers(self, user_id: str, org_id: str, filters: dict[str, list[str]] | None = None,
                                        time_range: dict[str, int] | None = None) -> AccessibleContainers:
        """The Apps the permission gate lets the user reach, and nothing trusted, as Neo4j's and ArangoDB's: a search scopes by them and the verifier (the batch access
        check on the hits) decides every record, so the search never enumerates what the user may read (the record-id path is O(org): past 20 s at 1M records).
        See the interface for the contract. A request for another org reaches nothing."""
        unsupported = _unsupported_container_filters(filters, time_range)
        if unsupported:
            return AccessibleContainers(fallback_reason=unsupported)
        scope = requested_scope_ids(filters)
        scope_set = frozenset(scope) if scope is not None else None
        if scope_set is not None and not scope_set:
            return AccessibleContainers(scope_connector_ids=scope_set)
        # a project-scoped chat that selected nothing reaches nothing, as on the record-id path
        if scope_set is None and bool((filters or {}).get(STRICT_SCOPE_FILTER_KEY)):
            return AccessibleContainers(scope_connector_ids=None)
        if not user_id:
            return AccessibleContainers(fallback_reason="no_user_or_client")
        org = current_org_id()
        if org_id != org:
            return AccessibleContainers(scope_connector_ids=scope_set)
        try:
            user = await self.get_user_by_user_id(user_id, raise_on_error=True)
            user_key = (user or {}).get("id") or (user or {}).get("_key")
            if not user_key:
                return AccessibleContainers(fallback_reason="user_not_found")
            access = await self.get_knowledge_hub_access_context_v2(user_key, org)
            # unscoped, a project's hidden Collection stays out of search, as on the record-id path; a scoped request reaches one only by naming it
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.accessible_container_apps */ SELECT a.id,
                  coalesce((a.doc->>'vectorMembershipBackfilled')::boolean, false) AND NOT coalesce((a.doc->>'vectorMembershipBackfillExhausted')::boolean, false) AS ready
                FROM graph.nodes a WHERE a.org_id = $1 AND a.coll = 'apps' AND a.id = ANY($2::text[])
                  AND ($3::text[] IS NULL OR a.id = ANY($3::text[])) AND (NOT coalesce((a.doc->>'isHidden')::boolean, false) OR $3::text[] IS NOT NULL)""",
                                                     org, list(access["gated_app_ids"]), sorted(scope_set) if scope_set is not None else None), None)
        except Exception as exc:
            error_class.log_failure(self.logger, "graph.provider.failed", exc, include_error_text=False,
                                    op="get_accessible_containers")
            return AccessibleContainers(fallback_reason="query_failed")
        # points of an App whose membership arrays were never written carry no connectorIds, so a container filter cannot see them at all
        unready = sorted(r["id"] for r in rows if not r["ready"])
        if unready:
            return AccessibleContainers(fallback_reason=f"membership_not_backfilled:{unready[0]}")
        return AccessibleContainers(app_ids=frozenset(r["id"] for r in rows), scope_connector_ids=scope_set)

    async def get_accessible_connector_types(self, user_id: str, org_id: str) -> list[str]:
        """Types of the apps the user passes the gate for. A failed read widens the search (an empty list), it never narrows it."""
        try:
            user = await self.get_user_by_user_id(user_id)
            user_key = (user or {}).get("id") or (user or {}).get("_key")
            return _distinct_connector_types(await self.get_gated_apps(user_key, org_id)) if user_key else []
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_accessible_connector_types")
            return []

    async def get_knowledge_hub_node_access(self, node_id: str, user_key: str, org_id: str, folder_mime_types: list[str],
                                            transaction: str | None = None) -> dict[str, Any] | None:
        """The node's metadata when the user may access it (the batch check); None for a missing node and a refused one alike."""
        if org_id != current_org_id():
            return None
        try:
            checked = await self.check_access(user_key, org_id, node_ids=[node_id], transaction=transaction)
            if node_id not in checked.node_ids:
                return None
            row = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_knowledge_hub_node_access */ SELECT n.coll, n.doc, kb.id AS kb_id FROM graph.nodes n
                LEFT JOIN graph.nodes kb ON kb.org_id = n.org_id AND kb.coll = 'apps' AND kb.doc->>'type' = 'KB'
                  AND kb.id = CASE WHEN n.coll = 'apps' THEN n.id ELSE n.doc->>'connectorId' END
                WHERE n.org_id = $1 AND n.id = $2 AND n.coll IN ('records', 'recordGroups', 'apps') AND n.doc->>'orgId' = $1
                ORDER BY n.coll LIMIT 1""", org_id, node_id), transaction)
            if not row:
                return None
            d, coll = _doc(row["doc"]), row["coll"]
            if coll == "records":
                out = {"id": node_id, "name": d.get("recordName"), "nodeType": "folder" if d.get("mimeType") in folder_mime_types else "record", "subType": d.get("recordType"),
                       "connector": d.get("connectorName"), "webUrl": d.get("webUrl"), "recordType": d.get("recordType"), "indexingStatus": d.get("indexingStatus")}
            elif coll == "recordGroups":
                out = {"id": node_id, "name": d.get("groupName"), "nodeType": "recordGroup",
                       "subType": "COLLECTION" if d.get("connectorName") == "KB" else d.get("groupType") or d.get("connectorName"),
                       "connector": d.get("connectorName"), "webUrl": d.get("webUrl"), "recordType": None, "indexingStatus": None}
            else:
                out = {"id": node_id, "name": d.get("name"), "nodeType": "app", "subType": d.get("type"), "connector": d.get("type"), "webUrl": d.get("webUrl"),
                       "recordType": None, "indexingStatus": None}
            out["isPlaceholder"] = node_id in checked.placeholder_ids
            out["userRole"] = await self.get_user_kb_permission(row["kb_id"], user_key, transaction=transaction) if row["kb_id"] else None
            return out
        except PermissionVerificationUnavailableError:
            raise
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_knowledge_hub_node_access")
            return None

    # ---- the context role, the record access details, and the accessible search scope ----------------------------
    async def _role_on(self, tx: Tx, org: str, user_key: str, coll: str, node_id: str, ranks: dict[str, int] = _CTX_RANK) -> tuple[str, str] | None:
        """The highest role the user holds on a record or record group: their own grant, a group's, a role's, a team's (never above WRITER) or the
        organization's, on the node or on what it inherits from (20 hops) or the collection it belongs to (on the node alone when its rule needs a grant
        of its own, read in the same statement); a source account the user authenticated as counts inside that connector with its own and its groups'
        grants, not an organisation's (the access check's rule). ``(role, source)``, a retired role read as READER; None when no grant gives one."""
        rows = await tx.fetch("""/* pipeshub:graph.node_roles */ WITH RECURSIVE anc(coll, id, depth, own_only) AS (
              SELECT n.coll, n.id, 0, coalesce(n.doc->>'accessRule', 'OPEN') IN ('RESTRICTED', 'PARENT_AND_GRANT') FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3
              UNION
              SELECT i.to_coll, i.to_id, a.depth + 1, a.own_only FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < 20 AND NOT a.own_only),
            -- each target with its connector (an app is its own): a grant held through a link counts only on a target of the link's connector
            targets AS (
              SELECT a.coll, a.id, CASE WHEN a.coll = 'apps' THEN a.id ELSE x.doc->>'connectorId' END AS cid FROM (
                SELECT coll, id FROM anc WHERE depth = 0 OR coll IN ('recordGroups', 'apps')
                UNION SELECT b.to_coll, b.to_id FROM graph.edges b JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = 'KB'
                  JOIN anc s0 ON s0.depth = 0 AND NOT s0.own_only
                  WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = $2 AND b.from_id = $3) a
              LEFT JOIN graph.nodes x ON x.org_id = $1 AND x.coll = a.coll AND x.id = a.id),
            -- the user (cid null), and each linked account with its link's connector
            principals AS (
              SELECT u.coll, u.id, NULL::text AS cid FROM graph.nodes u WHERE u.org_id = $1 AND u.coll = 'users' AND u.id = $4
              UNION SELECT l.to_coll, l.to_id, l.doc->>'connectorId' FROM graph.edges l JOIN graph.nodes s ON s.org_id = $1 AND s.coll = l.to_coll AND s.id = l.to_id AND coalesce(s.doc->>'state', '') <> 'withdrawn'
                WHERE l.org_id = $1 AND l.coll = 'authenticatedAs' AND l.from_coll = 'users' AND l.from_id = $4 AND l.to_coll IN ('users', 'sourceAccounts')
                  AND l.doc->>'connectorId' = (SELECT x.doc->>'connectorId' FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = $2 AND x.id = $3))
            SELECT 'direct' AS kind, t.coll AS tcoll, t.id AS tid, p.doc->>'role' AS role, NULL::text AS member_role, k.doc->>'type' AS app_type
              FROM targets t JOIN principals pr ON pr.cid IS NULL OR pr.cid = t.cid
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = pr.coll AND p.from_id = pr.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'USER' AND coalesce(p.doc->>'role', '') <> ''
              LEFT JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = t.id AND t.coll = 'apps'
            UNION ALL
            SELECT 'group', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll IN ('groups', 'roles') AND p.to_coll = t.coll AND p.to_id = t.id AND coalesce(p.doc->>'role', '') <> ''
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = p.from_coll AND g.id = p.from_id
              WHERE EXISTS (SELECT 1 FROM principals pr JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = pr.coll AND m.from_id = pr.id
                AND m.to_coll = p.from_coll AND m.to_id = p.from_id AND m.doc->>'type' = 'USER' WHERE pr.cid IS NULL OR pr.cid = t.cid)
            UNION ALL
            SELECT 'team', t.coll, t.id, p.doc->>'role', m.doc->>'role', NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'teams' AND p.to_coll = t.coll AND p.to_id = t.id AND p.doc->>'type' = 'TEAM'
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'teams' AND g.id = p.from_id
              JOIN principals pr ON pr.cid IS NULL OR pr.cid = t.cid
              JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = pr.coll AND m.from_id = pr.id AND m.to_coll = 'teams' AND m.to_id = p.from_id
                AND m.doc->>'type' = 'USER'
            UNION ALL
            SELECT 'org', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t JOIN principals pr ON pr.cid IS NULL
              JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = pr.id AND b.to_coll = 'organizations' AND b.doc->>'entityType' = 'ORGANIZATION'
              JOIN graph.nodes o ON o.org_id = $1 AND o.coll = 'organizations' AND o.id = b.to_id
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'organizations' AND p.from_id = o.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'ORG' AND coalesce(p.doc->>'role', '') <> ''""", org, coll, node_id, user_key)
        return _best_role(rows, coll, node_id, ranks)

    async def _roles_on(self, tx: Tx, org: str, user_key: str, coll: str, node_ids: list[str], ranks: dict[str, int] = _CTX_RANK) -> dict[str, tuple[str, str] | None]:
        """``_role_on`` for each of ``node_ids`` (the same node set and grant rules, the answer for each as if asked alone) in one statement: ``{id: (role, source) or None}``.
        The statement is ``_role_on``'s with an ``origin`` column on every row of its walk, so the nodes' hierarchies and source accounts stay apart."""
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return {}
        rows = await tx.fetch("""/* pipeshub:graph.node_roles_batch */ WITH RECURSIVE o(origin) AS (SELECT unnest($3::text[])),
            anc(origin, coll, id, depth, own_only) AS (
              SELECT n.id, n.coll, n.id, 0, coalesce(n.doc->>'accessRule', 'OPEN') IN ('RESTRICTED', 'PARENT_AND_GRANT') FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND n.id = ANY($3::text[])
              UNION
              SELECT a.origin, i.to_coll, i.to_id, a.depth + 1, a.own_only FROM anc a JOIN graph.edges i ON i.org_id = $1 AND i.coll = 'inheritPermissions'
                AND i.from_coll = a.coll AND i.from_id = a.id WHERE a.depth < 20 AND NOT a.own_only),
            targets(origin, coll, id, cid) AS (
              SELECT a.origin, a.coll, a.id, CASE WHEN a.coll = 'apps' THEN a.id ELSE x.doc->>'connectorId' END FROM (
                SELECT origin, coll, id FROM anc WHERE depth = 0 OR coll IN ('recordGroups', 'apps')
                UNION SELECT b.from_id, b.to_coll, b.to_id FROM graph.edges b JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = b.to_id AND k.doc->>'type' = 'KB'
                  JOIN anc s0 ON s0.origin = b.from_id AND s0.depth = 0 AND NOT s0.own_only
                  WHERE b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = $2 AND b.from_id = ANY($3::text[])) a
              LEFT JOIN graph.nodes x ON x.org_id = $1 AND x.coll = a.coll AND x.id = a.id),
            principals(origin, coll, id, cid) AS (
              SELECT o.origin, u.coll, u.id, NULL::text FROM o JOIN graph.nodes u ON u.org_id = $1 AND u.coll = 'users' AND u.id = $4
              UNION SELECT o.origin, l.to_coll, l.to_id, l.doc->>'connectorId' FROM o JOIN graph.edges l ON l.org_id = $1 AND l.coll = 'authenticatedAs' AND l.from_coll = 'users' AND l.from_id = $4
                AND l.to_coll IN ('users', 'sourceAccounts')
                JOIN graph.nodes s ON s.org_id = $1 AND s.coll = l.to_coll AND s.id = l.to_id AND coalesce(s.doc->>'state', '') <> 'withdrawn'
                WHERE l.doc->>'connectorId' = (SELECT x.doc->>'connectorId' FROM graph.nodes x WHERE x.org_id = $1 AND x.coll = $2 AND x.id = o.origin))
            SELECT t.origin, 'direct' AS kind, t.coll AS tcoll, t.id AS tid, p.doc->>'role' AS role, NULL::text AS member_role, k.doc->>'type' AS app_type
              FROM targets t JOIN principals pr ON pr.origin = t.origin AND (pr.cid IS NULL OR pr.cid = t.cid)
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = pr.coll AND p.from_id = pr.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'USER' AND coalesce(p.doc->>'role', '') <> ''
              LEFT JOIN graph.nodes k ON k.org_id = $1 AND k.coll = 'apps' AND k.id = t.id AND t.coll = 'apps'
            UNION ALL
            SELECT t.origin, 'group', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll IN ('groups', 'roles') AND p.to_coll = t.coll AND p.to_id = t.id AND coalesce(p.doc->>'role', '') <> ''
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = p.from_coll AND g.id = p.from_id
              WHERE EXISTS (SELECT 1 FROM principals pr JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = pr.coll AND m.from_id = pr.id
                AND m.to_coll = p.from_coll AND m.to_id = p.from_id AND m.doc->>'type' = 'USER' WHERE pr.origin = t.origin AND (pr.cid IS NULL OR pr.cid = t.cid))
            UNION ALL
            SELECT t.origin, 'team', t.coll, t.id, p.doc->>'role', m.doc->>'role', NULL FROM targets t
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'teams' AND p.to_coll = t.coll AND p.to_id = t.id AND p.doc->>'type' = 'TEAM'
              JOIN graph.nodes g ON g.org_id = $1 AND g.coll = 'teams' AND g.id = p.from_id
              JOIN principals pr ON pr.origin = t.origin AND (pr.cid IS NULL OR pr.cid = t.cid)
              JOIN graph.edges m ON m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = pr.coll AND m.from_id = pr.id AND m.to_coll = 'teams' AND m.to_id = p.from_id
                AND m.doc->>'type' = 'USER'
            UNION ALL
            SELECT t.origin, 'org', t.coll, t.id, p.doc->>'role', NULL, NULL FROM targets t JOIN principals pr ON pr.origin = t.origin AND pr.cid IS NULL
              JOIN graph.edges b ON b.org_id = $1 AND b.coll = 'belongsTo' AND b.from_coll = 'users' AND b.from_id = pr.id AND b.to_coll = 'organizations' AND b.doc->>'entityType' = 'ORGANIZATION'
              JOIN graph.nodes o2 ON o2.org_id = $1 AND o2.coll = 'organizations' AND o2.id = b.to_id
              JOIN graph.edges p ON p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'organizations' AND p.from_id = o2.id AND p.to_coll = t.coll AND p.to_id = t.id
                AND p.doc->>'type' = 'ORG' AND coalesce(p.doc->>'role', '') <> ''""", org, coll, ids, user_key)
        by_origin: dict[str, list] = {i: [] for i in ids}
        for r in rows:
            by_origin[r["origin"]].append(r)
        return {i: _best_role(by_origin[i], coll, i, ranks) for i in ids}

    async def _app_role(self, tx: Tx, org: str, user_key: str, app_id: str) -> str | None:
        """The user's role on an app (a connector or a collection); None when nothing relates them."""
        user = await tx.fetchrow("/* pipeshub:graph.ctx_user */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", org, user_key)
        app = await tx.fetchrow("/* pipeshub:graph.ctx_app */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = $2", org, app_id)
        if not user or not app:
            return None
        u, a = _doc(user["doc"]), _doc(app["doc"])
        rows = await tx.fetch("""/* pipeshub:graph.ctx_app_edges */ SELECT 'own' AS kind, NULL::text AS role, NULL::text AS member_role FROM graph.edges e
              WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.from_id = $2 AND e.to_coll = 'apps' AND e.to_id = $3
            UNION ALL SELECT 'own', NULL, NULL FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'authenticatedAs' AND e.from_coll = 'users' AND e.from_id = $2
              AND e.to_coll IN ('users', 'sourceAccounts') AND e.doc->>'connectorId' = $3
              AND NOT EXISTS (SELECT 1 FROM graph.nodes s WHERE s.org_id = e.org_id AND s.coll = e.to_coll AND s.id = e.to_id AND s.doc->>'state' = 'withdrawn')
            UNION ALL SELECT 'team_rel', NULL, NULL FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
              JOIN graph.edges e ON e.org_id = m.org_id AND e.coll = 'userAppRelation' AND e.from_coll = 'teams' AND e.from_id = t.id AND e.to_coll = 'apps' AND e.to_id = $3
              WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER'
            UNION ALL SELECT 'direct', p.doc->>'role', NULL FROM graph.edges p WHERE p.org_id = $1 AND p.coll = 'permission' AND p.from_coll = 'users' AND p.from_id = $2
              AND p.to_coll = 'apps' AND p.to_id = $3 AND p.doc->>'type' = 'USER'
            UNION ALL SELECT 'team_grant', g.doc->>'role', m.doc->>'role' FROM graph.edges m JOIN graph.nodes t ON t.org_id = m.org_id AND t.coll = 'teams' AND t.id = m.to_id
              JOIN graph.edges g ON g.org_id = m.org_id AND g.coll = 'permission' AND g.from_coll = 'teams' AND g.from_id = t.id AND g.to_coll = 'apps' AND g.to_id = $3
                AND g.doc->>'type' = 'TEAM'
              WHERE m.org_id = $1 AND m.coll = 'permission' AND m.from_coll = 'users' AND m.from_id = $2 AND m.to_coll = 'teams' AND m.doc->>'type' = 'USER'""",
                              org, user_key, app_id)
        kinds = {r["kind"] for r in rows}
        direct = [r["role"] for r in rows if r["kind"] == "direct"]
        team_roles = set()
        for r in rows:
            if r["kind"] == "team_grant":
                base = r["role"] if r["role"] else r["member_role"]
                team_roles.add(TEAM_GRANT_ROLE_CAP if base == "OWNER" else "READER" if base in RETIRED_ROLES else base if base in TEAM_GRANT_ROLES else None)
        team_role = "WRITER" if "WRITER" in team_roles else "READER" if "READER" in team_roles else None      # a team never gives OWNER
        raw = direct[0] if direct else None
        direct_role = ("READER" if raw in RETIRED_ROLES else raw if (a.get("type") != _KB_TYPE or raw in TEAM_GRANT_ROLES) else None) if direct else None
        own, team_rel = "own" in kinds, "team_rel" in kinds
        if not own and not team_rel and direct_role is None and (not direct or a.get("type") == _KB_TYPE) and team_role is None:
            return None
        if direct_role is not None and team_role is not None and _CTX_RANK.get(team_role, 0) > _CTX_RANK.get(direct_role, 0):
            return team_role
        if direct_role is not None:
            return direct_role
        if team_role is not None:
            return team_role
        admin = u.get("role") == "ADMIN" or u.get("orgRole") == "ADMIN"
        if not own and team_rel:
            return "EDITOR" if admin else "READER"
        if admin:
            return "EDITOR" if (a.get("scope") or "personal") == "team" else "OWNER"
        return "OWNER" if a.get("createdBy") in (u.get("id"), u.get("userId")) and a.get("createdBy") else "READER"

    async def get_knowledge_hub_context_permissions(self, user_key: str, org_id: str, parent_id: str | None, transaction: str | None = None,
                                                    parent_type: str | None = None) -> dict[str, Any]:
        """What the user may do inside a folder, collection or connector, from their role on it. At the root only an administrator may do anything.
        Anything that cannot be read answers the lowest role with no abilities, never more."""
        org = current_org_id()
        graph_type = None
        if parent_id:
            graph_type = ("record" if parent_type in ("folder", "record") else parent_type) if parent_type else None
            if graph_type not in ("record", "recordGroup", "app"):
                raise ValueError(f"Invalid or unsupported parent_type: {parent_type}")
        try:
            if org_id != org:
                return _ctx_perms("READER")

            if parent_id and parent_id not in (await self.check_access(user_key, org_id, node_ids=[parent_id], transaction=transaction)).node_ids:
                return _ctx_perms("READER")    # no abilities on a node the access check refuses (lost connector gate, deleted target), whatever grant remains

            async def go(tx: Tx) -> dict[str, Any]:
                if not parent_id:
                    row = await tx.fetchrow("/* pipeshub:graph.ctx_root_user */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id = $2", org, user_key)
                    if not row:
                        return _ctx_perms("READER")
                    d = _doc(row["doc"])
                    admin = d.get("role") == "ADMIN" or d.get("orgRole") == "ADMIN"
                    return _ctx_perms("ADMIN" if admin else "MEMBER", admin=admin)
                coll = {"record": "records", "recordGroup": "recordGroups", "app": "apps"}[graph_type]
                if graph_type == "app":
                    return _ctx_perms(await self._app_role(tx, org, user_key, parent_id) or "READER")
                got = await self._role_on(tx, org, user_key, coll, parent_id)
                return _ctx_perms(got[0] if got else "READER")
            return await self._q(go, transaction)
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_knowledge_hub_context_permissions")
            return _ctx_perms("READER")

    async def check_record_access_with_details(self, user_id: str, org_id: str, record_id: str, transaction: str | None = None) -> dict | None:
        """The record with how the user reaches it, or None. The batch access check decides whether they may; a placeholder is refused like a record they
        cannot read. A failed read raises, never an answer of None."""
        if org_id != current_org_id():
            return None
        user = await self.get_user_by_user_id(user_id, raise_on_error=True)
        user_key = (user or {}).get("id") or (user or {}).get("_key")
        if not user_key:
            return None
        record = await self.get_document(record_id, "records", transaction, raise_on_error=True)
        if not record:
            return None
        checked = await self.check_access(user_key, org_id, node_ids=[record_id], transaction=transaction)
        if record_id not in checked.node_ids or record_id in checked.placeholder_ids:
            return None
        org = current_org_id()
        type_coll = {RecordTypes.FILE.value: CollectionNames.FILES.value, RecordTypes.MAIL.value: CollectionNames.MAILS.value, RecordTypes.TICKET.value: CollectionNames.TICKETS.value}.get(record.get("recordType"))

        async def go(tx: Tx) -> tuple:
            best = await self._role_on(tx, org, user_key, "records", record_id, _DETAIL_RANK)
            meta = await tx.fetch("""/* pipeshub:graph.record_labels */ SELECT e.coll AS ecoll, m.coll AS mcoll, m.id AS mid, m.doc AS mdoc, e.doc AS edoc FROM graph.edges e
                JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = e.to_coll AND m.id = e.to_id
                WHERE e.org_id = $1 AND e.from_coll = 'records' AND e.from_id = $2 AND e.coll IN ('belongsToDepartment', 'belongsToCategory', 'belongsToTopic', 'belongsToLanguage')
                  AND m.coll IN ('departments', 'categories', 'subcategories1', 'subcategories2', 'subcategories3', 'topics', 'languages')""", org, record_id)
            return best, meta
        best, meta = await self._q(go, transaction)
        extra = await self.get_document(record_id, type_coll, transaction, raise_on_error=True) if type_coll else None
        email, connector_name = (user or {}).get("email"), record.get("connectorName")
        if extra and extra.get("webUrl"):
            extra["webUrl"] = substitute_user_email(extra["webUrl"], email, connector_name)
        labels: dict[str, list] = {k: [] for k in ("departments", "categories", "subcategories1", "subcategories2", "subcategories3", "topics", "languages")}
        for m in meta:
            d, ed = _doc(m["mdoc"]), _doc(m["edoc"])
            item = {"id": m["mid"], "name": d.get("departmentName") if m["mcoll"] == "departments" else d.get("name")}
            if m["mcoll"] != "departments":
                item |= {"extractedName": ed.get("extractedName"), "extractedNames": ed.get("extractedNames"), "canonical": d.get("normalizedName") is not None,
                         "migrated": ed.get("migratedFrom") is not None}
            labels[m["mcoll"]].append(item)
        kb_info = None
        app = await self.get_document(record.get("connectorId"), "apps", transaction, raise_on_error=True) if record.get("connectorId") and connector_name == Connectors.KNOWLEDGE_BASE.value else None
        if app and app.get("type") == _KB_TYPE and await self.get_user_kb_permission(app["id"], user_key, transaction=transaction):
            kb_info = {"id": app["id"], "name": app.get("name"), "orgId": org}
        role, source = best if best else ("READER", "CONNECTOR")      # a connector item carries no role; what the check admitted is read access
        record_out = {**record, "id": record["id"]}
        record_out.pop("_key", None)
        if record_out.get("webUrl"):
            record_out["webUrl"] = substitute_user_email(record_out["webUrl"], email, connector_name)
        rt = record.get("recordType")
        return {"record": {**record_out, "fileRecord": extra if rt == RecordTypes.FILE.value else None, "mailRecord": extra if rt == RecordTypes.MAIL.value else None,
                           "ticketRecord": extra if rt == RecordTypes.TICKET.value else None},
                "knowledgeBase": kb_info, "folder": None, "metadata": own_record_labels(labels),
                "permissions": [{"id": record["id"], "name": record.get("recordName"), "type": rt, "relationship": role, "accessType": source}]}

    async def get_accessible_virtual_record_ids(self, user_id: str, org_id: str, filters: dict[str, list[str]] | None = None, time_range: dict[str, int] | None = None, *,
                                                raise_on_error: bool = False, exclude_app_ids: frozenset[str] = frozenset()) -> dict[str, str]:
        """``{virtualRecordId: recordId}`` of the indexed, live records the user may open, inside the apps they pass the gate for (or the ones the filters
        name), narrowed by the metadata filters and the source time range. The batch access check decides each record, so this agrees with it.
        The candidates are what the user can reach: every record of a collection or an app-level connector (the user may open them all), and for a
        record-level connector what a walk down from the app, from the records and groups granted to the user and from the groups that declare a scope
        reaches (``access.reachable_records``). Records the user cannot reach are never read. The grants are read once."""
        try:
            org = current_org_id()
            if org_id != org:
                return {}
            user = await self.get_user_by_user_id(user_id, raise_on_error=raise_on_error)
            user_key = (user or {}).get("id") or (user or {}).get("_key")
            if not user_key:
                return {}
            filters = filters or {}
            scope = requested_scope_ids(filters)
            if filters.get(STRICT_SCOPE_FILTER_KEY) and scope is None:
                return {}
            meta = [(edge, coll, field, filters[k]) for k, edge, coll, field in _META_FILTERS if filters.get(k)]
            access = await self.get_knowledge_hub_access_v3(user_key, org_id)
            gated = [a for a in access["gated_app_ids"] if a not in exclude_app_ids]
            docs = {r["id"]: _doc(r["doc"]) for r in await self._q(lambda tx: tx.fetch(
                "/* pipeshub:graph.accessible_apps */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])", org, gated), None)}
            chosen = {i: d for i, d in docs.items() if (i in scope if scope is not None else d.get("type") != _KB_TYPE or not d.get("isHidden"))}
            kbs = [i for i, d in chosen.items() if d.get("type") == _KB_TYPE]
            wide = [i for i, d in chosen.items() if d.get("type") != _KB_TYPE and d.get("permissionModel") == "APP_LEVEL"]
            level = {i: d for i, d in chosen.items() if i not in kbs and i not in wide}
            if not chosen:
                return {}
            tr = time_range or {}
            dated_only = bool(tr.get(DATED_ONLY_TIME_RANGE_KEY)) and upload_times_undated()
            undated = upload_times_undated() and not dated_only
            bounds = [tr.get(k) for k in ("source_created_after_ms", "source_created_before_ms", "source_updated_after_ms", "source_updated_before_ms")]

            granted = {g for ids in access["by_connector"].values() for g in ids}
            scope = (_j([{"edge": e, "coll": c, "field": f, "vals": v} for e, c, f, v in meta]), *bounds, undated, dated_only)

            async def candidates(tx: Tx) -> list[Any]:
                """The scope filters are applied where the records are read: every record of a collection or app-level connector in one statement, and the
                records the walk reached, by key, in another."""
                rows = list(await tx.fetch("""/* pipeshub:graph.accessible_candidates_app_wide */ SELECT r.id, {cols} FROM graph.nodes r
                    WHERE r.org_id = $1 AND r.coll = 'records' AND r.doc->>'orgId' = $1 AND r.doc->>'virtualRecordId' IS NOT NULL
                      AND ((r.doc->>'connectorId' = ANY($2::text[])) OR (r.doc->>'connectorId' = ANY($3::text[]) AND r.doc->>'origin' = 'UPLOAD'))
                      AND (r.doc->>'indexingStatus' = 'COMPLETED' OR r.doc->>'hasIndexedCopy' = 'true') AND r.doc->>'isDeleted' IS DISTINCT FROM 'true'
                      AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset($4::jsonb) f(edge text, coll text, field text, vals jsonb) WHERE NOT EXISTS (
                          SELECT 1 FROM graph.edges e JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = f.coll AND m.id = e.to_id
                          WHERE e.org_id = $1 AND e.coll = f.edge AND e.from_coll = 'records' AND e.from_id = r.id AND m.doc->f.field IN (SELECT jsonb_array_elements(f.vals))))
                      AND ($5::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) >= $5)
                      AND ($6::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) <= $6
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND ($7::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) >= $7)
                      AND ($8::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) <= $8
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND (NOT $10 OR coalesce(r.doc->>'sourceTimestampOrigin', '') <> 'upload')
                    ORDER BY r.id""".replace("{cols}", _CANDIDATE_COLUMNS), org, wide, kbs, *scope)) if wide or kbs else []
                have = {r["id"] for r in rows}
                by_class, walked_apps = await _kg_admitted(tx, org, sorted(level), access)
                reached, below = await access_sql.reachable_records(tx, org, access, walked_apps) if walked_apps else (set(), {})
                for a, (sure, undecided) in by_class.items():
                    below[a] = sure      # admitted by its class in this snapshot: what the access check answers for it
                    reached |= sure | undecided
                # shortcut: the set path reads a whole app (every child, every membership), so it runs only for an app that more than a chunk of the
                # candidates come from and that is at most twice the size of them; a narrow search of a big app keeps the per-record check. Upgrade: a count
                # per app that does not scan its index.
                found: dict[str, int] = {}
                for r in rows:
                    found[r["app"]] = found.get(r["app"], 0) + 1
                sizes = {r["app"]: r["n"] for r in await tx.fetch(
                    "/* pipeshub:graph.accessible_app_sizes */ SELECT doc->>'connectorId' AS app, count(*) AS n FROM graph.nodes WHERE org_id = $1 AND coll = 'records'"
                    " AND doc->>'connectorId' = ANY($2::text[]) GROUP BY 1", org, [a for a, n in found.items() if n > _CHECK_CHUNK])} if any(n > _CHECK_CHUNK for n in found.values()) else {}
                whole = [a for a in (*kbs, *wide) if a in sizes and 2 * found[a] >= sizes[a]]
                for a in whole:
                    if a in wide:
                        below[a] = await access_sql.records_below_app(tx, org, a, granted, opens=True)
                walked = [i for i in sorted(reached) if i not in have]
                for i in range(0, len(walked), _CHECK_CHUNK * 5):
                    rows += await tx.fetch("""/* pipeshub:graph.accessible_candidates */ SELECT r.id, {cols} FROM graph.nodes r
                    WHERE r.org_id = $1 AND r.coll = 'records' AND r.id = ANY($2::text[]) AND r.doc->>'orgId' = $1 AND r.doc->>'virtualRecordId' IS NOT NULL
                      AND r.doc->>'connectorId' = ANY($3::text[])
                      AND (r.doc->>'indexingStatus' = 'COMPLETED' OR r.doc->>'hasIndexedCopy' = 'true') AND r.doc->>'isDeleted' IS DISTINCT FROM 'true'
                      AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset($4::jsonb) f(edge text, coll text, field text, vals jsonb) WHERE NOT EXISTS (
                          SELECT 1 FROM graph.edges e JOIN graph.nodes m ON m.org_id = e.org_id AND m.coll = f.coll AND m.id = e.to_id
                          WHERE e.org_id = $1 AND e.coll = f.edge AND e.from_coll = 'records' AND e.from_id = r.id AND m.doc->f.field IN (SELECT jsonb_array_elements(f.vals))))
                      AND ($5::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) >= $5)
                      AND ($6::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceCreatedAtTimestamp') = 'number' THEN (r.doc->>'sourceCreatedAtTimestamp')::numeric END) <= $6
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND ($7::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) >= $7)
                      AND ($8::bigint IS NULL OR (CASE WHEN jsonb_typeof(r.doc->'sourceLastModifiedTimestamp') = 'number' THEN (r.doc->>'sourceLastModifiedTimestamp')::numeric END) <= $8
                           OR ($9 AND coalesce(r.doc->>'sourceTimestampOrigin', '') = 'upload'))
                      AND (NOT $10 OR coalesce(r.doc->>'sourceTimestampOrigin', '') <> 'upload')
                    ORDER BY r.id""".replace("{cols}", _CANDIDATE_COLUMNS), org, walked[i:i + _CHECK_CHUNK * 5], sorted(level), *scope)
                members = {(m["from_id"], m["to_id"]) for m in await tx.fetch(      # one scan of the memberships of these apps, not a lookup per record
                    "/* pipeshub:graph.accessible_members */ SELECT from_id, to_id FROM graph.edges WHERE org_id = $1 AND coll = 'belongsTo' AND from_coll = 'records'"
                    " AND to_coll = 'apps' AND to_id = ANY($2::text[])", org, whole)} if whole else set()
                return sorted(rows, key=lambda r: r["id"]), below, members
            # one snapshot: what kg admits by class is read with the currency check that lets it answer
            kept, below, members = await self._db.run(candidates, isolation="repeatable_read", custom_plan=True, jobs=current_or_system().kind == "system")
            # What the arms of the access check admit in a set (a member of a collection or of an app that opens everything; a record the walk down from
            # its own app takes) is admitted here; the rest, which only a grant, a declared group or a seed can open, is checked one chunk at a time.
            ok = {c["id"] for c in kept if c["plain"] and ((c["id"], c["app"]) in members or c["id"] in below.get(c["app"], ()))}
            left = [c for c in kept if c["id"] not in ok]
            for i in range(0, len(left), _CHECK_CHUNK):
                rows = await self._kh_v3_accessible_rows(user_key, org_id, [c["id"] for c in left[i:i + _CHECK_CHUNK]], [], access=access, transaction=None)
                ok |= {r["id"] for r in rows if not r.get("isInternal") and not r.get("isPlaceholder")}
            out: dict[str, str] = {}
            for c in kept:
                if c["id"] in ok:
                    out.setdefault(c["vrid"], c["id"])
            return out
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="get_accessible_virtual_record_ids")
            return {}


# The records of a connector kg serves that a viewer's classes admit (as kg.listing.count_sql reads them): stamped, not hidden, of no class kg cannot vouch for ($3), and of
# a class the viewer's grants meet with no grant of the record's own ($4) or one that needs the record's own grant ($5, $6).
_KG_ADMITTED = f"""/* pipeshub:kg.accessible_records */ SELECT r.id FROM kg.records r WHERE r.org_id = $1 AND r.app_sk = $2 AND r.kind = 3 AND {kgl.LIVE} AND {kgl.DECIDED}
    AND NOT r.class_id = ANY($3::bigint[]) AND (r.class_id = ANY($4::bigint[]) OR (r.class_id = ANY($5::bigint[]) AND r.id = ANY($6::text[])))"""


async def _kg_admitted(tx: Tx, org: str, apps: list[str], access: dict[str, Any]) -> tuple[dict[str, tuple[set[str], set[str]]], list[str]]:
    """For each record-level connector kg serves in this snapshot: the records its classes admit, and the few rows no class decides (the access check's). Every other
    connector, and one with more undecided rows or classes than a listing evaluates, is left to the walk (the second value)."""
    if not kg_read.enabled() or current_or_system().kind == "system":
        return {}, apps
    held = {g for ids in access["by_connector"].values() for g in ids}
    out: dict[str, tuple[set[str], set[str]]] = {}
    rest: list[str] = []
    for a in apps:
        sk = await tx.fetchval(kgl.APP_KEY, org, a) if await kg_read.serves(tx, org, a) else None
        sets = await class_sets(tx, org, sk, held) if sk is not None else None
        unvouched = sorted(sets["unvouched"]) if sets is not None else []
        q = kgl.Params([org, sk, UNDECIDED_MAX + 1])
        undecided = [r["id"] for r in await tx.fetch(kgl.undecided_sql("", q.add(unvouched)), *q.values)] if sets is not None else None
        if undecided is None or len(undecided) > UNDECIDED_MAX:
            rest.append(a)
            continue
        own = sorted(access["by_connector"].get(a) or [])
        admitted = await tx.fetch(_KG_ADMITTED, org, sk, unvouched, sorted(sets["plain"]), sorted(sets["own"]), own)
        out[a] = ({r["id"] for r in admitted}, set(undecided))
    return out, rest
