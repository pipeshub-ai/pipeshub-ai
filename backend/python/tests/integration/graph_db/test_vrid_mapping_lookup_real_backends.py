"""``get_document_id_by_virtual_record_id`` against a real Neo4j and a real ArangoDB.

It runs for every indexed record and every unbatched fetch, so both of its
queries must be anchored: the keyed lookup on the mapping's unique key, and the
legacy ``virtualRecordId`` fallback (taken by every record indexed the first
time) on an index rather than a scan of every mapping node.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "vrid_mapping_lookup_it"
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
LABEL = collection_to_label(MAPPING)

logger = logging.getLogger("vrid-mapping-lookup-it")


def _current(vrid: str, doc_id: str) -> dict:
    """As a stored write's switch leaves it: keyed by the vrid."""
    return {"id": vrid, "orgId": "org-it", "virtualRecordId": vrid, "documentId": doc_id,
            "record_doc_id": doc_id, "fileSizeBytes": 7, "updatedAt": 1}


def _legacy(vrid: str, doc_id: str) -> dict:
    """As releases before the key change wrote it: keyed by vrid and document."""
    return {"id": f"{vrid}_{doc_id}", "virtualRecordId": vrid, "documentId": doc_id, "createdAt": 1}


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
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
        if isinstance(provider, Neo4jProvider):
            # The planner only uses an index once it is ONLINE.
            await provider.client.execute_query("CALL db.awaitIndexes(120)")
        run = uuid.uuid4().hex[:10]
        yield provider, run
        if isinstance(provider, Neo4jProvider):
            await provider.client.execute_query(
                f"MATCH (n:{LABEL}) WHERE n.id STARTS WITH $p DETACH DELETE n", parameters={"p": f"{run}-"},
            )
        else:
            await provider.http_client.execute_aql(
                f"FOR n IN {MAPPING} FILTER STARTS_WITH(n._key, @p) REMOVE n IN {MAPPING}", {"p": f"{run}-"},
            )


def _blob_storage(provider: IGraphDBProvider) -> BlobStorage:
    return BlobStorage(logger=logger, config_service=MagicMock(), graph_provider=provider)


@pytest.mark.asyncio
async def test_resolves_current_legacy_and_missing_mappings(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    current, legacy, both, stubbed, missing = (
        f"{run}-{name}" for name in ("current", "legacy", "both", "stubbed", "missing")
    )
    assert await provider.batch_upsert_nodes(
        [_current(current, "doc-current"), _legacy(legacy, "doc-legacy"),
         _legacy(both, "doc-old"), _current(both, "doc-new"),
         # What save_reconciliation_metadata writes when the keyed read misses.
         {"id": stubbed, "record_metadata_doc_id": "meta", "updatedAt": 2},
         _legacy(stubbed, "doc-stubbed")],
        MAPPING,
    )
    lookup = _blob_storage(provider).get_document_id_by_virtual_record_id

    assert (await lookup(current))["record_doc_id"] == "doc-current"
    assert (await lookup(legacy))["record_doc_id"] == "doc-legacy"
    assert (await lookup(both))["record_doc_id"] == "doc-new"
    assert (await lookup(stubbed))["record_doc_id"] == "doc-stubbed"
    assert await lookup(missing) is None


def _neo4j_operators(plan: dict[str, Any]) -> list[str]:
    ops = [plan["operatorType"].split("@")[0]]
    for child in plan.get("children", []):
        ops.extend(_neo4j_operators(child))
    return ops


async def _neo4j_plan(provider: Neo4jProvider, query: str, parameters: dict) -> list[str]:
    async with provider.client.driver.session(database=provider.client.database) as session:
        result = await session.run(f"EXPLAIN {query}", parameters)
        summary = await result.consume()
    return _neo4j_operators(summary.plan)


async def _arango_plan_nodes(provider: ArangoHTTPProvider, query: str, bind_vars: dict) -> list[dict]:
    client = provider.http_client
    session = await client._get_session()
    url = f"{client.base_url}/_db/{client.database}/_api/explain"
    async with session.post(url, json={"query": query, "bindVars": bind_vars}) as resp:
        assert resp.status == 200, await resp.text()
        body = await resp.json()
    return body["plan"]["nodes"]


@pytest.mark.asyncio
async def test_both_lookups_are_anchored(graph: tuple[IGraphDBProvider, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Explain the exact queries the provider sends for a vrid with no mapping,
    which takes the keyed lookup and then the field fallback."""
    provider, run = graph
    vrid = f"{run}-absent"

    if isinstance(provider, Neo4jProvider):
        sent: list[tuple[str, dict]] = []
        execute = provider.client.execute_query

        async def record(query: str, parameters: dict | None = None, **kwargs: object) -> object:
            sent.append((query, parameters or {}))
            return await execute(query, parameters=parameters, **kwargs)

        monkeypatch.setattr(provider.client, "execute_query", record)
        assert await _blob_storage(provider).get_document_id_by_virtual_record_id(vrid) is None
        monkeypatch.undo()

        assert len(sent) == 2, sent
        keyed, by_field = [await _neo4j_plan(provider, q, p) for q, p in sent]
        assert "NodeUniqueIndexSeek" in keyed, keyed
        assert "NodeIndexSeek" in by_field, by_field
        for plan in (keyed, by_field):
            assert "NodeByLabelScan" not in plan and "AllNodesScan" not in plan, plan
        return

    sent_aql: list[tuple[str, dict]] = []
    execute_aql = provider.http_client.execute_aql

    async def record_aql(query: str, bind_vars: dict | None = None, **kwargs: object) -> object:
        sent_aql.append((query, bind_vars or {}))
        return await execute_aql(query, bind_vars=bind_vars, **kwargs)

    get_document = provider.http_client.get_document
    keyed_reads: list[tuple[str, str]] = []

    async def record_get(collection: str, key: str, **kwargs: object) -> object:
        keyed_reads.append((collection, key))
        return await get_document(collection, key, **kwargs)

    monkeypatch.setattr(provider.http_client, "execute_aql", record_aql)
    monkeypatch.setattr(provider.http_client, "get_document", record_get)
    assert await _blob_storage(provider).get_document_id_by_virtual_record_id(vrid) is None
    monkeypatch.undo()

    # The keyed read is the document API's primary-key GET, not a query.
    assert keyed_reads == [(MAPPING, vrid)]
    assert len(sent_aql) == 1, sent_aql
    nodes = await _arango_plan_nodes(provider, *sent_aql[0])
    types = [n["type"] for n in nodes]
    assert "EnumerateCollectionNode" not in types, types
    index_nodes = [n for n in nodes if n["type"] == "IndexNode"]
    assert any(
        ix["type"] == "persistent" and ix["fields"] == ["virtualRecordId"]
        for n in index_nodes for ix in n["indexes"]
    ), index_nodes
