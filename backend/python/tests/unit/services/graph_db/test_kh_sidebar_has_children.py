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
    assert "hasChildren: (khHas OR node.id IN $kh_lists.kh_kept OR size(khProbe) > 0)" in statement


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

    assert "LET kh_has = (kh_sd.hideChildren != true" in slim and "LET kh_probe = kh_has ? [] :" in slim
    assert "hasChildren: (kh_has OR LENGTH(kh_probe) > 0)" in slim and "RETURN [kh_h, node.nodeType, sortKey, kh_probe]" in slim
    assert "hasChildren: false" not in slim


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
        ["records/a", "record", "a", ["c1"]], ["records/b", "record", "b", ["c2"]], ["records/c", "record", "c", []]])
    provider._kh_v3_run = AsyncMock(return_value=[])
    provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"c2"})))
    conditions, _ = provider._kh_v2_filters_aql(only_containers=True)
    page = await provider._kh_v3_page_rows(
        _Visible(nodes=["records/a", "records/b", "records/c"]), "app1", "org1", limit=10, sort_field="name", sort_dir="asc", after=None,
        direction="next", filters={"only_containers": True}, lists_placeholders=True, exact_arrows=True,
        row_ctx={"granted": {}, "kh_scope": {}, "kh_from_app": True, "kh_seeded": []}, transaction=None, access={"grantee_ids": []})
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"c1", "c2"}
    assert page["total"] == 2 and provider._kh_v3_run.await_args.args[1]["kh_page"] == ["records/b", "records/c"]
