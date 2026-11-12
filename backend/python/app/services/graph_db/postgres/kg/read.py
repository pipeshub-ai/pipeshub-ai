"""The access check from kg: which of the asked nodes the viewer may open, decided from the stamped classes instead of the walk. On unless ``PG_KG_READ=0``.

The switch is per node and fails closed to today's walk (``access.accessible_rows``). A node is answered from kg only when its connector is served (``drainer.serves``: enforced,
verified by this code, nothing waiting that can alter it, no restamp job hiding it) and the row is stamped and not hidden; every other node, and the whole call when the switch is off,
inside an explicit transaction, for the system role or failing, takes the walk. The two answers are merged. What the walk keeps for itself:

- a row with no class (refused by the compiler), a placeholder, a record of no connector (``r13`` true or not yet stamped: NULL), a row with no ``path`` (not stamped, a cycle or a chain
  too deep), a hidden row (``fence_job``), an id kg does not hold and an id held in two collections;
- every virtual record id (kg has no index for them yet);
- a node whose class names a literal that kg itself cannot vouch for (see :func:`class_verdicts`).

The viewer is the walk's own description (``get_knowledge_hub_access_v3``: the principals, the gated connectors and the grants per connector). What differs between the two answers is how
a node is decided: the walk follows the hierarchy of each node, kg evaluates the node's class (``kg.scope_classes``) against those grants.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.graph_db.postgres import access as access_sql
from app.services.graph_db.postgres.kg import drainer, flush, net
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.core.db import KernelDB, Tx

logger = logging.getLogger(__name__)
ENV = "PG_KG_READ"
SELF = "@self"
CHUNK = 10_000

ANSWERS = METRICS_BACKEND.counter("pipeshub_kg_read_nodes_total", "Nodes an access check decided, by who answered", ["answer"])
FALLBACKS = METRICS_BACKEND.counter("pipeshub_kg_read_fallback_total", "Nodes (or whole calls) that kg handed to the walk, by reason", ["reason"])

# The asked nodes: apps, record groups and records. One index probe per asked id (the LIMIT keeps the planner from turning the lateral join into a scan of the org when it has no statistics). ``hidden`` is the record's own mark;
# the connector-wide one (a queued restamp job that hides) is part of ``drainer.serves``.
BY_ID = """/* pipeshub:kg.read_rows */ SELECT r.coll, r.id, r.kind, r.deleted, r.placeholder, r.class_id, r.r13, r.path IS NULL AS no_path, r.fence_job IS NOT NULL AS hidden, a.id AS app_id
    FROM unnest($2::text[]) AS q(id)
    CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.coll = ANY('{1,2,3}'::smallint[]) AND k.id = q.id LIMIT 3) r
    LEFT JOIN kg.node_keys a ON a.org_id = r.org_id AND a.coll = 1 AND a.sk = r.app_sk"""
CLASSES = """/* pipeshub:kg.read_classes */ SELECT c.id, c.clauses FROM unnest($2::bigint[]) AS q(id)
    CROSS JOIN LATERAL (SELECT * FROM kg.scope_classes k WHERE k.org_id = $1 AND k.id = q.id LIMIT 1) c"""
# A literal is a node a grant can open the records below. A record that is a literal must have a child kg holds (a hierarchy child by ``parent_sk`` or a non-primary parent link, kinds as
# kg.model.LINK_HIERARCHY): its ``leaf`` flag alone is not trusted, because a stale flag is the only thing between a grant on a childless record and the records the class says it opens.
LITERALS = """/* pipeshub:kg.read_literals */ SELECT r.sk, r.id, r.leaf,
    (r.kind <> 3 OR coalesce((SELECT true FROM kg.records c WHERE c.org_id = r.org_id AND c.parent_sk = r.sk LIMIT 1),
        (SELECT true FROM kg.links l WHERE l.org_id = r.org_id AND l.parent_sk = r.sk AND l.kind = 1 LIMIT 1), false)) AS has_child
    FROM unnest($2::bigint[]) AS q(sk)
    CROSS JOIN LATERAL (SELECT * FROM kg.records k WHERE k.org_id = $1 AND k.sk = q.sk LIMIT 1) r"""
# With no statistics a bitmap scan of the whole org can look cheaper than the index searches an array of ids needs (plan_cache_mode, set by the unit, is the other half of the profile).
# The short lock and statement limits hand a blocked or slow read to the walk at once, instead of holding a pooled connection for the service's own limits.
PROFILE = """/* pipeshub:kg.read_profile */ SELECT set_config('enable_bitmapscan', 'off', true), set_config('lock_timeout', '200ms', true),
    set_config('statement_timeout', '2s', true)"""


def enabled() -> bool:
    """On unless PG_KG_READ=0 (the off switch): with 0 nothing reads kg and every check is the walk's. Unset and 1 are on; any other value raises (``flush.check_env``). Whatever this
    says, a connector that is not served, a node kg cannot decide and a read that is blocked or slow take the walk."""
    flush.check_env()
    return os.environ.get(ENV) != "0"


@dataclass
class Decision:
    admit: set[str] = field(default_factory=set)               # answered from kg: admitted (still to be shaped from the documents)
    walk: list[str] = field(default_factory=list)              # node ids the walk decides
    denied: int = 0                                            # answered from kg: not admitted
    deny: set[str] = field(default_factory=set)                # the same, by id
    apps: dict[str, str] = field(default_factory=dict)         # connector of each node kg decided


async def class_verdicts(tx: Tx, org: str, classes: dict[int, list[list[Any]]], granted: set[str]) -> dict[int, tuple[bool, bool] | None]:
    """Per class, ``(plain, own)`` for a viewer whose grants are ``granted`` (node ids): ``plain`` when some clause with no ``@self`` has every literal granted, ``own`` when a clause with
    ``@self`` has every other literal granted (the row is then readable if its own id is granted too). ``None`` when a literal of the class is a node kg cannot vouch for: not held, not
    stamped (``leaf`` NULL), flagged a leaf, or a record without a child. A consistent projection has no such literal, so it is a stale or half-written one: the walk decides."""
    lits = sorted({x for cl in classes.values() for clause in cl for x in clause if x != SELF})
    live: set[int] = set()
    seen: set[int] = set()
    for k in range(0, len(lits), CHUNK):
        for r in await tx.fetch(LITERALS, org, lits[k:k + CHUNK]):
            if r["leaf"] is False and r["has_child"]:
                seen.add(r["sk"])
                if r["id"] in granted:
                    live.add(r["sk"])
    out: dict[int, tuple[bool, bool] | None] = {}
    for c, cl in classes.items():
        if any(x != SELF and x not in seen for clause in cl for x in clause):
            out[c] = None
            continue
        plain = any(SELF not in clause and all(x in live for x in clause) for clause in cl)
        own = any(SELF in clause and all(x in live for x in clause if x != SELF) for clause in cl)
        out[c] = (plain, own)
    return out


async def decide(tx: Tx, org: str, ids: list[str], access: dict[str, Any]) -> Decision:
    """Classify the asked nodes: admitted by kg, denied by kg, or left to the walk (module docstring). Runs in one snapshot."""
    d = Decision()
    gated = set(access["gated_app_ids"])
    granted = {g for gs in access["by_connector"].values() for g in gs}
    ids = list(dict.fromkeys(ids))
    by_id: dict[str, list[Any]] = {}
    for i in range(0, len(ids), CHUNK):
        for r in await tx.fetch(BY_ID, org, ids[i:i + CHUNK]):
            by_id.setdefault(r["id"], []).append(r)
    served: dict[str, bool] = {}

    def to_walk(i: str, reason: str) -> None:
        FALLBACKS.inc(reason)
        d.walk.append(i)

    verdict: dict[str, bool] = {}                                      # id -> admitted, for the nodes kg decides
    need: dict[str, int] = {}                                          # id -> class id still to evaluate
    for i in ids:
        rs = by_id.get(i)
        if rs is None:
            to_walk(i, "unknown")                                      # kg has no row (not projected yet, or never existed): the walk knows
            continue
        if len(rs) > 1:
            to_walk(i, "ambiguous")                                    # one id in two collections: the walk's answer is by id
            continue
        r = rs[0]
        app = r["app_id"]
        if app is None:
            to_walk(i, "no_app")
            continue
        if app not in served:
            served[app] = not (net.DISTRUSTED and await net.distrusted(tx, org, app)) and await drainer.serves(tx, org, app)
        if not served[app]:
            to_walk(i, "not_current")
        elif r["deleted"]:
            verdict[i] = False
        elif r["kind"] == 1:
            verdict[i] = app in gated                                  # an app carries no stamps: the gate alone decides it
        elif r["placeholder"] or r["hidden"] or r["r13"] is not False or r["no_path"]:     # r13 NULL is not stamped: never read as false
            to_walk(i, "placeholder" if r["placeholder"] else ("hidden" if r["hidden"] else ("r13" if r["r13"] is not False else "no_path")))
        elif app not in gated:
            verdict[i] = False
        elif r["class_id"] is None:
            to_walk(i, "legacy")
        else:
            need[i] = r["class_id"]
    if need:
        classes = {c["id"]: (json.loads(c["clauses"]) if isinstance(c["clauses"], str) else list(c["clauses"])) for c in await tx.fetch(CLASSES, org, sorted(set(need.values())))}
        by_class = await class_verdicts(tx, org, classes, granted)
        for i, c in need.items():
            if by_class.get(c) is None:                                # no class row, or a literal kg cannot vouch for: never decide from it
                to_walk(i, "legacy" if c not in classes else "stale_literal")
                continue
            plain, own = by_class[c]  # type: ignore[misc]
            verdict[i] = plain or (own and i in granted)
    for i, ok in verdict.items():
        d.apps[i] = by_id[i][0]["app_id"]
        if ok:
            d.admit.add(i)
        else:
            d.denied += 1
            d.deny.add(i)
    return d


async def _read(tx: Tx, org: str, ids: list[str], access: dict[str, Any]) -> tuple[Decision, list[dict[str, Any]]]:
    await tx.fetchval(PROFILE)
    d = await decide(tx, org, ids, access)
    return d, (await access_sql.admitted_rows(tx, org, sorted(d.admit)) if d.admit else [])


async def accessible_rows(run: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]], org: str, ids: list[str], vrids: list[str], access: dict[str, Any],
                          walk: Callable[[list[str], list[str], dict[str, Any]], Awaitable[list[dict[str, Any]]]], db: KernelDB | None = None) -> list[dict[str, Any]]:
    """``access.accessible_rows`` with kg answering for the nodes it may. ``run`` opens a read unit (one snapshot, the kg planner profile); ``walk(ids, vrids, access)`` is the walk.
    With ``db``, a share of the answers kg gave is repeated by the walk off this call's path (:mod:`net`), and a difference takes the connector back to the walk."""
    if access.get("probe"):                                            # the walk refuses a grant probe; kg does not guess
        return await walk(ids, vrids, access)
    try:
        d, shaped = await run(lambda tx: _read(tx, org, ids, access))
    except Exception:
        logger.warning("kg access check failed; the walk answers", exc_info=True)
        FALLBACKS.inc("error")
        return await walk(ids, vrids, access)
    rows = {r["id"]: r for r in shaped}
    if d.walk or vrids:
        for r in await walk(d.walk, vrids, access):
            rows.setdefault(r["id"], r)
    ANSWERS.inc("kg", value=len(d.admit) + d.denied)
    ANSWERS.inc("walk", value=len(d.walk))
    if db is not None and (d.admit or d.denied):
        net.maybe_sample(run, db, org, d.admit | d.deny, access)
    return list(rows.values())
