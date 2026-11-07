"""Rust code-graph edges through the real Neo4j and ArangoDB providers.

A two-crate Rust repo is written the way production writes it (code file records, block projection, then the
real edge builder), and the edges Rust resolution adds are read back through the provider on both backends:
`use` imports across crates, an impl's heritage on its type, calls through a typed receiver chain and an
associated function. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

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
from app.modules.code_graph.edge_builder import (
    CODE_GRAPH_SOURCE,
    build_code_graph_edges,
)
from app.modules.code_graph.facts_source import facts_from_blob_record
from app.modules.parsers.code_parser import CodeFileParser
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

ARANGO_DB = "code_graph_rust_it"
BLOCKS = CollectionNames.BLOCKS.value
RECORDS = CollectionNames.RECORDS.value
RELATIONS = CollectionNames.RECORD_LINKS.value

FILES = {
    "kernel/src/lib.rs": "pub mod store;\npub mod engine;\n",
    "kernel/src/store.rs": "pub trait Store {\n    fn save(&self);\n}\n",
    "kernel/src/engine.rs": (
        "use crate::store::Store;\n\n"
        "pub struct Config {}\n\nimpl Config {\n    pub fn load(&self) -> Config { Config {} }\n}\n\n"
        "pub struct Engine {\n    cfg: Config,\n}\n\n"
        "impl Engine {\n    pub fn new() -> Self { Engine { cfg: Config {} } }\n"
        "    pub fn run(&self) { self.cfg.load(); }\n}\n\n"
        "impl Store for Engine {\n    fn save(&self) {}\n}\n"
    ),
    "app/src/main.rs": "use kernel::engine::Engine;\n\nfn main() {\n    let e = Engine::new();\n    e.run();\n}\n",
}

logger = logging.getLogger("code-graph-rust-real-backends-it")


class _Blobs:
    """The facts the indexing pipeline uploads, from the same parse as the blocks."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    async def load(self, org_id: str, record_ids: list[str]) -> dict[str, RecordFacts]:
        return {rid: facts_from_blob_record(rid, self.records[rid]) for rid in record_ids if rid in self.records}


async def _write_repo(graph: IGraphDBProvider, org_id: str, connector_id: str,
                      group_id: str) -> tuple[dict[str, str], _Blobs]:
    parser, blobs = CodeFileParser(), _Blobs()
    record_ids = {path: str(uuid.uuid4()) for path in FILES}
    await graph.batch_upsert_records([
        CodeFileRecord(
            id=record_ids[path], org_id=org_id, record_name=path.rsplit("/", 1)[-1],
            record_type=RecordType.CODE_FILE, external_record_id=f"ext-{record_ids[path]}",
            version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GITHUB,
            connector_id=connector_id, mime_type="text/x-rust", extension="rs",
            file_path=path, file_hash=uuid.uuid4().hex, language="rust",
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        for path in FILES
    ])
    for path, rid in record_ids.items():
        await graph.update_node(rid, RECORDS, {"recordGroupId": group_id})
        container = parser.parse_to_blocks(FILES[path].encode(), path.rsplit("/", 1)[-1], path, "rust")
        blobs.records[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=org_id, record_id=rid, record_group_id=group_id,
                                           connector_id=connector_id, language="rust", file_path=path),
            block_containers=container,
        )
    return record_ids, blobs


@pytest.fixture(params=["neo4j", "arango"])
async def built(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                ) -> AsyncIterator[tuple[IGraphDBProvider, dict[str, str]]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        org_id, group_id = f"org-rs-{run}", f"g-rs-{run}"
        record_ids, blobs = await _write_repo(graph, org_id, f"conn-rs-{run}", group_id)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(record_ids.values()))
            with contextlib.suppress(Exception):
                for collection in (CollectionNames.CODE_FILES.value, RECORDS):
                    await graph.delete_nodes_and_edges(list(record_ids.values()), collection)

        cleanup.push_async_callback(remove)
        result = await build_code_graph_edges(graph_provider=graph, org_id=org_id, record_group_id=group_id,
                                              touched_record_ids=None, facts_source=blobs)
        assert result.edges_written >= 6
        yield graph, record_ids


async def _edges_from(graph: IGraphDBProvider, node_id: str) -> set[tuple[str, str]]:
    """(relation, target id) of every code-graph edge leaving *node_id* (`blocks/<key>` or `records/<key>`)."""
    edges = await graph.get_edges_from_node(node_id, RELATIONS, raise_on_error=True) or []
    return {(e.get("relationshipType"), e.get("_to") or e.get("to_id") or "") for e in edges
            if e.get("source") == CODE_GRAPH_SOURCE}


def _block(record_ids: dict[str, str], path: str, qualified_name: str) -> str:
    return f"{BLOCKS}/{block_key_for(record_ids[path], qualified_name)}"


async def test_rust_imports_heritage_and_typed_calls_come_back_through_the_provider(built) -> None:
    graph, ids = built
    engine = "kernel/src/engine.rs"
    main = await _edges_from(graph, _block(ids, "app/src/main.rs", "function:main"))
    assert {(RecordRelations.CALLS.value, _block(ids, engine, "method:Engine.new")),
            (RecordRelations.CALLS.value, _block(ids, engine, "method:Engine.run"))} <= main

    file_edges = await _edges_from(graph, f"{RECORDS}/{ids['app/src/main.rs']}")
    assert (RecordRelations.IMPORTS_FROM.value, f"{RECORDS}/{ids[engine]}") in file_edges
    assert (RecordRelations.IMPORTS.value, _block(ids, engine, "struct:Engine")) in file_edges

    run = await _edges_from(graph, _block(ids, engine, "method:Engine.run"))
    assert (RecordRelations.CALLS.value, _block(ids, engine, "method:Config.load")) in run
    heritage = await _edges_from(graph, _block(ids, engine, "struct:Engine"))
    assert (RecordRelations.IMPLEMENTS.value, _block(ids, "kernel/src/store.rs", "trait:Store")) in heritage
