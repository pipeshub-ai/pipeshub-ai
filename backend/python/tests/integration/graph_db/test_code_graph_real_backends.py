"""The code graph's provider methods on real Neo4j and ArangoDB (T-2).

One small repo is written the way production writes it -- code file records,
block projection, then the real edge builder -- and every provider method the
code graph added is checked against it. Each assertion is the same on both
backends: the point is that the same feature runs on whichever one an install
has. Lookups that rely on an index also check the index exists.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.code_graph.neighbours import impact_impl
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.models.entities import CodeFileRecord, RecordType
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    block_key_for,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.build_state import BuildStateStore
from app.modules.code_graph.edge_builder import (
    CODE_GRAPH_SOURCE,
    build_code_graph_edges,
)
from app.modules.code_graph.facts_source import facts_from_blob_record
from app.modules.parsers.code_parser import CodeFileParser
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.modules.code_graph.facts_source import RecordFacts
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "code_graph_it"
BLOCKS = CollectionNames.BLOCKS.value
RECORDS = CollectionNames.RECORDS.value
RELATIONS = CollectionNames.RECORD_LINKS.value

FILES = {
    "src/shop/b.py": "def target(x):\n    return helper(x)\n\n\ndef helper(x):\n    return x\n",
    "src/shop/a.py": "from shop.b import target\n\n\ndef caller():\n    return target(1)\n",
}

logger = logging.getLogger("code-graph-real-backends-it")


class _Blobs:
    """The facts the indexing pipeline uploads, from the same parse as the blocks."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def load(self, org_id: str, record_ids: list[str]) -> dict[str, RecordFacts]:
        return {rid: facts_from_blob_record(rid, self.records[rid])
                for rid in record_ids if rid in self.records}


@dataclass
class _Repo:
    graph: IGraphDBProvider
    backend: str
    org_id: str
    connector_id: str
    group_id: str
    record_ids: dict[str, str]
    blobs: _Blobs

    def block(self, path: str, qualified_name: str) -> str:
        return block_key_for(self.record_ids[path], qualified_name)

    def block_id(self, path: str, qualified_name: str) -> str:
        """``blocks/<key>``: the form edge endpoints and the builder's key lists take."""
        return f"{BLOCKS}/{self.block(path, qualified_name)}"


async def _write_repo(graph: IGraphDBProvider, org_id: str, connector_id: str,
                      group_id: str) -> tuple[dict[str, str], _Blobs]:
    parser = CodeFileParser()
    blobs = _Blobs()
    record_ids = {path: str(uuid.uuid4()) for path in FILES}
    records = [
        CodeFileRecord(
            id=record_ids[path], org_id=org_id, record_name=path.rsplit("/", 1)[-1],
            record_type=RecordType.CODE_FILE, external_record_id=f"ext-{record_ids[path]}",
            version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB,
            connector_id=connector_id, mime_type="text/x-python", extension="py",
            file_path=path, file_hash=uuid.uuid4().hex, language="python",
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        for path in FILES
    ]
    await graph.batch_upsert_records(records)
    for path, rid in record_ids.items():
        await graph.update_node(rid, RECORDS, {"recordGroupId": group_id})
        container = parser.parse_to_blocks(FILES[path].encode(), path.rsplit("/", 1)[-1],
                                           path, "python")
        blobs.records[rid] = {
            "block_containers": container.model_dump(mode="json", exclude_none=True)
        }
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=org_id, record_id=rid,
                                           record_group_id=group_id,
                                           connector_id=connector_id, language="python",
                                           file_path=path),
            block_containers=container,
        )
    return record_ids, blobs


@pytest.fixture(params=["neo4j", "arango"])
async def repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
               ) -> AsyncIterator[_Repo]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id, connector_id, group_id = f"org-cg-{run}", f"conn-cg-{run}", f"g-cg-{run}"
        record_ids, blobs = await _write_repo(graph, org_id, connector_id, group_id)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(record_ids.values()))
            with contextlib.suppress(Exception):
                for collection in (CollectionNames.CODE_FILES.value, RECORDS):
                    await graph.delete_nodes_and_edges(list(record_ids.values()), collection)

        cleanup.push_async_callback(remove)
        yield _Repo(graph, request.param, org_id, connector_id, group_id, record_ids, blobs)


async def _build(repo: _Repo, touched: set[str] | None = None):  # noqa: ANN202 -- EdgeBuildResult
    return await build_code_graph_edges(
        graph_provider=repo.graph, org_id=repo.org_id, record_group_id=repo.group_id,
        touched_record_ids=touched, facts_source=repo.blobs,
    )


# -- the write path the builder relies on ------------------------------------


async def test_the_builder_writes_the_cross_file_call(repo: _Repo) -> None:
    result = await _build(repo)
    assert result.edges_written >= 1

    caller = repo.block("src/shop/a.py", "function:caller")
    rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=[caller], node_collection=BLOCKS,
        relationship_types=[RecordRelations.CALLS.value], direction="outbound",
        raise_on_error=True,
    )
    assert {r["key"] for r in rows} == {repo.block("src/shop/b.py", "function:target")}
    assert all(r["anchorKey"] == caller and r["direction"] == "outbound" for r in rows)

    one = await repo.graph.get_neighbors_by_relationship_types(
        caller, BLOCKS, [RecordRelations.CALLS.value], "outbound",
    )
    assert {r["key"] for r in one} == {r["key"] for r in rows}


async def test_a_rebuild_replaces_edges_in_place_and_keeps_their_creation_time(
    repo: _Repo,
) -> None:
    """Write, then delete what this build did not write (R-8); an upsert keeps createdAt."""
    await _build(repo)
    target = repo.block_id("src/shop/b.py", "function:target")
    first = await repo.graph.get_edges_by_target_keys(
        [target], RELATIONS, filters={"source": CODE_GRAPH_SOURCE}, raise_on_error=True,
    )
    assert repo.block_id("src/shop/a.py", "function:caller") in first
    before = await _edge_created_at(repo, target)

    result = await _build(repo)

    after = await repo.graph.get_edges_by_target_keys(
        [target], RELATIONS, filters={"source": CODE_GRAPH_SOURCE}, raise_on_error=True,
    )
    assert sorted(after) == sorted(first)
    assert result.edges_removed == 0, "an unchanged edge is updated, not deleted"
    assert await _edge_created_at(repo, target) == before


async def _code_edges_from(repo: _Repo, block_key: str) -> list[dict]:
    edges = await repo.graph.get_edges_from_node(f"{BLOCKS}/{block_key}", RELATIONS)
    return [e for e in edges or [] if e.get("source") == CODE_GRAPH_SOURCE]


async def _edge_created_at(repo: _Repo, _target_key: str) -> list:
    caller = repo.block("src/shop/a.py", "function:caller")
    times = sorted(e.get("createdAtTimestamp") for e in await _code_edges_from(repo, caller))
    assert times and all(times), "code edges carry createdAtTimestamp"
    return times


async def test_delete_by_source_keeps_edges_of_the_excluded_build(repo: _Repo) -> None:
    await _build(repo)
    caller = repo.block("src/shop/a.py", "function:caller")
    filters = {"source": CODE_GRAPH_SOURCE, "orgId": repo.org_id, "recordGroupId": repo.group_id}
    build_ids = {e.get("buildId") for e in await _code_edges_from(repo, caller)}
    assert len(build_ids) == 1 and None not in build_ids, "edges carry their build's id"

    source = repo.block_id("src/shop/a.py", "function:caller")
    kept = await repo.graph.delete_edges_by_source_keys(
        [source], RELATIONS, filters=filters, exclude={"buildId": build_ids.pop()},
    )
    assert kept == 0
    assert await _code_edges_from(repo, caller)
    removed = await repo.graph.delete_edges_by_source_keys([source], RELATIONS, filters=filters)
    assert removed >= 1
    assert await _code_edges_from(repo, caller) == []


async def test_deleting_edges_touching_a_block(repo: _Repo) -> None:
    await _build(repo)
    target = repo.block("src/shop/b.py", "function:target")
    target_id = repo.block_id("src/shop/b.py", "function:target")
    assert await repo.graph.get_edges_by_target_keys(
        [target_id], RELATIONS, filters={"source": CODE_GRAPH_SOURCE}, raise_on_error=True,
    )
    assert await repo.graph.delete_edges_touching_nodes([target], BLOCKS, RELATIONS) >= 1
    assert await repo.graph.get_edges_by_target_keys(
        [target_id], RELATIONS, filters={"source": CODE_GRAPH_SOURCE}, raise_on_error=True,
    ) == []


# -- reads the tools and the builder make ------------------------------------


async def test_counts_and_existence(repo: _Repo) -> None:
    scope = {"orgId": repo.org_id, "recordGroupId": repo.group_id}
    count = await repo.graph.count_nodes_by_filters(BLOCKS, filters=scope)
    assert count >= 4
    assert await repo.graph.has_nodes_by_filters(BLOCKS, filters=scope)
    assert not await repo.graph.has_nodes_by_filters(
        RECORDS, filters={"orgId": repo.org_id, "recordGroupId": repo.group_id},
        in_filters={"indexingStatus": [ProgressStatus.QUEUED.value]},
    )


async def test_records_updated_since_and_file_paths(repo: _Repo) -> None:
    rows = await repo.graph.get_nodes_updated_since(
        RECORDS, "updatedAtTimestamp", 0,
        filters={"orgId": repo.org_id, "recordGroupId": repo.group_id},
        return_fields=["_key"], raise_on_error=True,
    )
    assert {r["_key"] for r in rows} == set(repo.record_ids.values())
    assert await repo.graph.get_nodes_updated_since(
        RECORDS, "updatedAtTimestamp", 2**62,
        filters={"orgId": repo.org_id}, return_fields=["_key"], raise_on_error=True,
    ) == []

    paths = await repo.graph.get_file_paths_for_records(
        repo.org_id, list(repo.record_ids.values()), raise_on_error=True,
    )
    assert {info.file_path for info in paths.values()} == set(FILES)


async def test_path_prefix_and_name_lookups(repo: _Repo) -> None:
    files = await repo.graph.get_nodes_by_field_prefix(
        CollectionNames.CODE_FILES.value, "filePath", "src/shop/",
        filters={"orgId": repo.org_id}, raise_on_error=True,
    )
    assert {f["filePath"] for f in files} == set(FILES)

    hits = await repo.graph.search_nodes_by_field_terms(
        BLOCKS, "name", ["target"], filters={"orgId": repo.org_id}, raise_on_error=True,
    )
    assert repo.block("src/shop/b.py", "function:target") in {
        h.get("_key") or h.get("id") for h in hits}

    exact = await repo.graph.get_documents_paginated(
        collection=BLOCKS, limit=10, sort_field="_key", raise_on_error=True,
        filters={"orgId": repo.org_id, "connectorId": repo.connector_id, "nameFolded": "target"},
    )
    assert [(e.get("_key") or e.get("id")) for e in exact] == [
        repo.block("src/shop/b.py", "function:target")]


async def test_the_edge_rollup_by_file_prefix(repo: _Repo) -> None:
    await _build(repo)
    rows = await repo.graph.get_edge_rollup_by_file_prefix(
        repo.org_id, "src/shop/", [RecordRelations.CALLS.value], "outbound", repo.connector_id,
    )
    assert rows, "the CALLS edge from a.py to b.py rolls up under the prefix"


async def test_the_exact_name_index_exists(repo: _Repo) -> None:
    if repo.backend == "arango":
        indexes = await repo.graph.http_client.get_indexes(BLOCKS)
        fields = [tuple(i.get("fields", ())) for i in indexes]
        assert ("orgId", "connectorId", "nameFolded", "_key") in fields
    else:
        rows = await repo.graph.client.execute_query(
            "SHOW INDEXES YIELD name WHERE name = 'block_org_connector_name_folded' RETURN name",
            {},
        )
        assert rows


# -- sync point compare-and-set and the nameFolded backfill -------------------


async def test_settle_is_a_compare_and_set(repo: _Repo) -> None:
    store = BuildStateStore(repo.graph)
    await store.mark_requested(repo.org_id, repo.connector_id, repo.group_id, 100)
    start = await store.begin(repo.org_id, repo.connector_id, repo.group_id, "lease-1")

    key = f"{repo.group_id}/code-edge-build"
    collection = CollectionNames.SYNC_POINTS.value
    assert not await repo.graph.update_sync_point_if(
        key, {"edgeBuildLeaseToken": "someone-else"}, {"lastEdgeBuildAt": 1}, collection)
    assert await repo.graph.update_sync_point_if(
        key, {"edgeBuildLeaseToken": "lease-1", "edgeBuildFullRequestId": None},
        {"edgeBuildStatus": "building"}, collection)

    assert await store.settle_built(repo.org_id, repo.group_id, lease_token="lease-1",
                                    start=start, started_at_ms=500,
                                    advance_watermark=True, full=True)
    state = await store.read(repo.org_id, repo.group_id)
    assert state is not None
    assert (state.last_build, state.pending, state.outcome) == (500, False, "ready")
    assert state.lease_token is None
    with contextlib.suppress(Exception):
        await repo.graph.remove_sync_point(key, collection)


async def test_the_name_folded_backfill(repo: _Repo) -> None:
    target = repo.block("src/shop/b.py", "function:target")
    await repo.graph.update_node(target, BLOCKS, {"nameFolded": None})

    while await repo.graph.backfill_block_name_folded(limit=1000):
        pass

    doc = await repo.graph.get_document(target, BLOCKS)
    assert doc is not None and doc.get("nameFolded") == "target"


async def test_deleting_a_files_blocks(repo: _Repo) -> None:
    rid = repo.record_ids["src/shop/a.py"]
    assert await repo.graph.delete_blocks_for_records([rid]) >= 1
    assert not await repo.graph.has_nodes_by_filters(BLOCKS, filters={"recordId": rid})


# -- end to end: parse -> project -> build -> an agent tool's answer ---------


async def _grant(repo: _Repo, user_key: str, user_id: str) -> None:
    """A user who owns the repo's files through the connector, as a sync leaves it."""
    g, now = repo.graph, get_epoch_timestamp_in_ms()
    await g.batch_upsert_nodes(
        [{"id": user_key, "userId": user_id, "orgId": repo.org_id, "email": f"{user_id}@example.com",
          "fullName": "Code Graph Tester", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": repo.connector_id, "orgId": repo.org_id, "name": "GitHub", "type": "GitHub",
          "appGroup": "GitHub", "scope": "team", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )

    def edge(to_id: str, to_collection: str, **extra: object) -> dict:
        return {"from_id": user_key, "from_collection": CollectionNames.USERS.value,
                "to_id": to_id, "to_collection": to_collection,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    await g.batch_create_edges(
        [edge(rid, RECORDS, role="OWNER", type="USER") for rid in repo.record_ids.values()],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [edge(repo.connector_id, CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )


async def test_an_agent_tool_answers_from_the_built_graph(repo: _Repo) -> None:
    """The chain production runs, on a real backend: code files parsed and projected,
    the edge builder, then the impact tool asked as a user with access (T-3)."""
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    user_key = f"u-{user_id}"
    await _grant(repo, user_key, user_id)
    try:
        await _build(repo)
        out = await impact_impl(
            graph_provider=repo.graph, connector_id=repo.connector_id, org_id=repo.org_id,
            user_id=user_id, file_path="src/shop/b.py", qualified_name="function:target",
        )
        assert [f["file_path"] for f in out["impacted_files"]] == ["src/shop/a.py"], out
        stranger = await impact_impl(
            graph_provider=repo.graph, connector_id=repo.connector_id, org_id=repo.org_id,
            user_id="nobody", file_path="src/shop/b.py", qualified_name="function:target",
        )
        assert "impacted_files" not in stranger or not stranger["impacted_files"], stranger
    finally:
        with contextlib.suppress(Exception):
            await repo.graph.delete_nodes_and_edges([user_key], CollectionNames.USERS.value)
        with contextlib.suppress(Exception):
            await repo.graph.delete_nodes_and_edges([repo.connector_id], CollectionNames.APPS.value)


# Neo4j only: collected for one backend, so the Arango job never selects (and skips) it.
@pytest.mark.parametrize("repo", ["neo4j"], indirect=True)
async def test_full_text_search_is_scoped_to_the_org_inside_the_index(repo: _Repo) -> None:
    """S-9: the org is part of the Lucene query, not only a filter on what it returns,
    so another tenant's matches are never collected or scored. Queried directly too,
    since the provider falls back to a scan when a full-text query fails."""
    rows = await repo.graph.client.execute_query(
        "CALL db.index.fulltext.queryNodes('block_text_org', $q) YIELD node RETURN count(node) AS n",
        {"q": f'(name:"target") AND orgId:"{repo.org_id}"'},
    )
    assert rows[0]["n"] >= 1
    other = await repo.graph.client.execute_query(
        "CALL db.index.fulltext.queryNodes('block_text_org', $q) YIELD node RETURN count(node) AS n",
        {"q": '(name:"target") AND orgId:"org-that-does-not-exist"'},
    )
    assert other[0]["n"] == 0
    hits = await repo.graph.search_nodes_by_field_terms(
        BLOCKS, "name", ["target"], filters={"orgId": repo.org_id}, raise_on_error=True,
    )
    assert hits and all(h.get("orgId") == repo.org_id for h in hits)
