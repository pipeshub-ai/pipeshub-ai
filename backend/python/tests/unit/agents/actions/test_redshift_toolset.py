"""Redshift agent tools, driven through the real `RedshiftClient` and
`RedshiftDataSource`. Only `redshift_connector.connect` is replaced, by a fake
connection that records every statement and its bound parameters.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
import redshift_connector

from app.agents.actions.redshift.redshift import Redshift
from app.modules.transformers.blob_storage import BlobStorage
from app.sources.client.redshift import redshift as redshift_client_module
from app.sources.client.redshift.redshift import RedshiftClient
from app.utils.conversation_tasks import pop_tasks

PASSWORD = "rs-Pa55word!secret"

Responder = Callable[[str, tuple], "list[dict[str, Any]] | None"]


def _flat(query: str) -> str:
    return " ".join(query.split())


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn
        self.description: list[tuple] | None = None
        self.rowcount = -1
        self._rows: list[tuple] = []

    def execute(self, query: str, params: tuple | None = None) -> None:
        params = tuple(params or ())
        self._conn.calls.append((_flat(query), params))
        rows = self._conn.responder(_flat(query), params)
        if rows is None:
            self.description, self.rowcount = None, 3
            return
        columns = list(rows[0]) if rows else ["_"]
        self.description = [(c,) for c in columns]
        self._rows = [tuple(r.get(c) for c in columns) for r in rows]

    def fetchall(self) -> list[tuple]:
        return list(self._rows)

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        return None

    def statements(self) -> list[str]:
        return [q for q, _ in self.calls]


class _Catalog:
    """Answers the catalog queries `RedshiftDataSource` issues."""

    def __init__(self, schemas: dict[str, dict[str, dict[str, Any]]], views: dict[str, list[str]] | None = None,
                 on_query: Responder | None = None) -> None:
        self.schemas = schemas
        self.views = views or {}
        self.on_query = on_query

    def _table(self, params: tuple) -> dict[str, Any]:
        return self.schemas.get(params[0], {}).get(params[1], {})

    def __call__(self, query: str, params: tuple) -> list[dict[str, Any]] | None:
        if "FROM pg_namespace" in query:
            return [{"name": s, "owner": "admin"} for s in self.schemas]
        if "information_schema.views" in query:
            return [{"name": v, "schema": params[0], "definition": "select 1"} for v in self.views.get(params[0], [])]
        if "check_constraints" in query or "constraint_type = 'UNIQUE'" in query:
            return []
        if "constraint_type = 'PRIMARY KEY'" in query:
            cols = self._table(params).get("pk", [])
            if "constraint_name" in query.split("FROM")[0]:
                return [{"constraint_name": f"{params[1]}_pkey", "column_name": c} for c in cols]
            return [{"column_name": c} for c in cols]
        if "constraint_type = 'FOREIGN KEY'" in query:
            return list(self._table(params).get("fk", []))
        if "information_schema.columns" in query:
            return [{"name": c, "column_name": c, "data_type": "integer", "is_nullable": "NO",
                     "column_default": None, "nullable": False, "not_null": True}
                    for c in self._table(params).get("columns", [])]
        if "information_schema.tables" in query and "table_name = %s" in query:
            return [{"name": params[1], "schema": params[0], "type": "BASE TABLE"}] if self._table(params) else []
        if "information_schema.tables" in query:
            return [{"name": t, "schema": params[0], "type": "BASE TABLE"} for t in self.schemas.get(params[0], {})]
        if self.on_query is not None:
            return self.on_query(query, params)
        raise AssertionError(f"unexpected statement: {query}")


WAREHOUSE = {
    "public": {
        "orders": {"columns": ["id", "customer_id"], "pk": ["id"],
                   "fk": [{"constraint_name": "fk_c", "column_name": "customer_id",
                           "foreign_table_schema": "public", "foreign_table_name": "customers",
                           "foreign_column_name": "id"}]},
        "customers": {"columns": ["id"], "pk": ["id"]},
    },
    "analytics": {"daily": {"columns": ["day"], "pk": []}},
}


@pytest.fixture
def make_tool() -> Callable[..., tuple[Redshift, _FakeConnection]]:
    patches: list[Any] = []

    def _make(responder: Responder | None = None, *, state: dict[str, Any] | None = None,
              connect_error: Exception | None = None) -> tuple[Redshift, _FakeConnection]:
        conn = _FakeConnection(responder or _Catalog(WAREHOUSE))
        p = patch.object(redshift_connector, "connect",
                         side_effect=connect_error if connect_error else (lambda **kw: conn))
        p.start()
        patches.append(p)
        client = RedshiftClient(host="wh.example.redshift.amazonaws.com", database="dev",
                                user="agent", password=PASSWORD)
        return Redshift(client, state if state is not None else {}), conn

    yield _make
    for p in patches:
        p.stop()


def _body(result: tuple[bool, str]) -> dict[str, Any]:
    return json.loads(result[1])


def test_driver_is_the_real_package_not_a_stand_in() -> None:
    assert redshift_client_module.redshift_connector is redshift_connector
    assert not isinstance(redshift_connector, MagicMock)
    assert issubclass(redshift_connector.InterfaceError, redshift_connector.Error)
    assert callable(redshift_connector.connect)


class TestSchemaTools:
    async def test_list_schemas(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.list_schemas())
        assert [s["name"] for s in body["schemas"]] == ["public", "analytics"]
        assert body["schema_count"] == 2

    async def test_list_schemas_and_tables(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.list_schemas_and_tables())
        assert {s["schema"]: s["tables"] for s in body["schemas"]} == {
            "public": ["orders", "customers"], "analytics": ["daily"],
        }

    async def test_list_tables_binds_the_schema(self, make_tool) -> None:
        tool, conn = make_tool()
        body = _body(await tool.list_tables(schema_name="analytics"))
        assert [t["name"] for t in body["tables"]] == ["daily"]
        assert conn.calls[-1][1] == ("analytics",)

    async def test_get_table_ddl_is_built_from_the_catalog(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.get_table_ddl(schema_name="public", table="orders"))
        ddl = body["data"]["ddl"]
        assert ddl.startswith("CREATE TABLE public.orders (")
        assert "PRIMARY KEY (id)" in ddl
        assert "FOREIGN KEY (customer_id) REFERENCES public.customers(id)" in ddl

    async def test_get_table_ddl_for_a_missing_table(self, make_tool) -> None:
        tool, _ = make_tool()
        ok, payload = await tool.get_table_ddl(schema_name="public", table="ghost")
        assert ok is False
        assert json.loads(payload)["error"] == "Table not found"

    async def test_get_schema_ddl_covers_every_table(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.get_schema_ddl(schema_name="public"))
        assert [t["table"] for t in body["tables"]] == ["orders", "customers"]
        assert all(t["ddl"].startswith("CREATE TABLE public.") for t in body["tables"])

    async def test_names_are_bound_never_spliced(self, make_tool) -> None:
        tool, conn = make_tool()
        hostile_schema = "public'; DROP SCHEMA analytics CASCADE; --"
        hostile_table = "x' OR '1'='1"
        await tool.get_tables_schema(schema_name=hostile_schema, tables=[hostile_table])
        await tool.get_table_ddl(schema_name=hostile_schema, table=hostile_table)
        await tool.list_tables(schema_name=hostile_schema)
        await tool.get_schema_ddl(schema_name=hostile_schema)

        assert conn.calls
        for query, params in conn.calls:
            assert hostile_schema not in query and hostile_table not in query
            assert params and params[0] == hostile_schema

    async def test_get_tables_schema_collects_keys_and_reports_missing_tables(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.get_tables_schema(schema_name="public", tables=["orders", "ghost", ""]))
        by_name = {t["name"]: t for t in body["tables"]}
        assert by_name["orders"]["primary_keys"] == ["id"]
        assert by_name["orders"]["foreign_keys"][0]["foreign_table_name"] == "customers"
        assert by_name["ghost"]["error"] == "Table not found"
        assert body["table_count"] == 2

    async def test_get_tables_schema_accepts_one_table_name_as_text(self, make_tool) -> None:
        tool, _ = make_tool()
        body = _body(await tool.get_tables_schema(schema_name="public", tables="orders"))
        assert [t["name"] for t in body["tables"]] == ["orders"]

    @pytest.mark.parametrize(("include_views", "expected"), [(True, ["v_sales"]), (False, [])])
    async def test_fetch_db_schema(self, make_tool, include_views: bool, expected: list[str]) -> None:
        tool, conn = make_tool(_Catalog(WAREHOUSE, views={"public": ["v_sales"]}))
        body = _body(await tool.fetch_db_schema(include_views=include_views))

        by_schema = {s["name"]: s for s in body["schemas"]}
        assert by_schema["public"]["table_count"] == 2
        assert [v["name"] for v in by_schema["public"]["views"]] == expected
        assert by_schema["analytics"]["tables"][0]["primary_keys"] == []
        assert any("information_schema.views" in q for q in conn.statements()) is include_views

    async def test_read_tools_only_read(self, make_tool) -> None:
        tool, conn = make_tool(_Catalog(WAREHOUSE, views={"public": ["v_sales"]}))
        await tool.list_schemas()
        await tool.list_schemas_and_tables()
        await tool.list_tables(schema_name="public")
        await tool.get_table_ddl(schema_name="public", table="orders")
        await tool.get_schema_ddl(schema_name="public")
        await tool.get_tables_schema(schema_name="public", tables=["orders"])
        await tool.fetch_db_schema()

        assert conn.statements()
        assert all(q.split()[0] == "SELECT" for q in conn.statements())

    @pytest.mark.parametrize(
        ("method", "kwargs", "message"),
        [("list_tables", {"schema_name": ""}, "Missing required parameter: schema_name"),
         ("get_table_ddl", {"schema_name": "public", "table": ""},
          "Missing required parameters: schema_name and table"),
         ("get_schema_ddl", {"schema_name": ""}, "Missing required parameter: schema_name"),
         ("get_tables_schema", {"schema_name": "", "tables": ["t"]}, "Missing required parameter: schema_name"),
         ("get_tables_schema", {"schema_name": "public", "tables": []}, "Missing required parameter: tables"),
         ("execute_query", {"query": ""}, "Missing required parameter: query")],
    )
    async def test_missing_arguments(self, make_tool, method: str, kwargs: dict, message: str) -> None:
        tool, conn = make_tool()
        ok, payload = await getattr(tool, method)(**kwargs)
        assert (ok, json.loads(payload)["error"]) == (False, message)
        assert conn.calls == []


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"id": i, "email": f"user{i}@example.com"} for i in range(n)]


class TestExecuteQuery:
    async def test_runs_the_query_unchanged(self, make_tool) -> None:
        sql = "SELECT id, email FROM public.customers WHERE email LIKE '%@example.com'"
        tool, conn = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: _rows(2)))
        body = _body(await tool.execute_query(query=sql))

        assert conn.calls == [(sql, ())]
        assert body["row_count"] == 2
        assert body["data"][1] == {"id": 1, "email": "user1@example.com"}
        assert conn.commits == 1

    async def test_display_is_capped_at_100_rows(self, make_tool) -> None:
        tool, _ = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: _rows(150)))
        body = _body(await tool.execute_query(query="SELECT * FROM public.customers"))
        assert (body["row_count"], len(body["data"])) == (150, 100)
        assert (body["truncated"], body["displayed_row_count"]) == (True, 100)

    async def test_full_result_is_exported_as_csv(self, make_tool) -> None:
        blob = create_autospec(BlobStorage, instance=True)
        blob.save_conversation_file_to_storage.return_value = {"url": "https://files/r.csv"}
        state = {"conversation_id": "conv-rs", "org_id": "org-1", "blob_storage": blob}
        tool, _ = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: _rows(150)), state=state)

        await tool.execute_query(query="SELECT * FROM public.customers")
        tasks = pop_tasks("conv-rs")
        assert len(tasks) == 1
        assert await tasks[0] == {"type": "csv_download", "url": "https://files/r.csv"}
        lines = blob.save_conversation_file_to_storage.await_args.kwargs["file_bytes"].decode().splitlines()
        assert (lines[0], len(lines)) == ("id,email", 151)

    async def test_failed_export_does_not_fail_the_query(self, make_tool) -> None:
        blob = create_autospec(BlobStorage, instance=True)
        blob.save_conversation_file_to_storage.side_effect = OSError("bucket gone")
        state = {"conversation_id": "conv-rs-2", "org_id": "org-1", "blob_storage": blob}
        tool, _ = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: _rows(2)), state=state)

        ok, _ = await tool.execute_query(query="SELECT 1")
        assert ok is True
        assert await pop_tasks("conv-rs-2")[0] is None

    async def test_statement_without_rows_reports_affected_rows(self, make_tool) -> None:
        tool, conn = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: None))
        body = _body(await tool.execute_query(query="DELETE FROM public.orders WHERE id < 3"))
        assert body["data"] == [{"affected_rows": 3}]
        assert conn.commits == 1

    async def test_sql_error_is_returned_and_rolled_back(self, make_tool) -> None:
        def fail(q: str, p: tuple) -> None:
            raise redshift_connector.ProgrammingError({"S": "ERROR", "M": 'relation "nope" does not exist'})

        tool, conn = make_tool(_Catalog(WAREHOUSE, on_query=fail))
        ok, payload = await tool.execute_query(query="SELECT * FROM nope")

        assert ok is False
        body = json.loads(payload)
        assert body["error"].startswith("Query execution failed: ")
        assert "does not exist" in body["error"]
        assert conn.rollbacks == 1


class TestConnectionFailures:
    @pytest.mark.parametrize(
        "driver_error",
        [
            redshift_connector.InterfaceError("communication error"),
            redshift_connector.ProgrammingError(
                {"S": "FATAL", "C": "28000", "M": 'password authentication failed for user "agent"'}),
        ],
    )
    async def test_becomes_a_tool_error_without_secrets(
        self, make_tool, caplog: pytest.LogCaptureFixture, driver_error: Exception,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        tool, _ = make_tool(connect_error=driver_error)

        results = [await tool.list_schemas(), await tool.list_schemas_and_tables(),
                   await tool.fetch_db_schema(), await tool.execute_query(query="SELECT 1"),
                   await tool.list_tables(schema_name="public"),
                   await tool.get_schema_ddl(schema_name="public"),
                   await tool.get_table_ddl(schema_name="public", table="orders")]

        for ok, payload in results:
            assert ok is False
            body = json.loads(payload)
            assert body["error"].startswith("Failed to connect to Redshift: ")
            assert "Traceback" not in payload
            assert PASSWORD not in payload
        assert caplog.records
        assert PASSWORD not in caplog.text

    async def test_successful_run_logs_no_password(self, make_tool, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        tool, _ = make_tool(_Catalog(WAREHOUSE, on_query=lambda q, p: _rows(1)))
        outputs = [await tool.fetch_db_schema(), await tool.execute_query(query="SELECT 1")]

        assert caplog.records
        assert PASSWORD not in caplog.text
        assert all(PASSWORD not in payload for _, payload in outputs)

    async def test_one_unreadable_schema_does_not_hide_the_rest(self, make_tool) -> None:
        catalog = _Catalog(WAREHOUSE)

        def responder(q: str, p: tuple) -> list[dict[str, Any]] | None:
            if "information_schema.tables" in q and p == ("analytics",):
                raise redshift_connector.ProgrammingError({"M": "permission denied for schema analytics"})
            return catalog(q, p)

        tool, _ = make_tool(responder)
        ok, payload = await tool.fetch_db_schema(include_views=False)

        assert ok is True
        by_schema = {s["name"]: s for s in json.loads(payload)["schemas"]}
        assert by_schema["public"]["table_count"] == 2
        assert "permission denied" in by_schema["analytics"]["error"]
