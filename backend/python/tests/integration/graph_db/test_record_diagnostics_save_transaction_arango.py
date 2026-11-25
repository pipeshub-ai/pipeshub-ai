"""The duration of a connector's save transaction bounds how long a records schema widening can stall other
records writes (X = p99 + 1 s, see ``record_diagnostics_stall_bound.py``). On ArangoDB, hence the file name.

A save transaction is a run of sequential requests, so its duration is that number of round trips times the host's round-trip
time. The test counts the round trips each transaction holds open (what the code controls) instead of timing them: on a
2-CPU CI runner that shares its CPUs with the database, the round-trip time alone took the measured p99 from 0.3 s (an idle
host) to 1.5 s."""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import Counter

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordType
from tests.integration.graph_db.record_diagnostics_stall_bound import STALL_BOUND_S
from tests.integration.graph_db.record_diagnostics_support import new_org
from tests.integration.real_graph import backend_unavailable, connect_arango

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
logger = logging.getLogger("record-diagnostics-save-transaction-it")


#: Round trips a save transaction of 50 records may hold open: a base document, a type document and an edge per record.
ROUND_TRIPS_PER_SAVE = 3 * 50


class _CountedSession:
    """The client's HTTP session, counting the requests sent inside each stream transaction."""

    def __init__(self, session: object, sent: Counter[str]) -> None:
        self._session, self._sent = session, sent

    def __getattr__(self, name: str) -> object:
        attr = getattr(self._session, name)
        if name not in ("get", "post", "put", "patch", "delete"):
            return attr

        def send(*args: object, **kwargs: object) -> object:
            trx = (kwargs.get("headers") or {}).get("x-arango-trx-id")  # type: ignore[union-attr]
            if trx:
                self._sent[trx] += 1
            return attr(*args, **kwargs)
        return send


async def test_a_save_transaction_holds_a_bounded_number_of_round_trips_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """4 workers, each saving batches of 50 file records (base documents, type documents and edges)
    in one transaction, 40 batches each. Every transaction holds at most ``ROUND_TRIPS_PER_SAVE`` round trips open;
    the measured p99 plus 1 s is X, printed for the record. The bound is for the default save, without scope fields: with
    them the store adds structure reads to the transaction, so both settings are pinned off rather than read from the host."""
    monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    try:
        provider = await connect_arango(logger, "record_diagnostics_stall_bound_it")
    except Exception as exc:
        backend_unavailable("arango", exc)
    sent: Counter[str] = Counter()
    real_session = provider.http_client._get_session

    async def counted_session() -> _CountedSession:
        return _CountedSession(await real_session(), sent)

    monkeypatch.setattr(provider.http_client, "_get_session", counted_session)
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
    print(f"save transactions: {len(ordered)} transactions, p50 {statistics.median(ordered):.3f}s, p99 {p99:.3f}s, max {ordered[-1]:.3f}s -> X = {p99 + 1:.2f}s "
          f"(STALL_BOUND_S={STALL_BOUND_S}); round trips per transaction {sorted(set(sent.values()))}")
    assert len(sent) == len(ordered) == 160, (len(sent), len(ordered))
    assert max(sent.values()) <= ROUND_TRIPS_PER_SAVE, f"a save transaction held {max(sent.values())} round trips open (at most {ROUND_TRIPS_PER_SAVE})"
