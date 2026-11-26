"""The split browse's definition, on a graph in memory (`KH_BROWSE_MODE=split`, docs/knowledge-hub-browse.md).

A browse of N lists N's live hierarchy children of N's App that the batch check admits and that no group hiding its
children holds or lies above, the chain-tops placed under N, and a stub child only where opening the stub lists
something. A row's arrow is on exactly where opening the row lists something. The real stores hold the page to the
oracle (tests/permissions/test_conformance_kh.py); this pins the definition's pieces and their costs.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.services.graph_db.neo4j.neo4j_provider import (
    LISTED,
    Neo4jProvider,
    _KhSplitUndecided,
)


class World:
    """children: parent -> [(child, stub, hidden)]; admitted: what the check admits; groups; held groups and the chain-tops
    the walk places under them."""

    def __init__(self, children: dict[str, list[tuple[str, bool, bool]]], admitted: set[str], *, groups: set[str] = frozenset(),
                 held: set[str] = frozenset(), tops_under: set[str] = frozenset(), refused_stubs: set[str] = frozenset(),
                 by_hop: set[str] = frozenset()) -> None:
        self.children, self.admitted, self.groups, self.held, self.tops_under = children, admitted, set(groups), set(held), set(tops_under)
        self.by_hop = set(by_hop)      # children the check's walk from the App admits through the start (a subset of admitted)
        self.refused_stubs = set(refused_stubs)      # stubs a browse of would refuse to open (the rest open by the walk from the App)
        self.checked: list[set[str]] = []
        self.counted: dict[str, int] = {}      # a start's edge count, where it differs from its children (they changed in between)
        self.walked: list[str] = []


def _provider(world: World) -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_split_browse")

    async def kh_query(query: str, parameters: dict[str, Any] | None = None, txn_id: str | None = None) -> list[dict]:
        ids = (parameters or {}).get("ids") or []
        if "-[kh_d:NODE_RELATION]->() WHERE" in query:
            start = parameters["start_id"]
            return [{"n": world.counted.get(start, len(world.children.get(start, [])))}]
        if "kh_c:Record|RecordGroup" in query:
            per_parent, stubs_only = parameters["per_parent"], "kh_c <> kh_p AND coalesce(kh_c.isPlaceholder, false)" in query
            return [{"parent": i, "id": c, "stub": stub, "hidden": hidden, "isGroup": c in world.groups}
                    for i in ids for c, stub, hidden in sorted(world.children.get(i, [])) if stub or not stubs_only][:per_parent]
        if "RETURN g.id AS id" in query:
            return [{"id": i} for i in ids if i in world.groups]
        if "RETURN start.id AS id" in query:
            return [{"id": i} for i in ids if i not in world.refused_stubs]
        if "collect(start) AS reached" in query:
            return [{"ids": [i for i in ids if i in world.by_hop]}]
        if "RETURN node.id AS id" in query:
            return [{"id": i} for i in ids if i in world.held]
        raise AssertionError(query)

    async def admitted(ids: Any, **_k: Any) -> frozenset[str]:  # noqa: ANN401
        ids = set(ids)
        world.checked.append(ids)
        return frozenset(ids & world.admitted)

    async def lists_a_chain_top(_app: str, _org: str, group: str, *_a: Any) -> bool:  # noqa: ANN401
        world.walked.append(group)
        return group in world.tops_under

    p._kh_query = kh_query  # type: ignore[method-assign]
    p._kh_v3_admitted = admitted  # type: ignore[method-assign]
    p._kh_v3_lists_a_chain_top = lists_a_chain_top  # type: ignore[method-assign]
    return p


ACCESS = {"gated_app_ids": ["A"], "grantee_ids": ["u"], "by_connector": {"A": []}}


async def _listed(world: World, start: str = "N", placed: list[str] | None = None) -> list[str]:
    listed, wide = await _provider(world)._kh_split_listed(start, app_id="A", org_id="o", access=ACCESS, placed=placed or [], transaction=None,
                                                            memo={})
    assert not wide
    return listed


async def _lists(world: World, ids: list[str]) -> set[str]:
    return await _provider(world)._kh_split_lists(ids, app_id="A", org_id="o", access=ACCESS, transaction=None, memo={})


@pytest.mark.asyncio
async def test_a_browse_lists_the_admitted_children_no_hiding_group_holds() -> None:
    world = World({"N": [("a", False, False), ("b", False, False), ("h", False, True)]}, admitted={"a", "h"})
    assert await _listed(world) == ["a"]


@pytest.mark.asyncio
async def test_a_stub_is_listed_only_where_opening_it_lists_something() -> None:
    world = World({"N": [("s1", True, False), ("s2", True, False), ("s3", True, False)],
                   "s1": [("x", False, False)],              # admitted below: listed
                   "s2": [("y", False, False)],              # refused below: not listed
                   "s3": [("s4", True, False)], "s4": [("z", False, False)]},   # a stub below a stub, then admitted
                  admitted={"x", "z"})
    assert await _listed(world) == ["s1", "s3"]


@pytest.mark.asyncio
async def test_a_stub_the_browse_refuses_to_open_is_not_listed_and_lights_no_arrow() -> None:
    # golden-188: the stub below a seed is STRICT, so opening it is refused, though the check admits the record below it.
    world = World({"N": [("s", True, False), ("t", True, False)], "s": [("z", False, False)], "t": [("y", False, False)],
                   "r": [("s", True, False)]}, admitted={"z", "y"}, refused_stubs={"s"})
    assert await _listed(world) == ["t"]
    assert await _lists(world, ["r"]) == set()


@pytest.mark.asyncio
async def test_a_child_its_hop_admits_is_listed_without_asking_the_check_and_only_the_rest_are_asked() -> None:
    world = World({"N": [("a", False, False), ("b", False, False), ("c", False, False), ("s", True, False)], "s": [("d", False, False)]},
                  admitted={"a", "b", "d"}, by_hop={"a"})
    assert await _listed(world) == ["a", "b", "s"]
    assert world.checked[0] == {"b", "c"}


@pytest.mark.asyncio
async def test_the_chain_tops_placed_under_the_start_are_listed_once() -> None:
    world = World({"N": [("a", False, False)]}, admitted={"a"})
    assert await _listed(world, placed=["t", "a", "N"]) == ["a", "t"]


@pytest.mark.asyncio
async def test_an_arrow_is_on_where_opening_the_row_lists_something() -> None:
    world = World({"r1": [("a", False, False)], "r2": [("b", False, False)], "r3": [("c", False, True)], "r4": []},
                  admitted={"a", "c"})
    assert await _lists(world, ["r1", "r2", "r3", "r4"]) == {"r1"}


@pytest.mark.asyncio
async def test_a_row_whose_children_are_all_refused_is_read_to_its_end_a_window_at_a_time() -> None:
    kids = [(f"k{i:04d}", False, False) for i in range(1000)]
    world = World({"r": kids, "q": [("only", False, False)]}, admitted={"k0999", "only"})
    assert await _lists(world, ["r", "q"]) == {"r", "q"}
    # Windows of 64, 256, 1024: the row answered by its first child is not asked again.
    assert [len(c) for c in world.checked] == [65, 256, 680]


@pytest.mark.asyncio
async def test_a_group_lists_a_chain_top_only_when_it_holds_a_granted_record_and_the_walk_places_one() -> None:
    world = World({"g1": [], "g2": [], "g3": []}, admitted=set(), groups={"g1", "g2", "g3"}, held={"g1", "g2"}, tops_under={"g1"})
    assert await _lists(world, ["g1", "g2", "g3"]) == {"g1"}
    assert sorted(world.walked) == ["g1", "g2"]      # g3 holds no granted record: never walked


@pytest.mark.asyncio
async def test_one_request_asks_about_a_row_once() -> None:
    world = World({"r": [("a", False, False)]}, admitted={"a"})
    p, memo = _provider(world), {}
    for _ in range(3):
        assert await p._kh_split_lists(["r"], app_id="A", org_id="o", access=ACCESS, transaction=None, memo=memo) == {"r"}
    assert len(world.checked) == 1


@pytest.mark.asyncio
async def test_a_start_with_more_children_than_the_bound_leaves_them_to_the_page_windows() -> None:
    """A wide start lists only its stubs and chain-tops before the page: its other children are read with the page, a window at a time
    (`_kh_split_window`), so deciding it asks the check about none of them. A start with more stubs than the split browse reads is left
    to legacy."""
    world = World({"N": [*((f"c{i}", False, False) for i in range(4)), ("s", True, False)], "s": [("r", False, False)],
                   "M": [(f"d{i}", False, False) for i in range(3)], "W": [(f"t{i}", True, False) for i in range(5)]},
                  admitted={"c0", "d0", "r"})
    p = _provider(world)
    p._KH_SPLIT_START_MAX = 3
    kw = {"app_id": "A", "org_id": "o", "access": ACCESS, "placed": ["top"], "transaction": None, "memo": {}}
    assert await p._kh_split_listed("N", **kw) == (["s", "top"], True)
    assert {"c0", "c1", "c2", "c3"}.isdisjoint(set().union(*world.checked))      # the wide start's children were asked nothing
    world.checked.clear()
    assert await p._kh_split_listed("M", **kw) == (["d0", "top"], False)
    assert world.checked == [{"d0", "d1", "d2"}]
    p._KH_SPLIT_MAX_CHILDREN = 3
    with pytest.raises(_KhSplitUndecided):
        await p._kh_split_listed("W", **kw)


@pytest.mark.asyncio
async def test_a_start_that_grew_past_the_bound_after_its_count_is_read_in_windows() -> None:
    """A sync adds children between the count and the read: the read holds one past the bound, so the start is wide after all. Deciding the
    children it read would leave out those past them."""
    world = World({"N": [*((f"c{i}", False, False) for i in range(5)), ("s", True, False)], "s": [("r", False, False)]},
                  admitted={"c4", "r"})
    world.counted["N"] = 3
    p = _provider(world)
    p._KH_SPLIT_START_MAX = 3
    kw = {"app_id": "A", "org_id": "o", "access": ACCESS, "placed": [], "transaction": None, "memo": {}}
    assert await p._kh_split_listed("N", **kw) == (["s"], True)


@pytest.mark.asyncio
async def test_an_arrow_past_the_bound_is_decided_by_an_admitted_child_or_left_to_legacy() -> None:
    """A row reads its first children by id up to the bound. One admitted among them decides the arrow; with none, the child that
    decides it may lie past the bound, so the row is undecided and the page is answered by legacy, never shown without an arrow."""
    world = World({"r": [(f"k{i}", False, False) for i in range(5)][::-1]}, admitted={"k4"})
    p = _provider(world)
    p._KH_SPLIT_MAX_CHILDREN = 3
    with pytest.raises(_KhSplitUndecided):
        await p._kh_split_lists(["r"], app_id="A", org_id="o", access=ACCESS, transaction=None, memo={})
    assert set().union(*world.checked) == {"k0", "k1", "k2"}
    world = World({"r": [(f"k{i}", False, False) for i in range(5)]}, admitted={"k1"})
    p = _provider(world)
    p._KH_SPLIT_MAX_CHILDREN = 3
    assert await p._kh_split_lists(["r"], app_id="A", org_id="o", access=ACCESS, transaction=None, memo={}) == {"r"}


@pytest.mark.asyncio
async def test_a_stub_left_unfollowed_at_the_depth_cap_is_undecided_not_empty() -> None:
    """A chain of stubs deeper than the cap is not remembered as listing nothing: a request with more depth left would list it."""
    world = World({"N": [("s1", True, False)], "s1": [("s2", True, False)], "s2": [("r", False, False)]}, admitted={"r"})
    p = _provider(world)
    p._KH_SPLIT_STUB_DEPTH = 1
    memo: dict = {}
    with pytest.raises(_KhSplitUndecided):
        await p._kh_split_lists(["N"], app_id="A", org_id="o", access=ACCESS, transaction=None, memo=memo)
    assert "N" not in memo and "s1" not in memo
    p._KH_SPLIT_STUB_DEPTH = 2
    assert await p._kh_split_lists(["N"], app_id="A", org_id="o", access=ACCESS, transaction=None, memo={}) == {"N"}


def test_the_mode_is_split_unless_legacy_or_shadow_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [(None, "split"), ("", "split"), ("  ", "split"), ("split", "split"), (" SPLIT ", "split"),
             ("legacy", "legacy"), (" Legacy ", "legacy"), ("shadow", "shadow"), ("classes", "split")]
    for raw, mode in cases:
        if raw is None:
            monkeypatch.delenv("KH_BROWSE_MODE", raising=False)
        else:
            monkeypatch.setenv("KH_BROWSE_MODE", raw)
        assert Neo4jProvider.kh_browse_mode() == mode, raw


def test_an_unknown_mode_is_warned_about_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("KH_BROWSE_MODE", "no-such-mode")
    with caplog.at_level(logging.WARNING):
        assert [Neo4jProvider.kh_browse_mode() for _ in range(3)] == ["split"] * 3
    assert [r.getMessage() for r in caplog.records if "KH_BROWSE_MODE" in r.getMessage()] == [
        "KH_BROWSE_MODE='no-such-mode' is not legacy, split or shadow; browsing with split"]


def test_the_split_statements_are_small() -> None:
    """Fixed texts that plan in milliseconds: no browse rule, no walk."""
    p = object.__new__(Neo4jProvider)
    text = p._kh_split_children_cypher()
    assert len(text) < 1700 and text.count("EXISTS") <= 3 and "$grantedIds" not in text
    assert "LIMIT $per_parent" in text      # a wide node is read to the bound, not to its end


def test_what_the_walk_never_lists_the_split_browse_never_reads() -> None:
    """A group that carries access only (a Jira security level) is never a row, and an id decided by an earlier statement is dropped
    if it has since been deleted or moved to another App: the children read and the page's read of the decided ids both test it."""
    p = object.__new__(Neo4jProvider)
    for text, v in ((p._kh_split_children_cypher(), "kh_c"), (LISTED, "kh_x")):
        for test in (f"{v}.connectorId = $app_id", f"NOT coalesce({v}.accessOnly, false)", f"NOT coalesce({v}.isDeleted, false)",
                     f"{v}.orgId = $org_id"):
            assert test in text, (v, test)


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "folder"), [(True, "AND NOT n:KhFolder"), (False, "AND NOT (coalesce(n.mimeType IN $folder_mime_types, false)")])
async def test_the_sidebar_asks_an_arrow_only_of_records_that_are_not_folders(state: bool, folder: str) -> None:
    """The sidebar keeps every group and folder whatever lies below it, so only a plain record's arrow decides whether it is kept; a folder
    is decided as the page decides it, by its label once the listing state is ready."""
    seen: list[tuple[str, dict]] = []

    async def kh_query(query: str, parameters: dict[str, Any] | None = None, txn_id: str | None = None) -> list[dict]:
        seen.append((query, parameters or {}))
        return [{"id": "r1"}]

    p = object.__new__(Neo4jProvider)
    p._kh_query = kh_query  # type: ignore[method-assign]
    assert await p._kh_split_plain_records([], org_id="o", state=state, transaction=None) == []
    assert seen == []                                   # nothing listed, nothing asked
    assert await p._kh_split_plain_records(["r1", "f1", "r1"], org_id="o", state=state, transaction=None) == ["r1"]
    (query, params), = seen
    assert "NOT n:RecordGroup" in query and "n.orgId = $org_id" in query and folder in query
    assert params["ids"] == ["f1", "r1"] and params["org_id"] == "o"


@pytest.mark.asyncio
@pytest.mark.parametrize(("skipping", "limit"), [(True, 25), (False, 1000)])
async def test_a_read_before_a_page_split_cannot_decide_is_read_by_legacy_a_page_at_a_time(skipping: bool, limit: int) -> None:
    """The reads before a numbered page take up to 1,000 rows on split; legacy holds every row it keeps in transaction memory, so when it
    answers one of them it reads a page's worth, and the reads go on from its cursor. Outside those reads the limit is the page's own."""
    from app.services.graph_db.interface.graph_db_provider import KH_SKIP_READS

    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_split_browse")
    asked: list[dict[str, Any]] = []

    async def page(**k: Any) -> dict:  # noqa: ANN401
        asked.append(k)
        return {"rows": []}

    p.get_knowledge_hub_connector_page_v3 = page  # type: ignore[method-assign]
    token = KH_SKIP_READS.set({"page_rows": 25}) if skipping else None
    try:
        await p._kh_split_answered_by_legacy(_KhSplitUndecided("wide"), "A", {"limit": 1000, "app_id": "A"}, "split")
    finally:
        if token is not None:
            KH_SKIP_READS.reset(token)
    assert [(a["limit"], a["browse_mode"]) for a in asked] == [(limit, "legacy")]
