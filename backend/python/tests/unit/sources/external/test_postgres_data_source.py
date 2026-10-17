"""Tests for app.sources.external.postgres.postgres_."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.postgres.postgres_ import PostgreSQLDataSource, quote_ident


def _data_source(execute_query: AsyncMock) -> PostgreSQLDataSource:
    client = MagicMock()
    client.execute_query = execute_query
    return PostgreSQLDataSource(client)


class TestQuoteIdent:

    def test_wraps_in_double_quotes(self):
        assert quote_ident("Users") == '"Users"'

    def test_doubles_embedded_quotes(self):
        # Left undoubled, a quote in a name ends the identifier early.
        assert quote_ident('t","secret') == '"t"",""secret"'


class TestFetchTableRows:

    @pytest.mark.asyncio
    async def test_quotes_names_and_orders_rows(self):
        execute = AsyncMock(return_value=[])
        await _data_source(execute).fetch_table_rows("s", 'we"ird', limit=5, order_by=["id", "Name"])

        execute.assert_awaited_once_with('SELECT * FROM "s"."we""ird" ORDER BY "id", "Name" LIMIT 5')

    @pytest.mark.asyncio
    async def test_default_limit_without_order(self):
        execute = AsyncMock(return_value=[])
        await _data_source(execute).fetch_table_rows("public", "users")

        execute.assert_awaited_once_with('SELECT * FROM "public"."users" LIMIT 1000')


class TestConstraintQueries:
    """The information_schema constraint views hide constraints from a
    SELECT-only user and join constraints to columns by name; these queries
    must stay on pg_constraint."""

    @pytest.mark.asyncio
    async def test_foreign_keys_pair_columns_by_position(self):
        rows = [
            {"constraint_name": "fk", "column_name": "a", "foreign_table_schema": "s",
             "foreign_table_name": "p", "foreign_column_name": "x"},
            {"constraint_name": "fk", "column_name": "b", "foreign_table_schema": "s",
             "foreign_table_name": "p", "foreign_column_name": "y"},
        ]
        execute = AsyncMock(return_value=rows)

        response = await _data_source(execute).get_foreign_keys("s", "child")

        query, params = execute.await_args.args
        assert "pg_constraint" in query and "information_schema" not in query
        assert "unnest(con.conkey, con.confkey) WITH ORDINALITY" in query
        assert params == ("s", "child")
        assert [(r["column_name"], r["foreign_column_name"]) for r in response.data] == [("a", "x"), ("b", "y")]

    @pytest.mark.asyncio
    async def test_primary_keys_in_key_order(self):
        execute = AsyncMock(return_value=[{"column_name": "x"}, {"column_name": "y"}])

        response = await _data_source(execute).get_primary_keys("s", "parent")

        query = execute.await_args.args[0]
        assert "con.contype = 'p'" in query and "information_schema" not in query
        assert [r["column_name"] for r in response.data] == ["x", "y"]

    @pytest.mark.asyncio
    async def test_table_info_reads_unique_and_check_from_catalog(self):
        async def execute(query, params):
            if "information_schema.tables" in query:
                return [{"name": "child", "type": "BASE TABLE"}]
            if "information_schema.columns" in query:
                return [{"name": "a", "data_type": "integer"}]
            if "contype = 'u'" in query:
                return [{"column_name": "a"}]
            if "contype = 'c'" in query:
                return [{"constraint_name": "one_present", "check_clause": "((a IS NOT NULL) OR (b IS NOT NULL))"}]
            raise AssertionError(f"unexpected query: {query}")

        response = await _data_source(AsyncMock(side_effect=execute)).get_table_info("s", "child")

        assert response.success
        assert response.data["columns"][0]["is_unique"] is True
        # A check that mentions IS NOT NULL is still a check.
        assert response.data["check_constraints"][0]["constraint_name"] == "one_present"


class TestGetTableDdl:

    @pytest.mark.asyncio
    async def test_uses_postgres_quoting_and_definitions(self):
        async def execute(query, params):
            if "pg_attribute" in query and "format_type" in query:
                return [
                    {"column_name": "id", "quoted_name": "id", "qualified_table": 's."we""ird"',
                     "data_type": "integer", "not_null": True, "default_value": None, "ordinal": 1},
                    {"column_name": "Order", "quoted_name": '"Order"', "qualified_table": 's."we""ird"',
                     "data_type": "text", "not_null": False, "default_value": None, "ordinal": 2},
                ]
            return [
                {"constraint_name": "pk", "quoted_name": "pk", "definition": "PRIMARY KEY (id)"},
                {"constraint_name": "fk", "quoted_name": "fk",
                 "definition": "FOREIGN KEY (id, \"Order\") REFERENCES s.parent(x, y)"},
            ]

        response = await _data_source(AsyncMock(side_effect=execute)).get_table_ddl("s", 'we"ird')

        assert response.data["ddl"] == (
            'CREATE TABLE s."we""ird" (\n'
            "  id integer NOT NULL,\n"
            '  "Order" text,\n'
            "  CONSTRAINT pk PRIMARY KEY (id),\n"
            '  CONSTRAINT fk FOREIGN KEY (id, "Order") REFERENCES s.parent(x, y)\n'
            ");"
        )

    @pytest.mark.asyncio
    async def test_missing_table(self):
        response = await _data_source(AsyncMock(return_value=[])).get_table_ddl("s", "nope")
        assert response.success is False


class TestListingQueries:

    @pytest.mark.asyncio
    async def test_table_stats_match_the_listed_tables(self):
        # Materialized views and tables the user can't see are in
        # pg_stat_user_tables but never listed; counting them made
        # incremental sync treat them as new tables every run.
        execute = AsyncMock(return_value=[])

        await _data_source(execute).get_table_stats(["s"])

        query, params = execute.await_args.args
        assert "c.relkind IN ('r', 'p')" in query
        assert "has_table_privilege" in query
        assert params == (["s"],)

    @pytest.mark.asyncio
    async def test_schemas_skip_session_temp_schemas(self):
        execute = AsyncMock(return_value=[])

        await _data_source(execute).list_schemas()

        assert "!~ '^pg_(toast_)?temp_'" in execute.await_args.args[0]


class TestByteCappedRows:

    @pytest.mark.asyncio
    async def test_budget_is_enforced_in_sql_and_bookkeeping_stripped(self):
        execute = AsyncMock(return_value=[
            {"id": 1, "_pipeshub_row_number": 1, "_pipeshub_running_bytes": 10},
        ])

        rows = await _data_source(execute).fetch_table_rows(
            "s", "t", limit=50, order_by=["id"], max_bytes=4096
        )

        query = execute.await_args.args[0]
        assert 'SELECT * FROM "s"."t" ORDER BY "id" LIMIT 50' in query
        assert "_pipeshub_running_bytes <= 4096" in query
        # row_number() is not bound to the subquery's order, so it repeats it;
        # otherwise the budget could keep an arbitrary subset of the rows.
        assert 'row_number() OVER (ORDER BY sampled."id")' in query
        # Rows past the budget are filtered by Postgres, so they are never sent.
        assert rows == [{"id": 1}]


class TestChangeDetectionQueries:

    @pytest.mark.asyncio
    async def test_partitioned_tables_get_their_partitions_totals(self):
        # A partitioned table's own counters never move; its rows live in its partitions.
        execute = AsyncMock(return_value=[])

        await _data_source(execute).get_table_stats(None)

        query = execute.await_args.args[0]
        assert "WITH RECURSIVE partition_tree" in query
        assert "coalesce(pt.n_tup_ins, st.n_tup_ins, 0)" in query

    @pytest.mark.asyncio
    async def test_columns_for_all_tables_in_one_query(self):
        execute = AsyncMock(return_value=[{"schema_name": "s", "table_name": "t", "name": "id"}])

        response = await _data_source(execute).get_columns(["s"])

        query, params = execute.await_args.args
        assert "information_schema.columns" in query and "contype = 'u'" in query
        assert params == (["s"],)
        assert response.data == [{"schema_name": "s", "table_name": "t", "name": "id"}]

    @pytest.mark.asyncio
    async def test_empty_schema_list_skips_the_query(self):
        execute = AsyncMock()
        ds = _data_source(execute)

        assert (await ds.get_columns([])).data == []
        assert (await ds.get_primary_keys_by_table([])).data == []
        execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_is_in_recovery(self):
        execute = AsyncMock(return_value=[{"in_recovery": True}])
        response = await _data_source(execute).is_in_recovery()
        assert response.data == {"in_recovery": True}

    @pytest.mark.asyncio
    async def test_sample_hash_orders_the_aggregate_like_the_sample(self):
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        response = await _data_source(execute).get_sample_hash("s", "t", limit=10, order_by=["id"])

        query = execute.await_args.args[0]
        assert 'ORDER BY sampled."id"' in query
        assert 'SELECT * FROM "s"."t" ORDER BY "id" LIMIT 10' in query
        assert response.data == {"sample_hash": "abc"}

    @pytest.mark.asyncio
    async def test_sample_hash_without_a_key_orders_by_row_text(self):
        # A scan-order sample can change while the table does not (synchronized
        # scans start mid-table), which would read as a write on every sync.
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        await _data_source(execute).get_sample_hash("s", "t", limit=10)

        query = execute.await_args.args[0]
        assert 'SELECT * FROM "s"."t" AS _pipeshub_row ORDER BY _pipeshub_row::text LIMIT 10' in query
        assert "ORDER BY sampled::text" in query

    @pytest.mark.asyncio
    async def test_sample_hash_is_byte_capped_like_the_row_fetch(self):
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        await _data_source(execute).get_sample_hash("s", "t", limit=10, order_by=["id"], max_bytes=4096)

        query = execute.await_args.args[0]
        assert "_pipeshub_running_bytes <= 4096" in query
        assert 'row_number() OVER (ORDER BY sampled."id")' in query
        assert "string_agg(sized::text, E'\\n' ORDER BY _pipeshub_row_number)" in query

    @pytest.mark.asyncio
    async def test_failures_are_reported_not_raised(self):
        execute = AsyncMock(side_effect=Exception("boom"))
        ds = _data_source(execute)
        for response in (
            await ds.get_columns(None),
            await ds.get_primary_keys_by_table(None),
            await ds.is_in_recovery(),
            await ds.get_sample_hash("s", "t"),
        ):
            assert response.success is False
            assert response.error == "boom"
