"""Tests for app.sources.external.postgres.postgres_."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.postgres.postgres_ import (
    PROFILING_QUERY_SETTINGS,
    PostgreSQLDataSource,
    quote_ident,
)


def _data_source(execute_query: AsyncMock, *, server_version_num: int | None = 160000) -> PostgreSQLDataSource:
    """A data source whose queries go to ``execute_query``.

    The server version is known up front unless ``server_version_num`` is None,
    so the version query does not take the place of the one under test.
    """
    client = MagicMock()
    client.execute_query = execute_query
    data_source = PostgreSQLDataSource(client)
    if server_version_num is not None:
        data_source._has_conparentid = server_version_num >= 110000
    return data_source


class TestQuoteIdent:

    def test_wraps_in_double_quotes(self) -> None:
        assert quote_ident("Users") == '"Users"'

    def test_doubles_embedded_quotes(self) -> None:
        # Left undoubled, a quote in a name ends the identifier early.
        assert quote_ident('t","secret') == '"t"",""secret"'


@pytest.mark.asyncio
async def test_database_oid_is_the_connected_databases() -> None:
    execute = AsyncMock(return_value=[{"oid": 16384}])

    assert await _data_source(execute).get_database_oid() == 16384
    assert "current_database()" in execute.await_args.args[0]


class TestFetchTableRows:

    @pytest.mark.asyncio
    async def test_quotes_names_and_orders_rows(self) -> None:
        execute = AsyncMock(return_value=[])
        await _data_source(execute).fetch_table_rows("s", 'we"ird', limit=5, order_by=["id", "Name"])

        execute.assert_awaited_once_with('SELECT * FROM "s"."we""ird" ORDER BY "id", "Name" LIMIT 5')

    @pytest.mark.asyncio
    async def test_default_limit_without_order(self) -> None:
        execute = AsyncMock(return_value=[])
        await _data_source(execute).fetch_table_rows("public", "users")

        execute.assert_awaited_once_with('SELECT * FROM "public"."users" LIMIT 1000')


class TestFetchColumnValueCounts:

    @pytest.mark.asyncio
    async def test_groups_the_quoted_column_with_profiling_limits(self) -> None:
        execute = AsyncMock(return_value=[{"value": "A", "count": 3}])
        rows = await _data_source(execute).fetch_column_value_counts("s", "Orders", 'st"atus', limit=50)

        assert rows == [{"value": "A", "count": 3}]
        execute.assert_awaited_once_with(
            'SELECT "st""atus"::text AS value, COUNT(*) AS count FROM "s"."Orders" '
            "GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST LIMIT 50",
            local_settings=PROFILING_QUERY_SETTINGS,
        )


class TestConstraintQueries:
    """The information_schema constraint views hide constraints from a
    SELECT-only user and join constraints to columns by name; these queries
    must stay on pg_constraint."""

    @pytest.mark.asyncio
    async def test_foreign_keys_pair_columns_by_position(self) -> None:
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
    async def test_primary_keys_in_key_order(self) -> None:
        execute = AsyncMock(return_value=[
            {"key_type": "unique", "key_width": 1, "column_names": ["z"]},
            {"key_type": "primary", "key_width": 2, "column_names": ["x", "y"]},
        ])

        response = await _data_source(execute).get_primary_keys("s", "parent")

        query = execute.await_args.args[0]
        assert "pg_constraint" in query and "information_schema" not in query
        assert [r["column_name"] for r in response.data] == ["x", "y"]

    @pytest.mark.asyncio
    async def test_table_info_reads_unique_and_check_from_catalog(self) -> None:
        async def execute(query, params) -> object:
            if "information_schema.tables" in query:
                return [{"name": "child", "type": "BASE TABLE"}]
            if "information_schema.columns" in query:
                return [{"name": "a", "data_type": "integer"}]
            if "contype IN ('p', 'u')" in query:
                return [{"key_type": "unique", "key_width": 1, "column_names": ["a"]}]
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
    async def test_uses_postgres_quoting_and_definitions(self) -> None:
        async def execute(query, params) -> object:
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
    async def test_missing_table(self) -> None:
        response = await _data_source(AsyncMock(return_value=[])).get_table_ddl("s", "nope")
        assert response.success is False


class TestListingQueries:

    @pytest.mark.asyncio
    async def test_table_stats_match_the_listed_tables(self) -> None:
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
    async def test_schemas_skip_session_temp_schemas(self) -> None:
        execute = AsyncMock(return_value=[])

        await _data_source(execute).list_schemas()

        assert "!~ '^pg_(toast_)?temp_'" in execute.await_args.args[0]


class TestByteCappedRows:

    @pytest.mark.asyncio
    async def test_budget_is_enforced_in_sql_and_bookkeeping_stripped(self) -> None:
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
    async def test_partitioned_tables_get_their_partitions_totals(self) -> None:
        # A partitioned table's own counters never move; its rows live in its partitions.
        execute = AsyncMock(return_value=[])

        await _data_source(execute).get_table_stats(None)

        query = execute.await_args.args[0]
        assert "WITH RECURSIVE partition_tree" in query
        assert "coalesce(pt.n_tup_ins, st.n_tup_ins, 0)" in query

    @pytest.mark.asyncio
    async def test_table_stats_carry_the_oid_and_a_storage_id(self) -> None:
        # The oid survives a rename; the storage id is all a TRUNCATE moves.
        execute = AsyncMock(return_value=[
            {"schema_name": "s", "table_name": "t", "oid": 16402, "storage_id": "16409", "n_tup_ins": 3},
        ])

        response = await _data_source(execute).get_table_stats(None)

        query = execute.await_args.args[0]
        assert "c.oid::bigint AS oid" in query
        assert "coalesce(ps.storage_id, c.relfilenode::text) AS storage_id" in query
        [stat] = response.data
        assert (stat["oid"], stat["storage_id"], stat["n_tup_ins"]) == (16402, "16409", 3)

    @pytest.mark.asyncio
    async def test_partitioned_tables_get_a_storage_id_from_their_partitions(self) -> None:
        # A partitioned table has no storage of its own, so truncating a
        # partition would otherwise leave its storage id where it was.
        execute = AsyncMock(return_value=[])

        await _data_source(execute).get_table_stats(None)

        query = execute.await_args.args[0]
        assert "md5(string_agg(pc.relfilenode::text, ',' ORDER BY pc.oid)) AS storage_id" in query
        assert "LEFT JOIN partition_storage ps ON ps.root = c.oid" in query

    @pytest.mark.asyncio
    async def test_columns_for_all_tables_in_one_query(self) -> None:
        execute = AsyncMock(return_value=[{"schema_name": "s", "table_name": "t", "name": "id"}])

        response = await _data_source(execute).get_columns(["s"])

        query, params = execute.await_args.args
        assert "information_schema.columns" in query and "contype = 'u'" in query
        assert params == (["s"],)
        assert response.data == [{"schema_name": "s", "table_name": "t", "name": "id"}]

    @pytest.mark.asyncio
    async def test_columns_carry_their_attnum(self) -> None:
        # A rename leaves it alone, so it says which column a new name belongs to.
        rows = [{"schema_name": "s", "table_name": "t", "name": "id", "attnum": 1}]
        execute = AsyncMock(return_value=rows)

        response = await _data_source(execute).get_columns(None)

        assert "col.ordinal_position::int AS attnum" in execute.await_args.args[0]
        assert response.data == rows

    @pytest.mark.asyncio
    async def test_constraint_hashes_for_all_tables_in_one_query(self) -> None:
        rows = [{"schema_name": "s", "table_name": "t", "constraint_hash": "c" * 32}]
        execute = AsyncMock(return_value=rows)

        response = await _data_source(execute).get_constraint_hashes(None)

        query, params = execute.await_args.args
        assert "con.contype IN ('p', 'u', 'f', 'c')" in query
        # Unique indexes that back no constraint are keys too.
        assert "i.indisunique" in query and "con.conindid = i.indexrelid" in query
        # Ordered inside the aggregate, or the digest would move with the scan order.
        assert "md5(string_agg(d.definition, '|' ORDER BY d.definition)) AS constraint_hash" in query
        # The same keys with no name in them: columns by number, tables by oid.
        assert "md5(string_agg(d.shape, '|' ORDER BY d.shape)) AS constraint_shape" in query
        assert "con.conkey::text, con.confrelid::text" in query
        assert "con.conname" not in query.split("AS definition", 1)[1].split("FROM pg_constraint", 1)[0]
        # A foreign key's conindid is the unique index it references: another
        # table's key must not make this one's index count or not.
        assert "con.conindid = i.indexrelid AND con.contype IN ('p', 'u', 'x')" in query
        assert "ANY($1::text[])" not in query
        assert params is None
        assert response.success and response.data == rows

    @pytest.mark.asyncio
    async def test_constraint_hashes_are_limited_to_the_given_schemas(self) -> None:
        execute = AsyncMock(return_value=[])

        await _data_source(execute).get_constraint_hashes(["s", "other"])

        query, params = execute.await_args.args
        assert "AND n.nspname = ANY($1::text[]) GROUP BY n.nspname, c.relname" in query
        assert params == (["s", "other"],)

    @pytest.mark.asyncio
    async def test_empty_schema_list_skips_the_query(self) -> None:
        execute = AsyncMock()
        ds = _data_source(execute)

        assert (await ds.get_columns([])).data == []
        assert (await ds.get_primary_keys_by_table([])).data == []
        constraints = await ds.get_constraint_hashes([])
        assert constraints.success and constraints.data == []
        assert (await ds.get_referencing_tables([])).data == []
        execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_foreign_keys_cloned_for_partitions_are_left_out(self) -> None:
        # One clone per partition of the referenced table would change the
        # referencing table's digest each time a partition is added.
        execute = AsyncMock(return_value=[])
        ds = _data_source(execute)

        await ds.get_foreign_keys("s", "child")
        foreign_keys_query = execute.await_args.args[0]
        await ds.get_constraint_hashes(None)
        constraints_query = execute.await_args.args[0]

        assert "con.conparentid = 0" in foreign_keys_query
        assert "con.conparentid = 0" in constraints_query

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("version", "filtered"), [("100023", False), ("110000", True), ("170002", True)])
    async def test_the_partition_filter_follows_the_server_version(self, version, filtered) -> None:
        # conparentid exists from PostgreSQL 11; naming it on 10 fails the query.
        async def execute(query, params=None) -> object:
            return [{"server_version_num": version}] if query.startswith("SHOW") else []

        ds = _data_source(AsyncMock(side_effect=execute), server_version_num=None)

        await ds.get_constraint_hashes(None)
        await ds.get_foreign_keys("s", "child")

        queries = [call.args[0] for call in ds._client.execute_query.await_args_list]
        assert sum(q.startswith("SHOW") for q in queries) == 1
        assert all(("conparentid" in q) == filtered for q in queries if not q.startswith("SHOW"))

    @pytest.mark.asyncio
    async def test_referencing_tables_are_found_by_oid(self) -> None:
        rows = [{"schema_name": "shop", "table_name": "orders"}]
        execute = AsyncMock(return_value=rows)

        response = await _data_source(execute).get_referencing_tables([41, 42])

        query, params = execute.await_args.args
        assert "con.contype = 'f' AND con.confrelid = ANY($1::oid[])" in query
        assert "con.conparentid = 0" in query
        assert params == ([41, 42],)
        assert response.success and response.data == rows

    @pytest.mark.asyncio
    async def test_is_in_recovery(self) -> None:
        execute = AsyncMock(return_value=[{"in_recovery": True}])
        response = await _data_source(execute).is_in_recovery()
        assert response.data == {"in_recovery": True}

    @pytest.mark.asyncio
    async def test_sample_hash_orders_the_aggregate_like_the_sample(self) -> None:
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        response = await _data_source(execute).get_sample_hash("s", "t", limit=10, order_by=["id"])

        query = execute.await_args.args[0]
        assert 'ORDER BY sampled."id"' in query
        assert 'SELECT * FROM "s"."t" ORDER BY "id" LIMIT 10' in query
        assert response.data == {"sample_hash": "abc"}

    @pytest.mark.asyncio
    async def test_sample_hash_without_a_key_orders_by_row_text(self) -> None:
        # A scan-order sample can change while the table does not (synchronized
        # scans start mid-table), which would read as a write on every sync.
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        await _data_source(execute).get_sample_hash("s", "t", limit=10)

        query = execute.await_args.args[0]
        assert 'SELECT * FROM "s"."t" AS _pipeshub_row ORDER BY _pipeshub_row::text LIMIT 10' in query
        assert "ORDER BY sampled::text" in query

    @pytest.mark.asyncio
    async def test_sample_hash_is_byte_capped_like_the_row_fetch(self) -> None:
        execute = AsyncMock(return_value=[{"sample_hash": "abc"}])

        await _data_source(execute).get_sample_hash("s", "t", limit=10, order_by=["id"], max_bytes=4096)

        query = execute.await_args.args[0]
        assert "_pipeshub_running_bytes <= 4096" in query
        assert 'row_number() OVER (ORDER BY sampled."id")' in query
        assert "string_agg(sized::text, E'\\n' ORDER BY _pipeshub_row_number)" in query

    @pytest.mark.asyncio
    async def test_failures_are_reported_not_raised(self) -> None:
        execute = AsyncMock(side_effect=Exception("boom"))
        ds = _data_source(execute)
        for response in (
            await ds.get_columns(None),
            await ds.get_constraint_hashes(None),
            await ds.get_primary_keys_by_table(None),
            await ds.is_in_recovery(),
            await ds.get_sample_hash("s", "t"),
        ):
            assert response.success is False
            assert response.error == "boom"
