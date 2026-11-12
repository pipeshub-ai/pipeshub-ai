"""A reference scope filter over `ViewerScopes`, run against the oracle on the container-stamped worlds.

This is the contract a store-side filter has to meet, written as plain Python so a store translation can be
checked against it: for a record that carries no scope fields of its own (stamp `container`) the filter is
`recordGroupIds | rootRecordGroupIds` meets `readable_groups`, or `connectorIds` meets `open_apps` (the gate,
`gated_apps`, is never matched against `connectorIds`); for a record stamped `intersection` it is `id in
readable_scopes`, else

    path & (readable groups | readable scopes | open apps)  and  not path & (unreadable restricted | pending)

where `path` is the record and every hierarchy ancestor. A record stamped `union` is matched on its inheritance chain
(the climb stops above a parent the node does not inherit from) and is hidden by an unreadable restricted scope on it
(`unreadable_restricted` keeps the scopes restricted by rule or by deletion in every app). The filter never admits what
the oracle denies (no over-admit) and never denies what the oracle admits (no recall loss) on these worlds.

Not exact, and recorded as constraints on whatever stamps records: an Intersection record below a seed (R10), and an
Intersection record with a record-group-level group on its path or in its app (`rules.assert_may_stamp` refuses
both). The generated-world test pins that every inexact pair is on such a record.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from app.services.permissions.rules import (
    AncestorFact,
    Rule,
    RuleNotAllowed,
    assert_may_stamp,
    below_seed,
    connector_is_exact,
    rule_for,
)

from . import gen
from .runner import OracleLayer
from .scenario import load_all
from .scope_layer import OracleScopeLayer

if TYPE_CHECKING:
    from app.services.permissions.scopes import ViewerScopes

    from .model import Node, World

SCENARIOS = [s for s in load_all() if any(n.stamp for n in s.world.nodes)]
WORLDS = {s.id: s.final_world() for s in SCENARIOS}


def path_of(world: World, node_id: str, depth: int = 50) -> set[str]:
    """The node and every hierarchy ancestor, up to the depth cap."""
    seen, frontier = {node_id}, {node_id}
    for _ in range(depth):
        frontier = {p.id for n in frontier for p in world.node(n).parents if p.rel in ("PARENT_CHILD", "ATTACHMENT")} - seen
        if not frontier:
            break
        seen |= frontier
    return seen


def store_fields(world: World, node_id: str) -> dict[str, set[str]]:
    """The fields a vector point of this record carries: the app, the record groups it belongs to, the top of each
    group's chain (never `belongs[0]`: no store has "the first edge"), and the path."""
    node = world.node(node_id)
    groups = {b for b in node.belongs if world.node(b).kind == "group"}
    roots = set()
    for start in groups:
        top, seen = start, {start}
        while True:
            up = [b for b in world.node(top).belongs if world.node(b).kind == "group" and b not in seen]
            if not up:
                break
            top = up[0]
            seen.add(top)
        roots.add(top)
    return {"connectorIds": {node.connector} if node.connector else set(), "recordGroupIds": groups,
            "rootRecordGroupIds": roots, "pathIds": path_of(world, node_id)}


def inherit_chain(world: World, node_id: str, depth: int = 50) -> set[str]:
    """The node and the ancestors whose access flows down to it: climb only through parents the node inherits.
    This is `pathIds` for the Union rule."""
    seen, frontier = {node_id}, {node_id}
    for _ in range(depth):
        frontier = {p.id for n in frontier for p in world.node(n).parents
                    if p.rel in ("PARENT_CHILD", "ATTACHMENT") and p.id in world.node(n).inherits} - seen
        if not frontier:
            break
        seen |= frontier
    return seen


def reference_filter(world: World, scopes: ViewerScopes, node_id: str) -> bool | None:
    """The filter's verdict, or None for a node this reference does not cover: only records are points of the
    index (a group or an app is a container), and only the stamps below."""
    node = world.node(node_id)
    if node.kind != "record" or node.deleted or node.placeholder:
        return None
    f = store_fields(world, node_id)
    if node.stamp == "container":
        return bool((f["recordGroupIds"] | f["rootRecordGroupIds"]) & scopes.readable_groups
                    or f["connectorIds"] & scopes.open_apps)
    if node.stamp in ("intersection", "union"):
        if node_id in scopes.readable_scopes:  # the graph check already said yes (a seed below a restricted page)
            return True
        path = f["pathIds"] if node.stamp == "intersection" else inherit_chain(world, node_id)
        return bool(path & (scopes.readable_groups | scopes.readable_scopes | scopes.open_apps)) \
            and not path & (scopes.unreadable_restricted | scopes.pending)
    return None


class WorldFacts:
    """Viewer-independent facts about a world's nodes, computed once (a stamper has them from the graph)."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.paths = {n.id: path_of(world, n.id) for n in world.nodes}
        self.granted = {g.node for g in world.grants}
        self.restricted = {n.id for n in world.nodes if self._restricted(n)}
        self.rgl_apps = {n.connector for n in world.nodes if n.kind == "group" and n.model == "RECORD_GROUP_LEVEL"}
        self.rgl_groups = {n.id for n in world.nodes if n.kind == "group" and n.model == "RECORD_GROUP_LEVEL"}

    @staticmethod
    def _restricted(n: Node) -> bool:
        """Restricted as the hierarchy views define it: deleted, a rule other than OPEN/STRICT, or a hierarchy parent it
        does not inherit from."""
        parents = [p.id for p in n.parents if p.rel in ("PARENT_CHILD", "ATTACHMENT")]
        return n.deleted or n.rule not in (None, "OPEN", "STRICT") or any(p not in n.inherits for p in parents)

    def multi_parent(self, node_id: str) -> bool:
        return any(sum(1 for q in self.world.node(x).parents if q.rel in ("PARENT_CHILD", "ATTACHMENT")) > 1
                   for x in self.paths[node_id])

    def ancestors(self, node_id: str) -> list[AncestorFact]:
        return [AncestorFact(open_rule=self.world.node(a).rule in (None, "OPEN"), directly_granted=a in self.granted,
                             restricted_above=bool((self.paths[a] - {a}) & self.restricted))
                for a in self.paths[node_id] - {node_id}]


_FACTS: dict[int, WorldFacts] = {}


def facts_of(world: World) -> WorldFacts:
    if id(world) not in _FACTS or _FACTS[id(world)].world is not world:
        _FACTS[id(world)] = WorldFacts(world)
    return _FACTS[id(world)]


def multi_parent(world: World, node_id: str) -> bool:
    return facts_of(world).multi_parent(node_id)


def refused(world: World, node_id: str) -> bool:
    """Whether `assert_may_stamp` refuses the record's exception stamp (Intersection or Union), from facts a stamper can
    compute: record-group-level groups in the app or on the path, several parents on the path, a seed above it."""
    f = facts_of(world)
    node = world.node(node_id)
    flags = {"record_group_level_on_path": node.connector in f.rgl_apps or bool(f.paths[node_id] & f.rgl_groups),
             "multi_parent_on_path": f.multi_parent(node_id),
             "below_seed_on_path": below_seed(f.ancestors(node_id)),
             "record_is_open": node.rule in (None, "OPEN")}
    try:
        if node.stamp == "union":
            assert_may_stamp(Rule.UNION, "DRIVE WORKSPACE", **flags)  # the test worlds type Union apps loosely
        else:
            assert_may_stamp(Rule.INTERSECTION, world.node(node.connector).app_type, **flags)
    except RuleNotAllowed:
        return True
    return False


def derive_stamps(world: World) -> World:
    """What a projector would stamp, from the oracle: `container` when a record is readable by exactly the viewers who
    read its record group, else the connector's rule from the registry. For worlds written without stamps."""
    oracle = OracleLayer()
    nodes = [n.id for n in world.nodes]
    admits = {v: oracle.explain(world, v, nodes) for v in world.users()}
    out = world
    for n in world.nodes:
        if n.kind == "record" and n.belongs and not n.deleted and not n.placeholder and n.connector:
            same = all(admits[v][n.id].admitted == admits[v][n.belongs[0]].admitted for v in world.users())
            # An exception record follows the registry's Intersection, and the Union reading for every other app (the
            # test worlds type Union apps loosely).
            stamp = "container" if same else "intersection" if rule_for(world.node(n.connector).app_type) is Rule.INTERSECTION else "union"
            out = out.with_node(replace(n, stamp=stamp))
    return out


def as_confluence(world: World) -> World:
    """A generated world with every app but KB and APP_LEVEL ones typed as an Intersection-rule connector."""
    for n in world.nodes:
        if n.kind == "app" and n.app_type != "KB" and n.model != "APP_LEVEL":
            world = world.with_node(replace(n, app_type="CONFLUENCE"))
    return world


UNSTAMPED = {s.id: w for s in load_all() if s.id.startswith("SCOPES-ADV-") for w in [s.final_world()]
             if not any(n.stamp for n in s.world.nodes)}
# A record below a seed (R10) is readable without its restricted ancestors; `assert_may_stamp` refuses Intersection
# below a seed (and Union for a non-OPEN record), so the reference does not cover it.
BELOW_SEED = "R10:below-seed"


async def mismatches(world: World, *, skip_refused: bool) -> list[tuple[str, str, str, bool, str]]:
    """(viewer, node, stamp, filter admits, oracle reason) for every pair the filter gets wrong."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    nodes = [n.id for n in world.nodes]
    bad = []
    for viewer in world.users():
        scopes = await layer.viewer_scopes(world, viewer)
        truth = oracle.explain(world, viewer, nodes)
        for node in nodes:
            got = reference_filter(world, scopes, node)
            if got is None or world.node(node).stamp != "intersection":
                continue
            if refused(world, node) != (not skip_refused):
                continue
            if got != truth[node].admitted:
                bad.append((viewer, node, world.node(node).stamp or "", got, truth[node].reason))
    return bad


async def check(scenario_id: str, world: World, *, derived: bool) -> int:
    covered = 0
    layer, oracle = OracleScopeLayer(), OracleLayer()
    nodes = [n.id for n in world.nodes]
    for viewer in world.users():
        scopes = await layer.viewer_scopes(world, viewer)
        truth = oracle.explain(world, viewer, nodes)
        for node in nodes:
            got = reference_filter(world, scopes, node)
            if got is None or (derived and world.node(node).stamp in ("intersection", "union") and refused(world, node)):
                continue
            covered += 1
            assert got == truth[node].admitted, (
                f"{scenario_id}: {viewer} -> {node}: filter {'admits' if got else 'denies'}, "
                f"oracle {'admits' if truth[node].admitted else 'denies'} ({truth[node].reason})")
    return covered


@pytest.mark.parametrize("scenario_id", sorted(WORLDS))
async def test_the_reference_filter_equals_the_oracle_on_stamped_worlds(scenario_id: str) -> None:
    assert await check(scenario_id, WORLDS[scenario_id], derived=False)


@pytest.mark.parametrize("scenario_id", sorted(UNSTAMPED))
async def test_the_reference_filter_equals_the_oracle_on_derived_stamps(scenario_id: str) -> None:
    """Container, Intersection and Union rows over the adversarial worlds, including the mixed-rule world and the Drive
    limited-access folder (a rule-restricted scope in a Union app is kept in the exclusion set)."""
    assert await check(scenario_id, derive_stamps(UNSTAMPED[scenario_id]), derived=True)


GENERATED_SEED, GENERATED_WORLDS = 77, 100


def generated() -> list[World]:
    return [as_confluence(w) for kind in sorted(gen.GENERATORS) for w in gen.generate(kind, GENERATED_SEED, GENERATED_WORLDS)]


@pytest.mark.timeout(600)
async def test_generated_worlds_are_exact_wherever_the_stamper_may_stamp_intersection() -> None:
    """The latent inexactness cannot spread: every pair the Intersection row gets wrong is on a record that
    `assert_may_stamp` refuses (record-group-level group on its path or in its app, or several parents on its path)."""
    bad = []
    for world in generated():
        bad += await mismatches(derive_stamps(world), skip_refused=True)
    assert bad == [], bad[:5]


@pytest.mark.timeout(600)
async def test_generated_intersection_beside_record_group_level_groups_never_over_admits() -> None:
    """The path filter is not exact for an Intersection app with record-group-level groups, whether or not a stamper
    refuses the record: the resolver leaves such an app out of the scopes (`legacy_apps`), so the filter admits none of
    its records. Under-admits there are the price; an over-admit is never allowed."""
    over, left_out = [], 0
    for world in generated():
        stamped = derive_stamps(world)
        over += [m for m in await mismatches(stamped, skip_refused=False) if m[3]]
        for v in world.users():
            left_out += bool((await OracleScopeLayer().viewer_scopes(world, v)).legacy_apps)
    assert over == [], over[:5]
    assert left_out, "the generated worlds must reach the case"


async def test_an_app_the_path_filter_cannot_describe_is_named_and_left_out() -> None:
    for world in generated():
        for viewer in world.users():
            scopes = await OracleScopeLayer().viewer_scopes(world, viewer)
            if scopes.legacy_apps:
                described = scopes.open_apps | scopes.readable_groups | scopes.readable_scopes | scopes.unreadable_restricted
                assert not scopes.legacy_apps & described
                assert not scopes.legacy_apps & scopes.gated_apps
                return
    raise AssertionError("no generated world has a record-group-level group in an Intersection app")


async def test_a_seed_below_a_restricted_page_is_admitted_by_its_own_readability() -> None:
    """The R9 seed: an attachment granted to the viewer directly, under a restricted page the viewer cannot read.
    The path rule alone denies it (the page is in the exclusion set); `id in readable_scopes` admits it first."""
    world = derive_stamps(UNSTAMPED["SCOPES-ADV-02"])
    scopes = await OracleScopeLayer().viewer_scopes(world, "dee")
    assert "secret" in scopes.unreadable_restricted and "shared" in scopes.readable_scopes
    assert reference_filter(world, scopes, "shared") is True
    # the descendant of the seed (R10) is the documented exception
    truth = OracleLayer().explain(world, "dee", ["below"])["below"]
    assert truth.reason == BELOW_SEED and truth.admitted
    assert reference_filter(world, scopes, "below") is False


async def test_the_union_row_over_admits_without_the_rule_restricted_scopes() -> None:
    """The control for R2-1: with nothing kept in the exclusion set for a Union app (the rule-restricted and deleted
    scopes dropped with the merely non-inheriting ones), a record under such a scope is admitted to people who may not
    read it."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    over = 0
    for sid in ("SCOPES-ADV-04", "SCOPES-ADV-06"):
        world = derive_stamps(UNSTAMPED[sid])
        for viewer in world.users():
            scopes = await layer.viewer_scopes(world, viewer)
            thin = replace(scopes, unreadable_restricted=frozenset())
            truth = oracle.explain(world, viewer, [n.id for n in world.nodes])
            over += sum(1 for n in world.nodes if world.node(n.id).stamp == "union"
                        and reference_filter(world, thin, n.id) and not truth[n.id].admitted)
    assert over >= 1


def test_the_stamper_assertion_follows_the_registry() -> None:
    assert_may_stamp(Rule.INTERSECTION, "CONFLUENCE DATA CENTER")
    assert_may_stamp(Rule.UNION, "DRIVE WORKSPACE")
    assert_may_stamp(Rule.CONTAINER, "SLACK", equals_record_group=True)
    for item_acl in ("BOX", "DROPBOX", "SALESFORCE", "SERVICENOW", "ZENDESK", "ZAMMAD", "ZOOM", "DRUPAL WIKI", "BOOKSTACK",
                     "DEMO"):
        assert_may_stamp(Rule.OVERRIDE, item_acl)  # item-level ACL connectors are Override, not Container-only
    for rule, connector, on_path in ((Rule.INTERSECTION, "NOTION", False), (Rule.UNION, "JIRA", False),
                                     (Rule.INTERSECTION, "CONFLUENCE", True), (Rule.OVERRIDE, "SLACK", False),
                                     (Rule.UNION, "SHAREPOINT", False)):
        with pytest.raises(RuleNotAllowed):
            assert_may_stamp(rule, connector, record_group_level_on_path=on_path)


async def test_matching_the_gate_against_connector_ids_would_over_admit() -> None:
    """The control for the container row: `connectorIds` against `gated_apps` (the gate, not "may read the whole app")
    admits records the oracle denies in the container-stamped worlds. The real row uses `open_apps`."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    over = pairs = 0
    for world in WORLDS.values():
        nodes = [n.id for n in world.nodes]
        for viewer in world.users():
            scopes = await layer.viewer_scopes(world, viewer)
            truth = oracle.explain(world, viewer, nodes)
            for node in nodes:
                if reference_filter(world, scopes, node) is None or world.node(node).stamp != "container":
                    continue
                pairs += 1
                wrong = bool(store_fields(world, node)["connectorIds"] & scopes.gated_apps)
                over += wrong and not truth[node].admitted
    assert pairs and over > 50, (pairs, over)


def refused_records(worlds: list[World]) -> list[tuple[World, str]]:
    return [(w, n.id) for w in worlds for n in w.nodes
            if n.kind == "record" and n.stamp in ("intersection", "union") and not n.deleted and not n.placeholder
            and n.connector and refused(w, n.id)]


@pytest.mark.timeout(600)
async def test_stamping_a_refused_record_container_over_admits() -> None:
    """The fallback for a refused record is the legacy filter, never Container: its access differs from its record
    group's, so a Container stamp admits it through the group (QA measured 8,097 and 6,963 on these worlds)."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    over = {"record-group-level": 0, "multi-parent": 0}
    for world in (derive_stamps(w) for w in generated()):
        for w, node_id in refused_records([world]):
            kind = "multi-parent" if multi_parent(w, node_id) else "record-group-level"
            container = replace(w.node(node_id), stamp="container")
            forced = w.with_node(container)
            for viewer in w.users():
                scopes = await layer.viewer_scopes(forced, viewer)
                if reference_filter(forced, scopes, node_id) and not oracle.explain(forced, viewer, [node_id])[node_id].admitted:
                    over[kind] += 1
    assert over["record-group-level"] > 1000 and over["multi-parent"] > 1000, over


def test_a_container_stamp_needs_the_record_to_equal_its_record_group() -> None:
    assert_may_stamp(Rule.CONTAINER, "SLACK", equals_record_group=True)
    for connector in ("SLACK", "CONFLUENCE"):
        with pytest.raises(RuleNotAllowed):
            assert_may_stamp(Rule.CONTAINER, connector)  # the stamper has not shown equality
        with pytest.raises(RuleNotAllowed):
            assert_may_stamp(Rule.CONTAINER, connector, equals_record_group=False)


def test_a_connector_with_a_refused_record_is_not_exact() -> None:
    assert connector_is_exact(0)
    assert not connector_is_exact(1)


def test_a_non_open_record_below_a_seed_is_refused_for_union_and_open_ones_are_not() -> None:
    assert_may_stamp(Rule.UNION, "DRIVE WORKSPACE", below_seed_on_path=True, record_is_open=True)
    with pytest.raises(RuleNotAllowed):
        assert_may_stamp(Rule.UNION, "DRIVE WORKSPACE", below_seed_on_path=True, record_is_open=False)
    with pytest.raises(RuleNotAllowed):
        assert_may_stamp(Rule.INTERSECTION, "CONFLUENCE", below_seed_on_path=True)


async def test_the_strict_child_of_a_granted_seed_is_the_refused_union_case() -> None:
    """SCOPES-ADV-09 `sk2`: a STRICT child of a directly granted seed under a limited folder is admitted by the Union
    row and denied by the oracle (R10 carries only OPEN nodes). The stamper's predicate refuses it."""
    world = derive_stamps(UNSTAMPED["SCOPES-ADV-09"])
    stamped = [n for n in world.nodes if n.stamp == "union" and n.rule == "STRICT"]
    assert stamped and all(refused(world, n.id) for n in stamped if below_seed(facts_of(world).ancestors(n.id)))
    assert any(refused(world, n.id) for n in stamped)


async def test_the_stamped_corpus_has_no_refused_record() -> None:
    assert refused_records(list(WORLDS.values())) == []
