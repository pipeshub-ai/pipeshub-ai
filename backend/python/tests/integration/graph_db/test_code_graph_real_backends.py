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

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.agents.actions.code_graph.neighbours import impact_impl
from app.agents.actions.code_graph.ops import synced_commits
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.models.entities import CodeFileRecord, RecordGroupType, RecordType
from app.modules.code_graph import deleted_files
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
from app.modules.code_graph.synced_commit import (
    LAST_SYNCED_COMMIT,
    record_synced_commit,
)
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
COMMIT = "0123456789abcdef0123456789abcdef01234567"  # the commit the git connector stores per file

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


_LANGUAGES = {"py": ("python", "text/x-python"), "cs": ("csharp", "text/x-csharp"), "php": ("php", "text/x-php"),
              "ts": ("typescript", "text/typescript")}


async def _write_repo(graph: IGraphDBProvider, org_id: str, connector_id: str, group_id: str,
                      files: dict[str, str] = FILES, roles: dict[str, str] | None = None,
                      ) -> tuple[dict[str, str], _Blobs]:
    """Parse, project and store ``files`` for ``connector_id``; ``roles`` marks a path as test, docs and so on."""
    parser = CodeFileParser()
    blobs = _Blobs()
    record_ids = {path: str(uuid.uuid4()) for path in files}

    def language(path: str) -> tuple[str, str, str]:
        extension = path.rpartition(".")[2]
        return (*_LANGUAGES[extension], extension)

    records = [
        CodeFileRecord(
            id=record_ids[path], org_id=org_id, record_name=path.rsplit("/", 1)[-1],
            record_type=RecordType.CODE_FILE, external_record_id=f"ext-{record_ids[path]}",
            version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB,
            connector_id=connector_id, mime_type=language(path)[1], extension=language(path)[2],
            file_path=path, file_hash=uuid.uuid4().hex, language=language(path)[0],
            indexing_status=ProgressStatus.COMPLETED.value, file_role=(roles or {}).get(path), commit_sha=COMMIT,
        )
        for path in files
    ]
    await graph.batch_upsert_records(records)
    for path, rid in record_ids.items():
        await graph.update_node(rid, RECORDS, {"recordGroupId": group_id})
        container = parser.parse_to_blocks(files[path].encode(), path.rsplit("/", 1)[-1],
                                           path, language(path)[0])
        blobs.records[rid] = {
            "block_containers": container.model_dump(mode="json", exclude_none=True)
        }
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=org_id, record_id=rid,
                                           record_group_id=group_id,
                                           connector_id=connector_id, language=language(path)[0],
                                           file_path=path),
            block_containers=container,
        )
    return record_ids, blobs


@contextlib.asynccontextmanager
async def _connected_repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                          files: dict[str, str]) -> AsyncIterator[_Repo]:
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
        record_ids, blobs = await _write_repo(graph, org_id, connector_id, group_id, files)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(record_ids.values()))
            with contextlib.suppress(Exception):
                for collection in (CollectionNames.CODE_FILES.value, RECORDS):
                    await graph.delete_nodes_and_edges(list(record_ids.values()), collection)

        cleanup.push_async_callback(remove)
        yield _Repo(graph, request.param, org_id, connector_id, group_id, record_ids, blobs)


@pytest.fixture(params=["neo4j", "arango"])
async def repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
               ) -> AsyncIterator[_Repo]:
    async with _connected_repo(request, monkeypatch, FILES) as built:
        yield built


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


async def test_neighbours_page_in_cursor_order_and_resume_after_a_cursor(repo: _Repo) -> None:
    await _build(repo)
    keys = [repo.block("src/shop/a.py", "function:caller"), repo.block("src/shop/b.py", "function:target")]

    async def page(after: str | None, limit: int) -> list[dict]:
        return await repo.graph.get_neighbors_for_nodes_by_relationship_types(
            node_keys=keys, node_collection=BLOCKS, relationship_types=[RecordRelations.CALLS.value],
            direction="any", limit=limit, after=after, raise_on_error=True,
        )

    everything = await page("", 100)
    assert len(everything) >= 2
    cursors = [r["cursor"] for r in everything]
    assert cursors == sorted(cursors) and len(set(cursors)) == len(cursors)
    first, rest = await page("", 1), await page(everything[0]["cursor"], 100)
    assert [r["cursor"] for r in first] == cursors[:1] and [r["cursor"] for r in rest] == cursors[1:]
    assert await page(cursors[-1], 100) == []


async def test_parallel_edges_split_by_a_page_boundary_are_all_returned(repo: _Repo) -> None:
    await _build(repo)
    caller, target = repo.block("src/shop/a.py", "function:caller"), repo.block("src/shop/b.py", "function:target")
    await repo.graph.batch_upsert_node_relations([
        {"from_id": caller, "from_collection": BLOCKS, "to_id": target, "to_collection": BLOCKS,
         "relationshipType": RecordRelations.CALLS.value, "constraintName": name}
        for name in ("p1", "p2")
    ])

    async def pages(limit: int) -> list[dict]:
        out, after = [], ""
        while page := await repo.graph.get_neighbors_for_nodes_by_relationship_types(
                node_keys=[caller], node_collection=BLOCKS, relationship_types=[RecordRelations.CALLS.value],
                direction="outbound", limit=limit, after=after, raise_on_error=True):
            out += page
            after = page[-1]["cursor"]
        return out

    whole, one_by_one = await pages(100), await pages(1)
    assert len(whole) == 3 and len(one_by_one) == 3
    assert [r["cursor"] for r in one_by_one] == [r["cursor"] for r in whole]


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
    assert {info.commit_sha for info in paths.values()} == {COMMIT}  # read back for indexed_commit



async def test_a_code_group_is_stamped_with_its_synced_commit_and_read_back(repo: _Repo) -> None:
    """The strict record-group schema accepts the stamp, the patch keeps the group's other fields, and the
    tools read it back for the caller's org only."""
    groups = CollectionNames.RECORD_GROUPS.value
    await repo.graph.batch_upsert_nodes([{
        "id": repo.group_id, "orgId": repo.org_id, "connectorId": repo.connector_id, "groupName": "acme/shop",
        "groupType": RecordGroupType.PROJECT.value, "connectorName": Connectors.GITHUB.value,
        "externalGroupId": "101-code-repository", "createdAtTimestamp": 1,
    }], groups)
    try:
        processor = SimpleNamespace(
            get_record_group_by_external_id=lambda *_: asyncio.sleep(0, SimpleNamespace(id=repo.group_id)),
            batch_update_nodes=repo.graph.batch_update_nodes,
        )
        assert await record_synced_commit(processor, repo.connector_id, "101-code-repository", COMMIT)

        assert await synced_commits(repo.graph, repo.org_id, [repo.group_id]) == {repo.group_id: COMMIT}
        assert await synced_commits(repo.graph, f"{repo.org_id}-other", [repo.group_id]) == {}
        stored = await repo.graph.get_nodes_by_field_in(
            collection=groups, field_name="_key", field_values=[repo.group_id],
            return_fields=["groupName", LAST_SYNCED_COMMIT], raise_on_error=True,
        )
        assert [(g["groupName"], g[LAST_SYNCED_COMMIT]) for g in stored] == [("acme/shop", COMMIT)]
    finally:
        await repo.graph.delete_nodes_and_edges([repo.group_id], groups)

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


async def test_find_uncalled_blocks_anti_joins_in_the_database(repo: _Repo) -> None:
    """caller() calls target(), target() calls helper(): only caller has no inbound CALLS."""
    await _build(repo)
    ids = list(repo.record_ids.values())
    find = repo.graph.find_uncalled_blocks
    kinds, calls = ["function", "method"], [RecordRelations.CALLS.value]
    page, after = await find(repo.org_id, repo.connector_id, ids, kinds, calls, 10, raise_on_error=True)
    assert [b["qualifiedName"] for b in page] == ["function:caller"] and after is None
    assert page[0]["_key"] == repo.block("src/shop/a.py", "function:caller")
    # scoped to b.py, whose functions are both called: nothing
    only_b = [repo.record_ids["src/shop/b.py"]]
    assert await find(repo.org_id, repo.connector_id, only_b, kinds, calls, 10, raise_on_error=True) == ([], None)
    # a caller in an uncounted file does not make its callee called; one in a counted file still does
    hidden_a = [repo.record_ids["src/shop/a.py"]]
    page, _ = await find(repo.org_id, repo.connector_id, only_b, kinds, calls, 10,
                         uncounted_caller_records=hidden_a, raise_on_error=True)
    assert [b["qualifiedName"] for b in page] == ["function:target"]
    # every function counts once the relation is one no edge has
    everything, _ = await find(repo.org_id, repo.connector_id, ids, kinds, ["OVERRIDES"], 10, raise_on_error=True)
    assert {b["qualifiedName"] for b in everything} == {"function:caller", "function:target", "function:helper"}
    # paged in key order: a full page hands back where to go on, the last page None
    keys = sorted(b["_key"] for b in everything)
    first, after = await find(repo.org_id, repo.connector_id, ids, kinds, ["OVERRIDES"], 2, raise_on_error=True)
    assert [b["_key"] for b in first] == keys[:2] and after == keys[1]
    rest, end = await find(repo.org_id, repo.connector_id, ids, kinds, ["OVERRIDES"], 2, after, raise_on_error=True)
    assert [b["_key"] for b in rest] == keys[2:] and end is None
    # an empty scope asks nothing; another connector's blocks are never returned
    assert await find(repo.org_id, repo.connector_id, [], kinds, calls, 10, raise_on_error=True) == ([], None)
    assert await find(repo.org_id, "conn-other", ids, kinds, calls, 10, raise_on_error=True) == ([], None)


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


# c.py calls into a.py only, so its edge shares the build's write with a.py's edge into b.py but not b.py's fate.
RACE_FILES = {**FILES, "src/shop/c.py": "from shop.a import caller\n\n\ndef run():\n    return caller()\n"}


# Neo4j only: ArangoDB writes an edge without taking its endpoints' locks, so there is no wait to lose.
@pytest.fixture(params=["neo4j"])
async def race_repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                    ) -> AsyncIterator[_Repo]:
    async with _connected_repo(request, monkeypatch, RACE_FILES) as built:
        yield built


async def test_a_build_writing_into_a_file_deleted_under_it_skips_that_file(race_repo: _Repo) -> None:
    """B3: a branch reset deletes files while the build they asked for runs. The build
    read b.py; its write matched it, waited on the delete's lock, and lost it (EntityNotFound).
    The rerun writes every other edge of that statement."""
    repo = race_repo
    rid = repo.record_ids["src/shop/b.py"]
    # As the connector deletes a file (GraphDataStore.delete_record_by_key): the record, then its blocks.
    delete = await repo.graph.begin_transaction([], [RECORDS, BLOCKS], explicit=True)
    await repo.graph.delete_nodes_and_edges([rid], RECORDS, transaction=delete)
    await repo.graph.delete_blocks_for_records([rid], transaction=delete)
    writing = asyncio.Event()

    async def fence() -> None:
        writing.set()

    build = asyncio.create_task(build_code_graph_edges(
        graph_provider=repo.graph, org_id=repo.org_id, record_group_id=repo.group_id,
        facts_source=repo.blobs, fence=fence,
    ))
    try:
        await asyncio.wait_for(writing.wait(), timeout=60)
        for _ in range(200):   # until the write waits on the delete's lock
            rows = await repo.graph.client.execute_query(  # this build's write: an edge into this test's b.py
                "SHOW TRANSACTIONS YIELD status, currentQuery, parameters "
                "WHERE status STARTS WITH 'Blocked' AND currentQuery CONTAINS 'MERGE (from)' "
                "AND any(e IN coalesce(parameters.edges, []) WHERE e.to_key IN $into_b) "
                "RETURN count(*) AS blocked",
                parameters={"into_b": [rid, repo.block("src/shop/b.py", "function:target")]})
            if rows and rows[0]["blocked"]:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the build's write never waited on the delete")
    finally:
        await repo.graph.commit_transaction(delete)

    await asyncio.wait_for(build, timeout=60)
    into_b = [f"{RECORDS}/{rid}", repo.block_id("src/shop/b.py", "function:target")]
    assert await repo.graph.get_edges_by_target_keys(into_b, RELATIONS, raise_on_error=True) == []
    assert repo.block("src/shop/a.py", "function:caller") in await _outbound(
        repo, "src/shop/c.py", "function:run", RecordRelations.CALLS)


async def test_a_deleted_files_names_are_kept_and_served_once(repo: _Repo) -> None:
    """The sync point a delete leaves for the incremental build: found by the
    delete event's record, listed by the build's repo, removed when served."""
    rid = repo.record_ids["src/shop/b.py"]
    doc = await repo.graph.get_document(rid, RECORDS)
    assert doc is not None
    await deleted_files.remember(repo.graph, [{**doc, "_key": rid}], logger)
    try:
        assert await deleted_files.was_remembered(repo.graph, repo.org_id, rid)
        assert not (await deleted_files.pending(repo.graph, repo.org_id, repo.group_id)).keys, \
            "not served while the record is there"

        await repo.graph.delete_blocks_for_records([rid])
        await repo.graph.delete_nodes_and_edges([rid], CollectionNames.CODE_FILES.value)
        await repo.graph.delete_nodes_and_edges([rid], RECORDS)
        pending = await deleted_files.pending(repo.graph, repo.org_id, repo.group_id)
        assert len(pending.keys) == 1
        assert {"target", "helper", "module:shop/b"} <= pending.names
        result = await build_code_graph_edges(
            graph_provider=repo.graph, org_id=repo.org_id, record_group_id=repo.group_id,
            touched_record_ids=set(), deleted_names=pending.names, facts_source=repo.blobs,
        )
        assert result.files_reresolved >= 1, "the file that called into it"

        await deleted_files.forget(repo.graph, pending.keys, logging.getLogger("it"))
        assert not await deleted_files.was_remembered(repo.graph, repo.org_id, rid)
    finally:
        for key in (await deleted_files.pending(repo.graph, repo.org_id, repo.group_id)).keys:
            with contextlib.suppress(Exception):
                await repo.graph.remove_sync_point(key, CollectionNames.SYNC_POINTS.value)


# -- end to end: parse -> project -> build -> an agent tool's answer ---------


async def _grant(repo: _Repo, user_key: str, user_id: str, record_ids: list[str] | None = None) -> None:
    """A user who owns the repo's files (or just ``record_ids``) through the connector, as a sync leaves it."""
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
        [edge(rid, RECORDS, role="OWNER", type="USER") for rid in (repo.record_ids.values() if record_ids is None else record_ids)],
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


async def test_modifies_survives_a_pr_save_and_a_rewrite_keeps_one_edge(repo: _Repo) -> None:
    """A PR's MODIFIES edges are its own -- the processor's link rewrite on every PR save leaves them --
    and re-indexing the PR replaces them instead of adding a second set."""
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_processor.pr_links import write_modifies
    from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
    from app.models.blocks import FileChangeMetadata
    from app.models.entities import PullRequestRecord

    pr = PullRequestRecord(
        id=str(uuid.uuid4()), org_id=repo.org_id, record_name="Speed up target",
        record_type=RecordType.PULL_REQUEST, external_record_id=f"pr-{uuid.uuid4().hex[:8]}", version=1,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB, connector_id=repo.connector_id,
        mime_type="text/markdown", merged_at=1_700,
    )
    await repo.graph.batch_upsert_records([pr])
    store = GraphDataStore(logger, repo.graph)
    target = repo.record_ids["src/shop/b.py"]
    changes = [FileChangeMetadata(path="src/shop/b.py", status="modified", additions=2, deletions=1),
               FileChangeMetadata(path="docs/none.md", status="added")]

    async def prs_of_target() -> list[str]:
        rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
            node_keys=[target], node_collection=RECORDS, relationship_types=[RecordRelations.MODIFIES.value],
            direction="inbound", raise_on_error=True)
        return [r["key"] for r in rows]

    try:
        for _ in range(2):  # indexed, then re-indexed
            async with store.transaction() as tx:
                assert await write_modifies(tx, pr, changes,
                                            lambda path: f"ext-{repo.record_ids.get(path)}") == (1, 1)
        assert await prs_of_target() == [pr.id]
        async with store.transaction() as tx:  # what every save of the PR does to its link edges
            await tx.delete_edges_by_relationship_types(
                pr.id, RECORDS, CollectionNames.NODE_RELATIONS.value,
                list(DataSourceEntitiesProcessor.LINK_RELATION_TYPES))
        assert await prs_of_target() == [pr.id]
    finally:
        with contextlib.suppress(Exception):
            await repo.graph.delete_nodes_and_edges([pr.id], RECORDS)


async def _a_failed_rewrite_keeps_the_old_links(repo: _Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR linked to a.py is re-indexed with b.py as its only change, and the write fails after the delete: the
    processor's rewrite raises and the PR keeps exactly its link to a.py, none to b.py."""
    from types import SimpleNamespace

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.data_processor.pr_links import write_modifies
    from app.connectors.core.base.data_store.graph_data_store import (
        GraphDataStore,
        GraphTransactionStore,
    )
    from app.models.blocks import FileChangeMetadata
    from app.models.entities import PullRequestRecord

    pr = PullRequestRecord(
        id=str(uuid.uuid4()), org_id=repo.org_id, record_name="Rewrite that fails",
        record_type=RecordType.PULL_REQUEST, external_record_id=f"pr-{uuid.uuid4().hex[:8]}", version=1,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB, connector_id=repo.connector_id,
        mime_type="text/markdown",
    )
    await repo.graph.batch_upsert_records([pr])
    store = GraphDataStore(logger, repo.graph)

    def ext(path: str) -> str:
        return f"ext-{repo.record_ids.get(path)}"

    a, b = repo.record_ids["src/shop/a.py"], repo.record_ids["src/shop/b.py"]

    async def linked() -> set[str]:
        rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
            node_keys=[pr.id], node_collection=RECORDS, relationship_types=[RecordRelations.MODIFIES.value],
            direction="outbound", raise_on_error=True)
        return {r["key"] for r in rows}

    try:
        async with store.transaction() as tx:
            assert await write_modifies(tx, pr, [FileChangeMetadata(path="src/shop/a.py")], ext) == (1, 0)
        assert await linked() == {a}

        async def write_fails(self, edges, collection) -> None:
            raise RuntimeError("injected edge-write failure")

        monkeypatch.setattr(GraphTransactionStore, "batch_create_edges", write_fails)
        processor = SimpleNamespace(data_store_provider=store)
        with pytest.raises(RuntimeError, match="injected"):
            await DataSourceEntitiesProcessor.on_pr_file_changes(
                processor, pr, [FileChangeMetadata(path="src/shop/b.py")], ext)
        assert await linked() == {a}, "the delete must roll back with the failed write"
        assert b not in await linked()
    finally:
        with contextlib.suppress(Exception):
            await repo.graph.delete_nodes_and_edges([pr.id], RECORDS)


async def test_a_failed_modifies_rewrite_rolls_back_and_keeps_the_old_links(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _a_failed_rewrite_keeps_the_old_links(repo, monkeypatch)


# Neo4j only: auto-commit is a Neo4j setting (ArangoDB transactions are always real).
@pytest.mark.parametrize("repo", ["neo4j"], indirect=True)
async def test_a_failed_modifies_rewrite_rolls_back_with_neo4j_auto_commit_on(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With NEO4J_EXPLICIT_TRANSACTIONS false each statement commits on its own, so only the rewrite's real
    transaction (explicit=True) keeps the delete from landing without the write."""
    monkeypatch.setattr(repo.graph.client, "_explicit_transactions", False)
    await _a_failed_rewrite_keeps_the_old_links(repo, monkeypatch)


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


# -- C# and PHP resolve through the same provider methods ---------------------

CS_PHP_FILES = {
    "cs/Data/IRepo.cs": "namespace Shop.Data;\npublic interface IRepo { void Save(); }\n",
    "cs/Data/Repo.cs": "namespace Shop.Data;\npublic class Repo : IRepo { public void Save() {} }\n",
    "cs/Data/RepoExt.cs": "namespace Shop.Data;\npublic static class RepoExt { public static void Touch(this Repo r) {} }\n",
    "cs/App/Svc.cs": "using Shop.Data;\nnamespace Shop.App;\n"
                     "public class Svc { public void Run(Repo repo) { repo.Save(); repo.Touch(); } }\n",
    "php/Models/Model.php": "<?php\nnamespace App\\Models;\nclass Model { public function boot() {} }\n",
    "php/Models/User.php": "<?php\nnamespace App\\Models;\nclass User extends Model { public function save() {} }\n",
    "php/Http/UserCtl.php": "<?php\nnamespace App\\Http;\nuse App\\Models\\User;\n"
                            "class UserCtl { public function show(User $u) { $u->save(); $u->boot(); } }\n",
}


@pytest.fixture(params=["neo4j", "arango"])
async def cs_php_repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                      ) -> AsyncIterator[_Repo]:
    async with _connected_repo(request, monkeypatch, CS_PHP_FILES) as built:
        yield built


async def _outbound(repo: _Repo, path: str, qualified_name: str, relation: RecordRelations) -> set[str]:
    rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=[repo.block(path, qualified_name)], node_collection=BLOCKS,
        relationship_types=[relation.value], direction="outbound", raise_on_error=True,
    )
    return {r["key"] for r in rows}


async def test_csharp_namespaces_extension_methods_and_interfaces_come_back(cs_php_repo: _Repo) -> None:
    repo = cs_php_repo
    await _build(repo)
    assert await _outbound(repo, "cs/App/Svc.cs", "method:Shop.App.Svc.Run", RecordRelations.CALLS) == {
        repo.block("cs/Data/Repo.cs", "method:Shop.Data.Repo.Save"),
        repo.block("cs/Data/RepoExt.cs", "method:Shop.Data.RepoExt.Touch"),
    }
    assert await _outbound(repo, "cs/Data/Repo.cs", "class:Shop.Data.Repo", RecordRelations.IMPLEMENTS) == {
        repo.block("cs/Data/IRepo.cs", "interface:Shop.Data.IRepo")}
    assert await _outbound(repo, "cs/Data/Repo.cs", "method:Shop.Data.Repo.Save", RecordRelations.OVERRIDES) == {
        repo.block("cs/Data/IRepo.cs", "method:Shop.Data.IRepo.Save")}


async def test_php_use_and_inherited_methods_come_back(cs_php_repo: _Repo) -> None:
    repo = cs_php_repo
    await _build(repo)
    assert await _outbound(repo, "php/Http/UserCtl.php", "method:App.Http.UserCtl.show", RecordRelations.CALLS) == {
        repo.block("php/Models/User.php", "method:App.Models.User.save"),
        repo.block("php/Models/Model.php", "method:App.Models.Model.boot"),
    }
    assert await _outbound(repo, "php/Models/User.php", "class:App.Models.User", RecordRelations.INHERITS) == {
        repo.block("php/Models/Model.php", "class:App.Models.Model")}
