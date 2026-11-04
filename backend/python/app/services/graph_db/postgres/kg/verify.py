"""Verify: kg equals what the graph says, both ways.

For one connector: its nodes are read from graph.nodes in keyset batches, the rows kg should hold for them are derived (model.derive) and compared with what kg holds (records, the links
of the children, the grants on the nodes); what kg holds for the connector that no node of the graph accounts for is extra. For the org's principals: the closure rows of every user. The
digest is the SHA-256 of the expected rows in a fixed order. Two more checks that the writers cannot make: every record row's ``sk`` is the one ``kg.node_keys`` holds for its
``(coll, id)`` (``w_records_upsert`` takes any sk), and every connector the graph shares a hierarchy parent or a group with another connector for is marked ``cross_app``: kg may mark
more than the graph needs (the mark is sticky, written once), never less.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import Key, derive
from app.services.graph_db.postgres.kg.reader import load_sks, snapshot

if TYPE_CHECKING:
    from app.core.db import Tx

PAGE = 2000
LIMIT = 100                        # examples of one kind of difference kept in a result


@dataclass
class Result:
    expected: int = 0
    missing: list[str] = field(default_factory=list)     # expected and not in kg
    extra: list[str] = field(default_factory=list)       # in kg and not expected
    changed: list[str] = field(default_factory=list)     # in both, different
    digest: str = ""

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.changed)

    def summary(self) -> str:
        return f"expected={self.expected} missing={len(self.missing)} extra={len(self.extra)} changed={len(self.changed)}"

    def __add__(self, other: Result) -> Result:
        return Result(self.expected + other.expected, self.missing + other.missing, self.extra + other.extra, self.changed + other.changed,
                      hashlib.sha256((self.digest + other.digest).encode()).hexdigest())


def _compare(res: Result, exp: dict[str, str], act: dict[str, str]) -> None:
    for k, v in exp.items():
        if k not in act:
            res.missing.append(f"{k} {v}")
        elif act[k] != v:
            res.changed.append(f"{k} expected {v} got {act[k]}")
    for k, v in act.items():
        if k not in exp:
            res.extra.append(f"{k} {v}")


def _line(row: dict, cols: tuple[str, ...]) -> str:
    return repr(tuple(row.get(c) for c in cols))


async def app_nodes(tx: Tx, org: str, app_id: str, after: Key | None, limit: int) -> list[Key]:
    """The next page of a connector's nodes in (collection, id) order: the app, its record groups, its records."""
    c0, i0 = after if after else ("", "")
    rows = await tx.fetch("""/* pipeshub:kg.app_nodes */ SELECT n.coll, n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = ANY($2::text[])
        AND ((n.coll = 'apps' AND n.id = $3) OR (n.coll <> 'apps' AND n.doc->>'connectorId' = $3)) AND (n.coll, n.id) > ($4, $5) ORDER BY n.coll, n.id LIMIT $6""",
                          org, ["apps", "recordGroups", "records"], app_id, c0, i0, limit)
    return [(r["coll"], r["id"]) for r in rows]


async def _readonly(tx: Tx, org: str):  # noqa: ANN202
    async def allocate(existing: set[Key]) -> dict[Key, int]:
        return await load_sks(tx, org, existing)
    return allocate


async def verify_app(tx: Tx, org: str, app_id: str) -> Result:
    """Everything kg holds for one connector against the graph. Call under the connector's lock, in a unit that stays open for the whole read."""
    res = Result()
    h = hashlib.sha256()
    expected_sks: set[int] = set()
    cross: set[int] = set()
    alloc = await _readonly(tx, org)
    after: Key | None = None
    while True:
        page = await app_nodes(tx, org, app_id, after, PAGE)
        if not page:
            break
        after = page[-1]
        focus = set(page)
        snap = await snapshot(tx, org, focus, alloc)
        if lacking := sorted(k for k in snap.nodes if k not in snap.sk):     # a node of the graph that is connected to this connector and kg has no key for: it came after the copy
            res.missing += [f"k|{k[0]}:{k[1]} (no node key)" for k in lacking[:LIMIT]]
        try:
            d = derive(focus, snap)
        except KeyError as e:               # a node (or a neighbour) kg has no key for: the copy has not reached it
            res.missing.append(f"k|{e} (no node key)")
            continue
        sks = sorted(d.records)
        expected_sks |= set(sks)
        cross |= d.cross_apps
        cols = tuple(sorted({c for row in d.records.values() for c in row}))
        exp = {f"r|{sk}": _line(d.records[sk], cols) for sk in sks}
        exp |= {f"l|{p}|{k}|{c}": "" for (p, k, c) in d.links}
        exp |= {f"g|{p}|{n}|{g}": repr(v) for (p, n, g), v in d.grants.items() if n in d.grant_nodes}
        act: dict[str, str] = {}
        if sks:
            for r in await tx.fetch("""/* pipeshub:kg.verify_records */ SELECT * FROM kg.records WHERE org_id = $1 AND sk = ANY($2::bigint[])""", org, sks):
                act[f"r|{r['sk']}"] = _line(dict(r), cols)
            for r in await tx.fetch("""/* pipeshub:kg.verify_links */ SELECT parent_sk, kind, child_sk FROM kg.links WHERE org_id = $1 AND child_sk = ANY($2::bigint[])""", org, sks):
                act[f"l|{r['parent_sk']}|{r['kind']}|{r['child_sk']}"] = ""
            for r in await tx.fetch("""/* pipeshub:kg.verify_grants */ SELECT principal_sk, node_sk, gtype, role, app_sk, leaf FROM kg.scope_members WHERE org_id = $1 AND node_sk = ANY($2::bigint[])""", org, sks):
                act[f"g|{r['principal_sk']}|{r['node_sk']}|{r['gtype']}"] = repr((r["role"], r["app_sk"], r["leaf"]))
        _compare(res, exp, act)
        res.expected += len(exp)
        for k in sorted(exp):
            h.update(f"{k}={exp[k]}\n".encode())
    app_sk = (await load_sks(tx, org, {("apps", app_id)})).get(("apps", app_id))
    if app_sk is not None:
        sk_after = 0
        while True:        # kg rows of the connector that no node of the graph explains
            rows = await tx.fetch("""/* pipeshub:kg.verify_extra_records */ SELECT sk FROM kg.records WHERE org_id = $1 AND app_sk = $2 AND sk > $3 ORDER BY sk LIMIT $4""", org, app_sk, sk_after, PAGE)
            if not rows:
                break
            sk_after = rows[-1]["sk"]
            res.extra += [f"r|{r['sk']} (no node)" for r in rows if r["sk"] not in expected_sks]
        cur = (0, 0, "")
        while True:
            rows = await tx.fetch("""/* pipeshub:kg.verify_extra_grants */ SELECT principal_sk, node_sk, gtype FROM kg.scope_members WHERE org_id = $1 AND app_sk = $2
                AND (node_sk, principal_sk, gtype) > ($3, $4, $5) ORDER BY node_sk, principal_sk, gtype LIMIT $6""", org, app_sk, *cur, PAGE)
            if not rows:
                break
            cur = (rows[-1]["node_sk"], rows[-1]["principal_sk"], rows[-1]["gtype"])
            res.extra += [f"g|{r['principal_sk']}|{r['node_sk']}|{r['gtype']} (no node)" for r in rows if r["node_sk"] not in expected_sks]
        # a record row whose sk is not the one node_keys holds for its (coll, id): the writers take any sk
        res.changed += [f"s|{r['sk']} {r['coll']}:{r['id']} (node_keys holds {r['key_sk']})" for r in await tx.fetch(
            """/* pipeshub:kg.verify_sk */ SELECT r.sk, r.coll, r.id, k.sk AS key_sk FROM kg.records r
                LEFT JOIN kg.node_keys k ON k.org_id = r.org_id AND k.coll = r.coll AND k.id = r.id
                WHERE r.org_id = $1 AND r.app_sk = $2 AND k.sk IS DISTINCT FROM r.sk ORDER BY r.sk LIMIT $3""", org, app_sk, LIMIT)]
    if cross:   # superset: kg may mark a connector the graph no longer needs marked, never miss one
        marked = {r["app_sk"] for r in await tx.fetch("""/* pipeshub:kg.verify_cross */ SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND cross_app AND app_sk = ANY($2::bigint[])""",
                                                      org, sorted(cross))}
        res.missing += [f"x|{sk} (shares a parent or group with another connector, not marked)" for sk in sorted(cross - marked)]
    res.digest = h.hexdigest()
    return res


async def verify_principals(tx: Tx, org: str) -> Result:
    """The closure rows of every user of the org."""
    res = Result()
    h = hashlib.sha256()
    alloc = await _readonly(tx, org)
    expected_users: set[int] = set()
    after = ""
    while True:
        rows = await tx.fetch("""/* pipeshub:kg.verify_users */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'users' AND id > $2 ORDER BY id LIMIT $3""", org, after, PAGE)
        if not rows:
            break
        after = rows[-1]["id"]
        focus = {("users", r["id"]) for r in rows}
        snap = await snapshot(tx, org, focus, alloc)
        if lacking := sorted(k for k in snap.nodes if k not in snap.sk):
            res.missing += [f"k|{k[0]}:{k[1]} (no node key)" for k in lacking[:LIMIT]]
        try:
            d = derive(focus, snap)
        except KeyError as e:
            res.missing.append(f"k|{e} (no node key)")
            continue
        expected_users |= d.closure_users
        exp = {f"c|{u}|{p}|{a}": v for (u, p, a), v in d.closure.items()}
        act: dict[str, str] = {}
        if d.closure_users:
            for r in await tx.fetch("""/* pipeshub:kg.verify_closure */ SELECT user_sk, principal_sk, app_sk, via FROM kg.principal_closure WHERE org_id = $1 AND user_sk = ANY($2::bigint[])""",
                                    org, sorted(d.closure_users)):
                act[f"c|{r['user_sk']}|{r['principal_sk']}|{r['app_sk']}"] = r["via"]
        _compare(res, exp, act)
        res.expected += len(exp)
        for k in sorted(exp):
            h.update(f"{k}={exp[k]}\n".encode())
    cur = (0, 0, 0)
    while True:
        rows = await tx.fetch("""/* pipeshub:kg.verify_extra_closure */ SELECT user_sk, principal_sk, app_sk FROM kg.principal_closure WHERE org_id = $1
            AND (user_sk, principal_sk, app_sk) > ($2, $3, $4) ORDER BY user_sk, principal_sk, app_sk LIMIT $5""", org, *cur, PAGE)
        if not rows:
            break
        cur = (rows[-1]["user_sk"], rows[-1]["principal_sk"], rows[-1]["app_sk"])
        res.extra += [f"c|{r['user_sk']}|{r['principal_sk']}|{r['app_sk']} (no user)" for r in rows if r["user_sk"] not in expected_users]
    res.digest = h.hexdigest()
    return res
