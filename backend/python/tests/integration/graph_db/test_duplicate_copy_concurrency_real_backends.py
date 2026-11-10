"""Duplicates indexed at the same time all get their taxonomy copied.

When indexing finds a duplicate it copies the original's category and topic
edges onto it (copy_document_relationships). Those edges end on hub nodes that
many records share. On Neo4j two copies that reached the same hubs in a
different order deadlocked (Neo.TransientError.Transaction.DeadlockDetected),
the copy returned False and the duplicate was marked FAILED. ArangoDB runs the
same case.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
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

ARANGO_DB = "duplicate_copy_concurrency_it"
RECORDS = CollectionNames.RECORDS.value
TOPICS = CollectionNames.TOPICS.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
HUBS = 40
COPIES = 24

logger = logging.getLogger("duplicate-copy-concurrency-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]

        async def remove() -> None:
            with contextlib.suppress(Exception):
                if isinstance(provider, Neo4jProvider):
                    await provider.client.execute_query(
                        "MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": run},
                    )
                    return
                await provider.http_client.execute_aql(
                    f"FOR e IN {BELONGS_TO_TOPIC} FILTER CONTAINS(e._from, @run) REMOVE e IN {BELONGS_TO_TOPIC}",
                    {"run": run},
                )
                for collection in (RECORDS, TOPICS):
                    await provider.http_client.execute_aql(
                        f"FOR d IN {collection} FILTER CONTAINS(d._key, @run) REMOVE d IN {collection}",
                        {"run": run},
                    )

        cleanup.push_async_callback(remove)
        yield provider, run


async def _records(graph: IGraphDBProvider, keys: list[str], org_id: str) -> None:
    assert await graph.batch_upsert_nodes(
        [{
            "id": key, "orgId": org_id, "recordName": key, "externalRecordId": f"ext-{key}",
            "recordType": "FILE", "origin": "CONNECTOR", "connectorName": "WEB",
            "connectorId": f"conn-{org_id}", "version": 0, "virtualRecordId": f"vr-{key}",
            "isDeleted": False, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        } for key in keys],
        collection=RECORDS,
    )


async def test_concurrent_duplicate_copies_onto_shared_hubs_all_succeed(graph) -> None:
    provider, run = graph
    org_id = f"org-{run}"
    hubs = [f"topic-{i:02d}-{run}" for i in range(HUBS)]
    for i, hub in enumerate(hubs):
        await provider.create_taxonomy_node_if_absent(
            TOPICS, {"id": hub, "name": f"Topic {i}", "normalizedName": f"topic {i}", "orgId": org_id,
                     "createdAtTimestamp": 1},
        )
    sources = [f"src-{i:02d}-{run}" for i in range(COPIES)]
    targets = [f"dup-{i:02d}-{run}" for i in range(COPIES)]
    await _records(provider, sources + targets, org_id)
    # Every other original reaches the hubs in the opposite order, so the
    # copies would lock them in opposite orders.
    for i, source in enumerate(sources):
        await provider.batch_create_edges(
            [{"from_id": source, "from_collection": RECORDS, "to_id": hub, "to_collection": TOPICS,
              "createdAtTimestamp": 1} for hub in (hubs if i % 2 else hubs[::-1])],
            collection=BELONGS_TO_TOPIC,
        )

    results = await asyncio.gather(*(
        provider.copy_document_relationships(source, target) for source, target in zip(sources, targets)
    ))

    failed = [target for target, ok in zip(targets, results) if not ok]
    assert not failed, f"{len(failed)} of {COPIES} concurrent copies failed"
    links = await provider.get_record_taxonomy_links(targets)
    copied: dict[str, set[str]] = {}
    for link in links:
        copied.setdefault(link["recordId"], set()).add(link["entityId"])
    assert copied == {target: set(hubs) for target in targets}, "every duplicate carries every hub"
