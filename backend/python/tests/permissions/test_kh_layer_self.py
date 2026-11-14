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


def _wide_world() -> object:
    """f holds 201 children, each readable (it inherits from f)."""
    kids = "".join(f"  - {{id: k{i:03d}, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}}\n" for i in range(201))
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: u}}]
nodes:
  - {{id: A, kind: app, app_type: DRIVE}}
  - {{id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}}
{kids}grants: [[u, f]]
gates: [[u, A]]
"""))


@pytest.mark.parametrize("listed_first", [True, False])
async def test_a_child_left_out_is_an_under_admit_however_many_children_lie_ahead_of_it(monkeypatch, listed_first: bool) -> None:
    """k200 is left out of f, behind 200 children listed or not: a browse lists every child the viewer may open, so it is an under-admit (it was
    excused when a browse asked the check about the first 200 children by id alone)."""
    world = _wide_world()
    rows = [_item(f"k{i:03d}") for i in range(200)] if listed_first else []

    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        return {"A": ([_item("f", "recordGroup", arrow=True)], [1], [1]), "f": (rows, [len(rows)], [len(rows)])}.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    found = await kh_layer._browse(_Layer(), reference, world, "u", kh_layer.visible(reference, world, "u"), "self")
    assert ("browse_gap", "f: the browse leaves out k200, which the viewer may open") in {(f.kind, f.detail) for f in found}


def _stub_world(readable: int) -> object:
    """A stub S under h with 201 children; only ``k<readable>`` is readable (through q)."""
    kids = "".join(f"  - {{id: k{i:03d}, kind: record, connector: A, rule: STRICT, parents: [S{', q], inherits: [q' if i == readable else ''}]}}\n"
                   for i in range(201))
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: u}}]
nodes:
  - {{id: A, kind: app, app_type: DRIVE}}
  - {{id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}}
  - {{id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}}
  - {{id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [h], inherits: [h]}}
{kids}grants: [[u, h], [u, q]]
gates: [[u, A]]
"""))


@pytest.mark.parametrize("readable, kind", [(200, "gap"), (100, "browse_gap")])
async def test_a_stub_left_out_is_excused_only_when_what_it_lists_lies_past_the_sample(monkeypatch, readable: int, kind: str) -> None:
    """h leaves the stub S out, and opening S lists its one readable child: reported when that child lies past the first 200 children of S by id
    (the sample a stub's listing asks the check about), an under-admit when it lies within them."""
    world = _stub_world(readable)
    kid = f"k{readable:03d}"

    async def browse_pages(_layer, _world, _viewer, node, _parent_type, _page_size=kh_layer.PAGE, *, only_containers=False) -> tuple | None:
        return {"A": ([_item("h", "recordGroup"), _item("q", "recordGroup", arrow=True)], [2], [2]), "q": ([_item(kid)], [1], [1]),
                "S": ([_item(kid)], [1], [1])}.get(node, ([], [0], [0]))
    monkeypatch.setattr(kh_layer, "browse_pages", browse_pages)
    reference = OracleLayer()
    found = await kh_layer._browse(_Layer(), reference, world, "u", kh_layer.visible(reference, world, "u"), "self")
    assert (kind, "h: the browse leaves out the stub S, and opening it lists something") in {(f.kind, f.detail) for f in found}


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


def test_the_parts_hold_each_generated_world_once() -> None:
    for count in (0, 1, 7, 49, 50, 51, 600):
        assert sorted(i for part in range(kh_layer.PARTS) for i in kh_layer.world_part(count, part)) == list(range(count)), count


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


# Where the placement rule (`place_chain_tops`) puts r under the App, by store shape. It puts a chain-top with no own group the viewer may open
# under the App wherever that group hangs, so r is under the App on ArangoDB's graph too (no s there, so g hangs from nothing).
UNDER_THE_APP = {"as written": {"r"}, "arango": {"r"}}


def test_the_app_places_a_chain_top_by_the_graph_the_store_holds() -> None:
    """r's own group g hangs from the App through s, whose rule no connector writes, and ArangoDB keeps no node with an unknown rule (`arango_graph`).
    The layer reads parents and own groups from each store's own graph (`kh_layer.stored`), so its expectation for the App is `UNDER_THE_APP` of
    that store; the check admits r on both."""
    for reference, shape in ((OracleLayer(), "as written"), (OracleLayer(arango_graph), "arango")):
        assert reference.explain(STORED, "u", ["r"])["r"].admitted
        assert kh_layer.chain_tops(reference, STORED, "u", "A") == UNDER_THE_APP[shape], shape


def test_only_a_total_its_page_marks_as_a_lower_bound_is_held_to_the_rows() -> None:
    """A capped page's total that is a lower bound reads as the rows found; a page not marked capped keeps its total, so one that under-counts
    still fails the check, and a capped total above the rows (no lower bound) is kept too."""
    assert kh_layer.held_to_rows([30, 30, 40], [True, False, True], 40) == [40, 30, 40]
    assert kh_layer.held_to_rows([50, -1], [True, True], 40) == [50, -1]
    assert kh_layer.held_to_rows([30], [False], 40) == [30]
