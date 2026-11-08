"""Go struct embedding end to end on real Neo4j and ArangoDB.

A small Go package is written the way production writes it (code file records, block projection, stored facts)
and built by the real edge builder; the call a promoted method gets comes back through the provider on both
backends, and embedding writes no heritage edge.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.config.constants.arangodb import (
    Connectors,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.models.entities import CodeFileRecord, RecordType
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.parsers.code_parser import CodeFileParser
from tests.integration.graph_db import test_code_graph_real_backends as base
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

CALLS, INHERITS = RecordRelations.CALLS.value, RecordRelations.INHERITS.value

FILES = {
    "gin/group.go": ("go", "package gin\n\ntype RouterGroup struct{}\n\nfunc (g *RouterGroup) GET(path string) {}\n"),
    "gin/engine.go": ("go", "package gin\n\ntype Engine struct {\n\tRouterGroup\n}\n\nfunc serve(e *Engine) {\n"
                            "\te.GET(\"/\")\n}\n"),
}


async def _write(graph, org_id: str, connector_id: str, group_id: str) -> tuple[dict[str, str], base._Blobs]:
    parser, blobs = CodeFileParser(), base._Blobs()
    record_ids = {path: str(uuid.uuid4()) for path in FILES}
    await graph.batch_upsert_records([
        CodeFileRecord(
            id=record_ids[path], org_id=org_id, record_name=path.rsplit("/", 1)[-1],
            record_type=RecordType.CODE_FILE, external_record_id=f"ext-{record_ids[path]}",
            version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB,
            connector_id=connector_id, mime_type="text/plain", extension=path.rsplit(".", 1)[-1],
            file_path=path, file_hash=uuid.uuid4().hex, language=language,
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        for path, (language, _) in FILES.items()
    ])
    for path, (language, source) in FILES.items():
        rid = record_ids[path]
        await graph.update_node(rid, base.RECORDS, {"recordGroupId": group_id})
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, language)
        blobs.records[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=org_id, record_id=rid, record_group_id=group_id,
                                           connector_id=connector_id, language=language, file_path=path),
            block_containers=container,
        )
    return record_ids, blobs


@pytest.fixture(params=["neo4j", "arango"])
async def repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(base.logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(base.logger, base.ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        org_id, connector_id, group_id = f"org-cgg-{run}", f"conn-cgg-{run}", f"g-cgg-{run}"
        record_ids, blobs = await _write(graph, org_id, connector_id, group_id)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(record_ids.values()))
            with contextlib.suppress(Exception):
                for collection in (base.CollectionNames.CODE_FILES.value, base.RECORDS):
                    await graph.delete_nodes_and_edges(list(record_ids.values()), collection)

        cleanup.push_async_callback(remove)
        yield base._Repo(graph, request.param, org_id, connector_id, group_id, record_ids, blobs)


async def _out(repo: base._Repo, key: str, relation: str) -> set[str]:
    rows = await repo.graph.get_neighbors_for_nodes_by_relationship_types(
        node_keys=[key], node_collection=base.BLOCKS, relationship_types=[relation], direction="outbound",
        raise_on_error=True)
    return {r["key"] for r in rows}


async def test_a_promoted_method_call_comes_back_through_the_provider(repo: base._Repo) -> None:
    await base._build(repo)
    serve = repo.block("gin/engine.go", "function:serve")
    assert await _out(repo, serve, CALLS) == {repo.block("gin/group.go", "method:RouterGroup.GET")}
    assert await _out(repo, repo.block("gin/engine.go", "struct:Engine"), INHERITS) == set()
