"""Random worlds: groups, memberships, gates, every access rule and the odd edges.

Port of the property-test generator over the typed model. Per world: two apps
(one RECORD_LEVEL, one RECORD_LEVEL, APP_LEVEL or KB), 8 groups with a nesting
chain the rule must ignore, a role and a team, 5 viewers with random
memberships (including wrong-typed membership edges), gates by app relation,
permission or group, a linked account, 1-3 top groups and 3-14 nodes per app
with every access rule (absent included), inherit edges (10% to a non-parent),
record-group-level groups, second parents, attachment and link edges, deleted
and placeholder nodes, org grants of every type, and unattached uploads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model import ORG_ID, Node, World, WorldBuilder

if TYPE_CHECKING:
    import random

RULES = ["OPEN", "STRICT", "RESTRICTED", None]
RGL = "RECORD_GROUP_LEVEL"
INDEXING = ["COMPLETED", "COMPLETED", "COMPLETED", "QUEUED", "FAILED"]


def _shared_vrid(rng: random.Random, earlier: list[str]) -> str | None:
    """One record in five carries the content id of an earlier one, so a citation has copies to choose between."""
    return f"v-{rng.choice(earlier)}" if earlier and rng.random() < 0.2 else None


def _group(b: WorldBuilder, name: str, app: str, rule: str | None, rng: random.Random, rgl: float, deleted: float) -> str:
    return b.node(Node(
        name, "group", app, rule,
        model=RGL if rng.random() < rgl else None, deleted=rng.random() < deleted))


def random_world(rng: random.Random) -> World:
    b = WorldBuilder()
    kinds = [("A", rng.choice(["RECORD", "RECORD", "APP_LEVEL", "KB"])), ("B", "RECORD")]
    apps = [b.node(Node(
        name, "app", app_type="KB" if k == "KB" else "DRIVE",
        model="APP_LEVEL" if k == "APP_LEVEL" else None)) for name, k in kinds]
    groups = [b.principal(f"g{i}", "group") for i in range(8)]
    for i in range(1, 7):  # g0 <- g1 <- ... <- g6: nesting the rule must ignore
        b.member(groups[i], groups[i - 1])
    pool = [*groups, b.principal("r0", "role"), b.principal("t0", "team")]
    users = [b.principal(f"u{i}", org_member=rng.random() < 0.9) for i in range(5)]
    for u in users:
        for p in rng.sample(pool, rng.randint(0, 3)):
            b.member(u, p, kind=rng.choice(["USER", "USER", "USER", "GROUP"]))
        for a in apps:
            r = rng.random()
            if r < 0.6:
                b.gate(u, a)
            elif r < 0.7:
                b.grant(u, a)
    for p in rng.sample(pool, 2):
        b.gate(p, rng.choice(apps))
    src = b.principal("src", org_member=False)
    b.member(src, rng.choice(groups))
    if rng.random() < 0.5:
        b.link(users[0], src, rng.choice(apps))
    grantees = [*users, *pool, src, ORG_ID]
    content: list[str] = []
    records: list[str] = []
    for a in apps:
        tops = []
        for i in range(rng.randint(1, 3)):
            g = _group(b, f"{a}-s{i}", a, rng.choice(RULES), rng, 0.2, 0.05)
            b.parent(a, g)
            b.belong(g, a)
            if rng.random() < 0.6:
                b.inherit(g, a)
            tops.append(g)
            content.append(g)
        nodes = list(tops)
        for j in range(rng.randint(3, 14)):
            parent = rng.choice(nodes)
            is_group = rng.random() < 0.2
            if is_group:
                n = _group(b, f"{a}-g{j}", a, rng.choice(RULES), rng, 0.15, 0.06)
            else:
                n = b.node(Node(
                    f"{a}-r{j}", "record", a, rng.choice(RULES),
                    origin=rng.choice(["CONNECTOR", "CONNECTOR", "UPLOAD"]),
                    deleted=rng.random() < 0.06, placeholder=rng.random() < 0.04,
                    vrid=_shared_vrid(rng, records), indexing=rng.choice(INDEXING)))
                records.append(n)
            b.parent(parent, n, rng.choices(["PARENT_CHILD", "ATTACHMENT", "LINKED_TO"], [8, 1, 1])[0])
            if rng.random() < 0.65:
                b.inherit(n, parent if rng.random() < 0.9 else rng.choice(nodes))
            if not is_group:
                b.belong(n, rng.choice([x for x in nodes if x in tops or "-g" in x]))
                if a == apps[0] and kinds[0][1] in ("APP_LEVEL", "KB") and rng.random() < 0.5:
                    b.belong(n, a)
            if rng.random() < 0.12:  # second parent
                b.parent(rng.choice(nodes), n)
            nodes.append(n)
            content.append(n)
    for n in content:
        for _ in range(rng.choices([0, 1, 2], [5, 3, 1])[0]):
            g = rng.choice(grantees)
            typ = rng.choice(["ORG", "ORGANIZATION", "DOMAIN", "USER"]) if g == ORG_ID else "USER"
            b.grant(g, n, typ)
    for i in range(2):  # unattached uploads
        f = b.node(Node(f"up{i}", "record", "no-app", "OPEN", origin="UPLOAD"))
        g = rng.choice(grantees)
        b.grant(g, f, rng.choice(["ORGANIZATION", "DOMAIN"]) if g == ORG_ID else "USER")
    return b.build()
