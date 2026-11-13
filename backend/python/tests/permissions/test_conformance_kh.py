"""The Knowledge Hub layer against the oracle, on Neo4j and ArangoDB.

Every golden world and the generated worlds: no row the oracle denies, a total that is the row count, counts by
type that sum the rows, and facet totals that count only what the viewer may read. Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import logging
import warnings
from collections import Counter

import pytest

from . import gen
from .kh_layer import KhFinding, browse_pages, check_world, children_of, refused_by_cap
from .kh_worlds import (
    APPSIDE,
    CAPSTUB,
    CROSSREACH,
    DECLDEEP,
    DECLSTUB,
    DEEP_CHAIN,
    DEEPOPEN,
    DEEPSEED,
    FLATSIDE,
    FOLDERSTUB,
    HIDESEED,
    KEYCLASH,
    KH_WORLDS,
    NESTSTUB,
    OPENGRANT,
    OPENGRANTTEAM,
    OPENGROUP,
    OPENSTUB,
    REACHSTUB,
    SCOPEDSIDE,
    SCOPEDTWO,
    SIDEKID,
    SIDESEED,
    STUBQ,
    STUBSEED,
    TWOWAY,
    XDECL,
    XGRANT,
    XSTUB,
)
from .runner import OracleLayer
from .scenario import Scenario, load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(0)]  # sized by the scenario set and PERM_GEN_WORLDS

CHUNK = 25
# The listing's queries make Neo4j warn about every property a sparse world lacks; pytest keeps each record of a test, and the generated worlds
# run out of memory on them.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
# `gap` is reported, never failed: the shapes the oracle cannot decide (an upload, which has its own surface, and a node under a group that hides its
# children), and a row a browse lists that the oracle refuses only for its hop cap (`refused_by_cap`). A readable node the flatten listing or a browse
# leaves out is `under` or `browse_gap` and fails, however deep it lies. `truncated` counts the rows a viewer's whole-tree browse did not open
# past `MAX_OPENS`.
GAPS = {"gap", "truncated"}


def _describe(found: list[KhFinding]) -> str:
    return "\n".join(f"{f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in found[:40])


def _bad(found: list[KhFinding], known: set[tuple[str, str]] = frozenset()) -> list[KhFinding]:
    return [f for f in found if f.kind not in GAPS and (f.kind != "browse_gap" or (f.scenario, f.detail) not in known)]


def _report(where: str, found: list[KhFinding]) -> None:
    """The findings reported, not failed, counted by class in one warning: pytest lists it in the run's summary."""
    counts = Counter(f.kind for f in found if f.kind in GAPS)
    if counts:
        warnings.warn(f"{where}: reported {dict(sorted(counts.items()))}", stacklevel=2)


async def test_golden_worlds_count_what_the_viewer_may_read(graph_layers) -> None:
    scenarios: list[Scenario] = load_all()
    for layer in graph_layers:
        worlds = [*dict.fromkeys(s.final_world() for s in scenarios), *KH_WORLDS]      # shapes no generator builds: the connector check of every arm, arrows, a multi-page walk
        found: list[KhFinding] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                found += await check_world(layer, world, f"golden-{start + offset}")
            await layer.release(chunk)
        _report(f"{layer.name} golden", found)
        bad = _bad(found)
        assert not bad, f"{layer.name}\n{_describe(bad)}"


async def test_a_stub_on_the_way_to_a_node_the_open_app_reads_is_listed_and_opens(graph_layers) -> None:
    """The conformance check leaves stubs out of the children it expects, so this one asks the browse directly: in an App that opens everything the
    folder lists the stub, with an arrow, and opening the stub lists what it leads to."""
    for layer in graph_layers:
        await layer.preload([OPENSTUB])
        assert await children_of(layer, OPENSTUB, "u", "g", "recordGroup") == {"p": True}, layer.name
        assert await children_of(layer, OPENSTUB, "u", "p", "record") == {"s": True}, layer.name
        await layer.release([OPENSTUB])


async def test_a_stub_is_listed_only_where_a_browse_of_the_stub_itself_lists_something(graph_layers) -> None:
    """A stub's way below it is read as a browse of the stub starts, not as the folder it is listed under: in a declared group that walk starts
    below a seed, so a stub that leads only to a strict node is not listed, and the group shows no arrow."""
    for layer in graph_layers:
        await layer.preload([DECLSTUB])
        try:
            assert await children_of(layer, DECLSTUB, "u", "s", "record") == {}, layer.name
            assert await children_of(layer, DECLSTUB, "u", "d", "recordGroup") == {}, layer.name
            assert await children_of(layer, DECLSTUB, "u", "g", "recordGroup") == {"d": False}, layer.name
            bad = _bad(await check_world(layer, DECLSTUB, "declstub"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([DECLSTUB])


async def test_a_stub_is_listed_where_a_browse_of_the_stub_itself_lists_something(graph_layers) -> None:
    """A stub's reach is every route a browse of it starts from, not the hop from the folder it is listed under: below a folder that does not pass it
    on, a stub the App reaches through another folder is listed, with an arrow, as the way to the strict node a browse of it lists; so is its
    folder's arrow. Whether the folder's rule passes the stub (SCOPEDTWO, by the declared scope) or not (TWOWAY)."""
    for world in (TWOWAY, SCOPEDTWO):
        for layer in graph_layers:
            await layer.preload([world])
            try:
                assert await children_of(layer, world, "u", "s", "record") == {"t": False}, layer.name
                assert await children_of(layer, world, "u", "p", "recordGroup") == {"s": True}, layer.name
                assert (await children_of(layer, world, "u", "A", "app") or {}).get("p") is True, layer.name
                bad = _bad(await check_world(layer, world, "twoway"))
                assert not bad, f"{layer.name}\n{_describe(bad)}"
            finally:
                await layer.release([world])


async def test_a_folder_lists_each_stub_once_over_its_pages(graph_layers) -> None:
    """A stub the folder's rule lists and its reach reaches is one row: once over every page, and counted once in the total."""
    for layer in graph_layers:
        await layer.preload([FOLDERSTUB])
        try:
            items, totals, counted = await browse_pages(layer, FOLDERSTUB, "u", "f", "folder")
            ns = await layer._ns(FOLDERSTUB)
            ids = [ns.local(item.id) for item in items]
            assert sorted(ids) == sorted(["s", *(f"f{i}" for i in range(8))]), layer.name
            assert set(totals) == set(counted) == {9}, (layer.name, totals, counted)
            bad = _bad(await check_world(layer, FOLDERSTUB, "folderstub"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([FOLDERSTUB])


async def test_a_node_the_oracle_admits_past_the_cap_is_listed_under_its_parent(graph_layers) -> None:
    """A seed, and a member of an App that opens everything, are admitted with no hop cap: below depth 50 each link is listed under its parent,
    with an arrow, and the layer never excuses one as the cap's (`refused_by_cap` reads the verdict, not the depth)."""
    for world in (DEEPSEED, DEEPOPEN):
        assert not any(refused_by_cap(OracleLayer(graph_layers[0].shape), world, "u", f"c{i}") for i in range(DEEP_CHAIN))
        for layer in graph_layers:
            await layer.preload([world])
            try:
                for i in range(46, DEEP_CHAIN - 1):
                    assert await children_of(layer, world, "u", f"c{i}", "record") == {f"c{i + 1}": i + 2 < DEEP_CHAIN}, (layer.name, i)
            finally:
                await layer.release([world])


async def test_the_declared_group_arm_lists_only_its_own_connectors_records(graph_layers) -> None:
    """The worlds with a record of another connector inside a declared group (node `m4`): no row the oracle denies, no count that includes one."""
    worlds = [w for w in [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS] if "m4" in {n.id for n in w.nodes}]
    assert worlds
    for layer in graph_layers:
        await layer.preload(worlds)
        bad: list[KhFinding] = []
        for i, world in enumerate(worlds):
            bad += _bad(await check_world(layer, world, f"declared-{i}"))
        await layer.release(worlds)
        assert not bad, f"{layer.name}\n{_describe(bad)}"


async def test_a_grant_to_another_connectors_principal_lists_opens_and_passes_the_record_check(graph_layers) -> None:
    """A record granted only to a role of another connector that the viewer holds: its folder lists it, opening it is admitted, and the record
    check admits it, on both stores; a sibling with no grant is none of these."""
    for layer in graph_layers:
        await layer.preload([XGRANT])
        try:
            ns = await layer._ns(XGRANT)
            assert await children_of(layer, XGRANT, "u", "f", "recordGroup") == {"m": False}, layer.name
            assert await children_of(layer, XGRANT, "u", "m", "record") == {}, layer.name
            assert await children_of(layer, XGRANT, "u", "n", "record") is None or not await children_of(layer, XGRANT, "u", "n", "record")
            assert await layer._provider.check_record_access_with_details(ns.id("u"), ns.org, ns.id("m")), layer.name
            assert await layer._provider.check_record_access_with_details(ns.id("u"), ns.org, ns.id("n")) is None, layer.name
            bad = _bad(await check_world(layer, XGRANT, "xgrant"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([XGRANT])


async def test_a_group_of_another_connector_under_a_declared_group_is_not_listed_there(graph_layers) -> None:
    """The check decides a group in its own App, so a group of App B filed under a declared group of App A is neither listed under it nor gives
    it an arrow: the declared group opens to nothing, and shows no arrow."""
    for layer in graph_layers:
        await layer.preload([XDECL])
        try:
            assert (await children_of(layer, XDECL, "u", "A", "app") or {}).get("d") is False, layer.name
            assert await children_of(layer, XDECL, "u", "d", "recordGroup") == {}, layer.name
            bad = _bad(await check_world(layer, XDECL, "xdecl"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([XDECL])


async def test_review_shapes_list_what_the_oracle_admits(graph_layers) -> None:
    """The listing arms, held to the oracle: an open App reads a record that belongs to it as a member, not a group (OPENGROUP); a declaration the
    walk from the App reaches below a group is a scope (DECLDEEP); a stub of another connector is not listed in this App, nor lights an arrow
    (XSTUB); and the sidebar keeps a record a seed lists when its own reach opens a child (CROSSREACH)."""
    for layer in graph_layers:
        worlds = [OPENGROUP, DECLDEEP, XSTUB, CROSSREACH]
        await layer.preload(worlds)
        try:
            assert await children_of(layer, OPENGROUP, "u", "k", "record") == {"go": False}, layer.name
            assert await children_of(layer, DECLDEEP, "u", "F", "recordGroup") == {"M": False}, layer.name
            assert await children_of(layer, XSTUB, "u", "A", "app") == {"g": True}, layer.name
            assert await children_of(layer, XSTUB, "u", "g", "recordGroup") == {"r": False}, layer.name
            assert await children_of(layer, CROSSREACH, "u", "s", "record", only_containers=True) == {"R": True}, layer.name
            bad = [f for i, world in enumerate(worlds) for f in _bad(await check_world(layer, world, f"review-{i}"))]
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release(worlds)


async def test_a_child_the_check_admits_by_its_own_reach_keeps_its_row_and_its_stub(graph_layers) -> None:
    """A child the rule does not pass under its parent, which the walk from the App reaches by another parent, is listed when the parent is
    opened (the check admits it). So the sidebar keeps the parent row before the page's arrows are settled (SIDEKID, APPSIDE), and a stub whose
    only child it is is listed (STUBQ)."""
    for layer in graph_layers:
        worlds = [SIDEKID, APPSIDE, STUBQ]
        await layer.preload(worlds)
        try:
            assert await children_of(layer, SIDEKID, "u", "f", "recordGroup", only_containers=True) == {"R": True}, layer.name
            assert (await children_of(layer, APPSIDE, "u", "A", "app", only_containers=True) or {}).get("R") is True, layer.name
            assert await children_of(layer, STUBQ, "u", "h", "recordGroup") == {"S": True}, layer.name
            bad = [f for i, world in enumerate(worlds) for f in _bad(await check_world(layer, world, f"ownreach-{i}"))]
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release(worlds)


async def test_a_child_the_check_admits_by_any_arm_keeps_its_row_and_its_stub(graph_layers) -> None:
    """Where a node's rules list nothing under it, opening it lists what the check admits of its frontier: the children its browse asks the check
    about, and those of the stubs it lists on the way down. The sidebar keeps a record whose child the check admits below a seed (SIDESEED), a
    stub is listed over such a child (STUBSEED) and over a stub that holds a child the check admits by another parent (NESTSTUB), and a stub
    whose readable child is past the children its browse asks about is not listed, as opening it lists nothing (CAPSTUB). A stub child the walk
    from the App reaches by another parent counts with what lies below it (REACHSTUB)."""
    for layer in graph_layers:
        worlds = [SIDESEED, STUBSEED, NESTSTUB, CAPSTUB, REACHSTUB]
        await layer.preload(worlds)
        try:
            assert await children_of(layer, SIDESEED, "u", "f", "recordGroup", only_containers=True) == {"R": True}, layer.name
            assert await children_of(layer, STUBSEED, "u", "h", "recordGroup") == {"S": True}, layer.name
            assert await children_of(layer, NESTSTUB, "u", "h", "recordGroup") == {"S": True}, layer.name
            assert await children_of(layer, NESTSTUB, "u", "S", "record") == {"T": True}, layer.name
            assert await children_of(layer, CAPSTUB, "u", "h", "recordGroup") == {}, layer.name
            assert await children_of(layer, CAPSTUB, "u", "S", "record") == {}, layer.name
            assert await children_of(layer, REACHSTUB, "u", "R", "record") == {"S": True}, layer.name
            assert await children_of(layer, REACHSTUB, "u", "f", "recordGroup") == {"R": True}, layer.name
            assert await children_of(layer, REACHSTUB, "u", "f", "recordGroup", only_containers=True) == {"R": True}, layer.name
            bad = [f for i, world in enumerate(worlds) for f in _bad(await check_world(layer, world, f"frontier-{i}"))]
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release(worlds)


async def test_a_child_below_a_group_that_hides_its_children_is_listed_only_where_it_opens(graph_layers) -> None:
    """The check admits C below a seed, but C also hangs under a group that hides its children, and the walk from the App does not reach it: a
    browse of C refuses to open it, so the browse of its other parent R does not list it (nor take a place under the cap for it), R shows no
    arrow, and the seed does not list it either (HIDESEED)."""
    for layer in graph_layers:
        await layer.preload([HIDESEED])
        try:
            assert await layer.admitted(HIDESEED, "u", ["C"]) == {"C"}, layer.name
            assert await children_of(layer, HIDESEED, "u", "C", "record") is None, layer.name
            assert await children_of(layer, HIDESEED, "u", "f", "recordGroup") == {"R": False}, layer.name
            assert await children_of(layer, HIDESEED, "u", "R", "record") == {}, layer.name
            assert await children_of(layer, HIDESEED, "u", "s", "record") == {}, layer.name
            bad = _bad(await check_world(layer, HIDESEED, "hideseed"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([HIDESEED])


async def test_a_flattens_sidebar_keeps_a_record_exactly_where_its_arrow_is_on(graph_layers) -> None:
    """The sidebar of a flatten keeps the rows the flatten shows with children, by the flatten's own arrow: from the App, the stricter rule
    (FLATSIDE: f has no arrow, so the sidebar holds D alone); below a folder, read from the folder's reach (SCOPEDSIDE: R has an arrow, so the
    sidebar keeps it)."""
    for layer in graph_layers:
        await layer.preload([FLATSIDE, SCOPEDSIDE])
        try:
            flat = await children_of(layer, SCOPEDSIDE, "u", "G", "recordGroup", flattened=True)
            assert flat == {"R": True, "c": False}, layer.name
            assert await children_of(layer, SCOPEDSIDE, "u", "G", "recordGroup", flattened=True, only_containers=True) == {"R": True}, layer.name
            bad = [f for i, world in enumerate([FLATSIDE, SCOPEDSIDE]) for f in _bad(await check_world(layer, world, f"flatside-{i}"))]
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([FLATSIDE, SCOPEDSIDE])


async def test_an_open_apps_member_is_matched_by_the_apps_handle_not_its_key(graph_layers) -> None:
    """ArangoDB keys are unique only within a collection: a record filed in a record group whose key is the App's key belongs to that group, not
    to the App, so an App that opens everything does not read it as a member. The browse of the seed lists nothing, as the oracle says."""
    layer = next((layer for layer in graph_layers if layer.name == "arango"), None)
    if layer is None:
        pytest.skip("ArangoDB only")
    await layer.preload([KEYCLASH])
    try:
        ns = await layer._ns(KEYCLASH)
        group = next(n for n in layer._compiled(KEYCLASH)["nodes"] if n["id"] == ns.id("d"))
        props = {k: v for k, v in group["props"].items() if k not in ("id", "permissionModel")}
        await layer._provider.http_client.execute_aql(
            "INSERT MERGE(@props, {_key: @key}) INTO recordGroups INSERT {_from: @record, _to: CONCAT('recordGroups/', @key)} INTO belongsTo",
            bind_vars={"props": props, "key": ns.id("A"), "record": f"records/{ns.id('m')}"})
        assert await children_of(layer, KEYCLASH, "u", "k", "record") == {}, layer.name
    finally:
        await layer.release([KEYCLASH])


async def test_an_open_app_reads_only_its_records_as_members_and_lists_only_its_own_stubs(graph_layers) -> None:
    """An App that opens everything reads a record that belongs to it as a member wherever it hangs, but not a group: below a seed the walk does
    not reach, a strict group of the App is not listed and an OPEN one that inherits from the seed is (OPENGROUP). A stub of another connector
    that hangs in this App's hierarchy is not listed here, nor lights the arrow of the row above it (XSTUB)."""
    for layer in graph_layers:
        await layer.preload([OPENGROUP, XSTUB])
        try:
            assert await children_of(layer, OPENGROUP, "u", "k", "record") == {"go": False}, layer.name
            assert await children_of(layer, XSTUB, "u", "A", "app") == {"g": True}, layer.name
            assert await children_of(layer, XSTUB, "u", "g", "recordGroup") == {"r": False}, layer.name
            assert await children_of(layer, XSTUB, "u", "r", "record") == {}, layer.name
            bad = [f for world in (OPENGROUP, XSTUB) for f in _bad(await check_world(layer, world, "openapp"))]
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([OPENGROUP, XSTUB])


async def test_a_granted_strict_group_below_a_seed_the_app_does_not_reach_is_read_and_listed_nowhere(graph_layers) -> None:
    """OPENGROUP with the strict group granted to the viewer, directly (OPENGRANT) or through a group of users (OPENGRANTTEAM). The oracle denies it: its
    only chain to the App runs through a declaration (R7 skips it), and a strict node is no seed. The access check of each store says the same, and the
    browse of the seed lists exactly what the check admits."""
    oracle = OracleLayer()
    for world in (OPENGRANT, OPENGRANTTEAM):
        v = oracle.explain(world, "u", ["gs"])["gs"]
        assert (v.admitted, v.reason) == (False, "R7:rgl-parent-skipped@d")
    for layer in graph_layers:
        await layer.preload([OPENGRANT, OPENGRANTTEAM])
        try:
            for world in (OPENGRANT, OPENGRANTTEAM):
                assert await layer.admitted(world, "u", ["k", "gs", "go"]) == {"k", "go"}, layer.name
                assert await children_of(layer, world, "u", "k", "record") == {"go": False}, layer.name
                bad = _bad(await check_world(layer, world, "opengrant"))
                assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([OPENGRANT, OPENGRANTTEAM])


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_count_what_the_viewer_may_read(graph_layers, kind: str) -> None:
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for layer in graph_layers:
            found: list[KhFinding] = []
            for start in range(0, len(worlds), CHUNK):
                chunk = worlds[start:start + CHUNK]
                await layer.preload(chunk)
                for offset, world in enumerate(chunk):
                    found += await check_world(layer, world, f"{kind}-{seed}-{start + offset}")
                await layer.release(chunk)
            _report(f"{layer.name} {kind} seed {seed}", found)
            bad = _bad(found)
            assert not bad, f"{layer.name} {kind} seed {seed}\n{_describe(bad)}"
