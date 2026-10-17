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

    computed = statement.index("hasChildren: (EXISTS")
    assert computed < statement.index(NEO4J_CONTAINER_FILTER)


@pytest.mark.asyncio
async def test_neo4j_main_list_does_not_compute_children_before_the_page_is_cut() -> None:
    statement = await _browse_statement(None)

    assert NEO4J_CONTAINER_FILTER not in statement
    assert statement.count("hasChildren: (EXISTS") == 1


def _arango() -> ArangoHTTPProvider:
    return ArangoHTTPProvider(MagicMock(spec=logging.Logger), MagicMock())


def test_arango_decides_the_container_filter_on_real_children() -> None:
    conditions, _ = _arango()._kh_v2_filters_aql(only_containers=True)

    slim = ArangoHTTPProvider._kh_v3_slim_aql(tuple(conditions), "name")

    assert "hasChildren: (LENGTH(" in slim
    assert "hasChildren: false" not in slim


def test_arango_main_list_does_not_read_children_before_the_page_is_cut() -> None:
    slim = ArangoHTTPProvider._kh_v3_slim_aql((), "name")

    assert "hasChildren: false" in slim
