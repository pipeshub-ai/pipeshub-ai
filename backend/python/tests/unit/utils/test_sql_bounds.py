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
    ],
)
def test_timeout_errors_are_explained(message: str) -> None:
    out = friendly_sql_error(message)
    assert f"{SQL_QUERY_TIMEOUT_S}s limit" in out and message in out


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


# ---- Snowflake client: ALTER SESSION + fetchmany ----


def test_snowflake_sets_the_session_timeout_and_reads_at_most_the_cap() -> None:
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
    assert cursor.execute.call_args_list[0].args == (
        f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {SQL_QUERY_TIMEOUT_S}",
    )
    assert cursor.execute.call_args_list[1].args == ("SELECT id FROM t",)


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
