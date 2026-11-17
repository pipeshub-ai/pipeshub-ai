"""The records-schema widening lock, kept in ArangoDB itself, on a real server (3.12.4) with no Redis.

A lease lock in one document: one holder at a time, an expired lease taken over, a holder that lost its lease fenced out of the
schema write, and a lock store that cannot be written means no schema write at all (the refused write stays refused and the poll
tries again). Expected: an older process never overwrites a newer schema.
"""

from __future__ import annotations

import asyncio
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
