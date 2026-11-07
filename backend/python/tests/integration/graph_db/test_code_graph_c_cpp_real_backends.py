"""C and C++ code-graph edges through the real Neo4j and ArangoDB providers.

A small C/C++ repo is written the way production writes it (code file records, block projection, then the
real edge builder), and the edges C/C++ resolution adds are read back through the provider on both backends:
an `#include` resolved to a repo header, a C++ base class, an out-of-line method reached through a typed
receiver, and a C call through a wrapper macro. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
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
from app.modules.parsers.code_parser.lang_config import detect_language
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

ARANGO_DB = "code_graph_c_cpp_it"
BLOCKS = CollectionNames.BLOCKS.value
RECORDS = CollectionNames.RECORDS.value
RELATIONS = CollectionNames.RECORD_LINKS.value

FILES = {
    "include/net/conn.hpp": (
        "#pragma once\nclass Base {\n public:\n  void ping() {}\n};\n"
        "class Conn : public Base {\n public:\n  void open();\n};\n"
    ),
    "src/net/conn.cpp": '#include "net/conn.hpp"\nvoid Conn::open() { ping(); }\n',
    "src/app/main.cpp": '#include "net/conn.hpp"\nint main() {\n  Conn c;\n  c.open();\n  return 0;\n}\n',
    "server/log.h": "void _serverLog(int level);\n#define serverLog(level) _serverLog(level)\n",
    "server/log.c": '#include "log.h"\nvoid _serverLog(int level) {}\n',
    "server/server.c": '#include "log.h"\nvoid run(void) { serverLog(1); }\n',
}

logger = logging.getLogger("code-graph-c-cpp-real-backends-it")


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
            connector_id=connector_id, mime_type="text/x-c", extension=path.rsplit(".", 1)[-1],
            file_path=path, file_hash=uuid.uuid4().hex, language=detect_language(path),
            indexing_status=ProgressStatus.COMPLETED.value,
        )
        for path in FILES
    ])
    for path, rid in record_ids.items():
        await graph.update_node(rid, RECORDS, {"recordGroupId": group_id})
        container = parser.parse_to_blocks(FILES[path].encode(), path.rsplit("/", 1)[-1], path, detect_language(path))
        blobs.records[rid] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=org_id, record_id=rid, record_group_id=group_id,
                                           connector_id=connector_id, language=detect_language(path),
                                           file_path=path),
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
        org_id, group_id = f"org-c-{run}", f"g-c-{run}"
        record_ids, blobs = await _write_repo(graph, org_id, f"conn-c-{run}", group_id)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_blocks_for_records(list(record_ids.values()))
            with contextlib.suppress(Exception):
                for collection in (CollectionNames.CODE_FILES.value, RECORDS):
                    await graph.delete_nodes_and_edges(list(record_ids.values()), collection)

        cleanup.push_async_callback(remove)
        result = await build_code_graph_edges(graph_provider=graph, org_id=org_id, record_group_id=group_id,
                                              touched_record_ids=None, facts_source=blobs)
        assert result.edges_written >= 5
        yield graph, record_ids


async def _edges_from(graph: IGraphDBProvider, node_id: str) -> set[tuple[str, str]]:
    """(relation, target id) of every code-graph edge leaving *node_id* (`blocks/<key>` or `records/<key>`)."""
    edges = await graph.get_edges_from_node(node_id, RELATIONS, raise_on_error=True) or []
    return {(e.get("relationshipType"), e.get("_to") or e.get("to_id") or "") for e in edges
            if e.get("source") == CODE_GRAPH_SOURCE}


async def _file_edges(graph: IGraphDBProvider, record_id: str) -> set[tuple[str, str]]:
    """Every code-graph edge leaving the file: an `#include` is sourced from the block holding it."""
    blocks = await graph.get_nodes_by_filters(collection=BLOCKS, filters={"recordId": record_id},
                                              return_fields=["_key"]) or []
    out: set[tuple[str, str]] = set()
    for node_id in (f"{RECORDS}/{record_id}", *(f"{BLOCKS}/{b['_key']}" for b in blocks)):
        out |= await _edges_from(graph, node_id)
    return out


def _block(record_ids: dict[str, str], path: str, qualified_name: str) -> str:
    return f"{BLOCKS}/{block_key_for(record_ids[path], qualified_name)}"


async def test_c_cpp_includes_bases_typed_and_wrapper_calls_come_back_through_the_provider(built) -> None:
    graph, ids = built
    header, source = "include/net/conn.hpp", "src/net/conn.cpp"
    main = await _edges_from(graph, _block(ids, "src/app/main.cpp", "function:main"))
    assert (RecordRelations.CALLS.value, _block(ids, source, "method:Conn.open")) in main

    includes = await _file_edges(graph, ids["src/app/main.cpp"])
    assert (RecordRelations.IMPORTS_FROM.value, f"{RECORDS}/{ids[header]}") in includes

    heritage = await _edges_from(graph, _block(ids, header, "class:Conn"))
    assert (RecordRelations.INHERITS.value, _block(ids, header, "class:Base")) in heritage
    # An out-of-line method's bare call is `this->ping()`, the base's method.
    opened = await _edges_from(graph, _block(ids, source, "method:Conn.open"))
    assert (RecordRelations.CALLS.value, _block(ids, header, "method:Base.ping")) in opened

    run = await _edges_from(graph, _block(ids, "server/server.c", "function:run"))
    assert (RecordRelations.CALLS.value, _block(ids, "server/log.c", "function:_serverLog")) in run
