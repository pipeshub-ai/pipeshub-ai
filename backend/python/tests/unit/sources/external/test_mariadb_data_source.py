"""Tests for app.sources.external.mariadb.mariadb_."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.mariadb.mariadb_ import MariaDBDataSource, TableStatsEntry


class TestTableStats:
    def test_update_time_from_the_driver_is_kept_as_text(self) -> None:
        entry = TableStatsEntry.model_validate({
            "database_name": "db",
            "table_name": "t",
            "n_live_tup": 2,
            "last_updated": datetime(2026, 9, 18, 21, 47, 29),
            "auto_increment": 3,
        })
        assert entry.last_updated == "2026-09-18T21:47:29"

    def test_a_table_never_written_has_no_update_time(self) -> None:
        entry = TableStatsEntry.model_validate({"table_name": "t", "last_updated": None})
        assert entry.last_updated is None

    @pytest.mark.asyncio
    async def test_stats_succeed_once_a_table_has_been_written(self) -> None:
        # UPDATE_TIME is set for any table written since the server started, so
        # rejecting a datetime failed every stats read on a live server and left
        # incremental sync blind to new, changed and dropped tables.
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[
            {
                "database_name": "db",
                "table_name": "articles",
                "n_live_tup": 2,
                "last_updated": datetime(2026, 9, 18, 21, 47, 29),
                "auto_increment": 3,
            },
            {
                "database_name": "db",
                "table_name": "untouched",
                "n_live_tup": 0,
                "last_updated": None,
                "auto_increment": None,
            },
        ])

        response = await MariaDBDataSource(client).get_table_stats(["db"])

        assert response.success, response.error
        assert [s["last_updated"] for s in response.data] == ["2026-09-18T21:47:29", None]


class TestFetchTableRows:
    @staticmethod
    async def _query(**kwargs) -> str:
        client = MagicMock()
        client.execute_read_only_query = AsyncMock(return_value=([], [], False))
        await MariaDBDataSource(client).fetch_table_rows("shop", "orders", limit=5, **kwargs)
        return client.execute_read_only_query.call_args[0][0]

    @pytest.mark.asyncio
    async def test_sorts_by_the_given_columns_so_a_limit_returns_the_same_rows(self) -> None:
        query = await self._query(order_by=["tenant", "id"])
        assert query == "SELECT * FROM `shop`.`orders` ORDER BY `tenant`, `id` LIMIT 5"

    @pytest.mark.asyncio
    async def test_column_names_are_quoted(self) -> None:
        query = await self._query(order_by=["we`ird"])
        assert "ORDER BY `we``ird` LIMIT 5" in query

    @pytest.mark.asyncio
    async def test_no_order_without_columns(self) -> None:
        assert await self._query() == "SELECT * FROM `shop`.`orders` LIMIT 5"
        assert await self._query(order_by=[]) == "SELECT * FROM `shop`.`orders` LIMIT 5"
def keyed_client(statistics: list[tuple], columns: list[str]) -> MagicMock:
    """A client answering each catalog query by its text."""
    client = MagicMock()
    client.database = "shop"

    async def execute_query(query, params=None) -> list:
        if "information_schema.STATISTICS" in query:
            assert "NON_UNIQUE = 0" in query and "ORDER BY INDEX_NAME, SEQ_IN_INDEX" in query
            return [{"index_name": row[0], "column_name": row[1], "sub_part": row[2] if len(row) > 2 else None}
                    for row in statistics]
        if "information_schema.COLUMNS" in query:
            return [{"name": name, "data_type": "int", "nullable": 0} for name in columns]
        if "CHECK_CONSTRAINTS" in query:
            return []
        return [{"name": "orders", "type": "BASE TABLE"}]

    client.execute_query = AsyncMock(side_effect=execute_query)
    return client


class TestKeys:
    @pytest.mark.asyncio
    async def test_composite_unique_columns_are_not_unique_on_their_own(self) -> None:
        client = keyed_client([("PRIMARY", "id"), ("uq_pair", "a"), ("uq_pair", "b"), ("uq_code", "code")],
                              ["id", "a", "b", "code", "note"])
        response = await MariaDBDataSource(client).get_table_info("orders")
        flags = {c["name"]: c for c in response.data["columns"]}
        assert flags["id"]["is_primary_key"] and flags["id"]["is_unique"]
        assert not flags["a"]["is_unique"] and flags["a"]["is_part_of_composite_unique"]
        assert flags["code"]["is_unique"] and not flags["code"]["is_part_of_composite_unique"]
        assert not any(flags["note"][k] for k in ("is_unique", "is_primary_key"))

    @pytest.mark.asyncio
    async def test_a_composite_primary_key_in_key_order(self) -> None:
        client = keyed_client([("PRIMARY", "tenant"), ("PRIMARY", "id")], ["id", "tenant"])
        source = MariaDBDataSource(client)
        flags = {c["name"]: c for c in (await source.get_table_info("orders")).data["columns"]}
        assert flags["id"]["is_part_of_composite_primary_key"] and not flags["id"]["is_unique"]
        pks = await source.get_primary_keys("orders")
        # The agent toolset reads this shape.
        assert pks.success and pks.data == [{"column_name": "tenant"}, {"column_name": "id"}]

    @pytest.mark.asyncio
    async def test_a_failed_key_read_fails_the_table(self) -> None:
        client = keyed_client([], ["id"])
        original = client.execute_query.side_effect

        async def failing(query, params=None) -> list:
            if "STATISTICS" in query:
                raise RuntimeError("denied")
            return await original(query, params)

        client.execute_query = AsyncMock(side_effect=failing)
        assert (await MariaDBDataSource(client).get_table_info("orders")).success is False

    @pytest.mark.asyncio
    async def test_foreign_key_pairs_are_grouped_by_constraint_in_order(self) -> None:
        client = MagicMock()
        client.database = "shop"
        client.execute_query = AsyncMock(return_value=[])
        await MariaDBDataSource(client).get_foreign_keys("orders")
        query = client.execute_query.await_args.args[0]
        assert "ORDER BY kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION" in query


class TestPrefixIndexes:
    @pytest.mark.asyncio
    async def test_a_unique_prefix_is_no_key(self) -> None:
        # UNIQUE(name(10)) makes the first 10 characters unique, not the values.
        client = keyed_client([("PRIMARY", "id"), ("uq_name", "name", 10)], ["id", "name"])
        flags = {c["name"]: c for c in (await MariaDBDataSource(client).get_table_info("orders")).data["columns"]}
        assert flags["id"]["is_unique"] and not flags["name"]["is_unique"]


class TestReadCatalog:
    @pytest.mark.asyncio
    async def test_three_queries_grouped_by_table_with_flags_keys_fks_and_cardinality(self) -> None:
        client = MagicMock()
        client.database = "shop"
        answers = {
            "information_schema.COLUMNS": [
                {"table_name": "orders", "name": "id", "data_type": "int", "column_type": "int(11)", "nullable": 0},
                {"table_name": "orders", "name": "customer_id", "data_type": "int", "column_type": "int(11)",
                 "nullable": 1},
                {"table_name": "customers", "name": "id", "data_type": "int", "column_type": "int(11)", "nullable": 0},
            ],
            "information_schema.STATISTICS": [
                {"table_name": "orders", "index_name": "PRIMARY", "column_name": "id", "non_unique": 0,
                 "seq_in_index": 1, "sub_part": None, "cardinality": 5000},
                {"table_name": "orders", "index_name": "ix_cust", "column_name": "customer_id", "non_unique": 1,
                 "seq_in_index": 1, "sub_part": None, "cardinality": 70},
                {"table_name": "orders", "index_name": "ix_pair", "column_name": "id", "non_unique": 1,
                 "seq_in_index": 2, "sub_part": None, "cardinality": 9},
                {"table_name": "customers", "index_name": "PRIMARY", "column_name": "id", "non_unique": 0,
                 "seq_in_index": 1, "sub_part": None, "cardinality": None},
            ],
            "information_schema.KEY_COLUMN_USAGE": [
                {"table_name": "orders", "constraint_name": "fk_c", "column_name": "customer_id",
                 "foreign_database": "shop", "foreign_table_name": "customers", "foreign_column_name": "id"},
            ],
        }

        async def execute_query(query, params=None) -> list:
            return [dict(r) for key, rows in answers.items() if key in query for r in rows]

        client.execute_query = AsyncMock(side_effect=execute_query)
        catalog = await MariaDBDataSource(client).read_catalog()
        assert client.execute_query.await_count == 3
        orders = catalog["orders"]
        assert [c.name for c in orders.columns] == ["id", "customer_id"]
        assert orders.columns[0].is_primary_key and orders.primary_keys == ["id"]
        assert orders.foreign_keys[0].foreign_table_name == "customers"
        # Leading columns only; NULL is unknown.
        assert orders.cardinality == {"id": 5000, "customer_id": 70}
        assert catalog["customers"].cardinality == {}

    @pytest.mark.asyncio
    async def test_catalog_columns_hash_as_table_info_columns_do(self) -> None:
        from app.connectors.sources.mariadb.tables import structure_hash

        row = {"name": "id", "data_type": "int", "column_type": "int(11)", "nullable": 0, "default": None}
        client = keyed_client([("PRIMARY", "id")], ["id"])
        info = await MariaDBDataSource(client).get_table_info("orders")
        from app.sources.external.mariadb.mariadb_ import (
            ColumnInfo,
            flagged_columns,
            keys_from_statistics,
        )

        catalog_columns = flagged_columns([dict(row)], keys_from_statistics(
            [{"index_name": "PRIMARY", "column_name": "id", "sub_part": None}],
        ))
        table_columns = [ColumnInfo.model_validate(c) for c in info.data["columns"]]
        assert structure_hash(catalog_columns, ["id"], []) == structure_hash(
            [c.model_copy(update={"column_type": "int(11)", "default": None}) for c in table_columns], ["id"], [],
        )


class TestSamples:
    @staticmethod
    def _client(rows) -> MagicMock:
        client = MagicMock()
        client.database = "shop"
        client.execute_read_only_query = AsyncMock(return_value=(["id", "name", "_pipeshub_running_bytes",
                                                                  "_pipeshub_row_number"], rows, False))
        return client

    @pytest.mark.asyncio
    async def test_an_ordered_sample_is_capped_by_the_server_on_its_own_bounded_connection(self) -> None:
        client = self._client([(1, "a", 2, 1)])
        rows = await MariaDBDataSource(client).fetch_table_rows(
            "shop", "or`ders", 100, order_by=["id"], byte_columns=["id", "name"], max_bytes=1000,
        )
        assert rows == [{"id": 1, "name": "a"}]
        query, timeout = client.execute_read_only_query.await_args.args
        assert "FROM (SELECT * FROM `shop`.`or``ders` ORDER BY `id` LIMIT 100) s" in query
        assert "COALESCE(OCTET_LENGTH(`name`), 0)" in query and "<= 1000 OR" in query
        assert timeout == 60 and client.execute_read_only_query.await_args.kwargs == {"max_rows": 100}

    @pytest.mark.asyncio
    async def test_an_unordered_sample_is_capped_here(self) -> None:
        client = MagicMock()
        client.execute_read_only_query = AsyncMock(return_value=(["v"], [("x" * 600,), ("y" * 600,)], False))
        rows = await MariaDBDataSource(client).fetch_table_rows("shop", "t", 10, max_bytes=1000)
        assert rows == [{"v": "x" * 600}]
        assert "ORDER BY" not in client.execute_read_only_query.await_args.args[0]

    @pytest.mark.asyncio
    async def test_a_failed_read_raises(self) -> None:
        client = MagicMock()
        client.execute_read_only_query = AsyncMock(side_effect=RuntimeError("1969"))
        with pytest.raises(RuntimeError):
            await MariaDBDataSource(client).fetch_table_rows("shop", "t", 10, order_by=["id"])

    @pytest.mark.asyncio
    async def test_value_counts_are_bounded_and_put_null_last_among_ties(self) -> None:
        client = MagicMock()
        client.execute_read_only_query = AsyncMock(return_value=(["value", "count"], [("a", 3), (None, 1)], False))
        counts = await MariaDBDataSource(client).column_value_counts("shop", "t", "status", 1000)
        assert counts == [{"value": "a", "count": 3}, {"value": None, "count": 1}]
        query = client.execute_read_only_query.await_args.args[0]
        assert "GROUP BY `status` ORDER BY 2 DESC, (`status` IS NULL), 1 LIMIT 1000" in query


class TestForeignKeysForAReadOnlyUser:
    # TABLE_CONSTRAINTS is empty for a user with only SELECT (MariaDB 11.4):
    # a join on it hid every foreign key.
    @pytest.mark.asyncio
    async def test_foreign_keys_are_read_from_key_column_usage_alone(self) -> None:
        client = MagicMock()
        client.database = "shop"
        client.execute_query = AsyncMock(return_value=[])
        source = MariaDBDataSource(client)
        await source.get_foreign_keys("orders", "shop")
        await source.read_catalog("shop")
        fk_queries = [c.args[0] for c in client.execute_query.await_args_list if "KEY_COLUMN_USAGE" in c.args[0]]
        assert len(fk_queries) == 2
        assert all("TABLE_CONSTRAINTS" not in q and "REFERENCED_TABLE_NAME IS NOT NULL" in q for q in fk_queries)
