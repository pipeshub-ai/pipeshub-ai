"""A write-write conflict inside a transaction aborts it instead of losing a write.

``batch_update_nodes`` used to send ``ignoreErrors: true``, so the loser of a
conflict got a quiet False and went on to commit the rest of its transaction.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_batch_update_conflict_real_arango.py -m integration
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("batch-update-conflict-it")
RECORDS = CollectionNames.RECORDS.value


@pytest.fixture
async def arango() -> AsyncIterator[tuple]:
    try:
        provider = await connect_arango(logger, "batch_update_conflict_it")
    except Exception as exc:
        backend_unavailable("arango", exc)
    prefix = f"bu-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, prefix
    finally:
        await provider.http_client.execute_aql(
            f"FOR d IN {RECORDS} FILTER STARTS_WITH(d._key, @p) REMOVE d IN {RECORDS}", {"p": prefix}
        )
        await provider.disconnect()


async def _status(provider, key: str) -> str:
    rows = await provider.http_client.execute_aql(
        f"RETURN DOCUMENT('{RECORDS}/{key}').extractionStatus"
    )
    return rows[0]


async def test_conflicting_update_aborts_the_losing_transaction(arango) -> None:
    provider, prefix = arango
    shared, other = f"{prefix}-shared", f"{prefix}-other"
    await provider.batch_upsert_nodes(
        [
            {"id": key, "orgId": prefix, "recordName": key, "externalRecordId": key, "recordType": "FILE",
             "origin": "CONNECTOR", "connectorName": "KB", "connectorId": f"{prefix}-c",
             "virtualRecordId": f"{key}-v", "createdAtTimestamp": 1, "extractionStatus": "NOT_STARTED"}
            for key in (shared, other)
        ],
        RECORDS,
    )

    winner = await provider.begin_transaction(read=[], write=[RECORDS])
    loser = await provider.begin_transaction(read=[], write=[RECORDS])
    try:
        assert await provider.batch_update_nodes(
            [{"id": shared, "extractionStatus": "COMPLETED"}], RECORDS, transaction=winner
        ) is True

        with pytest.raises(Exception) as caught:
            await provider.batch_update_nodes(
                [{"id": other, "extractionStatus": "FAILED"}, {"id": shared, "extractionStatus": "FAILED"}],
                RECORDS,
                transaction=loser,
            )
        assert provider.is_write_conflict(caught.value)
    finally:
        await provider.rollback_transaction(loser)
        await provider.commit_transaction(winner)

    assert await _status(provider, shared) == "COMPLETED"
    assert await _status(provider, other) == "NOT_STARTED"


async def test_missing_node_is_skipped_and_reported(arango) -> None:
    provider, prefix = arango
    assert await provider.batch_update_nodes(
        [{"id": f"{prefix}-absent", "extractionStatus": "FAILED"}], RECORDS
    ) is False
