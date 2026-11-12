"""The batched role read of entity search (``_roles_on``) gives, for every record, what the per-record read (``_role_on``) gives, on random worlds.

The worlds mix every kind of grant the role read counts (the user's own, a group's, a role's, a team's with and without a role on its own edge, the organization's, a source
account the user authenticated as in the record's connector), grants on the record, on what it inherits from (chains, a cycle) and on its collection, roles the read ignores
(a retired one, an unknown one, an empty one) and ones it caps. Both rankings (the context's and the record details') are compared, with the same answer for ties.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.postgres import entity_access
from app.services.graph_db.postgres.permissions import _CTX_RANK, _DETAIL_RANK

from .test_access import w as w
from .test_entity_access import done, entity_world, ref

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration

APPS, KBS = ("a1", "a2"), ("kb1",)
ROLES = ["OWNER", "ADMIN", "EDITOR", "WRITER", "READER", "ORGANIZER", "COMMENTER", "OTHERS", "FOO", "", None]
USERS = ["u0", "u1", "u2", "u3", "src0", "src1"]


async def build(w: World, rng: random.Random, n: int) -> list[str]:
    """A random world; the ids of its records."""
    await w.nodes("users", *({"_key": u, "userId": f"uid-{u}"} for u in USERS))
    await w.nodes("groups", {"_key": "g0"}, {"_key": "g1"})
    await w.nodes("roles", {"_key": "ro0"})
    await w.nodes("teams", {"_key": "t0"}, {"_key": "t1"})
    await w.nodes("organizations", {"_key": "o1"})
    await w.nodes("apps", *({"_key": a, "type": "CONNECTOR"} for a in APPS), *({"_key": k, "type": "KB"} for k in KBS))
    await w.nodes("recordGroups", *({"_key": f"rg{i}", "connectorId": rng.choice(APPS)} for i in range(4)))
    nodes = [("apps", a) for a in (*APPS, *KBS)] + [("recordGroups", f"rg{i}") for i in range(4)]
    es: dict[str, list[dict]] = {}

    def edge(coll: str, f: tuple[str, str], t: tuple[str, str], **doc: object) -> None:
        es.setdefault(coll, []).append({"from_collection": f[0], "from_id": f[1], "to_collection": t[0], "to_id": t[1], **doc})

    for i in range(1, 4):                      # record groups inherit from an app or an earlier group
        edge("inheritPermissions", ("recordGroups", f"rg{i}"), rng.choice(nodes[: 3 + i]))
    ids: list[str] = []
    for i in range(n):
        rid = f"r{i}"
        app = rng.choice((*APPS, *KBS))
        await w.nodes("records", {"_key": rid, "connectorId": app, "recordName": rid})
        ids.append(rid)
        if app in KBS:
            if rng.random() < 0.8:
                edge("belongsTo", ("records", rid), ("apps", app))
        elif rng.random() < 0.85:              # an inherit chain through earlier records and groups (a cycle for some)
            edge("inheritPermissions", ("records", rid), rng.choice(nodes + [("records", r) for r in ids[:-1]]))
        if rng.random() < 0.05 and len(ids) > 2:
            edge("inheritPermissions", ("records", ids[0]), ("records", rid))
        nodes.append(("records", rid))
    for _ in range(n * 3):                      # grants of every kind, on records, groups and apps
        target = rng.choice(nodes)
        role = rng.choice(ROLES)
        doc = {} if role is None else {"role": role}
        kind = rng.choice(["user", "user", "group", "role", "team", "org"])
        if kind == "user":
            edge("permission", ("users", rng.choice(USERS)), target, type="USER", **doc)
        elif kind == "group":
            edge("permission", ("groups", rng.choice(["g0", "g1"])), target, type="GROUP", **doc)
        elif kind == "role":
            edge("permission", ("roles", "ro0"), target, type="GROUP", **doc)
        elif kind == "team":
            edge("permission", ("teams", rng.choice(["t0", "t1"])), target, type="TEAM", **doc)
        else:
            edge("permission", ("organizations", "o1"), target, type=rng.choice(["ORG", "DOMAIN"]), **doc)
    for u in USERS[:4]:                         # who belongs where
        for grp, coll in (("g0", "groups"), ("g1", "groups"), ("ro0", "roles")):
            if rng.random() < 0.4:
                edge("permission", ("users", u), (coll, grp), type="USER", role="READER")
        for t in ("t0", "t1"):
            if rng.random() < 0.4:
                edge("permission", ("users", u), ("teams", t), type="USER", **rng.choice([{}, {"role": "OWNER"}, {"role": "READER"}, {"role": "FOO"}]))
        if rng.random() < 0.5:
            edge("belongsTo", ("users", u), ("organizations", "o1"), entityType=rng.choice(["ORGANIZATION", "OTHER"]))
    for u, src in (("u0", "src0"), ("u1", "src0"), ("u2", "src1"), ("u3", "src0")):
        if rng.random() < 0.7:
            edge("authenticatedAs", ("users", u), ("users", src), connectorId=rng.choice(APPS))
    for coll, batch in es.items():
        await w.p.batch_create_edges(batch, coll)
    return ids


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
async def test_the_batched_role_read_gives_each_record_what_the_per_record_read_gives(w: World, seed: int) -> None:
    ids = await build(w, random.Random(seed), 45)
    seen: set = set()
    for user in USERS[:4]:
        for ranks in (_CTX_RANK, _DETAIL_RANK):
            one = await w.p._q(lambda tx, u=user, r=ranks: _per_record(w, tx, u, ids, r), None)
            batch = await w.p._q(lambda tx, u=user, r=ranks: w.p._roles_on(tx, w.org, u, "records", ids, r), None)
            assert batch == one, (seed, user, [i for i in ids if batch[i] != one[i]][:5])
            seen |= {v for v in one.values() if v}
    assert len({role for role, _ in seen}) >= 3 and len({src for _, src in seen}) >= 2      # the worlds reach several roles and sources, or the comparison proves little
    assert await w.p._q(lambda tx: w.p._roles_on(tx, w.org, "u0", "records", [], _CTX_RANK), None) == {}
    assert await w.p._q(lambda tx: w.p._roles_on(tx, w.org, "u0", "records", ["nope", "r1", "r1"], _CTX_RANK), None) == {
        "nope": None, "r1": await w.p._q(lambda tx: w.p._role_on(tx, w.org, "u0", "records", "r1"), None)}


async def _per_record(w: World, tx, user: str, ids: list[str], ranks: dict) -> dict:
    return {rid: await w.p._role_on(tx, w.org, user, "records", rid, ranks) for rid in ids}


async def test_entity_search_walks_the_same_window_with_the_batched_and_with_the_per_record_roles(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The records a user may read for an entity, at several limits and offsets, with 120 candidates of which the user holds a role on some (the read goes ahead 50 at a time)."""
    await entity_world(w, "other-org")
    rng = random.Random(9)
    await w.nodes("users", {"_key": "uu", "userId": "uid-uu"})
    await w.nodes("records", *({"_key": f"c{i}", "connectorId": "x", "recordName": f"c{i}", **done(sourceLastModifiedTimestamp=1000 + i)} for i in range(120)))
    for i in range(120):
        await w.edge("mentionsEntity", ("records", f"c{i}"), ("namedEntities", "E"))
        if rng.random() < 0.15:
            await w.edge("permission", ("users", "uu"), ("records", f"c{i}"), type="USER", role="READER")
    calls: list[int] = []
    real = type(w.p)._roles_on

    async def counting(self, tx, org, user_key, coll, node_ids, ranks=_CTX_RANK) -> dict:
        calls.append(len(node_ids))
        return await real(self, tx, org, user_key, coll, node_ids, ranks)
    monkeypatch.setattr(type(w.p), "_roles_on", counting)

    async def asked(limit: int, offset: int) -> list:
        got = await w.p.get_permitted_entity_records([ref("named_entity", "E", "x")], w.org, "uu", app_level_connector_ids=[], limit_per_entity=limit, offset=offset, window=100)
        return [(r["_key"] if isinstance(r, dict) else r.id) for r in got[("named_entity", "E")]]

    async def per_record(self, tx, org, user_key, coll, node_ids, ranks=_CTX_RANK) -> dict:
        return {i: await self._role_on(tx, org, user_key, coll, i, ranks) for i in node_ids}
    batched = {(limit, offset): await asked(limit, offset) for limit in (1, 5, 20) for offset in (0, 7)}
    assert calls and max(calls) <= entity_access._ROLE_BATCH
    monkeypatch.setattr(type(w.p), "_roles_on", per_record)
    assert batched == {(limit, offset): await asked(limit, offset) for limit in (1, 5, 20) for offset in (0, 7)}
    assert any(batched.values())
