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
    assert reached.startswith("(@kh_start IN kh_parents[*]._id AND ") and "kh_sp._id == @kh_start" in reached and "INBOUND node" in walked and "@kh_start" not in walked


async def test_a_declaration_the_viewer_neither_holds_nor_reaches_declares_no_scope_for_a_browse() -> None:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider._kh_v3_start_lineage = AsyncMock(return_value=(["up"], ["d1", "d2"]))
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"start", "up", "d1", "d2"})))
    provider._kh_v3_declared_reached = AsyncMock(return_value=["d1"])        # d2 is admitted, but only below a seed
    provider._kh_v3_declared_groups = AsyncMock(side_effect=lambda declared, _txn: [*declared, "below-d1"])
    probe = {"connector_id": APP, "grantees": ["u"]}
    admitted, ancestors, scope = await provider._kh_v3_scoped_start_access(
        "start", APP, "org", ["u"], [APP], {APP: ["g"]}, None, probe)
    assert admitted and ancestors == ["up"] and scope == ["d1", "below-d1"]
    provider._kh_v3_declared_reached.assert_awaited_once_with(["d1", "d2"], APP, ["g"], probe, None)
    provider._kh_v3_declared_groups.assert_awaited_once_with(["d1"], None)
    # a declaration the viewer is granted is not asked about
    provider._kh_v3_declared_reached.reset_mock()
    provider._kh_v3_declared_reached.return_value = []
    provider._kh_v3_declared_groups.reset_mock()
    _, _, scope = await provider._kh_v3_scoped_start_access("start", APP, "org", ["u"], [APP], {APP: ["d1", "x"]}, None)
    assert scope == ["d1", "below-d1"]
    provider._kh_v3_declared_reached.assert_awaited_once_with(["d2"], APP, ["d1", "x"], None, None)
    provider._kh_v3_declared_reached.reset_mock()
    provider._kh_v3_declared_reached.reset_mock()
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"start", "up"})))      # no declaration admitted: nothing to ask
    assert (await provider._kh_v3_scoped_start_access("start", APP, "org", ["u"], [APP], {APP: []}, None))[2] == []
    provider._kh_v3_declared_reached.assert_not_awaited()


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
    assert 'kh_obp.permissionModel == "RECORD_GROUP_LEVEL"' in _aql_row_from_app("node", reached=False)
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
    for name in ("enrich_exact", "enrich_exact_walk"):
        assert "khStub: LENGTH(" in queries[name] and "kh_row.hasChildren ? [] : [1]" in queries[name]      # asked only of a row with no other arrow
        second = queries[name + "_stubs"]
        assert "kh_pt.edges" in second and "RETURN {i: kh_i, hasChildren:" in second and "parentOptions" not in second


def test_an_app_that_opens_everything_opens_only_its_own_members_below_a_node_the_walk_does_not_reach() -> None:
    from app.services.graph_db.arango.arango_http_provider import _aql_browse_child

    cypher = Neo4jProvider.__new__(Neo4jProvider)._kh_v3_child_rule("c", "p", from_app="FROM", opens="OPENS", state=False)
    member = "(OPENS AND EXISTS { (c)-[:BELONGS_TO]->(app) })"
    assert f"(FROM OR {member})" in cypher and f"NOT FROM AND NOT {member}" in cypher and "FROM OR OPENS" not in cypher
    aql = _aql_browse_child("c", "p", "t", "FROM")
    assert "(FROM OR (kh_opens AND LENGTH(" in aql and "kh_mb._key == @app_id" in aql and "FROM) OR kh_opens" not in aql


def test_arangodb_stub_recheck_reads_hierarchy_parents_only() -> None:
    """The recheck feeds the same parent list as the page query to the reached shortcut: a link edge from the browsed node is no way in."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    queries = ArangoHTTPProvider._kh_v3_listing_aql()
    for name in ("enrich_exact_stubs", "enrich_exact_walk_stubs"):
        assert "kh_pe.relationshipType IN" in queries[name].split("kh_row_from_app")[0]


def test_arangodb_reached_shortcut_needs_the_browse_hop_from_the_start() -> None:
    from app.services.graph_db.arango.arango_http_provider import _aql_row_from_app

    assert "kh_sp._id == @kh_start" in _aql_row_from_app("node", reached=True)


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
