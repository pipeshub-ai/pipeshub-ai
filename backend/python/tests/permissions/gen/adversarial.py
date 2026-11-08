"""Adversarial random worlds, aimed at the places the rule is easiest to get wrong.

Deep chains that cross the 50-hop cap (45-58 hops), second parents that give a
deep node both a short and a long path, hierarchy cycles, many groups per
viewer and grants to many groups, mixed access rules (an unknown one included)
along the chains, seeds below seeds, link shares that must grant nothing,
linked accounts, deleted and placeholder intermediates, record-group-level
groups nested under each other, org grants on containers, non-member viewers
and an intermediate node of another org.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model import ORG_ID, Node, World, WorldBuilder

if TYPE_CHECKING:
    import random

RULES = ["OPEN", "OPEN", "STRICT", "STRICT", "RESTRICTED", None, "INHERITED"]
RGL = "RECORD_GROUP_LEVEL"
FOREIGN = "other"


class _Adversary:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.b = WorldBuilder()
        self.content: list[str] = []
        self.records: list[str] = []

    def node(self, app: str, name: str, is_group: bool, parent: str, chain: bool = False) -> str:
        rng = self.rng
        deleted = rng.random() < (0.03 if chain else 0.06)
        rule = rng.choice(RULES)
        if is_group:
            n = self.b.node(Node(name, "group", app, rule, model=RGL if rng.random() < 0.2 else None, deleted=deleted))
        else:
            n = self.b.node(Node(
                name, "record", app, rule, origin=rng.choice(["CONNECTOR"] * 4 + ["UPLOAD"]),
                deleted=deleted, placeholder=rng.random() < 0.04,
                vrid=f"v-{rng.choice(self.records)}" if self.records and rng.random() < 0.2 else None,
                indexing=rng.choice(["COMPLETED"] * 3 + ["QUEUED", "FAILED"])))
            self.records.append(n)
        rel = rng.choices(["PARENT_CHILD", "ATTACHMENT", "LINKED_TO"], [12, 1, 0 if chain else 1])[0]
        self.b.parent(parent, n, rel)
        if rng.random() < (0.85 if chain else 0.6):
            self.b.inherit(n, parent if rng.random() < 0.92 else rng.choice(self.content or [parent]))
        return n


def adversarial_world(rng: random.Random) -> World:
    adv = _Adversary(rng)
    b = adv.b
    apps = [
        b.node(Node("A", "app", app_type="CONFLUENCE")),
        b.node(Node("B", "app", app_type="DRIVE")),
        b.node(Node("C", "app", app_type=rng.choice(["DRIVE", "KB"]),
                    model="APP_LEVEL" if rng.random() < 0.3 else None)),
    ]
    foreign = b.node(Node("foreign", "group", "A", "OPEN", org=FOREIGN))
    groups = [b.principal(f"g{i}", "group") for i in range(30)]
    for i in range(1, 8):
        b.member(groups[i], groups[i - 1])  # nesting the rule must ignore
    pool = [*groups, b.principal("r0", "role"), b.principal("r1", "role"),
            b.principal("t0", "team"), b.principal("t1", "team")]
    users = [b.principal(f"u{i}", org_member=rng.random() < 0.85) for i in range(8)]
    for u in users:
        for p in rng.sample(pool, rng.randint(10, 25)):
            b.member(u, p, kind=rng.choice(["USER"] * 6 + ["GROUP"]))
        for a in apps:
            r = rng.random()
            if r < 0.7:
                b.gate(u, a)
            elif r < 0.8:
                b.grant(u, a)
    for p in rng.sample(pool, 4):
        b.gate(p, rng.choice(apps))
    sources = [b.principal(f"src{i}", org_member=False) for i in range(3)]
    for s in sources:
        for g in rng.sample(groups, 3):
            b.member(s, g)
    for u in rng.sample(users, 3):
        b.link(u, rng.choice(sources), rng.choice(apps))
    grantees = [*users, *pool, *sources, ORG_ID]
    for a in apps:
        _add_app_content(adv, a, foreign)
    for n in adv.content:
        for _ in range(rng.choices([0, 1, 2, 4], [5, 3, 1, 1])[0]):
            g = rng.choice(grantees)
            typ = rng.choice(["ORG", "ORGANIZATION", "DOMAIN", "ANYONE", "USER"]) if g == ORG_ID \
                else rng.choice(["USER"] * 8 + ["DOMAIN", "ANYONE"])
            b.grant(g, n, typ)
    for i in range(3):
        f = b.node(Node(f"up{i}", "record", "no-app", "OPEN", origin="UPLOAD"))
        g = rng.choice(grantees)
        b.grant(g, f, rng.choice(["ORGANIZATION", "DOMAIN"]) if g == ORG_ID else "USER")
    return b.build()


def _add_app_content(adv: _Adversary, a: str, foreign: str) -> None:
    rng, b = adv.rng, adv.b
    tops = []
    for i in range(rng.randint(1, 3)):
        g = b.node(Node(f"{a}-s{i}", "group", a, rng.choice(RULES), model=RGL if rng.random() < 0.2 else None))
        b.parent(a, g)
        b.belong(g, a)
        if rng.random() < 0.7:
            b.inherit(g, a)
        tops.append(g)
        adv.content.append(g)
    groups_a = list(tops)
    prev = rng.choice(tops)
    chain: list[str] = []
    for j in range(rng.randint(45, 58)):  # one deep chain crossing the cap
        is_group = rng.random() < 0.25
        n = adv.node(a, f"{a}-c{j}", is_group, prev, chain=True)
        if is_group:
            groups_a.append(n)
        else:
            b.belong(n, rng.choice(groups_a))
        chain.append(n)
        adv.content.append(n)
        prev = n
    if a == "A" and rng.random() < 0.5:  # a path through a node of another org
        b.parent(foreign, rng.choice(chain))
        b.parent(tops[0], foreign)
    for _ in range(rng.randint(1, 3)):  # short-cuts, so a deep node has a short path too
        lo = rng.randrange(0, max(1, len(chain) // 3))
        hi = rng.randrange(len(chain) // 2, len(chain))
        b.parent(chain[lo], chain[hi])
        if rng.random() < 0.5:
            b.inherit(chain[hi], chain[lo])
    if rng.random() < 0.4:  # a back edge: a cycle
        hi = rng.randrange(len(chain) // 2, len(chain))
        b.parent(chain[hi], chain[rng.randrange(0, hi)])
    nodes = tops + chain
    for j in range(rng.randint(8, 20)):  # the bushy part
        is_group = rng.random() < 0.2
        n = adv.node(a, f"{a}-b{j}", is_group, rng.choice(nodes))
        if is_group:
            groups_a.append(n)
        else:
            b.belong(n, rng.choice(groups_a))
            if a == "C" and rng.random() < 0.5:
                b.belong(n, a)
        if rng.random() < 0.15:
            b.parent(rng.choice(nodes), n)
        nodes.append(n)
        adv.content.append(n)
