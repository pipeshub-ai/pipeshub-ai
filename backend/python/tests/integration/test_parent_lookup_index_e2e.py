"""``get_records_by_parent`` (run once per synced record) seeks an index on a real Neo4j.

Seeds a few thousand records under distinct parents, creates the indexes as ``ensure_schema``
does, and PROFILEs the statement the provider sends: it must be a NodeIndexSeek on
``(externalParentId, connectorId)`` reading a handful of rows, not a scan of the connector.

  cd backend/python && NEO4J_IT_URI=bolt://localhost:17687 NEO4J_IT_PASSWORD=... \
      pytest tests/integration/test_parent_lookup_index_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD (skips when unset and not reachable).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

RECORDS = 3000
MAX_DB_HITS = 100  # a seek plus its rows; the scan this guards against read RECORDS * 3 or more


@pytest.fixture
async def graph(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Neo4jProvider]:
    uri = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass"))
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logging.getLogger(__name__), MagicMock())
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("Neo4jProvider.connect returned False")
    except Exception as exc:
        if os.environ.get("NEO4J_IT_URI"):
            pytest.fail(f"neo4j is configured (NEO4J_IT_URI) but not reachable: {exc!r}")
        pytest.skip(f"neo4j not configured and not reachable locally: {exc!r}")
    yield provider
    await provider.disconnect()


def _operators(plan: dict) -> list[dict]:
    return [plan, *(op for child in plan.get("children", []) for op in _operators(child))]


def _db_hits(plan: dict) -> int:
    return sum(op.get("dbHits", 0) for op in _operators(plan))


async def test_children_of_a_parent_are_read_through_the_index(graph: Neo4jProvider) -> None:
    connector = f"conn-{uuid.uuid4().hex[:10]}"
    try:
        for statement in graph._generate_performance_indexes():
            await graph.client.execute_query(statement)
        await graph.client.execute_query("CALL db.awaitIndexes(300)")
        await graph.client.execute_query(
            "UNWIND range(1, $n) AS i CREATE (:Record {id: $c + '-' + toString(i), connectorId: $c, "
            "externalRecordId: 'e' + toString(i), externalParentId: 'p' + toString(i % 1000), "
            "recordType: 'TICKET', recordName: 'r', orgId: 'o', version: 1, origin: 'CONNECTOR', connectorName: 'JIRA', createdAtTimestamp: 1, updatedAtTimestamp: 1, isDeleted: false})",
            {"n": RECORDS, "c": connector},
        )
        sent: list[tuple[str, dict]] = []
        original = graph.client.execute_query

        async def recording(query, parameters=None, txn_id=None, timeout=None) -> list[dict]:
            sent.append((query, parameters or {}))
            return await original(query, parameters=parameters, txn_id=txn_id, timeout=timeout)

        graph.client.execute_query = recording
        try:
            children = await graph.get_records_by_parent(connector, "p7")
        finally:
            graph.client.execute_query = original
        assert len(children) == RECORDS // 1000
        (query, parameters), = sent
        async with graph.client.driver.session(database=graph.client.database) as session:
            summary = await (await session.run(f"PROFILE {query}", parameters)).consume()
        seeks = [op for op in _operators(summary.profile) if "IndexSeek" in op["operatorType"]]
        # Both properties in one seek: the composite index, not a single-field one.
        assert any(
            all(f in op["args"].get("Details", "") for f in ("externalParentId", "connectorId")) for op in seeks
        ), summary.profile
        assert _db_hits(summary.profile) <= MAX_DB_HITS
    finally:
        await graph.client.execute_query("MATCH (r:Record {connectorId: $c}) DELETE r", {"c": connector})
