"""The expand arrow of a browse row that holds a granted record is settled by the chain-top walk a browse of that row runs (`_kh_v3_settle_held_arrows`),
for both backends; the queries' own arrows are tested on real graphs in ``tests/permissions/test_conformance_kh.py``."""

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

APP = "app-1"
ACCESS = {"grantee_ids": ["u"], "gated_app_ids": [APP], "by_connector": {APP: ["g"]}}


def _row(row_id: str, *, held: bool, arrow: bool, node_type: str = "recordGroup", reached: bool | None = None) -> dict:
    return {"id": row_id, "nodeType": node_type, "hasChildren": arrow, "khHeld": held, **({} if reached is None else {"khFromApp": reached})}


def _provider(placed: dict[str, list[dict]]) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider._kh_v3_chain_tops = AsyncMock(side_effect=lambda app, org, access, txn, *, only_group=None, by_groups=None: {only_group: placed.get(only_group, [])})
    provider._kh_v3_chain_top_groups_by_probe = AsyncMock(return_value=[{"ownGroups": ["x"], "ids": ["r"]}])
    provider.check_access = AsyncMock(side_effect=lambda _u, _o, node_ids=(), **_: AccessCheck(node_ids=frozenset(i for i in node_ids if i.startswith("open"))))
    return provider


async def _settle(provider: Neo4jProvider, rows: list[dict], access: dict | None = ACCESS) -> list[dict]:
    await provider._kh_v3_settle_held_arrows(rows, app_id=APP, org_id="org", access=access, transaction=None)
    return rows


async def test_a_group_that_holds_a_granted_record_has_an_arrow_only_if_the_chain_top_walk_lists_it() -> None:
    provider = _provider({"lists": [{"id": "r"}]})
    rows = await _settle(provider, [_row("lists", held=True, arrow=False), _row("empty", held=True, arrow=False)])
    assert [r["hasChildren"] for r in rows] == [True, False]
    assert provider._kh_v3_chain_tops.await_count == 2
    assert {c.kwargs["only_group"] for c in provider._kh_v3_chain_tops.await_args_list} == {"lists", "empty"}


async def test_only_a_row_that_may_have_a_chain_top_is_asked_and_nothing_is_left_on_any_row() -> None:
    provider = _provider({"g": [{"id": "r"}], "f": [{"id": "r"}]})
    rows = await _settle(provider, [
        _row("g", held=False, arrow=False),                                 # holds nothing granted: no walk, no arrow
        _row("has", held=True, arrow=True),                                 # already has a child: not asked
        _row("f", held=True, arrow=False, node_type="folder"),              # a folder is no chain-top's group
        _row("plain", held=False, arrow=True, node_type="record")])
    assert [r["hasChildren"] for r in rows] == [False, True, False, True] and all("khHeld" not in r for r in rows)
    provider._kh_v3_chain_tops.assert_not_awaited()


async def test_a_probe_reads_the_chain_top_groups_of_the_row_from_the_node() -> None:
    provider = _provider({"lists": [{"id": "r"}]})
    access = {**ACCESS, "probe": {"connector_id": APP, "grantees": ["u", "team"]}}
    row, = await _settle(provider, [_row("lists", held=True, arrow=False)], access)
    assert row["hasChildren"] is True
    provider._kh_v3_chain_top_groups_by_probe.assert_awaited_once_with(APP, "org", "lists", ["u", "team"], None)
    assert provider._kh_v3_chain_tops.await_args.kwargs["by_groups"] == [{"ownGroups": ["x"], "ids": ["r"]}]


async def test_without_the_viewers_access_the_flag_is_dropped_and_the_arrow_stays_what_the_query_said() -> None:
    provider = _provider({"lists": [{"id": "r"}]})
    row, = await _settle(provider, [_row("lists", held=True, arrow=False)], None)
    assert row["hasChildren"] is False and "khHeld" not in row
    provider._kh_v3_chain_tops.assert_not_awaited()


async def test_a_row_the_walk_from_the_app_does_not_reach_has_an_arrow_only_if_the_batch_check_opens_it() -> None:
    provider = _provider({})
    rows = await _settle(provider, [
        _row("open-seed", held=False, arrow=True, reached=False),      # not reached, the check admits it: it opens, the arrow stays
        _row("closed", held=False, arrow=True, reached=False),         # not reached, the check refuses it: opening it lists nothing
        _row("open-no-arrow", held=False, arrow=False, reached=False), # nothing to take away
        _row("reached", held=False, arrow=True, reached=True),         # reached from the App: no question
        _row("plain", held=False, arrow=True)])                        # a flatten row carries no flag
    assert [r["hasChildren"] for r in rows] == [True, False, False, True, True] and all("khFromApp" not in r for r in rows)
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"open-seed", "closed"}
    assert provider.check_access.await_count == 1


async def test_no_check_is_made_when_every_row_is_reached() -> None:
    provider = _provider({})
    await _settle(provider, [_row("a", held=False, arrow=True, reached=True)])
    provider.check_access.assert_not_awaited()


def test_neo4j_arrow_of_a_row_leaves_the_chain_top_to_the_page_and_reads_the_row_s_own_way_in() -> None:
    from app.services.graph_db.neo4j.neo4j_provider import _cypher_kh_has_children

    def stub(v: str) -> str:
        return ""

    row = _cypher_kh_has_children("node", "RULE", "OPENS", stub, "CONTAINER", exact=True, chain_tops=False)
    both = _cypher_kh_has_children("node", "RULE", "OPENS", stub, "CONTAINER", exact=True)
    assert "kh_bg" not in row and "kh_bg" in both            # the sidebar filter keeps the one-query form
    assert "kh_bg" not in _cypher_kh_has_children("node", "RULE", "OPENS", stub, "CONTAINER", exact=False)
    own = Neo4jProvider._kh_v3_row_from_app("node", hop="HOP", hierarchy="IN ['PARENT_CHILD']")
    assert "(kh_ot) WHERE kh_ot = app" in own and "HOP" in own and "<-[kh_ou:NODE_RELATION]-" in own


def test_arangodb_arrow_of_a_browse_row_reads_its_own_way_in_and_leaves_the_chain_top_to_the_page() -> None:
    from app.services.graph_db.arango.arango_http_provider import (
        _aql_kh_filed,
        _aql_kh_has_children,
        _aql_row_from_app,
    )

    exact, flat = _aql_kh_has_children("node", exact=True), _aql_kh_has_children("node")
    assert "kh_row_from_app" in exact and "@kh_from_app" not in exact       # the row's own path to the App, not the folder's
    assert "NOT IS_SAME_COLLECTION" in exact.replace("(NOT IS_SAME", "NOT IS_SAME") and "kh_filed" in exact    # a group's granted record is not its arrow
    assert "@kh_from_app" in flat and "kh_row_from_app" not in flat         # a flatten reads the stricter rule it always did
    assert "HAS(@granted, kh_bm._key)" in _aql_kh_filed("node") and "kh_bm" not in exact
    reached, walked = _aql_row_from_app("node", reached=True), _aql_row_from_app("node", reached=False)
    assert reached == "(@kh_start IN kh_parents[*]._id)" and "INBOUND node" in walked and "@kh_start" not in walked
