"""The expand arrow of a browse row that holds a granted record is settled by the chain-top walk a browse of that row runs (`_kh_v3_settle_held_arrows`),
for both backends; the queries' own arrows are tested on real graphs in ``tests/permissions/test_conformance_kh.py``."""

from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

APP = "app-1"
ACCESS = {"grantee_ids": ["u"], "gated_app_ids": [APP], "by_connector": {APP: ["g"]}}


def _row(row_id: str, *, held: bool, arrow: bool, node_type: str = "recordGroup", reached: bool | None = None, kids: list[str] | None = None) -> dict:
    return {"id": row_id, "nodeType": node_type, "hasChildren": arrow, "khHeld": held, **({} if reached is None else {"khFromApp": reached}),
            **({} if kids is None else {"khKids": kids})}


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


async def test_a_row_the_check_refuses_has_no_arrow_and_its_children_are_asked_only_once_it_is_admitted() -> None:
    """A browse of a start the check refuses lists nothing, so a row the walk from the App does not reach opens only if the check admits it;
    the children of such a row with no child the rules select are asked in a second round, for the rows the first one admits."""
    provider = _provider({})
    rows = [_row("open-bare", held=False, arrow=False, reached=False, kids=["open-k1", "k2"]),
            _row("shut-bare", held=False, arrow=False, reached=False, kids=["open-k3"]),
            _row("closed", held=False, arrow=True, reached=False),
            _row("open-arrow", held=False, arrow=True, reached=False, kids=["open-k4"])]
    await provider._kh_v3_settle_held_arrows(rows, app_id=APP, org_id="org", access=ACCESS, transaction=None, listed_under="p")
    assert [r["hasChildren"] for r in rows] == [True, False, False, True] and all("khKids" not in r for r in rows)
    assert [set(c.kwargs["node_ids"]) for c in provider.check_access.await_args_list] == [
        {"open-bare", "shut-bare", "closed", "open-arrow"}, {"open-k1", "k2"}]


async def test_a_row_with_no_child_the_rules_select_has_an_arrow_when_the_check_admits_a_child() -> None:
    provider = _provider({})
    rows = [_row("a", held=False, arrow=False, kids=["open-a1", "a2"]), _row("b", held=False, arrow=False, kids=["b1"]),
            _row("c", held=False, arrow=False), _row("x", held=False, arrow=False, node_type="app", kids=["open-x"]),
            _row("r", held=False, arrow=True, reached=False, kids=["r1"])]
    await provider._kh_v3_settle_held_arrows(rows, app_id=APP, org_id="org", access=ACCESS, transaction=None, listed_under="p")
    assert [r["hasChildren"] for r in rows] == [True, False, False, False, False]
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"open-a1", "a2", "b1", "r"}
    assert provider.check_access.await_count == 1


async def test_the_children_of_a_row_the_rules_give_an_arrow_are_never_asked() -> None:
    """Below a shared folder every row is unreached: a row with an arrow is asked about itself alone, however many children it has."""
    provider = _provider({})
    rows = [_row(f"open-s{i}", held=False, arrow=True, reached=False, kids=[f"open-s{i}-{k}" for k in range(200)]) for i in range(50)]
    await provider._kh_v3_settle_held_arrows(rows, app_id=APP, org_id="org", access=ACCESS, transaction=None, listed_under="p")
    assert all(r["hasChildren"] for r in rows)
    assert provider.check_access.await_count == 1 and len(provider.check_access.await_args.kwargs["node_ids"]) == 50


async def test_a_page_whose_arrows_the_rules_settle_asks_no_check() -> None:
    provider = _provider({})
    await provider._kh_v3_settle_held_arrows([_row("a", held=False, arrow=True, kids=[]), _row("b", held=False, arrow=False, kids=[])],
                                             app_id=APP, org_id="org", access=ACCESS, transaction=None, listed_under="p")
    provider.check_access.assert_not_awaited()


async def test_the_check_of_undecided_children_reads_the_viewers_access_as_given() -> None:
    """The probe's grantees are every principal the viewer holds (`_kh_v3_connector_grantees`), so the check reads them unchanged."""
    provider = _provider({})
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"c"})))
    access = {**ACCESS, "probe": {"connector_id": APP, "grantees": ["u", "grp"]}}
    assert await provider._kh_v3_admitted(["c", "d"], org_id="org", access=access, transaction=None) == {"c"}
    assert provider.check_access.await_args.kwargs["access"] is access
    assert await provider._kh_v3_admitted([], org_id="org", access=access, transaction=None) == frozenset()
    assert provider.check_access.await_count == 1


def _child(i: int) -> dict:
    """An undecided child in sort order: the check admits the ones named ``open``."""
    return {"id": f"{'open' if i % 400 == 399 else 'shut'}{i:04d}", "sortKey": f"n{i:04d}", "nullRank": 0}


def _reader(children: list[dict], selected: list[dict], limit: int) -> tuple[list, object]:
    """A browse's ``read``: the page (what the rules select, and the children admitted so far, in order) and ``n`` children past ``edge``."""
    calls: list = []

    async def read(admitted: list[str], edge: dict | None, n: int) -> tuple[dict, list[dict]]:
        calls.append((len(admitted), edge and edge["id"], n))
        listed = sorted([*selected, *(c for c in children if c["id"] in admitted)], key=lambda r: r["sortKey"])
        i = next((k + 1 for k, c in enumerate(children) if edge and c["id"] == edge["id"]), 0)
        return {"rows": listed[:limit + 1], "total": len(listed)}, children[i:i + n]
    return calls, read


async def _undecided(provider: Neo4jProvider, read, limit: int) -> tuple[dict, dict | None, dict | None]:
    payload, cut = await provider._kh_v3_browse_undecided(read, after=None, limit=limit, descending=False, direction="next", org_id="org",
                                                          access=ACCESS, transaction=None)
    return payload, cut, provider._kh_v3_end_at(payload, cut, limit=limit, descending=False, direction="next")


async def test_a_browse_asks_about_its_undecided_children_a_window_at_a_time_until_the_page_is_decided() -> None:
    """1,000 children the rules leave undecided, the check admitting every 400th: the first 200 are asked, then the next ones, until every one
    is asked, so the children it admits past the first 200 are listed (they were left out when only the first 200 by id were asked)."""
    provider = _provider({})
    children = [_child(i) for i in range(1000)]
    calls, read = _reader(children, [], limit=50)
    payload, cut, resume = await _undecided(provider, read, 50)
    assert [r["id"] for r in payload["rows"]] == ["open0399", "open0799"] and cut is None and resume is None
    assert [len(c.kwargs["node_ids"]) for c in provider.check_access.await_args_list] == [200, 800]
    # one read for the page and the children, one to list the two the check admitted
    assert calls == [(0, None, 2001), (2, None, 0)]


async def test_past_the_children_in_hand_the_page_is_read_again_from_the_last_one_asked(monkeypatch) -> None:
    provider = _provider({})
    monkeypatch.setattr(Neo4jProvider, "_KH_UNDECIDED_WINDOW_MAX", 300)
    children = [_child(i) for i in range(1000)]
    calls, read = _reader(children, [], limit=50)
    payload, cut, resume = await _undecided(provider, read, 50)
    assert [r["id"] for r in payload["rows"]] == ["open0399", "open0799"] and cut is None and resume is None
    assert [len(c.kwargs["node_ids"]) for c in provider.check_access.await_args_list] == [200, 100, 300, 300, 100]
    assert calls == [(0, None, 301), (0, "shut0299", 301), (1, "shut0599", 301), (2, "shut0899", 301)]


async def test_a_page_decided_before_its_undecided_children_run_out_stands_with_a_lower_bound_total() -> None:
    """Enough rows sort before the last child asked: the page is exact and stops asking, and its total says it is a lower bound."""
    provider = _provider({})
    children = [_child(i) for i in range(1000)]
    selected = [{"id": f"rule{i}", "sortKey": f"a{i:04d}", "nullRank": 0} for i in range(3)]
    calls, read = _reader(children, selected, limit=2)
    payload, cut, resume = await _undecided(provider, read, 2)
    assert [r["id"] for r in payload["rows"]] == ["rule0", "rule1", "rule2"] and cut["id"] == "shut0199" and resume is None
    assert len(calls) == 1 and provider.check_access.await_count == 1
    assert Neo4jProvider._kh_v3_with_cut({"hasMore": True}, cut, resume, include_total=True) == {"hasMore": True, "totalCapped": True}


async def test_past_the_budget_the_page_ends_at_the_last_child_asked_and_the_next_one_resumes_there(monkeypatch) -> None:
    provider = _provider({})
    monkeypatch.setattr(Neo4jProvider, "_KH_UNDECIDED_BUDGET_S", -1.0)
    children = [_child(i) for i in range(1000)]
    selected = [{"id": "rule", "sortKey": "n0300", "nullRank": 0}]
    _, read = _reader(children, selected, limit=50)
    payload, cut, resume = await _undecided(provider, read, 50)
    # the rule's row lies past the last child asked (shut0199): the children between them are not asked yet, so it waits for the next page
    assert payload["rows"] == [] and resume == {"nullRank": 0, "sortKey": "n0199", "id": "shut0199"}
    assert Neo4jProvider._kh_v3_with_cut({"hasMore": False}, cut, resume, include_total=False) == {"hasMore": True, "resume": resume}


def test_a_row_past_an_edge_is_read_in_the_page_s_direction() -> None:
    edge, before, after = ({"id": i, "sortKey": k, "nullRank": 0} for i, k in (("e", "m"), ("b", "a"), ("a", "z")))
    past = Neo4jProvider._kh_v3_past
    assert past(after, edge, descending=False, direction="next") and not past(before, edge, descending=False, direction="next")
    assert past(before, edge, descending=False, direction="prev") and past(before, edge, descending=True, direction="next")
    assert not past(edge, edge, descending=False, direction="next")


def test_a_browse_and_a_row_s_arrow_ask_about_the_same_children() -> None:
    """The undecided children of a node, read by its browse (beside what its rules select) and by its arrow (when its rules select none, as its
    frontier: its own and those of the stubs it lists on the way down): one expression on each backend. The arrow asks about the first ones by
    id; the browse reads every one, in the page's order a window at a time (`_kh_v3_browse_undecided`)."""
    from app.services.graph_db.arango.arango_http_provider import (
        ArangoHTTPProvider,
        _aql_frontier,
        _aql_undecided,
    )

    neo = Neo4jProvider.__new__(Neo4jProvider)
    kids = neo._kh_v3_undecided("node")
    assert "ORDER BY kh_uid LIMIT 200" in kids and "isPlaceholder" in kids and "kh_hidku" in kids
    tail = "WITH DISTINCT kh_u.id AS kh_uid ORDER BY kh_uid LIMIT 200 RETURN kh_uid"
    browse = neo._kh_v3_undecided("start", decided="RULE", order="ORDER")
    assert "WITH DISTINCT kh_u AS node, app ORDER LIMIT $kh_uread RETURN {id: node.id, sortKey: sortKey, nullRank: nullRank}" in browse
    assert browse.replace("start", "node").replace("AND NOT coalesce((RULE), false)", "").split("WITH DISTINCT")[0] == kids.split(tail)[0]
    aql = _aql_undecided("node")
    assert "SORT kh_uk LIMIT 200" in aql
    every = _aql_undecided("node", decided="@d")
    assert "SORT kh_uk" not in every and "FILTER kh_uc._id NOT IN @d" in every
    assert every.replace("FILTER kh_uc._id NOT IN @d", "").split() == aql.replace("SORT kh_uk LIMIT 200", "").split()
    queries = ArangoHTTPProvider._kh_v3_listing_aql()
    assert _aql_undecided("kh_fy") in _aql_frontier("node", "kh_row_from_app") and "0..50 OUTBOUND kh_fz.n" in _aql_frontier("node", "kh_row_from_app")
    # a row's frontier is read apart, for the rows that ask (`khAsk`), from each row's own reach
    assert _aql_frontier("node", "kh_rfa") in queries["frontier"] and "khAsk:" in queries["enrich_exact"] and "khAsk: false" in queries["enrich"]
    assert neo._kh_v3_undecided("kh_fy") in neo._kh_v3_frontier("node", "kh_row_from_app", state=True, grant_once=False)
    # the children are read only for a row whose rules select none, or that is asked again for its stub child
    assert "(kh_row.hasChildren AND NOT kh_stub) ? false :" in queries["enrich_exact"]


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
    own = _aql_row_from_app("node")
    assert "INBOUND node" in own and "@kh_start" not in own          # every route up from the row, not a hop from the browsed node


async def test_a_declaration_the_viewer_neither_holds_nor_reaches_declares_no_scope_for_a_browse() -> None:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider._kh_v3_start_lineage = AsyncMock(return_value=(["up"], ["d1", "d2", "d3"]))
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"start", "up", "d1", "d2"})))
    provider._kh_v3_declared_scope = AsyncMock(return_value=["d1", "below-d1"])      # d2 is admitted, but only below a seed
    probe = {"connector_id": APP, "grantees": ["u"]}
    admitted, ancestors, scope = await provider._kh_v3_scoped_start_access(
        "start", APP, "org", ["u"], [APP], {APP: ["g"]}, None, probe)
    assert admitted and ancestors == ["up"] and scope == ["d1", "below-d1"]
    # only the declarations the check admits are asked, with the grants and the probe the page reads (d3 is not admitted)
    provider._kh_v3_declared_scope.assert_awaited_once_with(["d1", "d2"], APP, ["g"], probe, None)
    provider._kh_v3_declared_scope.reset_mock()
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"start", "up"})))      # no declaration admitted: nothing to ask
    assert (await provider._kh_v3_scoped_start_access("start", APP, "org", ["u"], [APP], {APP: []}, None))[2] == []
    provider._kh_v3_declared_scope.assert_not_awaited()


async def test_a_declaration_is_a_scope_where_the_viewer_holds_it_or_the_walk_from_the_app_reaches_it_on_both_backends() -> None:
    """One statement each: the declarations that hold (granted, or reached from the App), then the groups under them, deleted groups cut."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    neo = Neo4jProvider.__new__(Neo4jProvider)
    neo._kh_query = AsyncMock(return_value=[{"scope": ["d1", "below-d1"]}])
    neo._kh_listing_state_ready = AsyncMock(return_value=False)
    assert await neo._kh_v3_declared_scope(["d1", "d2"], APP, ["d1"], None, None) == ["d1", "below-d1"]
    query, kwargs = neo._kh_query.await_args.args[0], neo._kh_query.await_args.kwargs
    assert neo._kh_query.await_count == 1 and kwargs["parameters"]["proved"] == ["d1"]
    assert "kh_dg.id IN $proved OR kh_dg.id IN $grantedIds OR" in query and "(kh_ot) WHERE kh_ot = app" in query and "{0,50}" in query
    arango = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    arango.http_client = MagicMock(execute_aql=AsyncMock(return_value=["d1", "below-d1"]))
    assert await arango._kh_v3_declared_scope(["d1", "d2"], APP, ["d1"], None, None) == ["d1", "below-d1"]
    aql = arango.http_client.execute_aql.await_args.args[0]
    assert arango.http_client.execute_aql.await_count == 1 and "HAS(@granted, kh_dg._key) OR" in aql and "0..50 OUTBOUND kh_dg" in aql


def test_the_browse_rule_reads_a_declaration_as_the_check_does_on_both_backends() -> None:
    from app.services.graph_db.arango.arango_http_provider import _aql_browse_child

    cypher = Neo4jProvider.__new__(Neo4jProvider)._kh_v3_child_rule("c", "p", from_app="FROM", opens="OPENS", state=False)
    seed = "(c.id IN $grantedIds AND coalesce(c.accessRule, 'OPEN') = 'OPEN')"
    assert cypher.count(seed) >= 4                               # a seed is a granted OPEN node, in the walk and the member test of both arms
    assert "p.id IN $declaredScope" in cypher and cypher.count("p.id IN $grantedIds") == 1      # only the arm that walks from the App lets a granted member list on
    aql = _aql_browse_child("c", "p", "t", "FROM")
    assert aql.count('(HAS(@granted, c._key) AND NOT_NULL(c.accessRule, "OPEN") == "OPEN")') >= 4
    assert "HAS(@kh_scope, p._key)" in aql and aql.count("HAS(@granted, p._key)") == 1


def test_the_walk_from_the_app_stops_at_a_declaration_and_a_stub_needs_every_hop_below_it_to_pass() -> None:
    from app.services.graph_db.arango.arango_http_provider import (
        _aql_row_from_app,
        _aql_way_below,
    )

    assert "RECORD_GROUP_LEVEL" in Neo4jProvider._kh_v3_row_from_app("node", hop="HOP", hierarchy="IN ['PARENT_CHILD']")
    assert 'kh_oq.permissionModel == "RECORD_GROUP_LEVEL"' in _aql_row_from_app("node")
    way = _aql_way_below("node")
    assert "kh_pt.edges" in way and "kh_wc" in way and "kh_wp" in way          # every hop of the path is held to the browse rule


def test_arangodb_leaves_a_stub_child_to_a_second_pass_that_returns_the_arrow_alone() -> None:
    from app.services.graph_db.arango.arango_http_provider import (
        ArangoHTTPProvider,
        _aql_kh_has_children,
    )

    page, every = _aql_kh_has_children("node", exact=True), _aql_kh_has_children("node", exact=True, every_hop=True)
    assert "kh_pt.edges" not in page and "kh_pt.edges" in every and "isPlaceholder != true OR" not in page
    assert "kh_pt.edges" not in _aql_kh_has_children("node") and "isPlaceholder != true OR" in _aql_kh_has_children("node")      # a flatten keeps its one-hop test
    queries = ArangoHTTPProvider._kh_v3_listing_aql()
    assert "LET kh_stub = LENGTH(" in queries["enrich_exact"] and "kh_row.hasChildren ? [] : [1]" in queries["enrich_exact"]      # asked only of a row with no other arrow
    second = queries["enrich_exact_stubs"]
    assert "kh_pt.edges" in second and "RETURN {i: kh_i, hasChildren:" in second and "parentOptions" not in second


def test_an_app_that_opens_everything_opens_only_its_own_members_below_a_node_the_walk_does_not_reach() -> None:
    from app.services.graph_db.arango.arango_http_provider import _aql_browse_child

    cypher = Neo4jProvider.__new__(Neo4jProvider)._kh_v3_child_rule("c", "p", from_app="FROM", opens="OPENS", state=False)
    # A record only, as the check's member arm: a group is read by its hops.
    member = "(OPENS AND c:Record AND EXISTS { (c)-[:BELONGS_TO]->(app) })"
    assert f"(FROM OR {member})" in cypher and f"NOT FROM AND NOT {member}" in cypher and "FROM OR OPENS" not in cypher
    aql = _aql_browse_child("c", "p", "t", "FROM")
    record = 'IS_SAME_COLLECTION("records", c)'
    assert f"(FROM OR (kh_opens AND {record} AND LENGTH(FOR kh_ma IN (kh_opens AND {record} ? [1] : [])" in aql
    # The App by its handle: a key is unique only in its collection.
    assert 'kh_mb._id == CONCAT("apps/", @app_id)' in aql and "FROM) OR kh_opens" not in aql


def test_every_listing_reads_the_reach_browse_start_reads() -> None:
    """One definition of the reach a browse of a node starts with, on each backend: the browse's start, the row data and its arrows, and the stub
    listing all read it, so a listing lists what opening the node lists."""
    from app.services.graph_db.arango.arango_http_provider import (
        ArangoHTTPProvider,
        _aql_kh_has_children,
        _aql_reach_walk,
    )

    queries = ArangoHTTPProvider._kh_v3_listing_aql()
    assert _aql_reach_walk("kh_start", "kh_b") in queries["browse_start"]
    walk = _aql_reach_walk("node", "kh_o")
    assert walk in queries["enrich_exact"] and walk in queries["enrich_exact_stubs"] and "@kh_start" not in queries["enrich_exact"]
    assert _aql_reach_walk("kh_ch", "kh_o") in _aql_kh_has_children("node", exact=True, every_hop=True)
    slim = ArangoHTTPProvider._kh_v3_slim_aql((), "name", stubs=True)
    assert _aql_reach_walk("kh_n", "kh_o") in slim and _aql_reach_walk("kh_sc", "kh_o") in slim and "OUTBOUND @kh_start" in slim
    pattern = Neo4jProvider._kh_v3_reach_pattern("node", hop="HOP", hierarchy="IN ['PARENT_CHILD']")
    assert pattern in Neo4jProvider._kh_v3_row_from_app("node", hop="HOP", hierarchy="IN ['PARENT_CHILD']")


def test_arangodb_stub_way_below_reads_the_reach_of_the_browse_and_a_filed_stub_stays_strict() -> None:
    from app.services.graph_db.arango.arango_http_provider import (
        _aql_kh_filed,
        _aql_kh_has_children,
        _aql_way_below,
    )

    for every_hop in (False, True):
        assert "REACH" in _aql_way_below("v", every_hop=every_hop, from_app="REACH") and "REACH" not in _aql_way_below("v", every_hop=every_hop)
    stub_pass, page = _aql_kh_has_children("node", exact=True, every_hop=True), _aql_kh_has_children("node", exact=True)
    assert stub_pass.count("kh_row_from_app") > page.count("kh_row_from_app")          # a child stub is reached as the row is
    assert "kh_row_from_app" not in _aql_kh_filed("node", every_hop=True)      # a stub only filed in the node is not


def test_neo4j_arrow_tests_a_child_stub_with_the_row_s_reach_and_a_filed_one_strictly() -> None:
    from app.services.graph_db.neo4j.neo4j_provider import _cypher_kh_has_children

    expr = _cypher_kh_has_children("node", "RULE", "FILED", lambda v: f"CHILD({v})", "CONT", exact=True, stub_filed=lambda v: f"STRICT({v})")
    assert "CHILD(kh_ch)" in expr and "STRICT(kh_bg)" in expr and "STRICT(kh_bc)" in expr and "CHILD(kh_bc)" not in expr


def test_the_frontier_and_the_sidebar_reach_walk_only_for_a_node_with_a_child() -> None:
    """The frontier (what the check is asked about where a node's rules list nothing) walks only inside a loop over a list that is empty unless
    the node has a hierarchy child, and the sidebar reads a record's own reach (for the row's own arrow test) only then: the sidebar asks both of
    every record of a folder, most of them leaves. A stub's way holds no copy of the check: what its rule leaves is the frontier's."""
    from app.services.graph_db.arango.arango_http_provider import (
        ArangoHTTPProvider,
        _aql_frontier,
        _aql_way_below,
    )

    assert _aql_frontier("node", "X").startswith("UNIQUE(FLATTEN(FOR kh_fg IN (LENGTH(FOR kh_fx, kh_fxe IN 1..1 OUTBOUND node")
    conditions, _ = ArangoHTTPProvider(MagicMock(), MagicMock())._kh_v2_filters_aql(only_containers=True)
    slim = ArangoHTTPProvider._kh_v3_slim_aql(tuple(conditions), "name", stubs=True, browse=True)
    assert "LET kh_row_from_app = (LENGTH(FOR kh_kx, kh_kxe IN 1..1 OUTBOUND kh_sd" in slim
    assert "FILTER kh_way OR (LENGTH(FOR kh_kx" in slim
    assert "kh_xc" not in _aql_way_below("kh_ch", every_hop=True) and "kh_rq" not in _aql_way_below("kh_ch", every_hop=True)
