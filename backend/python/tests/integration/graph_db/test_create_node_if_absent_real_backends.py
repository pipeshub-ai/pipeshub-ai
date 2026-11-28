"""`create_node_if_absent` takes the generic node format on every real graph store.

Callers pass a node with the generic `id` field. ArangoDB keeps the id as `_key`, and a
collection with a strict schema (the skills collection) refuses a document that also
carries an `id` attribute, so the provider must translate the node as `batch_upsert_nodes`
does. A collection with no schema (the virtual-record mapping) takes the same call.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
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

ARANGO_DB = "create_if_absent_it"
SKILLS = CollectionNames.AGENT_SKILLS.value
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
logger = logging.getLogger("create-if-absent-it")


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
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
        run = f"cia-{uuid.uuid4().hex[:10]}"
        yield provider, run
        for collection in (SKILLS, MAPPING):
            if isinstance(provider, Neo4jProvider):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(collection)}) WHERE n.id STARTS WITH $p DETACH DELETE n",
                    parameters={"p": run},
                )
            else:
                await provider.http_client.execute_aql(
                    f"FOR n IN {collection} FILTER STARTS_WITH(n._key, @p) REMOVE n IN {collection}", {"p": run},
                )


def _skill(run: str, key: str, owner: str) -> dict:
    return {
        "id": key, "orgId": run, "name": "deploy", "description": "d", "content": f"body of {owner}",
        "status": "active", "source": "manual", "version": "1.0.0", "createdBy": owner,
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }


async def test_a_document_with_a_generic_id_lands_on_a_strict_collection(graph) -> None:
    provider, run = graph
    key = f"{run}_deploy"

    assert await provider.create_node_if_absent(key, SKILLS, _skill(run, key, "user-a")) is True

    stored = await provider.get_document(key, SKILLS)
    assert stored is not None and stored["createdBy"] == "user-a"
    assert (stored.get("id") or stored.get("_key")) == key


async def test_a_second_create_leaves_the_first_document_alone(graph) -> None:
    provider, run = graph
    key = f"{run}_deploy"
    assert await provider.create_node_if_absent(key, SKILLS, _skill(run, key, "user-a")) is True

    assert await provider.create_node_if_absent(key, SKILLS, _skill(run, key, "user-b")) is False

    stored = await provider.get_document(key, SKILLS)
    assert stored["createdBy"] == "user-a" and stored["content"] == "body of user-a"


async def test_n_racing_creates_have_one_winner(graph) -> None:
    provider, run = graph
    key = f"{run}_race"
    results = await asyncio.gather(
        *(provider.create_node_if_absent(key, SKILLS, _skill(run, key, f"user-{i}")) for i in range(8)),
        return_exceptions=True,
    )
    assert [r for r in results if isinstance(r, BaseException)] == []
    assert sum(bool(r) for r in results) == 1
    winner = results.index(True)
    assert (await provider.get_document(key, SKILLS))["createdBy"] == f"user-{winner}"


async def test_a_collection_with_no_schema_takes_the_same_call(graph) -> None:
    """The virtual-record mapping callers pass `id` too."""
    provider, run = graph
    key = f"{run}-vr"
    assert await provider.create_node_if_absent(key, MAPPING, {"id": key, "documentId": "first", "switchSeq": 1})
    assert not await provider.create_node_if_absent(key, MAPPING, {"id": key, "documentId": "second", "switchSeq": 1})
    stored = await provider.get_document(key, MAPPING)
    assert stored["documentId"] == "first"
    assert (stored.get("id") or stored.get("_key")) == key
