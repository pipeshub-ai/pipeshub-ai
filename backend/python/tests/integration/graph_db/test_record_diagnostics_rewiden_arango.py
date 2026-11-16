"""A narrowed records schema and the rerun owners, on a real ArangoDB (3.12.4); the file name puts it in the ArangoDB job.

An older image applies its own, narrower record schema; records that hold the new fields then refuse every write
(errorNum 1620). What the product must do, each case measured here:

(a) outside a transaction: a status write, a revert, a FAILED write and a batch upsert (1 tainted + 99 clean) each
    succeed after at most one failed statement;
(b) inside a transaction: the 1620 aborts it before the caller's handler runs, a caller that catches and carries on
    cannot commit, nothing the transaction wrote is kept, and the owner's rerun leaves every write in place;
(c) a stream transaction held open during a widening: no other records write waits longer than the stall bound X
    (``STALL_BOUND_S``, measured in ``test_record_diagnostics_stall_bound_arango.py``);
(d) three processes that see the narrowing issue at most one change (the Redis lock);
(e) a narrowed ``indexingStatus`` enum and a record holding the newer value: the record updates after the widening,
    and not when the widening keeps the narrowed definition (the union control);
(f) an image without the guard fails for at most one poll interval (10 s);
(g) the generation rule: a process applies its schema only when the live generation is lower.

One forced 1620 per rerun owner (``retry_on_deadlock``, ``GraphDataStore.execute_in_transaction``, a plain
transaction block whose caller runs the unit again), each ending in the right state.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    retry_on_deadlock,
)
from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
from app.schema.arango.documents import RECORD_SCHEMA_GENERATION, record_schema
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.record_schema_guard import generation_of_schema
from tests.integration.graph_db.record_diagnostics_stall_bound import STALL_BOUND_S
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    base_record,
    drop_database,
    narrow_schema,
    new_org,
)
from tests.integration.real_graph import (
    ARANGO_PASSWORD,
    ARANGO_URL,
    backend_unavailable,
    connect_arango,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
logger = logging.getLogger("record-diagnostics-rewiden-it")
DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "indexingAttempts": 2, "pageCount": 12, "failedAt": 1_700_000_100_000}
NEWER_STATUS = "ENABLE_MULTIMODAL_MODELS"


class Cluster:
    """The provider under test, with counters on what it sends."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.provider = store.raw_provider
        self.client: ArangoHTTPClient = self.provider.http_client

    async def narrow(self, **kw: Any) -> None:  # noqa: ANN401
        assert await self.client.update_collection_schema(RECORDS, narrow_schema(**kw))
        self.client.record_guard.note_narrow()

    async def live_generation(self) -> int:
        return generation_of_schema(await self.client.get_collection_schema(RECORDS))

    async def wide(self) -> None:
        assert await self.client.update_collection_schema(RECORDS, record_schema)

    async def tainted(self, n: int, label: str = "t") -> list[str]:
        keys = [self.store.key(label) for _ in range(n)]
        docs = [{**base_record(k, self.store.org), **DIAGNOSTICS} for k in keys]
        assert await self.provider.batch_upsert_nodes(docs, RECORDS)
        return keys

    async def clean(self, n: int, label: str = "c") -> list[str]:
        keys = [self.store.key(label) for _ in range(n)]
        assert await self.provider.batch_upsert_nodes([base_record(k, self.store.org) for k in keys], RECORDS)
        return keys


@pytest.fixture
async def cluster(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Cluster]:
    # the graph jobs start Redis too (REDIS_IT_HOST / REDIS_IT_PORT); the widening's lock lives there
    host, port = os.environ.get("REDIS_IT_HOST", "localhost"), os.environ.get("REDIS_IT_PORT", "6389")
    monkeypatch.setenv("REDIS_HOST", host)
    monkeypatch.setenv("REDIS_PORT", port)
    monkeypatch.setenv("REDIS_MODE", "standalone")
    try:
        provider = await connect_arango(logger, f"rd_rewiden_{uuid.uuid4().hex[:6]}")
    except Exception as exc:
        backend_unavailable("arango", exc)
    made = Cluster(Store("arango", provider, new_org()))
    try:
        yield made
    finally:
        await made.wide()
        await made.store.cleanup()
        await drop_database(provider)
        await provider.disconnect()


async def counting_refusals(cluster: Cluster, action: Any) -> int:  # noqa: ANN401
    """How many statements the store refused (1620) while ``action`` ran: each refusal reaches the client's hook once."""
    from app.services.graph_db.arango import arango_http_client as client_module

    refused = {"n": 0}
    original = client_module.is_record_schema_error

    def counting(error: BaseException | str) -> bool:
        found = original(error)
        refused["n"] += int(found)
        return found

    client_module.is_record_schema_error = counting
    try:
        await action()
    finally:
        client_module.is_record_schema_error = original
    return refused["n"]


# ---- (a) outside a transaction ------------------------------------------------------------------------------------


async def test_a_status_write_a_revert_a_failure_and_a_batch_upsert_each_succeed_after_one_failed_statement(cluster: Cluster) -> None:
    keys = await cluster.tainted(1000)
    p = cluster.provider
    writes = {
        "attempt start": lambda k: p.write_attempt_start(k, {"indexingStatus": "IN_PROGRESS", "contentRev": "r"}),
        "revert": lambda k: p.update_node(k, RECORDS, {"indexingStatus": "QUEUED", "queuedAtTimestamp": 5}),
        "failed": lambda k: p.update_node(k, RECORDS, {"indexingStatus": "FAILED", "failedAt": 7}),
    }
    for index, (name, write) in enumerate(writes.items()):
        await cluster.narrow()
        refused = await counting_refusals(cluster, lambda k=keys[index], w=write: w(k))
        assert refused <= 1, f"{name}: {refused} refused statements"
    assert (await cluster.store.doc(keys[2]))["indexingStatus"] == "FAILED"

    await cluster.narrow()
    clean = await cluster.clean(99)
    batch = [{**base_record(keys[3], cluster.store.org), **DIAGNOSTICS, "indexingStatus": "QUEUED"}, *[{**base_record(k, cluster.store.org), "indexingStatus": "QUEUED"} for k in clean]]
    await cluster.narrow()
    refused = await counting_refusals(cluster, lambda: p.batch_upsert_nodes(batch, RECORDS))
    assert refused <= 1
    assert (await cluster.store.doc(keys[3]))["indexingStatus"] == "QUEUED" and (await cluster.store.doc(clean[-1]))["indexingStatus"] == "QUEUED"


async def test_without_the_guard_the_same_write_fails_for_good(cluster: Cluster) -> None:
    """The control: a client with no guard (an image older than the hook) is refused, and stays refused."""
    [key] = await cluster.tainted(1)
    await cluster.narrow()
    bare = ArangoHTTPClient(ARANGO_URL, "root", ARANGO_PASSWORD, cluster.client.database, logger)
    try:
        with pytest.raises(Exception, match="1620"):
            await bare.update_document(RECORDS, key, {"indexingStatus": "QUEUED"})
        with pytest.raises(Exception, match="1620"):
            await bare.update_document(RECORDS, key, {"indexingStatus": "QUEUED"})
    finally:
        await bare.disconnect()


# ---- (b) inside a transaction -----------------------------------------------------------------------------------


async def _multi_statement_transaction(cluster: Cluster, a: str, tainted: str, clean: str) -> None:
    """A save-like transaction: a write to ``a``, then one statement over a tainted record and a clean one."""
    p = cluster.provider
    txn = await p.begin_transaction(read=[RECORDS], write=[RECORDS])
    try:
        await p.update_node(a, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)
        await p.batch_upsert_nodes(
            [{**base_record(tainted, cluster.store.org), **DIAGNOSTICS, "indexingStatus": "QUEUED"},
             {**base_record(clean, cluster.store.org), "indexingStatus": "QUEUED"}], RECORDS, transaction=txn)
        await p.commit_transaction(txn)
    except BaseException:
        await p.rollback_transaction(txn)
        raise


async def test_a_refusal_in_a_transaction_aborts_it_and_the_rerun_leaves_every_write_in_place(cluster: Cluster) -> None:
    [a] = await cluster.clean(1, "a")
    [tainted] = await cluster.tainted(1)
    [clean] = await cluster.clean(1)
    await cluster.narrow()

    with pytest.raises(RecordSchemaNarrowError):
        await _multi_statement_transaction(cluster, a, tainted, clean)

    # all or nothing: neither the earlier statement nor the failed statement's other document was kept
    assert (await cluster.store.doc(a))["indexingStatus"] == "NOT_STARTED"
    assert (await cluster.store.doc(clean))["indexingStatus"] == "NOT_STARTED"
    # the owner's rerun (the next transaction waits for the widening, up to 5 s)
    await _multi_statement_transaction(cluster, a, tainted, clean)
    assert [(await cluster.store.doc(k))["indexingStatus"] for k in (a, tainted, clean)] == ["QUEUED"] * 3
    assert (await cluster.store.doc(tainted))["pageCount"] == 12


async def test_a_caller_that_catches_the_refusal_and_goes_on_cannot_commit_anything(cluster: Cluster) -> None:
    """The control for the partial batch: a handler that swallows the error and commits."""
    [a] = await cluster.clean(1, "a")
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    p = cluster.provider
    txn = await p.begin_transaction(read=[RECORDS], write=[RECORDS])
    await p.update_node(a, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)
    with pytest.raises(RecordSchemaNarrowError):
        await p.update_node(tainted, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)

    with pytest.raises(RecordSchemaNarrowError):
        await p.update_node(a, RECORDS, {"indexingStatus": "FAILED"}, transaction=txn)  # the next statement is not sent
    with pytest.raises(RecordSchemaNarrowError):
        await p.commit_transaction(txn)
    assert (await cluster.store.doc(a))["indexingStatus"] == "NOT_STARTED"


async def test_the_transaction_is_gone_from_the_server_when_the_error_reaches_the_caller(cluster: Cluster) -> None:
    """The abort happens before any caller code runs: whatever the caller does with the error, the server no longer lists the
    transaction (a missing abort leaves it open until its idle timeout, holding its locks)."""
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    p = cluster.provider
    txn = await p.begin_transaction(read=[RECORDS], write=[RECORDS])

    with pytest.raises(RecordSchemaNarrowError):
        await p.update_node(tainted, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)

    listed = [t.get("id") for t in await cluster.client.list_running_transactions()]
    assert txn not in listed


async def test_ensure_wide_inside_a_transaction_asserts(cluster: Cluster) -> None:
    with pytest.raises(AssertionError):
        await cluster.client.record_guard.ensure_wide("test", transaction="1")


# ---- (c) a long transaction during a widening: the stall bound --------------------------------------------------------


async def test_no_other_records_write_waits_longer_than_the_bound_while_a_transaction_is_open(cluster: Cluster) -> None:
    keys = await cluster.clean(20, "w")
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    p = cluster.provider
    holding = await p.begin_transaction(read=[RECORDS], write=[RECORDS])
    await p.update_node(keys[0], RECORDS, {"indexingStatus": "QUEUED"}, transaction=holding)
    waits: list[float] = []
    stop = asyncio.Event()

    async def others() -> None:
        index = 1
        while not stop.is_set():
            started = time.monotonic()
            await p.update_node(keys[index % 19 + 1], RECORDS, {"processingStartedAt": index})
            waits.append(time.monotonic() - started)
            index += 1
            await asyncio.sleep(0.05)

    async def release_later() -> None:
        await asyncio.sleep(3)
        await p.commit_transaction(holding)

    background = [asyncio.create_task(others()), asyncio.create_task(release_later())]
    trigger_started = time.monotonic()
    await p.update_node(tainted, RECORDS, {"indexingStatus": "QUEUED"})  # refused, widens at the quiet point, then lands
    stop.set()
    await asyncio.gather(*background)

    assert time.monotonic() - trigger_started >= 2.5, "the change waited for the open transaction"
    assert waits and max(waits) <= STALL_BOUND_S, f"a write waited {max(waits):.2f}s (bound {STALL_BOUND_S}s)"
    assert (await cluster.store.doc(tainted))["indexingStatus"] == "QUEUED"


# ---- (d) three processes, one change ------------------------------------------------------------------------------


async def test_three_processes_that_see_the_narrowing_issue_one_change(cluster: Cluster, monkeypatch: pytest.MonkeyPatch) -> None:
    providers = [cluster.provider]
    providers.extend([await connect_arango(logger, cluster.client.database), await connect_arango(logger, cluster.client.database)])
    try:
        await cluster.narrow()
        puts = {"n": 0}
        original = ArangoHTTPClient.update_collection_schema

        async def counting(self: ArangoHTTPClient, name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
            if timeout is not None:
                puts["n"] += 1
            return await original(self, name, schema, timeout)

        monkeypatch.setattr(ArangoHTTPClient, "update_collection_schema", counting)
        await asyncio.gather(*(p.ensure_wide_record_schema("test") for p in providers))

        assert puts["n"] == 1, f"{puts['n']} changes were issued"
        assert await cluster.live_generation() == RECORD_SCHEMA_GENERATION
    finally:
        for extra in providers[1:]:
            await extra.disconnect()


# ---- (e) the narrowed enum and the union control -----------------------------------------------------------------------


async def test_a_record_holding_a_newer_status_updates_after_the_widening(cluster: Cluster) -> None:
    [key] = await cluster.clean(1, "e")
    await cluster.provider.update_node(key, RECORDS, {"indexingStatus": NEWER_STATUS})
    await cluster.narrow(drop_status=NEWER_STATUS)

    assert await cluster.provider.update_node(key, RECORDS, {"processingStartedAt": 9})

    assert (await cluster.store.doc(key))["processingStartedAt"] == 9
    assert await cluster.live_generation() == RECORD_SCHEMA_GENERATION


async def test_a_widening_that_keeps_the_narrowed_definition_leaves_that_record_refused(cluster: Cluster) -> None:
    """The control: the union of the live and own properties keeps the narrowed enum, so the record still fails."""
    [key] = await cluster.clean(1, "e")
    await cluster.provider.update_node(key, RECORDS, {"indexingStatus": NEWER_STATUS})
    await cluster.narrow(drop_status=NEWER_STATUS)
    union = narrow_schema(drop_status=NEWER_STATUS)
    union["rule"]["properties"].update({name: record_schema["rule"]["properties"][name] for name in DIAGNOSTICS})
    union["rule"]["$comment"] = f"pipeshub-record-schema gen={RECORD_SCHEMA_GENERATION}"
    cluster.client.record_guard._schema = union

    with pytest.raises(Exception, match="1620"):
        await cluster.provider.update_node(key, RECORDS, {"processingStartedAt": 9})


# ---- (f) an image without the guard ---------------------------------------------------------------------------------


async def test_an_image_without_the_guard_fails_for_at_most_one_poll_interval(cluster: Cluster) -> None:
    [key] = await cluster.tainted(1)
    await cluster.narrow()
    cluster.provider.start_record_schema_poll(interval=10.0)
    bare = ArangoHTTPClient(ARANGO_URL, "root", ARANGO_PASSWORD, cluster.client.database, logger)
    started = time.monotonic()
    try:
        while True:
            try:
                await bare.update_document(RECORDS, key, {"processingStartedAt": 1})
                break
            except Exception as error:
                assert "1620" in str(error)
                await asyncio.sleep(0.5)
                assert time.monotonic() - started < 15, "still refused after the poll interval"
    finally:
        window = time.monotonic() - started
        await cluster.client.record_guard.stop()
        await bare.disconnect()
    print(f"old-image window: {window:.1f}s")
    # the poll interval, plus the request and the widening it issues
    assert window <= 10.0 + 2.0


# ---- (g) the generation ------------------------------------------------------------------------------------------------


async def test_generation(cluster: Cluster) -> None:
    """The marker survives a PUT and a GET; a process applies its schema only when the live generation is lower."""
    assert await cluster.live_generation() == RECORD_SCHEMA_GENERATION
    higher = copy.deepcopy(record_schema)
    higher["rule"]["$comment"] = f"pipeshub-record-schema gen={RECORD_SCHEMA_GENERATION + 4}"
    assert await cluster.client.update_collection_schema(RECORDS, higher)

    assert await cluster.provider._apply_schema(RECORDS, record_schema) is True
    assert await cluster.live_generation() == RECORD_SCHEMA_GENERATION + 4, "a lower generation must never narrow a higher one"
    assert await cluster.provider.ensure_wide_record_schema("test") is False

    assert await cluster.client.update_collection_schema(RECORDS, narrow_schema())
    assert await cluster.live_generation() == 0
    assert await cluster.provider._apply_schema(RECORDS, record_schema) is True
    assert await cluster.live_generation() == RECORD_SCHEMA_GENERATION


async def test_applying_when_equal_would_not_be_needed(cluster: Cluster) -> None:
    """The control for 'apply iff live < own': with an equal generation no change is sent."""
    puts = {"n": 0}
    original = cluster.client.update_collection_schema

    async def counting(*a: Any, **k: Any) -> bool:  # noqa: ANN401
        puts["n"] += 1
        return await original(*a, **k)

    cluster.client.update_collection_schema = counting  # type: ignore[method-assign]
    await cluster.provider._apply_schema(RECORDS, record_schema)
    assert puts["n"] == 0


# ---- the rerun owners: one forced 1620 each ----------------------------------------------------------------------------


async def _save_like(tx_store: Any, org: str, keys: list[str], tainted: str) -> None:  # noqa: ANN401
    await tx_store.batch_upsert_nodes(
        [{**base_record(keys[0], org), "indexingStatus": "QUEUED"},
         {**base_record(tainted, org), **DIAGNOSTICS, "indexingStatus": "QUEUED"},
         {**base_record(keys[1], org), "indexingStatus": "QUEUED"}], RECORDS)


async def test_owner_execute_in_transaction_reruns_to_the_correct_end_state(cluster: Cluster) -> None:
    keys = await cluster.clean(2)
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    attempts = {"n": 0}
    store = GraphDataStore(logger, cluster.provider)

    async def unit(tx_store: Any) -> None:  # noqa: ANN401
        attempts["n"] += 1
        await _save_like(tx_store, cluster.store.org, keys, tainted)

    await store.execute_in_transaction(unit)

    assert attempts["n"] == 2, "the first run was aborted by the refusal, the second landed"
    assert [(await cluster.store.doc(k))["indexingStatus"] for k in (*keys, tainted)] == ["QUEUED"] * 3


async def test_owner_retry_on_deadlock_reruns_to_the_correct_end_state(cluster: Cluster) -> None:
    keys = await cluster.clean(2)
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    attempts = {"n": 0}

    class Entry:
        data_store_provider = GraphDataStore(logger, cluster.provider)

        @retry_on_deadlock()
        async def on_save(self) -> None:
            attempts["n"] += 1
            async with self.data_store_provider.transaction() as tx_store:
                await _save_like(tx_store, cluster.store.org, keys, tainted)

    await Entry().on_save()

    assert attempts["n"] == 2
    assert [(await cluster.store.doc(k))["indexingStatus"] for k in (*keys, tainted)] == ["QUEUED"] * 3


async def test_owner_run_again_a_plain_block_raises_nothing_committed_and_the_unit_succeeds_when_run_again(cluster: Cluster) -> None:
    """A transaction block with no retry of its own (a sync run, an indexing message): the error leaves the block, nothing
    is kept, and the caller that redelivers the unit gets the correct end state."""
    keys = await cluster.clean(2)
    [tainted] = await cluster.tainted(1)
    await cluster.narrow()
    store = GraphDataStore(logger, cluster.provider)

    with pytest.raises(RecordSchemaNarrowError) as caught:
        async with store.transaction() as tx_store:
            await _save_like(tx_store, cluster.store.org, keys, tainted)
    # the real error from the real store, as the indexing consumer sees it: retried, not a verdict on the record
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )

    assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TRANSIENT
    assert [(await cluster.store.doc(k))["indexingStatus"] for k in (*keys, tainted)] == ["NOT_STARTED"] * 3

    async with store.transaction() as tx_store:  # the redelivery
        await _save_like(tx_store, cluster.store.org, keys, tainted)
    assert [(await cluster.store.doc(k))["indexingStatus"] for k in (*keys, tainted)] == ["QUEUED"] * 3
