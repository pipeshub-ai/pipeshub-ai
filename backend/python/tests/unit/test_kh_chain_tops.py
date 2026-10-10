"""Where browse lists a node shared on its own below a gap, decided once for
both backends. What the page queries return is tested on real graphs in
``tests/integration/graph_permissions/test_provider_v3_chain_tops.py``."""

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.kh_chain_tops import place_chain_tops

APP = "app-1"


def _group(group_id: str, *, deleted: bool = False, under_app: bool = True) -> dict:
    return {"id": group_id, "deleted": deleted, "underApp": under_app}


def _candidate(node_id: str, parents: tuple[str, ...] = (), own: tuple[dict, ...] = ()) -> dict:
    return {"id": node_id, "handle": node_id, "parents": list(parents), "ownGroups": list(own)}


def _placed(candidates: list[dict], admitted: set[str]) -> dict[str, list[str]]:
    return {under: [c["id"] for c in nodes]
            for under, nodes in place_chain_tops(candidates, frozenset(admitted), APP).items()}


def test_own_group_closed_lists_it_under_the_app() -> None:
    """R6's parent and own group are both closed."""
    assert _placed([_candidate("r6", ("rg2",), (_group("rg2"),))], set()) == {APP: ["r6"]}


def test_own_group_open_lists_it_under_that_group_not_a_nearer_parent() -> None:
    """R11's parent R10 is closed, its own group RG1 open."""
    assert _placed([_candidate("r11", ("r10",), (_group("rg1"),))], {"rg1", "r6"}) == {"rg1": ["r11"]}


def test_an_open_parent_lists_it_through_the_walk_instead() -> None:
    """A granted node under an open parent is no chain-top (``rs-s3``)."""
    assert _placed([_candidate("r", ("p",), (_group("g"),))], {"p"}) == {}


def test_a_child_of_the_app_is_listed_by_the_walk() -> None:
    assert _placed([_candidate("r", (APP,), ())], set()) == {}


def test_no_parent_and_no_group_falls_back_to_the_app() -> None:
    """A node whose parent is not in the graph."""
    assert _placed([_candidate("r")], set()) == {APP: ["r"]}


def test_a_deleted_own_group_lists_it_nowhere() -> None:
    """The App fallback never applies to a deleted group."""
    assert _placed([_candidate("r", ("p",), (_group("g", deleted=True),))], set()) == {}


def test_an_own_group_outside_the_app_lists_it_nowhere() -> None:
    """The group must be structurally under the App."""
    assert _placed([_candidate("r", ("p",), (_group("g", under_app=False),))], set()) == {}


def test_several_own_groups_list_it_under_each_open_one_or_the_app_once() -> None:
    both = _candidate("r", ("p",), (_group("g1"), _group("g2"), _group("g3")))
    assert _placed([both], {"g1", "g3"}) == {"g1": ["r"], "g3": ["r"]}
    assert _placed([both], set()) == {APP: ["r"]}
    one_outside = _candidate("r", ("p",), (_group("g1", under_app=False), _group("g2")))
    assert _placed([one_outside], set()) == {APP: ["r"]}


def test_a_group_below_a_gap_lists_under_the_app() -> None:
    """A group's own group is its closed parent group."""
    assert _placed([_candidate("rg3", ("rg2",), (_group("rg2"),))], {"rg1"}) == {APP: ["rg3"]}


def test_a_chain_top_inside_another_chain_tops_subtree() -> None:
    """R6 and R11 both list under the App; R6 being open does not take R11."""
    r6 = _candidate("r6", ("rg1",), (_group("rg1"),))
    r11 = _candidate("r11", ("r10",), (_group("rg1"),))
    assert _placed([r6, r11], {"r6", "r11"}) == {APP: ["r6", "r11"]}


def test_an_own_group_open_only_by_its_grant() -> None:
    """RG1 is a chain-top itself (under the App), and R6 lists under it."""
    rg1 = _candidate("rg1", ("rg0",), (_group("rg0"),))
    r6 = _candidate("r6", ("r3",), (_group("rg1"),))
    assert _placed([rg1, r6], {"rg1"}) == {APP: ["rg1"], "rg1": ["r6"]}


def test_a_node_is_never_its_own_group() -> None:
    assert _placed([_candidate("g", ("p",), (_group("g"),))], {"g"}) == {APP: ["g"]}


def _provider(candidates: list[dict], admitted: set[str], *, hidden: tuple[str, ...] = (),
              under_app: tuple[str, ...] | None = None) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    by_groups: dict[tuple, list[str]] = {}
    for c in candidates:
        by_groups.setdefault(tuple(sorted(g["id"] for g in c["ownGroups"])), []).append(c["id"])
    provider._kh_v3_chain_top_groups = AsyncMock(
        return_value=[{"ownGroups": list(k), "ids": v} for k, v in by_groups.items()])
    provider._kh_v3_chain_top_candidates = AsyncMock(side_effect=lambda _app, ids, _t: [
        c for c in candidates if c["id"] in ids])
    provider._kh_v3_chain_top_facts = AsyncMock(side_effect=lambda _a, nodes, groups, _t: {
        "hidden": set(hidden) & set(nodes),
        "underApp": set(groups) if under_app is None else set(under_app) & set(groups)})
    provider.check_access = AsyncMock(side_effect=lambda _u, _o, node_ids=(), **_: AccessCheck(
        node_ids=frozenset(set(node_ids) & admitted)))
    return provider


ACCESS = {"grantee_ids": ["u"], "gated_app_ids": [APP], "by_connector": {APP: ["r1", "r2", "r3"]}}


def _cand(node_id: str, parents: tuple[str, ...] = (), own: tuple[str, ...] = ()) -> dict:
    return {"id": node_id, "handle": node_id, "parents": list(parents),
            "ownGroups": [{"id": g, "deleted": False} for g in own]}


def _asked(provider) -> list[set[str]]:
    return [set(call.kwargs["node_ids"]) for call in provider.check_access.await_args_list]


async def test_browsing_the_app_asks_own_groups_then_only_the_parents_that_matter() -> None:
    """A node whose own group opens is listed under it, never under the App, so
    its parents are not asked about."""
    provider = _provider([
        _cand("r1", (APP,)),
        _cand("r2", ("p2",), ("g-open",)),
        _cand("r3", ("p3",), ("g-closed",)),
    ], {"g-open"})
    placed = await provider._kh_v3_chain_tops(APP, "org", ACCESS, None)
    assert {under: [c["id"] for c in nodes] for under, nodes in placed.items()} == {APP: ["r3"]}
    # A child of the App asks nothing: the walk from the App lists it. A node whose
    # own group opens is not fetched at all.
    assert _asked(provider) == [{"g-open", "g-closed"}, {"p3"}]
    assert sorted(provider._kh_v3_chain_top_candidates.await_args.args[1]) == ["r1", "r3"]


async def test_browsing_a_group_places_its_records_under_it() -> None:
    provider = _provider([_cand("r2", ("p2",), ("g",)), _cand("r3", ("p3",), ("g",))], {"g", "p3"})
    placed = await provider._kh_v3_chain_tops(APP, "org", ACCESS, None, only_group="g")
    assert {under: [c["id"] for c in nodes] for under, nodes in placed.items()} == {"g": ["r2"]}
    assert _asked(provider) == [{"g"}, {"p2", "p3"}]


async def test_the_walks_run_for_chain_tops_only_and_hidden_ones_drop() -> None:
    provider = _provider([_cand("r2", ("p2",)), _cand("r3", ("p3",)), _cand("r4", ("p4",))], {"p4"},
                         hidden=("r3",))
    placed = await provider._kh_v3_chain_tops(APP, "org", ACCESS, None)
    assert [c["id"] for c in placed[APP]] == ["r2"]
    assert provider._kh_v3_chain_top_facts.await_args.args[1] == ["r2", "r3"]


async def test_an_own_group_outside_the_app_is_learnt_from_the_walk() -> None:
    provider = _provider([_cand("r2", ("p2",), ("g-out",)), _cand("r3", ("p3",), ("g-in",))], set(),
                         under_app=("g-in",))
    placed = await provider._kh_v3_chain_tops(APP, "org", ACCESS, None)
    assert [c["id"] for c in placed[APP]] == ["r3"]


async def test_no_grant_in_the_app_asks_nothing() -> None:
    provider = _provider([], set())
    assert await provider._kh_v3_chain_tops(APP, "org", {**ACCESS, "by_connector": {APP: []}}, None) == {}
    provider._kh_v3_chain_top_groups.assert_not_awaited()
    provider._kh_v3_chain_top_candidates.assert_not_awaited()
    provider.check_access.assert_not_awaited()


async def test_browsing_a_group_asks_only_for_its_records() -> None:
    provider = _provider([], set())
    await provider._kh_v3_chain_tops(APP, "org", ACCESS, None, only_group="g")
    assert provider._kh_v3_chain_top_groups.await_args.args[3] == "g"
