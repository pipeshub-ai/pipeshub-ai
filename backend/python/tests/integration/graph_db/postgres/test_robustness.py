"""An explicit transaction never leaves a caller waiting: a unit that cannot open, a lost connection and a cancel each end it with an error and give
the connection back."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import uuid

import pytest

from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.base import TransactionClosed
from app.services.graph_db.postgres.provider import PostgresGraphProvider

pytestmark = pytest.mark.integration


def _org() -> str:
    return uuid.uuid4().hex[:24]


@pytest.fixture
async def one_connection():
    """A provider whose pool holds a single connection: a second open unit has nowhere to go."""
    if not os.environ.get("PG_CORE_URL"):
        pytest.skip("PG_CORE_URL is not set")
    db = KernelDB(dataclasses.replace(DbConfig.from_env("connectors"), pool_max=1))
    await db.start()
    yield PostgresGraphProvider(logging.getLogger("txn"), db), db
    await db.close()


@pytest.mark.timeout(150)    # the pool wait is the connectors class budget, 60 s
async def test_a_unit_that_cannot_open_ends_the_call_with_the_error(one_connection) -> None:
    p, _ = one_connection
    with bind(OrgContext(_org(), None, "job", "job:test", "t")):
        t1 = await p.begin_transaction([], [])
        await p.get_document("x", "records", transaction=t1)       # holds the only connection
        t2 = await p.begin_transaction([], [])
        with pytest.raises(Exception) as err:                       # PoolTimeout, not a wait for ever
            await asyncio.wait_for(p.get_document("y", "records", transaction=t2, raise_on_error=True), 100)
        assert not isinstance(err.value, TimeoutError)
        with pytest.raises(Exception):                              # later calls on the failed transaction fail at once
            await asyncio.wait_for(p.get_document("y", "records", transaction=t2, raise_on_error=True), 5)
        await asyncio.wait_for(p.rollback_transaction(t2), 5)
        await p.rollback_transaction(t1)
        assert await p.get_document("x", "records") is None         # the connection is back


async def test_a_lost_connection_fails_the_call_and_the_transaction(one_connection) -> None:
    p, _ = one_connection
    with bind(OrgContext(_org(), None, "job", "job:test", "t")):
        t = await p.begin_transaction([], [])
        pid = await p._q(lambda tx: tx.fetchval("SELECT pg_backend_pid()"), t)
        import asyncpg

        con = await asyncpg.connect(os.environ["PG_CORE_URL"])      # another session of the same login ends it
        try:
            await con.execute("SELECT pg_terminate_backend($1)", pid)
        finally:
            await con.close()
        with pytest.raises(Exception):
            await asyncio.wait_for(p._q(lambda tx: tx.fetchval("SELECT 1"), t), 30)
        with pytest.raises(Exception):
            await asyncio.wait_for(p._q(lambda tx: tx.fetchval("SELECT 1"), t), 5)
        await asyncio.wait_for(p.rollback_transaction(t), 10)
        assert await p.get_document("x", "records") is None         # the pool is whole again


async def test_a_cancel_mid_transaction_rolls_back_and_releases_the_connection(one_connection) -> None:
    p, _ = one_connection
    with bind(OrgContext(_org(), None, "job", "job:test", "t")):
        t = await p.begin_transaction([], [])
        await p.batch_upsert_nodes([{"_key": "gone", "recordName": "x"}], "records", transaction=t)
        task = asyncio.ensure_future(p._q(lambda tx: tx.fetchval("SELECT pg_sleep(60)"), t))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(TransactionClosed):
            await asyncio.wait_for(p._q(lambda tx: tx.fetchval("SELECT 1"), t), 5)
        await asyncio.wait_for(p.rollback_transaction(t), 5)
        assert await asyncio.wait_for(p.get_document("gone", "records"), 10) is None   # the only connection is free and the half-written row is not there
