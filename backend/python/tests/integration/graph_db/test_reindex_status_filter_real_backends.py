"""Reindexing a folder's records by status lists exactly the records that match.

The reindex consumer walks a folder with get_records_by_parent_record. On Neo4j,
a status filter that matched nothing came back as one row with a null record;
reading it raised, and the error was logged and turned into an empty list, so a
real failure there looked the same as "nothing matched".

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
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
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "reindex_status_filter_it"
RECORDS = CollectionNames.RECORDS.value
NODE_RELATIONS = CollectionNames.NODE_RELATIONS.value
FILES = CollectionNames.FILES.value

logger = logging.getLogger("reindex-status-filter-it")


class _Folder:
    def __init__(self, graph: IGraphDBProvider, run: str) -> None:
        self.graph = graph
        self.org_id = f"org-{run}"
        self.connector_id = f"conn-{run}"
        self.parent = f"folder-{run}"
        self.children = [f"file-a-{run}", f"file-b-{run}"]


@pytest.fixture(params=["neo4j", "arango"])
async def folder(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Folder]:
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
        tree = _Folder(graph, run)

        async def remove() -> None:
            with contextlib.suppress(Exception):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query(
                        "MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": run},
                    )
                    return
                keys = [tree.parent, *tree.children]
                await graph.delete_nodes_and_edges(keys, RECORDS)
                await graph.delete_nodes_and_edges(keys, FILES)

        cleanup.push_async_callback(remove)
        for key in (tree.parent, *tree.children):
            record = FileRecord(
                id=key, org_id=tree.org_id, record_name=key, record_type=RecordType.FILE,
                external_record_id=f"ext-{key}", version=0, origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.WEB, connector_id=tree.connector_id, mime_type="text/html",
                is_file=key != tree.parent, indexing_status=ProgressStatus.COMPLETED.value,
            )
            assert await graph.batch_upsert_nodes([record.to_arango_base_record()], RECORDS)
            assert await graph.batch_upsert_nodes([record.to_arango_record()], FILES)
            assert await graph.batch_create_edges(
                [{"from_id": key, "from_collection": RECORDS, "to_id": key, "to_collection": FILES,
                  "createdAtTimestamp": 1}],
                collection=CollectionNames.IS_OF_TYPE.value,
            )
        await graph.batch_create_edges(
            [{"from_id": tree.parent, "from_collection": RECORDS, "to_id": child, "to_collection": RECORDS,
              "relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1} for child in tree.children],
            collection=NODE_RELATIONS,
        )
        yield tree


async def _below(tree: _Folder, statuses: list[str]) -> list[str]:
    records = await tree.graph.get_records_by_parent_record(
        parent_record_id=tree.parent, connector_id=tree.connector_id, org_id=tree.org_id, depth=-1,
        status_filters=statuses,
    )
    return sorted(r.id for r in records)


async def test_a_filter_that_matches_nothing_lists_nothing_without_an_error(folder, caplog) -> None:
    with caplog.at_level(logging.ERROR):
        below = await _below(folder, [ProgressStatus.FAILED.value])

    assert below == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR and r.name != "asyncio"]
    assert not errors, f"an empty match is not an error: {errors}"


async def test_a_filter_lists_only_the_matching_records(folder) -> None:
    assert await folder.graph.update_node(
        folder.children[0], RECORDS, {"indexingStatus": ProgressStatus.FAILED.value},
    )

    assert await _below(folder, [ProgressStatus.FAILED.value]) == [folder.children[0]]
    assert await _below(folder, [ProgressStatus.COMPLETED.value]) == sorted([folder.parent, folder.children[1]])
