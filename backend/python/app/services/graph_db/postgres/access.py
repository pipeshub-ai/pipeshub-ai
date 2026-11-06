"""The access check on PostgreSQL: the rules of ``Neo4jProvider._kh_v3_accessible_rows`` over ``graph.nodes`` / ``graph.edges``.

A target is admitted by the first arm that holds (app itself, KB record, declared scope, walk up to the app, seed, below a seed). Each
walk goes up from the targets a level at a time with single-table lookups (edges by the key, nodes by primary key), so its cost follows
depth, not fan-out, and no plan depends on table statistics. Every statement binds the org first, so row-level security and the primary
keys see it.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.models.permission import ORG_SHARE_PERMISSION_TYPES
from app.services.graph_db.postgres.base import NotImplementedInPostgres

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.core.db import Tx

MAX_HOPS = 50  # ACCESS_WALK_MAX_DEPTH of the interface
# The nodes a PERMISSION edge can come from and grant to a user: Neo4j matches a grantee by these labels, so an edge out of any other node is not one.
PRINCIPALS = ["users", "groups", "roles", "teams", "organizations"]




_HIER_TYPES = ["PARENT_CHILD", "ATTACHMENT"]
_CHUNK = 10_000


async def _info(tx: Tx, org: str, nodes: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """connectorId, deletion and access rule of nodes, one primary-key lookup per collection (no join, so no plan depends on statistics)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    by_coll: dict[str, list[str]] = {}
    for c, i in nodes:
        by_coll.setdefault(c, []).append(i)
    for coll, ids in by_coll.items():
        for k in range(0, len(ids), _CHUNK):
            for r in await tx.fetch("""/* pipeshub:graph.access_node_info */ SELECT id, doc->>'connectorId' AS cid, coalesce((doc->>'isDeleted')::boolean, false) AS deleted,
                coalesce(doc->>'accessRule', 'OPEN') AS rule, doc->>'permissionModel' AS model FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])""", org, coll, ids[k:k + _CHUNK]):
                out[(coll, r["id"])] = {"coll": coll, "cid": r["cid"], "deleted": r["deleted"], "rule": r["rule"], "model": r["model"]}
    return out


async def granted_targets(tx: Tx, org: str, from_ids: list[str], org_types: list[str]) -> dict[str, list[str]]:
    """connectorId -> ids of the live records and groups that ``from_ids`` hold a permission edge to (an edge from an organization counts when its
    type is in ``org_types``): the edges by the key, then the nodes by primary key."""
    if not from_ids:
        return {}
    targets: set[tuple[str, str]] = set()
    for r in await tx.fetch("""/* pipeshub:graph.access_grant_edges */ SELECT e.from_coll, e.to_coll, e.to_id, e.doc->>'type' AS t FROM graph.edges e WHERE e.org_id = $1
        AND e.coll = 'permission' AND e.from_id = ANY($2::text[]) AND e.from_coll = ANY($3::text[]) AND e.to_coll IN ('records', 'recordGroups')""",
                              org, from_ids, PRINCIPALS):
        if r["from_coll"] != "organizations" or r["t"] in org_types:
            targets.add((r["to_coll"], r["to_id"]))
    out: dict[str, list[str]] = {}
    for (_, i), v in (await _info(tx, org, list(targets))).items():
        if v["cid"] and not v["deleted"]:
            out.setdefault(v["cid"], []).append(i)
    return out



def _walk_app(*, opens: bool, granted: set[str]) -> Callable[[str, bool, dict[str, Any]], bool]:
    def ok(cid: str, inh: bool, info: dict[str, Any]) -> bool:
        rule = info["rule"]
        return not info["deleted"] and (opens or (inh and rule in ("STRICT", "OPEN"))
                                        or (cid in granted and ((inh and rule == "RESTRICTED") or rule in ("STRICT", "OPEN"))))
    return ok


def _walk_seed(granted: set[str]) -> Callable[[str, bool, dict[str, Any]], bool]:
    def ok(cid: str, inh: bool, info: dict[str, Any]) -> bool:
        return not info["deleted"] and info["rule"] == "OPEN" and (inh or cid in granted)
    return ok



# ---- the walks up, one statement per level -----------------------------------------------------------------------------
# The three questions the per-candidate arms ask (reach the app, find the declaring group, find the seed below) are answered by walking up from
# each candidate, a level at a time, with single-table lookups (edges by the key, nodes by primary key). No plan depends on table statistics.
Node = tuple[str, str]


async def _parents(tx: Tx, org: str, curs: list[Node], only: tuple[str, str] | None = None) -> dict[Node, list[Node]]:
    """Hierarchy parents of each node; ``only`` = (parent collection, child collection) restricts the edges."""
    want = set(curs)
    out: dict[Node, list[Node]] = {}
    ids = [i for _, i in curs]
    for k in range(0, len(ids), _CHUNK):
        for r in await tx.fetch("""/* pipeshub:graph.access_parents */ SELECT e.from_coll, e.from_id, e.to_coll, e.to_id FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = 'nodeRelations' AND e.to_id = ANY($2::text[]) AND e.doc->>'relationshipType' = ANY($3::text[])""", org, ids[k:k + _CHUNK], _HIER_TYPES):
            child = (r["to_coll"], r["to_id"])
            if child in want and (only is None or (r["from_coll"], r["to_coll"]) == only):
                out.setdefault(child, []).append((r["from_coll"], r["from_id"]))
    return out


async def _inherit_pairs(tx: Tx, org: str, curs: list[Node]) -> set[tuple[Node, Node]]:
    out: set[tuple[Node, Node]] = set()
    ids = [i for _, i in curs]
    for k in range(0, len(ids), _CHUNK):
        for r in await tx.fetch("""/* pipeshub:graph.access_inherit_pairs */ SELECT i.from_coll, i.from_id, i.to_coll, i.to_id FROM graph.edges i WHERE i.org_id = $1
            AND i.coll = 'inheritPermissions' AND i.from_id = ANY($2::text[])""", org, ids[k:k + _CHUNK]):
            out.add(((r["from_coll"], r["from_id"]), (r["to_coll"], r["to_id"])))
    return out


async def _up(tx: Tx, org: str, starts: list[tuple[str, Node]], step: Callable[..., bool], visit: Callable[[str, Node, int], None],
              only: tuple[str, str] | None = None, *, parents_must_exist: bool = True) -> None:
    """From each start (sid, node) up the hierarchy. ``step(sid, cur, cur_info, inherits, parent, parent_info)`` says whether to go from ``cur`` to
    ``parent``; ``visit(sid, node, depth)`` sees every node reached, the start included."""
    seen: set[tuple[str, Node]] = set(starts)
    for sid, n in starts:
        visit(sid, n, 0)
    frontier = list(seen)
    for depth in range(1, MAX_HOPS + 1):
        if not frontier:
            break
        curs = list({n for _, n in frontier})
        parents = await _parents(tx, org, curs, only)
        every = list({*curs, *(p for ps in parents.values() for p in ps)})
        info = await _info(tx, org, every)
        inh = await _inherit_pairs(tx, org, curs)
        nxt = []
        for sid, cur in frontier:
            ci = info.get(cur)
            if ci is None:
                continue
            for par in parents.get(cur, ()):
                pi = info.get(par)
                if (pi is None and parents_must_exist) or (sid, par) in seen or not step(sid, cur, ci, (cur, par) in inh, par, pi):
                    continue
                seen.add((sid, par))
                nxt.append((sid, par))
                visit(sid, par, depth)
        frontier = nxt


async def from_app(tx: Tx, org: str, ids: list[str], colls: list[str], appids: list[str], opens: list[bool], granted_l: list[str]) -> list[dict[str, str]]:
    """The starts that reach their app over hops the granted-hop rule admits."""
    granted = set(granted_l)
    app_of = dict(zip(ids, appids, strict=True))
    opens_of = dict(zip(ids, opens, strict=True))
    hit: set[str] = set()

    def step(sid: str, cur: Node, ci: dict, inh: bool, par: Node, pi: dict | None) -> bool:
        app = app_of[sid]
        if par[0] == "recordGroups" and (pi or {}).get("model") == "RECORD_GROUP_LEVEL" and ((pi or {}).get("cid") or "") == app:
            return False
        return _walk_app(opens=opens_of[sid], granted=granted)(cur[1], inh, ci)

    def visit(sid: str, n: Node, depth: int) -> None:
        if depth and n == ("apps", app_of[sid]):
            hit.add(sid)

    await _up(tx, org, [(i, (c, i)) for i, c in zip(ids, colls, strict=True)], step, visit)
    return [{"sid": s} for s in hit]


async def declared_groups(tx: Tx, org: str, ids: list[str], colls: list[str], appids: list[str]) -> list[dict[str, str]]:
    """(start, declaring group): a group in or above the start's own groups that declares RECORD_GROUP_LEVEL for the start's app."""
    app_of = dict(zip(ids, appids, strict=True))
    starts: list[tuple[str, Node]] = [(i, ("recordGroups", i)) for i, c in zip(ids, colls, strict=True) if c == "recordGroups"]
    own = [(i, c) for i, c in zip(ids, colls, strict=True) if c != "recordGroups"]
    if own:
        by_id = dict(own)
        for k in range(0, len(own), _CHUNK):
            rows = await tx.fetch("""/* pipeshub:graph.access_own_groups */ SELECT e.from_coll, e.from_id, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo'
                AND e.to_coll = 'recordGroups' AND e.from_id = ANY($2::text[])""", org, [i for i, _ in own[k:k + _CHUNK]])
            starts.extend((r["from_id"], ("recordGroups", r["to_id"])) for r in rows if by_id.get(r["from_id"]) == r["from_coll"])
    # a start group must exist and be live; further groups are checked when they are stepped from
    live = await _info(tx, org, list({n for _, n in starts}))
    starts = [(s, n) for s, n in starts if n in live and not live[n]["deleted"]]
    found: set[tuple[str, str]] = set()

    def visit(sid: str, n: Node, _depth: int) -> None:
        found.add((sid, n[1]))

    await _up(tx, org, starts, lambda _s, _c, ci, _i, _p, _pi: not ci["deleted"], visit, only=("recordGroups", "recordGroups"), parents_must_exist=False)
    info = await _info(tx, org, [("recordGroups", g) for _, g in found])
    return [{"sid": s, "gid": g} for s, g in found
            if (v := info.get(("recordGroups", g))) and v["cid"] == app_of[s] and v["model"] == "RECORD_GROUP_LEVEL" and not v["deleted"]]


async def below_seed(tx: Tx, org: str, ids: list[str], colls: list[str], appids: list[str], granted_l: list[str]) -> list[dict[str, str]]:
    """(start, seed): a granted open record or group of the start's connector above it, every hop open and inheriting (or granted)."""
    granted = set(granted_l)
    app_of = dict(zip(ids, appids, strict=True))
    reached: set[tuple[str, Node]] = set()
    walk = _walk_seed(granted)

    def visit(sid: str, n: Node, depth: int) -> None:
        if depth >= 1 and n[1] in granted and n[0] in ("records", "recordGroups"):
            reached.add((sid, n))

    await _up(tx, org, [(i, (c, i)) for i, c in zip(ids, colls, strict=True)], lambda _s, cur, ci, inh, _p, _pi: walk(cur[1], inh, ci), visit)
    info = await _info(tx, org, list({n for _, n in reached}))
    return [{"sid": s, "seed": n[1], "seed_coll": n[0]} for s, n in reached
            if (v := info.get(n)) and v["cid"] == app_of[s] and not v["deleted"] and v["rule"] == "OPEN"]



def _is_seed(doc: dict, coll: str, app_id: str, granted: set[str], node_id: str) -> bool:
    rule = doc.get("accessRule")      # a stored null is a missing rule (OPEN), as the SQL coalesce reads it; an empty string is not OPEN
    return (coll in ("records", "recordGroups") and doc.get("connectorId") == app_id and not doc.get("isDeleted")
            and (rule is None or rule == "OPEN") and node_id in granted)


async def accessible_rows(tx: Tx, org: str, node_ids: list[str], vrids: list[str], access: dict[str, Any]) -> list[dict[str, Any]]:
    if access.get("probe"):      # a browse that tests grants from the node: its grants are not in ``by_connector``, so it is not answered by guessing
        raise NotImplementedInPostgres("an access check with a grant probe is not implemented")
    gated = set(access["gated_app_ids"])
    granted = {g for ids in access["by_connector"].values() for g in ids}
    granted_l = sorted(granted)
    # two statements, each served by one index, instead of one with an OR across them (which plans badly without statistics)
    rows = await tx.fetch("""/* pipeshub:graph.access_targets */ SELECT t.coll, t.id, t.doc, CASE WHEN t.coll = 'apps' THEN t.id ELSE t.doc->>'connectorId' END AS app_id
        FROM graph.nodes t
        WHERE t.org_id = $1 AND t.doc->>'orgId' = $1 AND NOT coalesce((t.doc->>'isDeleted')::boolean, false)
          AND (NOT coalesce((t.doc->>'isPlaceholder')::boolean, false) OR t.id = ANY($2::text[]))
          AND t.coll IN ('records', 'recordGroups', 'apps') AND t.id = ANY($2::text[])""", org, node_ids)
    if vrids:
        seen_ids = {r["id"] for r in rows}
        rows = [*rows, *(r for r in await tx.fetch("""/* pipeshub:graph.access_targets_vrid */ SELECT t.coll, t.id, t.doc, CASE WHEN t.coll = 'apps' THEN t.id ELSE t.doc->>'connectorId' END AS app_id
            FROM graph.nodes t
            WHERE t.org_id = $1 AND t.doc->>'orgId' = $1 AND NOT coalesce((t.doc->>'isDeleted')::boolean, false)
              AND (NOT coalesce((t.doc->>'isPlaceholder')::boolean, false) OR t.id = ANY($2::text[]))
              AND t.coll = 'records' AND t.doc->>'virtualRecordId' = ANY($3::text[])""", org, node_ids, vrids) if r["id"] not in seen_ids)]
    targets = []
    for r in rows:
        doc = json.loads(r["doc"]) if isinstance(r["doc"], str) else dict(r["doc"])
        targets.append((r["coll"], r["id"], doc, r["app_id"]))
    apps = {}
    if gated:
        for a in await tx.fetch("/* pipeshub:graph.access_apps */ SELECT id, doc FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])",
                                org, sorted(gated)):
            d = json.loads(a["doc"]) if isinstance(a["doc"], str) else dict(a["doc"])
            apps[a["id"]] = d.get("permissionModel") == "APP_LEVEL" or d.get("type") == "KB"
    ok: set[str] = set()
    pending = []  # gated targets no direct arm admitted
    for coll, nid, doc, app_id in targets:
        if app_id in gated and app_id in apps:
            if coll == "apps" or _is_seed(doc, coll, app_id, granted, nid):
                ok.add(nid)
            else:
                pending.append((coll, nid, doc, app_id))
    # KB records: opened by the app, member of it
    kb = [(c, i, a) for c, i, d, a in pending if apps[a] and c == "records"]
    if kb:
        app_of_kb = {i: a for _, i, a in kb}
        # custom plan: the generic one reads every membership edge of the app (edges_by_target) instead of the asked records by key
        for r in await tx.fetch_custom_plan("/* pipeshub:graph.access_kb */ SELECT e.from_id, e.to_id FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.from_coll = 'records' AND e.from_id = ANY($2::text[]) AND e.to_coll = 'apps' AND e.to_id = ANY($3::text[])",
                                org, [i for _, i, _ in kb], sorted({a for _, _, a in kb})):
            if app_of_kb[r["from_id"]] == r["to_id"]:      # a member of its own app, not of another that also opens everything
                ok.add(r["from_id"])
    pending = [p for p in pending if p[1] not in ok]
    if pending:
        ids, colls, appids = [p[1] for p in pending], [p[0] for p in pending], [p[3] for p in pending]
        opens = [apps[p[3]] for p in pending]
        declared = await declared_groups(tx, org, ids, colls, appids)
        dg_by_sid: dict[str, set[str]] = {}
        for r in declared:
            dg_by_sid.setdefault(r["sid"], set()).add(r["gid"])
        app_of = {p[1]: p[3] for p in pending}
        # a declaration counts when granted or reachable from its app
        dgs = sorted({g for gs in dg_by_sid.values() for g in gs})
        dg_ok = {g for g in dgs if g in granted}
        rest = [g for g in dgs if g not in dg_ok]
        if rest:
            a_of = {g: app_of[s] for s, gs in dg_by_sid.items() for g in gs}
            reach = await from_app(tx, org, rest, ["recordGroups"] * len(rest), [a_of[g] for g in rest], [apps[a_of[g]] for g in rest], granted_l)
            dg_ok |= {r["sid"] for r in reach}
        declared_ok = {s for s, gs in dg_by_sid.items() if gs & dg_ok}
        ok |= declared_ok
        left = [(i, c, a, o) for i, c, a, o in zip(ids, colls, appids, opens, strict=True) if i not in ok]
        if left:
            reach = await from_app(tx, org, [x[0] for x in left], [x[1] for x in left], [x[2] for x in left], [x[3] for x in left], granted_l)
            ok |= {r["sid"] for r in reach}
        left = [x for x in left if x[0] not in ok]
        if left and granted_l:
            pairs = await below_seed(tx, org, [x[0] for x in left], [x[1] for x in left], [x[2] for x in left], granted_l)
            seeds = sorted({r["seed"] for r in pairs})
            if seeds:
                seed_app = {r["seed"]: app_of[r["sid"]] for r in pairs}
                # a seed another arm admits is not a seed: its hierarchy-only children stay out
                seed_coll = {r["seed"]: r["seed_coll"] for r in pairs}
                bad = {r["sid"] for r in await from_app(tx, org, seeds, [seed_coll[s] for s in seeds],
                                                       [seed_app[s] for s in seeds], [apps[seed_app[s]] for s in seeds], granted_l)}
                dd = await declared_groups(tx, org, seeds, [seed_coll[s] for s in seeds], [seed_app[s] for s in seeds])
                dgs2 = sorted({r["gid"] for r in dd})
                dg2_ok = {g for g in dgs2 if g in granted}
                rest2 = [g for g in dgs2 if g not in dg2_ok]
                if rest2:
                    a2 = {r["gid"]: seed_app[r["sid"]] for r in dd}
                    dg2_ok |= {r["sid"] for r in await from_app(tx, org, rest2, ["recordGroups"] * len(rest2), [a2[g] for g in rest2],
                                                                 [apps[a2[g]] for g in rest2], granted_l)}
                bad |= {r["sid"] for r in dd if r["gid"] in dg2_ok}
                good = {(r["sid"]) for r in pairs if r["seed"] not in bad}
                ok |= good
    # a record outside every app (a chat attachment): only a grant on the record itself opens it
    orphan = [(c, i, d) for c, i, d, a in targets if i not in ok and c == "records" and d.get("origin") not in (None, "CONNECTOR")]
    if orphan and access["grantee_ids"]:
        known = {r["id"] for r in await tx.fetch("/* pipeshub:graph.access_orphan_apps */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps' AND id = ANY($2::text[])",
                                                 org, sorted({d.get("connectorId") for _, _, d in orphan if d.get("connectorId")}))}
        cand = [i for c, i, d in orphan if d.get("connectorId") not in known]
        if cand:
            for r in await tx.fetch("""/* pipeshub:graph.access_orphan */ SELECT DISTINCT e.to_id FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = 'permission' AND e.to_coll = 'records' AND e.to_id = ANY($2::text[]) AND e.from_id = ANY($3::text[])
                  AND (e.from_coll <> 'organizations' OR e.doc->>'type' = ANY($4::text[]))""", org, cand, access["grantee_ids"], list(ORG_SHARE_PERMISSION_TYPES)):
                ok.add(r["to_id"])
    out = []
    groups: dict[tuple[str, str], list[str]] = {}
    admitted = [(c, i) for c, i, d, a in targets if i in ok]
    if admitted:
        for r in await tx.fetch("/* pipeshub:graph.access_groups */ SELECT e.from_coll, e.from_id, array_agg(e.to_id) AS g FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'belongsTo' AND e.to_coll = 'recordGroups' AND e.from_id = ANY($2::text[]) GROUP BY e.from_coll, e.from_id",
                                org, [i for _, i in admitted]):
            groups[(r["from_coll"], r["from_id"])] = list(r["g"])
    for c, i, d, _a in targets:
        if i in ok:
            out.append({"id": i, "vrid": d.get("virtualRecordId"), "connectorId": d.get("connectorId"), "indexingStatus": d.get("indexingStatus"),
                        "hasIndexedCopy": bool(d.get("hasIndexedCopy")), "isInternal": bool(d.get("isInternal")), "isPlaceholder": bool(d.get("isPlaceholder")), "groupIds": groups.get((c, i), [])})
    return out
