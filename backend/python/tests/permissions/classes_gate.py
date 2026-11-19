"""The oracle gate of the access classes: ``app.services.permissions.classes`` over the real ScopeProjector's closure, pair by pair against ``oracle.verdict``.

For every viewer and every record or record group of a world: the oracle's verdict against the class's, where a record the compiler refuses with ``deny`` is denied, a record the
compiler refuses otherwise (or whose closure touches another organisation's node, which cannot exist in kg) is legacy and not compared, and the rest is
``class.admits(L, gate, SELF)`` with L the viewer's grants *as kg materializes them* (leaf records left out: a leaf's grant only ever appears as SELF).

A world is run as written and with every app (except knowledge bases and app-level ones) retyped as a container-only (SLACK), intersection (CONFLUENCE), union (DRIVE WORKSPACE)
and override (BOX) connector: the typing changes what the S3 projector stamps or refuses, and must change nothing here.
"""

from __future__ import annotations

import collections
from dataclasses import replace

from app.services.permissions import classes
from app.services.permissions.projector import ScopeProjector
from tests.permissions import oracle
from tests.permissions.graph import Namespace, compile_world
from tests.permissions.model import Gate, Grant, Node, Parent, Principal, World
from tests.permissions.stamp_world import ORG, stamp_graph

RETYPE = {"as_written": None, "container": "SLACK", "intersection": "CONFLUENCE", "union": "DRIVE WORKSPACE", "override": "BOX"}


def retype(world: World, app_type: str | None) -> World:
    if app_type is None:
        return world
    for n in world.nodes:
        if n.kind == "app" and n.app_type != "KB" and n.model != "APP_LEVEL":
            world = world.with_node(replace(n, app_type=app_type))
    return world


def tainted(i: str, nodes: dict, foreign: set[str]) -> bool:
    """The closure of ``i`` reaches a node of another organisation: kg cannot hold it (RLS), the oracle walks through it."""
    seen, stack = set(), [i]
    while stack:
        x = stack.pop()
        if x in seen or x not in nodes:
            continue
        seen.add(x)
        if x in foreign:
            return True
        stack += [*nodes[x].parents, *nodes[x].groups]
    return False


async def compile_world_classes(world: World, tally: collections.Counter, prefix: str = "o") -> tuple[dict, object, object, object]:
    """``{record id: Klass | Refusal}`` for every record and record group of the world, the way the stamper compiles an app (one compiler per app)."""
    ns = Namespace(prefix)
    graph = compile_world(world, prefix)
    store = oracle.Store(graph)
    sg = stamp_graph(world, prefix + "-")
    proj = ScopeProjector(sg)
    by_app = collections.defaultdict(list)
    for n in world.nodes:
        if n.org is None and n.kind in ("record", "group") and n.connector:
            by_app[prefix + "-" + n.connector].append(prefix + "-" + n.id)
    own = {m.id for m in world.nodes if m.org is None}
    foreign = {prefix + "-" + m.id for m in world.nodes if m.org is None and any(p.id not in own for p in m.parents)}
    outcome: dict = {}
    for app, ids in by_app.items():
        a = sg.apps.get(app)
        closure = await proj._closure(ORG, ids)       # the compiler reads what the projector's level-by-level closure loads: a node it lacks fails closed
        comp = None
        if a:
            shallow = classes.check_shallow(closure) is None
            tally["apps_shallow" if shallow else "apps_per_record_memo"] += 1
            comp = classes.Compiler(closure, app, opens=a.opens, group_level=a.has_group_level, shallow=shallow)
        for i in ids:
            k = comp.compile(i) if comp else classes.Refusal("no_app")
            if not isinstance(k, classes.Refusal) and tainted(i, closure, foreign):
                k = classes.Refusal("foreign_parent")
            outcome[i] = k
            tally["nodes"] += 1
            if isinstance(k, classes.Refusal):
                tally[f"refusal:{k.reason}"] += 1
    return outcome, store, ns, sg


async def check_world(world: World, label: str, tally: collections.Counter, examples: dict, *, leaf_groups: bool = False) -> None:
    outcome, store, ns, sg = await compile_world_classes(world, tally)
    kids = {p for n in sg.node_map.values() for p in n.parents}
    leaf_record = {i for i, n in sg.node_map.items() if (n.kind == "record" or (leaf_groups and n.kind == "group")) and i not in kids}
    for k in outcome.values():
        if not isinstance(k, classes.Refusal) and any(x in leaf_record for cl in k.clauses for x in cl if x != classes.SELF):
            tally["leaf_literal_violation"] += 1          # a non-SELF literal on a leaf record would be missing from L
    for user in world.users():
        v = oracle.viewer(store, ns.id(user), ns.org)
        l_mat = {x for x in v.granted if x not in leaf_record}
        for i, k in outcome.items():
            truth = oracle.verdict(store, v, i)
            if isinstance(k, classes.Refusal):
                if k.reason != "deny":
                    tally["pairs_legacy"] += 1
                    continue
                got = False
            else:
                got = k.admits(l_mat, v.apps, self_granted=i in v.granted)
            tally["pairs"] += 1
            if got and not truth.admitted:
                tally["over"] += 1
                if len(examples["over"]) < 12:
                    examples["over"].append([label, user, i, truth.reason, sorted(map(sorted, k.clauses))])
            elif truth.admitted and not got:
                tally["under"] += 1
                if len(examples["under"]) < 12:
                    examples["under"].append([label, user, i, truth.reason, sorted(map(sorted, k.clauses)) if not isinstance(k, classes.Refusal) else k.reason])


def targeted() -> list[tuple[str, World]]:
    """T1 (a cycle in front of a chain near the 50-hop cap), M8 (a record-group-level channel whose messages only belong to it), the same Slack shape after the channel stops inheriting
    the app (the narrowing: compile-from-scratch verdict), SM1 (a cycle through which a memo that outlives its record would leak SELF)."""
    out = []
    a = Node("A", "app", app_type="DRIVE WORKSPACE")
    nodes = [a, Node("P", "record", "A", parents=(Parent("Z01"), Parent("Q")), inherits=("Z01",)), Node("Q", "record", "A", parents=(Parent("P"), Parent("A")), inherits=("P",))]
    for k in range(1, 41):
        nxt = f"Z{k + 1:02d}" if k < 40 else "A"
        nodes.append(Node(f"Z{k:02d}", "record", "A", parents=(Parent(nxt),), inherits=(nxt,)))
    for k in range(1, 9):
        nxt = f"R{k + 1}" if k < 8 else "Q"
        nodes.append(Node(f"R{k}", "record", "A", parents=(Parent(nxt),), inherits=(nxt,)))
    nodes.append(Node("t", "record", "A", parents=(Parent("R1"), Parent("P")), inherits=("R1",)))
    out.append(("T1", World(principals=(Principal("u"),), nodes=tuple(nodes), grants=(Grant("u", "P"),), gates=(Gate("u", "A"),))))
    s = Node("S", "app", app_type="SLACK")
    ch = Node("ch", "group", "S", parents=(Parent("S"),), inherits=("S",), belongs=("S",), model="RECORD_GROUP_LEVEL")
    msg = Node("msg", "record", "S", belongs=("ch",))
    other = Node("other", "group", "S", parents=(Parent("S"),), belongs=("S",), model="RECORD_GROUP_LEVEL")
    msg2 = Node("msg2", "record", "S", belongs=("other",))
    base = World(principals=(Principal("u"), Principal("w")), nodes=(s, ch, msg, other, msg2), grants=(Grant("u", "other"),), gates=(Gate("u", "S"), Gate("w", "S")))
    out.append(("M8", base))
    b = Node("B", "app", app_type="DRIVE WORKSPACE")
    sm = World(principals=(Principal("u"), Principal("w")), nodes=(
        b, Node("t1", "record", "B", parents=(Parent("B"), Parent("X")), inherits=("X",)),
        Node("X", "record", "B", parents=(Parent("t1"),), inherits=("t1",)),
        Node("t2", "record", "B", rule="STRICT", parents=(Parent("X"),), inherits=("X",))),
        grants=(Grant("u", "t2"), Grant("w", "t1")), gates=(Gate("u", "B"), Gate("w", "B")))
    out.append(("SM1", sm))
    # RGL1: a record t below a granted record s, both members of one record-group-level group g (t2 belongs to another group, t3 breaks inheritance).
    # v holds s and g, so the oracle does not read t2 through s (g admits s by R8) while the monotone clause {s} would: t2 is the one record that must stay refused.
    mk = lambda i, g, par, inh=True, **kw: Node(i, "record", "S", parents=(Parent(par),), inherits=(par,) if inh else (), belongs=(g,), **kw)   # noqa: E731
    grp = lambda i: Node(i, "group", "S", parents=(Parent("S"),), belongs=("S",), model="RECORD_GROUP_LEVEL")   # noqa: E731
    out.append(("RGL1", World(principals=tuple(Principal(x) for x in "uvwx"), nodes=(
        s, grp("g"), grp("g2"), mk("s", "g", "g"), mk("t", "g", "s"), mk("t2", "g2", "s"), mk("t3", "g", "s", inh=False), mk("t4", "g", "t3")),
        grants=(Grant("u", "s"), Grant("v", "s"), Grant("v", "g"), Grant("w", "g2"), Grant("u", "t3")), gates=tuple(Gate(x, "S") for x in "uvwx"))))
    # t reaches the granted record s through a record-group-level group g it is no member of: R7 skips g, so the oracle refuses t and a class that ignores the crossing would admit it.
    out.append(("RGLX", World(principals=(Principal("u"),), nodes=(
        s, Node("s", "record", "S", parents=(Parent("S"),), inherits=("S",)), Node("g", "group", "S", parents=(Parent("s"),), inherits=("s",), belongs=("S",), model="RECORD_GROUP_LEVEL"),
        Node("t", "record", "S", parents=(Parent("g"),), inherits=("g",))),
        grants=(Grant("u", "s"),), gates=(Gate("u", "S"),))))
    # The seed has a group (g2) that t does not share and the viewer holds s and g2: a rule that asks only for some group in common would give t a class the oracle denies.
    out.append(("RGLY", World(principals=(Principal("u"),), nodes=(
        s, grp("g"), grp("g2"), Node("s", "record", "S", parents=(Parent("g"),), inherits=("g",), belongs=("g", "g2")), mk("t", "g", "s")),
        grants=(Grant("u", "s"), Grant("u", "g2")), gates=(Gate("u", "S"),))))
    out.append(("M8-narrowed", base.with_node(Node("ch", "group", "S", parents=(Parent("S"),), belongs=("S",), model="RECORD_GROUP_LEVEL"))))
    return out


async def run(worlds: list[tuple[str, World]], *, typings: tuple[str, ...] = tuple(RETYPE), leaf_groups: bool = False) -> tuple[collections.Counter, dict]:
    tally: collections.Counter = collections.Counter()
    examples: dict = collections.defaultdict(list)
    for wid, w in worlds:
        for variant in typings:
            await check_world(retype(w, RETYPE[variant]), f"{wid}:{variant}", tally, examples, leaf_groups=leaf_groups)
    return tally, examples
