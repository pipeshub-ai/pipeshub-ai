"""SR-5, SR-6 (QA24-O3): the MariaDB and Redshift clients report a statement that may have been applied, and never
send it twice.

The clients are the production ``MariaDBClient`` (Connector/Python's async pool) and ``RedshiftClient``
(redshift_connector), each against a loopback server speaking its wire protocol. Neither driver nor client re-sends
a statement; before this WP neither reported one whose answer was lost.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from app.sources.client.mariadb.mariadb import MariaDBClient
from app.sources.client.redshift.redshift import RedshiftClient
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import (
    FakeSQL,
    black_hole_handler,
    closed_port,
    mariadb_handler,
    real_mariadb,
    redshift_handler,
    serve,
)

pytestmark = pytest.mark.usefixtures(real_mariadb.__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

INSERT = "INSERT INTO notes VALUES (1)"
SELECT = "SELECT id FROM notes"

@asynccontextmanager
async def mariadb(port: int) -> AsyncIterator[Callable[[str], Awaitable[object]]]:
    client = MariaDBClient(host="127.0.0.1", user="qa", password="qa", port=port, timeout=5)
    try:
        yield client.execute_query
    finally:
        await client.close()


@asynccontextmanager
async def redshift(port: int) -> AsyncIterator[Callable[[str], Awaitable[object]]]:
    client = RedshiftClient(host="127.0.0.1", database="qa", user="qa", password="qa", port=port, timeout=5, ssl=False)

    async def run(statement: str) -> object:
        return await asyncio.to_thread(client.execute_query, statement)

    try:
        yield run
    finally:
        client.close()


DATABASES = [(mariadb, mariadb_handler), (redshift, redshift_handler)]
IDS = ["mariadb", "redshift"]


@pytest.mark.parametrize(("database", "handler"), DATABASES, ids=IDS)
@pytest.mark.parametrize("lost", ["INSERT", "COMMIT"])
async def test_sr_5_6_a_write_whose_answer_is_lost_is_applied_once_and_reported(
    database: object, handler: object, lost: str
) -> None:
    fake = FakeSQL(drop_on=lost)
    with serve(handler(fake), http=False) as address:  # type: ignore[operator]
        async with database(int(address.rsplit(":", 1)[1])) as run:  # type: ignore[operator]
            with track_sends() as sends, pytest.raises(Exception):  # noqa: B017 - each driver's own error
                await run(INSERT)
    assert [s for s in fake.received if s.lower() != "commit"] == [INSERT]
    assert sends.ambiguous


@pytest.mark.parametrize(("database", "handler"), DATABASES, ids=IDS)
async def test_sr_5_6_a_select_whose_answer_is_lost_is_not_reported(database: object, handler: object) -> None:
    fake = FakeSQL(drop_on="SELECT")
    with serve(handler(fake), http=False) as address:  # type: ignore[operator]
        async with database(int(address.rsplit(":", 1)[1])) as run:  # type: ignore[operator]
            with track_sends() as sends, pytest.raises(Exception):  # noqa: B017
                await run(SELECT)
    assert fake.received == [SELECT]
    assert not sends.ambiguous


@pytest.mark.parametrize("database", [mariadb, redshift], ids=IDS)
async def test_sr_5_6_a_write_that_never_left_fails_at_once_and_is_not_reported(database: object) -> None:
    """MariaDB's pool retried a refused connect forever without yielding (SR-F3); it now fails the query."""
    async with database(closed_port()) as run:  # type: ignore[operator]
        with track_sends() as sends, pytest.raises(ConnectionError):
            await run(INSERT)
    assert not sends.ambiguous


async def test_sr_6_a_lost_connection_surfaces_the_querys_error_not_the_rollbacks() -> None:
    fake = FakeSQL(drop_on="INSERT")
    with serve(redshift_handler(fake), http=False) as address, pytest.raises(RuntimeError, match="Query execution failed"):
        async with redshift(int(address.rsplit(":", 1)[1])) as run:
            await run(INSERT)


async def test_sr_15_other_coroutines_run_while_mariadb_connects_to_a_hung_server() -> None:
    """SR-O4: the binary driver connected with a blocking call on the event loop, stalling every run on the worker
    for the whole connect timeout. The connect now yields while it waits, and gives up at the client's timeout."""
    accepted = threading.Event()
    with serve(black_hole_handler(accepted), http=False) as address:
        client = MariaDBClient(host="127.0.0.1", user="qa", password="qa", port=int(address.rsplit(":", 1)[1]), timeout=1)
        query = asyncio.create_task(client.execute_query(INSERT))
        assert await asyncio.get_running_loop().run_in_executor(None, accepted.wait, 30)
        for _ in range(100):
            await asyncio.sleep(0)
        assert not query.done(), "the connect held the event loop until it gave up"
        with track_sends() as sends, pytest.raises(ConnectionError):
            await query
        await client.close()
    assert not sends.ambiguous
