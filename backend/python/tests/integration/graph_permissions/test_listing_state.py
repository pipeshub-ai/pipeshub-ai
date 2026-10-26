"""The knowledge hub listing state is exact, and every write keeps it so.

Once ``KH_LISTING_STATE_FLAG`` is set the Neo4j listing reads labels (deleted,
hides children, placeholder, folder, inherits from its one parent, has several
parents) and a stored lowercase sort name instead of deriving them per node.
Pinned here: the stamp leaves no node stale; the listing is the same page with and
without the state, on the scope-arms graph (every arm, a node with two parents,
deleted, hidden and placeholder nodes); and after every write primitive that can
change an input, no node is stale.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from neo4j import AsyncGraphDatabase

from app.config.constants.arangodb import DeleteSource

from .fixture_graph import ORG
from .loaders import load_into_neo4j
from .test_provider_v3_listing_parity import _page
from .test_provider_v3_scope_arms import APP, KB_APP, _graph

pytestmark = pytest.mark.integration

R, G, A = "records", "recordGroups", "apps"
NR, IP = "nodeRelations", "inheritPermissions"


@pytest.fixture(scope="module")
async def state_graph(neo4j_provider, neo4j_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await neo4j_provider.stamp_kh_listing_state(batch_size=7)
    return {"nodes": nodes}


def _set_state(provider, *, on: bool) -> None:
    provider._kh_state_ready = on
    provider._kh_state_next_check = 0.0 if on else math.inf


async def _pages(provider, **overrides: Any) -> tuple[dict, dict]:  # noqa: ANN401
    saved = provider._kh_state_ready, provider._kh_state_next_check
    try:
        _set_state(provider, on=False)
        plain = await _page(provider, **overrides)
        _set_state(provider, on=True)
        labelled = await _page(provider, **overrides)
    finally:
        provider._kh_state_ready, provider._kh_state_next_check = saved
    return plain, labelled


async def _cypher(settings: dict, query: str, **params: Any) -> list[dict]:  # noqa: ANN401
    driver = AsyncGraphDatabase.driver(settings["uri"], auth=(settings["username"], settings["password"]))
    try:
        async with driver.session(database=settings["database"]) as session:
            return [r.data() async for r in await session.run(query, **params)]
    finally:
        await driver.close()


async def _labels(settings: dict, node_id: str) -> set[str]:
    rows = await _cypher(settings, "MATCH (n {id: $id}) RETURN labels(n) AS l", id=node_id)
    return {label for label in rows[0]["l"] if label.startswith("Kh")}


async def test_the_stamp_leaves_nothing_stale(state_graph, neo4j_provider, neo4j_settings) -> None:
    assert await neo4j_provider.count_stale_kh_listing_state() == 0
    assert await _labels(neo4j_settings, "v3-dec-deleted") >= {"KhDeleted"}
    assert await _labels(neo4j_settings, "v3-hidden") >= {"KhHidesChildren"}
    assert await _labels(neo4j_settings, "v3-dec-placeholder") >= {"KhPlaceholder"}


@pytest.mark.parametrize("overrides", [
    {},
    {"app_id": KB_APP},
    {"flatten": False, "include_scope": True},
    {"sort_field": "name", "sort_dir": "DESC"},
    {"sort_field": "updatedAt", "sort_dir": "DESC"},
    {"sort_field": "nodeType", "sort_dir": "ASC"},
    {"filters": {"search_query": "Declared"}},
    {"filters": {"node_types": ["folder"]}},
    {"limit": 3},
    {"limit": 3, "include_total": False},
], ids=["global", "collection", "children", "name-desc", "updated", "type", "search", "folders", "page", "no-total"])
async def test_the_listing_is_the_same_with_the_state(state_graph, neo4j_provider, overrides) -> None:
    plain, labelled = await _pages(neo4j_provider, **overrides)
    assert labelled == plain


@pytest.mark.parametrize("flatten", [False, True], ids=["children", "descendants"])
async def test_browsing_every_node_is_the_same_with_the_state(state_graph, neo4j_provider, flatten) -> None:
    starts = [(n["id"], "recordGroup" if n["kind"] == "RecordGroup" else "record")
              for n in state_graph["nodes"] if n["kind"] in ("RecordGroup", "Record")]
    for start_id, start_type in starts:
        plain, labelled = await _pages(
            neo4j_provider, start_id=start_id, start_type=start_type, flatten=flatten, include_scope=True,
        )
        assert labelled == plain, f"browse from {start_id}"


async def test_paging_by_name_is_the_same_with_the_state(state_graph, neo4j_provider) -> None:
    """Cursor to cursor, three rows a page: the stored sort name resumes where
    toLower(name) did."""
    after, seen = None, []
    while True:
        plain, labelled = await _pages(neo4j_provider, limit=3, after=after, include_total=after is None)
        assert labelled == plain
        seen += [r["id"] for r in plain["rows"]]
        if not plain["hasMore"]:
            break
        last = plain["rows"][-1]
        after = {"id": last["id"], "sortKey": last["sortKey"], "nullRank": last["nullRank"]}
    first = plain["rows"][0]
    plain, labelled = await _pages(
        neo4j_provider, limit=3, direction="prev", include_total=False,
        after={"id": first["id"], "sortKey": first["sortKey"], "nullRank": first["nullRank"]},
    )
    assert labelled == plain
    assert len(seen) == len(set(seen)) > 3


def _node(node_id: str, **props: Any) -> dict:  # noqa: ANN401
    return {"id": node_id, "orgId": ORG, "connectorId": APP, "accessRule": "OPEN", **props}


def _edge(frm: str, frm_coll: str, to: str, to_coll: str, **props: Any) -> dict:  # noqa: ANN401
    return {"from_id": frm, "from_collection": frm_coll, "to_id": to, "to_collection": to_coll, **props}


async def test_every_write_keeps_the_state_current(state_graph, neo4j_provider, neo4j_settings) -> None:
    p = neo4j_provider
    parent_child = {"relationshipType": "PARENT_CHILD"}

    async def fresh(step: str) -> None:
        assert await p.count_stale_kh_listing_state() == 0, f"stale after {step}"

    await p.batch_upsert_nodes([
        _node("ls-g1", groupName="Listing group", groupType="DRIVE"),
        _node("ls-g2", groupName="Hiding group", groupType="DRIVE", hideChildren=True),
    ], G)
    await p.batch_upsert_nodes([
        _node("ls-r1", recordName="Beta", recordType="FILE"),
        _node("ls-r2", recordName="A folder", recordType="FILE", mimeType="text/directory"),
        _node("ls-r3", recordName="Gone", recordType="FILE", isDeleted=True),
    ], R)
    await fresh("batch_upsert_nodes")
    assert await _labels(neo4j_settings, "ls-r2") == {"KhFolder"}
    assert await _labels(neo4j_settings, "ls-g2") == {"KhHidesChildren"}

    await p.batch_create_edges([
        _edge(APP, A, "ls-g1", G, **parent_child), _edge(APP, A, "ls-g2", G, **parent_child),
        _edge("ls-g1", G, "ls-r1", R, **parent_child), _edge("ls-g1", G, "ls-r2", R, **parent_child),
        _edge("ls-r2", R, "ls-r3", R, **parent_child),
    ], NR)
    await p.batch_create_edges([
        _edge("ls-r1", R, "ls-g1", G), _edge("ls-r2", R, "ls-g1", G), _edge("ls-r3", R, "ls-r2", R),
    ], IP)
    await fresh("batch_create_edges")
    assert "KhInherits" in await _labels(neo4j_settings, "ls-r1")

    # A second parent: the label no longer speaks for the node; the edge decides.
    await p.batch_create_edges([_edge("ls-g2", G, "ls-r1", R, **parent_child)], NR)
    await fresh("second parent")
    assert await _labels(neo4j_settings, "ls-r1") == {"KhMultiParent"}

    await p.update_node("ls-r1", R, {"recordName": "alpha", "isDeleted": True})
    await fresh("update_node")
    await p.update_node("ls-r1", R, {"isDeleted": False})
    await p.update_node_if_match("ls-r2", R, _node("ls-r2", recordName="Now a file", recordType="FILE"),
                                 "recordName", "A folder")
    await p.batch_update_nodes([{"id": "ls-g2", "hideChildren": False}], G)
    await fresh("node updates")
    assert "KhFolder" not in await _labels(neo4j_settings, "ls-r2")

    await p.delete_edge("ls-g2", G, "ls-r1", R, NR)
    await fresh("delete_edge")
    assert await _labels(neo4j_settings, "ls-r1") == {"KhInherits"}
    await p.batch_delete_edges([_edge("ls-r1", R, "ls-g1", G)], IP)
    await fresh("batch_delete_edges")
    assert await _labels(neo4j_settings, "ls-r1") == set()

    await p.batch_upsert_node_relations([{"from_id": "ls-r1", "to_id": "ls-r3", **parent_child}])
    await fresh("batch_upsert_node_relations")
    await p.delete_parent_child_edge_to_record("ls-r3")
    await fresh("delete_parent_child_edge_to_record")
    await p.delete_edges_by_relationship_types("ls-g1", G, NR, ["PARENT_CHILD"])
    await fresh("delete_edges_by_relationship_types")

    await p.batch_create_edges([_edge("ls-g1", G, "ls-r1", R, **parent_child),
                                _edge("ls-g1", G, "ls-r2", R, **parent_child)], NR)
    await p.batch_create_edges([_edge("ls-r1", R, "ls-g1", G), _edge("ls-r2", R, "ls-g1", G)], IP)
    await p.delete_edges_to("ls-g1", G, IP)
    await fresh("delete_edges_to")
    await p.delete_edges_from("ls-g1", G, NR)
    await fresh("delete_edges_from")
    await p.batch_create_edges([_edge("ls-g1", G, "ls-r1", R, **parent_child)], NR)
    await p.batch_create_edges([_edge("ls-r1", R, "ls-g1", G)], IP)
    await p.delete_edges_between_collections("ls-r1", R, IP, G)
    await fresh("delete_edges_between_collections")
    await p.batch_create_edges([_edge("ls-r1", R, "ls-g1", G)], IP)
    await p.delete_all_edges_for_node(f"{G}/ls-g1", IP)
    await fresh("delete_all_edges_for_node")
    await p.batch_create_edges([_edge("ls-r1", R, "ls-g1", G)], IP)
    await p._delete_all_edges_for_nodes(None, [f"{G}/ls-g1"], [NR, IP])
    await fresh("_delete_all_edges_for_nodes")

    # Raw writes leave the state stale until the migration that owns them runs: a
    # link stored as hierarchy gives ls-r1, which inherits from its one parent, a
    # second parent.
    await p.batch_create_edges([_edge("ls-g1", G, "ls-r1", R, **parent_child)], NR)
    await p.batch_create_edges([_edge("ls-r1", R, "ls-g1", G)], IP)
    assert await _labels(neo4j_settings, "ls-r1") == {"KhInherits"}
    await _cypher(neo4j_settings, """
        MATCH (a {id: 'ls-r2'}), (b {id: 'ls-r1'})
        CREATE (a)-[:NODE_RELATION {relationshipType: 'RELATED'}]->(b)""")
    assert await p.count_stale_kh_listing_state() > 0
    await p.split_link_edges()
    await fresh("split_link_edges")
    await _cypher(neo4j_settings, """
        MATCH (r {id: 'ls-r1'})
        SET r.mimeType = 'application/vnd.folder'
        CREATE (r)-[:IS_OF_TYPE]->(:File {id: 'ls-f1', isFile: false})""")
    assert await p.count_stale_kh_listing_state() > 0
    await p.normalize_folder_mime_types()
    await fresh("normalize_folder_mime_types")
    assert "KhFolder" in await _labels(neo4j_settings, "ls-r1")

    await p.soft_delete_records(["ls-r2"], APP, delete_source=DeleteSource.USER.value, batch_id="ls-batch", follow=())
    await fresh("soft_delete_records")
    assert "KhDeleted" in await _labels(neo4j_settings, "ls-r2")
    await p._upsert_record_nodes_releasing_trash([
        _node("ls-r4", recordName="Released", recordType="FILE", mimeType="text/directory", externalRecordId="ls-x4"),
    ])
    await fresh("_upsert_record_nodes_releasing_trash")
    assert await _labels(neo4j_settings, "ls-r4") == {"KhFolder"}

    plain, labelled = await _pages(p)
    assert labelled == plain
