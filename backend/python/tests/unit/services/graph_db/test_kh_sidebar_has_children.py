"""The sidebar's container filter reads a real ``hasChildren``, on both stores.

The sidebar asks for containers only: Apps, record groups, folders and records
with children. The filter runs on the row built before the page is cut, where
``hasChildren`` was a constant false, so a page with child pages never reached
the tree. A real graph is in
tests/integration/graph_permissions/test_provider_v3_sidebar_containers.py.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

NEO4J_CONTAINER_FILTER = "(node.hasChildren = true"


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    provider._kh_listing_state_ready = AsyncMock(return_value=True)
    provider._kh_v3_connector_and_size = AsyncMock(return_value=("app1", 0))
    provider._kh_v3_chain_tops = AsyncMock(return_value={})
    return provider


async def _browse_statement(filters: dict | None) -> str:
    provider = _neo4j()
    await provider.get_knowledge_hub_connector_page_v3(
        "app1", "org1", ["uk1"], ["app1"], ["r9"], flatten=False,
        start_id="g1", start_type="recordGroup", filters=filters,
    )
    statements = [c.args[0] for c in provider.client.execute_query.await_args_list]
    return next(s for s in statements if "rawNodes" in s)


@pytest.mark.asyncio
async def test_neo4j_decides_the_container_filter_on_real_children() -> None:
    statement = await _browse_statement({"only_containers": True})

    # the rule's answer and the frontier the check is asked about, read before the filter that keeps the containers
    computed = statement.index("AS khHas")
    assert computed < statement.index("AS khProbe") < statement.index(NEO4J_CONTAINER_FILTER)
    assert "hasChildren: (khHas OR node.id IN $kh_lists.kh_kept OR khProbe)" in statement


@pytest.mark.asyncio
async def test_neo4j_main_list_does_not_compute_children_before_the_page_is_cut() -> None:
    statement = await _browse_statement(None)

    assert NEO4J_CONTAINER_FILTER not in statement
    # the page row's own arrow, read with the row's declared scope (`has_children(row=True)`)
    assert statement.count("hasChildren: any(kh_rowscope IN") == 1


def _arango() -> ArangoHTTPProvider:
    return ArangoHTTPProvider(MagicMock(spec=logging.Logger), MagicMock())


def test_arango_decides_the_container_filter_on_real_children() -> None:
    conditions, _ = _arango()._kh_v2_filters_aql(only_containers=True)

    slim = ArangoHTTPProvider._kh_v3_slim_aql(tuple(conditions), "name", browse=True)
    # a flatten's sidebar keeps a record by the flatten's own arrow, the stricter rule no check settles
    assert "hasChildren: ((IS_SAME_COLLECTION(" in ArangoHTTPProvider._kh_v3_slim_aql(tuple(conditions), "name")

    # a browse's sidebar keeps a record that can have an arrow, and reads the arrow apart (`sidebar_rows`)
    assert "LET kh_side = (LENGTH(FOR kh_kx" in slim and "LET kh_has" not in slim
    assert "hasChildren: kh_side" in slim and 'RETURN [kh_h, node.nodeType, sortKey, false, kh_side AND node.nodeType == "record"]' in slim
    assert "hasChildren: false" not in slim
    rows = ArangoHTTPProvider._kh_v3_listing_aql()["sidebar_rows"]
    assert "LET kh_has = (kh_sd.hideChildren != true AND (kh_filed OR LENGTH(FOR kh_ch" in rows and "RETURN [kh_h, kh_has, NOT kh_has AND" in rows


def test_arango_main_list_does_not_read_children_before_the_page_is_cut() -> None:
    slim = ArangoHTTPProvider._kh_v3_slim_aql((), "name")

    assert "hasChildren: false" in slim


async def test_arango_keeps_a_row_for_its_frontier_only_where_the_check_admits_a_child_of_it() -> None:
    """The slim statement keeps a row its rules list nothing under with its frontier; the page asks the check about every frontier at once and
    drops the rows it admits no child of, before the page is cut and counted."""
    from app.services.graph_db.arango.arango_http_provider import _Visible
    from app.services.graph_db.interface.graph_db_provider import AccessCheck

    provider = _arango()
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(return_value=[
        ["records/a", "record", "a", True], ["records/b", "record", "b", True], ["records/c", "record", "c", False]])
    provider._kh_v3_run = AsyncMock(return_value=[])
    # the frontier of the rows kept for it, read apart
    provider._kh_v3_frontiers = AsyncMock(return_value={"records/a": ["c1"], "records/b": ["c2"]})
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"c2"})))
    conditions, _ = provider._kh_v2_filters_aql(only_containers=True)
    page = await provider._kh_v3_page_rows(
        _Visible(nodes=["records/a", "records/b", "records/c"]), "app1", "org1", limit=10, sort_field="name", sort_dir="asc", after=None,
        direction="next", filters={"only_containers": True}, lists_placeholders=True, exact_arrows=True,
        row_ctx={"granted": {}, "kh_scope": {}, "kh_from_app": True, "kh_seeded": []}, transaction=None, access={"grantee_ids": []})
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"c1", "c2"}
    assert page["total"] == 2 and provider._kh_v3_run.await_args.args[1]["kh_page"] == ["records/b", "records/c"]


async def test_arango_keeps_a_sidebar_record_where_its_arrow_read_apart_is_on() -> None:
    """A browse sidebar's record that can have an arrow is kept where `sidebar_rows` lights it, asks the check about its frontier where only the
    check can light it, and is dropped otherwise or when it is gone; a folder is kept without asking."""
    from app.services.graph_db.arango.arango_http_provider import _Visible
    from app.services.graph_db.interface.graph_db_provider import AccessCheck

    provider = _arango()
    provider.http_client = MagicMock()
    provider.http_client.execute_aql = AsyncMock(return_value=[
        ["records/a", "record", "a", False, True], ["records/b", "record", "b", False, True], ["records/c", "record", "c", False, True],
        ["records/d", "record", "d", False, True], ["records/f", "folder", "f", False, False]])
    lit = {"sidebar_rows": [["records/a", True, False], ["records/b", False, True], ["records/c", False, False]]}
    provider._kh_v3_run = AsyncMock(side_effect=lambda name, *_a, **_k: lit.get(name, []))
    provider._kh_v3_frontiers = AsyncMock(return_value={"records/b": ["c2"]})
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"c2"})))
    page = await provider._kh_v3_page_rows(
        _Visible(nodes=["records/a", "records/b", "records/c", "records/d", "records/f"]), "app1", "org1", limit=10, sort_field="name",
        sort_dir="asc", after=None, direction="next", filters={"only_containers": True}, lists_placeholders=True, exact_arrows=True,
        row_ctx={"granted": {}, "kh_scope": {}, "kh_from_app": True, "kh_seeded": []}, transaction=None, access={"grantee_ids": []})
    sidebar = provider._kh_v3_run.await_args_list[0].args
    assert sidebar[0] == "sidebar_rows" and sidebar[1]["kh_page"] == ["records/a", "records/b", "records/c", "records/d"]
    assert provider._kh_v3_frontiers.await_args.args[0] == ["records/b"]
    assert page["total"] == 3 and provider._kh_v3_run.await_args.args[1]["kh_page"] == ["records/a", "records/b", "records/f"]


async def test_a_folder_in_the_sidebar_asks_the_check_nothing() -> None:
    """The container filter keeps a folder whatever lies below it, so only a record carries its frontier to the check: a folder dropped for a
    refused child would vanish once an unreadable child is added (both stores)."""
    conditions, _ = _arango()._kh_v2_filters_aql(only_containers=True)
    slim = ArangoHTTPProvider._kh_v3_slim_aql(tuple(conditions), "name", browse=True)
    assert 'kh_side AND node.nodeType == "record"]' in slim
    rows = ArangoHTTPProvider._kh_v3_listing_aql()["sidebar_rows"]
    assert 'NOT kh_has AND (IS_SAME_COLLECTION("recordGroups", kh_sd) ? "recordGroup"' in rows and '== "record" AND' in rows
    statement = await _browse_statement({"only_containers": True})
    assert "(NOT khHas AND NOT node:RecordGroup AND NOT node:KhFolder AND EXISTS" in statement


@pytest.mark.asyncio
async def test_neo4j_reads_the_hiding_group_test_once_per_copy_of_the_rule() -> None:
    """Each copy of the browse rule in a page statement costs its plan, and the plan is made again whenever the store's statistics move: with
    the hiding-group test in every copy, CI's Neo4j (768 MB heap) spent the conformance step planning. The rule holds no such test; the
    statement holds it once per copy of the rule (for every arm but the walk's hop), once for the undecided children, and once for the arrow's
    own declared scope; a stub's way is read once."""
    rule = Neo4jProvider.__new__(Neo4jProvider)._kh_v3_child_rule("c", "p", from_app="f", opens="o", state=True)
    assert rule.count("kh_q <>") == 1 and "WHERE (kh_hp AND f)" in rule
    browse = await _browse_statement(None)
    assert browse.count("kh_q <>") == browse.count("any(kh_hp IN") + 2
