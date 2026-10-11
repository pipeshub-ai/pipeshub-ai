"""Tests for app.sources.external.mariadb.mariadb_."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.mariadb.mariadb_ import MariaDBDataSource, TableStatsEntry


class TestTableStats:
    def test_update_time_from_the_driver_is_kept_as_text(self):
        entry = TableStatsEntry.model_validate({
            "database_name": "db",
            "table_name": "t",
            "n_live_tup": 2,
            "last_updated": datetime(2026, 9, 18, 21, 47, 29),
            "auto_increment": 3,
        })
        assert entry.last_updated == "2026-09-18T21:47:29"

    def test_a_table_never_written_has_no_update_time(self):
        entry = TableStatsEntry.model_validate({"table_name": "t", "last_updated": None})
        assert entry.last_updated is None

    @pytest.mark.asyncio
    async def test_stats_succeed_once_a_table_has_been_written(self):
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
    async def _query(**kwargs):
        client = MagicMock()
        client.execute_query = AsyncMock(return_value=[])
        await MariaDBDataSource(client).fetch_table_rows("shop", "orders", limit=5, **kwargs)
        return client.execute_query.call_args[0][0]

    @pytest.mark.asyncio
    async def test_sorts_by_the_given_columns_so_a_limit_returns_the_same_rows(self):
        query = await self._query(order_by=["tenant", "id"])
        assert query == "SELECT * FROM `shop`.`orders` ORDER BY `tenant`, `id` LIMIT 5"

    @pytest.mark.asyncio
    async def test_column_names_are_quoted(self):
        query = await self._query(order_by=["we`ird"])
        assert "ORDER BY `we``ird` LIMIT 5" in query

    @pytest.mark.asyncio
    async def test_no_order_without_columns(self):
        assert await self._query() == "SELECT * FROM `shop`.`orders` LIMIT 5"
        assert await self._query(order_by=[]) == "SELECT * FROM `shop`.`orders` LIMIT 5"
