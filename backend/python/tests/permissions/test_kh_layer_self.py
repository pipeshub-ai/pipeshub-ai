"""The Knowledge Hub layer's whole-tree browse finds what it is meant to find: fed store answers that are wrong in each way it checks, it raises
each class (`browse_extra`, `arrow`, `stub`, `browse_gap`, `sidebar`, a row listed twice, a total that does not count the rows; a failed browse of
any node the viewer may open, a leaf too), and past its budget of opens it opens nothing more, counting the rows and the left-out stubs it does
not open instead of dropping them silently. No store: `browse_pages` is replaced by canned answers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import yaml

from . import kh_layer
from .graph import Namespace
from .layers import GraphCheckLayer, arango_graph
from .runner import OracleLayer
from .scenario import world_from_data

WORLD = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r1, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f], belongs: [f]}
  - {id: r2, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f], belongs: [f]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: x, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
grants: [[u, f]]
gates: [[u, A]]
"""))


def _item(node_id: str, node_type: str = "record", *, arrow: bool = False, stub: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=node_id, nodeType=node_type, hasChildren=arrow, isPlaceholder=stub)


class _Layer:
    async def _ns(self, _world) -> Namespace:
        return Namespace("t")


async def _browse(monkeypatch, answers: dict) -> list[kh_layer.KhFinding]:
    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        found = answers.get(node, ([], [0], [0]))
        if not only_containers:
            return found
        # the sidebar: the canned one, else the rows with an arrow and every group
        return answers.get((node, "sidebar")) or ([i for i in found[0] if i.nodeType != "record" or i.hasChildren], [0], [0])
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    return await kh_layer._browse(_Layer(), reference, WORLD, "u", kh_layer.visible(reference, WORLD, "u"), "self")


async def test_a_right_store_gives_no_finding(monkeypatch) -> None:
    rows_f = [_item("r1"), _item("r2")]
    assert await _browse(monkeypatch, {
        "A": ([_item("f", "recordGroup", arrow=True)], [1], [1]),
        "f": (rows_f, [2], [2]),
    }) == []


@pytest.mark.parametrize("wrong, kind, detail", [
    ({"A": ([_item("f", "recordGroup", arrow=True), _item("x", "recordGroup")], [2], [2])},
     "browse_extra", "A: lists x, which the oracle does not let the viewer open"),
    ({"f": ([_item("r1", arrow=True), _item("r2")], [2], [2])}, "arrow", "f: r1 hasChildren True, opening it lists something: False"),
    ({"f": ([_item("r1"), _item("r2"), _item("s", stub=True)], [3], [3])}, "stub", "f: lists the stub s, and opening it lists nothing"),
    ({"f": ([_item("r1")], [1], [1])}, "browse_gap", "f: the browse leaves out r2, which the viewer may open"),
    ({"f": None}, "browse_gap", "f: the browse does not open it"),
    ({"r2": None}, "browse_gap", "r2: the browse does not open it"),
    ({"f": ([_item("r1"), _item("r2"), _item("r1")], [3], [3])}, "browse", "f: lists r1 more than once"),
    ({"f": ([_item("r1"), _item("r2")], [3], [2])}, "browse", "f: totalItems per page [3], counts.total [2], rows 2"),
    ({("f", "sidebar"): ([_item("r1")], [1], [1])}, "sidebar", "f: the sidebar lists r1, and opening it lists nothing"),
])
async def test_each_wrong_answer_raises_its_class(monkeypatch, wrong: dict, kind: str, detail: str) -> None:
    answers = {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": ([_item("r1"), _item("r2")], [2], [2]), **wrong}
    found = await _browse(monkeypatch, answers)
    assert (kind, detail) in {(f.kind, f.detail) for f in found}, found


async def test_the_rows_left_unopened_past_the_cap_are_counted(monkeypatch) -> None:
    monkeypatch.setattr(kh_layer, "MAX_OPENS", 2)
    found = await _browse(monkeypatch, {
        "A": ([_item("f", "recordGroup", arrow=True)], [1], [1]),
        "f": ([_item("r1"), _item("r2")], [2], [2]),
    })
    # A and f; the stub s left out of f, and r1 and r2, are counted apart, not opened
    assert [(f.kind, f.detail) for f in found] == [("truncated", "opened 2 nodes (the cap); 2 listed rows and 1 stubs left out not opened")]


async def test_a_record_the_viewer_may_open_whose_browse_fails_is_an_under_admit(monkeypatch) -> None:
    found = await _browse(monkeypatch, {
        "A": ([_item("f", "recordGroup", arrow=True)], [1], [1]),
        "f": ([_item("r1"), _item("r2")], [2], [2]),
        "r1": None,
    })
    # an under-admit of the record, and a row listed that does not open
    assert sorted((f.kind, f.detail) for f in found) == [("browse", "f: lists r1, and the browse refuses to open it"),
                                                         ("browse_gap", "r1: the browse does not open it")]


TWO_APPS = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r1, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f], belongs: [f]}
  - {id: g, kind: group, connector: B, rule: OPEN, parents: [B], belongs: [B]}
grants: [[u, f], [u, g]]
gates: [[u, A], [u, B]]
"""))


async def test_an_app_past_the_budget_is_counted_not_opened(monkeypatch) -> None:
    """The budget runs out before the second App is opened: it is counted with the rows, and the browse goes on."""
    monkeypatch.setattr(kh_layer, "MAX_OPENS", 2)

    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        return {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": ([_item("r1")], [1], [1])}.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    found = await kh_layer._browse(_Layer(), reference, TWO_APPS, "u", kh_layer.visible(reference, TWO_APPS, "u"), "self")
    assert [(f.kind, f.detail) for f in found] == [("truncated", "opened 2 nodes (the cap); 2 listed rows and 0 stubs left out not opened")]


def _capped_world(listed_first: bool, hidden_first: bool = False) -> object:
    """f holds 201 children, each readable (it inherits from f). ``listed_first``: the browse lists the first 200 (so its rules may select them).
    ``hidden_first``: the first 200 belong to a group that hides its children (the browse leaves them out before the cap)."""
    belongs = ", belongs: [H]" if hidden_first else ""
    kids = "".join(f"  - {{id: k{i:03d}, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]{belongs if i < 200 else ''}}}\n"
                   for i in range(201))
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: u}}]
nodes:
  - {{id: A, kind: app, app_type: DRIVE}}
  - {{id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}}
  - {{id: H, kind: group, connector: A, rule: OPEN, hide_children: true, parents: [A], belongs: [A]}}
{kids}grants: [[u, f]]
gates: [[u, A]]
"""))


@pytest.mark.parametrize("listed_first, kind", [(True, "browse_gap"), (False, "gap")])
async def test_a_child_left_out_is_excused_by_the_cap_only_behind_two_hundred_unlisted_ones(monkeypatch, listed_first: bool, kind: str) -> None:
    """k200 is left out of f. When the browse lists k000..k199 (its rules may select them, so they are no candidates of the cap), k200 is a
    real under-admit; when it lists none of them, k200 is past the 200 the check is asked about, and the cap is reported."""
    world = _capped_world(listed_first)
    rows = [_item(f"k{i:03d}") for i in range(200)] if listed_first else []

    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        return {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": (rows, [len(rows)], [len(rows)])}.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    found = await kh_layer._browse(_Layer(), reference, world, "u", kh_layer.visible(reference, world, "u"), "self")
    assert (kind, "f: the browse leaves out k200, which the viewer may open") in {(f.kind, f.detail) for f in found}


async def test_children_a_hiding_group_holds_are_no_candidates_of_the_cap(monkeypatch) -> None:
    """The first 200 children of f belong to a group that hides its children: the browse leaves them out before the cap, so k200 left out is a
    real under-admit, not the cap's."""
    world = _capped_world(False, hidden_first=True)

    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        return {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": ([], [0], [0])}.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    found = await kh_layer._browse(_Layer(), reference, world, "u", kh_layer.visible(reference, world, "u"), "self")
    assert ("browse_gap", "f: the browse leaves out k200, which the viewer may open") in {(f.kind, f.detail) for f in found}


class _Store:
    """A store that records what it loads and releases; ``check_world`` raises ``fails`` on the world ``fails_at``."""

    def __init__(self, name: str, fails: BaseException | None = None, fails_at: int = -1) -> None:
        self.name, self.fails, self.fails_at = name, fails, fails_at
        self.held: set[int] = set()
        self.checked: list[int] = []

    async def preload(self, worlds: list[int]) -> None:
        self.held |= set(worlds)

    async def release(self, worlds: list[int]) -> None:
        self.held -= set(worlds)


async def _check_world(layer: _Store, world: int, scenario_id: str) -> list[kh_layer.KhFinding]:
    await asyncio.sleep(0)      # the other store's turn
    if world == layer.fails_at:
        raise layer.fails
    layer.checked.append(world)
    return [kh_layer.KhFinding(scenario_id, "u", "under", layer.name)]


async def test_a_store_that_raises_keeps_the_others_findings_and_releases_its_worlds(monkeypatch) -> None:
    monkeypatch.setattr(kh_layer, "check_world", _check_world)
    worlds = list(range(kh_layer.CHUNK + 3))      # two chunks
    neo, arango = _Store("neo4j", ValueError("down"), fails_at=kh_layer.CHUNK + 1), _Store("arango")
    results = await kh_layer.on_every_store([neo, arango], lambda layer: kh_layer.check_worlds(layer, worlds, "t"))
    (first, raised), (second, found) = results
    assert (first, second) == (neo, arango)
    assert isinstance(raised, ValueError) and neo.held == set()
    assert [f.scenario for f in found] == [f"t-{i}" for i in worlds] and arango.held == set()


async def test_a_stop_that_is_no_exception_stops_every_store_and_releases_their_worlds(monkeypatch) -> None:
    """A test timeout (pytest's outcome, a BaseException) on one store cancels the other, which still releases what it loaded."""
    monkeypatch.setattr(kh_layer, "check_world", _check_world)

    class Stop(BaseException):
        pass
    worlds = list(range(5))
    neo, arango = _Store("neo4j", Stop(), fails_at=2), _Store("arango")
    with pytest.raises(Stop):
        await kh_layer.on_every_store([neo, arango], lambda layer: kh_layer.check_worlds(layer, worlds, "t"))
    assert neo.held == set() and arango.held == set()
    assert len(arango.checked) < len(worlds)


def _graph_layer(load, unload) -> GraphCheckLayer:
    return GraphCheckLayer("t", None, load, unload, narrow=False)


async def test_a_store_stopped_while_it_loads_a_chunk_releases_what_it_registered(monkeypatch) -> None:
    """The other store's stop cancels this one inside `_load`, after `preload` has given the worlds their prefixes: the chunk is still released,
    so no partly loaded world is left behind under a prefix a later `preload` would take as loaded."""
    loading, unloaded = asyncio.Event(), []

    async def load(_nodes, _edges) -> None:
        loading.set()
        await asyncio.Event().wait()

    async def unload(prefixes) -> None:
        unloaded.append(prefixes)
    layer = _graph_layer(load, unload)
    task = asyncio.ensure_future(kh_layer.check_worlds(layer, [WORLD], "t"))
    await loading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert unloaded == [["w0-"]] and layer._prefix == {}


async def test_a_release_cancelled_midway_keeps_the_world_for_a_later_release() -> None:
    """A delete that is cancelled half done leaves records: the world stays tracked until its delete returns, so a later release removes them."""
    unloads: list[list[str]] = []

    async def unload(prefixes) -> None:
        unloads.append(prefixes)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def load(_nodes, _edges) -> None:
        return None
    layer = _graph_layer(load, unload)
    await layer.preload([WORLD])
    task = asyncio.ensure_future(layer.release([WORLD]))
    await asyncio.sleep(0)      # the release is inside its delete
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert WORLD in layer._prefix
    await layer.release([WORLD])
    assert layer._prefix == {} and unloads == [["w0-"], ["w0-"]]


STORED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: s, kind: group, connector: A, rule: INHERITED, parents: [A], belongs: [A]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [s], belongs: [A]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [g], belongs: [g]}
grants: [[u, r]]
gates: [[u, A]]
"""))


# Where the placement rule (`place_chain_tops`) puts r under the App, by store shape. It puts a chain-top with no openable own group under the App only
# when an own group hangs from the App, so on ArangoDB's graph (no s, so g hangs from nothing) r goes nowhere. A rule that puts such a record
# under the App whatever its own group's chain changes the ArangoDB entry to {"r"}.
UNDER_THE_APP = {"as written": {"r"}, "arango": set()}


def test_the_app_places_a_chain_top_by_the_graph_the_store_holds() -> None:
    """r's own group g hangs from the App through s, whose rule no connector writes, and ArangoDB keeps no node with an unknown rule (`arango_graph`).
    The layer reads parents, own groups and the chain to the App from each store's own graph (`kh_layer.stored`), so its expectation for the App is
    `UNDER_THE_APP` of that store; the check admits r on both."""
    for reference, shape in ((OracleLayer(), "as written"), (OracleLayer(arango_graph), "arango")):
        assert reference.explain(STORED, "u", ["r"])["r"].admitted
        assert kh_layer.chain_tops(reference, STORED, "u", "A") == UNDER_THE_APP[shape], shape
