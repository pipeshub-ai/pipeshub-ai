"""MariaDB agent tools, driven through the real `MariaDBClient` and
`MariaDBDataSource`. Only the driver's connection pool is replaced, by a fake
that records every statement and its bound parameters.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from app.agents.actions.mariadb.mariadb import MariaDB
from app.modules.transformers.blob_storage import BlobStorage
from app.sources.client.mariadb import mariadb as mariadb_client_module
from app.sources.client.mariadb.mariadb import MariaDBClient
from app.utils.conversation_tasks import pop_tasks

if TYPE_CHECKING:
    from types import ModuleType


def _import_real_driver() -> ModuleType:
    # tests/unit/sources/client/test_mariadb_client.py swaps a MagicMock into
    # sys.modules["mariadb"] and into the client module for the whole session.
    current = sys.modules.get("mariadb")
    if current is not None and not isinstance(current, MagicMock):
        return current
    sys.modules.pop("mariadb", None)
    try:
        return importlib.import_module("mariadb")
    finally:
        if current is not None:
            sys.modules["mariadb"] = current


mariadb_driver = _import_real_driver()

PASSWORD = "s3cr3t-Pa55word!"
DATABASE = "shop"

Responder = Callable[[str, tuple], "list[dict[str, Any]] | None"]


def _flat(query: str) -> str:
    return " ".join(query.split())


class _FakeCursor:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool
        self.description: list[tuple] | None = None
        self.rowcount = 0
        self._rows: list[dict[str, Any]] = []

    async def execute(self, query: str, params: tuple = ()) -> None:
        params = tuple(params)
        self._pool.calls.append((_flat(query), params))
        rows = self._pool.responder(_flat(query), params)
        if rows is None:
            self.description = None
            self.rowcount = 1
            return
        self.description = [(k,) for k in (rows[0] if rows else {"_": None})]
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    async def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    def cursor(self, dictionary: bool = False) -> _FakeCursor:
        return _FakeCursor(self._pool)


class _FakePool:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, tuple]] = []
        self.connections: list[_FakeConnection] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_FakeConnection]:
        conn = _FakeConnection(self)
        self.connections.append(conn)
        yield conn

    async def close(self) -> None:
        return None

    def statements(self) -> list[str]:
        return [q for q, _ in self.calls]


class _Catalog:
    """Answers the information_schema queries `MariaDBDataSource` issues."""

    def __init__(self, tables: dict[str, dict[str, Any]], views: list[str] | None = None,
                 on_query: Responder | None = None) -> None:
        self.tables = tables
        self.views = views or []
        self.on_query = on_query

    def __call__(self, query: str, params: tuple) -> list[dict[str, Any]] | None:
        if query.startswith("USE "):
            return None
        if query.startswith("SHOW CREATE TABLE"):
            return [{"Table": "t", "Create Table": f"CREATE TABLE ... /* {query} */"}]
        if "information_schema.VIEWS" in query:
            return [{"name": v, "database": params[0], "definition": "select 1"} for v in self.views]
        if "CONSTRAINT_TYPE = 'PRIMARY KEY'" in query:
            return [{"column_name": c} for c in self.tables.get(params[1], {}).get("pk", [])]
        if "CONSTRAINT_TYPE = 'FOREIGN KEY'" in query:
            return list(self.tables.get(params[1], {}).get("fk", []))
        if "CONSTRAINT_TYPE = 'UNIQUE'" in query or "CHECK_CONSTRAINTS" in query:
            return []
        if "information_schema.COLUMNS" in query:
            return [{"name": c, "data_type": "int", "nullable": 0}
                    for c in self.tables.get(params[1], {}).get("columns", [])]
        if "information_schema.TABLES" in query and "TABLE_NAME = ?" in query:
            name = params[1]
            return [{"name": name, "database": params[0], "type": "BASE TABLE"}] if name in self.tables else []
        if "information_schema.TABLES" in query:
            return [{"name": n, "database": params[0], "type": "BASE TABLE"} for n in self.tables]
        if self.on_query is not None:
            return self.on_query(query, params)
        raise AssertionError(f"unexpected statement: {query}")


SHOP = {
    "orders": {"columns": ["id", "customer_id"], "pk": ["id"],
               "fk": [{"constraint_name": "fk_c", "column_name": "customer_id",
                       "foreign_database": DATABASE, "foreign_table_name": "customers",
                       "foreign_column_name": "id"}]},
    "customers": {"columns": ["id", "email"], "pk": ["id"], "fk": []},
}


@pytest.fixture
def make_tool() -> Callable[..., tuple[MariaDB, _FakePool]]:
    patches: list[Any] = []

    def _make(responder: Responder | None = None, *, database: str | None = DATABASE,
              state: dict[str, Any] | None = None, pool_error: Exception | None = None,
              ) -> tuple[MariaDB, _FakePool]:
        pool = _FakePool(responder or _Catalog(SHOP))
        create_pool = AsyncMock(side_effect=pool_error) if pool_error else AsyncMock(return_value=pool)
        for p in (patch.object(mariadb_client_module, "mariadb", mariadb_driver),
                  patch.object(mariadb_driver, "create_async_pool", create_pool)):
            p.start()
            patches.append(p)
        client = MariaDBClient(host="db.internal", user="agent", password=PASSWORD,
                               database=database, port=3306)
        return MariaDB(client, state if state is not None else {}), pool

    yield _make
    for p in patches:
        p.stop()


def _body(result: tuple[bool, str]) -> dict[str, Any]:
    return json.loads(result[1])


def test_driver_is_the_real_package_not_a_stand_in(make_tool) -> None:
    make_tool()
    assert mariadb_client_module.mariadb is mariadb_driver
    assert not isinstance(mariadb_driver, MagicMock)
    assert "site-packages" in mariadb_driver.__file__
    assert issubclass(mariadb_driver.OperationalError, mariadb_driver.Error)
    assert callable(mariadb_driver.create_async_pool)


class TestSchemaTools:
    async def test_list_tables_binds_the_configured_database(self, make_tool) -> None:
        tool, pool = make_tool()
        result = await tool.list_tables()

        assert result[0] is True
        body = _body(result)
        assert [t["name"] for t in body["tables"]] == ["orders", "customers"]
        assert (body["database"], body["table_count"]) == (DATABASE, 2)
        assert pool.calls[0][1] == (DATABASE,)

    async def test_get_table_ddl_quotes_the_table_name(self, make_tool) -> None:
        tool, pool = make_tool()
        hostile = "orders`; DROP TABLE customers; --"
        ok, _ = await tool.get_table_ddl(table=hostile)

        assert ok is True
        assert pool.statements() == ["SHOW CREATE TABLE `shop`.`orders``; DROP TABLE customers; --`"]

    async def test_database_name_is_quoted_too(self, make_tool) -> None:
        tool, pool = make_tool(database="we`ird")
        await tool.get_table_ddl(table="orders")
        assert pool.statements() == ["SHOW CREATE TABLE `we``ird`.`orders`"]

    async def test_table_names_are_bound_never_spliced(self, make_tool) -> None:
        tool, pool = make_tool()
        hostile = "x' OR '1'='1"
        result = await tool.get_tables_schema(tables=[hostile, "orders"])

        assert result[0] is True
        assert all(hostile not in query for query, _ in pool.calls)
        assert any(hostile in params for _, params in pool.calls)

    async def test_get_tables_schema_collects_keys_and_reports_missing_tables(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.get_tables_schema(tables=["orders", "ghost", ""]))

        by_name = {t["name"]: t for t in body["tables"]}
        assert by_name["orders"]["primary_keys"] == ["id"]
        assert by_name["orders"]["foreign_keys"][0]["foreign_table_name"] == "customers"
        assert [c["name"] for c in by_name["orders"]["columns"]] == ["id", "customer_id"]
        assert by_name["ghost"]["error"] == "Table not found"
        assert body["table_count"] == 2

    async def test_get_tables_schema_accepts_one_table_name_as_text(self, make_tool) -> None:
        tool, pool = make_tool()
        body = _body(await tool.get_tables_schema(tables="orders"))

        assert [t["name"] for t in body["tables"]] == ["orders"]
        assert all(params[1:] in ((), ("orders",)) for _, params in pool.calls)

    @pytest.mark.parametrize(("include_views", "expected"), [(True, ["v_sales"]), (False, [])])
    async def test_fetch_db_schema(self, make_tool, include_views: bool, expected: list[str]) -> None:
        tool, pool = make_tool(_Catalog(SHOP, views=["v_sales"]))
        body = _body(await tool.fetch_db_schema(include_views=include_views))

        db = body["databases"][0]
        assert (db["name"], db["table_count"]) == (DATABASE, 2)
        assert [v["name"] for v in db["views"]] == expected
        assert {t["name"]: t["primary_keys"] for t in db["tables"]} == {"orders": ["id"], "customers": ["id"]}
        assert any("information_schema.VIEWS" in q for q in pool.statements()) is include_views

    async def test_read_tools_only_read(self, make_tool) -> None:
        tool, pool = make_tool(_Catalog(SHOP, views=["v_sales"]))
        await tool.list_tables()
        await tool.get_table_ddl(table="orders")
        await tool.get_tables_schema(tables=["orders"])
        await tool.fetch_db_schema()

        assert pool.statements()
        assert all(q.split()[0] in {"SELECT", "SHOW"} for q in pool.statements())

    @pytest.mark.parametrize(
        ("method", "kwargs"),
        [("list_tables", {}), ("get_table_ddl", {"table": "t"}),
         ("get_tables_schema", {"tables": ["t"]}), ("fetch_db_schema", {}),
         ("execute_query", {"query": "SELECT 1"})],
    )
    async def test_no_default_database_touches_nothing(self, make_tool, method: str, kwargs: dict) -> None:
        tool, pool = make_tool(database=None)
        ok, payload = await getattr(tool, method)(**kwargs)

        assert ok is False
        assert json.loads(payload)["error"] == "No database selected"
        assert pool.calls == []

    @pytest.mark.parametrize(
        ("method", "kwargs", "message"),
        [("get_table_ddl", {"table": ""}, "Missing required parameter: table"),
         ("get_tables_schema", {"tables": []}, "Missing required parameter: tables"),
         ("execute_query", {"query": ""}, "Missing required parameter: query")],
    )
    async def test_missing_arguments(self, make_tool, method: str, kwargs: dict, message: str) -> None:
        tool, pool = make_tool()
        ok, payload = await getattr(tool, method)(**kwargs)
        assert (ok, json.loads(payload)["error"]) == (False, message)
        assert pool.calls == []


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"id": i, "email": f"user{i}@example.com"} for i in range(n)]


class TestExecuteQuery:
    async def test_runs_the_query_unchanged_in_the_default_database(self, make_tool) -> None:
        sql = "SELECT id, email FROM customers WHERE email LIKE '%@example.com'"
        tool, pool = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(2)))
        result = await tool.execute_query(query=sql)

        assert result[0] is True
        assert pool.statements() == ["USE `shop`", sql]
        body = _body(result)
        assert (body["row_count"], body["database"]) == (2, DATABASE)
        assert "truncated" not in body

    async def test_display_is_capped_at_100_rows(self, make_tool) -> None:
        tool, _ = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(250)))
        body = _body(await tool.execute_query(query="SELECT * FROM customers"))

        assert body["row_count"] == 250
        assert len(body["data"]) == 100
        assert (body["truncated"], body["displayed_row_count"]) == (True, 100)

    async def test_full_result_is_exported_as_csv(self, make_tool) -> None:
        blob = create_autospec(BlobStorage, instance=True)
        blob.save_conversation_file_to_storage.return_value = {"url": "https://files/x.csv"}
        state = {"conversation_id": "conv-maria", "org_id": "org-1", "blob_storage": blob}
        tool, _ = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(250)), state=state)

        await tool.execute_query(query="SELECT * FROM customers")
        tasks = pop_tasks("conv-maria")
        assert len(tasks) == 1
        assert await tasks[0] == {"type": "csv_download", "url": "https://files/x.csv"}

        kwargs = blob.save_conversation_file_to_storage.await_args.kwargs
        assert (kwargs["org_id"], kwargs["conversation_id"]) == ("org-1", "conv-maria")
        lines = kwargs["file_bytes"].decode().splitlines()
        assert lines[0] == "id,email"
        assert len(lines) == 251

    async def test_failed_export_does_not_fail_the_query(self, make_tool) -> None:
        blob = create_autospec(BlobStorage, instance=True)
        blob.save_conversation_file_to_storage.side_effect = OSError("bucket gone")
        state = {"conversation_id": "conv-maria-2", "org_id": "org-1", "blob_storage": blob}
        tool, _ = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(3)), state=state)

        ok, _ = await tool.execute_query(query="SELECT * FROM customers")
        tasks = pop_tasks("conv-maria-2")
        assert ok is True
        assert await tasks[0] is None

    async def test_no_export_without_a_conversation(self, make_tool) -> None:
        tool, _ = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(3)), state={"org_id": "org-1"})
        await tool.execute_query(query="SELECT * FROM customers")
        assert pop_tasks("") == []

    async def test_statement_without_rows_reports_affected_rows(self, make_tool) -> None:
        tool, pool = make_tool(_Catalog(SHOP, on_query=lambda q, p: None))
        body = _body(await tool.execute_query(query="UPDATE customers SET email = NULL WHERE id = 1"))

        assert body["data"] == [{"affected_rows": 1}]
        assert all(c.commit.await_count == 1 for c in pool.connections)

    async def test_sql_error_is_returned_and_rolled_back(self, make_tool) -> None:
        def fail(q: str, p: tuple) -> None:
            raise mariadb_driver.ProgrammingError("You have an error in your SQL syntax near 'FORM'")

        tool, pool = make_tool(_Catalog(SHOP, on_query=fail))
        ok, payload = await tool.execute_query(query="SELECT * FORM customers")

        assert ok is False
        body = json.loads(payload)
        assert "error in your SQL syntax" in body["error"]
        assert body["message"] == "Query execution failed"
        assert pool.connections[-1].rollback.await_count == 1

    async def test_failed_use_stops_before_the_query(self, make_tool) -> None:
        def responder(q: str, p: tuple) -> None:
            raise mariadb_driver.OperationalError("Unknown database 'shop'")

        tool, pool = make_tool(responder)
        ok, payload = await tool.execute_query(query="SELECT 1")

        assert ok is False
        assert "Unknown database" in json.loads(payload)["error"]
        assert pool.statements() == ["USE `shop`"]


class TestConnectionFailures:
    @pytest.mark.parametrize(
        "driver_error",
        [
            mariadb_driver.OperationalError("Can't connect to server on 'db.internal' (111)"),
            mariadb_driver.OperationalError(
                "Access denied for user 'agent'@'10.0.0.5' (using password: YES)"),
        ],
    )
    async def test_becomes_a_tool_error_without_secrets(
        self, make_tool, caplog: pytest.LogCaptureFixture, driver_error: Exception,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        tool, _ = make_tool(pool_error=driver_error)

        results = [await tool.list_tables(), await tool.execute_query(query="SELECT 1"),
                   await tool.get_table_ddl(table="orders")]

        for ok, payload in results:
            assert ok is False
            body = json.loads(payload)
            assert body["error"].startswith("Failed to connect to MariaDB: ")
            assert str(driver_error) in body["error"]
            assert "Traceback" not in payload
            assert PASSWORD not in payload
        assert caplog.records
        assert PASSWORD not in caplog.text

    async def test_fetch_db_schema_does_not_report_an_unreachable_database_as_empty(
        self, make_tool,
    ) -> None:
        tool, _ = make_tool(pool_error=mariadb_driver.OperationalError(
            "Can't connect to server on 'db.internal' (111)"))
        ok, payload = await tool.fetch_db_schema()

        assert ok is False
        body = json.loads(payload)
        assert body["error"].startswith("Failed to connect to MariaDB: ")
        assert "Schema fetched successfully" not in payload

    async def test_successful_run_logs_no_password(self, make_tool, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        tool, _ = make_tool(_Catalog(SHOP, on_query=lambda q, p: _rows(1)))

        outputs = [await tool.list_tables(), await tool.fetch_db_schema(),
                   await tool.execute_query(query="SELECT 1")]

        assert caplog.records
        assert PASSWORD not in caplog.text
        assert all(PASSWORD not in payload for _, payload in outputs)
