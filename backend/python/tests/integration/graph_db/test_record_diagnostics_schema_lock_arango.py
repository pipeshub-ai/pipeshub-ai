"""The records-schema widening lock, kept in ArangoDB itself, on a real server (3.12.4) with no Redis.

A lease lock in one document: one holder at a time, an expired lease taken over, a holder that finds it lost its lease before its
write does not write, and a lock store that cannot be written means no schema write at all (the refused write stays refused and the poll
tries again). Expected: an older process that reads the newer schema before it writes leaves it alone. The write itself is not fenced (see the last
tests): a stale overwrite is repaired eventually by a newer process's poll, provided its later checks and writes succeed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
from app.schema.arango.documents import RECORD_SCHEMA_GENERATION, record_schema
from app.services.graph_db.arango import record_schema_guard as guard_module
from app.services.graph_db.arango.record_schema_guard import (
    ArangoLock,
    RecordSchemaGuard,
    generation_of_schema,
)
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    base_record,
    drop_database,
    narrow_schema,
    new_org,
)
from tests.integration.real_graph import backend_unavailable, connect_arango

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("record-diagnostics-schema-lock-it")
NEWER = RECORD_SCHEMA_GENERATION + 1


@pytest.fixture
async def env() -> AsyncIterator[tuple[Any, Any]]:
    """Two processes (providers) on one database."""
    name = f"rd_lock_{uuid.uuid4().hex[:6]}"
    try:
        first = await connect_arango(logger, name)
    except Exception as exc:
        backend_unavailable("arango", exc)
    second = await connect_arango(logger, name)
    yield first, second
    await first.http_client.update_collection_schema(RECORDS, record_schema)
    await drop_database(first)
    await second.disconnect()
    await first.disconnect()


def _puts(client: ArangoHTTPClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Counts the schema writes this client issues (the widening's carry a timeout)."""
    counted = {"n": 0}
    original = client.update_collection_schema

    async def counting(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        if timeout is not None:
            counted["n"] += 1
        return await original(name, schema, timeout)

    monkeypatch.setattr(client, "update_collection_schema", counting)
    return counted


async def _generation(client: ArangoHTTPClient) -> int:
    return generation_of_schema(await client.get_collection_schema(RECORDS))


def _newer_schema() -> dict[str, Any]:
    return {**record_schema, "rule": {**record_schema["rule"], "$comment": f"pipeshub-record-schema gen={NEWER}"}}


async def test_one_holder_at_a_time_an_expired_lease_is_taken_over_and_the_old_holder_is_fenced(env: tuple[Any, Any]) -> None:
    a, b = ArangoLock(env[0].http_client), ArangoLock(env[1].http_client)

    assert await a.acquire(400) is True
    assert await b.acquire(400) is False, "the lease is held"
    assert await a.renew(400) is True

    await asyncio.sleep(0.7)  # the lease runs out
    assert await b.acquire(15_000) is True, "an expired lease is taken over"
    assert await a.renew(15_000) is False, "the old holder cannot renew a lease it lost"
    await a.release()  # not the owner: leaves the new holder's lock alone
    assert await b.renew(15_000) is True
    await b.release()
    assert await a.acquire(15_000) is True, "released: free again"
    await a.release()


async def test_a_holder_that_lost_its_lease_during_the_wait_writes_no_schema(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The lease runs out during the quiet wait and another process takes the lock: the renew before the write fails and nothing is written."""
    slow, other = env
    client = slow.http_client
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(client, monkeypatch)
    monkeypatch.setattr(guard_module, "LOCK_TTL_MS", 300)
    thief = ArangoLock(other.http_client)
    guard: RecordSchemaGuard = client.record_guard
    real_quiet = guard._wait_quiet

    async def quiet_after_the_lease_ran_out() -> bool:
        await asyncio.sleep(0.6)
        assert await thief.acquire(15_000) is True  # taken over from the expired holder
        return await real_quiet()

    monkeypatch.setattr(guard, "_wait_quiet", quiet_after_the_lease_ran_out)

    assert await guard.ensure_wide("test") is False
    assert puts["n"] == 0 and await _generation(client) == 0, "a process that lost its lease cannot write"
    await thief.release()


async def test_the_older_process_never_overwrites_a_newer_schema(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The race: the older process finds the schema narrow; before it has the lock a newer process installs its own schema."""
    older, newer = env
    assert await older.http_client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(older.http_client, monkeypatch)
    guard: RecordSchemaGuard = older.http_client.record_guard
    real_factory = guard._lock_factory

    async def newer_installs_first() -> Any:  # noqa: ANN401
        assert await newer.http_client.update_collection_schema(RECORDS, _newer_schema())
        return await real_factory()

    monkeypatch.setattr(guard, "_lock_factory", newer_installs_first)

    assert await guard.ensure_wide("test") is False
    assert puts["n"] == 0
    assert await _generation(older.http_client) == NEWER, "the newer schema was left alone"


async def test_a_lock_store_that_cannot_be_written_means_no_schema_write(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = env[0].http_client
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(client, monkeypatch)
    guard: RecordSchemaGuard = client.record_guard

    async def lock_on_a_collection_that_cannot_exist() -> ArangoLock:
        return ArangoLock(client, collection="a/b")  # "/" is never allowed in a collection name

    monkeypatch.setattr(guard, "_lock_factory", lock_on_a_collection_that_cannot_exist)

    assert await guard.ensure_wide("test") is False
    assert puts["n"] == 0 and await _generation(client) == 0


async def test_a_refused_write_raises_the_transient_error_when_the_lock_cannot_be_written(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    provider = env[0]
    client = provider.http_client
    org = new_org()
    key = f"{org}-k"
    diagnostics = {"firstQueuedAt": 1_700_000_000_000, "indexingAttempts": 2, "pageCount": 12, "failedAt": 1_700_000_100_000}
    assert await provider.batch_upsert_nodes([{**base_record(key, org), **diagnostics}], RECORDS)
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    guard: RecordSchemaGuard = client.record_guard

    async def broken_lock() -> ArangoLock:
        return ArangoLock(client, collection="a/b")  # "/" is never allowed in a collection name

    monkeypatch.setattr(guard, "_lock_factory", broken_lock)

    with pytest.raises(RecordSchemaNarrowError):
        await provider.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
    assert await _generation(client) == 0


# ---- the window a lease cannot close ------------------------------------------------------------------------------------------
#
# A collection-properties update is not transactional with the lock: a process paused longer than the lease between its last
# look and its write (or one whose write the server applies after the client gave up) can put an older schema over a newer one.
# What bounds that window is the poll: every process reads the live generation every POLL_INTERVAL_S and widens again when it is
# lower than its own, and a write refused meanwhile is transient and lands on retry. These tests make the stale write happen.

DIAGNOSTICS = {"firstQueuedAt": 1_700_000_000_000, "indexingAttempts": 2, "pageCount": 12, "failedAt": 1_700_000_100_000}


def _older_schema() -> dict[str, Any]:
    """What an older process writes: no diagnostics fields, and the generation it knows."""
    older = narrow_schema()
    older["rule"]["$comment"] = f"pipeshub-record-schema gen={RECORD_SCHEMA_GENERATION}"
    return older


async def _after_a_stale_write(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch, keys: int = 0) -> tuple[Any, Any, list[str]]:
    """The older process (``first``) renews its lease, is overtaken by the newer process's schema, and writes its older one."""
    older, newer = env
    org = new_org()
    seeded = [f"{org}-k{i}" for i in range(keys)]
    if seeded:
        assert await newer.batch_upsert_nodes([{**base_record(k, org), **DIAGNOSTICS} for k in seeded], RECORDS)
    assert await older.http_client.update_collection_schema(RECORDS, narrow_schema())
    older.http_client.record_guard = RecordSchemaGuard(
        older.http_client, logger, schema=_older_schema(), generation=RECORD_SCHEMA_GENERATION
    )
    newer.http_client.record_guard = RecordSchemaGuard(newer.http_client, logger, schema=_newer_schema(), generation=NEWER)
    real_put = older.http_client.update_collection_schema

    async def stale_put(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        if timeout is not None:  # the widening's own write
            assert await newer.http_client.update_collection_schema(RECORDS, _newer_schema())  # the newer process got in after the last look
        return await real_put(name, schema, timeout)

    monkeypatch.setattr(older.http_client, "update_collection_schema", stale_put)
    assert await older.http_client.record_guard.ensure_wide("test") is True
    assert await _generation(older.http_client) == RECORD_SCHEMA_GENERATION, "the older schema is over the newer one"
    return older, newer, seeded


async def test_the_poll_of_a_newer_process_restores_its_generation_within_one_interval(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    assert guard_module.POLL_INTERVAL_S == 10.0  # the interval the bound below is stated for
    _, newer, _ = await _after_a_stale_write(env, monkeypatch)
    interval = 0.3
    newer.http_client.record_guard.start(interval)
    started = asyncio.get_running_loop().time()
    try:
        while await _generation(newer.http_client) != NEWER:
            assert asyncio.get_running_loop().time() - started <= interval + 3.0, "not restored within one poll interval"
            await asyncio.sleep(0.05)
    finally:
        await newer.http_client.record_guard.stop()
    assert asyncio.get_running_loop().time() - started <= interval + 3.0


async def test_writes_made_in_the_window_are_refused_transiently_and_none_is_lost(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _, newer, keys = await _after_a_stale_write(env, monkeypatch, keys=12)

    # outside a transaction the client widens the schema and sends the write again
    for key in keys[:6]:
        assert await newer.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})

    # inside a transaction the refusal aborts it and is raised as the transient error; its owner runs the unit again
    assert await newer.http_client.update_collection_schema(RECORDS, _older_schema())  # the window opens again
    attempts = 0
    pending = keys[6:]
    while pending:
        attempts += 1
        assert attempts <= 4, "the transaction never landed"
        txn = await newer.begin_transaction(read=[RECORDS], write=[RECORDS])
        try:
            for key in pending:
                await newer.update_node(key, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)
            await newer.commit_transaction(txn)
            pending = []
        except RecordSchemaNarrowError:
            assert attempts == 1, "refused again after the schema was widened"
            assert await newer.http_client.record_guard.wait_wide(5.0)

    assert await _generation(newer.http_client) == NEWER
    for key in keys:
        landed = await newer.get_document(key, RECORDS)
        assert landed["indexingStatus"] == "QUEUED" and landed["pageCount"] == 12, (key, landed)  # the write landed, no field was lost


async def test_a_newer_schema_that_lands_while_the_lease_is_renewed_is_not_overwritten(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The last look before the write is after the renewal, so the pause that matters most cannot hide a newer schema."""
    older, newer = env
    assert await older.http_client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(older.http_client, monkeypatch)
    guard: RecordSchemaGuard = older.http_client.record_guard

    class RenewalIsSlow(ArangoLock):
        async def renew(self, ttl_ms: int) -> bool:
            assert await newer.http_client.update_collection_schema(RECORDS, _newer_schema())  # lands during the renewal
            return await super().renew(ttl_ms)

    async def factory() -> ArangoLock:
        return RenewalIsSlow(older.http_client)

    monkeypatch.setattr(guard, "_lock_factory", factory)

    assert await guard.ensure_wide("test") is False
    assert puts["n"] == 0 and await _generation(older.http_client) == NEWER


# ---- when the write may be in flight, and when no quiet point was found ------------------------------------------------------


async def test_a_write_that_got_no_answer_leaves_the_lock_to_expire(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The client gave up on the schema write, which the server may still apply: the lock document stays, and a second process cannot
    take the lock until the lease runs out."""
    first, second = env
    client = first.http_client
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    monkeypatch.setattr(guard_module, "LOCK_TTL_MS", 600)
    guard: RecordSchemaGuard = client.record_guard
    real_put = client.update_collection_schema

    async def gives_up(name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        if timeout is not None:
            return False  # a client timeout is a false answer
        return await real_put(name, schema, timeout)

    monkeypatch.setattr(client, "update_collection_schema", gives_up)
    assert await guard.ensure_wide("test") is False

    rival = ArangoLock(second.http_client)
    assert await rival.acquire(15_000) is False, "the lock was kept while the write may be in flight"
    await asyncio.sleep(0.9)  # the lease runs out
    assert await rival.acquire(15_000) is True
    await rival.release()


async def test_no_quiet_point_means_no_schema_write_and_a_free_lock(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A schema change waits for every open stream transaction, so one sent while a transaction is open could outlast its timeout and
    the lease. It is not sent; the lock is free for the next try."""
    first, second = env
    client = first.http_client
    org = new_org()
    key = f"{org}-held"
    assert await first.batch_upsert_nodes([base_record(key, org)], RECORDS)
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(client, monkeypatch)
    monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
    held = await second.begin_transaction(read=[RECORDS], write=[RECORDS])
    try:
        assert await second.update_node(key, RECORDS, {"indexingStatus": "QUEUED"}, transaction=held)  # the transaction is open and has written
        assert await client.record_guard.ensure_wide("test") is False
        assert puts["n"] == 0 and await _generation(client) == 0
        assert await ArangoLock(second.http_client).acquire(15_000) is True, "the lock was released"
    finally:
        await second.rollback_transaction(held)


async def test_a_transaction_that_is_always_open_cannot_keep_the_schema_narrow(env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic: one stream transaction stays open for every try. The first FORCE_AFTER_SKIPS (three) widenings are skipped; the next is sent
    anyway, waits for the transaction, and lands when it ends. Without the escalation the schema would stay narrow for good."""
    first, second = env
    client = first.http_client
    org = new_org()
    key = f"{org}-open"
    assert await first.batch_upsert_nodes([base_record(key, org)], RECORDS)
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 2.0)
    monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
    held = await second.begin_transaction(read=[RECORDS], write=[RECORDS])
    assert await second.update_node(key, RECORDS, {"indexingStatus": "QUEUED"}, transaction=held)
    guard: RecordSchemaGuard = client.record_guard
    results: list[bool] = []
    try:
        for _ in range(guard_module.FORCE_AFTER_SKIPS):
            results.append(await guard.ensure_wide("test"))
            assert await _generation(client) == 0, "skipped: nothing was sent"

        async def the_transaction_ends() -> None:
            await asyncio.sleep(guard_module.REWIDEN_TIMEOUT_S + 0.5)  # after the quiet wait gave up, while the forced write is waiting
            await second.rollback_transaction(held)

        ender = asyncio.get_running_loop().create_task(the_transaction_ends())
        results.append(await guard.ensure_wide("test"))  # forced: waits for the transaction, then lands
        await ender
    finally:
        with contextlib.suppress(Exception):
            await second.rollback_transaction(held)

    assert guard_module.FORCE_AFTER_SKIPS == 3
    assert results == [False, False, False, True]
    assert await _generation(client) == RECORD_SCHEMA_GENERATION


async def test_a_widening_that_timed_out_with_its_lease_held_is_not_repeated_and_lands_when_the_server_applies_it(
    env: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forced widening waits for an open transaction and the client gives up: the lease stays (the server may still apply the change), so the
    next tries are refused without a second widening, and the schema becomes wide when the transaction ends and the server applies the first."""
    first, second = env
    client = first.http_client
    org = new_org()
    key, open_key = f"{org}-pending", f"{org}-open"
    assert await first.batch_upsert_nodes([{**base_record(key, org), **DIAGNOSTICS}, base_record(open_key, org)], RECORDS)
    assert await client.update_collection_schema(RECORDS, narrow_schema())
    puts = _puts(client, monkeypatch)
    monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.4)
    monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
    guard: RecordSchemaGuard = client.record_guard
    guard._skips = guard_module.FORCE_AFTER_SKIPS  # the next widening is the forced one
    held = await second.begin_transaction(read=[RECORDS], write=[RECORDS])
    try:
        assert await second.update_node(open_key, RECORDS, {"indexingStatus": "QUEUED"}, transaction=held)  # a clean record: the narrow schema accepts it
        assert await guard.ensure_wide("test") is False  # sent, waits for the transaction, the client gives up
        assert puts["n"] == 1
        for _ in range(3):  # the owner tries again: refused, and no second widening while the lease is held
            assert await guard.ensure_wide("test") is False
            assert puts["n"] == 1
    finally:
        await second.rollback_transaction(held)  # the server can now apply the first change

    deadline = asyncio.get_running_loop().time() + 15
    while await _generation(client) != RECORD_SCHEMA_GENERATION:
        assert asyncio.get_running_loop().time() < deadline, "the pending change was never applied"
        await asyncio.sleep(0.1)
    assert await first.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
    assert puts["n"] == 1
