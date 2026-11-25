"""The cost of the diagnostics writes, shared by the ArangoDB/Neo4j file and the PostgreSQL file.

``write_attempt_start`` against the ``update_node`` it replaces, on the same record, interleaved. The delta is REPORTED, not
gated (a shared runner is not a benchmark); the throughput gate is the in-process A/B as a percentage of a realistic record
(``record_diagnostics_ab.py``)."""

from __future__ import annotations

import asyncio
import statistics
import time

from tests.integration.graph_db.record_diagnostics_support import RECORDS, Store

ROUNDS = 150


async def test_write_attempt_start_costs_about_what_update_node_costs(store: Store) -> None:
    key = store.key("cost")
    await store.put(key, indexingStatus="QUEUED", firstQueuedAt=5, indexingAttempts=0)
    plain, attempt = [], []
    for round_ in range(ROUNDS):
        fields = {"indexingStatus": "IN_PROGRESS", "processingStartedAt": round_, "contentRev": "rev-a"}
        for name, bucket in ((("plain", plain), ("attempt", attempt))[round_ % 2], (("attempt", attempt), ("plain", plain))[round_ % 2]):
            started = time.perf_counter()
            if name == "plain":
                await store.p.update_node(key, RECORDS, fields)
            else:
                await store.p.write_attempt_start(key, fields)
            bucket.append((time.perf_counter() - started) * 1000)
        await asyncio.sleep(0)
    delta = statistics.median(attempt) - statistics.median(plain)
    print(f"{store.name}: update_node p50 {statistics.median(plain):.3f} ms, write_attempt_start p50 {statistics.median(attempt):.3f} ms, delta {delta:+.3f} ms")
    assert plain and attempt  # reported, not gated


TEST_NAMES = ["test_write_attempt_start_costs_about_what_update_node_costs"]
