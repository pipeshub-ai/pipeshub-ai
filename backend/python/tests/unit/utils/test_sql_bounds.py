"""Agent SQL is bounded: at most SQL_MAX_ROWS (+1, to see a cut) rows are read,
and each engine's own statement timeout is set. Drivers are replaced by fakes
that fail if more rows than asked for are requested."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.mariadb.mariadb import MariaDBClient
from app.sources.client.postgres.postgres import PostgreSQLClient
from app.sources.client.snowflake.snowflake import SnowflakeSDKClient
from app.utils.sql_limits import (
    SQL_CELL_MAX_CHARS,
    SQL_MAX_ROWS,
    SQL_QUERY_TIMEOUT_S,
    cap_rows,
    clip_cells,
    friendly_sql_error,
)

BIG = SQL_MAX_ROWS + 500


def test_cap_rows_cuts_and_says_so() -> None:
    assert cap_rows(list(range(BIG)))[1] is True
    assert len(cap_rows(list(range(BIG)))[0]) == SQL_MAX_ROWS
    assert cap_rows(list(range(SQL_MAX_ROWS)))[1] is False


def test_clip_cells_shortens_text_only() -> None:
    row = clip_cells({"t": "x" * 500, "n": 12345678, "s": "ok"})
    assert len(row["t"]) == SQL_CELL_MAX_CHARS and row["t"].endswith("...")
    assert (row["n"], row["s"]) == (12345678, "ok")


@pytest.mark.parametrize(
    "message",
    [
        "canceling statement due to statement timeout",
        "Query execution was interrupted (max_statement_time exceeded)",
        "Statement reached its statement or warehouse timeout of 120 second(s) and was canceled.",
        "SQL execution was cancelled by the client due to a timeout. Error message received from the server: "
        "SQL execution canceled",
    ],
)
def test_timeout_errors_are_explained(message: str) -> None:
    out = friendly_sql_error(message)
    assert "hit a time limit" in out and message in out


def test_a_shorter_warehouse_timeout_is_not_reported_as_ours() -> None:
    out = friendly_sql_error("Statement reached its statement or warehouse timeout of 30 second(s) and was canceled.")
    assert "ran longer than" not in out and "of 30 second(s)" in out


@pytest.mark.parametrize(
    "message", ["Query (1234) cancelled on user's request", "Query execution was interrupted"]
)
def test_a_cancel_is_explained_as_a_likely_timeout(message: str) -> None:
    out = friendly_sql_error(message)
    assert "most likely" in out and f"{SQL_QUERY_TIMEOUT_S}s limit" in out and message in out


def test_other_errors_pass_through() -> None:
    assert friendly_sql_error("syntax error at or near FORM") == "syntax error at or near FORM"


# ---- PostgreSQL client: server-side cursor + SET LOCAL statement_timeout ----

PG_CLIENT_TIMEOUT = SQL_QUERY_TIMEOUT_S + 10.0


def _pg_client() -> tuple[PostgreSQLClient, MagicMock, MagicMock]:
    cursor = MagicMock()
    cursor.fetch = AsyncMock(side_effect=lambda n, timeout=None: [(i,) for i in range(min(n, BIG))])
    statement = MagicMock()
    statement.get_attributes.return_value = [SimpleNamespace(name="id")]
    statement.fetch = AsyncMock(side_effect=AssertionError("unbounded fetch"))
    statement.cursor = AsyncMock(return_value=cursor)
    conn = MagicMock()
    conn.prepare = AsyncMock(return_value=statement)
    conn.execute = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.is_closing.return_value = False
    pool.acquire.return_value = ctx
    client = PostgreSQLClient(host="h", database="d", user="u", password="p")
    client._pool = pool
    return client, conn, cursor


@pytest.mark.asyncio
async def test_postgres_reads_at_most_the_cap_through_a_cursor() -> None:
    client, conn, cursor = _pg_client()
    columns, rows = await client.execute_query_raw(
        "SELECT id FROM t", timeout_s=SQL_QUERY_TIMEOUT_S, max_rows=SQL_MAX_ROWS + 1
    )
    cursor.fetch.assert_awaited_once_with(SQL_MAX_ROWS + 1, timeout=PG_CLIENT_TIMEOUT)
    assert (columns, len(rows)) == (["id"], SQL_MAX_ROWS + 1)
    conn.execute.assert_awaited_once_with(f"SET LOCAL statement_timeout = {SQL_QUERY_TIMEOUT_S * 1000}")


@pytest.mark.asyncio
async def test_postgres_read_only_path_is_bounded_too() -> None:
    client, _, cursor = _pg_client()
    _, rows = await client.execute_read_only_query("SELECT id FROM t", SQL_QUERY_TIMEOUT_S, max_rows=SQL_MAX_ROWS + 1)
    cursor.fetch.assert_awaited_once_with(SQL_MAX_ROWS + 1, timeout=PG_CLIENT_TIMEOUT)
    assert len(rows) == SQL_MAX_ROWS + 1


@pytest.mark.asyncio
async def test_postgres_client_timeout_outlasts_the_server_limit() -> None:
    # The connection's 30 s command_timeout would otherwise cancel before the server's 120 s limit.
    client, conn, _ = _pg_client()
    await client.execute_query_raw("SELECT id FROM t", timeout_s=SQL_QUERY_TIMEOUT_S, max_rows=SQL_MAX_ROWS + 1)
    statement = conn.prepare.return_value
    assert conn.prepare.await_args.kwargs["timeout"] > SQL_QUERY_TIMEOUT_S
    assert statement.cursor.await_args.kwargs["timeout"] > SQL_QUERY_TIMEOUT_S


# ---- Snowflake client: per-call timeout + fetchmany ----


def test_snowflake_times_out_this_call_only_and_reads_at_most_the_cap() -> None:
    cursor = MagicMock()
    cursor.description = [("ID",)]
    cursor.fetchall.side_effect = AssertionError("unbounded fetch")
    cursor.fetchmany.side_effect = lambda n: [(i,) for i in range(min(n, BIG))]
    client = SnowflakeSDKClient(account_identifier="acct")
    client._connection = MagicMock()
    client._connection.is_closed.return_value = False
    client._connection.cursor.return_value = cursor
    _, rows = client.execute_query_raw("SELECT id FROM t", timeout_s=SQL_QUERY_TIMEOUT_S, max_rows=SQL_MAX_ROWS + 1)
    assert len(rows) == SQL_MAX_ROWS + 1
    cursor.fetchmany.assert_called_once_with(SQL_MAX_ROWS + 1)
    # Snowflake enforces it for this statement; an ALTER SESSION would stay for every later query,
    # and connector 5.x ignores execute(timeout=...).
    cursor.execute.assert_called_once_with(
        "SELECT id FROM t", _statement_params={"STATEMENT_TIMEOUT_IN_SECONDS": str(SQL_QUERY_TIMEOUT_S)}
    )


def test_snowflake_client_waits_past_the_server_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Connector 4.x cancels at network_timeout and 5.x turns it into query_timeout; 60 s cut 60-120 s queries.
    import app.sources.client.snowflake.snowflake as sf

    connector = MagicMock()
    monkeypatch.setattr(sf, "snowflake_connector", connector)
    SnowflakeSDKClient(account_identifier="acct", user="u", password="p").connect()
    assert connector.connect.call_args.kwargs["network_timeout"] > SQL_QUERY_TIMEOUT_S


# ---- MariaDB client: SET STATEMENT FOR + fetchmany, cursor closed before commit ----


@pytest.mark.asyncio
async def test_mariadb_raw_query_has_a_timeout_and_reads_at_most_the_cap() -> None:
    events: list[str] = []
    cursor = MagicMock()
    cursor.description = [("id",)]
    cursor.execute = AsyncMock(side_effect=lambda q, p=(): events.append(q))
    cursor.fetchall = AsyncMock(side_effect=AssertionError("unbounded fetch"))
    cursor.fetchmany = AsyncMock(side_effect=lambda n: [(i,) for i in range(min(n, BIG))])
    cursor.close = AsyncMock(side_effect=lambda: events.append("close"))
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = ctx
    client = MariaDBClient(host="h", port=3306, database="d", user="u", password="p")
    client._pool = pool

    _, rows = await client.execute_query_raw(
        "SELECT id FROM t", timeout_s=SQL_QUERY_TIMEOUT_S, max_rows=SQL_MAX_ROWS + 1
    )

    assert len(rows) == SQL_MAX_ROWS + 1
    cursor.fetchmany.assert_awaited_once_with(SQL_MAX_ROWS + 1)
    assert events[0] == f"SET STATEMENT max_statement_time={SQL_QUERY_TIMEOUT_S} FOR SELECT id FROM t"
    assert events.index("close") < events.index("commit")
    conn.cursor.assert_called_once_with(buffered=False)


@pytest.mark.asyncio
async def test_mariadb_capped_dict_query_streams_instead_of_buffering() -> None:
    cursor = MagicMock()
    cursor.description = [("id",)]
    cursor.execute = AsyncMock()
    cursor.fetchmany = AsyncMock(return_value=[{"id": 1}])
    cursor.close = AsyncMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = ctx
    client = MariaDBClient(host="h", port=3306, database="d", user="u", password="p")
    client._pool = pool

    await client.execute_query("SELECT id FROM t", read_only=True, max_rows=SQL_MAX_ROWS + 1)

    conn.cursor.assert_called_once_with(dictionary=True, buffered=False)
    cursor.fetchmany.assert_awaited_once_with(SQL_MAX_ROWS + 1)


# ---- MariaDB client: KILL QUERY deadline (max_statement_time does not stop a CALL) ----


def _maria_with(execute, events: list[str]) -> MariaDBClient:
    cursor = MagicMock()
    cursor.description = None
    cursor.rowcount = 0
    cursor.execute = AsyncMock(side_effect=execute)
    cursor.close = AsyncMock()
    conn = MagicMock()
    conn.connection_id = 42
    conn.cursor.return_value = cursor
    conn.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    conn.rollback = AsyncMock(side_effect=lambda: events.append("rollback"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value = ctx
    client = MariaDBClient(host="h", port=3306, database="d", user="u", password="p")
    client._pool = pool
    client._connect_kwargs = AsyncMock(return_value={})
    return client


@pytest.fixture
def short_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.sources.client.mariadb.mariadb as mariadb_client

    # timeout_s=1 below: the kill is due 0.05 s in.
    monkeypatch.setattr(mariadb_client, "READ_TIMEOUT_MARGIN_S", -0.95)


@pytest.mark.asyncio
async def test_mariadb_kills_a_call_that_outlives_the_limit(
    monkeypatch: pytest.MonkeyPatch, short_deadline: None
) -> None:
    import asyncio

    import app.sources.client.mariadb.mariadb as mariadb_client

    loop = asyncio.get_running_loop()
    killed = asyncio.Event()
    kills: list[int] = []

    def fake_kill(kwargs: dict, thread_id: int) -> None:
        kills.append(thread_id)
        loop.call_soon_threadsafe(killed.set)

    async def stuck_procedure(query: str, params: tuple = ()) -> None:
        await killed.wait()
        raise RuntimeError("Query execution was interrupted")

    monkeypatch.setattr(mariadb_client, "_kill_query", fake_kill)
    events: list[str] = []
    client = _maria_with(stuck_procedure, events)

    with pytest.raises(RuntimeError, match="interrupted"):
        await client.execute_query("CALL slow()", timeout_s=1)

    assert kills == [42]
    assert events == ["rollback"]


@pytest.mark.asyncio
async def test_mariadb_does_not_kill_a_statement_that_ends_in_time(
    monkeypatch: pytest.MonkeyPatch, short_deadline: None
) -> None:
    import asyncio

    import app.sources.client.mariadb.mariadb as mariadb_client

    kills: list[int] = []
    monkeypatch.setattr(mariadb_client, "_kill_query", lambda kwargs, thread_id: kills.append(thread_id))
    events: list[str] = []
    client = _maria_with(lambda query, params=(): None, events)

    await client.execute_query("UPDATE t SET x = 1", timeout_s=1)
    await asyncio.sleep(0.15)

    assert kills == [] and events == ["commit"]


@pytest.mark.asyncio
async def test_mariadb_lets_a_late_kill_land_before_the_connection_moves_on(
    monkeypatch: pytest.MonkeyPatch, short_deadline: None
) -> None:
    # A kill sent after the commit could stop whatever the pooled connection runs next.
    import asyncio
    import time

    import app.sources.client.mariadb.mariadb as mariadb_client

    events: list[str] = []

    def slow_kill(kwargs: dict, thread_id: int) -> None:
        time.sleep(0.2)
        events.append("kill")

    async def ends_just_after_the_deadline(query: str, params: tuple = ()) -> None:
        await asyncio.sleep(0.1)

    monkeypatch.setattr(mariadb_client, "_kill_query", slow_kill)
    client = _maria_with(ends_just_after_the_deadline, events)

    await client.execute_query("CALL quick()", timeout_s=1)

    assert events == ["kill", "commit"]
