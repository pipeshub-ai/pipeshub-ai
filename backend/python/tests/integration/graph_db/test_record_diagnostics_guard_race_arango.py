"""The guard decides "the data's error, not the schema's" from the live schema read AFTER the refusal.

When another process widens the schema between the server's 1620 and the guard's ``is_wide()`` read (every process
polls every 10 s, so with several processes this is the normal way a narrowing ends), the guard:
  outside a transaction: raises the raw 1620 and does not send the write again, so a write that would land is lost;
  inside a transaction: does not abort it, so a caller that catches and goes on commits the failed multi-document
     statement's partial writes (F-b) and the statement before it.
Expected: the first is sent again and lands; the second leaves the transaction aborted, the commit refused, nothing kept.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from app.schema.arango.documents import record_schema
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    base_record,
    drop_database,
    narrow_schema,
    new_org,
)
from tests.integration.real_graph import backend_unavailable, connect_arango

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("record-diagnostics-guard-race")
DIAG = {"firstQueuedAt": 1_700_000_000_000, "indexingAttempts": 2, "pageCount": 12, "failedAt": 1_700_000_100_000}


@pytest.fixture
async def env():  # noqa: ANN201
    try:
        p = await connect_arango(logger, f"rd_race_{uuid.uuid4().hex[:6]}")
    except Exception as exc:
        backend_unavailable("arango", exc)
    s = Store("arango", p, new_org())
    yield p, s
    await p.http_client.update_collection_schema(RECORDS, record_schema)
    await drop_database(p)
    await p.disconnect()


def _widen_just_before_the_check(p) -> None:
    """Another process's poll widens the schema after the refusal and before this process reads the live schema."""
    guard = p.http_client.record_guard
    original = guard.is_wide

    async def is_wide() -> bool:
        await p.http_client.update_collection_schema(RECORDS, record_schema)
        return await original()

    guard.is_wide = is_wide


async def test_a_refusal_widened_by_another_process_is_sent_again(env) -> None:
    p, s = env
    key = s.key("t")
    assert await p.batch_upsert_nodes([{**base_record(key, s.org), **DIAG}], RECORDS)
    assert await p.http_client.update_collection_schema(RECORDS, narrow_schema())
    _widen_just_before_the_check(p)
    await p.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
    assert (await s.doc(key))["indexingStatus"] == "QUEUED"


async def test_inside_a_transaction_a_caller_that_goes_on_cannot_commit(env) -> None:
    """The refused statement is the transaction's first records write, so nothing holds the other process's PUT back."""
    p, s = env
    a, b = s.key("a"), s.key("b")
    assert await p.batch_upsert_nodes([base_record(a, s.org), {**base_record(b, s.org), **DIAG}], RECORDS)
    assert await p.http_client.update_collection_schema(RECORDS, narrow_schema())
    _widen_just_before_the_check(p)
    txn = await p.begin_transaction(read=[RECORDS], write=[RECORDS])
    try:
        await p.update_node(b, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)
    except Exception as error:  # a handler that logs and goes on
        logger.warning("caught: %s", type(error).__name__)
    p.http_client.record_guard.is_wide = type(p.http_client.record_guard).is_wide.__get__(p.http_client.record_guard)
    committed = True
    try:
        await p.update_node(a, RECORDS, {"indexingStatus": "QUEUED"}, transaction=txn)
        await p.commit_transaction(txn)
    except Exception:
        committed = False
    statuses = [(await s.doc(k))["indexingStatus"] for k in (a, b)]
    assert not committed and statuses == ["NOT_STARTED"] * 2, f"committed={committed} statuses(a,b)={statuses}"
