"""The duration of a connector's save transaction bounds how long a records schema widening can stall other
records writes (X = p99 + 1 s, see ``record_diagnostics_stall_bound.py``). On ArangoDB, hence the file name."""

from __future__ import annotations

import asyncio
import logging
import statistics
import time

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordType
from tests.integration.graph_db.record_diagnostics_stall_bound import STALL_BOUND_S
from tests.integration.graph_db.record_diagnostics_support import new_org
from tests.integration.real_graph import backend_unavailable, connect_arango

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
logger = logging.getLogger("record-diagnostics-save-transaction-it")


async def test_the_save_transaction_duration_stays_under_the_stall_bound() -> None:
    """4 workers, each saving batches of 50 file records (base documents, type documents and edges)
    in one transaction, 40 batches each. The 99th percentile plus 1 s is the bound."""
    try:
        provider = await connect_arango(logger, "record_diagnostics_stall_bound_it")
    except Exception as exc:
        backend_unavailable("arango", exc)
    org = new_org()
    store = GraphDataStore(logger, provider)
    durations: list[float] = []

    def batch(worker: int, index: int) -> list[FileRecord]:
        return [
            FileRecord(
                id=f"{org}-{worker}-{index}-{n}", org_id=org, record_name=f"f{n}.pdf", record_type=RecordType.FILE,
                external_record_id=f"{org}-{worker}-{index}-{n}", version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.KNOWLEDGE_BASE,
                connector_id=f"{org}-c", is_file=True, extension="pdf", mime_type="application/pdf", indexing_status="NOT_STARTED",
            )
            for n in range(50)
        ]

    async def worker(number: int) -> None:
        for index in range(40):
            records = batch(number, index)
            started = time.monotonic()
            async with store.transaction() as tx_store:
                await tx_store.batch_upsert_records(records)
            durations.append(time.monotonic() - started)

    try:
        await asyncio.gather(*(worker(i) for i in range(4)))
    finally:
        keys = [f"{org}-{w}-{i}-{n}" for w in range(4) for i in range(40) for n in range(50)]
        await provider.delete_nodes(keys, "records")
        await provider.disconnect()
    ordered = sorted(durations)
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    print(f"save transactions: {len(ordered)} transactions, p50 {statistics.median(ordered):.3f}s, p99 {p99:.3f}s, max {ordered[-1]:.3f}s -> X = {p99 + 1:.2f}s")
    assert p99 + 1.0 <= STALL_BOUND_S, f"measured X {p99 + 1:.2f}s is above STALL_BOUND_S={STALL_BOUND_S}"
