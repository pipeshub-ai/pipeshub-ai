"""The knowledge-hub v3 listing gives the same pages on Arango as on Neo4j.

The scope-arms graph exercises every arm and deliberate gap of the listing
(``test_provider_v3_scope_arms.py`` pins Neo4j's answers). Here Neo4j's page is
the oracle and Arango's must equal it, row for row, for the global flatten, the
App's direct children, a browse from every group and record of the graph (with
its breadcrumbs), the sorts and filters, and paging by cursor both ways.
"""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import ORG, USER_U
from .loaders import arango_shape, load_into_arango, load_into_neo4j
from .test_provider_v3_scope_arms import APP, EXPECTED_VISIBLE, KB_APP, _graph

# A node with two hierarchy parents names one of them; which one is the
# backend's choice (Neo4j keeps the first it collects), so parent fields are
# compared only for nodes with one parent.
PARENT_FIELDS = ("parentId", "parentName", "parentType", "parentIsInternal")


@pytest.fixture(scope="module")
async def parity_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    parents: dict[str, set[str]] = {}
    for edge in edges:
        if edge["type"] == "NODE_RELATION":
            parents.setdefault(edge["to"], set()).add(edge["from"])
    return {"nodes": nodes, "multi_parent": {n for n, p in parents.items() if len(p) > 1}}


async def _page(provider, *, app_id: str = APP, **overrides: Any) -> dict:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(USER_U, ORG)
    kwargs = {
        "app_id": app_id, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 500, "flatten": True, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


def _comparable(page: dict, multi_parent: set[str]) -> dict:
    rows = [
        {k: v for k, v in row.items() if row["id"] not in multi_parent or k not in PARENT_FIELDS}
        for row in page["rows"]
    ]
    return {**page, "rows": rows}


async def _assert_same(parity_graph, neo4j_provider, arango_provider, **overrides: Any) -> dict:  # noqa: ANN401
    expected = await _page(neo4j_provider, **overrides)
    actual = await _page(arango_provider, **overrides)
    multi = parity_graph["multi_parent"]
    assert [r["id"] for r in actual["rows"]] == [r["id"] for r in expected["rows"]]
    assert _comparable(actual, multi) == _comparable(expected, multi)
    return expected


async def test_the_global_flatten(parity_graph, neo4j_provider, arango_provider) -> None:
    page = await _assert_same(parity_graph, neo4j_provider, arango_provider)
    assert {r["id"] for r in page["rows"]} == EXPECTED_VISIBLE


async def test_the_collection(parity_graph, neo4j_provider, arango_provider) -> None:
    await _assert_same(parity_graph, neo4j_provider, arango_provider, app_id=KB_APP)


async def test_the_apps_direct_children(parity_graph, neo4j_provider, arango_provider) -> None:
    await _assert_same(parity_graph, neo4j_provider, arango_provider, flatten=False, include_scope=True)


@pytest.mark.parametrize(("sort_field", "sort_dir"), [
    ("name", "DESC"), ("updatedAt", "ASC"), ("createdAt", "DESC"), ("sizeInBytes", "ASC"), ("nodeType", "ASC"),
])
async def test_the_sorts(parity_graph, neo4j_provider, arango_provider, sort_field, sort_dir) -> None:
    await _assert_same(parity_graph, neo4j_provider, arango_provider, sort_field=sort_field, sort_dir=sort_dir)


@pytest.mark.parametrize("filters", [
    {"search_query": "declared"},
    {"node_types": ["recordGroup"]},
    {"node_types": ["record"], "record_types": ["FILE"]},
    {"only_containers": True},
    {"search_query": "item", "node_types": ["record"]},
], ids=["search", "groups", "files", "containers", "search-records"])
async def test_the_filters(parity_graph, neo4j_provider, arango_provider, filters) -> None:
    await _assert_same(parity_graph, neo4j_provider, arango_provider, filters=filters)


@pytest.mark.parametrize("flatten", [False, True], ids=["children", "descendants"])
async def test_browsing_every_node(parity_graph, neo4j_provider, arango_provider, flatten) -> None:
    """Every group and record as a browse start: admission, the listing below it
    and the breadcrumbs, including starts the user may not open."""
    starts = [(n["id"], "recordGroup" if n["kind"] == "RecordGroup" else "record")
              for n in parity_graph["nodes"] if n["kind"] in ("RecordGroup", "Record")]
    assert len(starts) > 40
    for start_id, start_type in starts:
        try:
            await _assert_same(
                parity_graph, neo4j_provider, arango_provider,
                start_id=start_id, start_type=start_type, flatten=flatten, include_scope=True,
            )
        except AssertionError as exc:
            raise AssertionError(f"browse from {start_id}: {exc}") from exc


@pytest.mark.parametrize(("sort_field", "sort_dir"), [("name", "ASC"), ("updatedAt", "DESC")])
async def test_paging_both_ways(parity_graph, neo4j_provider, arango_provider, sort_field, sort_dir) -> None:
    """Three rows a page, forward to the end and back again, cursors taken from
    each backend's own rows."""
    walks = {}
    for name, provider in (("neo4j", neo4j_provider), ("arango", arango_provider)):
        seen: list[list[str]] = []
        after = None
        while True:
            page = await _page(provider, limit=3, sort_field=sort_field, sort_dir=sort_dir,
                               after=after, include_total=after is None)
            seen.append([r["id"] for r in page["rows"]])
            if not page["hasMore"]:
                break
            last = page["rows"][-1]
            after = {"id": last["id"], "sortKey": last["sortKey"], "nullRank": last["nullRank"]}
        first = page["rows"][0]
        back = await _page(provider, limit=3, sort_field=sort_field, sort_dir=sort_dir,
                           after={"id": first["id"], "sortKey": first["sortKey"], "nullRank": first["nullRank"]},
                           direction="prev", include_total=False)
        seen.append([r["id"] for r in back["rows"]])
        walks[name] = seen
    assert walks["arango"] == walks["neo4j"]
    assert sum(len(p) for p in walks["neo4j"][:-1]) == len(EXPECTED_VISIBLE)
