"""The stage-state schema is forward-compatible on ArangoDB and Neo4j (PostgreSQL: postgres/test_stage_state_forward_compat_pg.py).

A later release may add a stage-state field or a status value; a pod still on this schema must accept and
read the row. Record nodes stay strict, and rolling back past this release is the documented floor.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_stage_state_forward_compat.py -m integration
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.schema import node_validator
from app.schema.node_schema_registry import adapt_schema
from tests.integration.graph_db import stage_forward_compat_support as support
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("stage-forward-compat-it")
STAGES = CollectionNames.STAGE_STATES.value
ORG = f"org-sfc-{uuid.uuid4().hex[:8]}"
ARANGO_DB = "stage_forward_compat_it"
# The rollback test swaps the collection's schema while it runs, so on ArangoDB it owns a database no other
# test uses, and drops it afterwards: a test on another xdist worker never writes under the strict schema.
ARANGO_ROLLBACK_DB = "stage_forward_compat_rollback_it"


async def _connected(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, arango_db: str, *, drop: bool = False
) -> AsyncIterator[IGraphDBProvider]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            if request.param == "neo4j":
                provider = await connect_neo4j(logger, monkeypatch)
            else:
                provider = await connect_arango(logger, arango_db)
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(provider.disconnect)
        if drop and request.param == "arango":
            # Before the disconnect, which closes the session the drop uses.
            cleanup.push_async_callback(_drop_database, provider, arango_db)
        yield provider
        await provider.stage_states_delete_for_virtual_records(list(support.CREATED_VRIDS))


async def _drop_database(provider: IGraphDBProvider, name: str) -> None:
    client = provider.http_client  # type: ignore[attr-defined]
    session = await client._get_session()
    async with session.delete(f"{client.base_url}/_api/database/{name}") as response:
        assert response.status in (200, 404), await response.text()


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    async for provider in _connected(request, monkeypatch, ARANGO_DB):
        yield provider


@pytest.fixture(params=["neo4j", "arango"])
async def rollback_graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[IGraphDBProvider]:
    async for provider in _connected(request, monkeypatch, ARANGO_ROLLBACK_DB, drop=True):
        yield provider


async def test_unknown_stage_field_and_value_accepted(graph: IGraphDBProvider) -> None:
    await support.unknown_field_and_value_accepted(graph, ORG)


async def test_unknown_tier_and_priority_read_tolerantly(graph: IGraphDBProvider) -> None:
    await support.unknown_tier_and_priority_read_tolerantly(graph, ORG)


async def test_a_foreign_row_is_left_byte_identical(graph: IGraphDBProvider) -> None:
    await support.a_foreign_row_is_left_byte_identical(graph, ORG)


async def test_records_strict_types_checked_and_rollback_floor(
    rollback_graph: IGraphDBProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = rollback_graph
    await support.record_nodes_stay_strict(graph, ORG)
    await support.stage_types_still_checked(graph, ORG)

    # Rollback past this release: the strict schema comes back. What it refuses differs by store, and the
    # documented floor is the weakest of them (docs/indexing-service.md section 2.5):
    #  * ArangoDB validates the whole stored document, so every update of a row that carries a field this
    #    schema does not know is refused (error 1620), even a patch of known fields.
    #  * Neo4j (and PostgreSQL) validate the patch only: a patch of known fields and a known status is accepted,
    #    and one that carries an unknown field or a status the old enum lacked is refused.
    document = support.stage_document(ORG)
    assert await graph.stage_state_create({**document, "sweepAtMs": 1})
    strict = support.strict_schema_of_the_previous_release()
    if hasattr(graph, "http_client"):  # ArangoDB: the collection validator
        from app.schema.arango.documents import stage_state_schema

        assert await graph.http_client.update_collection_schema(STAGES, strict)
        try:
            with pytest.raises(support.REFUSED):
                await graph.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "COMPLETED", {"attempt": 1})
        finally:
            assert await graph.http_client.update_collection_schema(STAGES, stage_state_schema)
    else:  # Neo4j: the application validator, handed the strict schema
        original = node_validator.get_node_schema
        monkeypatch.setattr(
            node_validator, "get_node_schema",
            lambda name: adapt_schema(strict) if name == STAGES else original(name),
        )
        with pytest.raises(support.REFUSED):
            await graph.stage_state_create({**support.stage_document(ORG), "sweepAtMs": 1})
        with pytest.raises(support.REFUSED):
            await graph.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "SPLIT", {"attempt": 1})
        # The patch alone is checked: a known patch on the extended row goes through.
        assert await graph.stage_state_compare_and_set(document["id"], "IN_PROGRESS", "COMPLETED", {"attempt": 1})
