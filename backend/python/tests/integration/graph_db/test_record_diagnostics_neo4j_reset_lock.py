"""The connector status reset on Neo4j locks only the records that pass its filters.

The reset locks a record before it reads the run fields (so the rule sees what another writer committed while it waited). A lock taken
before the status and visibility filters would hold every record of the connector, completed, in progress and trashed ones included, and
block every concurrent write of those records until the statement ends.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    new_org,
)
from tests.integration.real_graph import backend_unavailable, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]
logger = logging.getLogger("record-diagnostics-reset-lock-it")


@pytest.fixture
async def store(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Store]:
    try:
        provider = await connect_neo4j(logger, monkeypatch)
    except Exception as exc:
        backend_unavailable("neo4j", exc)
    made = Store("neo4j", provider, new_org())
    try:
        yield made
    finally:
        await made.cleanup()
        await provider.disconnect()


async def test_a_record_outside_the_filters_is_not_locked_by_the_reset(store: Store) -> None:
    """Expected: with a write open on the excluded IN_PROGRESS record, the reset of the rest finishes at once (a lock on it would wait for
    the 10 s lock timeout and fail), and that record is untouched."""
    queued, busy = store.key("queued"), store.key("busy")
    await store.put(queued, indexingStatus="QUEUED", firstQueuedAt=5, indexingAttempts=1)
    await store.put(busy, indexingStatus="IN_PROGRESS", firstQueuedAt=5, indexingAttempts=1)
    connector = f"{store.org}-c"

    holder = await store.p.begin_transaction([RECORDS], [RECORDS], explicit=True)
    try:
        assert await store.p.update_node(busy, RECORDS, {"indexingStatus": "IN_PROGRESS", "processingStartedAt": 7}, transaction=holder)

        await asyncio.wait_for(
            store.p.reset_indexing_status_for_connector(connector, "NOT_STARTED", exclude_statuses=["IN_PROGRESS"]), timeout=5
        )
    finally:
        await store.p.rollback_transaction(holder)

    assert (await store.doc(queued))["indexingStatus"] == "NOT_STARTED"
    assert (await store.doc(busy))["indexingStatus"] == "IN_PROGRESS"
