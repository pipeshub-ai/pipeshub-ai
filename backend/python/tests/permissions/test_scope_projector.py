"""The projector against the corpus: its fields, fed to the reference filter, equal the oracle on every non-refused record.

The refusal set equals the one the viewer-scope tests derive from the same facts (`refused`), a container stamp is never given to
a record the oracle says departs from its group, and the exception ids equal the reference path/chain.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import replace
from typing import Never

import pytest

from app.services.permissions.projector import Projection, ScopeProjector
from app.services.permissions.rules import Rule, RuleNotAllowed, connector_is_exact
from app.services.permissions.scope_fields import ScopeFields, Stamp, combine

from . import gen
from .runner import OracleLayer
from .scenario import load_all
from .scope_layer import OracleScopeLayer
from .stamp_world import ORG, stamp_graph
from .test_scopes_reference_filter import (
    UNSTAMPED,
    WORLDS,
    as_confluence,
    derive_stamps,
    inherit_chain,
    path_of,
    reference_filter,
    store_fields,
)

CASES = {**{f"stamped:{k}": v for k, v in WORLDS.items()}, **{f"derived:{k}": derive_stamps(v) for k, v in UNSTAMPED.items()}}


async def project(world) -> dict[str, ScopeFields]:
    graph = stamp_graph(world)
    projector = ScopeProjector(graph)
    out: dict[str, Projection] = {}
    for app in (n for n in world.nodes if n.kind == "app"):
        recs = [n.id for n in world.nodes if n.kind == "record" and n.connector == app.id]
        out.update(await projector.project(ORG, app.id, recs))
    return out


def as_stamped(world, projected) -> object:
    """The world with the stamps the projector chose, so the reference filter reads them."""
    for rid, p in projected.items():
        if p.skipped:
            continue
        world = world.with_node(replace(world.node(rid), stamp=None if p.refused else p.fields.stamp.value))
    return world


@pytest.mark.parametrize("name", sorted(CASES))
async def test_projected_fields_equal_the_oracle(name: str) -> None:
    world = CASES[name]
    projected = await project(world)
    stamped = as_stamped(world, projected)
    layer, oracle = OracleScopeLayer(), OracleLayer()
    nodes = [n.id for n in world.nodes]
    checked = 0
    for rid, p in projected.items():
        node = world.node(rid)
        if p.skipped:
            continue
        if p.refused:  # legacy only: no exact stamp, never container
            assert p.fields.stamp is Stamp.LEGACY and not p.fields.path_ids
            continue
        if p.fields.stamp is Stamp.CONTAINER and node.stamp in ("union", "intersection", "override"):
            raise AssertionError(f"{name}: {rid} is container but the corpus stamps it {node.stamp}")
        if p.fields.stamp is Stamp.INTERSECTION:
            assert set(p.fields.path_ids) == path_of(world, rid), (name, rid)
        if p.fields.stamp is Stamp.UNION:
            assert set(p.fields.path_ids) == inherit_chain(world, rid), (name, rid)
    for viewer in world.users():
        scopes = await layer.viewer_scopes(stamped, viewer)
        truth = oracle.explain(stamped, viewer, nodes)
        for rid, p in projected.items():
            if p.skipped or p.refused or stamped.node(rid).stamp is None:
                continue
            if world.node(rid).connector in scopes.legacy_apps:  # the resolver leaves that app to the legacy filter
                continue
            got = _filter(stamped, scopes, rid, p.fields)
            if got is None:
                continue
            checked += 1
            assert got == truth[rid].admitted, f"{name}: {viewer} -> {rid} ({p.fields.stamp.value}): filter {got}, oracle {truth[rid].admitted} ({truth[rid].reason})"
    assert checked or not projected


def _filter(world, scopes, rid, fields) -> bool | None:
    return reference_filter(world, scopes, rid)


def override_filter(world, scopes, rid, fields) -> bool:
    """Override: the record is read when its `aclScope` is (the graph check already said yes for a seed first)."""
    if rid in scopes.readable_scopes:
        return True
    return bool(set(fields.acl_scope) & (scopes.readable_scopes | scopes.readable_groups | scopes.open_apps))


async def test_override_equals_the_oracle_on_the_sharepoint_corpus() -> None:
    """Every scenario that has a SharePoint or OneDrive app: the nearest scope is the `aclScope`, and the filter over it
    equals the oracle. (Generated worlds are not used: they write graphs no Override connector writes.)"""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    pairs = overrides = 0
    for sc in load_all():
        world = sc.final_world()
        if not {n.app_type for n in world.nodes if n.kind == "app"} & {"SHAREPOINT", "ONEDRIVE"}:
            continue
        projected = await project(world)
        nodes = [n.id for n in world.nodes]
        for viewer in world.users():
            scopes = await layer.viewer_scopes(world, viewer)
            truth = oracle.explain(world, viewer, nodes)
            for rid, p in projected.items():
                if p.skipped or p.refused or world.node(rid).connector is None:
                    continue
                if world.node(world.node(rid).connector).app_type not in ("SHAREPOINT", "ONEDRIVE"):
                    continue
                if p.fields.stamp is Stamp.OVERRIDE:
                    overrides += 1
                    got = override_filter(world, scopes, rid, p.fields)
                else:
                    got = reference_filter(replace_stamp(world, rid, "container"), scopes, rid)
                pairs += 1
                assert got == truth[rid].admitted, (sc.id, viewer, rid, p.fields, truth[rid])
    assert pairs > 500 and overrides > 100, (pairs, overrides)


def replace_stamp(world, rid, stamp):  # noqa: ANN201
    return world.with_node(replace(world.node(rid), stamp=stamp))


GENERATED_WORLDS = int(os.environ.get("PERM_GEN_WORLDS", "12"))
GENERATED_SEED = int(os.environ.get("PERM_GEN_SEED", "77"))


async def generated_findings(seed: int, worlds: int) -> tuple[list[tuple], Counter[str], int]:
    """Every (kind, viewer, record, stamp, oracle verdict, filter verdict) where the filter over the projector's stamp differs from the
    oracle, over `worlds` generated worlds of each kind; the stamps given; the records refused."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    stamps: Counter[str] = Counter()
    refused_n, found = 0, []
    for kind in sorted(gen.GENERATORS):
        for generated_world in gen.generate(kind, seed, worlds):
            world = as_confluence(generated_world)
            projected = await project(world)
            stamped = as_stamped(world, projected)
            nodes = [n.id for n in world.nodes]
            for viewer in world.users():
                scopes = await layer.viewer_scopes(stamped, viewer)
                truth = oracle.explain(stamped, viewer, nodes)
                for rid, p in projected.items():
                    if p.skipped:
                        continue
                    if p.refused:
                        assert p.fields.stamp is Stamp.LEGACY
                        continue
                    if world.node(rid).connector in scopes.legacy_apps:  # the resolver leaves that app to the legacy filter
                        continue
                    got = reference_filter(stamped, scopes, rid)
                    if got is None:
                        continue
                    stamps[p.fields.stamp.value] += 1
                    if got != truth[rid].admitted:
                        found.append((kind, viewer, rid, p.fields.stamp, truth[rid], got))
            refused_n += sum(p.refused for p in projected.values())
    return found, stamps, refused_n


@pytest.mark.timeout(900)
async def test_generated_worlds_are_exact_wherever_the_projector_gives_an_exact_stamp() -> None:
    """Container, Intersection and Union stamps the projector chooses (from the graph alone) never differ from the oracle;
    what it cannot stamp it refuses (legacy), and a connector with a refusal is not exact."""
    found, stamps, refused_n = await generated_findings(GENERATED_SEED, GENERATED_WORLDS)
    assert not found, found[:3]
    assert stamps["container"] and stamps["intersection"], stamps
    assert refused_n and not connector_is_exact(refused_n)


async def test_a_refused_record_is_never_stamped_container() -> None:
    projected = await project(CASES["derived:SCOPES-ADV-06"])
    refused_ = [p for p in projected.values() if p.refused]
    assert refused_ and all(p.fields == ScopeFields.legacy() for p in refused_)


def test_a_point_that_stands_for_records_is_exact_only_where_an_or_over_them_is() -> None:
    c, u, o1, o2, leg = (ScopeFields.container(), ScopeFields(Stamp.UNION, ("a",)), ScopeFields(Stamp.OVERRIDE, ("p",), ("x",)),
                         ScopeFields(Stamp.OVERRIDE, ("q",), ("y",)), ScopeFields.legacy())
    assert combine([c, c]) == c
    assert combine([o1, o2]) == ScopeFields(Stamp.OVERRIDE, ("p", "q"), ("x", "y"))
    assert combine([c, u]) == leg and combine([u, u]) == leg  # a united chain would hide what another record admits
    assert combine([c, leg]) == leg and combine([]) == leg and combine([u]) == u
    assert ScopeFields.from_payload(o1.to_payload()) == o1 and ScopeFields.from_payload({}) is None


async def test_every_stamp_goes_through_assert_may_stamp_and_a_refusal_is_never_overridden(monkeypatch) -> None:
    """The stamper constraints of the viewer scopes: `assert_may_stamp` is called before every stamp, and a record it refuses gets no exact stamp."""
    from app.services.permissions import projector as projector_module
    from app.services.permissions.rules import RuleNotAllowed

    world = CASES["derived:SCOPES-ADV-06"]
    calls: list[str] = []
    real = projector_module.assert_may_stamp

    def spy(rule, connector_type, **facts) -> None:
        calls.append(rule.value)
        return real(rule, connector_type, **facts)

    monkeypatch.setattr(projector_module, "assert_may_stamp", spy)
    projected = await project(world)
    live = [p for p in projected.values() if not p.skipped]
    assert len(calls) == len(live) and calls  # one call per record that is stamped or refused

    def refuse_all(rule, connector_type, **facts) -> Never:
        raise RuleNotAllowed("no")

    monkeypatch.setattr(projector_module, "assert_may_stamp", refuse_all)
    refused_all = await project(world)
    assert all(p.refused and p.fields == ScopeFields.legacy() for p in refused_all.values() if not p.skipped)


async def test_a_restricted_space_is_the_container_of_its_plain_pages_and_a_restricted_page_departs_from_it() -> None:
    """The shape of a Confluence space: the space group is RESTRICTED (its readers hold a grant), a plain page inherits it
    (container), a page with a read restriction of its own is an exception (intersection, with its path), and an
    unknown rule on the space is never trusted (legacy: the connector's rule is Intersection, but the facts refuse nothing, so it
    is stamped from its path)."""
    from .model import Node, Parent, WorldBuilder

    b = WorldBuilder()
    b.principal("ann")
    b.node(Node("app", "app", app_type="CONFLUENCE"))
    b.node(Node("space", "group", connector="app", rule="RESTRICTED", parents=(Parent("app"),), inherits=("app",), belongs=("app",)))
    b.node(Node("plain", "record", connector="app", rule="STRICT", parents=(Parent("space"),), inherits=("space",), belongs=("space",)))
    b.node(Node("limited", "record", connector="app", rule="RESTRICTED", parents=(Parent("space"),), inherits=("space",), belongs=("space",)))
    b.node(Node("below", "record", connector="app", rule="STRICT", parents=(Parent("limited"),), inherits=("limited",), belongs=("space",)))
    b.grant("ann", "space")
    b.grant("ann", "limited")
    world = b.build()
    got = await project(world)
    assert got["plain"].fields.stamp is Stamp.CONTAINER
    assert got["limited"].fields.stamp is Stamp.INTERSECTION and set(got["limited"].fields.path_ids) == {"limited", "space", "app"}
    assert got["below"].fields.stamp is Stamp.INTERSECTION and "limited" in got["below"].fields.path_ids
    odd = world.with_node(replace(world.node("space"), rule="INHERITED"))
    assert (await project(odd))["plain"].fields.stamp is Stamp.INTERSECTION  # never container under a rule the check fails closed on


def retyped(world, app_type: str):  # noqa: ANN201
    """Every app but a KB or an APP_LEVEL one typed as this connector."""
    for n in world.nodes:
        if n.kind == "app" and n.app_type != "KB" and n.model != "APP_LEVEL":
            world = world.with_node(replace(n, app_type=app_type))
    return world


def fields_filter(world, scopes, rid: str, f: ScopeFields) -> bool | None:
    """The store filter over the *projected* fields (nothing taken from the world's own paths)."""
    if f.stamp is Stamp.LEGACY:
        return None
    if f.stamp is Stamp.CONTAINER:
        s = store_fields(world, rid)
        return bool((s["recordGroupIds"] | s["rootRecordGroupIds"]) & scopes.readable_groups or s["connectorIds"] & scopes.open_apps)
    if rid in scopes.readable_scopes:
        return True
    reach = scopes.readable_groups | scopes.readable_scopes | scopes.open_apps
    if f.stamp is Stamp.OVERRIDE:
        return bool(set(f.acl_scope) & reach)
    if set(f.acl_scope) & reach:  # the record's own grant, or a seed on its chain
        return True
    path = set(f.path_ids)
    return bool(path & reach) and not path & (scopes.unreadable_restricted | scopes.pending)


FAMILIES = [("CONFLUENCE", Stamp.INTERSECTION), ("DRIVE WORKSPACE", Stamp.UNION), ("SHAREPOINT", Stamp.OVERRIDE), ("SLACK", Stamp.CONTAINER)]


async def never_wider(app_type: str, seed: int, worlds: int) -> Counter[str]:
    """Access rules and record-group-level groups on: the stamps of one rule family, each filtered on the fields the projector wrote, are never
    more permissive than the oracle over `worlds` generated worlds of each kind (a refused record is legacy; an under-admit is recall, not a leak)."""
    layer, oracle = OracleScopeLayer(), OracleLayer()
    seen: Counter[str] = Counter()
    for kind in sorted(gen.GENERATORS):
        for generated_world in gen.generate(kind, seed, worlds):
            world = retyped(generated_world, app_type)
            projected = await project(world)
            nodes = [n.id for n in world.nodes]
            for viewer in world.users():
                scopes = await layer.viewer_scopes(world, viewer)
                truth = oracle.explain(world, viewer, nodes)
                for rid, p in projected.items():
                    if p.skipped:
                        continue
                    got = fields_filter(world, scopes, rid, p.fields)
                    if got is None:
                        continue
                    seen[p.fields.stamp.value] += 1
                    assert not (got and not truth[rid].admitted), (app_type, kind, seed, viewer, rid, p.fields, truth[rid])
    return seen


@pytest.mark.parametrize(("app_type", "stamp"), FAMILIES)
@pytest.mark.timeout(1800)
async def test_every_registered_rule_never_over_admits_on_generated_worlds(app_type: str, stamp: Stamp) -> None:
    seen = await never_wider(app_type, GENERATED_SEED, GENERATED_WORLDS)
    assert seen[stamp.value] or stamp is Stamp.OVERRIDE and seen["container"], seen


# the seeds that once found a stamp wider than the oracle (a route through a group-level group the walk skips; an unknown access rule) and a
# seed of the CI run, at 400 worlds of each kind on every rule family: a flaw that shows in about one world in two thousand needs that many
@pytest.mark.parametrize("seed", [80002, 70095, GENERATED_SEED])
@pytest.mark.parametrize("app_type", [f[0] for f in FAMILIES])
@pytest.mark.timeout(1800)
async def test_a_stamp_is_never_wider_than_the_oracle(app_type: str, seed: int) -> None:
    await never_wider(app_type, seed, 400)


async def test_a_record_filed_under_a_group_its_hierarchy_never_passes_is_not_container() -> None:
    from .model import Node, Parent, WorldBuilder

    b = WorldBuilder()
    b.principal("x")
    b.node(Node("app", "app", app_type="SLACK"))
    b.node(Node("g1", "group", connector="app", rule="RESTRICTED", parents=(Parent("app"),), inherits=("app",), belongs=("app",)))
    b.node(Node("g2", "group", connector="app", rule="OPEN", parents=(Parent("app"),), inherits=("app",), belongs=("app",)))
    b.node(Node("r", "record", connector="app", rule="OPEN", parents=(Parent("g1"),), inherits=("g1",), belongs=("g1", "g2")))
    b.node(Node("ok", "record", connector="app", rule="OPEN", parents=(Parent("g1"),), inherits=("g1",), belongs=("g1",)))
    got = await project(b.build())
    assert got["r"].fields == ScopeFields.legacy() and got["r"].refused  # g2 would admit it; the hierarchy says g1
    assert got["ok"].fields.stamp is Stamp.CONTAINER


async def test_a_record_of_an_open_app_is_container_only_with_the_edge_to_the_app() -> None:
    from .model import Node, WorldBuilder

    b = WorldBuilder()
    b.principal("x")
    b.node(Node("app", "app", app_type="KB", model="APP_LEVEL"))
    b.node(Node("member", "record", connector="app", belongs=("app",)))
    b.node(Node("loose", "record", connector="app"))
    got = await project(b.build())
    assert got["member"].fields.stamp is Stamp.CONTAINER
    assert got["loose"].refused and got["loose"].fields == ScopeFields.legacy()


def test_union_and_override_are_refused_below_a_record_group_level_group_and_when_a_non_open_node_sits_below_a_seed() -> None:
    from app.services.permissions.rules import assert_may_stamp as may

    for rule, connector in ((Rule.UNION, "DRIVE WORKSPACE"), (Rule.OVERRIDE, "SHAREPOINT")):
        may(rule, connector)
        with pytest.raises(RuleNotAllowed):
            may(rule, connector, record_group_level_on_path=True)
        with pytest.raises(RuleNotAllowed):
            may(rule, connector, below_seed_on_path=True, record_is_open=False)
        may(rule, connector, below_seed_on_path=True, record_is_open=True)


async def test_a_non_open_node_between_a_record_and_its_seed_makes_union_and_override_refuse_it() -> None:
    from .model import Grant, Node, Parent, WorldBuilder

    for connector, stamp in (("DRIVE WORKSPACE", Stamp.UNION), ("SHAREPOINT", Stamp.OVERRIDE)):
        b = WorldBuilder()
        b.principal("u")
        b.node(Node("app", "app", app_type=connector))
        b.node(Node("top", "group", connector="app", rule="RESTRICTED", parents=(Parent("app"),), inherits=("app",), belongs=("app",)))
        b.node(Node("seed", "record", connector="app", rule="OPEN", parents=(Parent("top"),), inherits=(), belongs=("top",)))
        b.node(Node("mid", "record", connector="app", rule="STRICT", parents=(Parent("seed"),), inherits=("seed",), belongs=("top",)))
        b.node(Node("leaf", "record", connector="app", rule="OPEN", parents=(Parent("mid"),), inherits=("mid",), belongs=("top",)))
        world = b.build()
        world = replace(world, grants=(Grant("u", "seed"),))
        got = await project(world)
        assert got["leaf"].refused and got["leaf"].fields == ScopeFields.legacy(), (connector, got["leaf"])
        assert got["seed"].fields.stamp in (stamp, Stamp.LEGACY)


async def test_a_parentless_granted_open_node_is_a_seed_and_what_hangs_below_it_is_not_stamped_union() -> None:
    """A STRICT record under a granted OPEN ancestor that reaches nothing: the grant makes that ancestor a seed, but the walk
    does not carry the STRICT record below it, so a path stamp through the seed would admit it to the seed's readers."""
    world = retyped(gen.generate("proptest", 1414213, 200)[78], "DRIVE WORKSPACE")
    layer, oracle = OracleScopeLayer(), OracleLayer()
    projected = await project(world)
    nodes = [n.id for n in world.nodes]
    assert projected["A-r3"].refused
    for viewer in world.users():
        scopes = await layer.viewer_scopes(world, viewer)
        truth = oracle.explain(world, viewer, nodes)
        for rid, p in projected.items():
            if not p.skipped and (got := fields_filter(world, scopes, rid, p.fields)) is not None:
                assert not (got and not truth[rid].admitted), (viewer, rid, p.fields)


async def test_a_record_below_a_readable_seed_is_admitted_whatever_restricts_the_nodes_above_the_seed() -> None:
    """Generated world 116 of the adversarial generator at seed 20261024, typed as a Drive connector: `A-c1` is OPEN under the seed
    `A-c0` (granted, with the restricted `A-s0` above it). Its reader of the seed reads it (R10) although `A-s0` is excluded, so the Union
    stamp carries the seed in `aclScope`."""
    world = retyped(gen.generate("adversarial", 20261024, 200)[116], "DRIVE WORKSPACE")
    projected = await project(world)
    fields = projected["A-c1"].fields
    assert fields.stamp is Stamp.UNION and {"A-c1", "A-c0"} <= set(fields.acl_scope)
    scopes = await OracleScopeLayer().viewer_scopes(world, "u3")
    assert "A-c0" in scopes.readable_scopes and "A-s0" in scopes.unreadable_restricted
    assert fields_filter(world, scopes, "A-c1", fields) is True
    assert OracleLayer().explain(world, "u3", ["A-c1"])["A-c1"].admitted
