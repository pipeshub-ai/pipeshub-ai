"""The Knowledge Hub layer against the oracle, on Neo4j and ArangoDB.

Every golden world and the generated worlds: no row the oracle denies, a total that is the row count, counts by
type that sum the rows, and facet totals that count only what the viewer may read. Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import logging

import pytest

from . import gen
from .kh_layer import KhFinding, browse_pages, check_world, children_of, refused_by_cap
from .kh_worlds import (
    DECLSTUB,
    DEEP_CHAIN,
    DEEPOPEN,
    DEEPSEED,
    FOLDERSTUB,
    KH_WORLDS,
    OPENSTUB,
    SCOPEDTWO,
    TWOWAY,
    XDECL,
    XGRANT,
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


async def test_golden_worlds_count_what_the_viewer_may_read(graph_layers) -> None:
    scenarios: list[Scenario] = load_all()
    for layer in graph_layers:
        worlds = [*dict.fromkeys(s.final_world() for s in scenarios), *KH_WORLDS]      # shapes no generator builds: the connector check of every arm, arrows, a multi-page walk
        bad: list[KhFinding] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                bad += _bad(await check_world(layer, world, f"golden-{start + offset}"))
            await layer.release(chunk)
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


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_count_what_the_viewer_may_read(graph_layers, kind: str) -> None:
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for layer in graph_layers:
            bad: list[KhFinding] = []
            for start in range(0, len(worlds), CHUNK):
                chunk = worlds[start:start + CHUNK]
                await layer.preload(chunk)
                for offset, world in enumerate(chunk):
                    bad += _bad(await check_world(layer, world, f"{kind}-{seed}-{start + offset}"))
                await layer.release(chunk)
            assert not bad, f"{layer.name} {kind} seed {seed}\n{_describe(bad)}"
